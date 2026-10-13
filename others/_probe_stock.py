import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
os.environ.setdefault("ZX_MYSQL_DB", "tsdb")
from server import db

async def main():
    await db.init_pool()
    async with db.pool().acquire() as conn:
        async with conn.cursor(db.aiomysql.DictCursor) as cur:
            for q, label in [
                ("SELECT COUNT(*) n FROM stock_info", "stock_info 行数"),
                ("SELECT COUNT(*) n FROM stock_finance", "stock_finance 行数"),
                ("SELECT COUNT(*) n FROM stock_xdxr", "stock_xdxr 行数"),
                ("SELECT COUNT(DISTINCT code) n FROM kline_day", "kline_day 去重代码数"),
            ]:
                await cur.execute(q)
                print(f"{label:22s} = {(await cur.fetchone())['n']}")
            print()
            await cur.execute("SELECT * FROM stock_info LIMIT 5")
            rows = await cur.fetchall()
            print("stock 表内容:", rows if rows else "(空表)")
            await cur.execute("SELECT DISTINCT code FROM kline_day ORDER BY code")
            print("kline_day 里的代码:", [r["code"] for r in await cur.fetchall()])
    await db.close_pool()

asyncio.run(main())
