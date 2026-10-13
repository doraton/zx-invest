"""从 Tushare 拉数据写入 MySQL ``zx_invest``。

严格对齐既有 schema（kline_day / stock_info / stock_xdxr / sync_state 的真实列）。

实测频次限制（2026-10-07，170 积分）：
- daily50 次/分钟，8000 次/天
- adj_factor   **1 次/小时**（不能批量）
- dividend     另有频次上限

单位约定（项目既有规范）：
- ``volume`` / ``ah_vol`` 存**手**（1 手 = 100 股）
- ``amount`` / ``ah_amt`` 存**千元**（1 千元 = 1000 元）
  —— 与 Tushare 原生口径一致，**全程不做任何换算**
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import tushare as ts  # noqa: E402

from server import config, db, kline_repo  # noqa: E402
from server.external.tdx_kline import canonical_code  # noqa: E402

PROJ = Path(__file__).resolve().parents[2]

# Tushare 市场码 -> 项目 market tinyint（0=深, 1=沪）
MKT = {"SH": 1, "SZ": 0, "BJ": 2}


def split_ts_code(ts_code: str) -> tuple[str, int]:
    """``600570.SH`` → ``('SH.600570', 1)``。

    落库 code 取**大写带点**形态（全库唯一形态，由
    :func:`server.external.tdx_kline.canonical_code` 定义），
    也正好是前端 ``formatCode`` 想渲染成的样子。
    """
    num, _, mk = ts_code.partition(".")
    return canonical_code(ts_code), MKT.get(mk, 1)


def _num(v) -> float:
    """pandas 缺失值 -> 0.0。

    **不能写 `float(v or 0)`**：pandas 的 NaN 是 float，`bool(nan)` 为 True，
    所以 `nan or 0` 返回的是 nan 而不是 0，NaN 会被 pymysql 抛
    「nan can not be used with MySQL」。dividend 接口的空字段全是 NaN。
    """
    if v is None:
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f != f else f   # f != f 即 NaN


def _num2_or_none(v) -> float | None:
    """pandas 缺失值 -> None，保留 2 位小数。**只给可空的 ah_vol / ah_amt 用**。

    盘后固定交易**不是每天都发生**，没有就是没有。落 0 会被读成
    「当天有盘后交易但成交为零」，与事实不符——库里 NULL 与 0 是两种语义。
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, 2)   # f != f 即 NaN


def _txt(v) -> str:
    """pandas 缺失值 -> ""。同 _num，NaN 不能直接当字符串用。"""
    if v is None:
        return ""
    s = str(v)
    return "" if s in ("nan", "NaN", "None") else s


def load_token() -> str:
    p = PROJ / "config.txt"
    if not p.exists():
        raise SystemExit("找不到 config.txt（项目根，第一行为裸 token）")
    tok = p.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    if not tok:
        raise SystemExit("config.txt 为空")
    return tok


def _fname(fn) -> str:
    return getattr(fn, "__name__", None) or getattr(fn, "func", None).__name__ \
        if hasattr(fn, "func") else type(fn).__name__


class RateLimited(Exception):
    """命中频次限制（1次/分钟 或 1次/小时）。"""


def guard(fn, *args, retries: int = 3, **kwargs):
    """命中频次限制时退避重试。

    实测（2026-10-07，170 积分）各接口档位：
      daily / weekly / monthly  -> 50 次/分钟，退避 65s 可过
      stock_basic / adj_factor -> 1 次/小时，等也没用，直接抛 RateLimited 降级
    """
    name = _fname(fn)
    for i in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            if "每小时" in msg:
                raise RateLimited(f"{name}: 1次/小时限制，本次跳过") from e
            if any(k in msg for k in ("频次", "频率", "每分钟")):
                if i == retries - 1:
                    raise RateLimited(f"{name}: 退避 {retries} 次仍被限流") from e
                print(f"  [{name}] 限流，等满 65s 后重试 ({i+1}/{retries})")
                time.sleep(65)
                continue
            raise
    raise RateLimited(f"{name}: 重试 {retries} 次仍受限")


