"""本地通达信 vipdoc 日线读取（个股 K 线的唯一可行来源）。

为什么需要它
------------
2026-10-05 全量扫描 pytdx 内置 104 个 ``hq_hosts`` 的结论：

- 个股（sh600570 / sh600519 / sz000001）的 ``get_security_bars`` **返回 None**，
  一个节点都没有；
- ``get_security_quotes``（实时行情）个股**正常**；
- ``get_finance_info`` / ``get_xdxr_info`` 个股**正常**。

也就是说，能连上的那 8 个「国泰君安」节点只服务**指数** K 线，个股历史行情不对外。
所以个股日线只能从本地通达信 vipdoc 的 ``.day`` 文件取 —— 这本来就是通达信客户端
自己读的那份数据，与实时行情是同一份（见 README 里的交叉校验）。

文件格式：每条记录固定 32 字节，按交易日升序::

    date     uint32  YYYYMMDD
    open     uint32  价格 × 100
    high     uint32  价格 × 100
    low      uint32  价格 × 100
    close    uint32  价格 × 100
    amount   float32 成交额，单位元
    volume   uint32  成交量，单位股
    reserved uint32

注意：``.day`` 的价格是 **×100**，与网络协议（×1000）不同，这是常见踩坑点。
"""
import re
import struct
from pathlib import Path

from server import config
from server.external.tdx_kline import PERIOD_DAY

_REC = struct.Struct("<IIIII f I I")
_REC_SIZE = 32

# vipdoc 的市场目录名与后端 code 前缀一致（sh / sz）
_MARKET_DIR = {"sh": "sh", "sz": "sz"}


def day_file_path(code: str, vipdoc: Path | None = None) -> Path:
    """``sh600570`` → ``<vipdoc>/sh/lday/sh600570.day``。"""
    code = code.lower()
    prefix, raw = code[:2], code[2:]
    if prefix not in _MARKET_DIR:
        raise ValueError(f"不支持的交易所前缀: {prefix!r}（仅支持 sh/sz）")
    root = Path(vipdoc or config.TDX_VIPDOC)
    return root / _MARKET_DIR[prefix] / "lday" / f"{code}.day"


def read_day(code: str, vipdoc: Path | None = None) -> list[dict]:
    """读取本地日线，返回按日期升序的记录列表。

    字段与 :func:`tdx_kline.fetch_bars` 产出的记录完全一致，
    因此下游的 upsert / 物化 / 展示代码不需要区分数据来源。

    ``.day`` 文件里 vol 是**股**、amount 是**元**；这里已换算成库里的口径
    （volume 手、amount 千元），所以与 tdx_kline._to_rows 出站一致。
    """
    path = day_file_path(code, vipdoc)
    if not path.exists():
        raise FileNotFoundError(f"本地日线文件不存在: {path}")

    raw = path.read_bytes()
    n = len(raw) // _REC_SIZE
    out = []
    for i in range(n):
        d, o, h, l, c, amt, vol, _r = _REC.unpack_from(raw, i * _REC_SIZE)
        if d < 19900101:  # 停牌/空记录：通达信用 0 或垃圾日期占位
            continue
        stamp = f"{d // 10000:04d}-{d // 100 % 100:02d}-{d % 100:02d}"
        out.append({
            "code": code.lower(),
            "period": PERIOD_DAY,
            "trade_date": stamp,
            "open": round(o / 100.0, 4),
            "high": round(h / 100.0, 4),
            "low": round(l / 100.0, 4),
            "close": round(c / 100.0, 4),
            "volume": round(float(vol) / 100.0, 2),      # 股 -> 手
            "amount": round(float(amt) / 1000.0, 2),    # 元 -> 千元
        })
    out.sort(key=lambda r: r["trade_date"])
    return out


def available_local_codes(vipdoc: Path | None = None) -> list[str]:
    """列出本地有日线文件的全部标的。"""
    root = Path(vipdoc or config.TDX_VIPDOC)
    out = []
    for market in _MARKET_DIR:
        d = root / market / "lday"
        if d.is_dir():
            out.extend(p.stem for p in d.glob("*.day"))
    return sorted(out)


