"""实测东财 HTTP 接口可用性 + 返回字段。多个端点交叉试，定位是哪一层被断。"""
import json
import urllib.parse
import urllib.request

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')


def probe(name, url, headers=None, timeout=12):
    hdrs = {'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/'}
    if headers:
        hdrs.update(headers)
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode('utf-8', errors='replace')
        print(f'[OK  ] {name}  status={r.status} len={len(body)}')
        return body
    except Exception as e:
        print(f'[FAIL] {name}  {type(e).__name__}: {e}')
        return None


print('=== 1. push2his kline (旧探针用的端点) ===')
u1 = ('https://push2his.eastmoney.com/api/qt/stock/kline/get'
      '?secid=1.600570&fields1=f1,f2,f3,f4,f5,f6'
      '&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
      '&klt=101&fqt=1&end=20500101&lmt=5')
b1 = probe('push2his klt=101 fqt=1', u1)
if b1:
    try:
        d = json.loads(b1)
        ks = d.get('data', {}).get('klines') or []
        print(f'      klines={len(ks)}')
        if ks:
            print('      last:', ks[-1])
    except Exception as e:
        print('      JSON err:', e)

print()
print('=== 2. push2his 换 push2his2 (备用域名) ===')
b2 = probe('push2his2', u1.replace('push2his.', 'push2his2.'))
if b2:
    try:
        d = json.loads(b2)
        ks = d.get('data', {}).get('klines') or []
        print(f'      klines={len(ks)}')
    except Exception as e:
        print('      JSON err:', e)

print()
print('=== 3. push2 实时快照 (单股) ===')
u3 = 'https://push2.eastmoney.com/api/qt/stock/get?secid=1.600570&fields=f43,f57,f58,f169,f170,f46,f44,f45,f47,f48,f60,f86'
b3 = probe('push2 stock/get', u3)
if b3:
    try:
        d = json.loads(b3)
        print('      data:', json.dumps(d.get('data'), ensure_ascii=False)[:300])
    except Exception as e:
        print('      JSON err:', e)

print()
print('=== 4. suggest 搜索接口 (data_source.py 已用它) ===')
u4 = 'https://searchadapter.eastmoney.com/api/suggest/get?input=600570&type=14&count=5'
b4 = probe('searchadapter suggest', u4)
if b4:
    print('      ', b4[:250])

print()
print('=== 5. datacenter-web (东财数据中心, 财务/除权除息) ===')
u5 = ('https://datacenter-web.eastmoney.com/api/data/v1/get'
      '?reportName=RPT_SHAREBONUS_DET&columns=ALL'
      '&filter=(SECURITY_CODE%3D%22600570%22)&pageSize=5&sortColumns=REPORT_DATE&sortTypes=-1')
b5 = probe('datacenter 除权除息', u5)
if b5:
    print('      ', b5[:400])

print()
print('=== 6. 除权除息 老端点 f10 ===')
u6 = ('https://datacenter.eastmoney.com/securities/api/data/v1/get'
      '?reportName=RPT_F10_FHSP&columns=ALL&quoteColumns=&filter=(SECUCODE%3D%22600570.SH%22)'
      '&pageNumber=1&pageSize=5&sortTypes=-1&sortColumns=FH_DATE')
b6 = probe('datacenter.eastmoney 除权除息', u6)
if b6:
    print('      ', b6[:400])

print()
print('=== 7. 直接 TCP 连通性 (绕过 HTTP 层) ===')
import socket
for host in ('push2his.eastmoney.com', 'push2.eastmoney.com', 'quote.eastmoney.com'):
    try:
        s = socket.create_connection((host, 443), timeout=6)
        s.close()
        print(f'[OK  ] TCP 443 {host}')
    except Exception as e:
        print(f'[FAIL] TCP 443 {host}  {type(e).__name__}: {e}')