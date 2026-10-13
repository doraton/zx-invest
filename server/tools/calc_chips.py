"""筹码分布计算 —— 三角形分布衰减法（通达信/东财一致口径）。

**为什么可以自己算**：筹码分布不是「存储的数据」，而是「由行情推算的派生量」。
本机通达信 T0002/hq_cache 下没有任何筹码数据文件（只有 cloud_dax/CHIPLETGN.sp
那个指标公式），因为通达信/东财客户端也是在运行时算的。

**算法**（每个交易日递推）：
    1. 换手率 h = 当日成交量 / 流通股本（裁剪到 [0, 1]）
    2. 衰减：原有全部筹码 × (1 - h)        —— 未换手的人还在
    3. 新增：当日成交量对应的筹码，按**三角形分布**摊到 [最低价, 最高价]
       三角形峰在区间中点：
           p(x) = 4 * min(x - low, high - x) / (high - low)^2
       （∫p = 1，可验证归一性）
    4. 初始状态：全部筹码集中在首日收盘价

**所需输入**：日线 OHLCV（zx_invest.kline_day）+ 流通股本
⚠️ **单位**：`kline_day.volume` 存**手**（1 手 = 100 股），流通股本存**股**，
换手率必须先把手换成股再相除，见 calc_chips() 里那行 `* 100.0`。
流通股本优先取 Tushare daily_basic.free_share，其次退化为
volume / (turnover_rate/100)，都没有则用 tdx2db.stock_info.ltag。
"""
import argparse
import sys
from pathlib import Path

import numpy as np

import pymysql

STEP = 0.01  # 价格档位（元）


def fetch_series(db: str, code: str, start: str | None = None, end: str | None = None):
    c = pymysql.connect(host='127.0.0.1', port=3306, user='root',
                        password='dba126543', database=db, charset='utf8mb4')
    cur = c.cursor()
    sql = ("SELECT trade_date, open, high, low, close, volume "
           "FROM kline_day WHERE code=%s")
    args = [code]
    if start:
        sql += " AND trade_date >= %s"
        args.append(start)
    if end:
        sql += " AND trade_date <= %s"
        args.append(end)
    sql += " ORDER BY trade_date"
    cur.execute(sql, args)
    rows = cur.fetchall()
    c.close()
    return rows


def fetch_float_share(code: str):
    """流通股本（股）。

    tdx2db.stock_info.ltag 单位是万股，且 code 口径是**带市场前缀的 8 位**
    （'sh600570'，与 daily_data.code 的 6 位口径不同——这也是 tdx2db 0.5.0
    跨表 code 不一致的坑）。两种写法都试一次。
    """
    variants = [code]
    if len(code) > 2 and code[:2] in ('sh', 'sz', 'bj'):
        variants.append(code[2:])
    else:
        variants.append(('sh' if code.startswith(('6', '9')) else 'sz') + code)
    try:
        c = pymysql.connect(host='127.0.0.1', port=3306, user='root',
                            password='dba126543', database='tdx2db', charset='utf8mb4')
        cur = c.cursor()
        for v in variants:
            cur.execute("SELECT ltag, code FROM stock_info WHERE code=%s", (v,))
            row = cur.fetchone()
            if row and row[0]:
                c.close()
                return float(row[0]) * 1e4, f'tdx2db.stock_info.ltag (code={row[1]}, 万股×1e4)'
        c.close()
    except Exception as e:
        print(f'  [warn] tdx2db 查询失败: {e}')
    return None, None


