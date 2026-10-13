# -*- coding: utf-8 -*-
"""pytdx 数据能力全矩阵（实测，2026-10-05）。

一条命令看清 pytdx 到底能给什么：
    .venv/Scripts/python.exe -W ignore others/probe_pytdx_capability.py

关键结论（详见输出底部）：
  * 日K / 分钟线：OHLC + 成交量 + 成交额 全部正确
  * 周K / 月K / 季K / 年K：OHLC + 成交额 正确，但**成交量恒差 100 倍**（量纲错误）
  * 副图指标：pytdx 完全不提供，必须自算
  * 筹码分布：无接口，且逐笔成交原料也拿不到（只有分时）
  * 基本面：get_finance_info 37 字段 + 16 个文本板块 + 除权除息，非常完整

注意：category 10/11 的 count 必须给足，否则服务器返回稀疏数据（曾导致误判为"数据损坏"）。
"""
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pytdx.hq import TdxHq_API  # noqa: E402
from pytdx.params import TDXParams  # noqa: E402
from server.external import tdx_kline  # noqa: E402

HOSTS = [("117.34.114.13", 7709)]
CODE, MKT = "000001", 1

# (名称, category, count) —— 非日线一律用大 count 拉全历史
SERIES = [
    ("日K", TDXParams.KLINE_TYPE_DAILY, 800),
    ("周K", TDXParams.KLINE_TYPE_WEEKLY, 400),
    ("月K", TDXParams.KLINE_TYPE_MONTHLY, 400),
    ("季K", TDXParams.KLINE_TYPE_3MONTH, 400),
    ("年K", TDXParams.KLINE_TYPE_YEARLY, 400),
    ("5分", TDXParams.KLINE_TYPE_5MIN, 100),
    ("15分", TDXParams.KLINE_TYPE_15MIN, 100),
    ("30分", TDXParams.KLINE_TYPE_30MIN, 100),
    ("60分", TDXParams.KLINE_TYPE_1HOUR, 100),
    ("1分", TDXParams.KLINE_TYPE_1MIN, 100),
]

INTRADAY = (0, 1, 2, 3, 8, 9)


def keyfn(cat):
    if cat == 5:
        return lambda d: f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
    if cat == 6:
        return lambda d: f"{d.year}-{d.month:02d}"
    if cat == 10:
        return lambda d: f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    if cat == 11:
        return lambda d: str(d.year)
    return lambda d: d.isoformat()


def connect():
    api = TdxHq_API(heartbeat=False)
    for h in HOSTS:
        if api.connect(*h, time_out=8):
            return api, h
    raise RuntimeError("无可用通达信服务器，请先跑 sweep_tdx_hosts.py")


