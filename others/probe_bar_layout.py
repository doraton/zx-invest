"""穷举「每根 K 线末尾要跳几个字节（extra）」。

背景：pytdx 1.72 的 ``GetSecurityBarsCmd.parseResponse`` 对 extra 长度写死为 0，
但实测这个长度**既因标的而异、也因请求条数而变**：

- sh000001（指数）count=5 → extra=4
- sh600570（个股）count=5 → extra=0
- sh600570（个股）count=30 → 候选 0..12 全部走不通

所以不能写死，只能按候选值试探，用「正好消费完整个 body_buf + 日期合法」
自证。本脚本把候选值全跑一遍打印出来，供人工确认。

用法::

    .\\.venv\\Scripts\\python.exe -W ignore tools\\probe_bar_layout.py
    .\\.venv\\Scripts\\python.exe -W ignore tools\\probe_bar_layout.py --count 30 --max-skip 40
    .\\.venv\\Scripts\\python.exe -W ignore tools\\probe_bar_layout.py --codes sh600570
"""
import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pytdx.helper import get_datetime, get_price, get_volume  # noqa: E402
from pytdx.parser.get_security_bars import GetSecurityBarsCmd  # noqa: E402
from pytdx.hq import TdxHq_API  # noqa: E402

from server import config  # noqa: E402
from server.external import tdx_local  # noqa: E402

DEFAULT_TARGETS = [("sh000001", "000001", 1), ("sh600570", "600570", 1),
                   ("sh600519", "600519", 1), ("sz000001", "000001", 0)]

_captured: list[bytes] = []
GetSecurityBarsCmd.parseResponse = (
    lambda self, body: _captured.append(bytes(body)))


def parse(body: bytes, category: int, n: int, extra: int):
    pos = 2
    rows = []
    for _ in range(n):
        y, m, d, _h, _mi, pos = get_datetime(category, body, pos)
        po, pos = get_price(body, pos)
        pc, pos = get_price(body, pos)
        ph, pos = get_price(body, pos)
        pl, pos = get_price(body, pos)
        v = struct.unpack("<I", body[pos:pos + 4])[0]
        pos += 4
        a = struct.unpack("<I", body[pos:pos + 4])[0]
        pos += 4
        pos += extra
        rows.append((y, m, d, po, pc, ph, pl, v, a))
    return rows, pos


def ohlc(rows):
    base = 0
    out = []
    for y, m, d, po, pc, ph, pl, v, a in rows:
        b = po + base
        c = b + pc
        base = c
        out.append({"date": f"{y:04d}-{m:02d}-{d:02d}", "open": b / 1000.0,
                    "high": (b + ph) / 1000.0, "low": (b + pl) / 1000.0,
                    "close": c / 1000.0, "volume": get_volume(v), "amount": get_volume(a)})
    return out


def plausible(bars) -> bool:
    seq = [int(b["date"].replace("-", "")) for b in bars]
    inc = all(seq[i] < seq[i + 1] for i in range(len(seq) - 1))
    dec = all(seq[i] > seq[i + 1] for i in range(len(seq) - 1))
    return all(1990 <= int(b["date"][:4]) <= 2100 and b["high"] >= b["low"] > 0
               for b in bars) and (inc or dec)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--max-skip", type=int, default=12)
    ap.add_argument("--codes", nargs="*", default=None)
    ap.add_argument("--verbose", action="store_true", help="打印可用布局的逐根明细")
    args = ap.parse_args()

    targets = ([(c, c[2:], 1 if c[:2] == "sh" else 0) for c in args.codes]
               if args.codes else DEFAULT_TARGETS)

    api = TdxHq_API(heartbeat=False)
    if not api.connect(config.TDX_HOSTS[0][0], config.TDX_HOSTS[0][1],
                       time_out=config.TDX_CONNECT_TIMEOUT):
        print("连接失败")
        return 1

    for name, code, market in targets:
        _captured.clear()
        api.get_security_bars(4, market, code, 0, args.count)
        if not _captured:
            print(f"\n### {name}: 服务端未返回 body_buf")
            continue
        body = _captured[-1]
        n = struct.unpack("<H", body[:2])[0]
        print(f"\n### {name}  ret_count={n}  body_len={len(body)}  "
              f"平均每根 {(len(body) - 2) / max(n, 1):.1f} 字节")

        try:
            truth = {r["trade_date"]: r for r in tdx_local.read_day(name)}
        except Exception as e:
            truth = {}
            print(f"    (本地 .day 不可用: {e})")

        found = []
        for extra in range(args.max_skip + 1):
            try:
                rows, pos = parse(body, 4, n, extra)
            except Exception as e:
                print(f"  extra={extra:2d}  ERR {type(e).__name__}")
                continue
            bars = ohlc(rows)
            exact = pos == len(body)
            ok = exact and plausible(bars)
            hit = 0
            for b in bars:
                t = truth.get(b["date"])
                if t and all(abs(b[k] - t[k]) < 0.005
                             for k in ("open", "high", "low", "close")):
                    hit += 1
            flag = "  <<<" if ok else ""
            print(f"  extra={extra:2d}  end={pos:3d}/{len(body)}  消费完={exact}  "
                  f"合法={plausible(bars)}  与本地一致={hit}/{n}{flag}")
            if ok:
                found.append((extra, bars, hit))

        for extra, bars, hit in found:
            print(f"  === extra={extra} 逐根明细（与本地一致 {hit}/{n}）===")
            for b in (bars if args.verbose else bars[:3] + bars[-3:]):
                t = truth.get(b["date"], {})
                print(f"     {b['date']}  O{b['open']:.2f} H{b['high']:.2f} "
                      f"L{b['low']:.2f} C{b['close']:.2f} vol={b['volume']:.0f}"
                      + (f"  | 本地 O{t['open']:.2f} H{t['high']:.2f} "
                         f"L{t['low']:.2f} C{t['close']:.2f} vol={t['volume']:.0f}"
                         if t else "  | 本地无此日期"))
        if not found:
            print("  !!! 没有候选能走通")

    api.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
