"""只跑剩余两项：复权自算验证 + 多股批量。等限流窗口重置。"""
import time
from pathlib import Path

import tushare as ts

PROJ = Path(__file__).resolve().parents[2]
TOKEN = (PROJ / 'config.txt').read_text(encoding='utf-8').strip()
ts.set_token(TOKEN)
pro = ts.pro_api()

print('[wait] 等限流窗口重置 65s ...')
time.sleep(65)
print('[wait] ok\n')

print('=' * 70)
print('验证 1: daily(pre_close) + adj_factor 自算复权')
print('=' * 70)
d = pro.daily(ts_code='600570.SH', start_date='20260901', end_date='20260930')
a = pro.adj_factor(ts_code='600570.SH', start_date='20260901', end_date='20260930')
d = d.merge(a, on=['ts_code', 'trade_date'], how='left').sort_values('trade_date')
print(f'  daily+adj_factor 合并 {len(d)} 行, factor 空值 {d["adj_factor"].isna().sum()} 行')
print(f'  列: {list(d.columns)}')
print()
print('  trade_date      close   pre_close     factor   hfq_close        amount')
for _, r in d.tail(5).iterrows():
    print(f'  {r["trade_date"]}  {r["close"]:>7.2f}  {r["pre_close"]:>9.2f}  '
          f'{r["adj_factor"]:.6f}  {r["close"] * r["adj_factor"]:>9.2f}  {r["amount"]:>14.0f}')

print()
print('  除权日检测（close/pre_close 偏离 >3%，理论只在除权日跳变）:')
d['jump'] = (d['close'] / d['pre_close'] - 1).abs()
big = d[d['jump'] > 0.03]
print(f'    {len(big)} 根:')
for _, r in big.iterrows():
    print(f'      {r["trade_date"]}  pre_close={r["pre_close"]:>8.2f}  '
          f'close={r["close"]:>7.2f}  跳变={r["jump"]*100:>6.2f}%')

print()
print('=' * 70)
print('验证 2: 一次拉多只股票')
print('=' * 70)
df = pro.daily(ts_code='600570.SH,600519.SH,000001.SZ,300750.SZ,688981.SH',
               start_date='20260925', end_date='20260930')
print(f'  一次拉 5 只: {len(df)} 行, 覆盖 {df["ts_code"].nunique()} 只')
print(f'  每只行数: {df.groupby("ts_code").size().to_dict()}')

print()
print('=' * 70)
print('验证 3: 全历史一次性拉取深度（多股 + 全区间）')
print('=' * 70)
deep = pro.daily(ts_code='600570.SH', start_date='19900101', end_date='20260930')
print(f'  600570 全历史: {len(deep)} 行, 最早={deep["trade_date"].min()}  '
      f'最新={deep["trade_date"].max()}')
print(f'  字段: {list(deep.columns)}')
print(f'  vol 单位抽样(手?股?): 首行 vol={deep.iloc[0]["vol"]}, amount={deep.iloc[0]["amount"]}')
print(f'  → 换手校验: amount/vol = {deep.iloc[0]["amount"]/deep.iloc[0]["vol"]:.2f} '
      f'(应约等于均价 {deep.iloc[0]["close"]:.2f} 的量级)')