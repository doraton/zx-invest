"""把库里的同步结果打成人可读汇总（给验收/汇报用）。

用法::

    .\\.venv\\Scripts\\python.exe -W ignore tools\\show_db.py
    .\\.venv\\Scripts\\python.exe -W ignore tools\\show_db.py sh600570
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import aiomysql  # noqa: E402

from server import db  # noqa: E402

PERIODS = ("day", "week", "month", "quarter", "year")
ORDER = "FIELD(period, 'day','week','month','quarter','year')"


async def show(conn) -> None:
    async with conn.cursor(aiomysql.DictCursor) as cur:
        # K 线按周期分表，表名即周期，逐张表汇总。
        print("=== kline ===")
        for period in PERIODS:
            await cur.execute(
                f"SELECT code, COUNT(*) n, MIN(trade_date) a, MAX(trade_date) b "
                f"FROM kline_{period} GROUP BY code ORDER BY code")
            print(f"-- kline_{period} --")
            for r in await cur.fetchall():
                print(f"  {r['code']:<10} {period:<8} {r['n']:>5} 根  {r['a']} ~ {r['b']}")

        await cur.execute(
            f"SELECT code, period, status, first_date, last_date, message FROM sync_state "
            f"ORDER BY code, {ORDER}")
        print("=== sync_state ===")
        for r in await cur.fetchall():
            print(f"  {r['code']:<10} {r['period']:<8} {r['status']:<7} "
                  f"{r['first_date']} ~ {r['last_date']}  {r['message']}")

        await cur.execute("SELECT * FROM stock_info")
        print("=== stock_info ===")
        for r in await cur.fetchall():
            print("  " + "  ".join(f"{k}={v}" for k, v in r.items() if v is not None))

        await cur.execute("SELECT * FROM stock_info_finance")
        print("=== stock_finance ===")
        for r in await cur.fetchall():
            items = [f"{k}={v}" for k, v in r.items() if v is not None]
            print("  " + "  ".join(items))

        await cur.execute("SELECT * FROM stock_info_xdxr ORDER BY ex_date DESC LIMIT 6")
        print("=== stock_xdxr（最近 6 条）===")
        for r in await cur.fetchall():
            items = [f"{k}={v}" for k, v in r.items() if v is not None]
            print("  " + "  ".join(items))
        await cur.execute("SELECT COUNT(*) n FROM stock_xdxr")
        print(f"  共 {(await cur.fetchone())['n']} 条")


async def main_async() -> int:
    await db.init_pool()
    async with db.pool().acquire() as conn:
        await show(conn)
    await db.close_pool()
    return 0


def main() -> int:
    argparse.ArgumentParser().parse_args()
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
