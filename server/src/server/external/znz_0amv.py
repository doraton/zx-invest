"""指南针 0AMV（活跃市值指数）日线解析。

0AMV 是指南针专有指标，没有公开 API、没有明文文件，早期方案是 OCR 截图
（见 docs/znz-active-mv-ocr-sync.md §14 的替代方案对比）。本模块改为直接读
指南针客户端的本地二进制缓存，OCR 路线整体不再需要。

来源与格式（2026-10-09 在本机 v?? 实测，与 compass-0amv-extractor 仓库
描述的版本**不同**，以下为本机实测值）
--------------------------------------------------------------------------

``day.vdat`` —— 历史日线，是 ``.fde`` 的权威来源
    路径 ``<compass>\\ANALYSE\\Data\\ChinaStk\\Z_SK\\day.vdat``

    ⚠️ 目录是 ``Z_SK\\``，不是早期文档记的 ``ZNZ_\\day.vdat``（后者是另一
    个序列，不含 0AMV 数据页）。

    一个文件里混放多个标的序列，0AMV 的数据页以 28 字节定长头标识::

        b"Z_SK0AMV" + 20 个零字节

    页内布局::

        28 字节    数据页头
        7000 字节  最多 250 条 × 28 字节
        20 字节    页尾

    每条 28 字节（小端）::

        struct.Struct("<fIfffff")
        # amount(float32) date(uint32 YYYYMMDD) O H L C(float32) volume(float32)

    **页是连续排布的，间距恒为 7048 = 28 + 250*28 + 20**。本机 35 处命中
    ``Z_SK0AMV`` 页头，其中 33 处落在 7048 网格上、间距严格一致，是真数据页；
    另外 2 处落在别的结构内部（同文件混有 ``Z_SK0B01`` / ``Z_SK0DMV`` 等兄弟
    序列的 magic），属**误匹配**，解不出有效记录自然被 OHLC 校验丢掉。
    因此不能用「页数 × 250」估算记录数。

``.fde`` —— 当日盘中缓存（每天首次打开 0AMV 页面时生成）
    路径 ``<compass>\\Temp\\Z_SK0AMV__.fde``（另有 ``Z_SK0AMV<日期>.fde``
    是历史留存的零星快照，多数为空壳）

    ⚠️ 本机版本是 ``.fde`` / magic ``2ACR``；上游仓库描述的是
    ``.cde`` / magic ``XADR``，记录 48 字节。**不要照抄仓库代码**。

    本机布局::

        24 字节头
            0   4 字节   magic b"2ACR"
            4   uint32   日期 YYYYMMDD
            8   uint32   单条记录长度 = 16
            12  uint32   固定常量 145900（14:59:00）
            16  uint32   声明 body 长度 = 3824（实测比实际多 4 字节，勿信）
            20  uint32   固定常量 93059（09:30:59）
        body：16 字节/条，条数取 (len-24)//16::

            struct.Struct("<fffI")   # value, volume, amount, time(HHMMSS)

    偏移 8 / 12 / 16 / 20 在**不同日期**的文件里取值完全相同，故 12 与 20 是
    采样窗口的固定端点常量，不是当日的实际起止时刻。

    时间字段在剔除午休后每 60 秒递增一次，本机实测 09:31:59 ~ 14:58:59 共 238 条
    （236 次间隔 60 秒，唯一例外是午休 11:29:59 → 13:00:59 跳 5460 秒）。
    日 OHLC 由首条 value / 末条 value / 全程 max / 全程 min 推出。

锁
----
指南针**运行中**打开 0AMV 页面时，``Z_SK\\day.vdat`` 与 ``Dr2.vdat`` 被独占
锁定，Python 读会抛 ``PermissionError``，``Copy-Item`` 与
``FileShare.ReadWrite`` 同样被拒。而 ``Temp\\*.fde`` 与其它目录的 day.vdat
**不受影响**。因此日常增量优先读 ``.fde``，全量补历史才需要先退出指南针。

⚠️ 锁定时抛的是权限错误而不是解析错误，**不要把它误判成「文件里没有数据」**
——那会得出「历史缺失」的错误结论（2026-10-09 实测踩过：锁着时只解出 276 条
稀疏记录，关掉客户端后同一文件是完整的 8219 条）。

数据口径
--------
``volume`` / ``amount`` **原值返回，不做单位换算**（2026-10-09 起由「忽略」改为采集）。
但必须清楚它们的真实性质，别按成交量额解读：

- ``day.vdat`` 的两个字段**内部自洽**——``amount ÷ volume`` 跨 30 年稳定落在 8~22；
  但它与 0AMV 指数值**不同量纲**：``amount ÷ volume ÷ close`` 从 1993 年的 ~0.99
  一路漂到 2026 年的 ~1e-4。单位尚未查明，因此列注释不写「手 / 千元」。
- ``day.vdat`` 的 ``amount`` **每页首条为 0**（实测 8219 条里 33 条为 0，恰好
  33 个数据页），不是「这个指标没有成交额」，而是该字段并非逐日完整结算值。
- ``.fde`` 的量额则**连内部都不自洽**——末条 ``amount ÷ value`` ≈ 1.2e7，
  不可能是元。故 ``.fde`` 路径的量额返回 ``None``，不并入日线结果。

除权：0AMV 是「活跃筹码流通市值加总」，价格与股本反向等比变动、市值恒定，
不存在需要修正的除权断层。因此 ``kline_0amv`` **不设 pre_close 列**——
前收即前一交易日的 close。
"""
from __future__ import annotations

