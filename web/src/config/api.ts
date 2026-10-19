/**
 * 后端地址与 API 调用。
 *
 * API_BASE 之前在 5 个文件里各写死一份 `'http://localhost:8310'`，改端口要改 5 处，
 * 漏改的表现是「接口静默拿不到数据、图表空白、控制台无报错」——极难排查。
 * 现在统一从这里取。
 *
 * ⚠️ 取值是**空串**（同源相对路径），不是写死的 `localhost:3301`。
 * 原因：这段 JS 是在**用户的浏览器里**执行的，`localhost` 指的是用户自己那台电脑，
 * 不是部署服务器。开发时前后端同机，`localhost:3301` 碰巧成立；一旦部署到服务器，
 * 浏览器就会把请求打到用户自己的电脑上——页面能开、图表空白、控制台只有连接失败。
 *
 * 现在的形态：
 *   - 本地开发：vite 把 `/api`、`/health` 代理到 127.0.0.1:3301（见 vite.config.ts）
 *   - 部署：nginx 同时提供静态文件和 `/api` 反代（生产与开发由不同工具做同一件事）
 * 两套环境共用这份相对路径的代码，不需要分别构建，也不会出现跨域。
 *
 * 开发时后端仍需在本机 3301 端口运行：
 *   cd server; $env:ZX_MYSQL_DB='zx_invest'
 *   .\.venv\Scripts\python.exe -W ignore -m uvicorn server.main:app --host :: --port 3301
 * 注意 `--host ::`：本机前端 vite 绑的是 IPv6 回环，绑 127.0.0.1 会连不上。
 */
export const API_BASE = '';

/** 单次请求最多取多少根。后端 query_klines.count 的硬上限也是这个值（MAX_COUNT）。 */
export const MAX_COUNT = 1000;

/**
 * 首次进入图表加载多少根。500 根 = 日线约 2.1 年。
 * 兼顾三件事：① MA250 年线有 2 倍余量 ② 能看一个完整牛熊周期
 * ③ 首屏请求 gzip 后约 12KB，绘制成本仍是毫秒级。
 */
export const INITIAL_BARS = 500;

/**
 * 每次向左撞到边界后追加多少根。
 * 与 INITIAL_BARS 同值，所以「首屏 500 → 1000 → 1500…」每档正好对得上
 * docs/kline-bar-counts.md 里的档位表。
 */
export const PAGE_BARS = 500;

/**
 * 复权模式。因子不在库里落列，由后端查询时从 ``kline_day.pre_close`` 现算
 * （见 server/src/server/market.py 的 ``Adjustment``）。
 *
 * - ``none`` 不复权，库里原值
 * - ``qfq``  前复权，末根不动，历史往前压
 * - ``hfq``  后复权，上市首日不动，历史往后抬
 */
export type AdjMode = 'none' | 'qfq' | 'hfq';

export const ADJ_MODES: AdjMode[] = ['none', 'qfq', 'hfq'];
export const ADJ_LABELS: Record<AdjMode, string> = {
  none: '不复权',
  qfq: '前复权',
  hfq: '后复权',
};

export interface KLineBar {
  datetime: string;
  open: number;
  high: number;
  low: number;
  close: number;
  /** 成交量（手，1 手 = 100 股）。字段名与库列 `volume` 一致。 */
  volume: number;
  /** 成交额（千元，1 千元 = 1000 元）。字段名取图形库原生的 `turnover`。 */
  turnover: number;
  /**
   * 这个周期里是否发生过除权。由后端按 ``pre_close <> 前一根 close`` 判定（**不落库**）。
   * 翻历史加载时标记随 bar 一起增删，前端不用另行对账。
   *
   * ⚠️ 周期线上判的是「**这一桶里有没有除权日**」，不是「``datetime`` 那天是不是」。
   * 周期 bar 的 ``datetime`` 是桶内最后一个交易日，两者常常不是同一天
   * （恒生电子 23 个除权日里，月线上只有 1 个恰好落在月末）。
   */
  is_xdxr?: boolean;
  /**
   * ``is_xdxr`` 为真时，该周期内**真实**的除权日（YYYY-MM-DD），由后端给出。
   * 必须用它去高亮弹窗里的那一条：标记的位置是桶尾，弹窗的日期是除权日，
   * 前端自己从 timestamp 反推只会拿到桶尾，周线上一条都对不上。
   */
  xdxr_date?: string;
  /**
   * 交易所给的**已除权**昨收，浮窗「昨收」栏直接显示它。
   *
   * ⚠️ 只有**日线**有（`kline_0amv` 没这概念，派生周期那列已删——它恒等于
   * 上一根 bar 的 close，纯冗余）。派生周期恒为 `null`，前端回落到前一根 close。
   *
   * 为什么必须要它：平时 `pre_close` 就等于前一根 close，两者重合、看不出问题；
   * 只有除权日才分叉。恒生电子 2026-06-09 前一根收 22.26、除权参考价 22.06，
   * 用错基准会让「昨收 22.26 / 今价 22.27」和「涨跌幅 +0.95%」自相矛盾
   * （图上现算只有 +0.04%）。
   */
  pre_close?: number | null;
}

export interface KlineResponse {
  code: number;
  data: KLineBar[];
  /** true = 按 end_date 查时更早还有数据、这次被 count 截断了。前端必须读，否则会把截断误当成"就这么多"。 */
  truncated?: boolean;
  error?: string;
}

