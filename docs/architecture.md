# zx-stock 架构说明

> 上证指数等 A 股指数的查看工具复刻 MVP。前后端分离，FastAPI + React + Vite。

---

## 1. 技术栈

### 后端

| 层 | 选型 | 说明 |
|----|------|------|
| 运行时 | Python 3.11+ | 当前开发环境使用 3.14 |
| 包管理 | uv | 锁文件 `server/uv.lock` |
| Web 框架 | FastAPI ≥ 0.115 | REST API |
| ASGI | uvicorn[standard] ≥ 0.30 | 开发期运行：`uv run python -m uvicorn server.main:app --host 127.0.0.1 --port 8310` |
| 数据源（备选） | akshare ≥ 1.16 / pytdx ≥ 1.13 | akshare 用于指数看板（实际实现走 HTTP，详见第 5 节） |
| 跨域 | CORSMiddleware | `allow_origins=["*"]` 仅开发期 |

### 前端

| 层 | 选型 | 说明 |
|----|------|------|
| 框架 | React 19 + TypeScript | StrictMode 已禁用（klinecharts 同一 DOM 重复 init 报错） |
| 构建 | Vite 8 | dev 端口 8210 |
| UI 组件 | antd 6 | Card / Radio.Group / Select / Spin / Tag |
| 图表 | **klinecharts 10** | 金融图表库，主图叠加 MA(20) + 副图 VOL / MACD；MA/MACD 一律前端算 |
| 图表（旧） | recharts 3 | 仍装在 deps，但已不在页面中使用，留作备份 |

> 设计决策细节见 `server/DATASOURCE.md`。

---

## 2. 目录结构

```
zx-stock/
├── docs/
│   ├── architecture.md              # 本文件
│   ├── tqcenter_module_path.md      # 通达信 tqcenter 启动方式
│   └── (server/DATASOURCE.md)       # 数据源设计
├── server/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── DATASOURCE.md
│   └── src/
│       └── server/
│           ├── main.py              # FastAPI 入口
│           └── data_source.py       # 数据源抽象 + 工厂
├── web/
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── pages/
│       │   ├── MarketChart.tsx     # 主页面：K 线 + 顶栏 + 切换器
│       │   └── MarketChart.css
│       ├── pages/StockChart.tsx    # 旧 Recharts 实现（未引用）
│       └── assets/
```

---

## 3. 系统架构图

```
┌─────────────────────┐                  ┌─────────────────────────────────┐
│   Browser (Vite)    │   HTTP / JSON    │   FastAPI (uvicorn :8310)       │
│                     │ ◄──────────────► │                                 │
│   React + antd      │   CORS allowed   │   main.py                       │
│   klinecharts       │                  │   ├─ /api/index/list            │
│   fetch()           │                  │   ├─ /api/index/quote           │
└──────────┬──────────┘                  │   ├─ /api/index/kline           │
           │                             │   └─ /api/datasource/list      │
           │                             │                                 │
           │                             │   data_source.py                │
           │                             │   ├─ DataSourceFactory          │
           │                             │   ├─ AkshareDataSource ◄────┐   │
           │                             │   ├─ TqcenterDataSource     │   │
           │                             │   └─ PytdxDataSource        │   │
           │                             └────────┬────────────────────┘   │
           │                                      │                        │
           │                                      ▼                        │
           │                             ┌─────────────────────────────────┐
           │                             │  外部 HTTP 数据源（实际拉取）   │
           │                             │  ├─ hq.sinajs.cn  （新浪行情）   │
           │                             │  ├─ web.ifzq.gtimg.cn（腾讯K线） │
           │                             │  └─ push2his.eastmoney.com      │
           │                             │      （备选，目前不稳定）       │
           │                             └─────────────────────────────────┘
           │
           │            可选
           ▼
   通达信客户端（Tdx）
   └─ D:/prod-forces/finance/tdx-tq/PYPlugins/user/tqcenter.py
```

---

## 4. 数据流

### 4.1 前端 → 后端

| 触发 | 调用 | 说明 |
|------|------|------|
| 页面加载 / 切换指数 / 切换周期 | `GET /api/index/list?` | 获取支持的指数列表（4 个：上证/深证/创业板/科创50） |
| 每 5 秒轮询 | `GET /api/index/quote?code=sh000001&source=akshare` | 实时点位（open / close / high / low） |
| chart.setSymbol / setPeriod | `GET /api/index/kline?code=sh000001&category=4&count=200&source=akshare` | K 线原始数据 |
| 进入页面 | `GET /api/datasource/list` | 可用数据源列表（用于调试） |

> `category` 编码：4=日K、5=周K、6=月K（前端→后端约定，与腾讯 `m1/m5/...` 不一致，**腾讯 K 线由后端 data_source.py 转换**）。

### 4.2 后端 → 数据源

```
get_index_quote(code)
    └─ _SinaQuoteHelper.fetch(code)   # 新浪 hq.sinajs.cn，HTTP/1.1
         └─ (回退) akshare stock_zh_index_spot_em / _spot  # akshare 1.18+ 经常失效

get_index_kline(code, category, count)
    └─ AkshareDataSource._fetch_raw_kline(code, period, count)
         └─ web.ifzq.gtimg.cn/appstock/app/fqkline/get
            字段: [date, open, close, high, low, volume]
         └─ 后端处理: resample 周/月、映射到 KLineData dataclass
```

---

## 5. 数据源设计

### 5.1 DataSource 抽象

```python
class DataSource(ABC):
    def get_index_quote(market, code) -> Optional[QuoteData]: ...
    def get_index_kline(market, code, category, count) -> List[KLineData]: ...
```

