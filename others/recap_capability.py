"""用**修好后**的解析器重测各周期与本地的差异倍率。

为什么必须重测：2026-10-05 第一版能力矩阵是在解析器有 bug 的情况下测的
（extra 长度写死成 4，指数正确、个股越界）。「非日线 vol 恒差 100 倍」
这个结论当时对 sh000001 成立，但结论的来源不可靠，必须重测。

对每个标的 × 每个 category：
  - 拉 N 根，与本地 .day 的**同期**数据按日期对齐
  - 逐根算 vol / amount / OHLC 的比值
  - 报告比值是否恒定、偏离多少

注意：非日线的 trade_date 是「周期最后一个交易日」，与日线能对上；
周/月/季/年的最后一根可能是未走完的周期，只作参考不作结论。

用法::

    .\\.venv\\Scripts\\python.exe -W ignore tools\\recap_capability.py
    .\\.venv\\Scripts\\python.exe -W ignore tools\\recap_capability.py --codes sh600570 sh000001
"""
import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pytdx.params import TDXParams  # noqa: E402

from server import config  # noqa: E402
from server.external import tdx_kline, tdx_local  # noqa: E402

PERIODS = [
    ("day", TDXParams.KLINE_TYPE_DAILY),
    ("week", TDXParams.KLINE_TYPE_WEEKLY),
    ("month", TDXParams.KLINE_TYPE_MONTHLY),
    ("quarter", TDXParams.KLINE_TYPE_3MONTH),
    ("year", TDXParams.KLINE_TYPE_YEARLY),
]


def ratio_report(name: str, pairs: list[tuple[float, float]]) -> str:
    """pairs = [(远端值, 本地值)]，输出比值的中位数与离散度。"""
    rs = []
    for remote, local in pairs:
        if local:
            rs.append(remote / local)
    if not rs:
        return f"{name:<8} 无可比数据"
    med = statistics.median(rs)
    spread = (max(rs) - min(rs)) / med * 100 if med else float("inf")
    return f"{name:<8} 中位倍数={med:>10.4f}  离散={spread:>6.2f}%  (n={len(rs)})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", nargs="*", default=["sh000001", "sh600570", "sh600519", "sz000001"])
    ap.add_argument("--count", type=int, default=24)
    args = ap.parse_args()

    for code in args.codes:
        market_id, raw = tdx_kline.split_code(code)
        try:
            local = {r["trade_date"]: r for r in tdx_local.read_day(code)}
        except Exception as e:
            print(f"\n### {code}: 本地 .day 不可用（{e}）")
            continue
        print(f"\n### {code}  （本地 {len(local)} 根，"
              f"{min(local)} ~ {max(local)}）")

        for period, category in PERIODS:
            try:
                rows = tdx_kline.fetch_bars(code, period, days=args.count)
            except Exception as e:
                print(f"  {period:<8} 拉取失败: {type(e).__name__}: {e}")
                continue
            if not rows:
                print(f"  {period:<8} 返回空")
                continue

            aligned = [r for r in rows if r["trade_date"] in local]
            head = f"  {period:<8} {len(rows):>4} 根 {rows[0]['trade_date']}~{rows[-1]['trade_date']}"
            if not aligned:
                print(f"{head}  与本地日期无交集（无法比对）")
                continue

            # 非日线要按周期聚合本地日线再比，而不是直接拿单日对
            if period != "day":
                agg = _aggregate_local(local, aligned, period)
            else:
                agg = [local[r["trade_date"]] for r in aligned]

            print(f"{head}  可比 {len(aligned)} 根")
            for field in ("open", "close", "volume", "amount"):
                pairs = [(r[field], a[field]) for r, a in zip(aligned, agg)]
                print("     " + ratio_report(field, pairs))

    return 0


def _aggregate_local(local: dict, aligned: list[dict], period: str):
    """把本地日线按 period 聚合成与远端同口径的记录。"""
    import datetime
    from collections import defaultdict

    def key(d: str) -> str:
        dt = datetime.date.fromisoformat(d)
        if period == "week":
            y, w, _ = dt.isocalendar()
            return f"{y:04d}-W{w:02d}"
        if period == "month":
            return f"{dt.year:04d}-{dt.month:02d}"
        if period == "quarter":
            return f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"
        if period == "year":
            return str(dt.year)
        return d

    groups: dict[str, list] = defaultdict(list)
    for r in aligned:
        groups[key(r["trade_date"])].append(local[r["trade_date"]])
    out = []
    for _k, rows in groups.items():
        rows.sort(key=lambda x: x["trade_date"])
        out.append({
            "open": rows[0]["open"], "close": rows[-1]["close"],
            "high": max(x["high"] for x in rows), "low": min(x["low"] for x in rows),
            "volume": sum(x["volume"] for x in rows),
            "amount": sum(x["amount"] for x in rows),
        })
    return out


if __name__ == "__main__":
    raise SystemExit(main())
