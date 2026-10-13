"""探测 Tushare 筹码相关接口在 170 积分档的可用性。"""
import json
import time
from pathlib import Path

import tushare as ts

PROJ = Path(__file__).resolve().parents[2]
TOKEN = (PROJ / 'config.txt').read_text(encoding='utf-8').strip()
ts.set_token(TOKEN)
pro = ts.pro_api()

CASES = [
    ('cyq_perf 每日筹码分布', lambda: pro.cyq_perf(
        ts_code='600570.SH', start_date='20260901', end_date='20260930')),
    ('cyq_chips 筹码分布', lambda: pro.cyq_chips(ts_code='600570.SH', trade_date='20260930')),
    ('stk_holder_number 股东人数', lambda: pro.stk_holder_number(
        ts_code='600570.SH', start_date='20250101', end_date='20260930')),
    ('stk_forecast 业绩预告', lambda: pro.stk_forecast(
        ts_code='600570.SH', start_date='20250101', end_date='20260930')),
    ('daily_basic 每日指标', lambda: pro.daily_basic(
        ts_code='600570.SH', start_date='20260901', end_date='20260930',
        fields='ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,free_share')),
    ('adj_factor 复权因子', lambda: pro.adj_factor(
        ts_code='600570.SH', start_date='20260101', end_date='20260930')),
    ('dividend 分红送股', lambda: pro.dividend(ts_code='600570.SH')),
    ('stock_basic 股票列表', lambda: pro.stock_basic(
        exchange='', list_status='L',
        fields='ts_code,name,area,industry,market,list_date')),
    ('trade_cal 交易日历', lambda: pro.trade_cal(start_date='20260101', end_date='20261231')),
]

results = []
for name, fn in CASES:
    t0 = time.time()
    try:
        df = fn()
        results.append({'name': name, 'status': 'OK',
                        'rows': 0 if df is None else len(df),
                        'cols': [] if df is None or not len(df) else list(df.columns),
                        'sample': (df.iloc[0].to_dict() if df is not None and len(df) else {}),
                        'msg': ''})
    except Exception as e:
        results.append({'name': name, 'status': 'DENY', 'rows': 0, 'cols': [],
                        'sample': {}, 'msg': str(e).replace('\n', ' ')})
    time.sleep(1.2)

out = PROJ / 'server' / 'tools' / '_ts_chip.json'
out.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
print(f'written {out}\n')
print(f"{'接口':<28} {'状态':<6} {'行数':>6}  限制")
print('-' * 78)
for r in results:
    msg = r['msg']
    if '每小时' in msg:
        lim = '1次/小时'
    elif '每分钟' in msg:
        lim = '1次/分钟'
    elif '积分' in msg or '权限' in msg:
        lim = '权限/积分不足'
    else:
        lim = '-'
    print(f"{r['name']:<28} {r['status']:<6} {r['rows']:>6}  {lim}")