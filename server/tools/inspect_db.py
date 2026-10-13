"""数据库全貌巡检：所有库、所有表、字段、行数、日K 样本。"""
import pymysql

CONN = dict(host='127.0.0.1', port=3306, user='root',
            password='dba126543', charset='utf8mb4')


def line(ch='=', n=78):
    print(ch * n)


root = pymysql.connect(**CONN)
rc = root.cursor()

# ---------- 1. 所有数据库 ----------
line()
print('1. 本机 MySQL 全部数据库')
line()
rc.execute("SHOW DATABASES")
dbs = [r[0] for r in rc.fetchall()
       if r[0] not in ('information_schema', 'mysql', 'performance_schema', 'sys')]
for d in dbs:
    rc.execute(f"SELECT COUNT(*) FROM information_schema.tables "
               f"WHERE table_schema='{d}'")
    t = rc.fetchone()[0]
    mark = '  <-- 本轮工作库' if d == 'zx_invest' else ''
    print(f'  {d:<16} {t:>3} 张表{mark}')

# ---------- 2. 各库各表行数 ----------
line()
print('2. 各库各表行数')
line()
for d in dbs:
    print(f'\n  === {d} ===')
    rc.execute(f"""SELECT table_name, table_rows,
                          ROUND((data_length+index_length)/1024, 1) kb
                   FROM information_schema.tables
                   WHERE table_schema='{d}' ORDER BY table_name""")
    for name, est, kb in rc.fetchall():
        print(f'    {name:<22} ~{est:>9,} 行   {kb:>8} KB')
root.close()

# ---------- 3. 核心日K表完整字段 ----------
line()
print('3. 核心日K表 kline_day（zx_invest）完整字段')
line()
c = pymysql.connect(database='zx_invest', **CONN)
cur = c.cursor()
# kline_period 与 kline_day 结构基本相同（多一个 period 列），只是数据口径不同。
cur.execute("SHOW FULL COLUMNS FROM kline_day")
print(f'  {"字段":<12}{"类型":<18}{"NULL":<6}{"键":<6}{"默认值":<12}注释')
# SHOW FULL COLUMNS 列序: Field Type Collation Null Key Default Extra Privileges Comment
for r in cur.fetchall():
    f, typ, _coll, null, key, default, extra = r[0], r[1], r[2], r[3], r[4], r[5], r[8]
    print(f'  {str(f):<12}{str(typ):<18}{str(null):<6}{str(key or ""):<6}'
          f'{str(default if default is not None else ""):<12}{extra or ""}')

cur.execute("SHOW INDEX FROM kline_day")
print('\n  索引:')
seen = set()
for r in cur.fetchall():
    key = (r[2], r[4])
    if key in seen:
        continue
    seen.add(key)
    cols = cur.execute("SELECT column_name FROM information_schema.statistics "
                       "WHERE table_schema='zx_invest' AND table_name='kline_day' "
                       f"AND index_name='{r[2]}' ORDER BY seq_in_index")
    names = [x[0] for x in cur.fetchall()]
    print(f'    {r[2]:<24} {"唯一" if r[1] == 0 else "普通":<5} {names}')

# ---------- 4. 各周期覆盖分布 ----------
line()
print('4. 各周期覆盖分布（zx_invest）')
line()
# 日线一张表 kline_day；派生周期合并为一张 kline_period，用 period 列区分。
cur.execute("SELECT COUNT(*), COUNT(DISTINCT code), MIN(trade_date), MAX(trade_date) "
            "FROM kline_day")
n, cds, lo, hi = cur.fetchone()
print(f'  {"kline_day":<14} {n:>8,} 行   {cds:>5} 只   {lo} ~ {hi}')
cur.execute("SELECT period, COUNT(*), COUNT(DISTINCT code), "
            "MIN(trade_date), MAX(trade_date) FROM kline_period GROUP BY period "
            "ORDER BY FIELD(period,'week','month','quarter','year')")
for _p, _n, _c, _lo, _hi in cur.fetchall():
    print(f'  kline_period:{_p:<4} {_n:>8,} 行   {_c:>5} 只   {_lo} ~ {_hi}')

# ---------- 5. 样本数据 ----------
line()
print('5. 日K 样本（恒生电子最新 5 根，含 pre_close / chg_pct）')
line()
cur.execute("""SELECT trade_date, open, high, low, close, pre_close,
                      chg_pct, volume, amount
               FROM kline_day WHERE code='sh600570'
               ORDER BY trade_date DESC LIMIT 5""")
print(f'  {"日期":<12}{"开":>8}{"高":>8}{"低":>8}{"收":>8}'
      f'{"昨收":>8}{"涨跌%":>8}{"成交量(股)":>15}{"成交额(元)":>18}')
for r in cur.fetchall():
    print(f'  {str(r[0]):<12}{r[1]:>8.2f}{r[2]:>8.2f}{r[3]:>8.2f}{r[4]:>8.2f}'
          f'{(r[5] if r[5] is not None else 0):>8.2f}'
          f'{(r[6] if r[6] is not None else 0):>8.2f}'
          f'{r[7]:>15,.0f}{r[8]:>18,.0f}')

# ---------- 6. 其它表用途 ----------
line()
print('6. 其它表结构速览')
line()
for t in ('stock_info', 'stock_xdxr', 'sync_state', 'stock_finance'):
    cur.execute(f"SHOW COLUMNS FROM {t}")
    cols = [r[0] for r in cur.fetchall()]
    cur.execute(f"SELECT COUNT(*) FROM {t}")
    n = cur.fetchone()[0]
    print(f'\n  {t}  ({n:,} 行)')
    print(f'    {cols}')

c.close()