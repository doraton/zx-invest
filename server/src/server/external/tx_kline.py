"""腾讯 K 线源（架构文档 §3 指定的 HTTP fallback）。

这是与 ``external/tdx_kline.py`` **完全对等**的第二个数据源：两者对外暴露同样的
``fetch_history()``、产出同样的记录字典，让「数据源」成为对照实验中唯一的变量。

接口（2026-10-05 实测）
------------------------
实时快照::

    https://qt.gtimg.cn/q=sh600570                  # GBK，v_sh600570="1~恒生电子~..."

K 线（支持复权）::

    https://web.ifzq.gtimg.cn/appstock/app/fqkline/get
        ?param=sh600570,day,2020-01-01,2026-09-30,640,qfq

``param`` 的字段是 ``代码,周期,起始日,结束日,数量,复权``，
复权留空 = 不复权，取值 ``qfq``(前) / ``hfq``(后)。

行结构（实测，**只有 6 个字段**）::

    ['2026-09-30', '19.380', '19.400', '19.440', '19.240', '99857.000']
      日期        开盘      收盘      最高      最低      成交量

两个必须记住的坑
----------------
1. **字段顺序是 O, C, H, L——收盘价排在最高价前面。**
   按直觉写成 date/open/high/low/close 会把 close 当成 high、high 当成 low，
   而且因为「收盘 ≈ 最高」看起来还挺像，能骗过肉眼下半年的对账。
2. **成交量单位本来就是「手」，与库里的 ``volume`` 口径一致。**
   库已统一为「手」（1 手 = 100 股），所以本模块**不做任何换算**——
   与通达信侧「日线返回股、需 ÷100」正好互补。

**成交额缺口**：腾讯 K 线不返回成交额（行里只有 6 个字段），``amount`` 一律写 0。
本模块**不会**用 OHLC×volume 反推——那是编数据，不是取数据。
pytdx 侧能拿到真实 amount，这是腾讯的明确能力劣势，对比报告里如实标注。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from server.external.tdx_kline import PERIOD_DAY

BASE_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
BASE_QUOTE = "https://qt.gtimg.cn/q="

# 腾讯单次最多返回 640 根（比 pytdx 的 800 少），翻页步长据此定
MAX_BARS_PER_CALL = 640

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://gu.qq.com/",
}


def _get(url: str, encoding: str, timeout: float) -> str:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode(encoding, errors="replace")


def _row_key(period: str, adjust: str) -> str:
    """响应里 K 线数组的键名：不复权是 day，前复权 qfqday，后复权 hfqday。"""
    return f"{adjust}{period}" if adjust else period


def _parse_rows(rows: list, code: str, period: str) -> list[dict]:
    """腾讯行 → 与 tdx_kline 完全一致的记录字典。

    注意 O/C/H/L 的顺序；成交量腾讯本就返回「手」，原样落库。
    """
    out = []
    for r in rows:
        if len(r) < 6:
            continue
        date, o, c, h, l, vol = r[0], r[1], r[2], r[3], r[4], r[5]
        out.append({
            "code": code,
            "period": period,
            "trade_date": date,
            "open": float(o),
            "close": float(c),
            "high": float(h),
            "low": float(l),
            "volume": round(float(vol), 2),
            # 腾讯 K 线不提供成交额，如实留 0（见模块 docstring）
            "amount": 0.0,
        })
    out.sort(key=lambda x: x["trade_date"])
    return out


def fetch_period(code: str, period: str = "day", adjust: str = "",
                 start: str = "", end: str = "", count: int = MAX_BARS_PER_CALL,
                 timeout: float = 10.0) -> list[dict]:
    """拉单段 K 线。``start``/``end`` 为 ``YYYY-MM-DD``，留空则取最近 ``count`` 根。"""
    n = max(1, min(int(count), MAX_BARS_PER_CALL))
    param = f"{code},{period},{start},{end},{n},{adjust}"
    url = f"{BASE_KLINE}?param={param}"
    data = json.loads(_get(url, "utf-8", timeout))["data"][code]
    rows = data.get(_row_key(period, adjust)) or data.get(period) or []
    return _parse_rows(rows, code, period)


def fetch_history(code: str, max_bars: int = 4000, adjust: str = "",
                  timeout: float = 10.0, retries: int = 3,
                  pause: float = 0.15) -> list[dict]:
    """翻页拉全历史日线，返回按日期升序的记录列表（与 tdx_kline.fetch_history 同形）。

    翻页方式：腾讯支持按日期区间取，所以从最早日期往回二分，
    比「取最近 N 根再猜偏移」更不容易漏数据或重复。
    """
    code = code.lower()
    merged: dict[str, dict] = {}
    end = ""  # 空 = 最新
    for _ in range(retries * 4):  # 最多翻这么多页，兜底防死循环
        if len(merged) >= max_bars:
            break
        seg = fetch_period(code, PERIOD_DAY, adjust,
                           start="", end=end, count=min(MAX_BARS_PER_CALL, max_bars),
                           timeout=timeout)
        if not seg:
            break
        before = len(merged)
        for r in seg:
            merged.setdefault(r["trade_date"], r)
        if len(merged) == before:
            break  # 这一页全是重复，说明已经翻到头
        end = seg[0]["trade_date"]
        time.sleep(pause)
    out = sorted(merged.values(), key=lambda x: x["trade_date"])
    return out[-max_bars:] if max_bars else out


def fetch_quote(code: str, timeout: float = 8.0) -> dict | None:
    """实时快照。返回 ``{name, close, open, high, low, prev_close, volume, amount, ...}``。

    成交量腾讯返回的就是「手」，与库里的 ``volume`` 同口径，故直接叫 ``volume``，
    不再另给一份「股」的字段——两套并存正是最容易拿错的那个坑。
    """
    code = code.lower()
    body = _get(f"{BASE_QUOTE}{code}", "gbk", timeout)
    if "=" not in body:
        return None
    payload = body.split('"')[1]
    f = payload.split("~")
    if len(f) < 40:
        return None
    lots = float(f[6] or 0)
    return {
        "code": code,
        "name": f[1],
        "raw_code": f[2],
        "close": float(f[3] or 0),
        "prev_close": float(f[4] or 0),
        "open": float(f[5] or 0),
        "volume": lots,
        "time": f[30],
        "change": float(f[31] or 0),
        "change_pct": float(f[32] or 0),
        "high": float(f[33] or 0),
        "low": float(f[34] or 0),
        "amount": float((f[37] or "0")) if len(f) > 37 else 0.0,
        "source": "tencent",
    }


def fetch_name(code: str, timeout: float = 8.0) -> str | None:
    """只要证券简称（腾讯是 pytdx 缺失的众多数据源里少数能给的）。"""
    q = fetch_quote(code, timeout=timeout)
    return q["name"] if q else None
