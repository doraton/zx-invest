from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from server.data_source import (
    DataSourceFactory,
    get_data_source,
    get_default_source,
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/index/quote")
def get_index_quote(code: str = "000001", source: str = "akshare"):
    """获取指数当前行情。code 支持 sh000001 / sz399001 / sz399006 / sh000688。"""
    try:
        ds = get_data_source(source)
        data = ds.get_index_quote(market=0, code=code)
        return JSONResponse(content={"code": 0, "data": asdict(data) if data else None})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return JSONResponse(content={"code": 1, "error": str(e)}, status_code=500)


@app.get("/api/index/kline")
def get_index_kline(code: str = "000001", category: int = 4, count: int = 200, source: str = "akshare"):
    """获取指数 K 线数据。

    - code: 指数代码（sh000001 / sz399001 / sz399006 / sh000688）
    - category: 4=日K, 5=周K, 6=月K
    - count: 返回条数（尾部取）
    - source: 数据源（默认 akshare）
    """
    try:
        ds = get_data_source(source)
        data = ds.get_index_kline(market=0, code=code, category=category, count=count)
        return JSONResponse(content={"code": 0, "data": [asdict(k) for k in data]})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return JSONResponse(content={"code": 1, "error": str(e)}, status_code=500)


@app.get("/api/index/list")
def list_indices():
    """返回支持的指数列表（前端做切换器）。"""
    return JSONResponse(content={
        "code": 0,
        "data": [
            {"code": "sh000001", "name": "上证指数", "short": "上证"},
            {"code": "sz399001", "name": "深证成指", "short": "深证"},
            {"code": "sz399006", "name": "创业板指", "short": "创业板"},
            {"code": "sh000688", "name": "科创50",    "short": "科创50"},
        ],
    })


@app.get("/api/datasource/list")
def list_datasource():
    """列出所有可用数据源。"""
    return JSONResponse(content={
        "code": 0,
        "data": {
            "available": DataSourceFactory.list_available(),
            "default": "akshare",
        },
    })


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8310)