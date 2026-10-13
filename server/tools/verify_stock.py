"""端到端验收：库里落的数据 vs 本地通达信 .day 真值。

覆盖三件事：
1. **日线**：逐根逐字段比对 OHLCV（本地 .day 覆盖到的每一根）
2. **派生周期**：由日线在 Python 侧重算周/月/季/年，与库内物化的行逐根比对
3. **一致性**：high >= max(open, close)、low <= min(open, close)、volume/amount >= 0

用法::

    .\\.venv\\Scripts\\python.exe -W ignore tools\\verify_stock.py sh600570
"""
import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import aiomysql  # noqa: E402

from server import db, kline_repo  # noqa: E402
from server.external import tdx_local  # noqa: E402

# 见 verify_against_local.py：指数存在 1 tick 舍入差，个股为 0
PRICE_TOL = 0.011
VOL_TOL = 200.0
AMT_TOL = 5000.0


def bucket_key(d: date, period: str) -> str:
    """与 kline_repo.PERIOD_BUCKET_SQL 口径一致的 Python 侧实现。"""
    if period == "week":
        y, w, _ = d.isocalendar()
        return f"{y:04d}-W{w:02d}"
    if period == "month":
        return f"{d.year:04d}-{d.month:02d}"
    if period == "quarter":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    if period == "year":
        return str(d.year)
    raise ValueError(period)


