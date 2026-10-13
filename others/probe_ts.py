"""Tushare 170 积分能力实测探针。

token 读取优先级：
  1. 环境变量 ZX_TUSHARE_TOKEN
  2. 项目根 config.txt（裸 token 单行）
  3. tushare 自带的本地配置（此前调过 ts.set_token）

用法：
  .\\.venv\\Scripts\\python.exe tools\\probe_ts.py
"""
import os
import sys
import time
from pathlib import Path

import tushare as ts

PROJ = Path(__file__).resolve().parents[2]


def load_token():
    env = os.getenv('ZX_TUSHARE_TOKEN', '').strip()
    if env:
        return env, 'env ZX_TUSHARE_TOKEN'
    cfg = PROJ / 'config.txt'
    if cfg.exists():
        t = cfg.read_text(encoding='utf-8').strip().splitlines()
        for line in t:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # 兼容 "KEY=VALUE" 和裸 token 两种写法
            if '=' in line and len(line.split('=', 1)[0]) < 30:
                return line.split('=', 1)[1].strip(), f'config.txt:{line.split("=",1)[0].strip()}'
            return line, 'config.txt (裸 token)'
    return None, '未找到'


TOKEN, SRC = load_token()
if not TOKEN:
    print('[setup] 找不到 token。请设置 ZX_TUSHARE_TOKEN 或在项目根放 config.txt')
    sys.exit(1)
print(f'[setup] token 来源: {SRC}  (长度 {len(TOKEN)})')

pro = ts.pro_api(token=TOKEN)

OK, DENY, ERR, EMPTY = 'OK', 'DENY', 'ERR ', 'EMPTY'


def probe(label, fn, show=None):
    """返回 (状态, 摘要)。show 为可选的格式化函数。"""
    t0 = time.time()
    try:
        df = fn()
    except Exception as e:
        msg = str(e).replace('\n', ' ')[:110]
        # 权限类错误统一归 DENY
        low = msg.lower()
        if any(k in low for k in ('积分', '权限', 'permission', '无权', '您没有')):
            print(f'  [{DENY}] {label:<34} {msg}')
            return DENY, msg
        print(f'  [{ERR}] {label:<34} {type(e).__name__}: {msg}')
        return ERR, msg

    dt = time.time() - t0
    if df is None or len(df) == 0:
        print(f'  [{EMPTY}] {label:<34} 返回 0 行 ({dt:.2f}s)')
        return EMPTY, '0 行'
    detail = show(df) if show else f'{len(df)} 行'
    print(f'  [{OK}] {label:<34} {detail} ({dt:.2f}s)')
    return OK, detail


print('=' * 78)
print('Tushare 能力实测')
print('=' * 78)

# ---------- 1. 基础数据 ----------
print('\n[1] 基础数据')
probe('股票列表 stock_basic', lambda: pro.stock_basic(
    exchange='', list_status='L', fields='ts_code,name,area,industry,list_date'),
    lambda d: f'{len(d)} 行, 列={list(d.columns)}')
probe('交易日历 trade_cal', lambda: pro.trade_cal(
    start_date='20260101', end_date='20260131'),
    lambda d: f'{len(d)} 行')
probe('上市公司基本信息 stock_company',
      lambda: pro.stock_company(ts_code='600570.SH'),
      lambda d: f'{len(d)} 行')
probe('曾用名 namechange', lambda: pro.namechange(ts_code='600519.SH'))

# ---------- 2. 行情数据（核心） ----------
print('\n[2] 行情数据')
probe('历史日线 daily', lambda: pro.daily(
    ts_code='600570.SH', start_date='20260901', end_date='20260930'),
    lambda d: f'{len(d)} 行, {d["trade_date"].min()}~{d["trade_date"].max()}, 列={list(d.columns)}')
probe('历史日线(全历史深度)', lambda: pro.daily(
    ts_code='600570.SH', start_date='19900101', end_date='20991231'),
    lambda d: f'{len(d)} 行, 最早={d["trade_date"].min()}')
probe('周线行情 weekly', lambda: pro.weekly(
    ts_code='600570.SH', start_date='20260101', end_date='20260930'),
    lambda d: f'{len(d)} 行')
probe('月线行情 monthly', lambda: pro.monthly(
    ts_code='600570.SH', start_date='20200101', end_date='20260930'),
    lambda d: f'{len(d)} 行')
probe('复权行情 pro_bar(daily adj=hfq)', lambda: ts.pro_bar(
    ts_code='600570.SH', adj='hfq', start_date='20260101', end_date='20260930'),
    lambda d: f'{len(d)} 行, {d["trade_date"].min()}~{d["trade_date"].max()}, 列={list(d.columns)}')
probe('复权因子 adj_factor', lambda: pro.adj_factor(
    ts_code='600570.SH', start_date='20260101', end_date='20260930'),
    lambda d: f'{len(d)} 行, 列={list(d.columns)}')
