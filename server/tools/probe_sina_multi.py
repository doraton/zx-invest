import urllib.request

# 新浪多指数一次性拉
url = 'http://hq.sinajs.cn/list=sh000001,sz399001,sz399006,sh000688'
req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn/'})
with urllib.request.urlopen(req, timeout=10) as resp:
    body = resp.read().decode('gbk')

for line in body.strip().split('\n'):
    if not line.strip():
        continue
    sym = line.split('=')[0]
    payload = line.split('"')[1]
    fields = payload.split(',')
    print(f'{sym}: close={fields[1]} prev_close={fields[2]} open={fields[3]} high={fields[4]} low={fields[5]}')