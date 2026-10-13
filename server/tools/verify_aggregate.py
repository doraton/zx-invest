# -*- coding: utf-8 -*-
"""校验周/月 K 聚合结果是否与日线手工聚合一致（OHLC + 量额逐根比对）。"""
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server import db, market  # noqa: E402


def iso_week(d: date) -> str:
    """与 MySQL ``%x-%v`` 保持一致（isocalendar 返回 (year, week, weekday)）。"""
    y, w, _ = d.isocalendar()
    return f"{y}-{w:02d}"


def fold(bars, key):
    out = {}
    for b in bars:
        k = key(b["datetime"])
        e = out.setdefault(k, {"open": b["open"], "high": b["high"], "low": b["low"],
                               "close": b["close"], "vol": 0.0, "amt": 0.0})
        e["high"] = max(e["high"], b["high"])
        e["low"] = min(e["low"], b["low"])
        e["close"] = b["close"]
        e["vol"] += b["amount"]
        e["amt"] += b["turnover"]
    return out


async def compare(name, actual, expected, keys):
    bad = 0
    for b in actual:
        e = expected.get(keys(b["datetime"]))
        if not e:
            bad += 1
            print(f"  [{name}] {b['datetime']} 期望集中不存在")
            continue
        for f in ("open", "high", "low", "close"):
            if abs(b[f] - e[f]) > 0.01:
                bad += 1
                print(f"  [{name}] {b['datetime']} {f} 不符 实际={b[f]} 期望={e[f]}")
        if abs(b["amount"] - e["vol"]) > 1:
            bad += 1
            print(f"  [{name}] {b['datetime']} 量不符 实际={b['amount']} 期望={e['vol']}")
        if abs(b["turnover"] - e["amt"]) > 1:
            bad += 1
            print(f"  [{name}] {b['datetime']} 额不符 实际={b['turnover']} 期望={e['amt']}")
    print(f"  [{name}] 返回 {len(actual)} 根 / 期望 {len(expected)} 组 -> "
          + ("全部通过" if bad == 0 else f"{bad} 处不符"))
    return bad


async def main():
    await db.init_pool()
    day, _ = await market.query_klines("sh000001", "day", 5000)
    print(f"日线 {len(day)} 根: {day[0]['datetime']} ~ {day[-1]['datetime']}\n")

    bad = 0
    wk, _ = await market.query_klines("sh000001", "week", 5000)
    bad += await compare("周K", wk, fold(day, lambda s: iso_week(date.fromisoformat(s))),
                         lambda s: iso_week(date.fromisoformat(s)))

    mo, _ = await market.query_klines("sh000001", "month", 5000)
    bad += await compare("月K", mo, fold(day, lambda s: s[:7]), lambda s: s[:7])

    print()
    print("汇总:", "周/月 K 聚合全部正确" if bad == 0 else f"{bad} 处不符")
    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
