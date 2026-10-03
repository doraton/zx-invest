import urllib.request

url = 'http://hq.sinajs.cn/list=sh000001'
req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn'})
with urllib.request.urlopen(req, timeout=10) as resp:
    body = resp.read().decode('gbk')
data = body.split('"')[1].split(',')
for i, v in enumerate(data):
    print(i, repr(v))