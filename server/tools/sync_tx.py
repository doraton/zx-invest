"""把腾讯 K 线同步进指定库（默认 ``tq-stock``）。

**对照实验的核心脚本**：拉取逻辑换成 ``external/tx_kline``，
但**下游完全复用 pytdx 那条链路的同一套代码**——同一个
``kline_repo.upsert_klines``、同一个 ``kline_repo.materialize_periods``、
同一张表结构、同一个库名配置入口。

这样「数据源」就是唯一变量，派生周期的差异只可能来自日线本身，
而不是来自两套各写各的聚合 SQL。

用法::

    # 拉到 tq-stock（对照实验用）
    .\\.venv\\Scripts\\python.exe -W ignore tools\\sync_tx.py sh600570 --db tq-stock

    # 拉到主库 mysqldb（真要换源时）
    .\\.venv\\Scripts\\python.exe -W ignore tools\\sync_tx.py sh600570

    # 不做周/月/季/年物化，只留日线
    .\\.venv\\Scripts\\python.exe -W ignore tools\\sync_tx.py sh600570 --db tq-stock --no-derive
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server import config, db, kline_repo  # noqa: E402
from server.external import tx_kline  # noqa: E402
from server.external.tdx_kline import PERIOD_DAY  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description="腾讯 K 线 → MySQL")
    ap.add_argument("code", nargs="?", default="sh600570", help="标的，如 sh600570")
    ap.add_argument("--db", default="tq-stock", help="目标库名（默认 tq-stock 对照实验用）")
    ap.add_argument("--max-bars", type=int, default=4000, help="日线最多拉多少根")
    ap.add_argument("--adjust", default="", choices=["", "qfq", "hfq"],
                    help="复权：空=不复权（默认，与 pytdx 侧口径一致）")
    ap.add_argument("--no-derive", action="store_true", help="跳过周/月/季/年物化")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    config.MYSQL_DB = args.db
    await db.init_pool()

    result = {"code": args.code, "db": args.db, "adjust": args.adjust or "none"}
    try:
        t0 = time.time()
        rows = tx_kline.fetch_history(args.code, max_bars=args.max_bars, adjust=args.adjust)
        result["fetch_seconds"] = round(time.time() - t0, 2)
        result["fetched"] = len(rows)
        if not rows:
            result["status"] = "empty"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1

        result["range"] = [rows[0]["trade_date"], rows[-1]["trade_date"]]
        await kline_repo.set_sync_state(
            args.code, PERIOD_DAY, "running",
            first_date=rows[0]["trade_date"], last_date=rows[-1]["trade_date"])

        written = await kline_repo.upsert_klines(rows)
        result["written"] = written
        await kline_repo.set_sync_state(
            args.code, PERIOD_DAY, "ok",
            first_date=rows[0]["trade_date"], last_date=rows[-1]["trade_date"],
            message=f"tencent {len(rows)} 根")

        if not args.no_derive:
            # 与 pytdx 链路调用的是同一个函数，这是控制变量的关键
            result["derived"] = await kline_repo.materialize_periods(args.code)

        result["status"] = "ok"
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"
        await kline_repo.set_sync_state(
            args.code, PERIOD_DAY, "failed", message=str(e)[:250])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        await db.close_pool()
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    await db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
