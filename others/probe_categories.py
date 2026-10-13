"""直接按 category 探测各周期是否可取（绕过 fetch_bars 的周期白名单）。

设计上是「只拉日线 + SQL 物化周/月/季/年」，所以 fetch_bars 只开放 day/week/month。
但能力矩阵需要确认 category 10(季) / 11(年) 到底能不能取到数据，
以及非日线 vol 的单位到底是「手」还是「股」。

用法::

    .\\.venv\\Scripts\\python.exe -W ignore tools\\probe_categories.py sh600570
"""
import argparse
import struct
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pytdx.hq import TdxHq_API  # noqa: E402
from pytdx.params import TDXParams  # noqa: E402

from server import config  # noqa: E402
from server.external import tdx_kline, tdx_local  # noqa: E402

CATS = [
    (0, "5分"), (1, "15分"), (2, "30分"), (3, "60分"),
    (4, "日K"), (5, "周K"), (6, "月K"), (8, "1分"), (10, "季K"), (11, "年K"),
]

BUCKET = {
    "周K": lambda d: f"{d.isocalendar()[0]:04d}-W{d.isocalendar()[1]:02d}",
    "月K": lambda d: f"{d.year:04d}-{d.month:02d}",
    "季K": lambda d: f"{d.year}-Q{(d.month - 1) // 3 + 1}",
    "年K": lambda d: str(d.year),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("code", nargs="?", default="sh600570")
    ap.add_argument("--count", type=int, default=24)
    args = ap.parse_args()

    market_id, raw = tdx_kline.split_code(args.code)
    local = defaultdict(list)
    for r in tdx_local.read_day(args.code):
        local[r["trade_date"]].append(r)

    api = TdxHq_API(heartbeat=False, raise_exception=True)
    api.connect(config.TDX_HOSTS[0][0], config.TDX_HOSTS[0][1],
                time_out=config.TDX_CONNECT_TIMEOUT)

    for cat, name in CATS:
        try:
            bars = api.get_security_bars(cat, market_id, raw, 0, args.count)
        except Exception as e:
            print(f"category={cat:>2} {name:<4} 失败: {type(e).__name__}: {e}")
            continue
        if not bars:
            print(f"category={cat:>2} {name:<4} 返回空")
            continue
        first, last = bars[0]["datetime"][:10], bars[-1]["datetime"][:10]
        line = f"category={cat:>2} {name:<4} {len(bars):>3} 根 {first}~{last}"

        if name not in BUCKET:
            print(line + f"  (末根 vol={bars[-1]['vol']:.0f})")
            continue

        # 非日线：把本地日线按同口径分桶，逐根比对
        buckets = defaultdict(list)
        for r in local.values() if isinstance(local, dict) else ():
            pass
        allrows = [x for v in local.values() for x in v]
        for r in allrows:
            buckets[BUCKET[name](date.fromisoformat(r["trade_date"]))].append(r)

        checked = vol_ratios = 0
        vol_ratios = []
        ohlc_hits = 0
        amt_ratios = []
        for b in bars:
            d = b["datetime"][:10]
            key = BUCKET[name](date.fromisoformat(d))
            rows = buckets.get(key)
            if not rows:
                continue
            rows.sort(key=lambda x: x["trade_date"])
            checked += 1
            o = rows[0]["open"]; c = rows[-1]["close"]
            h = max(x["high"] for x in rows); l = min(x["low"] for x in rows)
            sv = sum(x["volume"] for x in rows); sa = sum(x["amount"] for x in rows)
            if (abs(b["open"] - o) < 0.02 and abs(b["high"] - h) < 0.02
                    and abs(b["low"] - l) < 0.02 and abs(b["close"] - c) < 0.02):
                ohlc_hits += 1
            if sv:
                vol_ratios.append(b["vol"] / sv)
            if sa:
                amt_ratios.append(b["amount"] / sa)
        vr = f"{sum(vol_ratios) / len(vol_ratios):.4f}" if vol_ratios else "n/a"
        ar = f"{sum(amt_ratios) / len(amt_ratios):.4f}" if amt_ratios else "n/a"
        print(f"{line}  可比 {checked} 根 | OHLC一致 {ohlc_hits}/{checked} "
              f"| vol倍数 {vr} | amt倍数 {ar}")

    api.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
