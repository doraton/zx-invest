"""通达信 → MySQL 同步编排。

两级入口：
- :func:`sync_tdx_kline` —— 早期 MVP：单标的 + 日线 + 最近 N 根（默认上证指数）
- :func:`sync_stock` —— 完整链路：日线全历史 → 物化周/月/季/年 → 基本面 → 除权除息

派生周期（周/月/季/年）**不向通达信单独拉取**，一律由日线物化，
理由见 docs/architecture.md §3.3：原生非日线 vol 恒差 100 倍。
"""
import time

from server import config, db, kline_repo, stock_repo
from server.external import tdx_finance, tdx_kline, tdx_local
from server.external.tdx_kline import canonical_code


def _fetch_daily(code: str, max_bars: int, source: str, log) -> tuple[list[dict], str, str | None]:
    """取日线全历史。返回 (记录, 实际来源, 回退原因)。

    pytdx 的免费节点**只服务指数**，个股 ``get_security_bars`` 一律返回 None
    （见 external/tdx_local.py 模块 docstring），所以个股必须回退到本地 vipdoc。
    """
    if source == "local":
        return tdx_local.read_day(code)[:max_bars], "local-vipdoc", None
    try:
        rows = tdx_kline.fetch_history(code, period=tdx_kline.PERIOD_DAY,
                                       max_bars=max_bars, log=log)
        return rows, "pytdx", None
    except Exception as e:
        if source == "pytdx":
            raise
        reason = f"{type(e).__name__}: {e}"
        log(f"      pytdx 取 {code} 日线失败（{reason}），回退本地 vipdoc")
        return tdx_local.read_day(code)[:max_bars], "local-vipdoc", reason


async def sync_tdx_kline(code: str | None = None, period: str = tdx_kline.PERIOD_DAY,
                         days: int = 30, verbose: bool = True) -> dict:
    """拉取 K 线并落库。

    :param code: 后端 code 形态，如 ``sh000001``；默认取 config.MVP_TARGET。
    :param days: 取最近多少根日线（一个月约 21 根）。
    """
    code = code or config.MVP_TARGET
    log = (lambda m: print(m, flush=True)) if verbose else (lambda m: None)

    await db.init_pool()
    applied = await db.init_schema()
    if applied:
        log(f"[sync] 执行迁移: {applied}")

    log(f"[sync] 开始 code={code} period={period} days={days}")
    await kline_repo.set_sync_state(code, period, "running", message="fetching")

    started = time.time()
    try:
        rows = tdx_kline.fetch_bars(code, period=period, days=days)
        log(f"[sync] 通达信返回 {len(rows)} 根: {rows[0]['trade_date']} ~ {rows[-1]['trade_date']}")

        affected = await kline_repo.upsert_klines(rows)

        # first_date / last_date 取自 kline 表聚合结果（覆盖全部历史），
        # 而不是本次拉取窗口 —— 否则增量同步会把范围收窄成本次窗口。
        first_date, last_date, n = await kline_repo.get_range(code, period)
        await kline_repo.set_sync_state(code, period, "ok",
                                        first_date=first_date, last_date=last_date,
                                        message=f"{len(rows)} rows")

        elapsed = time.time() - started
        log(f"[sync] 完成 落库 {affected} 行 | 库内 {n} 行 {first_date}~{last_date} | 耗时 {elapsed:.2f}s")
        return {"code": code, "period": period, "fetched": len(rows),
                "affected": affected, "total": n, "first": first_date, "last": last_date,
                "elapsed": round(elapsed, 3)}
    except Exception as e:
        await kline_repo.set_sync_state(code, period, "failed", message=str(e))
        log(f"[sync] 失败: {type(e).__name__}: {e}")
        raise