def calc_chips(rows, float_share, step: float = STEP):
    """返回 (grid, chips_matrix, meta)。chips_matrix[i] 是第 i 日的筹码分布。"""
    dates = [r[0] for r in rows]
    highs = np.array([float(r[2]) for r in rows])
    lows = np.array([float(r[3]) for r in rows])
    closes = np.array([float(r[4]) for r in rows])
    vols = np.array([float(r[5]) for r in rows])

    lo_all = np.floor(lows.min() / step) * step
    hi_all = np.ceil(highs.max() / step) * step
    grid = np.round(np.arange(lo_all, hi_all + step / 2, step), 2)
    n = len(grid)

    # 初始筹码全部集中在首日收盘价
    chips = np.zeros(n, dtype=np.float64)
    init_i = int(np.argmin(np.abs(grid - closes[0])))
    chips[init_i] = float_share

    # 预计算每日的三角形分布（只落在当日 [low, high] 区间内）
    snapshots = []
    turns = []
    for i in range(len(rows)):
        low, high = lows[i], highs[i]
        # volume 存「手」、float_share 存「股」，换算率必须是**无量纲比值**，
        # 所以这里要 ×100 把手换成股再除。
        # ⚠️ 漏掉这个 ×100 时换手率会大 100 倍、被下面的 clip 全部压到 1.0，
        #    而「筹码总量守恒」断言**依然会通过**（衰减到 0 再补回满仓，总量不变）——
        #    量纲错误在这里是完全静默的，只能靠这一行注释提醒。
        turn = 0.0 if float_share <= 0 else vols[i] * 100.0 / float_share
        turn = min(max(turn, 0.0), 1.0)
        turns.append(turn)

        if i > 0:
            # 1) 衰减（无条件）
            chips *= (1.0 - turn)
            # 2) 新增筹码。用 turn * float_share 而非原始 vol：
            #    turn 被 clip 到 1.0（新股/极端换手）时，只有用裁剪后的
            #    turn 乘流通股本，总量才严格守恒。
            add = turn * float_share
            if add > 0:
                width = high - low
                if width > 1e-9:
                    # 三角形分布。dist 是密度，离散到价档要 × step 才是质量。
                    in_range = (grid >= low) & (grid <= high)
                    if in_range.any():
                        dist = np.where(
                            in_range,
                            4.0 * np.minimum(grid - low, high - grid) / (width ** 2),
                            0.0) * step
                        total = dist.sum()
                        if total > 0:
                            chips += add * dist / total
                else:
                    # 一字板：high == low，三角分布退化为该价位的点分布。
                    # 这里**不能跳过**，否则衰减掉了却不补回，总量不守恒。
                    j = int(np.argmin(np.abs(grid - low)))
                    chips[j] += add
        snapshots.append(chips.copy())

    chips_matrix = np.array(snapshots)

    # 守恒性断言：每一步都必须保持「总量 == 流通股本」。
    # 三角形分布离散化时最容易犯的错就是漏乘/多乘 price step，
    # 导致总量差一个数量级——这个断言能在第一时间暴露它。
    totals = chips_matrix.sum(axis=1)
    drift = np.abs(totals - float_share) / float_share
    if drift.max() > 1e-6:
        bad = int(np.argmax(drift))
        raise AssertionError(
            f'筹码总量不守恒：末日 {totals[-1]:,.2f} vs 流通股本 {float_share:,.2f} '
            f'(相对偏差 {drift[bad]:.4%}，出现在第 {bad} 日)。'
            f'通常是三角形分布离散化时漏乘/多乘了价格步长 {step}。')
    meta = {
        'days': len(rows),
        'first': dates[0], 'last': dates[-1],
        'price_min': float(grid.min()), 'price_max': float(grid.max()),
        'turnover_min': float(np.min(turns)), 'turnover_max': float(np.max(turns)),
        'turnover_mean': float(np.mean(turns)),
        'chips_per_day': float_share,
    }
    return grid, chips_matrix, meta


