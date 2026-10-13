"""复权自算可行性验证（不依赖 adj_factor——它是 1次/小时）

核心思路：daily.pre_close 是「已除权调整的昨收」。
若 pre_close 与 dividend 的 ex_date 能对齐，则说明：
  Tushare 直接给了除权后的前收盘 -> 用 daily 单表即可自算复权因子
交叉源：东财 datacenter-web 的除权除息表（已实测 6/6 可用）
"""
import json
import time
import urllib.request
from pathlib import Path

import tushare as ts

PROJ = Path(__file__).resolve().parents[2]
TOKEN = (PROJ / 'config.txt').read_text(encoding='utf-8').strip()
ts.set_token(TOKEN)
pro = ts.pro_api()

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')


def em_dividend(code):
    url = ('https://datacenter-web.eastmoney.com/api/data/v1/get'
           '?reportName=RPT_SHAREBONUS_DET&columns=ALL'
           f'&filter=(SECURITY_CODE%3D%22{code}%22)&pageSize=100'
           '&sortColumns=EX_DIVIDEND_DATE&sortTypes=-1')
    req = urllib.request.Request(url, headers={
        'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/'})
    raw = urllib.request.urlopen(req, timeout=20).read()
    d = json.loads(raw.decode('utf-8-sig', errors='replace'))
    return (d.get('result') or {}).get('data') or []


print('=' * 74)
print('验证 A: Tushare dividend.ex_date 与 daily.pre_close 跳变日是否对齐')
print('=' * 74)

div = pro.dividend(ts_code='600570.SH')
div = div[div['ex_date'].notna()].sort_values('ex_date')
print(f'Tushare dividend: {len(div)} 条, ex_date 范围 '
      f'{div["ex_date"].min()} ~ {div["ex_date"].max()}')
print(f'  字段: {list(div.columns)}')
print()
print('  最近 6 条除权记录:')
for _, r in div.tail(6).iterrows():
    print(f'    ex_date={r["ex_date"]}  派息(税前)={r["cash_div_tax"]}  '
          f'送股={r["stk_div"]}  转增={r["stk_bo_rate"]}  {r["div_proc"]}')

# 取一个跨除权日的区间
ex_dates = [d for d in div['ex_date'] if '20200101' <= d <= '20260930']
print(f'\n  2020 年后的 ex_date: {ex_dates}')

if ex_dates:
    lo, hi = '20260801', '20260930'
    d = pro.daily(ts_code='600570.SH', start_date=lo, end_date=hi).sort_values('trade_date')
    d['jump'] = (d['close'] / d['pre_close'] - 1).abs()
    hits = d[d['jump'] > 0.025]
    print(f'\n  daily({lo}~{hi}) 共 {len(d)} 根')
    print(f'  |close/pre_close-1| > 2.5% 的 {len(hits)} 根:')
    for _, r in hits.iterrows():
        mark = '  <== 与 ex_date 对齐' if r['trade_date'] in ex_dates else ''
        print(f'    {r["trade_date"]}  pre_close={r["pre_close"]:>7.2f}  '
              f'close={r["close"]:>7.2f}  跳变={r["jump"]*100:>6.2f}%{mark}')
else:
    print('  2020 年后无 ex_date，改看全区间')
    d = pro.daily(ts_code='600570.SH', start_date='20190101', end_date='20260930')
    d = d.sort_values('trade_date')
    d['jump'] = (d['close'] / d['pre_close'] - 1).abs()
    hits = d[d['jump'] > 0.025]
    print(f'  2019-2026 共 {len(d)} 根, |jump|>2.5% 共 {len(hits)} 根')
    print(f'  其中落在 ex_date 上的: '
          f'{sum(1 for _, r in hits.iterrows() if r["trade_date"] in set(div["ex_date"]))} 根')

print()
print('=' * 74)
print('验证 B: 交叉源核对 —— 东财 vs Tushare 除权除息')
print('=' * 74)
em = em_dividend('600570')
em_rows = []
for r in em:
    ex = (r.get('EX_DIVIDEND_DATE') or '')[:10].replace('-', '')
    if ex and '2019' <= ex[:4] <= '2026':
        em_rows.append((ex, r.get('PRETAX_BONUS_RMB'), r.get('BONUS_IT_RATIO'),
                        r.get('IT_RATIO')))
em_rows.sort(reverse=True)
print(f'  东财 2019-2026 除权除息 {len(em_rows)} 条:')
for x, cash, bonus, it in em_rows[:8]:
    print(f'    ex_date={x}  税前派息={cash}  送股={bonus}  转增={it}')

ts_rows = []
for _, r in div.iterrows():
    if r['ex_date'] and '2019' <= r['ex_date'][:4] <= '2026':
        ts_rows.append((r['ex_date'], r['cash_div_tax'], r['stk_div'], r['stk_bo_rate']))
ts_rows.sort(reverse=True)
print(f'\n  Tushare 同区间 {len(ts_rows)} 条:')
for x, cash, bonus, bo in ts_rows[:8]:
    print(f'    ex_date={x}  税前派息={cash}  送股={bonus}  转增={bo}')

ts_set = {r[0] for r in ts_rows}
em_set = {r[0] for r in em_rows}
print(f'\n  ex_date 交集: {sorted(ts_set & em_set)}')
print(f'  仅 Tushare 有: {sorted(ts_set - em_set)}')
print(f'  仅东财有:{sorted(em_set - ts_set)}')

print()
print('=' * 74)
print('验证 C: pro_bar 正确用法（复权最省事的路）')
print('=' * 74)
for desc, fn in (
    ('ts.pro_bar adj=hfq',
     lambda: ts.pro_bar(ts_code='600570.SH', adj='hfq',
                        start_date='20260901', end_date='20260930')),
    ('ts.pro_bar(adj=qfq) 带 freq',
     lambda: ts.pro_bar(ts_code='600570.SH', adj='qfq', freq='daily',
                        start_date='20260901', end_date='20260930')),
):
    try:
        df = fn()
        n = 0 if df is None else len(df)
        print(f'  [OK] {desc}: {n} 行')
        if n:
            print(f'        列: {list(df.columns)}')
    except Exception as e:
        print(f'  [ERR] {desc}: {str(e)[:100]}')
    time.sleep(1.2)