`DataSourceFactory` 提供注册 / 创建 / 列表接口，前端通过 `?source=` 参数切换。

### 5.2 已注册的数据源

| name | 实现 | 状态 | 备注 |
|------|------|------|------|
| `akshare` | `AkshareDataSource` | **默认** | K 线走腾讯、行情走新浪 HTTP；绕过 akshare 的 py_mini_racer 崩溃 |
| `tqcenter` | `TqcenterDataSource` | 可选 | 依赖通达信客户端启动 Python，详见 `docs/tqcenter_module_path.md` |
| `pytdx` | `PytdxDataSource` | 可选 | 直连通达信服务器 |

### 5.3 历史教训

- **akshare 1.18+ 实时接口频繁改版**：`stock_zh_index_spot_em` 等接口字段、签名会变；实时行情改用新浪 `hq.sinajs.cn` 兜底。
- **akshare 1.19 + py_mini_racer + Python 3.14 三方组合崩溃**：`stock_zh_index_daily` 内部依赖 py_mini_racer 执行 JS，CPython 3.14 上 FATAL crash。绕路为直接调腾讯 HTTP。
- **东方财富 `push2his.eastmoney.com` 不稳定**：偶发 `RemoteDisconnected`，加了 Referer + UA 也救不回来。腾讯接口稳定得多。

---

## 6. 前端模块设计

### 6.1 单页架构

当前 `App.tsx` 只挂载 `<MarketChart />`，没有路由。

### 6.2 MarketChart 组件状态

```ts
const [currentIndex, setCurrentIndex]   // 4 个指数之一
const [period, setPeriod]               // 日/周/月 K
const [range, setRange]                 // 近1K/近5K/全部
const [quote, setQuote]                 // 当前点位（轮询）
const [updateTime, setUpdateTime]
const [error, setError]
```

副作用：

- **init useEffect（一次性）**：创建 chart → 注册 MA(20) / VOL / MACD → setDataLoader → setSymbol + setPeriod
- **[currentIndex, period, range]**：触发 `chart.setSymbol` + `chart.setPeriod`，由 klinecharts 内部回调 `getBars` 重新拉数据
- **[currentIndex] quote 轮询**：每 5 秒拉一次 `/api/index/quote`，跟着指数切换

### 6.3 关键模式：ref 镜像 state

```ts
const currentIndexRef = useRef<IndexInfo>(INDICES[0]);
useEffect(() => { currentIndexRef.current = currentIndex; }, [currentIndex]);
```

`setDataLoader` 内的 `getBars` 闭包捕获的是初始化时的 state，用 ref 让它能读到最新值，避免 React 闭包陷阱。

### 6.4 关键技术点

- **MA 指标不显示在独立 pane**：`createIndicator({ ..., paneId: 'candle_pane' })` 显式锁定到主图。
- **MA 线默认虚线**：在 `DARK_THEME.indicator.lines` 中改为 `style: 'solid'` + `dashedValue: [0, 0]`。
- **VOL 用 MA5/10/20 三条**、主图只用 MA20，避免线条重叠混乱。
- **移除 `<StrictMode>`**：klinecharts v10 在同一 DOM 上第二次 init 会报 `has been initialized`。

---

## 7. API 设计

| Method | Path | 参数 | 返回 |
|--------|------|------|------|
| GET | `/api/index/list` | — | `[{code, name, short}, ...]` 4 个指数 |
| GET | `/api/index/quote` | `code`, `source` | `{open, close, high, low}` |
| GET | `/api/index/kline` | `code`, `category` (4/5/6), `count`, `source` | `[{datetime, open, high, low, close, amount}, ...]` |
| GET | `/api/datasource/list` | — | `{available: [...], default: 'akshare'}` |
| GET | `/health` | — | `{status: 'ok'}` |

返回结构统一：

```json
{ "code": 0, "data": <payload> }
// 失败：
{ "code": 1, "error": "<msg>" }  // HTTP 500
```

---

## 8. 部署运行

```bash
# 后端
cd D:\repo\03_pers-proj\zx-stock\server
uv run python -m uvicorn server.main:app --host 127.0.0.1 --port 8310

# 前端
cd D:\repo\03_pers-proj\zx-stock\web
npm install     # 首次
npm run dev     # 端口 8210
```

打开 http://localhost:8210/ 即可看到上证指数 K 线图。

### 生产构建

```bash
cd web && npm run build     # 输出到 dist/
cd server && uv run python -m uvicorn server.main:app --host 0.0.0.0 --port 8310 --workers 4
```

前端可用 nginx 反向代理到后端；CORS 在生产环境应收紧为前端域名。

---

## 9. 已知限制 / TODO

详见仓库根目录 `TODO` 计划（11 项）。当前未做的：
- 路由 / 多页面（首页、条件检索、自选股等）
- 分钟线（1m / 5m / 15m / 30m / 60m）
- 行业板块列表 / 自选股 API
- 主题切换（亮色主题）
- 后端缓存层（避免重复 HTTP）
- 错误 / 空状态 UI 完善

---

## 10. 术语表

| 术语 | 含义 |
|------|------|
| `tqcenter` | 通达信客户端（Tdx）的 Python 插件机制，自动把插件目录加入 `sys.path` |
| `qfq` | 前复权（腾讯接口参数），指数无复权概念但接口一致 |
| `category` | 周期代号（4/5/6/...），与腾讯 m1/m5... 不直接对应 |
| `pane` | klinecharts 中的图表面板；多个 indicator 默认在独立 pane，需 `paneId: 'candle_pane'` 锁定主图 |
| `DataSourceFactory` | 数据源工厂，单模式名 + 注册 / 创建 / 列表 |