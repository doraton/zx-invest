import json, urllib.request, urllib.parse, pymysql
B = "http://localhost:8310/api/index/kline"
def q(**kw):
    u = urllib.parse.urlencode({"code":"sh600570","category":4, **kw})
    with urllib.request.urlopen(f"{B}?{u}", timeout=15) as r:
        return json.loads(r.read())

PAGE, CAP = 250, 1000
print(f"首屏 {PAGE} 根 / 每次向左 {PAGE} 根 / 封顶 {CAP} 根（闭区间游标，前端按 datetime 去重）\n")
seen = {}          # datetime -> bar，用 dict 天然去重
order = []
def add(bars):
    for b in bars:
        if b["datetime"] not in seen:
            seen[b["datetime"]] = b; order.append(b["datetime"])

first = q(count=PAGE); add(first["data"]); trunc = first["truncated"]
print(f"  首屏: {len(first['data'])} 根  最早 {order[0]}  truncated={trunc}")
step = 0
while trunc and len(seen) < CAP:
    step += 1
    room = CAP - len(seen)
    if room <= 0: break
    r = q(count=min(PAGE, room), end_date=order[0])
    if not r["data"]: break
    before = len(seen)
    add(r["data"])
    dup = len(r["data"]) - (len(seen) - before)
    print(f"  左滑{step}: 返回 {len(r['data'])} 根 (重叠 {dup}) -> 累计唯一 {len(seen)} 根 "
          f"最早 {order[0]}  truncated={r['truncated']}")
    trunc = r["truncated"]

acc = [seen[d] for d in order]
print(f"\n最终 {len(acc)} 根  {acc[0]['datetime']} ~ {acc[-1]['datetime']}")
dates = [x["datetime"] for x in acc]
print(f"  无重复: {len(dates)==len(set(dates))}   严格升序: {all(dates[i]<dates[i+1] for i in range(len(dates)-1))}")

conn = pymysql.connect(host="127.0.0.1", user="root", password="dba126543", database="tsdb", charset="utf8mb4")
c = conn.cursor()
c.execute("""SELECT DATE_FORMAT(trade_date,'%%Y-%%m-%%d'), close FROM kline_day
             WHERE code='sh600570' AND trade_date BETWEEN %%s AND %%s ORDER BY trade_date""",
          (acc[0]["datetime"], acc[-1]["datetime"]))
truth = c.fetchall(); conn.close()
print(f"  库内同区间真值: {len(truth)} 根")
mism = [(d, api['close'], db) for (d, db), api in zip(truth, acc)
        if d != api["datetime"] or abs(db - api["close"]) > 0.0001]
print(f"  逐根对账不一致: {len(mism)} 处")
for m in mism[:3]: print("   ", m)
ok = not mism and len(truth) == len(acc)
print("\n结论:", "游标分页与库内真值逐根一致，无跳格、无重叠、无遗漏" if ok else "有问题")
print(f"可回溯到 {acc[0]['datetime']}（4.1 年前），库内还有 {5432-len(acc)} 根未取（受 1000 封顶）")