async def pull_stock(cur, pro, ts_code: str) -> int:
    """stock_basic 是 1次/小时限流。拉不到就降级为「只留代码」，
    名称从本地 .tnf / 已有库取，绝不因为元数据阻断日线同步。"""
    try:
        raw = guard(pro.stock_basic, ts_code=ts_code,
                    fields="ts_code,symbol,name,industry,market,list_date")
    except RateLimited as e:
        print(f"  stock_info: 跳过（{e}）")
        return 0
    if raw is None or len(raw) == 0:
        return 0
    r = raw.iloc[0]
    code, mkt = split_ts_code(ts_code)
    num = ts_code.partition(".")[0]
    await cur.execute(
        "INSERT INTO stock_info (code, raw_code, market, name, ipo_date, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,NOW()) "
        "ON DUPLICATE KEY UPDATE name=VALUES(name), ipo_date=VALUES(ipo_date), "
        "updated_at=VALUES(updated_at)",
        (code, num, mkt, r.get("name"), r.get("list_date")))
    print(f"  stock_info: {code} {r.get('name')} 上市={r.get('list_date')} "
          f"行业={r.get('industry')}")
    return 1


async def _upsert_kline(cur, batch: list[dict]) -> int:
    """写日线到 kline_day。

    列顺序对齐 Tushare pro.daily 的输出字段序（见 docs/tushare-reference.md §2.1）：
    code / trade_date / open / high / low / close / pre_close / chg_amt / chg_pct /
    volume / amount / ah_vol / ah_amt。

    两处改名：ts_code → code；change → chg_amt（`change` 是 MySQL 保留字，
    且与 chg_pct 对仗）。不换算：vol 本就是手、amount 本就是千元。

    pre_close / chg_pct 是 Tushare daily 直接给的，必须一起落库：
    pre_close 是「已除权的前收盘」，自算复权因子和筹码分布都靠它，
    丢了只能重拉全历史才能补。用 COALESCE 让缺失值不覆盖已有值。
    """
    if not batch:
        return 0
    sql = ("INSERT INTO kline_day (code, trade_date, open, high, low, close, "
           "pre_close, chg_amt, chg_pct, volume, amount, ah_vol, ah_amt) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
           "ON DUPLICATE KEY UPDATE open=VALUES(open), high=VALUES(high), "
           "low=VALUES(low), close=VALUES(close), volume=VALUES(volume), "
           "amount=VALUES(amount), "
           "pre_close=COALESCE(VALUES(pre_close), pre_close), "
           "chg_amt=COALESCE(VALUES(chg_amt), chg_amt), "
           "chg_pct=COALESCE(VALUES(chg_pct), chg_pct), "
           "ah_vol=VALUES(ah_vol), ah_amt=VALUES(ah_amt)")
    cols = ("code", "trade_date", "open", "high", "low", "close",
            "pre_close", "chg_amt", "chg_pct", "volume", "amount",
            "ah_vol", "ah_amt")
    await cur.executemany(sql, [[r.get(c) for c in cols] for r in batch])
    return len(batch)


async def materialize_periods(cur, code: str) -> int:
    """由日线物化 week/month/quarter/year，零 API 调用。

    **SQL 直接复用** ``kline_repo.materialize_periods_on``，不再自己抄一份。
    这里曾经维护着与 kline_repo 几乎相同的一份，只有细微差别（少写 pre_close /
    chg_amt / chg_pct 三列），口径漂移时极难发现——两处都要改，还得记得两处都改。

    口径见 kline_repo.materialize_periods 的 docstring。

    四个周期落**同一张 kline_period 表**，靠 period 列区分，
    主键 (code, period, trade_date) —— 同一交易日在四个桶里各有一根 bar，
    不带 period 会互相覆盖。
    """
    written = await kline_repo.materialize_periods_on(
        cur, code, print_fn=lambda p, n: print(f"  物化 {p}: {n} 行"))
    return sum(written.values())


