"""通达信 pytdx 直连数据源。

K 线协议解析：为什么不能照抄 pytdx，也不能照抄「补 4 字节」的民间补丁
--------------------------------------------------------------------
``GetSecurityBarsCmd.parseResponse`` 的记录布局（变长）::

    date(4)     日线以上是 uint32 YYYYMMDD；分钟线是 (zipday, tminutes)
    open/close/high/low  varint，相对前一记录基址的差值
    vol    (4)  自定义浮点编码，pytdx.helper.get_volume 解
    amount (4)  同上
    extra  (N)  **尾部保留字段，长度不固定**

实测（2026-10-05，见 others/probe_bar_layout.py）：

=========  ==========  ================  ==========================
标的       body 长度    正确的 extra 长度  备注
=========  ==========  ================  ==========================
sh000001   135         4                指数
sh600570    96         0                个股
sh600519   111         0                个股
sz000001    92         0                个股
=========  ==========  ================  ==========================

也就是说 **extra 长度因标的而异**，写死任何一个值都会让另一类标的错位：
- 写死 0（pytdx 原版）→ 个股完全正确，指数每根少跳 4 字节、逐根错位；
- 写死 4（常见民间补丁）→ 指数正确，个股越界读出垃圾日期。

而且 pytdx 的 ``get_security_bars`` 被 ``@update_last_ack_time`` 装饰器包着，
它会**吞掉所有异常并返回 None**（除非 ``raise_exception=True``），
所以「解析器越界」在调用方看来和「服务端没数据」完全一样 —— 
这正是 2026-10-05 一度误判「免费节点不提供个股 K 线」的原因。

本模块因此不写死 extra 长度，而是**按候选值逐个试探，取第一个能
「正好消费完整个 body_buf」且「日期合法」的布局**（见 :func:`_parse_body`）。
判据是自洽的，不依赖对协议的猜测。
"""
import struct
import time
from collections import OrderedDict

from pytdx.helper import get_datetime, get_price, get_volume
from pytdx.hq import TdxHq_API
from pytdx.params import TDXParams
from pytdx.parser.get_security_bars import GetSecurityBarsCmd

from server import config

# pytdx market 常量
MARKET_SZ = 0
MARKET_SH = 1

# 周期
PERIOD_DAY = "day"
PERIOD_WEEK = "week"
PERIOD_MONTH = "month"
# 季/年线是**物化**出来的（由日线按窗口函数分桶），不直接向 TDX 拉取：
# 实测 TDX 虽有 category 10/11，但非日线 vol 单位是「手」而日线是「股」，
# 混用同一列会造成隐蔽错误，所以派生周期一律走日线 → SQL 物化。
# 即便仍走原生周/月，_to_rows 也已按 period 分别换算，不会串单位。
PERIOD_QUARTER = "quarter"
PERIOD_YEAR = "year"

_CATEGORY = {
    PERIOD_DAY: TDXParams.KLINE_TYPE_DAILY,
    PERIOD_WEEK: TDXParams.KLINE_TYPE_WEEKLY,
    PERIOD_MONTH: TDXParams.KLINE_TYPE_MONTHLY,
}

# 尾部保留字段的候选长度，按实测命中率排序（个股 0、指数 4）
_EXTRA_CANDIDATES = (0, 4, 8, 12, 16, 20)

_applied = False