def analyze(grid, chips_matrix, meta):
    """基于筹码分布算几个常用指标。"""
    out = []

    def at(i):
        return chips_matrix[i]

    for i in range(len(chips_matrix) - 1, max(len(chips_matrix) - 6, -1), -1):
        c = at(i)
        total = c.sum()
        if total <= 0:
            continue
        p = c / total
        # 平均成本
        avg_cost = float((grid * p).sum())
        # 90% 筹码集中区间（获利盘/套牢盘分界常用）
        cum = np.cumsum(p)
        p5 = float(grid[int(np.searchsorted(cum, 0.05))])
        p95 = float(grid[int(np.searchsorted(cum, 0.95))])
        p15 = float(grid[int(np.searchsorted(cum, 0.15))])
        p85 = float(grid[int(np.searchsorted(cum, 0.85))])
        # 众数（最密集价格）
        mode_i = int(np.argmax(c))
        mode = float(grid[mode_i])
        # 集中度：90% 区间宽度 / 价格
        conc = (p95 - p5) / avg_cost if avg_cost else 0.0
        out.append({
            'date': meta['last'] if i == len(chips_matrix) - 1 else None,
            'avg_cost': avg_cost, 'p5': p5, 'p15': p15,
            'p85': p85, 'p95': p95, 'mode': mode, 'conc': conc,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description='筹码分布计算（三角形分布衰减法）')
    ap.add_argument('--db', default='zx_invest')
    ap.add_argument('--code', default='sh600570')
    ap.add_argument('--start', default='20240101')
    ap.add_argument('--end', default='20260930')
    ap.add_argument('--save', action='store_true', help='写入 chip_distribution 表')
    args = ap.parse_args()

    start = f"{args.start[:4]}-{args.start[4:6]}-{args.start[6:]}" if args.start else None
    end = f"{args.end[:4]}-{args.end[4:6]}-{args.end[6:]}" if args.end else None

    rows = fetch_series(args.db, args.code, start, end)
    if not rows:
        print('没有日线数据')
        return 1
    fshare, src = fetch_float_share(args.code)
    if not fshare:
        print('找不到流通股本，无法计算换手率')
        return 1

    grid, mat, meta = calc_chips(rows, fshare)
    print('=' * 72)
    print(f'筹码分布计算完成  {args.code}  ({src})')
    print('=' * 72)
    print(f"  交易日数 {meta['days']}  {meta['first']} ~ {meta['last']}")
    print(f"  价格区间 {meta['price_min']:.2f} ~ {meta['price_max']:.2f} 元"
          f"  ({len(grid)} 个 {STEP} 元档位)")
    print(f"  流通股本 {meta['chips_per_day']:,.0f} 股")
    print(f"  换手率 min={meta['turnover_min']*100:.3f}%  "
          f"mean={meta['turnover_mean']*100:.3f}%  max={meta['turnover_max']*100:.3f}%")

    total = mat[-1].sum()
    print(f"\n  末日筹码总量 {total:,.0f} 股 "
          f"(与流通股本比 {total/fshare*100:.4f}%)")

    # 末日筹码分布形态
    last = mat[-1]
    nz = np.nonzero(last)[0]
    print(f"  筹码实际分布区间 {grid[nz[0]]:.2f} ~ {grid[nz[-1]]:.2f} 元")

    print('\n  最新交易日筹码特征:')
    p = last / last.sum()
    cum = np.cumsum(p)
    avg = float((grid * p).sum())
    p5 = float(grid[int(np.searchsorted(cum, 0.05))])
    p95 = float(grid[int(np.searchsorted(cum, 0.95))])
    mode = float(grid[int(np.argmax(last))])
    print(f"    平均成本AVG = {avg:.2f}")
    print(f"    众数价位     = {mode:.2f}")
    print(f"    90%区间      = [{p5:.2f}, {p95:.2f}]  宽度 {p95-p5:.2f}")
    print(f"    集中度       = {(p95-p5)/avg*100:.2f}%  (越小越集中)")

    # 获利盘比例（最后一根收盘价之上的筹码占比）
    close = float(rows[-1][4])
    profit = float(p[grid > close].sum())
    print(f"    收盘价       = {close:.2f}")
    print(f"    获利盘比例   = {profit*100:.2f}%   套牢盘 = {(1-profit)*100:.2f}%")

    print('\n  筹码分布密度 Top10 价位段:')
    order = np.argsort(last)[::-1][:10]
    for i in sorted(order, key=lambda k: grid[k]):
        print(f"    {grid[i]:>7.2f} 元  {last[i]/total*100:>6.3f}%  "
              f"{'#' * int(last[i]/total*300)}")

    if args.save:
        c = pymysql.connect(host='127.0.0.1', port=3306, user='root',
                            password='dba126543', database=args.db, charset='utf8mb4')
        cur = c.cursor()
        cur.execute(f"""CREATE TABLE IF NOT EXISTS chip_distribution (
            code VARCHAR(16) NOT NULL,
            trade_date DATE NOT NULL,
            price DECIMAL(12,4) NOT NULL,
            chips DECIMAL(24,4) NOT NULL DEFAULT 0,
            PRIMARY KEY (code, trade_date, price)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        ins = "INSERT INTO chip_distribution (code, trade_date, price, chips) " \
              "VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE chips=VALUES(chips)"
        payload = []
        last_date = meta['last']
        for price, chips in zip(grid, last):
            if chips > 0:
                payload.append((args.code, last_date, float(price), float(chips)))
        cur.executemany(ins, payload)
        c.commit()
        cur.execute("SELECT COUNT(*), MIN(price), MAX(price) "
                    "FROM chip_distribution WHERE code=%s AND trade_date=%s",
                    (args.code, last_date))
        print(f"\n  已写入 chip_distribution: {cur.fetchone()}")
        c.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())