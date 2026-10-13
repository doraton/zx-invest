"""从指南针本地文件拉 0AMV（活跃市值）日线写入 MySQL ``kline_0amv``。

数据源只有一个：``<compass>\\ANALYSE\\Data\\ChinaStk\\Z_SK\\day.vdat``。
本项目只要收盘后的日 K，不做分时，因此不读 ``.fde``（盘中缓存）。

⚠️ ``day.vdat`` 在**指南针运行中**会被独占锁定，读会抛 ``PermissionError``。
    这是权限错误不是「没有数据」——别据此判定历史缺失。
    每日调度请安排在收盘后、且用户未开着指南针的时段。
    详见 docs/znz-active-mv-ocr-sync.md §2.5。

落库口径
--------
- ``open/high/low/close``：**原值**，不做任何换算。
- ``volume`` / ``amount``：day.vdat 里的**原始值**，同样不换算，单位是**点**。
  ⚠️ 这两列**不是**「成交量·手 / 成交额·千元」，量纲与 0AMV 指数不同口径
  （详见 docs/znz-active-mv-ocr-sync.md §2.1.3）。原值入库是为了留证据，
  待口径查明后再决定是否换算或改列名。
  两列在库里是 ``BIGINT``：实测 8219 行**全部是整数**，无小数、无 NULL、无负值，
  最大 amount 3.9e12 远在 BIGINT 上限 9.2e18 之内。入库用 ``int(round(x))``，
  让「这列是整数」这件事在代码里就成立。
  （实测：发 float 也不会触发 MySQL 的截断警告——值本身就是整数，没有小数可截。
  所以这是表达意图，不是修 bug。别拿"避免警告"当理由再改回去。）
- ``chg_amt`` / ``chg_pct``：本模块**派生**。文件里没有这两个值，也没有昨收。
  0AMV 是「活跃筹码流通市值加总」，市值恒定、不存在除权断层，
  所以前收 = 前一交易日的 close。首行（1993-01-03）无前一根，两列留 NULL。

本脚本**不写** ``pre_close``——``kline_0amv`` 刻意没有这一列。
"""
import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server import config, db  # noqa: E402
from server.external import znz_0amv as znz  # noqa: E402

#: 指南针安装目录。可用环境变量 ``ZX_COMPASS_ROOT`` 覆盖。
DEFAULT_COMPASS = Path("D:/application/finance/compass")

#: 库内 code 取解析器的常量，不在这里再硬编码一份——
#: 曾经就是因为两边各写一个 "hysz"，改了另一处就静默写回旧值。
CODE = znz.DEFAULT_CODE


def compass_root() -> Path:
    root = Path(DEFAULT_COMPASS)
    v = getattr(config, "COMPASS_ROOT", None)
    if v:
        root = Path(v)
    return root


def day_vdat_path() -> Path:
    return compass_root() / "ANALYSE" / "Data" / "ChinaStk" / "Z_SK" / "day.vdat"


#: 价格列的存储精度，必须和 kline_0amv 的 DECIMAL(12,2) 对齐。
#: 解析器给的是 float32 原值，**落库前先按这个精度舍入**——因为
#: ``round(a, 2) - round(b, 2) != round(a - b, 2)``，不先舍入的话
#: chg_amt 会和落库后的 close 对不上（实测 8220 行里有 184 行差 0.01）。
PRICE_DP = 2
#: chg_pct 是 DECIMAL(10,2)。
PCT_DP = 2


