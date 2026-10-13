# -*- coding: utf-8 -*-
"""独立验收：直接把 MySQL 里的 30 行与本地 .day 真值逐根逐字段比对。

不经同步代码，直接查库，确保「落库的数据」本身正确。
"""
import asyncio
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import aiomysql  # noqa: E402
from server import config  # noqa: E402

DAY = config.TDX_VIPDOC / "sh" / "lday" / "sh000001.day"
TOL_PRICE = 0.01      # 价格允许 1 分
TOL_VOL = 1e-6        # 成交量按比例


def read_truth():
    raw = DAY.read_bytes()
    out = {}
    for i in range(len(raw) // 32):
        d, o, h, l, c, amt, vol, _ = struct.unpack("<IIIIIfII", raw[i * 32:(i + 1) * 32])
        out[str(d)] = dict(open=o / 100.0, high=h / 100.0, low=l / 100.0,
                           close=c / 100.0, amount=float(amt), volume=float(vol))
    return out


async def main():
    truth = read_truth()
    conn = await aiomysql.connect(charset="utf8mb4", **config.dsn())
    async with conn.cursor(aiomysql.DictCursor) as cur:
        await cur.execute("""SELECT trade_date, open, high, low, close, volume, amount
                             FROM kline_day WHERE code='sh000001'
                             ORDER BY trade_date""")
        rows = await cur.fetchall()
    conn.close()

    print(f"库内 {len(rows)} 行 | 本地真值 {len(truth)} 根\n")
    ok = miss = 0
    diffs = []
    for r in rows:
        # 库里是 DATE 字段（2026-08-19），真值 key 是 .day 里的 YYYYMMDD（20260819）
        d = r["trade_date"]
        d = d.isoformat() if hasattr(d, "isoformat") else str(d)
        t = truth.get(d.replace("-", ""))
        if not t:
            miss += 1
            diffs.append(f"  {d}: 真值无此交易日")
            continue
        errs = []
        for f in ("open", "high", "low", "close"):
            if abs(float(r[f]) - t[f]) > TOL_PRICE:
                errs.append(f"{f} 库={float(r[f])} 真值={t[f]}")
        if abs(float(r["amount"]) - t["amount"]) > 1:
            errs.append(f"amount 库={float(r['amount'])} 真值={t['amount']}")
        if abs(float(r["volume"]) - t["volume"]) / max(t["volume"], 1) > 1e-6:
            errs.append(f"volume 库={float(r['volume'])} 真值={t['volume']}")
        if errs:
            diffs.append(f"  {d}: " + "; ".join(errs))
        else:
            ok += 1

    print(f"逐字段一致: {ok}/{len(rows)}   真值缺失: {miss}")
    if diffs:
        print("\n差异明细:")
        for d in diffs:
            print(d)
    else:
        print("\n全部一致 —— 落库 OHLC / amount / volume 与本地通达信文件完全吻合。")

    # 反向：真值有但库里没有的日期
    db_dates = {(r["trade_date"].isoformat() if hasattr(r["trade_date"], "isoformat")
                 else str(r["trade_date"])).replace("-", "") for r in rows}
    extra = sorted(set(truth) - db_dates)
    print(f"\n真值中存在但库里缺失的日期（仅最近一个月内）: "
          f"{[d for d in extra if d >= '20260801'] or '无'}")


if __name__ == "__main__":
    asyncio.run(main())
