"""一次性脚本：把指数数据层收敛成 2 张表（index_info 名录 + index_daily 统一数据），
并写入 6 条名录。

本脚本会**删表**。它只删 `index_daily` / `indicator_hysz` / `indicator_mom`
这三张 2026-10-08 当天刚建的空表，用来把「3 张分表」换成「1 张合并表」。

安全阀：任何一张待删表**有数据就直接中止**，不执行 DROP。除非显式传
``--force-drop``（那时会先打印将被丢弃的行数）。

用法::

    .\\.venv\\Scripts\\python.exe -W ignore -u tools\\init_index_tables.py --dry-run
    .\\.venv\\Scripts\\python.exe -W ignore -u tools\\init_index_tables.py
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("ZX_MYSQL_DB", "zx_invest")

from server import db, index_repo  # noqa: E402

#: 收敛后应该存在的表。
EXPECTED_TABLES = ("index_info", "index_daily")

#: 2026-10-08 拆分方案留下的表，本脚本负责删掉。
#: indicator_hysz / indicator_mom 是拆分时新建的空表，没有写入方。
OBSOLETE_TABLES = ("indicator_hysz", "indicator_mom")


async def table_exists(cur, name: str) -> bool:
    await cur.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema=DATABASE() AND table_name=%s", (name,))
    return (await cur.fetchone())[0] > 0


async def table_rows(cur, name: str) -> int:
    await cur.execute(f"SELECT COUNT(*) FROM {name}")
    return (await cur.fetchone())[0]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将执行的操作，不建表不写名录")
    ap.add_argument("--force-drop", action="store_true",
                    help="即使待删表里有数据也照删（会打印被丢弃的行数）")
    args = ap.parse_args()

    await db.init_pool()
    try:
        # ---- 1. 清理旧表 ----
        async with db.pool().acquire() as conn:
            async with conn.cursor() as cur:
                for t in OBSOLETE_TABLES:
                    if not await table_exists(cur, t):
                        print(f"  {t:18} 不存在，跳过")
                        continue
                    rows = await table_rows(cur, t)
                    print(f"  {t:18} 存在，{rows} 行 -> 将删除")
                    if rows and not args.force_drop:
                        print(f"    !! 有数据，拒绝删除。确认要丢弃就加 --force-drop")
                        return 1

        if args.dry_run:
            print("\n--dry-run：未执行任何变更。")
            return 0

        async with db.pool().acquire() as conn:
            async with conn.cursor() as cur:
                for t in OBSOLETE_TABLES:
                    await cur.execute(f"DROP TABLE IF EXISTS {t}")
                # index_daily 结构变过（DECIMAL 宽度 + open/high/low 改可空 +
                # 拆掉 pre_close/chg_pct、加 sample_size/confidence/source），
                # CREATE TABLE IF NOT EXISTS 不会改已存在的表，只能 DROP 重建。
                # 它此刻 0 行，重建无损失。
                if await table_exists(cur, "index_daily"):
                    rows = await table_rows(cur, "index_daily")
                    print(f"  index_daily        存在，{rows} 行 -> DROP 后按新结构重建")
                    if rows:
                        print("    !! index_daily 有数据，重建会丢。先确认。")
                        return 1
                    await cur.execute("DROP TABLE index_daily")
                else:
                    print("  index_daily        不存在，直接建")

        # ---- 2. 建表 ----
        # 必须 init_schema()：init_pool() 只建连接池，不建表。
        applied = await db.init_schema()
        print(f"\n本次执行的迁移语句: {len(applied)} 条")
        for s in applied:
            print(f"  {s}")

        async with db.pool().acquire() as conn:
            async with conn.cursor() as cur:
                ph = ",".join(["%s"] * len(EXPECTED_TABLES))
                await cur.execute(
                    f"SELECT table_name FROM information_schema.tables "
                    f"WHERE table_schema=DATABASE() AND table_name IN ({ph})",
                    EXPECTED_TABLES)
                found = {r[0] for r in await cur.fetchall()}
        missing = [t for t in EXPECTED_TABLES if t not in found]
        print(f"\n建表检查: {len(found)}/{len(EXPECTED_TABLES)}")
        for t in EXPECTED_TABLES:
            print(f"  [{'OK' if t in found else 'MISS'}] {t}")
        if missing:
            return 1

        # ---- 3. 旧表确认已消失 ----
        async with db.pool().acquire() as conn:
            async with conn.cursor() as cur:
                for t in OBSOLETE_TABLES:
                    left = await table_exists(cur, t)
                    print(f"  [{'OK' if not left else 'FAIL'}] {t} 已删除")
                    if left:
                        return 1

        # ---- 4. 写名录 ----
        n = await index_repo.seed_indexes()
        print(f"\n已写入 {n} 条指数名录（幂等，可重跑）")

        rows = await index_repo.list_indexes(enabled_only=False)
        print(f"\n回读 index_info: {len(rows)} 行")
        print(f"  {'code':10} {'单位':7} {'形态':7} 名称")
        for r in rows:
            print(f"  {r['code']:10} {r['value_unit']:7} "
                  f"{r['data_shape']:7} {r['name']}")

        units = {r["value_unit"] for r in rows}
        shapes = {r["data_shape"] for r in rows}
        if units != {"point", "yuan", "score"}:
            print(f"\n  <<< 量纲种类异常: {units}")
            return 1
        if shapes != {"ohlc", "single"}:
            print(f"\n  <<< 数据形态异常: {shapes}")
            return 1
        print(f"\n量纲 {sorted(units)} / 形态 {sorted(shapes)} 符合预期")

        # ---- 5. 列结构核对：确认 DECIMAL(20,4) 和 NULL 约束真的生效 ----
        async with db.pool().acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT column_name, column_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema=DATABASE() AND table_name='index_daily'")
                cols = {r[0]: (r[1], r[2]) for r in await cur.fetchall()}
        print("\nindex_daily 列结构核对：")
        expect = {
            "open_value": ("decimal(20,4)", "YES"),
            "high_value": ("decimal(20,4)", "YES"),
            "low_value": ("decimal(20,4)", "YES"),
            "close_value": ("decimal(20,4)", "NO"),
            "sample_size": ("int", "YES"),
            "confidence": ("decimal(4,3)", "YES"),
        }
        bad = False
        for cname, (want_type, want_null) in expect.items():
            got = cols.get(cname)
            good = got and got[0] == want_type and got[1] == want_null
            bad = bad or not good
            print(f"  [{'OK' if good else 'FAIL'}] {cname:12} "
                  f"{got[0] if got else '缺失':14} nullable={got[1] if got else '-'}"
                  + ("" if good else f"  期望 {want_type} nullable={want_null}"))
        if bad:
            return 1

        # ---- 6. 写一条假数据验证 upsert 走通（单值形态，open/high/low 留 NULL）----
        probe = {
            "code": "mom", "trade_date": "2000-01-01",
            "open_value": None, "high_value": None, "low_value": None,
            "close_value": "38.50", "sample_size": 74, "confidence": "0.350",
            "source": "probe",
        }
        await index_repo.upsert_daily(probe)
        got = await index_repo.list_daily("mom", limit=5)
        assert got and got[-1]["close_value"] == 38.5, f"upsert 回读异常: {got}"
        assert got[-1]["open_value"] is None, "单值指标的 open 应为 NULL"
        print("\nupsert 探针 OK：单值形态写 NULL 开高低收，回读一致")
        # 清掉探针，不留脏数据
        async with db.pool().acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM index_daily WHERE code='mom' AND trade_date='2000-01-01'")

        counts = await index_repo.count_daily()
        print(f"\nindex_daily 各 code 行数（待接数据源）: {counts}")
        return 0
    finally:
        await db.close_pool()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))