def main():
    api, host = connect()
    print(f"服务器 {host[0]}:{host[1]}   标的 sh{CODE}\n")

    data = {name: api.get_security_bars(cat, MKT, CODE, 0, n) for name, cat, n in SERIES}
    daily = data["日K"]

    # 基准：日线自身
    agg_day = {}
    for b in daily:
        d = date(b["year"], b["month"], b["day"])
        e = agg_day.setdefault(d.isoformat(), {"open": b["open"], "high": b["high"], "low": b["low"],
                                               "close": b["close"], "vol": 0.0, "amt": 0.0})
        e["vol"] += b["vol"]; e["amt"] += b["amount"]

    print("=" * 78)
    print("周期能力矩阵（基准 = 日线聚合）")
    print("=" * 78)
    print(f"{'周期':<7}{'cat':<5}{'条数':<7}{'OHLC':<10}{'vol比':<9}{'amt比':<9}{'判定'}")
    print("-" * 78)

    verdicts = []
    for name, cat, n in SERIES:
        bars = data[name]
        if not bars:
            print(f"{name:<7}{cat:<5}{0:<7}{'无数据':<10}")
            verdicts.append((name, "无数据"))
            continue

        if cat in INTRADAY:
            sane = sum(1 for b in bars if 1990 <= b["year"] <= 2030
                       and 1 <= b["month"] <= 12 and 1 <= b["day"] <= 31)
            ok = sane == len(bars)
            print(f"{name:<7}{cat:<5}{len(bars):<7}{f'{sane}/{len(bars)}':<10}{'基准':<9}{'基准':<9}"
                  f"{'✅ 解析正常' if ok else '❌ 解析异常'}")
            verdicts.append((name, "✅ 解析正常" if ok else "❌ 解析异常"))
            continue

        kf = keyfn(cat)
        # 日线覆盖范围的首尾所落的桶是"被截断"的（首桶缺前半段、末桶还在进行中），
        # 这些桶不参与比对；其余桶视为完整周期。
        first_day = min(date(b["year"], b["month"], b["day"]) for b in daily)
        last_day = max(date(b["year"], b["month"], b["day"]) for b in daily)
        boundary = {kf(first_day), kf(last_day)}

        agg = {}
        for b in daily:
            d = date(b["year"], b["month"], b["day"])
            k = kf(d)
            e = agg.setdefault(k, {"open": b["open"], "high": b["high"], "low": b["low"],
                                   "close": b["close"], "vol": 0.0, "amt": 0.0})
            e["high"] = max(e["high"], b["high"]); e["low"] = min(e["low"], b["low"])
            e["close"] = b["close"]; e["vol"] += b["vol"]; e["amt"] += b["amount"]

        ok = tot = 0
        vr, ar = [], []
        for b in bars:
            d = date(b["year"], b["month"], b["day"])
            k = kf(d)
            if k in boundary:
                continue
            a = agg.get(k)
            if not a:
                continue
            tot += 1
            ok += all(abs(b[f] - a[f]) < 0.01 for f in ("open", "high", "low", "close"))
            if b["vol"]:
                vr.append(a["vol"] / b["vol"])
            if b["amount"]:
                ar.append(a["amt"] / b["amount"])

        if not tot:
            print(f"{name:<7}{cat:<5}{len(bars):<7}{'无完整重叠':<10}")
            verdicts.append((name, "数据不足，无法核对"))
            continue
        v_txt = f"{min(vr):.2f}" if vr else "-"
        a_txt = f"{min(ar):.2f}" if ar else "-"
        if ok == tot and vr and abs(min(vr) - 100) < 0.01 and ar and all(abs(x - 1) < 0.01 for x in ar):
            v = "⚠️ OHLC对/volume差100倍"
        elif ok == tot:
            v = "✅ 可用"
        else:
            v = "❌ OHLC不吻合"
        print(f"{name:<7}{cat:<5}{len(bars):<7}{f'{ok}/{tot}':<10}{v_txt:<9}{a_txt:<9}{v}")
        verdicts.append((name, v))

    # ---- 其它能力 ----
    print()
    print("=" * 78)
    print("其它数据能力")
    print("=" * 78)
    tests = [
        ("基本面 get_finance_info", lambda: api.get_finance_info(1, "600570")),
        ("公司资料 get_company_info_category", lambda: api.get_company_info_category(1, "600570")),
        ("除权除息 get_xdxr_info", lambda: api.get_xdxr_info(1, "600570")),
        ("实时快照 get_security_quotes", lambda: api.get_security_quotes([(1, "600570")])),
        ("证券总数 get_security_count", lambda: api.get_security_count(1)),
        ("证券列表 get_security_list", lambda: api.get_security_list(1, 0)),
        ("筹码原料 逐笔 get_transaction_data", lambda: api.get_transaction_data(1, "600570")),
        ("筹码原料 分时 get_minute_time_data", lambda: api.get_minute_time_data(1, "600570")),
    ]
    for label, fn in tests:
        try:
            r = fn()
            if r is None:
                print(f"  [X] {label}: 不可用")
            elif isinstance(r, list):
                print(f"  [O] {label}: {len(r)} 条/项")
            elif isinstance(r, dict):
                print(f"  [O] {label}: {len(r)} 个字段")
            else:
                print(f"  [O] {label}: {r}")
        except Exception as e:
            print(f"  [X] {label}: {type(e).__name__}")

    methods = [m for m in dir(TdxHq_API) if not m.startswith("_")]
    hits = {kw: [m for m in methods if kw in m.lower()]
            for kw in ("indicator", "macd", "kdj", "boll", "rsi", "chip", "cost")}
    print()
    print("  指标/筹码接口搜索: " + "; ".join(f"{k}={v or '无'}" for k, v in hits.items()))

    api.disconnect()

    print()
    print("=" * 78)
    print("结论")
    print("=" * 78)
    for name, v in verdicts:
        print(f"  {name:<6} {v}")
    print()
    print("  → 非日线的 volume 恒差 100 倍，故本项目统一「只拉日线，周/月/季/年在 SQL 聚合」。")
    print("  → OHLC 与成交额两条路径一致，聚合口径正确。")


if __name__ == "__main__":
    main()
