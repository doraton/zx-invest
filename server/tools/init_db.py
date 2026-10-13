"""建库 + 建表（幂等）。

用途：对照实验需要在一台 MySQL 上并存多套**完全相同 schema** 的库
（例如 ``mysqldb`` 存 pytdx 源、``tq-stock`` 存腾讯源），
两套跑的是同一份代码，只靠 ``ZX_MYSQL_DB`` 切换，从而把「数据源」变成唯一变量。

用法::

    .\\.venv\\Scripts\\python.exe -W ignore tools\\init_db.py
    .\\.venv\\Scripts\\python.exe -W ignore tools\\init_db.py --db tq-stock
    .\\.venv\\Scripts\\python.exe -W ignore tools\\init_db.py --db tq-stock --drop
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import aiomysql  # noqa: E402

from server import config, db  # noqa: E402


async def create_database(name: str, drop: bool) -> None:
    """先不指定库连上去，把库本身建出来（库名含连字符，必须反引号包裹）。"""
    conn = await aiomysql.connect(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT,
        user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
        autocommit=True, charset="utf8mb4",
    )
    try:
        async with conn.cursor() as cur:
            if drop:
                await cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
                print(f"已删除库 {name}")
            await cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{name}` "
                f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
            print(f"库 {name} 就绪")
    finally:
        conn.close()


async def main() -> int:
    ap = argparse.ArgumentParser(description="建库 + 建表")
    ap.add_argument("--db", default=config.MYSQL_DB, help="库名，默认取 ZX_MYSQL_DB")
    ap.add_argument("--drop", action="store_true", help="先 DROP 再建（会清空数据）")
    args = ap.parse_args()

    await create_database(args.db, args.drop)
    # 让后续 init_pool 用新库名：config.dsn() 在调用时才读 MYSQL_DB
    config.MYSQL_DB = args.db

    await db.init_pool()
    applied = await db.init_schema()
    print(f"schema 就绪，迁移 {len(applied)} 条")
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SHOW TABLES")
            tables = [r[0] for r in await cur.fetchall()]
    print("表:", ", ".join(tables))
    await db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
