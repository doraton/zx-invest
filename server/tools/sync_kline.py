"""手动触发通达信 K 线同步。

用法（在 server/ 目录下）：
    .venv/Scripts/python.exe -W ignore tools/sync_kline.py
    .venv/Scripts/python.exe -W ignore tools/sync_kline.py --days 60 --dry-run
    .venv/Scripts/python.exe -W ignore tools/sync_kline.py --code sh000001 --show
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server import config, db, kline_repo  # noqa: E402
from server.external import tdx_kline  # noqa: E402
from server.sync import sync_tdx_kline  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="通达信 K 线同步（MySQL 落库）")
    ap.add_argument("--code", default=config.MVP_TARGET,
                    help=f"标的 code，默认 {config.MVP_TARGET}")
    ap.add_argument("--period", default=tdx_kline.PERIOD_DAY,
                    choices=[tdx_kline.PERIOD_DAY, tdx_kline.PERIOD_WEEK,
                             tdx_kline.PERIOD_MONTH])
    ap.add_argument("--days", type=int, default=30, help="取最近多少根，默认 30")
    ap.add_argument("--dry-run", action="store_true", help="只拉取不写库")
    ap.add_argument("--show", type=int, default=0, metavar="N", help="完成后打印最近 N 根")
    args = ap.parse_args()

    async def run() -> int:
        if args.dry_run:
            rows = tdx_kline.fetch_bars(args.code, period=args.period, days=args.days)
            print(f"[dry-run] {args.code} {args.period} 共 {len(rows)} 根")
            for r in rows[:3]:
                print("  ", r)
            print("   ...")
            for r in rows[-3:]:
                print("  ", r)
            return 0

        result = await sync_tdx_kline(code=args.code, period=args.period, days=args.days)
        print("[result]", result)

        if args.show:
            rows = await kline_repo.list_recent(args.code, args.period, args.show)
            print(f"[db] 最近 {len(rows)} 根:")
            for r in reversed(rows):
                print(f"  {r['datetime']}  O={r['open']:<10} H={r['high']:<10} "
                      f"L={r['low']:<10} C={r['close']:<10} vol={r['volume']:<16} amt={r['amount']}")
        await db.close_pool()
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