probe('每日指标 daily_basic', lambda: pro.daily_basic(
    ts_code='600570.SH', trade_date='20260930',
    fields='ts_code,trade_date,pe_ttm,pb,turnover_rate,total_mv'),
    lambda d: f'{len(d)} 行')
probe('涨跌停价格 stk_limit', lambda: pro.stk_limit(
    ts_code='600570.SH', start_date='20260901', end_date='20260930'),
    lambda d: f'{len(d)} 行')
probe('停复牌 suspend_d', lambda: pro.suspend_d(
    ts_code='600570.SH', start_date='20260101', end_date='20260930'))

# ---------- 3. 财务数据（项目最缺的） ----------
print('\n[3] 财务数据')
probe('利润表 income', lambda: pro.income(
    ts_code='600570.SH', start_date='20240101', end_date='20260930'),
    lambda d: f'{len(d)} 行, 列数={len(d.columns)}')
probe('资产负债表 balancesheet', lambda: pro.balancesheet(
    ts_code='600570.SH', start_date='20240101', end_date='20260930'),
    lambda d: f'{len(d)} 行, 列数={len(d.columns)}')
probe('现金流量表 cashflow', lambda: pro.cashflow(
    ts_code='600570.SH', start_date='20240101', end_date='20260930'),
    lambda d: f'{len(d)} 行, 列数={len(d.columns)}')
probe('分红送股 dividend', lambda: pro.dividend(
    ts_code='600570.SH'),
    lambda d: f'{len(d)} 行, 列={list(d.columns)}')
probe('财务指标 fina_indicator', lambda: pro.fina_indicator(
    ts_code='600570.SH', start_date='20240101', end_date='20260930'),
    lambda d: f'{len(d)} 行, 列数={len(d.columns)}')

# ---------- 4. 板块（决定 tdx2db 去留） ----------
print('\n[4] 板块分类（关键：与 tdx2db 136461 条对照）')
probe('申万行业分类 index_classify',
      lambda: pro.index_classify(level='L1', src='SW2021'),
      lambda d: f'{len(d)} 行')
probe('申万行业成分 index_member_all',
      lambda: pro.index_member_all(l1_code='801080.SI'),
      lambda d: f'{len(d)} 行')
probe('THS 概念板块分类 ths_index',
      lambda: pro.ths_index(exchange='A'),
      lambda d: f'{len(d)} 行')
probe('THS 概念成分 ths_member',
      lambda: pro.ths_member(ts_code='885300.TI'),
      lambda d: f'{len(d)} 行')
probe('DC 概念板块分类 dc_index', lambda: pro.dc_index(trade_date='20260930'),
      lambda d: f'{len(d)} 行')
probe('**TDX 概念板块分类** tdx_index',
      lambda: pro.tdx_index(exchange='A'),
      lambda d: f'{len(d)} 行')
probe('**TDX 概念成分** tdx_member',
      lambda: pro.tdx_member(ts_code='880519'),
      lambda d: f'{len(d)} 行')
probe('**TDX 板块行情** tdx_daily',
      lambda: pro.tdx_daily(trade_date='20260930'),
      lambda d: f'{len(d)} 行')

# ---------- 5. 独立权限类（预期 DENY） ----------
print('\n[5] 独立权限类（分钟/实时/公告/研报，预期 DENY 或需单独开通）')
probe('历史分钟 stk_mins', lambda: pro.stk_mins(
    ts_code='600570.SH', freq='1min', start_date='20260929 09:30:00',
    end_date='20260929 10:00:00'))
probe('实时日线 rt_k', lambda: pro.rt_k(ts_code='600570.SH'))
probe('上市公司公告 anns_d', lambda: pro.anns_d(
    ts_code='600570.SH', start_date='20260901', end_date='20260930'))
probe('券商研报 report_rc', lambda: pro.report_rc(
    ts_code='600570.SH', start_date='20260901', end_date='20260930'))
probe('盘前股本 stk_premarket', lambda: pro.stk_premarket(
    ts_code='600570.SH', trade_date='20260930'))

# ---------- 6. 频率限制实测 ----------
print('\n[6] 频率限制探测（连打 daily，看第几次开始被拒）')
success = 0
denied_at = None
for i in range(1, 61):
    try:
        df = pro.daily(ts_code='600570.SH', trade_date='20260930')
        if df is not None and len(df):
            success += 1
    except Exception as e:
        msg = str(e).replace('\n', ' ')[:80]
        denied_at = (i, msg)
        break
    time.sleep(0.05)
print(f'  连打 60 次：成功 {success} 次' +
      (f'，第 {denied_at[0]} 次被拒：{denied_at[1]}' if denied_at else '，未触发限流'))

print('\n' + '=' * 78)
print('小结：OK=可用  DENY=积分/权限不足  EMPTY=能调但无数据  ERR=其他错误')
print('=' * 78)