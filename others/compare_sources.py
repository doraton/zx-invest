"""两个数据源的逐根对照：pytdx 库 vs 腾讯库。

对照实验保持严格控制变量——两个库 schema 相同、写入代码相同、派生周期由
**同一个** ``kline_repo.materialize_periods()`` 算出，唯一差别是 K 线的来源。

比较维度：
  1. 逐根对齐（按 ``trade_date`` join）后的行数差异
  2. open/high/low/close 绝对偏差，逐根统计超阈值条数
  3. volume 偏差（注意两边都已归一到「股」）
  4. amount 覆盖情况（腾讯 K 线不提供成交额，这一项预期为 0）
  5. 只在单边存在的交易日（停牌/缺口）

用法::

    .\\.venv\\Scripts\\python.exe -W ignore tools\\compare_sources.py sh600570
    .\\.venv\\Scripts\\python.exe -W ignore tools\\compare_sources.py sh600570 --limit 10
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import aiomysql  # noqa: E402

from server import config  # noqa: E402
from server.external.tdx_kline import (  # noqa: E402
    PERIOD_DAY, PERIOD_MONTH, PERIOD_QUARTER, PERIOD_WEEK, PERIOD_YEAR,
)

ALL_PERIODS = [PERIOD_DAY, PERIOD_WEEK, PERIOD_MONTH, PERIOD_QUARTER, PERIOD_YEAR]
PRICE_TOL = 0.005      # 半分钱；A股最小变动单位
VOL_TOL_PCT = 0.01     # 相对差 1% 以内算「取整级」——腾讯按整手存、TDX 精确到股，
                       # 单根最大就有 <99 股的固有误差，必须按相对量级判否则全是噪声
VOL_MATERIAL_PCT = 0.01  # 超过这个相对差才算「实质分歧」


async def connect(db_name: str):
    return await aiomysql.connect(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT,
        user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
        db=db_name, autocommit=True, charset="utf8mb4",
    )


async def load(conn, code: str, period: str) -> dict:
    async with conn.cursor(aiomysql.DictCursor) as cur:
        await cur.execute(
            "SELECT trade_date, open, high, low, close, volume, amount "
            f"FROM kline_{period} WHERE code=%s ORDER BY trade_date", (code,))
        return {r["trade_date"].isoformat(): r for r in await cur.fetchall()}


def fmt(v) -> float:
    return float(v) if v is not None else 0.0


async def main() -> int:
    ap = argparse.ArgumentParser(description="pytdx 库 vs 腾讯库 逐根对照")
    ap.add_argument("code", nargs="?", default="sh600570")
    ap.add_argument("--left", default="mysqldb", help="pytdx 侧库名")
    ap.add_argument("--right", default="tq-stock", help="腾讯侧库名")
    ap.add_argument("--limit", type=int, default=5, help="每周期最多列出几条差异样例")
    args = ap.parse_args()

    lc = await connect(args.left)
    rc = await connect(args.right)
    try:
        print(f"对照：{args.left}（pytdx） ←→ {args.right}（腾讯）  标的 {args.code}\n")
        grand_bad = 0
        for period in ALL_PERIODS:
            L, R = await load(lc, args.code, period), await load(rc, args.code, period)
            if not L and not R:
                print(f"[{period:<8}] 两边都为空")
                continue
            common = sorted(set(L) & set(R))
            only_l, only_r = sorted(set(L) - set(R)), sorted(set(R) - set(L))

            worst = {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0}
            bad_price = []
            vol_buckets = {"完全相等": 0, "取整级(<1%)": 0, "可察觉(1~10%)": 0, "实质(>=10%)": 0}
            bad_vol = []
            worst_amt = 0.0
            amt_a, amt_b = 0, 0

            for d in common:
                a, b = L[d], R[d]
                row_bad = False
                for f in ("open", "high", "low", "close"):
                    diff = abs(fmt(a[f]) - fmt(b[f]))
                    worst[f] = max(worst[f], diff)
                    if diff > PRICE_TOL:
                        row_bad = True
                if row_bad:
                    bad_price.append(d)
                va, vb = fmt(a["volume"]), fmt(b["volume"])
                rel = abs(va - vb) / va if va else 0.0
                if rel == 0:
                    vol_buckets["完全相等"] += 1
                elif rel < VOL_TOL_PCT:
                    vol_buckets["取整级(<1%)"] += 1
                elif rel < 0.10:
                    vol_buckets["可察觉(1~10%)"] += 1
                else:
                    vol_buckets["实质(>=10%)"] += 1
                if rel >= VOL_MATERIAL_PCT:
                    bad_vol.append(d)
                aa, ba = fmt(a["amount"]), fmt(b["amount"])
                if aa:
                    amt_a += 1
                    worst_amt = max(worst_amt, abs(aa - ba))
                if ba:
                    amt_b += 1

            n_bad = len(bad_price) + len(bad_vol)
            grand_bad += n_bad
            status = "✅ 全一致" if n_bad == 0 and not only_l and not only_r else "⚠️ 有差异"
            print(f"[{period:<8}] {status}  可比 {len(common)} 根")
            print(f"            仅 {args.left} 有: {len(only_l)}   仅 {args.right} 有: {len(only_r)}")
            print(f"            价格最大偏差 O{worst['open']:.4f} H{worst['high']:.4f} "
                  f"L{worst['low']:.4f} C{worst['close']:.4f}  超阈值 {len(bad_price)} 根")
            print(f"            成交量分档  " + "  ".join(f"{k}:{v}" for k, v in vol_buckets.items())
                  + f"   → 实质分歧 {len(bad_vol)} 根")
            print(f"            成交额：{args.left} 有值 {amt_a} 根，{args.right} 有值 {amt_b} 根"
                  f"（最大差 {worst_amt:,.0f}）")

            for d in (bad_price + bad_vol)[:args.limit]:
                a, b = L[d], R[d]
                print(f"              {d}  {args.left} "
                      f"{fmt(a['open']):.2f}/{fmt(a['high']):.2f}/{fmt(a['low']):.2f}/{fmt(a['close']):.2f}"
                      f"   {args.right} "
                      f"{fmt(b['open']):.2f}/{fmt(b['high']):.2f}/{fmt(b['low']):.2f}/{fmt(b['close']):.2f}")
            if only_l:
                print(f"              仅左有: {only_l[:args.limit]}")
            if only_r:
                print(f"              仅右有: {only_r[:args.limit]}")
            print()

        print("=" * 60)
        print("总体差异条数:", grand_bad)
    finally:
        lc.close()
        rc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
