import asyncio, os, sys
sys.path.insert(0, os.path.join(os.getcwd(), 'src'))
os.environ.setdefault('ZX_MYSQL_DB', 'tsdb')
from server import db
async def main():
    await db.init_pool()
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute('SHOW CREATE TABLE stock')
            print((await cur.fetchone())[1])
    await db.close_pool()
asyncio.run(main())
