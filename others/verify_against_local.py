"""pytdx 拉到的 K 线 vs 本地通达信 .day 真值，逐根逐字段比对。

这是本项目唯一能证明「数据对不对」的锚点：pytdx 是逆向协议，
解析器出错时不会报错（异常被吞、返回 None 或垃圾数据），
只有拿本地 .day 交叉比对才能发现。

用法::

    .\\.venv\\Scripts\\python.exe -W ignore tools\\verify_against_local.py
    .\\.venv\\Scripts\\python.exe -W ignore tools\\verify_against_local.py --count 800
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server import config  # noqa: E402
from server.external import tdx_kline, tdx_local  # noqa: E402

DEFAULT_TARGETS = ["sh000001", "sh600570", "sh600519", "sz000001"]
# vol 走 float32 自定义编码，末位有精度损失，比对时放宽。
# 价格容差 0.011：实测**指数**（sh000001）800 根里有 6 天与本地 .day 差恰好 1 个
# 最小价位（±0.01），且次日自动归位、不累积漂移 —— 这是指数行情源自身的舍入差异，
# 不是解析错位（错位会产出垃圾值或持续偏移）。个股在 800 根上是 100% 精确的。
TOL = {"open": 0.011, "high": 0.011, "low": 0.011, "close": 0.011,
       "volume": 200.0, "amount": 5000.0}


async def run(targets, count, log) -> int:
    bad = 0
    for code in targets:
        try:
            local = {r["trade_date"]: r for r in tdx_local.read_day(code)}
        except FileNotFoundError as e:
            print(f"{code}: 本地 .day 不存在，跳过（{e}）")
            continue

        try:
            remote = await asyncio.to_thread(tdx_kline.fetch_bars, code, "day", count)
        except Exception as e:
            print(f"{code}: pytdx 拉取失败 {type(e).__name__}: {e}")
            bad += 1
            continue

        if not remote:
            print(f"{code}: pytdx 返回空")
            bad += 1
            continue

        # 只比对双方都有的日期（本地文件可能比网络更新或滞后）
        common = [r for r in remote if r["trade_date"] in local]
        if not common:
            print(f"{code}: 日期完全对不上！pytdx {remote[0]['trade_date']}~"
                  f"{remote[-1]['trade_date']} vs 本地 "
                  f"{min(local)}~{max(local)}")
            bad += 1
            continue

        diffs: dict[str, int] = {}
        for r in common:
            t = local[r["trade_date"]]
            for k, tol in TOL.items():
                if abs(r[k] - t[k]) > tol:
                    diffs[k] = diffs.get(k, 0) + 1

        status = "OK" if not diffs else "DIFF"
        print(f"{code}: {status}  pytdx {len(remote)} 根 "
              f"({remote[0]['trade_date']}~{remote[-1]['trade_date']}), "
              f"与本地可比 {len(common)} 根"
              + (f"  差异={diffs}" if diffs else ""))
        print(f"    最新一根 pytdx: O{remote[-1]['open']} H{remote[-1]['high']} "
              f"L{remote[-1]['low']} C{remote[-1]['close']} vol={remote[-1]['volume']} "
              f"amt={remote[-1]['amount']}")
        if diffs:
            bad += 1
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=30)
    ap.add_argument("--codes", nargs="*", default=DEFAULT_TARGETS)
    args = ap.parse_args()
    print(f"比对 {len(args.codes)} 个标的 × 最近 {args.count} 根 vs 本地 {config.TDX_VIPDOC}\n")
    bad = asyncio.run(run(args.codes, args.count, print))
    print(f"\n结果: {'全部一致' if bad == 0 else f'{bad} 个标的存在差异'}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
