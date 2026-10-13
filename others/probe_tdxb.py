import sys, asyncio
sys.path.insert(0, 'src')
from server import db, config
print('config.MYSQL_HOST =', config.MYSQL_HOST)

async def m():
    await db.init_pool()
    async with db.pool().acquire() as c:
        async with c.cursor() as cur:
            await cur.execute(
                "CREATE DATABASE IF NOT EXISTS `tdx2db` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
            await cur.execute("SHOW DATABASES LIKE 'tdx2db'")
            print('库状态:', await cur.fetchall())
            await cur.execute("SELECT @@version")
            print('MySQL 版本:', (await cur.fetchone())[0])
    await db.close_pool()

asyncio.run(m())