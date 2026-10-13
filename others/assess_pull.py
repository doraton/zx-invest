"""拉取前的规模与结构评估。"""
import pymysql


def conn(db):
    return pymysql.connect(host='127.0.0.1', port=3306, user='root',
                           password='dba126543', database=db, charset='utf8mb4')


print('=' * 72)
print('1. 上交所标的规模（从 tdx2db.stock_info 离线取，零 API 调用）')
print('=' * 72)
c = conn('tdx2db')
cur = c.cursor()
cur.execute("SELECT COUNT(*) FROM stock_info WHERE code LIKE 'sh%'")
print(f'  上交所合计: {cur.fetchone()[0]} 只')
cur.execute("SELECT SUBSTRING(code, 3, 3) pre, COUNT(*) n FROM stock_info "
            "WHERE code LIKE 'sh%' GROUP BY pre ORDER BY pre")
for r in cur.fetchall():
    print(f'    {r[0]}xxx  {r[1]:>5} 只')
cur.execute("SELECT COUNT(*) FROM stock_info WHERE code LIKE 'sz%'")
print(f'  （深交所对照: {cur.fetchone()[0]} 只）')
c.close()

print()
print('=' * 72)
print('2. 2026-09 交易日数量与预计数据量')
print('=' * 72)
c = conn('tsdb')
cur = c.cursor()
cur.execute("SELECT COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date) "
            "FROM kline_day WHERE code='sh600570' "
            "AND trade_date >= '2026-09-01'")
n, lo, hi = cur.fetchone()
print(f'  tsdb 已有恒生电子 9 月后交易日: {n} 天  ({lo} ~ {hi})')
sh = 2300  # 上交所约数
print(f'  预计上交所全量: {sh} 只 × ~{n} 天 = {sh*n:,} 行')

print()
print('=' * 72)
print('3. 分页策略（关键：daily 支持多代码一次调用）')
print('=' * 72)
for per_call in (50, 100, 200, 500, 8000):
    calls = -(-sh // per_call)
    print(f'  每次传 {per_call:>4} 只 -> {calls:>2} 次调用  '
          f'（配额 50次/分钟，{"安全" if calls <= 50 else "需分批"}）')

print()
print('=' * 72)
print('4. 现有表结构能否承载（重点看 pre_close）')
print('=' * 72)
cur.execute('SHOW COLUMNS FROM kline_day')
cols = [r[0] for r in cur.fetchall()]
print(f'  kline_day 现有列: {cols}')
need = {'pre_close': '复权/涨跌计算必需（Tushare daily 直接给）',
        'chg_pct': '涨跌幅（Tushare daily 直接给）'}
for k, why in need.items():
    print(f'  {k:<12} {"[有] 已有" if k in cols else "[缺] 缺失"}  <- {why}')

print()
print('  说明：')
print('    · Tushare daily 返回 pre_close / change / pct_chg 三个字段，')
print('      我们只存了 OHLCV，丢掉了 pre_close。')
print('    · pre_close 是「已除权的前收盘」，是自算复权因子和筹码的关键输入，')
print('      也是唯一能还原 Tushare 复权口径的字段。')
print('    · 缺失后只能重新拉全历史才能补，属于不可逆的数据损失。')

print()
print('  stock_info 表列:', end=' ')
cur.execute('SHOW COLUMNS FROM stock_info')
print([r[0] for r in cur.fetchall()])
c.close()