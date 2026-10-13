"""用本地通达信名称缓存回填 ``stock_info.name``。

证券简称是本项目唯一没有网络来源的字段：pytdx 的 ``get_security_list``
在全部 8 个可用节点上都返回 None，只能读通达信客户端自己的
``T0002/hq_cache/{shs,szs}.tnf``。

用法::

    .\\.venv\\Scripts\\python.exe -W ignore tools\\backfill_names.py
    .\\.venv\\Scripts\\python.exe -W ignore tools\\backfill_names.py --overwrite
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server import config, db, stock_repo  # noqa: E402
from server.external import tdx_local  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description="回填 stock_info.name")
    ap.add_argument("--overwrite", action="store_true", help="覆盖已有名称（默认只填空值）")
    args = ap.parse_args()

    table = tdx_local.load_name_map()
    print(f"名称缓存目录: {config.TDX_HQ_CACHE}")
    print(f"  sh: {len(table.get('sh', {}))} 条, sz: {len(table.get('sz', {}))} 条")
    if not any(table.values()):
        print("  ! 没读到任何名称，确认通达信已启动过并下载过行情数据")
        await db.close_pool()
        return 1

    await db.init_pool()
    updated, missed = await stock_repo.backfill_names(overwrite=args.overwrite)
    print(f"已更新 {updated} 行，未命中 {missed} 行")
    await db.close_pool()
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
