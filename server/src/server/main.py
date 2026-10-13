"""FastAPI 入口。

数据链路（MVP）：MySQL ← sync.py ← 通达信 pytdx 直连。
K 线接口只读库，保证前端拿到的就是刚同步进来的数据；**报价接口例外**——
它走通达信实时快照（失败自动回落库内最后一根），否则头部价格在没同步的日子里
不会随盘中行情变化。组装逻辑见 ``server/quote.py``。
"""
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from server import app_config, db, market, quote, stock_repo
from server.external.tdx_kline import (
    PERIOD_DAY, PERIOD_MONTH, PERIOD_QUARTER, PERIOD_WEEK, PERIOD_YEAR,
)
from server.sync import sync_tdx_kline

# category 是**前端契约值**，不是 TDX 线协议。实测 TDX 协议：4=日K 5=周K 6=月K
# 10=季K 11=年K，而 7/8 是 5分钟/15分钟，不是季/年线。
# 前端 web/src/utils/chartOptions.ts 的 PERIOD_OPTIONS 目前仍按错误的
# 「7=季 8=年」发参（该文件归属并行会话），所以这里两种都收：
#   - 10/11 是协议正确值
#   - 7/8 仅为兼容前端现有取值而存在的别名，不代表 TDX 线类型
# 未命中的 category 一律回落日线——这会让「季线/年线」静默显示成日线，
# 曾经就是这个漏映射导致的。宁可查不到也不要静默给错周期。
PERIOD_BY_CATEGORY = {
    "4": PERIOD_DAY,
    "5": PERIOD_WEEK,
    "6": PERIOD_MONTH,
    "10": PERIOD_QUARTER,
    "11": PERIOD_YEAR,
    # 兼容前端旧取值
    "7": PERIOD_QUARTER,
    "8": PERIOD_YEAR,
}

