"""底层探针：把 pytdx 发出的请求与收到的原始报文打出来，判断「返回 None」是
**服务端没数据**还是**pytdx 解析层吃掉了数据**。

同时横向试一遍 pytdx 里所有可能给个股历史数据的命令：
- ``get_security_bars``   标准 K 线（category 0..11 全试）
- ``get_index_bars``     独立��「指数 K 线」命令，个股有时走这条
- ``get_history_minute_time_data`` 历史分时
- ``get_transaction_data``     历史逐笔
- ``get_minute_time_data``     当日分时
- ``get_security_list``        证券列表
- ``get_finance_info`` / ``get_xdxr_info`` / ``get_company_info_*`` 基本面

用法::

    .\\.venv\\Scripts\\python.exe -W ignore tools\\probe_stock_raw.py
    .\\.venv\\Scripts\\python.exe -W ignore tools\\probe_stock_raw.py --code 600570 --market 1
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pytdx.hq import TdxHq_API  # noqa: E402
from pytdx.params import TDXParams  # noqa: E402

from server import config  # noqa: E402
from server.external import tdx_kline  # noqa: E402  (import 即安装解析补丁)

CATEGORY_NAMES = {
    TDXParams.KLINE_TYPE_5MIN: "5分", TDXParams.KLINE_TYPE_15MIN: "15分",
    TDXParams.KLINE_TYPE_30MIN: "30分", TDXParams.KLINE_TYPE_1HOUR: "60分",
    TDXParams.KLINE_TYPE_DAILY: "日K", TDXParams.KLINE_TYPE_WEEKLY: "周K",
    TDXParams.KLINE_TYPE_MONTHLY: "月K", TDXParams.KLINE_TYPE_1MIN: "1分",
    TDXParams.KLINE_TYPE_3MONTH: "季K", TDXParams.KLINE_TYPE_YEARLY: "年K",
}


def hexdump(data: bytes, width: int = 32, limit: int = 160) -> str:
    out = [f"len={len(data)}"]
    for i in range(0, min(len(data), limit), width):
        chunk = data[i:i + width]
        out.append(f"  {i:04x}  " + " ".join(f"{b:02x}" for b in chunk))
    if len(data) > limit:
        out.append(f"  ... 其余 {len(data) - limit} 字节省略")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="600570")
    ap.add_argument("--market", type=int, default=1)
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--host", default=None, help="默认用服务器池第一个")
    args = ap.parse_args()

    host = (args.host, 7709) if args.host else config.TDX_HOSTS[0]
    print(f"目标 host={host} market={args.market} code={args.code}\n")

    # ---- 抓原始报文 ----
    captured = {}
    orig_parse = tdx_kline.fixed_parse_response

    def spy(self, body_buf):
        captured["bars_body"] = body_buf
        return orig_parse(self, body_buf)

    from pytdx.parser.get_security_bars import GetSecurityBarsCmd
    GetSecurityBarsCmd.parseResponse = spy

    api = TdxHq_API(heartbeat=False)
    if not api.connect(host[0], host[1], time_out=config.TDX_CONNECT_TIMEOUT):
        print("连接失败")
        return 1

    def show(label, fn):
        try:
            r = fn()
        except Exception as e:
            print(f"{label:<34} EXC {type(e).__name__}: {e}")
            return None
        if r is None:
            print(f"{label:<34} None")
        elif isinstance(r, list):
            head = r[0] if r else {}
            tail = r[-1] if r else {}
            extra = ""
            if isinstance(head, dict) and "datetime" in head:
                extra = f" {head.get('datetime')} ~ {tail.get('datetime')}"
                extra += f" lastC={tail.get('close')}"
            print(f"{label:<34} {len(r)} 条{extra}")
        else:
            print(f"{label:<34} {type(r).__name__} {str(r)[:80]}")
        return r

    print("=== 1. get_security_bars 全 category ===")
    for cat, name in CATEGORY_NAMES.items():
        show(f"get_security_bars(cat={cat} {name})",
             lambda c=cat: api.get_security_bars(c, args.market, args.code, 0, args.count))
    if "bars_body" in captured:
        body = captured["bars_body"]
        print(f"\n[原始 body_buf] {hexdump(body)}")
        if len(body) >= 2:
            import struct
            print(f"[首个 uint16 = ret_count] {struct.unpack('<H', body[:2])[0]}")
    else:
        print("\n[原始 body_buf] 未进入 parseResponse —— 说明请求根本没拿到行情包体")

    print("\n=== 2. get_index_bars（独立命令）===")
    for cat in (TDXParams.KLINE_TYPE_DAILY, TDXParams.KLINE_TYPE_WEEKLY, TDXParams.KLINE_TYPE_MONTHLY):
        show(f"get_index_bars(cat={cat})",
             lambda c=cat: api.get_index_bars(c, args.market, args.code, 0, args.count))

    print("\n=== 3. 分时 / 逐笔 ===")
    show("get_minute_time_data(600570)", lambda: api.get_minute_time_data(args.market, args.code))
    for year in (2026, 2025):
        show(f"get_history_minute_time_data({year})",
             lambda y=year: api.get_history_minute_time_data(args.market, args.code, y))
    show("get_transaction_data(2026-09-30)",
         lambda: api.get_transaction_data(args.market, args.code, 2026, 9, 30))

    print("\n=== 4. 列表 / 计数 / 板块 ===")
    show("get_security_count", lambda: api.get_security_count(args.market))
    show("get_security_list(0..3)", lambda: api.get_security_list(args.market, 0))
    show("get_index_list(0..3)", lambda: api.get_index_list(args.market, 0))
    show("get_block_info(datacenter)", lambda: api.get_block_info("block_zs.dat"))

    print("\n=== 5. 基本面（已知可用，回归确认）===")
    show("get_finance_info", lambda: api.get_finance_info(args.market, args.code))
    show("get_xdxr_info", lambda: api.get_xdxr_info(args.market, args.code))
    show("get_company_info_category", lambda: api.get_company_info_category(args.code))
    show("get_company_info_content(000001)",
         lambda: api.get_company_info_content(args.market, args.code, "000001"))

    api.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
