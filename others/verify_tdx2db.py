"""修正: stock_info 是带 sh/sz 前缀 8 位；daily_data/block_stock_relation 是纯 6 位"""
import pymysql

conn = pymysql.connect(
    host='127.0.0.1', port=3306, user='root', password='dba126543',
    database='tdx2db', charset='utf8mb4',
)
cur = conn.cursor()

# stock_info 4 标的（带 sh 前缀）
cur.execute(
    "SELECT code, HEX(name) "
    "FROM stock_info WHERE code IN ('sh600570','sh600519','sz000001','sz000002')"
)
print('stock_info 4 标的 name hex (utf8mb4):')
for code, hx in cur.fetchall():
    print(f'  {code}: {hx}')

# daily_data 是 6 位
cur.execute(
    "SELECT code, COUNT(*), MIN(date), MAX(date) "
    "FROM daily_data GROUP BY code"
)
print('\ndaily_data 三只股票:')
for r in cur.fetchall():
    print(f'  {r}')

# sh600570 在 daily 表里的行业 / 概念 / 指数 / 风格 hex
cur.execute(
    "SELECT block_type, HEX(block_name) "
    "FROM block_stock_relation WHERE code='600570' "
    "AND block_type IN ('行业','概念','风格','指数') LIMIT 8"
)
print('\nsh600570 板块 hex (前 8):')
for bt, c in cur.fetchall():
    print(f'  {bt}: {c}')

# sh600570 在各 block_type 的数量（HEX 显示类型不会被 cp936 撕）
cur.execute(
    "SELECT HEX(block_type), COUNT(*) "
    "FROM block_stock_relation WHERE code='600570' GROUP BY block_type"
)
print('\nsh600570 各 block_type 数量:')
for hx, n in cur.fetchall():
    print(f'  block_type hex={hx}  rows={n}')

# 日线数据 sanity
cur.execute(
    "SELECT MIN(close), MAX(close), AVG(volume) "
    "FROM daily_data WHERE code='600570'"
)
mn, mx, av = cur.fetchone()
print(f'\nsh600570 close 区间: {mn} ~ {mx}; 平均成交量: {av:.1f}')

cur.execute(
    "SELECT date, open, close, volume FROM daily_data "
    "WHERE code='600570' ORDER BY volume DESC LIMIT 3"
)
print('\nsh600570 单日成交量最大 3 天:')
for r in cur.fetchall():
    print(f'  {r}')

conn.close()