# ───────────────────────── 证券简称（shs.tnf / szs.tnf） ─────────────────────────
#
# 证券简称是本项目唯一没有网络来源的字段：pytdx 全线拿不到名字
# （get_security_list 返回 None，且已扫描的 8 个可用节点无一例外）。
# 唯一可用的来源是本地通达信客户端自己的名称缓存：
#
#   T0002/hq_cache/shs.tnf   沪市
#   T0002/hq_cache/szs.tnf   深市
#
# 记录布局（按 6 位数字代码定位，布局经实测确认）：
#
#   <6 字节 ASCII 数字代码><若干 NUL 填充><GBK 名称><NUL>
#
# 填充长度不是文档化的，实测 shs.tnf / szs.tnf 里统一是 25 字节，
# 但这里不写死：向前跳过至多 _NAME_PAD_MAX 个 NUL 再取名，避免换版本就失效。
#
# 名称本身是 GBK，且有三种坑：
#   - 000002 万科Ａ  —— 末字是全角 Ａ（U+FF21），不是 ASCII A
#   - 000858 五 粮 液 —— 字节里嵌了 ASCII 空格（0x20）
#   - ST / *ST 前缀 —— 含 ASCII 字母和星号
# 所以字符集校验必须同时放行 CJK、ASCII 字母、全角字母、星号和空格。
#
# 同一个 6 位代码在不同文件里可能指向不同标的（例如 000002 在 shs.tnf 里是
# 上证A股指数、在 szs.tnf 里是万科A），所以按市场分别建表，不做跨市场合并。

_NAME_FILE = {"sh": "shs.tnf", "sz": "szs.tnf"}
_NAME_PAD_MAX = 48
_NAME_MAX_BYTES = 24
_NAME_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z\uFF01-\uFF5E* ]{1,16}$")
_CODE_RE = re.compile(rb"(?<![0-9])([0-9]{6})(?![0-9])")

_name_cache: dict[str, dict[str, str]] = {}


def _parse_tnf(raw: bytes) -> dict[str, str]:
    """从单个 .tnf 解析 ``{6 位代码: 简称}``。"""
    out: dict[str, str] = {}
    for m in _CODE_RE.finditer(raw):
        i = m.start(1)
        if raw[i + 6:i + 7] != b"\x00":  # 数字代码后必须紧跟 NUL，否则是价格的巧合匹配
            continue
        j = i + 6
        while j < len(raw) and raw[j] == 0 and j - i <= _NAME_PAD_MAX:
            j += 1
        if j == i + 6:  # 没有填充，代码后面直接就是下一个字段，不是名称记录
            continue
        k = raw.find(b"\x00", j)
        if not 0 < k - j <= _NAME_MAX_BYTES:
            continue
        try:
            name = raw[j:k].decode("gbk")
        except UnicodeDecodeError:
            continue
        name = re.sub(r"\s+", "", name)
        if name and _NAME_RE.match(name):
            out.setdefault(m.group(1).decode(), name)
    return out


def load_name_map(hq_cache: Path | None = None) -> dict[str, dict[str, str]]:
    """加载 ``{市场: {6 位代码: 简称}}``，按目录缓存（.tnf 近 10MB，只解析一次）。"""
    root = Path(hq_cache or config.TDX_HQ_CACHE)
    key = str(root)
    cached = _name_cache.get(key)
    if cached is not None:
        return cached
    table: dict[str, dict[str, str]] = {}
    for market, fname in _NAME_FILE.items():
        path = root / fname
        if path.is_file():
            table[market] = _parse_tnf(path.read_bytes())
        else:
            table[market] = {}
    _name_cache[key] = table
    return table


def get_security_name(code: str, hq_cache: Path | None = None) -> str | None:
    """``sh600570`` → ``恒生电子``。取不到返回 ``None``（不抛异常）。"""
    code = code.lower()
    prefix = code[:2]
    if prefix not in _NAME_FILE:
        return None
    return load_name_map(hq_cache).get(prefix, {}).get(code[2:])