def _decode(body_buf: bytes, category: int, ret_count: int, extra: int) -> tuple[list, int]:
    """按给定 extra 长度解码，返回 (K 线列表, 消费到的字节位置)。"""
    pos = 2
    klines = []
    pre_diff_base = 0
    for _ in range(ret_count):
        year, month, day, hour, minute, pos = get_datetime(category, body_buf, pos)
        price_open_diff, pos = get_price(body_buf, pos)
        price_close_diff, pos = get_price(body_buf, pos)
        price_high_diff, pos = get_price(body_buf, pos)
        price_low_diff, pos = get_price(body_buf, pos)

        (vol_raw,) = struct.unpack("<I", body_buf[pos: pos + 4])
        pos += 4
        (amount_raw,) = struct.unpack("<I", body_buf[pos: pos + 4])
        pos += 4
        pos += extra

        base = price_open_diff + pre_diff_base
        close = base + price_close_diff
        high = base + price_high_diff
        low = base + price_low_diff
        pre_diff_base = close

        klines.append(OrderedDict([
            ("open", base / 1000.0),
            ("close", close / 1000.0),
            ("high", high / 1000.0),
            ("low", low / 1000.0),
            ("vol", get_volume(vol_raw)),
            ("amount", get_volume(amount_raw)),
            ("year", year), ("month", month), ("day", day),
            ("hour", hour), ("minute", minute),
            ("datetime", "%d-%02d-%02d %02d:%02d" % (year, month, day, hour, minute)),
        ]))
    return klines, pos


def _plausible(klines: list) -> bool:
    """日期与价格是否合理。用于排除错位解析出的垃圾数据。

    用**完整时间元组** (年,月,日,时,分) 判单调，而不是只用日期：
    分钟线一天有多根，只看日期会把正常数据判成非法；带上时分则既能
    接受日内多根，又能识别出日期乱跳的错位解析。
    方向不写死（升序或降序都接受），实测报文为旧→新升序。
    """
    if not klines:
        return False
    seq = []
    for k in klines:
        y, m, d = k["year"], k["month"], k["day"]
        if not (1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31):
            return False
        if not (0 <= k["hour"] <= 23 and 0 <= k["minute"] <= 59):
            return False
        if k["high"] < k["low"] or k["high"] <= 0:
            return False
        seq.append((y, m, d, k["hour"], k["minute"]))
    inc = all(seq[i] < seq[i + 1] for i in range(len(seq) - 1))
    dec = all(seq[i] > seq[i + 1] for i in range(len(seq) - 1))
    return inc or dec


def _parse_body(body_buf, category: int):
    """解析 K 线报文，自动判定尾部保留字段长度。

    判据：能走完 ret_count 根、**正好消费完整个 body_buf**、且结果日期合法。
    三个条件同时满足才采纳，否则换下一个候选长度。
    """
    if len(body_buf) < 2:
        raise ValueError(f"K 线报文过短: {len(body_buf)} 字节")
    (ret_count,) = struct.unpack("<H", body_buf[0:2])
    if ret_count == 0:
        return []
    last_err: Exception | None = None
    for extra in _EXTRA_CANDIDATES:
        try:
            klines, pos = _decode(body_buf, category, ret_count, extra)
        except Exception as e:
            last_err = e
            continue
        if pos == len(body_buf) and _plausible(klines):
            return klines
        last_err = RuntimeError(
            f"extra={extra} 消费 {pos}/{len(body_buf)} 字节，"
            f"结果{'合法' if _plausible(klines) else '非法'}")
    raise ValueError(
        f"无法确定 K 线报文布局（ret_count={ret_count}, len={len(body_buf)}）；"
        f"最后原因: {last_err}")


def fixed_parse_response(self, body_buf):
    """替换 pytdx 原版：自动判定尾部保留字段长度。"""
    return _parse_body(body_buf, self.category)


def apply_parse_fix() -> None:
    """幂等安装解析补丁。"""
    global _applied
    if _applied:
        return
    GetSecurityBarsCmd.parseResponse = fixed_parse_response
    _applied = True


apply_parse_fix()