import datetime as dt
import struct
from pathlib import Path

# ---- day.vdat ----
_CODE = b"Z_SK0AMV"
_PAGE_HEADER = _CODE + b"\x00" * 20
_PAGE_RECORDS = 250
_DAY_RECORD = struct.Struct("<fIfffff")  # amount, YYYYMMDD, O, H, L, C, volume

# ---- .fde ----
_FDE_MAGIC = b"2ACR"
_FDE_HEADER_SIZE = 24
_FDE_RECORD = struct.Struct("<fffI")  # value, volume, amount, HHMMSS

#: 库内 code。首字符是**数字 0**（指南针内部代号 0AMV），不是字母 O。
DEFAULT_CODE = "0AMV"


class ZnzParseError(RuntimeError):
    """0AMV 文件格式无法解析。"""


def _parse_yyyymmdd(value: int) -> dt.date | None:
    try:
        return dt.date(value // 10000, (value // 100) % 100, value % 100)
    except ValueError:
        return None


def _valid_ohlc(o: float, h: float, l: float, c: float) -> bool:
    """OHLC 关系自洽性。

    不设「值必须大于某个阈值」的下限——0AMV 早期只有几千亿，
    用 ``OHLC > 1000`` 这类阈值会直接毁掉 1993-2000 年的真实历史。
    """
    return (o > 0 and h >= l > 0 and h >= max(o, c) and l <= min(o, c))


def read_file(path: Path) -> bytes:
    """读文件，遇到锁给出可操作的报错而不是含糊的解析失败。"""
    try:
        return Path(path).read_bytes()
    except PermissionError as e:
        raise ZnzParseError(
            f"{path} 被指南针锁定（客户端运行中打开了 0AMV 页面）。\n"
            f"  关闭指南针后重试；或改读 Temp\\Z_SK0AMV__.fde 拿当日数据。\n"
            f"  注意：这是权限错误，文件内容是完整的，不是「没有数据」。"
        ) from e


def _find_pages(data: bytes) -> list[int]:
    """定位所有 0AMV 数据页的起始偏移。

    严格比对 28 字节定长头，**不做**「值看起来合理」的启发式扫描——
    同一文件混放多个标的，启发式会误取别的序列。
    """
    return [i for i in range(len(data)) if data.startswith(_PAGE_HEADER, i)]


def parse_day_vdat(path: Path) -> dict[dt.date, dict]:
    """解析 ``day.vdat``，返回 ``{trade_date: {open, high, low, close, volume, amount}}``。

    只接受头 28 字节严格等于 ``Z_SK0AMV`` + 20 个零字节的页，不做全文件
    启发式扫描——否则会误取同一文件里其它标的的序列。

    ``volume`` / ``amount`` 一并返回**原值**（2026-10-09 起由忽略改为采集）：
    两者内部自洽（``amount ÷ volume`` 稳定在 8~22，跨 30 年不变），但与 0AMV
    指数值不同量纲，**不要**当成「成交量·手 / 成交额·千元」解读，也不要做单位换算。
    实测见 docs/znz-active-mv-ocr-sync.md §2.1.3。
    """
    data = read_file(path)
    out: dict[dt.date, dict] = {}
    pages = _find_pages(data)
    if not pages:
        raise ZnzParseError(f"{path} 未找到 Z_SK0AMV 数据页（0AMV 页面是否打开过？）")

    for page in pages:
        for slot in range(_PAGE_RECORDS):
            off = page + len(_PAGE_HEADER) + slot * _DAY_RECORD.size
            if off + _DAY_RECORD.size > len(data):
                break
            amt, raw_date, o, h, l, c, vol = _DAY_RECORD.unpack_from(data, off)
            date = _parse_yyyymmdd(raw_date)
            if date is None or not _valid_ohlc(o, h, l, c):
                continue  # 空页 / 索引表槽位
            out.setdefault(date, {"open": o, "high": h, "low": l, "close": c,
                                  "volume": vol, "amount": amt})
    if not out:
        raise ZnzParseError(f"{path} 找到 {len(pages)} 个数据页但没有一条有效记录")
    return out


def parse_fde(path: Path) -> dict | None:
    """解析 ``.fde`` 当日缓存，返回 ``{trade_date, open, high, low, close, samples}``。

    文件被占用 / 未生成时返回 ``None``——``.fde`` 是可选补充，不该因此中断同步。
    """
    try:
        data = Path(path).read_bytes()
    except (OSError, PermissionError):
        return None
    if len(data) <= _FDE_HEADER_SIZE or data[:4] != _FDE_MAGIC:
        return None

    date = _parse_yyyymmdd(struct.unpack_from("<I", data, 4)[0])
    if date is None:
        return None

    body = data[_FDE_HEADER_SIZE:]
    values = []
    for i in range(len(body) // _FDE_RECORD.size):
        value = _FDE_RECORD.unpack_from(body, i * _FDE_RECORD.size)[0]
        # 空槽位（补齐用）value 恒为 0；活跃市值不可能为 0
        if value > 0:
            values.append(value)
    if len(values) < 2:
        return None

    return {
        "trade_date": date,
        "open": values[0],
        "high": max(values),
        "low": min(values),
        "close": values[-1],
        "samples": len(values),
    }


def fetch_daily(compass_root: Path, since: dt.date | None = None,
                today_only: bool = False) -> dict[dt.date, dict]:
    """汇总 0AMV 日线。

    优先级：``day.vdat``（历史权威）> ``.fde``（当日补充）。
    同一天两者都有时以 ``day.vdat`` 为准——它是收盘后最终落盘的值。

    :param since: 只保留该日期及之后的记录（含）。``None`` = 全历史。
    :param today_only: 跳过 ``day.vdat``（会被锁），只读 ``.fde``。
    """
    root = Path(compass_root)
    merged: dict[dt.date, dict] = {}

    if not today_only:
        day_vdat = root / "ANALYSE" / "Data" / "ChinaStk" / "Z_SK" / "day.vdat"
        try:
            merged.update(parse_day_vdat(day_vdat))
        except ZnzParseError:
            # 锁住时不致命：降级为只取 .fde，调用方据 source 字段判断覆盖度
            pass

    fde = parse_fde(root / "Temp" / "Z_SK0AMV__.fde")
    if fde:
        merged.setdefault(fde["trade_date"], {"open": fde["open"], "high": fde["high"],
                                              "low": fde["low"], "close": fde["close"],
                                              # .fde 的量额与 0AMV 不自洽（amount÷value≈1.2e7），
                                              # 与 day.vdat 的量额不同口径，故不并入。
                                              "volume": None, "amount": None})

    if since is not None:
        merged = {d: v for d, v in merged.items() if d >= since}
    return merged
