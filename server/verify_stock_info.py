import pymysql
conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password='dba126543', database='tdx2db', charset='utf8mb4')
cur = conn.cursor()
cur.execute('SELECT code, HEX(code), LENGTH(code) FROM stock_info LIMIT 5')
print('前 5 行 code 实际形态:')
for r in cur.fetchall():
    print(f'  {r}')
cur.execute("SELECT code FROM stock_info WHERE code LIKE '60057%' LIMIT 5")
print('\nLIKE 60057%:')
for r in cur.fetchall():
    print(f'  {r}')
conn.close()