"""东财历史K线 host 全枚举 + 除权除息/实时/财务 端点定位。"""
import json
import re
import urllib.request

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
KLINE_PATH = ('/api/qt/stock/kline/get'
              '?secid=1.600570&fields1=f1,f2,f3,f4,f5,f6'
              '&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
              '&klt=101&fqt=1&end=20500101&lmt=3')


def raw_get(url, timeout=15):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/'})
    return urllib.request.urlopen(req, timeout=timeout).read()


def classify(raw):
    """返回 (kind, detail)。kind: json / html / other"""
    head = raw[:64]
    if b'<!DOCTYPE' in raw[:200] or b'<html' in raw[:200].lower():
        m = re.search(rb'www-zp-(\d+)', raw[:200])
        node = m.group(1).decode() if m else '?'
        return 'html', f'node={node} len={len(raw)}'
    t = raw.decode('utf-8-sig', errors='replace').strip()
    if t.startswith('{') or t.startswith('['):
        return 'json', f'len={len(raw)}'
    return 'other', f'head={t[:60]!r}'


print('=== 1. 历史K线 host 枚举（找能返 JSON 的节点）===')
hosts = [
    'push2his.eastmoney.com',
    'push2his2.eastmoney.com',
    '1.push2his.eastmoney.com',
    '2.push2his.eastmoney.com',
    '5.push2his.eastmoney.com',
    '7.push2his.eastmoney.com',
    'push2his.eastmoney.com:443',
    'quote.eastmoney.com',
]
json_hosts = []
for h in hosts:
    url = f'https://{h}{KLINE_PATH}'
    try:
        raw = raw_get(url)
        kind, detail = classify(raw)
        mark = 'JSON✓' if kind == 'json' else ('HTML' if kind == 'html' else '??')
        print(f'  [{mark:6}] {h:32} {detail}')
        if kind == 'json':
            json_hosts.append(h)
    except Exception as e:
        print(f'  [FAIL  ] {h:32} {type(e).__name__}: {str(e)[:60]}')

print(f'\n  → 能返 JSON 的 host: {json_hosts or "无"}')

print()
print('=== 2. push2his2 加 cb / ut 参数再试（有时 JSONP 能绕过）===')
for extra in ('&cb=jQuery112&ut=fa5fd1943c7b386f172d6893dbfba10b',
              '&ut=7eea3edcaed734bea9cbfc24409ed989&fltt=2',
              '&_=1'):
    url = f'https://push2his2.eastmoney.com{KLINE_PATH}{extra}'
    try:
        raw = raw_get(url)
        kind, detail = classify(raw)
        print(f'  {extra[:40]:42} [{kind}] {detail}')
    except Exception as e:
        print(f'  {extra[:40]:42} FAIL {type(e).__name__}')

print()
print('=== 3. datacenter-web 除权除息 (上一轮已 OK，再确认字段) ===')
url = ('https://datacenter-web.eastmoney.com/api/data/v1/get'
       '?reportName=RPT_SHAREBONUS_DET&columns=ALL'
       '&filter=(SECURITY_CODE%3D%22600570%22)&pageSize=50'
       '&sortColumns=EX_DIVIDEND_DATE&sortTypes=-1')
d = json.loads(raw_get(url, 20).decode('utf-8-sig', errors='replace'))
rows = (d.get('result') or {}).get('data') or []
print(f'  恒生电子除权除息 {len(rows)} 条:')
for r in rows[:8]:
    print(f"    除权日={r.get('EX_DIVIDEND_DATE')}  税前派息={r.get('PRETAX_BONUS_RMB')}  "
          f"送股={r.get('BONUS_IT_RATIO')} 转增={r.get('IT_RATIO')}  "
          f"方案={r.get('PLAN_NOTICE_DATE')}")

print()
print('=== 4. push2 实时快照 (确认可用) ===')
url = 'https://push2.eastmoney.com/api/qt/stock/get?secid=1.600570&fields=f43,f44,f45,f46,f47,f48,f57,f58,f60,f86,f169,f170'
raw = raw_get(url)
kind, detail = classify(raw)
print(f'  [{kind}] {detail}')
if kind == 'json':
    d = json.loads(raw.decode('utf-8-sig', errors='replace'))
    dd = d.get('data')
    print(f"    现价={dd.get('f43')} 昨收={dd.get('f60')} 涨={dd.get('f169')} 跌={dd.get('f170')} "
          f"名称={dd.get('f58')} 量={dd.get('f47')}")

print()
print('=== 5. datacenter-web 财务（利润表）探测 ===')
for rpt, label in (('RPT_DMSK_FN_INCOME', '利润表'), ('RPT_F10_FINANCE_GINCOME', 'F10利润表')):
    url = (f'https://datacenter-web.eastmoney.com/api/data/v1/get?reportName={rpt}'
           f'&columns=ALL&filter=(SECUCODE%3D%22600570.SH%22)&pageSize=3'
           f'&sortColumns=REPORT_DATE&sortTypes=-1')
    try:
        raw = raw_get(url, 20)
        kind, detail = classify(raw)
        print(f'  {label:12} [{kind}] {detail}')
        if kind == 'json':
            dd = json.loads(raw.decode('utf-8-sig', errors='replace'))
            n = len(((dd.get('result') or {}).get('data')) or [])
            print(f'      → {n} 行数据')
    except Exception as e:
        print(f'  {label:12} FAIL {type(e).__name__}')