def reaggregate(day_rows: list[dict], period: str) -> list[dict]:
    """从日线重算一个派生周期。OHLC = 首开/高/低/末收，量额求和。

    ``trade_date`` 统一返回 ``YYYY-MM-DD`` 字符串，与 DB 取出的形态一致，
    否则比对时会因为 date 对象 vs 字符串而全部误判为「缺失」。
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in day_rows:
        d = r["trade_date"]
        d = date.fromisoformat(d) if isinstance(d, str) else d
        groups[bucket_key(d, period)].append(r)
    out = []
    for _key, rows in groups.items():
        rows.sort(key=lambda r: r["trade_date"])
        out.append({
            "trade_date": (rows[-1]["trade_date"].isoformat()
                           if hasattr(rows[-1]["trade_date"], "isoformat")
                           else str(rows[-1]["trade_date"])),
            "open": float(rows[0]["open"]),
            "high": max(float(x["high"]) for x in rows),
            "low": min(float(x["low"]) for x in rows),
            "close": float(rows[-1]["close"]),
            "volume": sum(float(x["volume"]) for x in rows),
            "amount": sum(float(x["amount"]) for x in rows),
            "days": len(rows),
        })
    out.sort(key=lambda r: r["trade_date"])
    return out


async def fetch_all(cur, code: str, period: str) -> list[dict]:
    # 表名走 db.kline_table，不要拼 kline_{period}——派生周期已合并成
    # kline_period 一张表，且必须按 period 列收窄，否则读到全部四个周期。
    extra, extra_params = kline_repo.period_filter(period)
    await cur.execute(
        "SELECT trade_date, open, high, low, close, volume, amount "
        f"FROM {db.kline_table(period)} WHERE code=%s{extra} ORDER BY trade_date",
        [code, *extra_params])
    rows = await cur.fetchall()
    for r in rows:
        r["trade_date"] = r["trade_date"].isoformat() if hasattr(r["trade_date"], "isoformat") \
            else str(r["trade_date"])
    return rows


async def main_async(code: str) -> int:
    await db.init_pool()
    failures = 0

    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # ---------- 1. 日线 vs 本地 .day ----------
            days = await fetch_all(cur, code, "day")
            try:
                local = {r["trade_date"]: r for r in tdx_local.read_day(code)}
            except FileNotFoundError as e:
                print(f"[日线] 本地 .day 缺失，跳过比对（{e}）")
                local = {}

            if local:
                common = [r for r in days if r["trade_date"] in local]
                bad = defaultdict(list)
                for r in common:
                    t = local[r["trade_date"]]
                    for k, tol in (("open", PRICE_TOL), ("high", PRICE_TOL),
                                   ("low", PRICE_TOL), ("close", PRICE_TOL),
                                   ("volume", VOL_TOL), ("amount", AMT_TOL)):
                        if abs(float(r[k]) - float(t[k])) > tol:
                            bad[k].append(r["trade_date"])
                status = "一致" if not bad else f"差异 {dict((k, len(v)) for k, v in bad.items())}"
                print(f"[日线] 库内 {len(days)} 根，本地可比 {len(common)} 根 -> {status}")
                for k, v in bad.items():
                    print(f"        {k} 不一致: {v[:5]}{' ...' if len(v) > 5 else ''}")
                failures += len(bad)

            # ---------- 2. 派生周期 vs Python 重算 ----------
            day_dates = [date.fromisoformat(r["trade_date"]) for r in days]
            for period in kline_repo.DERIVED_PERIODS:
                stored = await fetch_all(cur, code, period)
                if not stored:
                    print(f"[{period:<7}] 库内无数据")
                    failures += 1
                    continue
                expect = reaggregate(
                    [{"trade_date": date.fromisoformat(r["trade_date"]),
                      "open": r["open"], "high": r["high"], "low": r["low"],
                      "close": r["close"], "volume": r["volume"], "amount": r["amount"]}
                     for r in days], period)
                smap = {r["trade_date"]: r for r in stored}
                diffs = []
                for e in expect:
                    s = smap.get(e["trade_date"])
                    if s is None:
                        diffs.append(f"{e['trade_date']} 缺失")
                        continue
                    for k, tol in (("open", PRICE_TOL), ("high", PRICE_TOL),
                                   ("low", PRICE_TOL), ("close", PRICE_TOL),
                                   ("volume", VOL_TOL), ("amount", AMT_TOL)):
                        if abs(float(s[k]) - e[k]) > tol:
                            diffs.append(f"{e['trade_date']}.{k} {s[k]}!={e[k]}")
                extra = set(smap) - {e["trade_date"] for e in expect}
                if extra:
                    diffs.append(f"{len(extra)} 根多余: {sorted(extra)[:3]}")
                print(f"[{period:<7}] 库内 {len(stored)} 根 vs 重算 {len(expect)} 根 -> "
                      + ("全部一致" if not diffs else f"{len(diffs)} 处差异"))
                for d in diffs[:5]:
                    print(f"        {d}")
                failures += len(diffs)

                # 抽样打印
                print(f"         末 3 根: " + " | ".join(
                    f"{r['trade_date']} O{float(r['open']):.2f} H{float(r['high']):.2f} "
                    f"L{float(r['low']):.2f} C{float(r['close']):.2f}"
                    for r in stored[-3:]))

            # ---------- 3. 内部一致性 ----------
            viol = 0
            for r in days:
                o, h, l, c = (float(r["open"]), float(r["high"]),
                              float(r["low"]), float(r["close"]))
                if h < max(o, c) or l > min(o, c) or h < l:
                    viol += 1
            print(f"[一致性] 日线 OHLC 关系违例 {viol} 条"
                  f"（{len(days)} 根 "
                  f"{day_dates[0] if day_dates else '-'} ~ "
                  f"{day_dates[-1] if day_dates else '-'}）")
            failures += viol

            # ---------- 4. 基本面 / 除权除息 ----------
            for table, key in (("stock", "code"), ("stock_finance", "code"),
                               ("stock_xdxr", "code")):
                await cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {key}=%s", (code,))
                n = (await cur.fetchone())["n"]
                print(f"[{table}] {n} 行")

    await db.close_pool()
    print(f"\n验收结果: {'全部通过' if failures == 0 else f'{failures} 项未通过'}")
    return 0 if failures == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("code", nargs="?", default="sh600570")
    args = ap.parse_args()
    return asyncio.run(main_async(args.code))


if __name__ == "__main__":
    raise SystemExit(main())
