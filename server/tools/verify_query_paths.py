"""验证 market.query_klines 的两条读取路径口径一致。

1. **读库路径**：kline_period 表里已有物化的 week/month/quarter/year 行
2. **回退路径**：把某周期的最后一行临时删掉，让 query_klines 走「查询时窗口函数聚合」

两条路径应给出完全相同的结果。脚本结束前会把临时删掉的行补回。

K 线分 kline_day / kline_period 两张表，派生周期用 period 列区分。

用法::

    .\\.venv\\Scripts\\python.exe -W ignore tools\\verify_query_paths.py SH.600570
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import aiomysql  # noqa: E402

from server import db, market  # noqa: E402

COUNT = 5

#: 默认标的。⚠️ 必须是**大写带点**形态（tdx_kline.canonical_code），
#: 旧格式 sh600570 在库里一条都匹配不到，脚本会一路「库内无物化数据，跳过」
#: 然后打印「两条路径口径一致」——**什么都没验就报通过**（2026-10-13 修）。
DEFAULT_CODE = "SH.600570"

# ⚠️ 列数必须与下面的占位符个数严格一致。此前这里是 9 列配 8 个占位符，
#    而还原语句在 `finally` 里——它一抛，**刚删掉的那行就再也回不去了**。
#    2026-10-13 一并修好，并把涨跌三列纳入比对（此前只比 OHLC，
#    而那正是本次改动新增的部分）。
#    同日又移除 pre_close：kline_period 那列已删（恒等于上一根 close，纯冗余），
#    留着会让 SELECT 报 Unknown column，整个脚本直接跑不起来。
_COLS = ("code, period, trade_date, open, high, low, close, volume, amount, "
         "chg_amt, chg_pct, ah_vol, ah_amt")
_MARKS = ",".join(["%s"] * 13)

#: ⚠️ 必须 strip。``_COLS.split(",")`` 切出来的是 ``['code', ' period', ...]``，
#:    带前导空格；而 DictCursor 的行 key 是去空格的，直接 ``saved[c]`` 会 KeyError。
#:    这个 KeyError 发生在 **finally 里**——被删掉的那行就再也回不去了
#:    （2026-10-13 实际发生过一次，week 少 1 行）。
_COL_NAMES = tuple(c.strip() for c in _COLS.split(","))


async def main_async(code: str) -> int:
    await db.init_pool()
    failures = 0
    for period in ("week", "month", "quarter", "year"):
        stored, _ = await market.query_klines(code, period, count=COUNT)
        if not stored:
            print(f"{period:<8} 库内无物化数据，跳过")
            continue
        last = stored[-1]
        table = db.kline_table(period)

        async with db.pool().acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # 删之前先把**整行原样**抓下来。不能用上面 bars 里的 last 还原：
                # bar 里的 amount 装的是成交量、turnover 才是成交额（见 market._to_bars），
                # 而且 _to_bars 根本不下发 pre_close —— 少还原的列会变 NULL，
                # 直到下次物化才恢复，而本脚本正是拿「最后一行」当对照的。
                await cur.execute(
                    f"SELECT {', '.join(_COL_NAMES)} FROM {table} "
                    f"WHERE code=%s AND period=%s AND trade_date=%s",
                    (code, period, last["datetime"]))
                saved = await cur.fetchone()
                # period 条件不能少：四个派生周期在同一 trade_date 上各有一根，
                # 只按 (code, trade_date) 删会把它们一起删掉。
                await cur.execute(
                    f"DELETE FROM {table} WHERE code=%s AND period=%s AND trade_date=%s",
                    (code, period, last["datetime"]))
                try:
                    derived, _ = await market.query_klines(code, period, count=COUNT)
                finally:
                    # 无论成败都要把数据放回去。还原用的是删之前抓下的整行。
                    # ⚠️ ah_vol / ah_amt 也在快照里：这两列**可空**，
                    #    漏掉的话还原出来的行会变成 NULL，把「该周期有盘后交易」
                    #    悄悄抹成「没有」——而且要等到下次物化才恢复。
                    await cur.execute(
                        f"INSERT INTO {table} ({_COLS}) VALUES ({_MARKS}) "
                        f"ON DUPLICATE KEY UPDATE open=VALUES(open), high=VALUES(high), "
                        f"low=VALUES(low), close=VALUES(close), volume=VALUES(volume), "
                        f"amount=VALUES(amount), "
                        f"chg_amt=VALUES(chg_amt), chg_pct=VALUES(chg_pct), "
                        f"ah_vol=VALUES(ah_vol), ah_amt=VALUES(ah_amt)",
                        tuple(saved[c] for c in _COL_NAMES))
                    await conn.commit()

        # 删掉最后一行后，回退聚合会**多算出**那一根（它是从日线重算的），
        # 所以两边的窗口天然错开一根。正确做法是按日期取交集比对。
        dmap = {x["datetime"]: x for x in derived}
        common = [x for x in stored if x["datetime"] in dmap]
        mismatches = [
            (x["datetime"], k, x[k], dmap[x["datetime"]][k])
            for x in common for k in ("open", "high", "low", "close", "chg_amt",
                                      "chg_pct")
            if x[k] is not None and dmap[x["datetime"]][k] is not None
            and abs(x[k] - dmap[x["datetime"]][k]) >= 0.011
        ]
        print(f"{period:<8} 读库 {len(stored)} 根 / 回退聚合 {len(derived)} 根，"
              f"可比 {len(common)} 根 -> "
              + ("口径一致" if not mismatches else f"{len(mismatches)} 处不一致"))
        for d in mismatches[:3]:
            print(f"    {d[0]} {d[1]}: 读库={d[2]} 聚合={d[3]}")
        if not common or mismatches:
            failures += 1

    await db.close_pool()
    print(f"\n结果: {'两条路径口径一致' if failures == 0 else f'{failures} 个周期不一致'}")
    return 0 if failures == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("code", nargs="?", default=DEFAULT_CODE)
    args = ap.parse_args()
    return asyncio.run(main_async(args.code))


if __name__ == "__main__":
    raise SystemExit(main())
