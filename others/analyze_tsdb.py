"""tsdb 库深度分析（仅聚焦 tsdb）。用精确 COUNT(*) 而非 information_schema 估算。"""
import pymysql

c = pymysql.connect(host='127.0.0.1', port=3306, user='root',
                    password='dba126543', database='tsdb', charset='utf8mb4')
cur = c.cursor()

# K 线已按周期分表，表名即周期；kline_backup / ref_industry / ref_province 已废弃。
PERIODS = ['day', 'week', 'month', 'quarter', 'year']
TABLES = [f'kline_{p}' for p in PERIODS] + \
         ['stock_info', 'stock_xdxr', 'stock_finance', 'sync_state']


def line(ch='=', n=78):
    print(ch * n)


def one(sql, args=()):
    cur.execute(sql, args)
    return cur.fetchone()


def all_(sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()


line()
print('1. 精确行数（COUNT(*) 而非估算）')
line()
total = 0
for t in TABLES:
    n = one(f'SELECT COUNT(*) FROM `{t}`')[0]
    total += n
    print(f'  {t:<20} {n:>9,} 行' + ('' if n else '   (空)'))
print(f'  {"合计":<20} {total:>9,} 行')

line()
print('2. kline 全景（按周期分表，表名即周期）')
line()
# 全表口径跨 5 张表，用 UNION ALL 合成一个派生表再聚合。
ALL_KLINE = ' UNION ALL '.join(
    f'SELECT code, trade_date, amount FROM kline_{p}' for p in PERIODS)
cnt, codes, d1, d2, amt = one(
    f"SELECT COUNT(*), COUNT(DISTINCT code), COUNT(DISTINCT trade_date), "
    f"MIN(trade_date), MAX(trade_date), SUM(amount) FROM ({ALL_KLINE}) k")
print(f'  总行数 {cnt:,}  股票 {codes} 只  交易日 {d1} ~ {d2} ({amt:,.0f} 元)')

print('\n  按周期:')
for per in PERIODS:
    cnt, codes, d1, d2 = one(f"SELECT COUNT(*), COUNT(DISTINCT code), "
                             f"MIN(trade_date), MAX(trade_date) FROM kline_{per}")
    print(f'    {per:<9} {cnt:>7,} 行  {codes:>4} 只   {d1} ~ {d2}')

print('\n  按市场:')
for r in all_("SELECT LEFT(code,2) mkt, COUNT(*), COUNT(DISTINCT code) "
              f"FROM ({ALL_KLINE}) k GROUP BY mkt ORDER BY mkt"):
    print(f'    {r[0]}    {r[1]:>7,} 行  {r[2]:>4} 只')

line()
print('3. 日线覆盖明细（哪些股票有多少数据）')
line()
rows = all_("""SELECT code, COUNT(*) n, MIN(trade_date) lo, MAX(trade_date) hi,
                      SUM(pre_close IS NULL) null_pc, SUM(volume<=0) zero_vol
               FROM kline_day
               GROUP BY code ORDER BY n DESC, code""")
print(f'  共 {len(rows)} 只股票')
buckets = {}
for code, n, lo, hi, nul, zv in rows:
    k = '21天(完整9月)' if n >= 21 else ('11-20天' if n >= 11 else '≤10天')
    buckets.setdefault(k, []).append(code)
for k in ('21天(完整9月)', '11-20天', '≤10天'):
    if k in buckets:
        lst = buckets[k]
        print(f'\n  {k}: {len(lst)} 只')
        print(f'    {", ".join(lst[:14])}{" ..." if len(lst) > 14 else ""}')
        if k != '21天(完整9月)':
            r0 = [x for x in rows if x[0] in lst][:3]
            for code, n, lo, hi, nul, zv in r0:
                print(f'      {code}: {n} 行 {lo}~{hi}  pre_close空={nul} 零成交={zv}')

line()
print('4. pre_close / chg_pct 完整性')
line()
d, n_pc, n_pct = one("""SELECT COUNT(*), SUM(pre_close IS NULL), SUM(chg_pct IS NULL)
                       FROM kline_day""")
print(f'  日线 {d:,} 行：pre_close 空 {n_pc:,} ({n_pc/d*100:.1f}%)  '
      f'chg_pct 空 {n_pct:,} ({n_pct/d*100:.1f}%)')

line()
print('5. 日线数据质量')
line()
n, bad = one("""SELECT COUNT(*), SUM(high<GREATEST(open,close) OR low>LEAST(open,close)
                 OR open<=0 OR high<=0 OR low<=0 OR close<=0)
              FROM kline_day""")
print(f'  OHLC 违例          {bad} 条 / {n:,} 行')

gap = one("""SELECT COUNT(*) FROM (
  SELECT DATEDIFF(nd, d) - 1 AS g FROM (
    SELECT code, trade_date d,
           LEAD(trade_date) OVER (PARTITION BY code ORDER BY trade_date) nd
    FROM kline_day) x
  WHERE nd IS NOT NULL) y WHERE g > 5""")[0]
print(f'  异常断档(>5自然日)  {gap} 处')

vw = one("""SELECT AVG(amount/volume), MIN(close), MAX(close), COUNT(*)
           FROM kline_day AND volume>0 AND amount>0""")
out_of_range = one("""SELECT COUNT(*) FROM kline_day
                      WHERE volume>0 AND amount>0
                        AND (amount/volume < low*0.9 OR amount/volume > high*1.1)""")[0]
print(f'  均价 amount/volume  {vw[0]:.3f}（全表价格区间 {vw[1]:.2f}~{vw[2]:.2f}）')
print(f'  均价落在当日高低区间外的行  {out_of_range} 条')

# 主键已是 (code, trade_date)，逐张周期表查重再累加。
dup = 0
for per in PERIODS:
    dup += one(f"""SELECT COUNT(*) FROM (SELECT code, trade_date, COUNT(*) n
                   FROM kline_{per} GROUP BY code, trade_date HAVING n>1) z""")[0]
print(f'  主键重复          {dup} 组')

line()
print('6. sync_state 内容')
line()
for r in all_('SELECT * FROM sync_state'):
    print(f'  {r}')

line()
print('7. 空表对应的上游能力缺口')
line()
gaps = [
    ('stock_info', '股票名称/流通股本', 'Tushare stock_basic = 1次/小时（已用尽）；可从 tdx2db.stock_info 离线搬'),
    ('stock_xdxr', '除权除息', 'Tushare dividend = 1次/分钟；东财 datacenter-web 6/6 可用'),
    ('stock_finance', '财务三表', 'Tushare income/balancesheet/cashflow 权限不足，当前无解'),
]
print(f'  {"表":<18}{"行数":>6}  缺什么 / 怎么补')
for t, what, how in gaps:
    n = one(f'SELECT COUNT(*) FROM `{t}`')[0]
    print(f'  {t:<18}{n:>6}  {what}')
    print(f'  {"":<18}{"":>6}    -> {how}')

c.close()