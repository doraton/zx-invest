"""验证 Tushare 前复权机制与行情软件的差异。

官方说法（doc_id=122）：
  1) Tushare 以用户设定的 end_date 为复权基准日，行情软件以最近交易日为基准
  2) Tushare 用「分红再投」模式

验证点：
  A. adj_factor 是否受 end_date 影响？（若不受 → 它是稳定的后复权因子，可自行选基准）
  B. 用 adj_factor 合成的 qfq 在不同基准日下差异多大？
  C. 与腾讯 fqt=1（基准日=最新交易日）对比，看是否一致
"""
import json
import time
from pathlib import Path

import tushare as ts

PROJ = Path(__file__).resolve().parents[2]
TOKEN = (PROJ / 'config.txt').read_text(encoding='utf-8').strip()
ts.set_token(TOKEN)
pro = ts.pro_api()

OUT = {}

# ---------- A. adj_factor 是否受 end_date 影响 ----------
# adj_factor 是 1次/小时，一次调用就要覆盖全区间。
# 关键验证思路：拿「同一批 factor」在两个不同 end_date 区间内取到的值做对比——
# 若 factor 完全相同，说明它与 end_date 无关（是稳定的后复权因子）。
print('=' * 72)
print('A. adj_factor 对 end_date 的敏感性')
print('=' * 72)
FULL = pro.adj_factor(ts_code='600570.SH', start_date='20200101', end_date='20260930')
print(f'  一次调用拉全区间 -> {len(FULL)} 行 '
      f'({FULL["trade_date"].min()} ~ {FULL["trade_date"].max()})')
time.sleep(1.2)
f1 = pro.adj_factor(ts_code='600570.SH', start_date='20260101', end_date='20260630')
print(f'  再按 end_date=20260630 拉 -> {len(f1)} 行（此处预期 1次/小时 拒绝）')

# ---------- B. daily 全区间 ----------
print()
print('=' * 72)
print('B. 不同基准日下 qfq 的差异')
print('=' * 72)
d = pro.daily(ts_code='600570.SH', start_date='20240101', end_date='20260930')
af = FULL[FULL['trade_date'] >= '20240101']
m = d.merge(af, on=['ts_code', 'trade_date']).sort_values('trade_date').reset_index(drop=True)
print(f'  daily+adj_factor 合并 {len(m)} 行')
print(f'  factor 范围 {m["adj_factor"].min():.6f} ~ {m["adj_factor"].max():.6f}')

latest_f = m['adj_factor'].iloc[-1]
print(f'  最新 factor = {latest_f:.6f}（基准日 {m["trade_date"].iloc[-1]}）')

# 基准日1 = 最新交易日（行情软件口径）
qfq_latest = m['close'] * m['adj_factor'] / latest_f
# 基准日2 = 2026-06-30（Tushare pro_bar 若传该 end_date 的口径）
mid = m[m['trade_date'] <= '2026-06-30']
qfq_june = mid['close'] * mid['adj_factor'] / mid['adj_factor'].iloc[-1]

n = min(len(qfq_june), len(qfq_latest))
print(f'\n  同一天、不同基准日的前复权价对比（前 {n} 根重叠）:')
print('    date         close   qfq@最新  qfq@0630   差异%')
for i in range(0, n, max(n // 6, 1)):
    dt = mid['trade_date'].iloc[i]
    a = float(qfq_june.iloc[i])
    b = float(qfq_latest.iloc[i])
    pct = (a - b) / b * 100 if b else 0
    print(f'    {dt}  {float(mid["close"].iloc[i]):>7.2f}  {b:>8.2f}  {a:>8.2f}  {pct:>+6.2f}%')

maxdiff = float(((qfq_june - qfq_latest.iloc[:n]) / qfq_latest.iloc[:n]).abs().max() * 100)
print(f'\n  最大相对差异 {maxdiff:.2f}%')
OUT['qfq_max_diff_pct'] = round(maxdiff, 4)

# ---------- C. 与腾讯 fqt=1 对比 ----------
print()
print('=' * 72)
print('C. 与腾讯 fqt=1（基准日=最新交易日）对比')
print('=' * 72)
import urllib.request
url = ('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
       '?param=sh600570,day,2026-01-01,2026-09-30,320,qfq')
req = urllib.request.Request(url, headers={
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
    'Referer': 'https://gu.qq.com/'})
tx = {}
try:
    raw = urllib.request.urlopen(req, timeout=20).read()
    j = json.loads(raw.decode('utf-8', errors='replace'))
    node = j['data']['sh600570']
    arr = node.get('qfqday') or node.get('day') or []
    for row in arr:
        tx[row[0]] = {'open': float(row[1]), 'close': float(row[2]),
                      'high': float(row[3]), 'low': float(row[4])}
    print(f'  腾讯 qfq 返回 {len(tx)} 根')
except Exception as e:
    print(f'  腾讯接口失败: {type(e).__name__}: {str(e)[:90]}')

if tx:
    mine = {r['trade_date']: {'open': float(r['open'] * r['adj_factor'] / latest_f),
                               'close': float(r['close'] * r['adj_factor'] / latest_f),
                               'high': float(r['high'] * r['adj_factor'] / latest_f),
                               'low': float(r['low'] * r['adj_factor'] / latest_f)}
            for _, r in m.iterrows()}
    common = sorted(set(mine) & set(tx))
    print(f'  共同日期 {len(common)} 天')
    print('\n  date        tsClose  txClose   tsOpen   txOpen')
    for dte in common[-6:]:
        a, b = mine[dte], tx[dte]
        print(f'  {dte}  {a["close"]:>8.2f} {b["close"]:>8.2f}  {a["open"]:>8.2f} {b["open"]:>8.2f}')
    cd = [abs(mine[d]['close'] - tx[d]['close']) / tx[d]['close'] for d in common]
    od = [abs(mine[d]['open'] - tx[d]['open']) / tx[d]['open'] for d in common]
    print(f'\n  close 最大相对差 {max(cd)*100:.4f}%   open 最大相对差 {max(od)*100:.4f}%')
    OUT['vs_tencent_close_max_pct'] = round(max(cd) * 100, 4)
    OUT['vs_tencent_days'] = len(common)

(OUT_FILE := PROJ / 'server' / 'tools' / '_qfq_test.json').write_text(
    json.dumps(OUT, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
print(f'\n结果已写入 {OUT_FILE}')