def canonical_code(code: str) -> str:
    """把任意写法的 code 归一成**库里唯一的形态**：大写前缀 + 点 + 6 位数字。

        sh600570 -> SH.600570
        SZ.399001 -> SZ.399001
        600570.SH -> SH.600570

    这是一轮 code 收口（2026-10-09）之后**唯一的形态来源**。此前各写入方
    各造各的：``sync_tushare.py`` 产出 ``SH.600570``，而
    ``backfill_stock_universe.py`` 产出 ``sh600570``，两个都往同一张
    ``stock_info`` 写，于是「谁最后跑谁说了算」，``kline_day`` 和
    ``stock_info`` 的 JOIN 长期接不上（行情列表里名称恒为空）。

    认不出来的（``0AMV`` / ``mom`` 这类自研标的没有交易所前缀）原样返回，
    不硬加前缀——那会造出一个不存在的市场。
    """
    c = code.strip()
    if "." in c:
        head, _, tail = c.partition(".")
        if head.isdigit() and tail[:2].lower() in ("sh", "sz"):
            # Tushare 的 600570.SH 后缀写法
            return f"{tail.upper()}.{head}"
        if head[:2].lower() in ("sh", "sz") and tail.isdigit():
            # SH.600570 前缀写法（已是目标形态，只做大小写归一）
            return f"{head.upper()}.{tail}"
    if len(c) == 8 and c[:2].lower() in ("sh", "sz") and c[2:].isdigit():
        return f"{c[:2].upper()}.{c[2:]}"
    # 认不出来的（0AMV / mom 这类自研标的）**原样返回，一个字符都不改**。
    # 早先这里对纯字母做了 .upper()，于是 canonical_code('mom') 会返回 'MOM'，
    # 而 index_info 里存的是 'mom'——一旦哪个调用方把指数 code 丢进来，
    # 大小写对不上就静默查不到。归一化只该做「已知等价写法」的合并，
    # 不该顺手改写它不认识的东西。
    return c


def split_code(code: str) -> tuple[int, str]:
    """``sh000001`` / ``SH.000001`` -> ``(MARKET_SH, '000001')``。

    两种写法都要认：2026-10-09 起落库形态是 :func:`canonical_code` 的
    ``SH.000001``，而通达信 .tnf 那边仍是 ``sh000001``。
    """
    c = canonical_code(code)
    if len(c) < 3:
        raise ValueError(f"非法 code: {code!r}")
    prefix, rest = c[:2].lower(), c[2:]
    # canonical_code 已经把分隔点吃掉了；这里再兜一层，防止将来混进别处的
    # 裸 'SH.' 形态把点当成 raw_code 的第一位（那会污染 stock_info.raw_code）。
    rest = rest.lstrip(".")
    if prefix == "sh":
        return MARKET_SH, rest
    if prefix == "sz":
        return MARKET_SZ, rest
    raise ValueError(f"不支持的交易所前缀: {prefix!r}（仅支持 sh/sz）")


def to_date(stamp: str) -> str:
    """``'2026-09-30 15:00'`` → ``'2026-09-30'``。"""
    return stamp[:10]


def _connect(host, time_out):
    """建连，并**打开异常抛出**。

    pytdx 的 ``get_security_bars`` 被 ``@update_last_ack_time`` 包着，
    构造函数默认 ``raise_exception=False`` 时它会吞掉所有异常并返回 ``None``，
    于是「解析器崩溃」和「服务端没数据」完全无法区分
    （2026-10-05 一度因此误判免费节点不提供个股 K 线）。
    这里一律 ``raise_exception=True``，让失败带着原始异常暴露出来。
    """
    api = TdxHq_API(heartbeat=False, raise_exception=True)
    if not api.connect(host[0], host[1], time_out=time_out):
        raise ConnectionError(f"连接失败 {host[0]}:{host[1]}")
    return api


#: 1 手 = 100 股。通达信**日线** vol 返回股、非日线返回手，
#: 落库统一为「手」，故日线要 ÷100 而非日线不用。
_SHARES_PER_LOT = 100.0


