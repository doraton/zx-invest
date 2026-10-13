"""补测 2 个不确定项：
1. pro_bar 报错是否为 token 初始化问题（ts.set_token 未调用）而非权限问题
2. stk_mins 返回的 31 行到底是什么数据（历史分钟还是别的）
另外验证 daily 的 pre_close 能否自算出复权因子
"""
import os
from pathlib import Path

import tushare as ts

PROJ = Path(__file__).resolve().parents[2]
TOKEN = (PROJ / 'config.txt').read_text(encoding='utf-8').strip()

# 关键：先 set_token 写本地配置，pro_bar 内部会自己读
ts.set_token(TOKEN)
pro = ts.pro_api()

print('=' * 70)
print('补测 1: pro_bar 复权行情（先 set_token 再试）')
print('=' * 70)
for adj, label in ((None, '不复权'), ('qfq', '前复权'), ('hfq', '后复权')):
    try:
        df = ts.pro_bar(ts_code='600570.SH', adj=adj,
                        start_date='20260101', end_date='20260930')
        if df is None or len(df) == 0:
            print(f'  [EMPTY] adj={adj} ({label})  返回 0 行')
        else:
            print(f'  [OK] adj={adj} ({label})  {len(df)} 行, '
                  f'{df["trade_date"].min()}~{df["trade_date"].max()}')
            print(f'        列: {list(df.columns)}')
            print(f'        末根: {df.iloc[-1].to_dict()}')
    except Exception as e:
        print(f'  [ERR] adj={adj}: {str(e)[:120]}')

print()
print('=' * 70)
print('补测 2: stk_mins 到底给了什么')
print('=' * 70)
for freq in ('1min', '5min'):
    try:
        df = pro.stk_mins(ts_code='600570.SH', freq=freq,
                          start_date='20260929 09:30:00', end_date='20260929 10:00:00')
        print(f'  freq={freq}: {0 if df is None else len(df)} 行')
        if df is not None and len(df):
            print(f'    列: {list(df.columns)}')
            print(f'    首根: {df.iloc[0].to_dict()}')
    except Exception as e:
        print(f'  freq={freq}: ERR {str(e)[:100]}')

print()
print('=' * 70)
print('补测 3: daily 的 pre_close vs adj_factor 自算复权')
print('=' * 70)
d = pro.daily(ts_code='600570.SH', start_date='20260901', end_date='20260930')
a = pro.adj_factor(ts_code='600570.SH', start_date='20260901', end_date='20260930')
d = d.merge(a, on=['ts_code', 'trade_date'], how='left')
print(f'  daily 列: {list(d.columns)}')
print(f'  合并后 {len(d)} 行, adj_factor 空值 {d["adj_factor"].isna().sum()} 行')
print()
print('  最近 5 根（不复权 close / adj_factor / hfq close = close*factor）:')
for _, r in d.tail(5).sort_values('trade_date').iterrows():
    print(f'    {r["trade_date"]}  close={r["close"]:>7.2f}  '
          f'pre_close={r["pre_close"]:>7.2f}  factor={r["adj_factor"]:.6f}  '
          f'hfq_close={r["close"] * r["adj_factor"]:>8.2f}  amount={r["amount"]:.0f}')
print()
print('  除权日检测（close/pre_close 跳变 > 5%）:')
d2 = d.sort_values('trade_date').copy()
d2['jump'] = (d2['close'] / d2['pre_close'] - 1).abs()
big = d2[d2['jump'] > 0.05]
print(f'    区间内 {len(big)} 根跳变 >5%:')
for _, r in big.iterrows():
    print(f'      {r["trade_date"]}  pre_close={r["pre_close"]}  close={r["close"]}  '
          f'跳变={r["jump"]*100:.2f}%')

print()
print('=' * 70)
print('补测 4: 能否一次拉多只股票（验证批量能力）')
print('=' * 70)
try:
    df = pro.daily(ts_code='600570.SH,600519.SH,000001.SZ',
                    start_date='20260925', end_date='20260930')
    print(f'  多股一次拉: {len(df)} 行, 覆盖 {df["ts_code"].nunique()} 只')
    print(f'  {df.groupby("ts_code").size().to_dict()}')
except Exception as e:
    print(f'  ERR: {str(e)[:100]}')