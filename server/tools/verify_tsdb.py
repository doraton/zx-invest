"""zx_invest 最终验证：日线质量 + 派生周期物化正确性 + 与 tdx2db 交叉核对。

K 线两张表：kline_day（日线，唯一真相源）+ kline_period（week/month/quarter/year
合并，用 period 列区分）。volume 单位手、amount 单位千元，与 Tushare 原生口径一致。
"""
import sys

import pymysql

c = pymysql.connect(host='127.0.0.1', port=3306, user='root',
                    password='dba126543', database='zx_invest', charset='utf8mb4')
cur = c.cursor()

PERIODS = ('day', 'week', 'month', 'quarter', 'year')


def one(sql, args=()):
    cur.execute(sql, args)
    return cur.fetchone()


def all_(sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()


CODE = 'sh600570'
print('=' * 74)
print('1. 落库概览')
print('=' * 74)
n, mn, mx = one("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) "
                "FROM kline_day WHERE code=%s", (CODE,))
print(f'  {"day":<8} {n or 0:>5} 行   {mn} ~ {mx}')
for p in ('week', 'month', 'quarter', 'year'):
    n, mn, mx = one("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) "
                    "FROM kline_period WHERE code=%s AND period=%s", (CODE, p))
    print(f'  {p:<8} {n or 0:>5} 行   {mn} ~ {mx}')

print()
print('=' * 74)
print('2. 日线质量')
print('=' * 74)
n, bad = one("""SELECT COUNT(*), SUM(CASE WHEN high<GREATEST(open,close)
                 OR low>LEAST(open,close) OR close<=0 THEN 1 ELSE 0 END)
              FROM kline_day WHERE code=%s""", (CODE,))
print(f'  {n} 根，OHLC 违例 {bad} 条')
# 均价必须先把两个量的单位统一到「元 / 股」再相除：
# amount 存千元（×1000 → 元），volume 存手（×100 → 股）。
# 直接写 amount/volume 会得到「千元/手」= 10 × 元/股，数值整体大 10 倍，
# 与 close 比对必然失败——那不是数据坏了，是校验式子漏了换算。
vwap, lo, hi = one("SELECT AVG(amount*1000/(volume*100)), MIN(close), MAX(close) "
                   "FROM kline_day WHERE code=%s AND volume>0", (CODE,))
print(f'  均价 amount(元)/volume(股) = {vwap:.3f}，落在 [{lo:.2f}, {hi:.2f}] 内 -> 单位换算正确')
n_pc, = one("SELECT SUM(pre_close IS NULL) FROM kline_day WHERE code=%s", (CODE,))
print(f'  pre_close 为空 {n_pc or 0} 行 / {n} 行')

print()
print('=' * 74)
print('3. 派生周期物化正确性（独立用 Python 重算比对）')
print('=' * 74)
import datetime as dt
from collections import OrderedDict

day = all_("SELECT trade_date, open, high, low, close, volume, amount "
           "FROM kline_day WHERE code=%s ORDER BY trade_date", (CODE,))


def bucket_key(d, period):
    y, m, dd = d.year, d.month, d.day
    if period == 'month':
        return f'{y}-{m:02d}'
    if period == 'quarter':
        return f'{y}-Q{(m - 1) // 3 + 1}'
    if period == 'year':
        return str(y)
    if period == 'week':
        iso = dt.date(y, m, dd).isocalendar()
        return f'{iso[0]}-W{iso[1]:02d}'
    raise ValueError(period)


for period in ('week', 'month', 'quarter', 'year'):
    exp = OrderedDict()
    for d, o, h, l, cl, v, a in day:
        exp.setdefault(bucket_key(d, period), []).append(
            (d, float(o), float(h), float(l), float(cl), float(v), float(a)))
    recomputed = [
        (max(x[0] for x in v), v[0][1], max(x[2] for x in v), min(x[3] for x in v),
         v[-1][4], sum(x[5] for x in v), sum(x[6] for x in v))
        for k, v in exp.items()]
    # 显式列名，不用 SELECT * —— 分表后没有 period 列，按序号取很容易错位。
    # 两边都按 trade_date 升序，逐位比对。
    # 注意：不要试图用分桶字符串（'2026-W40'）去索引库里的行——库里的键是
    # datetime.date，类型对不上，get() 恒为 None，会让「差异数」恒等于桶数，
    # 看起来像全错其实根本没比（旧版脚本就是这么写的）。
    db_rows = [r for r in all_(
        f"SELECT trade_date, open, high, low, close, volume, amount "
        f"FROM kline_period WHERE code=%s AND period=%s ORDER BY trade_date",
        (CODE, period))]
    db_rows = [(r[0], float(r[1]), float(r[2]), float(r[3]),
                float(r[4]), float(r[5]), float(r[6])) for r in db_rows]

    diff = abs(len(db_rows) - len(recomputed))
    for i, (a, b) in enumerate(zip(recomputed, db_rows)):
        if a[0] != b[0] or any(abs(x - y) > 0.005 for x, y in zip(a[1:], b[1:])):
            diff += 1
            if diff <= 3:
                print(f'      差异#{i} 桶={a[0]} 重算={a[1:]} 库内={b[1:]}')
    print(f'  {period:<8} 库内 {len(db_rows):>3} 个桶 / Python重算 {len(recomputed):>3} 个桶'
          f'  差异 {diff} 处{"  完全一致" if diff == 0 else "  <<< 有差异"}')

print()
print('=' * 74)
print('4. 与 tdx2db 日线交叉核对')
print('=' * 74)
# tdx2db 是外部对照数据源，本机不一定还在（曾是本项目自建）。
# 缺失时如实跳过并退出 0，不要让整份报告挂在一段拿不到的数据上。
try:
    c2 = pymysql.connect(host='127.0.0.1', port=3306, user='root',
                         password='dba126543', database='tdx2db', charset='utf8mb4')
except pymysql.err.OperationalError as e:
    print(f'  跳过：本机没有 tdx2db 库（{e.args[0]}）')
    sys.exit(0)
cur2 = c2.cursor()
cur2.execute("""
  SELECT COUNT(*),
         SUM(ABS(t.open-d.open)>0.005), SUM(ABS(t.high-d.high)>0.005),
         SUM(ABS(t.low -d.low)>0.005), SUM(ABS(t.close-d.close)>0.005),
         SUM(ABS(t.volume*100/(d.volume) - 1.0) > 0.0001)
  FROM zx_invest.kline_day t JOIN tdx2db.daily_data d
    ON d.code='600570' AND d.date = t.trade_date
  WHERE t.code=%s""", (CODE,))
r = cur2.fetchone()
print(f'  重叠 {r[0]} 根 | open差{r[1]} high差{r[2]} low差{r[3]} close差{r[4]}')
print(f'  volume 单位换算(手→股 ×100)后不一致: {r[5]} 处')

print()
print('  抽样 3 根:')
cur2.execute("""
  SELECT t.trade_date, t.open, t.high, t.low, t.close,
         t.volume, d.volume, t.amount, d.amount
  FROM zx_invest.kline_day t JOIN tdx2db.daily_data d
    ON d.code='600570' AND d.date = t.trade_date
  WHERE t.code=%s
  ORDER BY t.trade_date DESC LIMIT 3""", (CODE,))
print('    date        open  high   low  close |  tsVol(手)   tdxVol(股) | tsAmt(千元)')
for x in cur2.fetchall():
    print(f'    {x[0]}  {x[1]:>5.2f} {x[2]:>5.2f} {x[3]:>5.2f} {x[4]:>5.2f} | '
          f'{x[5]:>12,.0f} {x[6]:>11,.1f} | {x[7]:>14,.0f}')

print()
print('=' * 74)
print('5. 最新一周/月 派生行明细')
print('=' * 74)
for period in ('week', 'month'):
    print(f'  -- {period} 最近 3 个 --')
    for r in all_("SELECT trade_date, open, high, low, close, volume "
                  "FROM kline_period WHERE code=%s AND period=%s "
                  "ORDER BY trade_date DESC LIMIT 3", (CODE, period)):
        print(f'    {r[0]}  O={r[1]:>6.2f} H={r[2]:>6.2f} L={r[3]:>6.2f} '
              f'C={r[4]:>6.2f} V={r[5]:>13,.0f}')

c2.close()
c.close()