async def pull_daily(cur, pro, ts_code: str, start: str, end: str) -> int:
    code, _ = split_ts_code(ts_code)
    n, offset = 0, 0
    while True:
        # 必须显式给 fields：ah_vol / ah_amount 在官网字段表里「默认显示」是 N，
        # 不写 fields 就拿不到（官网示例见 docs/tushare-reference.md §2.1）。
        # 字段顺序与官网输出参数表一致，便于逐字段对账。
        raw = guard(pro.daily, ts_code=ts_code, start_date=start, end_date=end,
                    offset=offset, limit=800,
                    fields=("ts_code,trade_date,open,high,low,close,pre_close,"
                            "change,pct_chg,vol,amount,ah_vol,ah_amount"))
        if raw is None or len(raw) == 0:
            break
        batch = [{
            "code": code,
            # 原样落库：trade_date 是 'YYYYMMDD' 字符串，DATE 列会自行解析
            "trade_date": r["trade_date"],
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "pre_close": (float(r["pre_close"])
                          if r.get("pre_close") == r.get("pre_close") else None),
            # chg_amt ≡ close - pre_close，源接口直接给的，一并落库以对齐字段序
            "chg_amt": (round(float(r["change"]), 4)
                        if r.get("change") == r.get("change") else None),
            "chg_pct": (round(float(r["pct_chg"]), 4)
                        if r.get("pct_chg") == r.get("pct_chg") else None),
            "volume": round(float(r["vol"]), 2),        # 手，原样落库
            "amount": round(float(r["amount"]), 2),     # 千元，原样落库
            # 盘后固定交易量额，2026-07-06 起才有数据，两列可空。
            # 空值有**两种**形态：早期行是 None，新行缺失列才是 NaN，
            # 只判 `x == x`（NaN）会让 float(None) 抛 TypeError，中途中断整轮拉取。
            # 没有盘后交易写 **NULL** 而不是 0（见 _num2_or_none）。
            "ah_vol": _num2_or_none(r.get("ah_vol")),
            "ah_amt": _num2_or_none(r.get("ah_amount")),
        } for _, r in raw.iterrows()]
        n += await _upsert_kline(cur, batch)
        offset += len(raw)
        print(f"  daily: 已拉 {n} 行")
        if len(raw) < 800:
            break
        time.sleep(1.3)
    return n


async def pull_period(cur, pro, ts_code: str, start: str, end: str,
                      api, period: str) -> int:
    """**故意不调用 Tushare 的 weekly/monthly 接口。**

    实测（170 积分）这两个接口是 **1 次/小时**，批量同步完全不可行。
    项目既有约定就是「派生周期一律由日线物化，不向数据源单独拉取」
    （见 src/server/kline_repo.py:materialize_periods 与 database-schema.md §7.2），
    所以这里直接复用物化逻辑：口径与其它数据源一致，且零 API 调用。
    """
    code, _ = split_ts_code(ts_code)
    return await materialize_periods(cur, code)


