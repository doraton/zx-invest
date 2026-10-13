"""按 ``kline_day`` 里实际有行情的代码，补齐 ``stock_info`` 主表的元信息行。

为什么需要它
------------
``stock_info`` 表本该由 ``sync.sync_stock()`` 从 Tushare ``stock_basic`` 落库，
但那个接口每天只有 5 次额度，2026-10-08 已耗尽，于是 ``sync_tushare.py`` 只能
加 ``--skip-meta`` 绕过，``stock_info`` / ``stock_finance`` / ``stock_xdxr`` 全是空表。
连带后果：

- ``/api/stock/search`` 恒返回空（它查的就是 ``stock_info`` 表）
- 行情列表拿不到证券名称，只能显示原始代码
- 「恒生电子」这个名称只存在于前端硬编码的 ``INDICES`` 里，库里没有

本工具**只为「已经有行情数据」的代码补行**，而不是把本地 TDX 名称表里
8896 只 A 股全灌进去。理由：全量灌会让「库里有多少只股票」产生歧义——
``stock_info`` 8896 行而 ``kline_day`` 只有 1 只。行情列表以 ``kline_day`` 为准，
``stock_info`` 只承担元信息补充。行业 / 流通股本这些等 Tushare 额度恢复后
由 ``sync_stock`` 正常补齐。

名称来源是本地通达信客户端缓存 ``T0002/hq_cache/{shs,szs}.tnf``
（``tdx_local.load_name_map()``，约 8896 条），不吃网络额度。

用法::

    .\\.venv\\Scripts\\python.exe tools\\backfill_stock_from_klines.py --dry-run
    .\\.venv\\Scripts\\python.exe tools\\backfill_stock_from_klines.py
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("ZX_MYSQL_DB", "zx_invest")

import aiomysql  # noqa: E402

from server import db, stock_repo  # noqa: E402
from server.external import tdx_local  # noqa: E402
from server.external.tdx_kline import split_code  # noqa: E402


async def kline_codes() -> list[str]:
    """``kline_day`` 里出现过的全部代码（行情事实的来源）。"""
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT DISTINCT code FROM kline_day ORDER BY code")
            return [r[0] for r in await cur.fetchall()]


async def existing_codes() -> set[str]:
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT code FROM stock_info")
            return {r[0] for r in await cur.fetchall()}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="只打印将要写入的行，不落库")
    args = ap.parse_args()

    await db.init_pool()
    try:
        codes = await kline_codes()
        have = await existing_codes()
        todo = [c for c in codes if c not in have]
        print(f"kline_day 里的代码   : {len(codes)} 个 -> {codes}")
        print(f"stock_info 表已有         : {len(have)} 个 -> {sorted(have)}")
        print(f"待补                 : {len(todo)} 个 -> {todo}")
        if not todo:
            print("\n无需补齐。")
            return 0

        rows = []
        for code in todo:
            # market 列是 TINYINT（db.py: `0=深 1=沪`），复用 tdx_kline.split_code
            # 的 pytdx 常量，别自己拼字符串——upsert 会因为 'sh' 不是整数而报错。
            try:
                market, raw = split_code(code)
            except ValueError as e:
                print(f"  {code}  跳过：{e}")
                continue
            name = tdx_local.get_security_name(code)
            rows.append({
                "code": code,
                "raw_code": raw,
                "market": market,
                "name": name,
                # 下面几项目前没有来源，留 NULL；sync_stock 有额度后会自动补上
                "ipo_date": None,
                "total_share": None,
                "float_share": None,
                "industry_code": None,
                "province_code": None,
            })
            print(f"  {code}  market={market}  raw={raw}  name={name!r}"
                  + ("" if name else "   <-- 本地名称表未命中，名称将留空"))

        if not rows:
            print("\n没有可写入的行。")
            return 1

        if args.dry_run:
            print("\n--dry-run：未写入。")
            return 0

        for row in rows:
            await stock_repo.upsert_stock(row)
        print(f"\n已写入 {len(rows)} 行。")

        # 回读确认，避免「以为写进去了」
        async with db.pool().acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT code, raw_code, market, name FROM stock_info ORDER BY code")
                got = await cur.fetchall()
        print("回读 stock_info 表：")
        for r in got:
            print(f"  {r['code']}  {r['name']}  market={r['market']}  raw={r['raw_code']}")
        return 0
    finally:
        await db.close_pool()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))