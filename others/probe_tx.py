import urllib.request
import json

# 腾讯财经 K线
url = 'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,5,qfq'

print('=== 腾讯日 K ===')
try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'http://gu.qq.com/'})
    with urllib.request.urlopen(req, timeout=10) as r:
        body = r.read().decode()
        data = json.loads(body)
        if data.get('code') == 0:
            info = data['data']['sh000001']
            print('  name field:', info.get('name', ''))
            print('  qfq rows:', len(info.get('qfqday') or info.get('day') or []))
            rows = info.get('qfqday') or info.get('day') or []
            for r_ in rows[:3]:
                print('   ', r_)
        else:
            print('  code != 0:', data.get('code'))
except Exception as e:
    print('  ERR:', type(e).__name__, e)

# 腾讯周/月
for period in ['week', 'month']:
    url2 = url.replace('day', period)
    try:
        req2 = urllib.request.Request(url2, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'http://gu.qq.com/'})
        with urllib.request.urlopen(req2, timeout=10) as r:
            body2 = r.read().decode()
            data2 = json.loads(body2)
            info = data2.get('data', {}).get('sh000001', {})
            key = f'qfq{period}' if 'qfq' in url else period
            rows = info.get(key) or info.get(period) or []
            print(f'=== 腾讯 {period} ===', f'rows={len(rows)}', f'first={rows[0] if rows else None}')
    except Exception as e:
        print(f'=== 腾讯 {period} === ERR:', e)