#: /api/index/list 的返回内容。⚠️ 这**不是** `index_info` 表的镜像，而是
#: 一份手写常量——`index_info` 里有 0AMV / mom 两条自研指标，但它们没有行情来源
#: （mom 是线上面板抓的，不在本项目的取数链路里），所以这里只列通达信 4 个 + 0AMV。
#: 前端真正用的是 web/src/utils/indices.ts 里那份，两边要同步改。
#:
#: 2026-10-09 起 code 统一成全库唯一形态 `SH.000001`（大写带点，
#: 见 server.external.tdx_kline.canonical_code）。`0AMV` / `mom` 没有交易所
#: 前缀，不参与这个约定，原样保留。
INDICES = [
    {"code": "SH.000001", "name": "上证指数", "short": "上证"},
    {"code": "SZ.399001", "name": "深证成指", "short": "深证"},
    {"code": "SZ.399006", "name": "创业板指", "short": "创业板"},
    {"code": "SH.000688", "name": "科创50", "short": "科创50"},
    # 指南针本地文件导出的活跃市值，走独立表 kline_0amv；首字符是数字 0，
    # 且只有日线（daily_only 供调用方禁用周/月/季/年）。
    {"code": "0AMV", "name": "活跃市值指数", "short": "活跃市值", "daily_only": True},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建连接池与表；MySQL 不可用不阻断服务，仅记录。"""
    try:
        await db.init_pool()
        await db.init_schema()
        print("[startup] MySQL 连接池就绪，表结构已校验")
    except Exception as e:
        print(f"[startup] MySQL 初始化失败（服务仍启动，接口会返回错误）: {e}")
    yield
    await db.close_pool()
    print("[shutdown] MySQL 连接池已关闭")


app = FastAPI(title="zx-stock API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/index/kline")
async def get_index_kline(
    code: str = "SH.000001",
    category: str = Query("4", description="4=日K 5=周K 6=月K 10=季K 11=年K；7/8 为兼容前端旧取值"),
    count: int = Query(250, ge=1, le=market.MAX_COUNT),
    period: str | None = Query(None, description="直接指定 day/week/month，优先于 category"),
    start_date: str | None = Query(None, description="YYYY-MM-DD，含当天。补更新的数据时用"),
    end_date: str | None = Query(None, description="YYYY-MM-DD，含当天。向前翻历史时传当前已加载的最早日期"),
    adj: str = Query("none", description="复权模式 none/qfq/hfq，见 market.ADJ_MODES"),
):
    """K 线数据（读 MySQL）。返回 {code, data, truncated}。

    游标用法（前端向左加载更多历史）：
      首次        不传 start_date/end_date            -> 最近 count 根
      往前翻      end_date=<当前已加载最早日期>        -> 该日期及之前的 count 根
      补最新      start_date=<当前已加载最晚日期>      -> 该日期及之后的 count 根

    ``truncated=true`` 表示按 end_date 查时更早还有数据、这次被 count 截断了；
    前端必须读它，否则会把截断结果误当成"库里就这么多"。

    每根 bar 带 ``is_xdxr``（当天是否除权日，由 pre_close 判定，不落库），
    前端据此在主图下方画除权标记——翻历史加载时标记会跟着增删，不用单独对账。

    ``adj`` 选复权模式。**复权不改变 ``chg_pct``**：库里的 chg_pct 基准是已除权
    pre_close，三种模式下语义都成立（见 market._apply_adj 的推导）。
    """
    p = period or PERIOD_BY_CATEGORY.get(str(category), PERIOD_DAY)
    try:
        data, truncated = await market.query_klines(
            code, period=p, count=count, start_date=start_date, end_date=end_date,
            adj=adj)
        return JSONResponse(content={"code": 0, "data": data, "truncated": truncated})
    except Exception as e:
        return JSONResponse(content={"code": 1, "error": str(e)}, status_code=500)


@app.get("/api/stock/xdxr")
async def get_stock_xdxr(code: str):
    """某标的的除息除权记录（弹窗列表用）。返回 {code, data}。

    列表骨架是「真实发生过的除权日」（由 ``kline_day.pre_close`` 判定），
    文案取自 ``stock_xdxr``。库里没同步过除权除息时 ``text`` 为空串，
    这是正常中间态——除权日由价格事实决定，不依赖文案表。

    响应刻意用 ``ensure_ascii=True``，理由同 :func:`list_stocks`：
    ``text`` 是「每10股派2.00元」这样的中文，不转义的话本机浏览器读这个响应体会挂起。
    """
    try:
        payload = json.dumps({"code": 0, "data": await market.query_xdxr_records(code)},
                             ensure_ascii=True)
        return Response(content=payload, media_type="application/json")
    except Exception as e:
        return JSONResponse(content={"code": 1, "error": str(e)}, status_code=500)


@app.get("/api/index/quote")
async def get_index_quote(code: str = "SH.000001", period: str = PERIOD_DAY,
                          live: bool = Query(True, description="false=强制只读库，用于对账排查")):
    """最新报价：实时快照优先，失败回落库内最后一根 K 线。

    涨跌幅以**昨收**（prev_close）为基准，实时路径的 prev_close 来自通达信快照，
    库内路径取前一根 K 线的收盘。返回 ``source`` 标明这次的数据来源。
    """
    try:
        data = await quote.get_quote(code, period=period, live=live)
        if data is None:
            return JSONResponse(content={"code": 1, "error": f"{code} 无数据，请先同步"},
                                status_code=404)
        return JSONResponse(content={"code": 0, "data": data})
    except Exception as e:
        return JSONResponse(content={"code": 1, "error": str(e)}, status_code=500)


@app.get("/api/index/quotes")
async def get_index_quotes(codes: str = Query("", description="逗号分隔，如 SH.000001,SZ.399001"),
                           live: bool = Query(True, description="false=强制只读库")):
    """批量报价（自选股列表用）。MVP 阶段列表里通常只有上证指数，够用即可。"""
    wanted = [c.strip() for c in codes.split(",") if c.strip()][:50]
    if not wanted:
        return JSONResponse(content={"code": 1, "error": "codes 不能为空"}, status_code=400)
    try:
        data = await quote.get_quotes(wanted, live=live)
        return JSONResponse(content={"code": 0, "data": data})
    except Exception as e:
        return JSONResponse(content={"code": 1, "error": str(e)}, status_code=500)


@app.get("/api/index/list")
async def list_indices():
    return JSONResponse(content={"code": 0, "data": INDICES})


@app.get("/api/stock/list")
async def list_stocks():
    """行情列表：库里已有行情数据的全部标的 + 每只的最新一根与涨跌幅。

    以 ``kline_day`` 为准（见 ``market.list_stocks`` 的 docstring），
    名称从 ``stock_info`` 表 LEFT JOIN，缺名时返回 ``null`` 由前端兜底。

    响应刻意用 ``ensure_ascii=True``（\\uXXXX 转义）而不是 FastAPI 默认的
    ``JSONResponse``：这是唯一一个会返回**原始 UTF-8 中文**的接口，实测浏览器
    在本机对未转义的中文响应体读取会挂起（请求到达、服务端回 200，
    但 fetch 的 body 流不结束）。转义后纯 ASCII，`\\u6052\\u751f\\u7535\\u5b50`
    解析回来还是同一个字符串。
    """
    try:
        payload = json.dumps({"code": 0, "data": await market.list_stocks()},
                             ensure_ascii=True)
        return Response(content=payload, media_type="application/json")
    except Exception as e:
        return JSONResponse(content={"code": 1, "error": str(e)}, status_code=500)


@app.get("/api/stock/search")
async def search_stocks(q: str = "", limit: int = 30):
    """A 股搜索：查 MySQL ``stock_info`` 主表。

    之前固定返回空列表（stock_info 表未同步）。``stock_info`` 表已由
    ``sync.sync_stock()`` 落库（见 docs/architecture.md §5.3），现在真正查库。
    纯 LIKE 实现够 MVP 用；ES 索引就绪后切到 ES，届时再补拼音/模糊召回。
    """
    if not q or not q.strip():
        return JSONResponse(content={"code": 0, "data": [], "query": q})
    try:
        data = await stock_repo.search_stocks(q, limit=limit)
        return JSONResponse(content={"code": 0, "data": data, "query": q})
    except Exception as e:
        return JSONResponse(content={"code": 1, "error": str(e)}, status_code=500)


@app.post("/api/admin/sync")
async def trigger_sync(code: str | None = None, period: str = PERIOD_DAY, days: int = 30):
    """手动触发通达信同步（拉取 → 落库 → 更新 sync_state）。"""
    try:
        result = await sync_tdx_kline(code=code, period=period, days=days)
        return JSONResponse(content={"code": 0, "data": result})
    except Exception as e:
        return JSONResponse(content={"code": 1, "error": str(e)}, status_code=500)


@app.get("/api/admin/status")
async def admin_status():
    """同步状态 + 库中已有数据概览。"""
    try:
        return JSONResponse(content={"code": 0, "data": {
            "db_ok": await db.healthcheck(),
            "items": await market.available_codes(),
        }})
    except Exception as e:
        return JSONResponse(content={"code": 1, "error": str(e)}, status_code=500)


@app.get("/api/config")
async def get_app_config():
    """前端显示设置的持久化值（app_config 表）。

    **没配过的键不会出现在 data 里**，前端按自己的默认值兜底——
    默认值的唯一真相在前端，后端不存第二份。
    """
    try:
        data = await app_config.get_all()
        return JSONResponse(content={"code": 0, "data": data})
    except Exception as e:
        return JSONResponse(content={"code": 1, "error": str(e)}, status_code=500)


@app.put("/api/config")
async def put_app_config(payload: dict[str, Any]):
    """写入显示设置。只 upsert 传进来的键，**不会删掉没传的**。

    body 直接就是配置对象本身（不是 ``{"data": {...}}``），
    因为这是纯设置接口，没有分页/包装的必要。
    """
    try:
        affected = await app_config.upsert_all(payload or {})
        return JSONResponse(content={"code": 0, "data": {"updated": affected}})
    except Exception as e:
        return JSONResponse(content={"code": 1, "error": str(e)}, status_code=500)


@app.get("/health")
async def health():
    return {"status": "ok", "db": await db.healthcheck()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8310)