async def pull_xdxr(cur, pro, ts_code: str) -> int:
    """除权除息。schema: category/name/bonus_ratio/allot_ratio/cash_dividend/reduce_ratio"""
    try:
        raw = guard(pro.dividend, ts_code=ts_code)
    except RateLimited as e:
        print(f"  stock_xdxr: 跳过（{e}）")
        return 0
    if raw is None or len(raw) == 0:
        print("  stock_xdxr: 0 条")
        return 0
    code, _ = split_ts_code(ts_code)
    rows = []
    for _, r in raw.iterrows():
        ex_raw = _txt(r.get("ex_date"))
        if not ex_raw:
            continue
        ex = f"{ex_raw[:4]}-{ex_raw[4:6]}-{ex_raw[6:]}"
        proc = _txt(r.get("div_proc"))
        # 分类：0=除权除息 1=除息 2=送转 3=非分红（与项目既有约定对齐）
        if _num(r.get("stk_div")) > 0 or _num(r.get("stk_bo_rate")) > 0:
            category = 2
        elif _num(r.get("cash_div")) > 0 or _num(r.get("cash_div_tax")) > 0:
            category = 1
        else:
            category = 0
        rows.append((
            code, ex, category, proc or "分红",
            _num(r.get("stk_div")),            # 送股
            _num(r.get("stk_co_rate")),        # 配股
            _num(r.get("cash_div_tax")),       # 税前每股派息（元）
            0.0, 0.0, None, None, None, None,
        ))
    sql = ("INSERT IGNORE INTO stock_xdxr (code, ex_date, category, name, bonus_ratio, "
           "allot_ratio, cash_dividend, reduce_ratio, allot_price, share_before, "
           "share_after, float_before, float_after) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
    await cur.executemany(sql, rows)
    print(f"  stock_xdxr: {len(rows)} 条")
    return len(rows)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Tushare -> MySQL(zx_invest) 同步")
    ap.add_argument("--db", default="zx_invest")
    ap.add_argument("--code", default="600570.SH")
    ap.add_argument("--start", default="20240101")
    ap.add_argument("--end", default="20260930")
    ap.add_argument("--no-weekly-monthly", action="store_true")
    ap.add_argument("--skip-meta", action="store_true",
                    help="跳过 stock_basic / dividend（二者仅 5 次/天，"
                         "只想拉日线时用本开关，避免无谓的 65s 退避）")
    ap.add_argument("--drop", action="store_true", help="先清空该股 kline/xdxr")
    args = ap.parse_args()

    ts.set_token(load_token())
    pro = ts.pro_api()
    config.MYSQL_DB = args.db
    code, _ = split_ts_code(args.code)

    await db.init_pool()
    conn = await db.pool().acquire()
    try:
        async with conn.cursor() as cur:
            if args.drop:
                # 日线与派生周期分属两张表，都要清，否则会留下孤立的派生行
                await cur.execute("DELETE FROM kline_day WHERE code=%s", (code,))
                await cur.execute("DELETE FROM kline_period WHERE code=%s", (code,))
                await cur.execute("DELETE FROM stock_xdxr WHERE code=%s", (code,))
                await conn.commit()
                print(f"已清空 {code}")

            print(f"\n=== {args.db} / {args.code} / {args.start}~{args.end} ===")
            if not args.skip_meta:
                await pull_stock(cur, pro, args.code)
                await conn.commit()
            else:
                print("  stock_info: 跳过（--skip-meta）")

            n = await pull_daily(cur, pro, args.code, args.start, args.end)
            await conn.commit()

            if not args.no_weekly_monthly:
                await pull_period(cur, pro, args.code, args.start, args.end,
                                  None, None)
                await conn.commit()

            if not args.skip_meta:
                await pull_xdxr(cur, pro, args.code)
                await conn.commit()
            else:
                print("  stock_xdxr: 跳过（--skip-meta）")

            # first_date / last_date 必须取**库内实际范围**，不是本次请求的窗口。
            # 原因（见 db.py 的 sync_state 建表注释）：
            #   请求窗口是「这次想拉哪一段」，库内范围是「实际有哪些数据」。
            #   若记窗口，下次只想增量最近一个月时会把 first_date 改写成那个月的起点，
            #   历史的起始日期就此丢失，增量逻辑随后会误判「2003~2026 从没同步过」而全量重拉。
            #   --start 传的是 Tushare 能接受的最小日期（如 19901219），照抄必然是错的。
            await cur.execute("SELECT MIN(trade_date), MAX(trade_date) FROM kline_day "
                              "WHERE code=%s", (code,))
            _lo, _hi = await cur.fetchone()
            await cur.execute(
                "INSERT INTO sync_state (code, period, last_sync, first_date, "
                "last_date, status, message) "
                "VALUES (%s,'day',NOW(),%s,%s,'ok','tushare sync') "
                "ON DUPLICATE KEY UPDATE last_sync=NOW(), first_date=VALUES(first_date), "
                "last_date=VALUES(last_date), status='ok', message=VALUES(message)",
                (code, _lo, _hi))
            await conn.commit()
            if _lo is not None:
                _req = (f"{args.start[:4]}-{args.start[4:6]}-{args.start[6:]}",
                        f"{args.end[:4]}-{args.end[4:6]}-{args.end[6:]}")
                if (str(_lo), str(_hi)) != _req:
                    print(f"  sync_state 记库内实际范围 {_lo} ~ {_hi}"
                          f"（请求窗口 {_req[0]} ~ {_req[1]}，以实际为准）")

            print("\n=== 落库汇总 ===")
            # 日线一张表、派生周期一张表（period 列区分），不再有 kline_<period>
            await cur.execute(
                "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) "
                "FROM kline_day WHERE code=%s", (code,))
            cnt, mn, mx = await cur.fetchone()
            print(f"  {'day':<8} {cnt or 0:>5} 行   {mn} ~ {mx}")
            await cur.execute(
                "SELECT period, COUNT(*), MIN(trade_date), MAX(trade_date) "
                "FROM kline_period WHERE code=%s GROUP BY period "
                "ORDER BY FIELD(period,'week','month','quarter','year')", (code,))
            got = {r[0]: r for r in await cur.fetchall()}
            for _p in ("week", "month", "quarter", "year"):
                r = got.get(_p)
                if r is None:
                    print(f"  {_p:<8}       0 行   （未物化）")
                else:
                    print(f"  {_p:<8} {r[1] or 0:>5} 行   {r[2]} ~ {r[3]}")

            await cur.execute(
                "SELECT COUNT(*), MIN(ex_date), MAX(ex_date), SUM(cash_dividend) "
                "FROM stock_xdxr WHERE code=%s", (code,))
            cnt, mn, mx, s = await cur.fetchone()
            print(f"\n  stock_xdxr: {cnt} 条  {mn} ~ {mx}  累计税前派息={s} 元/股")

            await cur.execute(
                "SELECT name, ipo_date, market FROM stock_info WHERE code=%s", (code,))
            r = await cur.fetchone()
            print(f"  stock_info: {r}")
    finally:
        db.pool().release(conn)
        await db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))