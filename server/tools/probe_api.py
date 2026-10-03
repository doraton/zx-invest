import urllib.request
import json

codes = ['sh000001', 'sz399001', 'sz399006', 'sh000688']
def call(p):
    r = urllib.request.urlopen(f'http://127.0.0.1:8310{p}', timeout=10)
    return json.loads(r.read().decode())

print('=== /api/index/list ===')
j = call('/api/index/list')
print(json.dumps(j, ensure_ascii=False, indent=2))

for code in codes:
    print(f'\n=== /api/index/quote?code={code} ===')
    j = call(f'/api/index/quote?source=akshare&code={code}')
    print(json.dumps(j, ensure_ascii=False))
    print(f'=== /api/index/kline?code={code}&count=3 ===')
    j = call(f'/api/index/kline?source=akshare&code={code}&category=4&count=3')
    if j['data']:
        print(f'  rows={len(j["data"])}, first={j["data"][0]}, last={j["data"][-1]}')
    else:
        print('  empty')