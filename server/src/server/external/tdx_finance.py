"""通达信基本面数据源：财务快照 + 除权除息。

实测结论（2026-10-05，见 docs/architecture.md §3.2）：
- ``get_finance_info`` 一次返回 37 个字段，但**只有最新一期**（无日期参数）
- ``industry`` / ``province`` 是**数字编码**，``get_block_info`` 在可用节点返回空，无法翻译
- ``get_security_list`` 在可用节点返回 None，**名称暂无来源**
- ``get_finance_info`` 的日期字段返回 **int**（如 20260826）而非字符串，务必用 to_date() 转换
"""
from pytdx.hq import TdxHq_API
from pytdx.params import TDXParams  # noqa: F401  (保持与 tdx_kline 同一导入副作用)

from server import config
from server.external.tdx_kline import canonical_code, split_code  # import 即安装 pytdx 解析补丁


def to_date(value) -> str | None:
    """pytdx 的 YYYYMMDD 可能是 int 或 str，统一转成 ``YYYY-MM-DD``。"""
    if value in (None, "", 0, "0"):
        return None
    s = str(int(value))
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 else None


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# pytdx 拼音字段 -> stock_finance 英文字段
FINANCE_FIELD_MAP = {
    "zongzichan": "total_asset", "liudongzichan": "current_asset",
    "gudingzichan": "fixed_asset", "wuxingzichan": "intangible_asset",
    "zongfuzhai": "total_liability", "liudongfuzhai": "current_liability",
    "jingzichan": "net_asset",
    "zhuyingshouru": "main_revenue", "yingyelirun": "operating_profit",
    "lirunzonghe": "total_profit", "jinglirun": "net_profit",
    "shuihoulirun": "after_tax_profit", "weifenpeilirun": "undistributed_profit",
    "jingyingxianjinliu": "op_cash_flow", "zongxianjinliu": "net_cash_flow",
    "cunhuo": "inventory", "yingshouzhangkuan": "accounts_receivable",
    "gudongrenshu": "shareholder_count", "meigujingzichan": "per_share_net_asset",
}


def _with_pool(fn):
    """在服务器池上轮询执行，失败换节点。"""
    last_err: Exception | None = None
    for host in config.TDX_HOSTS:
        api = TdxHq_API(heartbeat=False)
        try:
            if not api.connect(host[0], host[1], time_out=config.TDX_CONNECT_TIMEOUT):
                last_err = ConnectionError(f"连接失败 {host[0]}:{host[1]}")
                continue
            result = fn(api)
            if result is None:
                last_err = RuntimeError(f"{host[0]}:{host[1]} 返回空")
                continue
            return result
        except Exception as e:
            last_err = e
        finally:
            try:
                api.disconnect()
            except Exception:
                pass
    raise RuntimeError(f"所有通达信服务器均失败，最后错误: {last_err}")


def fetch_finance(code: str) -> dict:
    """拉取证券主数据 + 财务快照（一次 ``get_finance_info`` 拆成两份）。"""
    market, _ = split_code(code)

    def _run(api):
        return api.get_finance_info(market, code[2:])

    fi = _with_pool(_run)

    stock = {
        "code": code,
        "raw_code": code[2:],
        "market": market,
        "name": None,                       # 暂无来源，见模块 docstring
        "ipo_date": to_date(fi.get("ipo_date")),
        "total_share": _num(fi.get("zongguben")),
        "float_share": _num(fi.get("liutongguben")),
        "industry_code": fi.get("industry"),
        "province_code": fi.get("province"),
    }
    finance = {"code": code, "report_date": to_date(fi.get("updated_date"))}
    for py_key, en_key in FINANCE_FIELD_MAP.items():
        v = fi.get(py_key)
        finance[en_key] = int(v) if (en_key == "shareholder_count" and v not in (None, "")) else _num(v)
    return {"stock": stock, "finance": finance}


def fetch_xdxr(code: str) -> list[dict]:
    """拉取除权除息历史（多条）。

    ⚠️ 两个 code 形态必须分开用（2026-10-12 实测）：
      - **查询**用 :func:`split_code` 返回的裸 6 位（``600570``）——
        早先写成 ``code[2:]``，传库里的规范形态 ``SH.600570`` 会切出
        ``.600570``，pytdx 静默返回**空列表**，不报错，最难查。
      - **落库**用 :func:`canonical_code` 的规范形态（``SH.600570``）——
        传 ``sh600570`` 落进去就跟 kline_day / stock_info 的 code 对不上，
        以后 join 不掉。
    """
    market, plain = split_code(code)
    db_code = canonical_code(code)

    def _run(api):
        return api.get_xdxr_info(market, plain)

    raw = _with_pool(_run) or []
    out = []
    for r in raw:
        out.append({
            "code": db_code,
            "ex_date": to_date(f"{r.get('year', 0):04d}{r.get('month', 0):02d}{r.get('day', 0):02d}"),
            "category": r.get("category"),
            "name": r.get("name"),
            # 以下比例字段量纲均为「每 10 股」
            "bonus_ratio": _num(r.get("songzhuangu")),   # 送转股
            "allot_ratio": _num(r.get("peigu")),           # 配股
            "allot_price": _num(r.get("peigujia")),        # 配股价
            "cash_dividend": _num(r.get("fenhong")),       # 现金分红
            "reduce_ratio": _num(r.get("suogu")),          # 缩股比例
            "share_before": _num(r.get("qianzongguben")),  # 前总股本
            "share_after": _num(r.get("houzongguben")),   # 后总股本
            "float_before": _num(r.get("panqianliutong")), # 前流通股本
            "float_after": _num(r.get("panhouliutong")),  # 后流通股本
        })
    return [r for r in out if r["ex_date"]]
