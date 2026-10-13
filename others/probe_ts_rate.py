"""精确探测每个接口的真实频次限制。

问题：PowerShell 5.1 cp936 会把中文报错撕碎，
所以这里把错误消息原样写入 UTF-8 文件，再用 read 工具读。
"""
import json
import time
from pathlib import Path

import tushare as ts

PROJ = Path(__file__).resolve().parents[2]
TOKEN = (PROJ / 'config.txt').read_text(encoding='utf-8').strip()
ts.set_token(TOKEN)
pro = ts.pro_api()

OUT = PROJ / 'server' / 'tools' / '_ts_ratelimit.json'

CASES = [
    ('daily',          lambda: pro.daily(ts_code='600570.SH', trade_date='20260930')),
    ('weekly',         lambda: pro.weekly(ts_code='600570.SH', start_date='20260901', end_date='20260930')),
    ('monthly',        lambda: pro.monthly(ts_code='600570.SH', start_date='20260101', end_date='20260930')),
    ('daily_basic',    lambda: pro.daily_basic(ts_code='600570.SH', trade_date='20260930')),
    ('dividend',       lambda: pro.dividend(ts_code='600570.SH')),
    ('stock_basic',    lambda: pro.stock_basic(ts_code='600570.SH')),
    ('adj_factor',     lambda: pro.adj_factor(ts_code='600570.SH', start_date='20260101', end_date='20260930')),
    ('trade_cal',      lambda: pro.trade_cal(start_date='20260901', end_date='20260930')),
    ('namechange',     lambda: pro.namechange(ts_code='600570.SH')),
    ('stock_company',  lambda: pro.stock_company(ts_code='600570.SH')),
    ('stk_mins',       lambda: pro.stk_mins(ts_code='600570.SH', freq='1min',
                                            start_date='20260929 09:30:00',
                                            end_date='20260929 09:35:00')),
]

results = []
for name, fn in CASES:
    t0 = time.time()
    try:
        df = fn()
        results.append({
            'api': name,
            'status': 'OK',
            'rows': 0 if df is None else len(df),
            'elapsed': round(time.time() - t0, 2),
            'msg': '',
        })
    except Exception as e:
        results.append({
            'api': name,
            'status': 'DENY',
            'rows': 0,
            'elapsed': round(time.time() - t0, 2),
            'msg': str(e).replace('\n', ' '),
        })
    time.sleep(1.1)

OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'written: {OUT}')

# 同时把每个接口声明的限制做一次汇总输出（ASCII）
print('\nAPI                STATUS  ROWS  ELAPSED')
for r in results:
    print(f"{r['api']:<18} {r['status']:<6} {r['rows']:>4}  {r['elapsed']:>6}s")