def build_rows(daily: dict[dt.date, dict]) -> list[list]:
    """把 ``{date: {ohlc, volume, amount}}`` 摊成 INSERT 行。

    chg_amt / chg_pct 依赖**前一交易日**，所以这里必须按日期升序遍历，
    边算边攒行；漏排序会让第一行的涨跌全错。

    首行没有前一根 → chg_amt / chg_pct 为 None（而不是 0，
    0 会被误读成「当日没涨跌」）。

    ⚠️ 关键：**先按 PRICE_DP 舍入 OHLC，再用舍入后的 close 算涨跌**。
       顺序反了就会重演那个 bug——用 float32 原值算 chg_amt，close 却按
       2 位落库，两边各自舍入后差最多一个末位单位（0.01），恒等式崩掉。
       这个顺序是库表列注释「严格等于 close - 前一根 close」的前提。
    """
    rows: list[list] = []
    prev_close: float | None = None
    for d in sorted(daily):
        v = daily[d]
        o = round(float(v["open"]), PRICE_DP)
        h = round(float(v["high"]), PRICE_DP)
        low = round(float(v["low"]), PRICE_DP)
        close = round(float(v["close"]), PRICE_DP)
        chg_amt = None if prev_close is None else round(close - prev_close, PRICE_DP)
        chg_pct = (None if prev_close is None
                   else round((close - prev_close) / prev_close * 100, PCT_DP))
        rows.append([
            CODE, d, o, h, low, close, chg_amt, chg_pct,
            int(round(v["volume"])), int(round(v["amount"])),
        ])
        prev_close = close
    return rows


UPSERT_SQL = (
    "INSERT INTO kline_0amv (code, trade_date, open, high, low, close, "
    "chg_amt, chg_pct, volume, amount) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON DUPLICATE KEY UPDATE open=VALUES(open), high=VALUES(high), "
    "low=VALUES(low), close=VALUES(close), "
    "volume=VALUES(volume), amount=VALUES(amount), "
    "chg_amt=COALESCE(VALUES(chg_amt), chg_amt), "
    "chg_pct=COALESCE(VALUES(chg_pct), chg_pct)"
)


async def run(start: dt.date | None, limit: int | None, dry_run: bool) -> None:
    p = day_vdat_path()
    if not p.exists():
        raise SystemExit(f"找不到 {p}\n  用 ZX_COMPASS_ROOT 指定指南针安装目录")

    print(f"读取 {p}")
    try:
        daily = znz.parse_day_vdat(p)
    except znz.ZnzParseError as e:
        raise SystemExit(str(e))

    lo, hi = min(daily), max(daily)
    print(f"解析到 {len(daily)} 条：{lo} ~ {hi}")

    if start:
        before = len(daily)
        daily = {d: v for d, v in daily.items() if d >= start}
        print(f"过滤 >= {start}：{before} -> {len(daily)} 条")
        if not daily:
            print("没有符合条件的记录")
            return

    rows = build_rows(daily)
    if limit:
        rows = rows[-limit:]
        print(f"仅取最近 {limit} 条")

    if dry_run:
        print("\n--dry-run，不写库。前 3 行：")
        for r in rows[:3]:
            print("   ", r)
        print(f"   ...共 {len(rows)} 行")
        return

    await db.init_pool()
    # 必须 close_pool：否则事件循环销毁时 aiomysql 的 __del__ 会抛
    # "Event loop is closed"，进程退出码变成 1 —— 明明写库成功了，
    # 调度侧却会把它当成失败。
    try:
        async with db.pool().acquire() as conn:
            async with conn.cursor() as cur:
                n = 0
                for i in range(0, len(rows), 500):
                    await cur.executemany(UPSERT_SQL, rows[i:i + 500])
                    n += len(rows[i:i + 500])
                    print(f"  写入 {n}/{len(rows)}")
                await conn.commit()

                await cur.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) "
                                  "FROM kline_0amv")
                total, dmin, dmax = await cur.fetchone()
                # 不用 ✓ 之类非 ASCII 符号：Windows 控制台默认 GBK，
                # 打印会抛 UnicodeEncodeError 并把退出码带成 1。
                print(f"\n[OK] 写入 {len(rows)} 行；"
                      f"kline_0amv 共 {total} 行：{dmin} ~ {dmax}")
    finally:
        await db.close_pool()


def main() -> None:
    ap = argparse.ArgumentParser(description="同步指南针 0AMV 日线到 kline_0amv")
    ap.add_argument("--start", type=str, default=None,
                    help="起始交易日 YYYY-MM-DD（含），默认全历史")
    ap.add_argument("--limit", type=int, default=None,
                    help="仅取最近 N 条")
    ap.add_argument("--dry-run", action="store_true", help="只解析并打印，不写库")
    args = ap.parse_args()

    start = None
    if args.start:
        start = dt.datetime.strptime(args.start, "%Y-%m-%d").date()

    asyncio.run(run(start, args.limit, args.dry_run))


if __name__ == "__main__":
    main()