def _to_rows(code: str, period: str, bars: list) -> list[dict]:
    # 通达信口径：vol 日线=股、非日线=手（精确差 100 倍）；amount 各周期都是元。
    # 库里的 volume 存手、amount 存千元，故日线 ÷100、amount 一律 ÷1000。
    vol_div = _SHARES_PER_LOT if period == PERIOD_DAY else 1.0
    return [
        {
            "code": code,
            "period": period,
            "trade_date": to_date(b["datetime"]),
            "open": round(b["open"], 4),
            "high": round(b["high"], 4),
            "low": round(b["low"], 4),
            "close": round(b["close"], 4),
            "volume": round(b["vol"] / vol_div, 2),
            "amount": round(b["amount"] / 1000.0, 2),
        }
        for b in bars
    ]


def fetch_bars(code: str, period: str = PERIOD_DAY, days: int = 30,
               hosts: list[tuple[str, int]] | None = None) -> list[dict]:
    """按服务器池轮询抓取 K 线，失败自动换节点。

    :param days: 取最近多少根（交易日）。一个月约 21 根，取 30 根留足余量。
    :returns: 按日期升序的记录列表，字段 code/period/trade_date/ohlc/volume/amount。
    """
    market, raw_code = split_code(code)
    category = _CATEGORY.get(period)
    if category is None:
        raise ValueError(f"不支持的周期: {period!r}")

    count = min(days, config.TDX_MAX_BARS_PER_CALL)
    last_err: Exception | None = None

    for host in (hosts or config.TDX_HOSTS):
        api = None
        try:
            api = _connect(host, config.TDX_CONNECT_TIMEOUT)
            raw = api.get_security_bars(category, market, raw_code, 0, count)
            if not raw:
                last_err = RuntimeError(f"{host[0]}:{host[1]} 返回空")
                continue
            return _to_rows(code, period, raw)
        except Exception as e:  # 单节点异常不应中断整个池
            last_err = e
        finally:
            if api is not None:
                try:
                    api.disconnect()
                except Exception:
                    pass
        time.sleep(0.2)

    raise RuntimeError(f"所有通达信服务器均失败，最后错误: {last_err}")


def fetch_history(code: str, period: str = PERIOD_DAY, max_bars: int = 4000,
                  hosts: list[tuple[str, int]] | None = None,
                  log=None) -> list[dict]:
    """分页抓取全历史 K 线（单次协议上限 800 根，必须翻页）。

    ``get_security_bars`` 的 ``start`` 是**从最新往回数的偏移量**：
    ``start=0`` 取最近 count 根，``start=800`` 再往前 800 根，以此类推。
    每页内部是**旧→新升序**，页与页之间是**新→旧**，
    所以最终要把「页的顺序」倒过来，页内顺序保持不动。
    """
    market, raw_code = split_code(code)
    category = _CATEGORY.get(period)
    if category is None:
        raise ValueError(f"不支持的周期: {period!r}")

    page = config.TDX_MAX_BARS_PER_CALL
    last_err: Exception | None = None

    for host in (hosts or config.TDX_HOSTS):
        api = None
        try:
            api = _connect(host, config.TDX_CONNECT_TIMEOUT)
            pages: list[list[dict]] = []
            total = 0
            start = 0
            while total < max_bars:
                raw = api.get_security_bars(category, market, raw_code, start, page)
                if not raw:
                    break
                rows = _to_rows(code, period, raw)
                pages.append(rows)
                total += len(rows)
                if log:
                    log(f"  [page] start={start} 本页 {len(rows)} 根, 累计 {total}")
                if len(raw) < page:
                    break  # 已经取到最早的一根
                start += page
            if not pages:
                last_err = RuntimeError(f"{host[0]}:{host[1]} 返回空")
                continue
            # 页倒序拼接，页内保持升序 -> 整体严格升序
            return [r for pg in reversed(pages) for r in pg]
        except Exception as e:
            last_err = e
        finally:
            if api is not None:
                try:
                    api.disconnect()
                except Exception:
                    pass
        time.sleep(0.2)

    raise RuntimeError(f"所有通达信服务器均失败，最后错误: {last_err}")
