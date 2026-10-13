"""通达信 pytdx 实时行情快照（单标的）。

K 线（``tdx_kline.fetch_bars``）是日线级别的历史数据，只在同步时落库；而头部报价
要求盘中联动，所以单独走 pytdx 的 ``get_security_quotes`` 快照接口——它返回最新价、
昨收、最高、最低、成交量额，是"当下这一刻"的值。

MVP 只展示上证指数（``config.MVP_TARGET``），所以这里按单标的实现：一次连接只问
一个代码，附带问一次该 market 的基准指数用于识别"回显"（见 ``_looks_like_echo``）。

三点实测结论（2026-10-05）：

1. 服务器池沿用 ``config.TDX_HOSTS``：pytdx 内置 104 个节点里只有那 8 个能真正
   返回数据，其余连接成功但 ``get_security_quotes`` 返回 None。必须轮询整池。
2. 快照**不带交易日字段**，只有 ``servertime``（如 ``14:55:16``，服务器本地时间）。
   这里原样透传，交易日由调用方决定是否拼接，不在这里编造。
3. **回显坑**：请求一个不存在的代码时，部分节点会把该 market 第一只票的数据原样
   回显——``code`` 字段仍回显你请求的代码，价格却是别人的。实测
   ``get_security_quotes([(1, '999999')])`` 返回 ``code=999999 price=3842.19``，
   也就是上证指数的价；``get_security_bars`` 有同样的毛病（同样返回 3842.19），
   所以不能拿它做存在性校验。
"""
import time

from pytdx.hq import TdxHq_API

from server import config
from server.external.tdx_kline import MARKET_SH, MARKET_SZ, split_code

# 每个 market 的基准指数：沪市 = 上证指数，深市 = 深证成指。
_MARKET_REFERENCE = {MARKET_SH: "000001", MARKET_SZ: "399001"}


def _to_float(value) -> float:
    """快照里数值字段偶尔是 None / 空串，统一压成 float。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize(item: dict) -> dict:
    """把 pytdx 的快照条目转成项目自己的报价结构。"""
    return {
        # price = 最新价（收盘后等于当日收盘）
        "close": _to_float(item.get("price")),
        "open": _to_float(item.get("open")),
        "high": _to_float(item.get("high")),
        "low": _to_float(item.get("low")),
        # last_close = 昨收，涨跌幅必须以它为基准（不是开盘价）
        "prev_close": _to_float(item.get("last_close")),
        "volume": _to_float(item.get("vol")),
        "amount": _to_float(item.get("amount")),
        "server_time": str(item.get("servertime") or ""),
    }


def _looks_like_echo(row: dict, reference: dict | None) -> bool:
    """判断这行是不是"不存在的代码被回显成了基准指数"。

    同一个响应里的 ``servertime`` 本来就是共享的，光比时间没有区分度，所以比 5 个价位
    字段。基准指数自己没取到时不做判断（宁可显示，也不要错判成空）。
    """
    if not reference:
        return False
    return all(row[f] == reference[f] for f in ("close", "open", "high", "low", "prev_close"))


def fetch_quote(code: str, hosts: list[tuple[str, int]] | None = None) -> dict | None:
    """抓单个标的的实时快照。

    :param code: 后端 code，如 ``sh000001``。
    :returns: 报价 dict；所有节点都取不到、或判定为回显时返回 None（不抛，调用方回落库）。
    """
    market, raw_code = split_code(code)
    ref_code = _MARKET_REFERENCE.get(market)
    # 一次快照调用里顺带问基准指数，用来识别回显；问的就是它自己时不重复问。
    targets = [(market, raw_code)]
    if ref_code and ref_code != raw_code:
        targets.append((market, ref_code))

    pool = list(hosts or config.TDX_HOSTS)
    last_err: Exception | None = None

    for host in pool:
        api = TdxHq_API(heartbeat=False)
        try:
            if not api.connect(host[0], host[1], time_out=config.TDX_CONNECT_TIMEOUT):
                last_err = ConnectionError(f"连接失败 {host[0]}:{host[1]}")
                continue
            raw = api.get_security_quotes(targets)
            rows = {r["code"]: r for r in (raw or [])}
            item = rows.get(raw_code)
            if not item:
                last_err = RuntimeError(f"{host[0]}:{host[1]} 无 {raw_code}")
                continue
            # 请求的就是基准指数本身时，参考行和目标行是同一条，不能拿来判回显。
            ref_item = rows.get(ref_code) if (ref_code and ref_code != raw_code) else None
            if _looks_like_echo(_normalize(item), _normalize(ref_item) if ref_item else None):
                # 判为"代码不存在"：这是数据问题不是节点问题，换节点也没用，直接返回。
                return None
            return _normalize(item)
        except Exception as e:  # 单节点异常不应中断整个池
            last_err = e
        finally:
            try:
                api.disconnect()
            except Exception:
                pass
        time.sleep(0.2)

    raise RuntimeError(f"所有通达信服务器均失败，最后错误: {last_err}")


def fetch_quotes(codes: list[str], hosts: list[tuple[str, int]] | None = None) -> dict[str, dict]:
    """批量抓实时快照。

    MVP 阶段只有上证指数，走的是简单循环——**一次连接一个标的**，好读、好排查。
    后续标的变多时再优化：pytdx 的 ``get_security_quotes`` 一次能带多个
    (market, code)，改成共用一次连接即可（参考本文件里"基准指数"那段注释，
    批量时要把各 market 的基准指数也带上，才能识别回显）。

    :returns: ``{code: 报价}``；取不到 / 判定为回显的不出现在结果里。
    """
    out: dict[str, dict] = {}
    for code in codes:
        try:
            snap = fetch_quote(code, hosts=hosts)
        except Exception:
            snap = None  # 整池失败已在 fetch_quote 抛出，这里按缺失处理
        if snap:
            out[code] = snap
    return out
