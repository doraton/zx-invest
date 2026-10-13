"""扫描 pytdx 内置全部 hq_hosts，找出**能返回个股 K 线**的节点。

背景：2026-10-05 扫描出的 8 个可用节点只能返回**指数**（sh000001）的 K 线，
个股（sh600570 / sh600519 / sz000001）的 get_security_bars 一律返回 None，
但 get_security_quotes（实时行情）个股是正常的。
本脚本用「个股 K 线」重新扫一遍全量节点，确认是否存在只服务个股的节点。

用法::

    .\\.venv\\Scripts\\python.exe -W ignore tools\\sweep_stock_bars.py
    .\\.venv\\Scripts\\python.exe -W ignore tools\\sweep_stock_bars.py --code 600570 --market 1
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pytdx.config.hosts import hq_hosts  # noqa: E402
from pytdx.hq import TdxHq_API  # noqa: E402

from server.external import tdx_kline  # noqa: E402  (import 即安装解析补丁)

TIMEOUT = 3.0


def probe(host, market, code, count=5):
    # hq_hosts 的元素是 (名称, IP, 端口) 三元组，不是 (IP, 端口)
    name, ip, port = host[0], host[1], host[2]
    api = TdxHq_API(heartbeat=False)
    try:
        if not api.connect(ip, port, time_out=TIMEOUT):
            return None, "connect-fail"
        bars = api.get_security_bars(4, market, code, 0, count)
        if not bars:
            # 再确认实时行情是否通，用于区分「节点全挂」和「只不给 K 线」
            q = api.get_security_quotes([(market, code)])
            return None, ("quote-ok/no-bars" if q else "no-data")
        return bars, "ok"
    except Exception as e:
        return None, f"{type(e).__name__}"
    finally:
        try:
            api.disconnect()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="600570")
    ap.add_argument("--market", type=int, default=1)
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    hosts = list(hq_hosts)
    print(f"扫描 {len(hosts)} 个节点，找 market={args.market} code={args.code} 的日 K ...")
    hits, partial = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(probe, h, args.market, args.code, args.count): h for h in hosts}
        for fut, host in futures.items():
            bars, status = fut.result()
            if status == "ok":
                hits.append((host, bars))
                print(f"  [K线OK] {host[1]}:{host[2]} ({host[0]}) -> {len(bars)} 根, "
                      f"{bars[0]['datetime']} ~ {bars[-1]['datetime']}")
            elif status == "quote-ok/no-bars":
                partial.append(host)
            else:
                print(f"  {host[1]}:{host[2]:<6} {status}")

    print(f"\n个股 K 线可用节点: {len(hits)} 个")
    for host, _ in hits:
        print(f"  {host[1]}:{host[2]}  ({host[0]})")
    print(f"行情通但 K 线不给: {len(partial)} 个")
    for host in partial:
        print(f"  {host[1]}:{host[2]}  ({host[0]})")
    return 0 if hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
