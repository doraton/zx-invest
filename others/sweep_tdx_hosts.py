# -*- coding: utf-8 -*-
"""扫描 pytdx 内置 hq_hosts，找出能真正返回 K 线的服务器。

用法：
    .venv/Scripts/python.exe -W ignore others/sweep_tdx_hosts.py

背景：get_security_count 能通但 get_security_bars 返回 None，需要区分
「服务器整体不可用」和「只有 bars 接口被限制」。
"""
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from pytdx.config.hosts import hq_hosts
from pytdx.hq import TdxHq_API
from pytdx.params import TDXParams

TARGET = ("sh000001", 1, "000001")  # (后端 code, pytdx market, code)


def probe(item):
    name, ip, port = item
    out = {"name": name, "ip": ip, "port": port, "connected": False,
           "count": None, "bars": 0, "index_bars": 0, "err": ""}
    api = TdxHq_API(heartbeat=True)
    try:
        if not api.connect(ip, port, time_out=6):
            out["err"] = "connect=False"
            return out
        out["connected"] = True
        out["count"] = api.get_security_count(TARGET[1])
        bars = api.get_security_bars(TDXParams.KLINE_TYPE_DAILY, TARGET[1], TARGET[2], 0, 30)
        out["bars"] = len(bars) if bars else 0
        ib = api.get_index_bars(TDXParams.KLINE_TYPE_DAILY, TARGET[1], TARGET[2], 0, 30)
        out["index_bars"] = len(ib) if ib else 0
        if out["bars"]:
            out["last"] = bars[-1]
    except Exception as e:
        out["err"] = f"{type(e).__name__}: {e}"
    finally:
        try:
            api.disconnect()
        except Exception:
            pass
    return out


def main():
    results = []
    with ThreadPoolExecutor(max_workers=24) as pool:
        futs = {pool.submit(probe, h): h for h in hq_hosts}
        for fut in as_completed(futs):
            results.append(fut.result())
            r = results[-1]
            if r["bars"] or r["index_bars"]:
                print(f"HIT  {r['ip']}:{r['port']} bars={r['bars']} index_bars={r['index_bars']}", flush=True)

    print("\n===== 汇总 =====")
    ok = [r for r in results if r["connected"]]
    withbars = [r for r in results if r["bars"] or r["index_bars"]]
    print(f"总服务器 {len(results)} / 连接成功 {len(ok)} / 返回K线 {len(withbars)}")
    print("\n-- 返回K线的服务器 --")
    for r in sorted(withbars, key=lambda x: -x["bars"]):
        print(f"  {r['ip']}:{r['port']} bars={r['bars']} idx={r['index_bars']} count={r['count']} last={r.get('last')}")
    if not withbars:
        print("  （无）")
    print("\n-- 连上但 bars 为空（前 15） --")
    for r in [x for x in ok if not (x["bars"] or x["index_bars"])][:15]:
        print(f"  {r['ip']}:{r['port']} count={r['count']} err={r['err'] or '-'}")
    return 0 if withbars else 1


if __name__ == "__main__":
    sys.exit(main())
