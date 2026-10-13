"""间歇性 vs 硬封判定：push2his / push2 各重试 6 次。"""
import json
import re
import time
import urllib.request

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

KLINE = ('https://push2his.eastmoney.com/api/qt/stock/kline/get'
         '?secid=1.600570&fields1=f1,f2,f3,f4,f5,f6'
         '&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
         '&klt=101&fqt=1&end=20500101&lmt=3')
RT = ('https://push2.eastmoney.com/api/qt/stock/get'
      '?secid=1.600570&fields=f43,f57,f58,f60')
DC = ('https://datacenter-web.eastmoney.com/api/data/v1/get'
      '?reportName=RPT_SHAREBONUS_DET&columns=SECURITY_CODE'
      '&filter=(SECURITY_CODE%3D%22600570%22)&pageSize=1')


def try_once(url):
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/'})
        raw = urllib.request.urlopen(req, timeout=12).read()
        if b'<!DOCTYPE' in raw[:200] or b'<html' in raw[:200].lower():
            return 'HTML'
        t = raw.decode('utf-8-sig', errors='replace').strip()
        if t.startswith('{'):
            try:
                json.loads(t)
                return 'JSON'
            except Exception:
                return 'BADJSON'
        return 'OTHER'
    except Exception as e:
        return type(e).__name__


for label, url in (('push2his 历史K线', KLINE), ('push2 实时快照', RT),
                   ('datacenter-web 除权除息', DC)):
    results = []
    for i in range(6):
        results.append(try_once(url))
        time.sleep(1.5)
    ok = sum(1 for r in results if r == 'JSON')
    print(f'{label:24} 成功 {ok}/6   逐次: {results}')