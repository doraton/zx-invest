"""全市场日线批量同步（Tushare）。

**分页策略**：Tushare ``daily`` 一次调用可以传多个 ts_code（逗号分隔），
所以**按「每批 N 只股票」切分，而不是按日期翻页**。
2346 只上交所股票 @ 200 只/批 = 12 次调用，远在 50 次/分钟 配额之内。

**标的清单来源**：``tdx2db.stock_info``（本地通达信解析，离线、零 API 调用）。
不调 Tushare 的 ``stock_basic``——它是 1 次/小时，拉一次全市场要等 1 小时。

**单位**：Tushare ``vol`` 是手（1 手 = 100 股）、``amount`` 是千元（1 千元 = 1000 元），
与库里的 ``volume`` / ``amount`` 口径一致，**原样落库不换算**。

用法::

    # 上交所 9 月至今
    .\\.venv\\Scripts\\python.exe -W ignore tools\\sync_market.py --market sh --start 20260901
    # 深交所全部历史
    .\\.venv\\Scripts\\python.exe -W ignore tools\\sync_market.py --market sz --start 20200101
    # 预览不写库
    .\\.venv\\Scripts\\python.exe -W ignore tools\\sync_market.py --market sh --dry-run
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymysql  # noqa: E402
import tushare as ts  # noqa: E402

from server import config, db  # noqa: E402

PROJ = Path(__file__).resolve().parents[2]
# K 线已按周期分表，日线的表名就是 kline_day，表内不再有 period 列
# （原先这里是 INSERT INTO kline ... VALUES (%s,'day',...)）。
UPSERT = """
INSERT INTO kline_day (code, trade_date, open, high, low, close,
                       volume, amount, pre_close, chg_pct)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON DUPLICATE KEY UPDATE
    open=VALUES(open), high=VALUES(high), low=VALUES(low), close=VALUES(close),
    volume=VALUES(volume), amount=VALUES(amount),
    pre_close=COALESCE(VALUES(pre_close), pre_close),
    chg_pct=COALESCE(VALUES(chg_pct), chg_pct)
"""


def load_token() -> str:
    p = PROJ / "config.txt"
    if not p.exists():
        raise SystemExit("找不到 config.txt")
    return p.read_text(encoding="utf-8").strip().splitlines()[0].strip()


def fetch_universe(market: str, board: str = "all", list_src: str = "tdx2db") -> list[str]:
    """返回 Tushare 口径的 ts_code 列表，如 ['600570.SH', ...]。

    board:
      all   —— 全部板块（沪市含 688 科创板 / 900 B股）
      main  —— 只要主板 600/601/603/605（沪市 1728 只，不含科创板 618 只）
    """
    if list_src != "tdx2db":
        raise ValueError(list_src)
    c = pymysql.connect(host="127.0.0.1", port=3306, user=config.MYSQL_USER,
                        password=config.MYSQL_PASSWORD, database="tdx2db",
                        charset="utf8mb4")
    cur = c.cursor()
    cur.execute("SELECT code FROM stock_info WHERE code LIKE %s ORDER BY code",
                (f"{market}%",))
    codes = [r[0] for r in cur.fetchall()]
    c.close()
    if board == "main":
        codes = [cd for cd in codes if cd[2:5] in ("600", "601", "603", "605")]
    return [f"{cd[2:]}.{market.upper()}" for cd in codes]


def guard(fn, *args, retries=4, **kwargs):
    for i in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            if "每小时" in msg:
                raise
            if any(k in msg for k in ("频次", "频率", "每分钟")):
                if i == retries - 1:
                    raise
                wait = 20.0 * (i + 1)
                print(f"    限流，等 {wait:.0f}s ({i+1}/{retries})")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("重试耗尽")


def rows_from_df(df):
    for _, r in df.iterrows():
        d = str(r["trade_date"])
        yield (
            f"{str(r['ts_code']).split('.')[1].lower()}{r['ts_code'].split('.')[0]}",
            f"{d[:4]}-{d[4:6]}-{d[6:]}",
            float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
            round(float(r["vol"]), 2),        # 手，原样落库
            round(float(r["amount"]), 2),     # 千元，原样落库
            float(r["pre_close"]) if r.get("pre_close") == r.get("pre_close") else None,
            round(float(r["pct_chg"]), 4) if r.get("pct_chg") == r.get("pct_chg") else None,
        )


async def main() -> int:
    ap = argparse.ArgumentParser(description="全市场日线批量同步")
    ap.add_argument("--db", default="zx_invest")
    ap.add_argument("--market", default="sh", choices=["sh", "sz"])
    ap.add_argument("--start", default="20260901")
    ap.add_argument("--end", default="20260930")
    ap.add_argument("--batch", type=int, default=200, help="每次调用传多少只股票")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    universe = fetch_universe(args.market)
    batches = [universe[i:i + args.batch]
               for i in range(0, len(universe), args.batch)]
    print(f"标的 {len(universe)} 只（{args.market}）-> 分 {len(batches)} 批，"
          f"每批 {args.batch} 只，区间 {args.start}~{args.end}")
    print(f"配额：daily 50 次/分钟，本次共需 {len(batches)} 次调用")

    if args.dry_run:
        print("dry-run，不调用 API")
        for b in batches[:3]:
            print("  " + ",".join(b[:6]) + ",...")
        return 0

    ts.set_token(load_token())
    pro = ts.pro_api()
    config.MYSQL_DB = args.db

    await db.init_pool()
    conn = await db.pool().acquire()
    total, done_codes, t0 = 0, set(), time.time()
    try:
        async with conn.cursor() as cur:
            for bi, batch in enumerate(batches, 1):
                try:
                    df = guard(pro.daily, ts_code=",".join(batch),
                               start_date=args.start, end_date=args.end)
                except Exception as e:
                    print(f"  [{bi}/{len(batches)}] 跳过: {str(e)[:70]}")
                    continue
                if df is None or len(df) == 0:
                    print(f"  [{bi}/{len(batches)}] 0 行")
                    continue
                payload = list(rows_from_df(df))
                done_codes.update(x[0] for x in payload)
                await cur.executemany(UPSERT, payload)
                await conn.commit()
                total += len(payload)
                el = time.time() - t0
                print(f"  [{bi}/{len(batches)}] {len(payload):>6} 行  "
                      f"累计 {total:>7,}  {el:>5.1f}s")
                time.sleep(0.4)   # 50 次/分钟 -> 1.2s 一次即可，这里留大余量

        print(f"\n共写入 {total:,} 行，覆盖 {len(done_codes)} 只股票，"
              f"耗时 {time.time()-t0:.1f}s")

        print("\n=== 落库校验 ===")
        await cur.execute(
            "SELECT COUNT(*), COUNT(DISTINCT code), MIN(trade_date), MAX(trade_date) "
            "FROM kline_day WHERE code LIKE %s",
            (f"{args.market}%",))
        print("  库内该市场:", await cur.fetchone())
        await cur.execute("SELECT COUNT(*) FROM kline_day WHERE pre_close IS NULL")
        n_null = (await cur.fetchone())[0]
        print(f"  pre_close 为空的行: {n_null}  "
              f"({'正常，历史数据是在加列之前入库的' if n_null else '全部已填充'})")
    finally:
        db.pool().release(conn)
        await db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))