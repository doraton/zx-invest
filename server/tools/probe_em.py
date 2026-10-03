import urllib.request
import json

# 直接从 Python 测试东方财富
url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&klt=101&fqt=1&end=20500101&lmt=5'

req = urllib.request.Request(url, headers={
    'Referer': 'https://quote.eastmoney.com/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
})

print('test 1: with UA + Referer')
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        body = r.read().decode()
        data = json.loads(body)
        print('  status:', r.status)
        klines = data.get('data', {}).get('klines') or []
        print('  rows:', len(klines))
        if klines:
            print('  first:', klines[0])
except Exception as e:
    print('  ERR:', type(e).__name__, e)

print('test 2: with http (not https)')
try:
    url2 = url.replace('https://', 'http://')
    req2 = urllib.request.Request(url2, headers={'Referer': 'https://quote.eastmoney.com/', 'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req2, timeout=10) as r:
        body = r.read().decode()
        data = json.loads(body)
        print('  status:', r.status)
        klines = data.get('data', {}).get('klines') or []
        print('  rows:', len(klines))
except Exception as e:
    print('  ERR:', type(e).__name__, e)

print('test 3: 多次重复 (看看限流)')
for i in range(3):
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode()
            data = json.loads(body)
            klines = data.get('data', {}).get('klines') or []
            print(f'  try {i}: rows={len(klines)}')
    except Exception as e:
        print(f'  try {i}: ERR {type(e).__name__}: {e}')