async def sync_stock(code: str, max_bars: int = 4000, derive: bool = True,
                     with_finance: bool = True, with_xdxr: bool = True,
                     source: str = "auto", verbose: bool = True) -> dict:
    """完整同步单个标的。

    步骤：
      1. 拉取**日线全历史**（唯一真相源）
      2. 由日线物化 week / month / quarter / year
      3. ``get_finance_info`` → stock_info + stock_finance
      4. ``get_xdxr_info`` → stock_xdxr

    :param source: 日线来源。``auto`` = 先试 pytdx，失败回退本地 vipdoc；
        ``pytdx`` / ``local`` 强制指定。

    **code 入参会被 :func:`canonical_code` 归一**，所以 ``sh600570`` / ``SH.600570`` /
    ``600570.SH`` 都能用，落库形态一律是 ``SH.600570``。
    早先这里原样把入参往下传，而 :func:`external.tdx_kline._to_rows` 会把它写进
    ``code`` 列，于是 ``tools/sync_stock.py sh600570`` 落的是小写带不带点的形态，
    与 ``kline_day`` 里既有的 ``SH.600570`` 大小写对不上，JOIN 静默接不上。
    （``fetch_xdxr`` 当初栽的就是同一个坑，见 ``tdx_finance.fetch_xdxr`` 的 docstring。）

    任何一步失败都不会回滚已落库的部分：日线是主数据，先落；派生与基本面
    失败只记 sync_state 的 message，次日调度重试即可。
    """
    code = canonical_code(code)
    log = (lambda m: print(m, flush=True)) if verbose else (lambda m: None)
    started = time.time()

    await db.init_pool()
    applied = await db.init_schema()
    if applied:
        log(f"[sync] 执行迁移: {applied}")

    result: dict = {"code": code}

    # ---- 1. 日线 ----
    bars, source_used, source_error = _fetch_daily(code, max_bars, source, log)
    log(f"      拿到 {len(bars)} 根: {bars[0]['trade_date']} ~ {bars[-1]['trade_date']}"
        f"  来源={source_used}")
    result["day_source"] = source_used
    if source_error:
        result["day_source_error"] = source_error
    result["day_fetched"] = len(bars)
    result["day_affected"] = await kline_repo.upsert_klines(bars)
    d_first, d_last, d_n = await kline_repo.get_range(code, tdx_kline.PERIOD_DAY)
    await kline_repo.set_sync_state(code, tdx_kline.PERIOD_DAY, "ok",
                                    first_date=d_first, last_date=d_last,
                                    message=f"{len(bars)} rows via {source_used}")
    log(f"      日线入库 {d_n} 根 {d_first}~{d_last}")
    result["day_total"] = d_n
    result["day_first"], result["day_last"] = d_first, d_last

    # ---- 2. 物化派生周期 ----
    result["derived"] = {}
    if derive:
        log(f"[sync] 2/4 物化 {', '.join(kline_repo.DERIVED_PERIODS)}")
        result["derived"] = await kline_repo.materialize_periods(code)
        for period, affected in result["derived"].items():
            p_first, p_last, p_n = await kline_repo.get_range(code, period)
            await kline_repo.set_sync_state(code, period, "ok",
                                            first_date=p_first, last_date=p_last,
                                            message="materialized from day")
            log(f"      {period:<7} {p_n:>4} 根 {p_first}~{p_last} (写入 {affected} 行)")
            result["derived"][period] = {"affected": affected, "total": p_n,
                                         "first": p_first, "last": p_last}

    # ---- 3. 基本面 ----
    if with_finance:
        log("[sync] 3/4 拉基本面 get_finance_info")
        try:
            fi = tdx_finance.fetch_finance(code)
            await stock_repo.upsert_stock(fi["stock"])
            await stock_repo.upsert_finance(fi["finance"])
            result["finance"] = {"report_date": fi["finance"]["report_date"],
                                 "fields": len(fi["finance"]) - 2}
            log(f"      证券资料 + 财务快照 {fi['finance']['report_date']} 已入库")
        except Exception as e:
            result["finance_error"] = f"{type(e).__name__}: {e}"
            log(f"      基本面失败（不阻断）: {result['finance_error']}")
    else:
        log("[sync] 3/4 跳过基本面")

    # ---- 4. 除权除息 ----
    if with_xdxr:
        log("[sync] 4/4 拉除权除息 get_xdxr_info")
        try:
            xd = tdx_finance.fetch_xdxr(code)
            result["xdxr"] = {"rows": len(xd),
                              "affected": await stock_repo.upsert_xdxr(xd)}
            log(f"      {len(xd)} 条除权除息已入库")
        except Exception as e:
            result["xdxr_error"] = f"{type(e).__name__}: {e}"
            log(f"      除权除息失败（不阻断）: {result['xdxr_error']}")
    else:
        log("[sync] 4/4 跳过除权除息")

    result["elapsed"] = round(time.time() - started, 3)
    log(f"[sync] 全部完成，耗时 {result['elapsed']}s")
    return result
