import urllib.request
import json

# 测试 4 个指数
symbols = ['sh000001', 'sz399001', 'sz399006', 'sh000688']
names = {'sh000001': '上证指数', 'sz399001': '深证成指', 'sz399006': '创业板指', 'sh000688': '科创50'}

for sym in symbols:
    print(f'=== {sym} ({names[sym]}) ===')
    # 日 K
    url = f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,3,qfq'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
            if data.get('code') == 0:
                info = (data.get('data') or {}).get(sym) or {}
                qfq = info.get('qfqday') or info.get('day') or []
                print(f'  day rows={len(qfq)}, last={qfq[-1] if qfq else None}')
            else:
                print(f'  code != 0: {data.get("code")}')
    except Exception as e:
        print(f'  ERR: {e}')
    # 实时（用东方财富备用，因为新浪行情可能不支持）
    url2 = f'http://qt.gtimg.cn/q={sym}'
    try:
        req2 = urllib.request.Request(url2, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req2, timeout=5) as r:
            body = r.read().decode('gbk')
            print(f'  腾讯实时: {body[:120]}')
    except Exception as e:
        print(f'  实时 ERR: {e}')