"""临时调研：指数现在实际存在哪。用完即删。"""
import asyncio
import os
import re
import sys

sys.path.insert(0, "src")
os.environ.setdefault("ZX_MYSQL_DB", "tsdb")

from server import db  # noqa: E402

# 前端 chartOptions.INDEX_CODE_RE 的等价物
IDX = re.compile(r"^(sh|sz)(000|399)\d{3}$")


async def main() -> None:
    await db.init_pool()
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT code, COUNT(*), MIN(trade_date), MAX(trade_date) "
                "FROM kline_day GROUP BY code ORDER BY code")
            rows = await cur.fetchall()
            print(f"kline_day 里的全部代码: {len(rows)} 个")
            for c, n, a, b in rows:
                kind = "指数" if IDX.match(c) else "个股"
                print(f"  {c:12} {kind}  {n:6} 根  {a} ~ {b}")

            print("\n正则自检（前端 INDEX_CODE_RE 的 Python 等价）:")
            for probe in ("sh000001", "sz399001", "sh000300", "sz399006",
                          "sh000688", "sh600570", "sz000001", "sh999999"):
                print(f"  {probe:10} -> {'指数' if IDX.match(probe) else '个股'}")

            codes = [r[0] for r in rows]
            if codes:
                ph = ",".join(["%s"] * len(codes))
                await cur.execute(
                    f"SELECT code, name, pinyin, market FROM stock_info WHERE code IN ({ph})", codes)
                print("\nstock_info 表里的对应行:", await cur.fetchall())

            # stock_info 表里有多少行会被这个正则误判（说明正则的覆盖缺口）
            await cur.execute("SELECT code, name FROM stock_info")
            allrows = await cur.fetchall()
            hit = [r for r in allrows if IDX.match(r[0])]
            print(f"\nstock 表 {len(allrows)} 行里，被正则判成「指数」的: {len(hit)}")
            for r in hit[:10]:
                print("   ", r)

            # tnf 里的指数候选（白名单排除了 000/399 段，但 999xxx 之类呢）
            from server.external import tdx_local
            nm = tdx_local.load_name_map()
            print("\n白名单 _A_SHARE_PREFIXES 覆盖情况:")
            white = {"sh": {"600", "601", "603", "605", "688"},
                     "sz": {"000", "001", "002", "003", "300", "301"}}
            for mkt, tbl in nm.items():
                seg = {}
                for raw in tbl:
                    s = raw[:3]
                    seg[s] = seg.get(s, 0) + 1
                inw = sum(v for s, v in seg.items() if s in white.get(mkt, ()))
                out = sum(v for s, v in seg.items() if s not in white.get(mkt, ()))
                print(f"  {mkt}: 白名单内 {inw} / 排除 {out}")
    await db.close_pool()


asyncio.run(main())