"""报价组装：优先通达信实时快照，失败回落 MySQL 最后一根 K 线。

背景（docs/architecture.md 第 10 节经验 11）：K 线接口坚持"只读库、不实时打外部源"，
避免每次刷新都走网络。但**头部报价**不一样——它要跟盘中行情联动，只读库就永远是
"最后一根已入库的日 K"，在没同步的那几天里就是一个固定值。

所以这里把两者拆开：

- ``live=True``（默认）：先打实时快照，快照不可用时自动回落库，并如实标注来源。
- ``live=False``：强制只读库，供对账 / 排查时使用。

返回结构在两条路径上一致，多两个溯源字段：

- ``source``      : ``"tdx-live"``（实时）/ ``"db"``（库内最后一根）
- ``server_time`` : 实时路径才有，通达信服务器本地时间（HH:MM:SS）
- ``datetime``    : 库内路径才有，交易日；实时路径不编造交易日
"""
import asyncio
import logging

from server import market
from server.external import tdx_quote
from server.external.tdx_kline import PERIOD_DAY

SOURCE_LIVE = "tdx-live"
SOURCE_DB = "db"


def _with_change(data: dict) -> dict:
    """补齐涨跌点数 / 涨跌幅。

    **上游已经给好就原样保留**（2026-10-10 改）。库内路径的
    ``market.query_quote`` 现在直取 ``kline_day.chg_amt`` / ``chg_pct``，
    基准是已除权的 pre_close；这里再按 close - prev_close 覆盖一遍，
    除权日会把它改回未除权口径，前端和浮窗就自相矛盾了。

    实时快照（``tdx_quote.fetch_quote``）没有这两列——它只给 last_close，
    所以走下面的现算分支，行为与从前一致。
    """
    if data.get("change") is not None and data.get("change_pct") is not None:
        return data
    prev_close = data.get("prev_close") or 0.0
    close = data.get("close") or 0.0
    data["change"] = round(close - prev_close, 4)
    data["change_pct"] = round((close - prev_close) / prev_close * 100, 4) if prev_close else 0.0
    return data


async def get_quote(code: str, period: str = PERIOD_DAY, live: bool = True) -> dict | None:
    """单个标的报价。拿不到任何数据返回 None。"""
    if live:
        # pytdx 是阻塞 socket 调用，放线程里跑，别堵住 event loop。
        try:
            snap = await asyncio.to_thread(tdx_quote.fetch_quote, code)
        except Exception as e:
            snap = None
            logging.warning("[quote] 实时快照失败，回落库内数据: code=%s %s", code, e)
        if snap and snap.get("close"):
            return _with_change({**snap, "source": SOURCE_LIVE})

    data = await market.query_quote(code, period=period)
    if data is None:
        return None
    return _with_change({**data, "source": SOURCE_DB})


async def get_quotes(codes: list[str], live: bool = True) -> dict[str, dict]:
    """批量报价（自选股列表用）。

    MVP 阶段列表里只有上证指数，这里就是逐个并发拿——不提前优化。
    后续标的多了再换成一次快照取回全部（见 ``external/tdx_quote.fetch_quotes``）。
    """
    if not codes:
        return {}
    results = await asyncio.gather(*(get_quote(c, live=live) for c in codes))
    return {code: data for code, data in zip(codes, results) if data is not None}
