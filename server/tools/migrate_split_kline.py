"""一次性迁移：老单表 `kline`（带 period 列）→ 5 张分表 `kline_<period>`。

背景：2026-10-07 起 K 线按周期分表，表名即周期，表内不再有 ``period`` 列，
主键由 ``(code, period, trade_date)`` 简化为 ``(code, trade_date)``。

**幂等**：可重复执行。三个库都已迁完并 DROP 了老表，重跑本脚本会打印
「跳过 老表 kline 不存在」并正常退出 0 —— 这是预期行为，不是错误。

用法::

    .\\.venv\\Scripts\\python.exe -W ignore tools\\migrate_split_kline.py --db zx_invest

迁移并核对通过后，老表 ``kline`` 会被保留（不自动 DROP），确认无误再手工执行::

    DROP TABLE kline;
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server import config, db  # noqa: E402

OLD_TABLE = "kline"

_COLS = "code, trade_date, open, high, low, close, volume, amount, pre_close, chg_pct"


async def table_exists(cur, name: str) -> bool:
    await cur.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema=DATABASE() AND table_name=%s", (name,))
    return (await cur.fetchone())[0] > 0


async def report_skip(cur) -> bool:
    """老表已不存在时的分支。返回是否一切正常。

    绝不要在这里 close_pool()：调用方此刻还持有该池的连接，
    close_pool() 里的 wait_closed() 会等所有连接归还，正好在等自己那条 -> 死锁。
    连接池统一由 main_async 结束时关闭。
    """
    print(f"[跳过] 老表 {OLD_TABLE} 不存在（已 DROP 或本库从未用过旧schema）")
    await cur.execute("SHOW TABLES")
    have = [r[0] for r in await cur.fetchall()]
    print(f"  库内现有表: {', '.join(have)}")
    missing = [db.kline_table(p) for p in db.KLINE_PERIODS
               if db.kline_table(p) not in have]
    if missing:
        print(f"  !! 缺少分表 {missing}，需先跑 tools/init_db.py --db <库名> 建表")
        return False
    for p in db.KLINE_PERIODS:
        t = db.kline_table(p)
        await cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"  {t:<14} {(await cur.fetchone())[0]:>6} 行")
    print("  分表齐备，迁移已完成")
    return True


async def migrate(cur) -> bool:
    """老表存在时执行迁移 + 核对。"""
    ok = True
    await cur.execute(f"SELECT COUNT(*) FROM {OLD_TABLE}")
    old_n = (await cur.fetchone())[0]
    await cur.execute(
        f"SELECT period, COUNT(*) FROM {OLD_TABLE} GROUP BY period ORDER BY period")
    dist = dict(await cur.fetchall())
    print(f"老表 {OLD_TABLE}: {old_n} 行  分布={dist}\n")

    # 建新表（幂等）
    applied = await db.init_schema()
    print(f"[建表] 本次补列 {len(applied)} 条")

    print("\n[迁移]")
    # 老表不一定有 pre_close / chg_pct —— tq-stock 是加列迁移之前建的库，
    # 老 kline 只有 9 列。直接 SELECT 这两列会报 Unknown column，
    # 必须先探测列是否存在，缺的在新表里补 NULL。
    await cur.execute("""SELECT column_name FROM information_schema.columns
                         WHERE table_schema=DATABASE() AND table_name=%s""",
                      (OLD_TABLE,))
    old_cols = {r[0] for r in await cur.fetchall()}
    missing = [c for c in _COLS.split(", ") if c not in old_cols]
    if missing:
        print(f"  注意：老表缺列 {missing}（加列迁移之前建的库），新表里补 NULL")
    select_expr = ", ".join(c if c in old_cols else "NULL" for c in _COLS.split(", "))

    for period, n in dist.items():
        if period not in db.KLINE_PERIODS:
            print(f"  !! 老表出现未知 period={period!r}（{n} 行），已跳过，请人工确认")
            ok = False
            continue
        dst = db.kline_table(period)
        await cur.execute(
            f"INSERT IGNORE INTO {dst} ({_COLS}) "
            f"SELECT {select_expr} "
            f"FROM {OLD_TABLE} WHERE period=%s", (period,))
        print(f"  {period:<8} -> {dst:<14} 搬入 {n:>6} 行 (affected={cur.rowcount})")

    print("\n[核对]")
    moved = 0
    for period in db.KLINE_PERIODS:
        t = db.kline_table(period)
        await cur.execute(f"SELECT COUNT(*) FROM {t}")
        n = (await cur.fetchone())[0]
        moved += n
        print(f"  {t:<14} {n:>6} 行 {'' if n else '（空表）'}")
    print(f"\n  老表 {old_n} 行 / 新表合计 {moved} 行 -> "
          f"{'一致' if old_n == moved else '不一致，请检查'}")

    print("\n[逐根比对 恒生电子]")
    await cur.execute(
        f"SELECT code, period, trade_date, close, volume FROM {OLD_TABLE} "
        f"WHERE code=%s ORDER BY period, trade_date", ("sh600570",))
    rows = await cur.fetchall()
    bad = 0
    for code, period, td, close, vol in rows:
        if period not in db.KLINE_PERIODS:
            continue
        await cur.execute(
            f"SELECT close, volume FROM {db.kline_table(period)} "
            f"WHERE code=%s AND trade_date=%s", (code, td))
        r = await cur.fetchone()
        if r is None or r[0] != close or r[1] != vol:
            bad += 1
            if bad <= 3:
                print(f"    不一致 {code} {period} {td}: 老={close}/{vol} 新={r}")
    print(f"  恒生电子共 {len(rows)} 行，不一致 {bad} 行 -> {'通过' if bad == 0 else '失败'}")

    ok = ok and bad == 0 and old_n == moved
    print(f"\n下一步：核对无误后手工执行 DROP TABLE {OLD_TABLE};"
          f"旧表当前 {old_n} 行仍在，可随时回退。")
    return ok


async def main_async(db_name: str) -> int:
    # config.MYSQL_DB 是**导入时**从 ZX_MYSQL_DB 绑定的，改环境变量来不及生效；
    # 必须像 init_db.py 那样直接改模块属性（dsn() 在调用时才读它）。
    config.MYSQL_DB = db_name

    await db.init_pool()
    try:
        async with db.pool().acquire() as conn:
            async with conn.cursor() as cur:
                if await table_exists(cur, OLD_TABLE):
                    ok = await migrate(cur)
                else:
                    ok = await report_skip(cur)
    finally:
        await db.close_pool()
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=config.MYSQL_DB, help="库名，默认取 ZX_MYSQL_DB")
    a = ap.parse_args()
    print(f"目标库: {a.db}\n")
    return asyncio.run(main_async(a.db))


if __name__ == "__main__":
    raise SystemExit(main())