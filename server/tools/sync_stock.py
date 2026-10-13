"""完整同步单个标的：日线全历史 + 周/月/季/年物化 + 基本面 + 除权除息。

用法::

    .\\.venv\\Scripts\\python.exe -W ignore tools\\sync_stock.py sh600570
    .\\.venv\\Scripts\\python.exe -W ignore tools\\sync_stock.py sh600570 --max-bars 4000
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server import db, sync  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description="通达信 → MySQL 完整同步")
    ap.add_argument("code", nargs="?", default="sh600570", help="标的，如 sh600570")
    ap.add_argument("--max-bars", type=int, default=4000, help="日线最多拉多少根（翻页 800/次）")
    ap.add_argument("--no-derive", action="store_true", help="跳过周/月/季/年物化")
    ap.add_argument("--no-finance", action="store_true", help="跳过基本面")
    ap.add_argument("--no-xdxr", action="store_true", help="跳过除权除息")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    result = await sync.sync_stock(
        args.code, max_bars=args.max_bars, derive=not args.no_derive,
        with_finance=not args.no_finance, with_xdxr=not args.no_xdxr,
        verbose=not args.json)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    await db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
