"""读 MySQL `tdx2db` 库，抽样给用户看实际入库数据。"""
import pymysql

conn = pymysql.connect(
    host='127.0.0.1', port=3306, user='root', password='dba126543',
    database='tdx2db', charset='utf8mb4',
)
cur = conn.cursor()

print('=== stock_info 4 只样本 ===')
cur.execute(
    "SELECT code, name, zgb, ltag, list_date "
    "FROM stock_info WHERE code IN ('600570','600519','000001','000002')"
)
for r in cur.fetchall():
    print(r)

print()
print('=== sh600570 板块（行业/概念/风格/指数 示例各 3 条）===')
cur.execute(
    "SELECT block_type, block_code, block_name "
    "FROM block_stock_relation "
    "WHERE code='600570' AND block_type IN ('行业','概念','风格','指数') "
    "ORDER BY block_type, block_code LIMIT 12"
)
for r in cur.fetchall():
    print(r)

print()
print('=== daily_data sh600570 最新 3 / 最早 3 ===')
cur.execute(
    "SELECT date, open, high, low, close, volume "
    "FROM daily_data WHERE code='600570' ORDER BY date DESC LIMIT 3"
)
for r in cur.fetchall():
    print('最新', r)
cur.execute(
    "SELECT date, open, high, low, close, volume "
    "FROM daily_data WHERE code='600570' ORDER BY date ASC LIMIT 3"
)
for r in cur.fetchall():
    print('最早', r)

print()
print('=== 各表行数 ===')
for t in ('stock_info', 'daily_data', 'minute5_data', 'minute15_data',
          'minute30_data', 'minute60_data', 'block_stock_relation'):
    cur.execute(f'SELECT COUNT(*) FROM {t}')
    n = cur.fetchone()[0]
    print(f'  {t:<22} {n:>10,}')

conn.close()