"""东财 push2his2 实测：klt / fqt 全组合 + 除权除息表解析。"""
import json
import urllib.request

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
BASE = 'https://push2his2.eastmoney.com/api/qt/stock/kline/get'


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8-sig', errors='replace')  # 注意 BOM


def kline(secid, klt, fqt, lmt=10, end='20500101'):
    url = (f'{BASE}?secid={secid}'
           '&fields1=f1,f2,f3,f4,f5,f6'
           '&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
           f'&klt={klt}&fqt={fqt}&end={end}&lmt={lmt}')
    d = json.loads(get(url))
    data = d.get('data')
    if not data:
        return None, None
    return data.get('name'), data.get('klines') or []


print('=== A. 恒生电子 sh600570 日线 fqt 三档对比（klt=101）===')
for fqt, label in ((0, '不复权'), (1, '前复权'), (2, '后复权')):
    name, ks = kline('1.600570', 101, fqt, lmt=6)
    print(f'\n  fqt={fqt} ({label})  name={name}  rows={len(ks or [])}')
    for k in (ks or [])[-3:]:
        print(f'    {k}')

print()
print('=== B. klt 周期档位验证 ===')
for klt, label in ((101, '日'), (102, '周'), (103, '月')):
    name, ks = kline('1.600570', klt, 1, lmt=4)
    print(f'  klt={klt} ({label}) rows={len(ks or [])}  last={ks[-1] if ks else None}')

print()
print('=== C. 全历史深度 (fqt=1, lmt=10000) ===')
name, ks = kline('1.600570', 101, 1, lmt=10000)
print(f'  {name}: {len(ks or [])} 根')
if ks:
    print(f'  最早: {ks[0]}')
    print(f'  最新: {ks[-1]}')

print()
print('=== D. 深市/科创/创业板/北交所 secid 前缀验证 ===')
for secid, label in (('1.600519', '沪主板'), ('0.000001', '深主板'),
                     ('1.688981', '科创板'), ('0.300750', '创业板'),
                     ('0.899050', '北交所')):
    name, ks = kline(secid, 101, 1, lmt=2)
    print(f'  {secid} ({label}): name={name} rows={len(ks or [])}')

print()
print('=== E. 除权除息表 (datacenter-web RPT_SHAREBONUS_DET) ===')
url = ('https://datacenter-web.eastmoney.com/api/data/v1/get'
       '?reportName=RPT_SHAREBONUS_DET&columns=ALL'
       '&filter=(SECURITY_CODE%3D%22600570%22)&pageSize=100'
       '&sortColumns=EX_DIVIDEND_DATE&sortTypes=-1')
d = json.loads(get(url, 20))
rows = (d.get('result') or {}).get('data') or []
print(f'  恒生电子除权除息记录数: {len(rows)}')
for r in rows[:6]:
    print(f"    {r.get('EX_DIVIDEND_DATE')}  "
          f"税前派息={r.get('PRETAX_BONUS_RMB')}  "
          f"送股={r.get('BONUS_IT_RATIO')} 转增={r.get('IT_RATIO')} "
          f"股权登记={r.get('EQUITY_RECORD_DATE')}")

print()
print('=== F. 除权除息表可用字段清单（前 25 个）===')
if rows:
    keys = list(rows[0].keys())
    print('  ', ', '.join(keys[:25]))
    print(f'   ... 共 {len(keys)} 个字段')