export interface KlineQuery {
  category: number | string;
  count?: number;
  /** 闭区间下界（含当天），补更新的数据时用。 */
  startDate?: string;
  /** 闭区间上界（含当天），向前翻历史时传「已加载集合里的最早日期」。 */
  endDate?: string;
  /** 复权模式，不传等于 none。 */
  adj?: AdjMode;
}

/**
 * 拉 K 线。区间是**闭区间**，所以相邻两页会有 1 根重叠——
 * 这是刻意的：重复可以在前端去重，而空隙是数据丢失。调用方需按 datetime 去重。
 */
export async function fetchKlines(code: string, q: KlineQuery): Promise<KlineResponse> {
  const p = new URLSearchParams({ code, category: String(q.category) });
  if (q.count) p.set('count', String(q.count));
  if (q.startDate) p.set('start_date', q.startDate);
  if (q.endDate) p.set('end_date', q.endDate);
  if (q.adj) p.set('adj', q.adj);
  const resp = await fetch(`${API_BASE}/api/index/kline?${p.toString()}`);
  return (await resp.json()) as KlineResponse;
}

/** 一条除权日记录（弹窗列表用）。 */
export interface XdxrRecord {
  /** 除权日 YYYY-MM-DD。 */
  date: string;
  /** 「每10股派2.00元，送3.00股」；库里没同步过除权除息时为空串。 */
  text: string;
  /** 除权**前**收盘（未除权）。 */
  pre_close: number;
  /** 库里的 ``pre_close``（已除权）。 */
  adj_pre_close: number;
  /** 截至该日的累积复权因子。 */
  factor: number;
  ratio: number;
  details: Array<{
    category: number | null;
    name: string | null;
    bonus_ratio: number | null;
    allot_ratio: number | null;
    cash_dividend: number | null;
  }>;
}

export interface XdxrResponse {
  code: number;
  data: XdxrRecord[];
  error?: string;
}

/**
 * 某标的的除息除权记录（弹窗列表）。
 *
 * 列表骨架是**真实发生过的除权日**（后端按 pre_close 判定），不是 ``stock_xdxr``
 * 全表——后者混着大量「股本变化 / 送配股上市」，不影响价格，混进来每条都点不开。
 */
export async function fetchXdxr(code: string): Promise<XdxrResponse> {
  const resp = await fetch(`${API_BASE}/api/stock/xdxr?code=${encodeURIComponent(code)}`);
  return (await resp.json()) as XdxrResponse;
}

/**
 * 行情列表的一行。``name`` 可能为 null——``stock_info`` 表元信息缺失时后端不编造。
 *
 * 指数 / 指标名录行（见 StockRow.is_index）**没有行情数据**：
 * ``index_info`` 表只有名录列，所以行情相关的列一律为 null，前端显示「—」。
 */
export interface StockRow {
  code: string;
  name: string | null;
  /** pytdx 市场常量：0=深 1=沪（与后端 ``stock_info.market`` 同一套）。指数行为 null。 */
  market: number | null;
  industry_code: number | null;
  /** 指数行没有行情数据，这两列为 null。 */
  last_date: string | null;
  first_date: string | null;
  bar_count: number;
  /** 指数行为 null——``index_info`` 是名录表，没有最新价。 */
  close: number | null;
  open: number | null;
  /** 前一交易日收盘。只有一根数据、或指数行时为 null。 */
  prev_close: number | null;
  change: number | null;
  change_pct: number | null;
  /** 名录行的更新时间（ISO 字符串），来自 ``stock_info.updated_at`` /
   *  ``index_info.updated_at``。
   *  ⚠️ 这是**名录**的更新时间，不是行情的同步时间——kline_day / kline_0amv
   *  两张表没有 updated_at 列，所以"这根 K 线什么时候写进来的"目前查不到。 */
  updated_at: string | null;
  /** true = 来自 ``index_info`` 名录（指数 / 指标）；false = 有真实行情的标的。 */
  is_index: boolean;
}

export interface StockListResponse {
  code: number;
  data: StockRow[];
  error?: string;
}

/** 行情列表：库里已有行情数据的全部标的。 */
export async function fetchStockList(): Promise<StockListResponse> {
  const resp = await fetch(`${API_BASE}/api/stock/list`);
  return (await resp.json()) as StockListResponse;
}

/**
 * 前端显示设置的持久化值（后端 ``app_config`` 表）。
 *
 * ⚠️ **没配过的键不会出现在返回值里**。默认值由前端自己兜底，
 * 后端不存第二份——两处真相迟早会漂移。
 */
export interface AppConfig {
  /** 主图现价线是否显示。缺省 = true（与 klinecharts 内建默认一致）。 */
  show_price_line?: boolean;
  /** 横线距上比例 = 主图 pane 高 ÷ 可分配高度。缺省 = 0.42。 */
  top_ratio?: number;
  /** 竖线距右比例 = 右列宽 ÷ chart-row 宽。缺省 = 0.225。 */
  right_ratio?: number;
  [key: string]: unknown;
}

export async function fetchConfig(): Promise<AppConfig> {
  const resp = await fetch(`${API_BASE}/api/config`);
  const json = (await resp.json()) as { code: number; data?: AppConfig; error?: string };
  if (json.code !== 0) throw new Error(json.error || '配置读取失败');
  return json.data ?? {};
}

/** 写入设置。后端只 upsert 传进来的键，不会删掉没传的。 */
export async function saveConfig(cfg: AppConfig): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  });
  const json = (await resp.json()) as { code: number; error?: string };
  if (json.code !== 0) throw new Error(json.error || '配置保存失败');
}