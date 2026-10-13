import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import type { Chart, Coordinate, IndicatorFigure, KLineData as KCKLineData } from 'klinecharts';
import { init, dispose } from 'klinecharts';
import { INDICES, isDailyOnly, type IndexInfo } from '../utils/indices';
import {
  PERIOD_OPTIONS,
  DEFAULT_BAR_COUNT,
  type AdjustType,
  type PeriodOption,
} from '../utils/chartOptions';
import ChipDistribution from '../components/ChipDistribution';
import CrosshairTooltip from '../components/CrosshairTooltip';
import ChartFrame from '../components/ChartFrame';
import RegionStatsModal from '../components/RegionStatsModal';
import XdxrModal from '../components/XdxrModal';
import { SymbolInfo, ChartTools } from '../components/ChartHeader';
import {
  registerExDividendOverlay,
  renderExDividendMarkers,
  clearExDividendMarkers,
  setExDividendClickHandler,
} from '../utils/exDividendMarkers';
import {
  DEFAULT_RATIO,
  ratioFromConfig,
  ratioToConfig,
  subPaneHeight,
  subChartsTotalHeight,
  X_AXIS_H,
  type LayoutRatio,
} from '../utils/layout';
import { formatBarDate } from '../utils/formatDate';
import './MarketChart.css';
import { API_BASE, INITIAL_BARS, PAGE_BARS, fetchConfig, fetchKlines, saveConfig } from '../config/api';

interface ApiKLine {
  datetime: string;
  open: number;
  high: number;
  low: number;
  close: number;
  /** 成交量（手 / 0AMV 的点）。字段名与库列 `volume` 一致。 */
  volume: number;
  /** 成交额（千元 / 0AMV 的点）。字段名取图形库原生的 `turnover`，能直接透传。 */
  turnover: number;
  /** 涨跌额。日线来自 Tushare 官方 change（**除权口径**），派生周期来自
   *  kline_period 物化（**原始口径**，与该周期 close 同源，2026-10-13 起）。
   *  两种模式的差异见 docs/formulas.md §6。 */
  chg_amt: number | null;
  /** 涨跌幅 %，基准同 chg_amt。第一个周期为 null。 */
  chg_pct: number | null;
  /** 当天是否除权日。后端按 pre_close <> 前一根 close 判定，不落库。 */
  is_xdxr?: boolean;
}

/** `/api/index/quote` 返回值（组装逻辑见 server/src/server/quote.py）。
 *  实时快照与库内回落共用同一结构，只在溯源字段上有差异。 */
interface QuoteData {
  open: number;
  close: number;
  high: number;
  low: number;
  /** 昨收：涨跌幅的唯一基准。实时取通达信快照 last_close，库内取前一根 K 线收盘。 */
  prev_close: number;
  change: number;
  change_pct: number;
  /** 'tdx-live' = 实时快照；'db' = 回落到库内最后一根。 */
  source?: 'tdx-live' | 'db';
  /** 实时路径才有：通达信服务器本地时间 HH:MM:SS。 */
  server_time?: string;
  /** 库内路径才有：交易日（实时快照不带交易日，不编造）。 */
  datetime?: string;
}

const DARK_THEME = {
  background: { color: '#22272e' },
  grid: {
    show: true,
    horizontal: { show: true, color: '#373e47' },
    vertical: { show: true, color: '#373e47' },
  },
  // y 轴：刻度文字和轴线统一灰，跟 separator（同色 #545d68，主副图分界线）保持一致。
  // 默认 klinecharts 文字颜色太亮，"8" "4" 这些数字会喧宾夺主。
  yAxis: {
    show: true,
    size: 'auto',
    axisLine: { show: true, color: '#545d68', size: 1 },
    tickLine: { show: true, size: 1, length: 3, color: '#545d68' },
    tickText: {
      show: true,
      color: '#545d68',
      size: 12,
      family: 'Consolas, Monaco, monospace',
      weight: 'normal',
      marginStart: 4,
      marginEnd: 4,
    },
  },
  xAxis: {
    show: true,
    size: 'auto',
    axisLine: { show: true, color: '#545d68', size: 1 },
    tickLine: { show: true, size: 1, length: 3, color: '#545d68' },
    tickText: {
      show: true,
      color: '#545d68',
      size: 12,
      family: 'Consolas, Monaco, monospace',
      weight: 'normal',
      marginStart: 4,
      marginEnd: 4,
    },
  },
  separator: {
    color: '#545d68',
    size: 1,
    fill: false,
    activeBackgroundColor: 'transparent',
  },
  candle: {
    // 阳线空心、阴线实心。klinecharts v10 的蜡烛形态由这一个字段决定，
    // 可选 candle_solid（全实心，内建默认）/ candle_stroke（全空心）/
    // candle_up_stroke（阳线空心）/ candle_down_stroke（阴线空心）/ ohlc / area。
    // 绘制分派见 index.esm.js:6568-6594，其中 candle_up_stroke 是
    // `close > open` 走 _createStrokeBar（只描边框），否则 _createSolidBar。
    // 阴线仍实心，且边框色 = 填充色（upBorderColor 与 upColor 同值），
    // 所以空心阳线看上去就是一根红色的空心矩形。
    type: 'candle_up_stroke',
    bar: {
      upColor: '#ef5350',
      downColor: '#26a69a',
      noChangeColor: '#888888',
      upBorderColor: '#ef5350',
      downBorderColor: '#26a69a',
      noChangeBorderColor: '#888888',
      upWickColor: '#ef5350',
      downWickColor: '#26a69a',
      noChangeWickColor: '#888888',
    },
    // 关掉 klinecharts 自带的 candle tooltip（默认含红框 `{ticker} · {period}` +
    // 蓝框 `Time: ... Open: ...`）。我们自己在 React 里渲染一个悬浮窗口，
    // 既能把红框挪到证券名前面，也能让蓝框根据十字线位置自适应左右。
    tooltip: {
      showRule: 'none',
    },
    priceMark: {
      last: {
        // 现价线的颜色**不读 line.color**。klinecharts 在类型层面就把 color 从
        // 现价线的 line style 里剔掉了（`CandleLastPriceMarkLineStyle =
        // Omit<StateLineStyle, 'color'>`），绘制时按 compareRule 在
        // upColor / downColor / noChangeColor 三个里三选一：
        // 画线 index.esm.js:6859-6867、画右侧标签底色 9645-9653。
        // 所以下面 line 里**不要**再写 color，写了也不生效（曾经白写了一条）。
        //
        // 三个槽全给同一个蓝：现价线表达的是"最新收盘价在哪"，
        // 不承载涨跌语义——涨红跌绿是蜡烛和浮窗在表达的事，
        // 这里再染一遍红绿只会和蜡烛打架（曾经用内建默认 upColor=绿 /
        // downColor=红，结果同一张图里"跌的蜡烛是绿的、现价线却是红的"）。
        // 线色和右侧标签底色取的是同一个变量，所以三个都给蓝就整条统一了。
        // #1677ff 是本项目既有的强调蓝（indicator 第三条线、focus 态同色）。
        upColor: '#1677ff',
        downColor: '#1677ff',
        noChangeColor: '#1677ff',
        line: { dashedValue: [4, 4], size: 1 },
        text: { color: '#fff', size: 12 },
      },
    },
  },
  indicator: {
    tooltip: {
      text: { color: '#adbac7' },
      custom: [
        { title: '时间', value: '{time}' },
        { title: '开', value: '{open}' },
        { title: '收', value: '{close}' },
        { title: '高', value: '{high}' },
        { title: '低', value: '{low}' },
        { title: '量', value: '{volume}' },
      ],
    },
    lines: [
      { style: 'solid', smooth: false, size: 1, dashedValue: [0, 0], color: '#FFA500' },
      { style: 'solid', smooth: false, size: 1, dashedValue: [0, 0], color: '#935EBD' },
      { style: 'solid', smooth: false, size: 1, dashedValue: [0, 0], color: '#1677FF' },
    ],
  },
};

interface Props {
  /** 受控当前指数（可选）；不传则组件内自管 */
  currentIndex?: IndexInfo;
  /** 当前指数变化回调（受控模式必填） */
  onCurrentIndexChange?: (idx: IndexInfo) => void;
  /** 是否隐藏切换器（Watchlist 页面不需要切换） */
  hideSwitcher?: boolean;
}

/** 回调 more 用：两侧都没有更多数据了。 */
const NO_MORE = { backward: false, forward: false };

/** 主图（蜡烛图）所在 pane 的 id。klinecharts 里的 PaneId.CANDLE 就是这个字符串，
 *  见 node_modules/klinecharts/dist/index.esm.js:824。
 *  它同时是 klinecharts 分配 pane 高度时的 flexible pane —— 吸收余数的那个，
 *  所以它的 options.height 会被静默忽略（见 applySubHeight 的注释）。 */
const CANDLE_PANE_ID = 'candle_pane';

/** 左键框选出来的矩形，容器（.kline-container）坐标，y 已向下翻正。 */
type SelRect = { x: number; y: number; w: number; h: number };
/** 框选成立所需的最小位移（px）。小于它就算左键单击，不留框。 */
const MIN_SEL = 5;
/** 「区域放大」的最小根数。太少的 K 线放大了也是一片竖线，不如不动。 */
const MIN_ZOOM_BARS = 10;
/** 框选菜单的尺寸（px）。只用来判断贴边时往哪边翻，不参与布局。 */
const SEL_MENU_W = 74;
const SEL_MENU_H = 58;
/** chart 还没初始化时喂给「区域统计」弹窗的占位空数组。用模块级常量而不是
 *  每次渲染 `?? []`：后者每次都是新引用，会把弹窗里的 useMemo 打掉重算。 */
const NO_BARS: KCKLineData[] = [];

/** MACD 一根的计算结果字段（klinecharts 内建 calc 产出这三个）。 */
interface MacdPoint {
  dif: number;
  dea: number;
  macd: number;
}

/** 取 MACD 柱子的配色：优先 indicator.styles 上的覆盖，没有就用内建默认。
 *  klinecharts 内部有个同名 formatValue 做这件事，但它没导出，这里手写一份。
 *  两个入参都走 unknown：indicator.styles 的类型是 `DeepPartial<IndicatorStyle>`
 *  （bars 里的字段全是可选），直接按 IndicatorStyle 索引会报类型不兼容。 */
function macdBarColor(indicator: unknown, defaultStyles: unknown, key: string): string {
  const fromIndicator = ((indicator as { styles?: { bars?: unknown[] } | null } | null)?.styles?.bars?.[0] ??
    {}) as Record<string, unknown>;
  const v = fromIndicator[key];
  if (typeof v === 'string') return v;
  const fromDefault = ((defaultStyles as { bars?: unknown[] } | null)?.bars?.[0] ?? {}) as Record<string, unknown>;
  const dv = fromDefault[key];
  // 两者都拿不到才会走到这里；给个中性灰而不是空串——空串在 canvas 上等于
  // 「用默认画笔色」（黑），压在深色副图上直接看不见。
  return typeof dv === 'string' ? dv : '#768390';
}

/**
 * MACD 副图的 figures：把柱子改成「固定 1px 的细柱，且不区分实心空心」。
 *
 *  klinecharts 内建 MACD 的柱子有两个特点跟常见行情软件不一样（对比用户给的参考图）：
 *    ① **柱宽默认 = `halfGapBar * 2`**，即正好一个 barSpace（index.esm.js:8129）。
 *       K 线一放大就变成一排粗柱子，把 DIF / DEA 两条线压得几乎看不见。
 *       靠 `figure.attrs` 返回 `{ width }` 覆盖（8129 行 `attrs?.width ?? halfGapBar * 2`）。
 *    ② **区分实心空心**：`prevMacd < currentMacd ? 'stroke' : 'fill'`（4216 行），
 *       即柱值在放大时画空心、缩小时画实心。常见软件一律实心，这里恒定返回 'fill'。
 *
 *  DIF / DEA 两条线原样保留（key / title / type 照抄内建，4213-4214 行）。
 *  配色也仍走 klinecharts 默认，不在这里统一成蜡烛那套红绿。
 */
const MACD_FIGURES: IndicatorFigure<MacdPoint>[] = [
  { key: 'dif', title: 'DIF: ', type: 'line' },
  { key: 'dea', title: 'DEA: ', type: 'line' },
  {
    key: 'macd',
    title: 'MACD: ',
    type: 'bar',
    baseValue: 0,
    /** 固定 1px：柱子细成一条竖线，宽窄不再随 barSpace 变化。 */
    attrs: () => ({ width: 1 }),
    styles: ({ data, indicator, defaultStyles }) => {
      const macd = data.current?.macd;
      const color =
        typeof macd === 'number' && macd > 0
          ? macdBarColor(indicator, defaultStyles, 'upColor')
          : typeof macd === 'number' && macd < 0
            ? macdBarColor(indicator, defaultStyles, 'downColor')
            : macdBarColor(indicator, defaultStyles, 'noChangeColor');
      return { style: 'fill', color, borderColor: color };
    },
  },
];

/** klinecharts 没在 index.d.ts 里暴露的那部分 chart store。
 *
 *  `setCrosshair` 是 v10 里**唯一**能程序化挪十字线的入口：Chart 公开接口只有
 *  `executeAction('onCrosshairChange', …)`（那是往外发事件，不改状态），
 *  真正写状态的是 StoreImp.setCrosshair，见 index.esm.js:14065。
 *  签名和内部约束（都在那儿有注释）：
 *    ① 只在 prev.x/y/paneId 跟新值不同，或传了 forceInvalidate 时才重绘 + 发事件；
 *    ② paneId 必须是字符串，否则 onCrosshairChange 根本不会触发；
 *    ③ x 与 dataIndexToCoordinate() 同一坐标系；y 则是**该 pane 内的局部坐标**。 */
type ChartStoreLike = {
  getCrosshair?: () =>
    | {
        x?: number;
        y?: number;
        paneId?: string;
        kLineData?: KCKLineData;
        dataIndex?: number;
        realDataIndex?: number;
      }
    | undefined;
  getBarSpace?: () => { bar?: number; halfBar?: number };
  dataIndexToCoordinate?: (index: number) => number;
  /** 像素 x → dataIndex。上面 dataIndexToCoordinate 的严格逆运算（13890 行）。 */
  coordinateToDataIndex?: (x: number) => number;
  /** **整体替换**数据。v10 的 Chart 接口没有 setData（只有 resetData，会重发 init
   *  走一遍 DataLoader），替换数据的唯一入口就是这个私有方法（13439 行）。
   *  type='init' 分支做的是旧版 setData 的事：_clearData → 赋 _dataList →
   *  记 more → 复位 offsetRightDistance → 重算全部指标 → 重绘（13501-13516）。
   *  配套的 more 要传 {backward:false, forward:false}，否则 _adjustVisibleRange
   *  看到视口顶到两端会立刻又发一轮 DataLoader 请求。 */
  _addData?: (data: KCKLineData[], type: 'init', more: { backward: boolean; forward: boolean }) => void;
  /** 绘图区（不含右侧 y 轴）的像素宽度。dataIndexToCoordinate 直接拿它当基准
   *  （13888 行 `_totalBarSpace - (deltaFromRight - 0.5) * barSpace`），
   *  是唯一能准确算出"每根该多宽才铺满"的量。 */
  _totalBarSpace?: number;
  /** 右侧留白距离（px）。它同时决定视口位置：diff = distance / barSpace，
   *  diff=0 即"最后一根贴右边缘"。isUpdate 传 true 才会重绘（13692-13705）。 */
  setOffsetRightDistance?: (distance: number, isUpdate?: boolean) => void;
  setCrosshair?: (crosshair?: { x?: number; y?: number; paneId?: string }) => void;
};

function getChartStore(chart: Chart | null): ChartStoreLike | undefined {
  return (chart as unknown as { getChartStore?: () => ChartStoreLike } | null)?.getChartStore?.();
}

/** 开关主图现价线（candle.priceMark.last）。
 *
 *  走 ChartImp.setStyles 而不直接改 store._styles：这个 setter 内部是
 *  `_setOptions(() => store.setStyles(value))`，会顺带把依赖样式的量重新算一遍
 *  （index.esm.js:15101-15106）。直接改 store 的话还得自己 chart.resize() 兜底。
 *  Chart 接口没声明它、只声明了 Store.setStyles，所以要断言一下。
 *
 *  底层 merge 是**递归深合并**（`merge(target, source)` 里两个都是对象就继续往下
 *  merge，见 index.esm.js:157-174），所以只写要改的 `show` 一个字段即可，
 *  不会被库里的默认值把 line / text 样式打回去。 */
function setLastPriceMarkVisible(chart: Chart, show: boolean) {
  (chart as unknown as { setStyles: (v: unknown) => void }).setStyles({
    candle: { priceMark: { last: { show } } },
  });
}

/** 把 count 根 K 线铺满整个绘图区所需的 barSpace。
 *
 *  优先直接读 store 的 `_totalBarSpace`（绘图区像素宽度，dataIndexToCoordinate
 *  的基准）。**不能**改用「可视根数 × 当前 barSpace」反推：getVisibleRange()
 *  的 to 会越界（实测数据 200 根时 to=200，最大合法下标才 199），
 *  算出来的可视根数偏大，barSpace 就跟着偏大 —— 32 根 × 26.69 = 854px
 *  比绘图区 847px 还宽，多出的 7px 正好把第 0 根挤出视口。
 *
 *  必须给一点余量：正好铺满时最后一根的右缘会压在 y 轴刻度上。
 *  setBarSpace 有 layoutOptions.barSpaceLimit 上下限钳制（13667 行），
 *  越界会被静默忽略，所以先夹一下。 */
function barSpaceToFill(chart: Chart, count: number): number | null {
  if (count <= 0) return null;
  const store = getChartStore(chart);
  const totalSpace = store?._totalBarSpace;
  if (typeof totalSpace === 'number' && totalSpace > 0) {
    return Math.max(1.5, totalSpace / count - 1);
  }
  // 兜底：没有 _totalBarSpace 时退回可视根数反推（差 1~2 根，只是不那么准）
  const bar = store?.getBarSpace?.()?.bar;
  if (!bar || !Number.isFinite(bar)) return null;
  const range = chart.getVisibleRange();
  const visibleCount = Math.max(1, range.to - range.from + 1);
  return Math.max(1.5, (visibleCount * bar) / count - 1);
}

/** 把某个 dataIndex 对应的 K 线收盘价换算成**主图 pane 内**的像素 y。
 *
 *  方向键移动时用。crosshair.y 是 pane 局部坐标（鼠标那套传进来的就是
 *  `_makeWidgetEvent` 减掉 bounding.top 之后的值），klinecharts 画横线时直接
 *  拿它当 pane 内像素用，没有二次换算，见 index.esm.js:7870-7874。
 *  所以不能拿「容器高度一半」糊弄——主图只占顶部一条，那样线会画到副图区域
 *  甚至直接画到画布外看不见。
 *
 *  取收盘价反算是稳的：主图 y 值域是按可视区那批 K 线的高低点算出来的，
 *  close 必然落在 [low, high] 之内，任何缩放/滚动下都不会算出画布外的值。 */
function candlePaneY(chart: Chart, dataIndex: number): number | null {
  const bar = chart.getDataList()[dataIndex];
  if (!bar) return null;
  const pixel = chart.convertToPixel({ value: bar.close }, { paneId: CANDLE_PANE_ID }) as
    | Partial<Coordinate>
    | undefined;
  const y = pixel?.y;
  return typeof y === 'number' && Number.isFinite(y) ? y : null;
}

// 2026-10-10 删掉了 HistoryState / loadedCount 和 .kline-history-hint 覆盖层。
// 它是 position:absolute 钉在图内左上角的一条「已加载 N 根，已到最早的历史数据」，
// 而左上角正是主图指标 legend（VOL / MA5 / MA10 / MA20）的地盘，直接盖住。
// 「往左拖不动了」这个信息改由 X 轴日期范围本身表达，不再叠一层浮字。

export default function MarketChart(props: Props) {
  const controlledMode = !!props.currentIndex;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<Chart | null>(null);
  const requestIdRef = useRef(0);
  const fetchQuoteTimerRef = useRef<number | undefined>(undefined);

  // 镜像 ref，让 getBars 闭包读到最新值
  const currentIndexRef = useRef<IndexInfo>(INDICES[0]);
  const periodRef = useRef<PeriodOption>(PERIOD_OPTIONS[0]);
  const adjustRef = useRef<AdjustType>('none');

  // ---- 向左翻历史的游标 ----
  // 必须用 ref 而不是 state：getBars 里要在**同一次调用**中读到刚写进去的游标，
  // 才能算出下一轮的 end_date。state 的写入要等下一次渲染才可见，连续两次
  // backward 会拿到同一个旧游标 → 反复请求同一页 → 图不动但后端一直在被打。
  //
  // earliestRef 存的是**已加载集合里最小的 datetime**（合并后的全局最小值）。
  // 千万别写成"最后一次返回数组的第 0 项"——那只是这一页的起点，
  // 往回翻时它会随页面下移，一旦写成单页起点，下一轮 end_date 就等于上一轮起点，
  // 永远卡在同一页。
  const earliestRef = useRef<string | null>(null);

  // 库里才有的涨跌两列（klinecharts 不认，按 timestamp 另存）。
  // 换标的/换周期时必须清空：不同标的的 timestamp 虽然不撞，
  // 但留着旧条目只会让「查不到」和「查到旧值」两种情况混在一起，难排查。
  const extraRef = useRef<Map<number, ExtraBars>>(new Map());

  // 已加载范围内所有除权日的 timestamp，**跨翻页累计**。
  // 用 timestamp 而不是日期字符串：图表数据本身就是按 timestamp 索引的，
  // 标记重建时可以直接 set.has(dataList[i].timestamp)，不用再做一次日期换算。
  const exDatesRef = useRef<Map<number, string>>(new Map());
  // 除权记录弹窗：开关 + 本次点中的除权日（null = 关着）。
  const [xdxrOpen, setXdxrOpen] = useState(false);
  const [xdxrActive, setXdxrActive] = useState<string | null>(null);

  // 浮窗当前是不是显示着。用它做「上升沿」判断：只在浮窗**刚出现**的那一刻
  // 把焦点抢到图表上，而不是每次十字线移动都抢。
  // 区别在于：浮窗显示期间用户 Tab 去了别处，鼠标又在图里轻微挪一下——
  // 那种情况不该把焦点抢回来（人已经明确去干别的事了）。
  const tooltipShownRef = useRef(false);

  // ---- 左键框选（区域选择）----
  //
  // 松手后确定下来的矩形，容器坐标。同时决定右下角菜单显不显示。
  const [selRect, setSelRect] = useState<SelRect | null>(null);
  // 拖动过程中的矩形 + 其 DOM 节点。高频更新直接改 DOM style，不进 React：
  // 每次 mousemove 都 setState 会把整棵组件树重渲染一遍，拖起来明显发涩。
  const selBoxRef = useRef<HTMLDivElement | null>(null);
  const selMenuRef = useRef<HTMLDivElement | null>(null);
  const selDragRef = useRef<{ x0: number; y0: number; x: number; y: number } | null>(null);
  // 框选矩形换算出来的 dataIndex 区间（闭区间）。菜单点「区域放大」时用它切片。
  const selRangeRef = useRef<{ from: number; to: number } | null>(null);
  // 区域统计弹窗的开关 + 打开那一刻的框选区间。
  // 区间存成 state 而不是只留在 selRangeRef：弹窗的数据由它算，
  // 而 ref 改动不触发重渲染。真正打开弹窗时把 ref 里的值快照过来。
  const [statsRange, setStatsRange] = useState<{ from: number; to: number } | null>(null);
  // 撤掉框选和菜单。矩形和区间必须一起清 —— 只清 state 的话，
  // selRangeRef 里会留着一个已经看不见的旧区间，下次框选若换算失败就被它顶用。
  const clearSelection = () => {
    selRangeRef.current = null;
    setSelRect(null);
  };
  // 区域放大不再需要任何跨状态：它只调 barSpace 和视口，数据一根不动，
  // dataIndex 空间也就没变过。原来记的 zoomBase / zoomedRef 都是为
  // 「把数据替换成切片」服务的，数据不换了它们就没有存在意义。

  // 受控/非受控双轨：受控时完全用 props 推动换；非受控时用 state
  const [internalIndex] = useState<IndexInfo>(INDICES[0]);
  const currentIndex = controlledMode ? props.currentIndex! : internalIndex;

  const [quote, setQuote] = useState<QuoteData | null>(null);
  /** 头部报价的数据源：K 线**最新一根** + 它的前一根收盘。
   *
   *  为什么不直接用 `quote.close`：quote 是通达信实时快照，跟图上最后一根不是
   *  同一回事（2026-10-08 实测：快照 18.83，图上 09-30 收盘 19.40），导致头部
   *  和图表、浮窗三处数字互相打架。头部既然是"这张图现在长什么样"的摘要，
   *  就该跟图同源。
   *
   *  只有 init（首屏/切标的/切周期）那一次请求需要更新：往回翻页拿到的是
   *  更早的 K 线，末两根永远不变。 */
  const [lastBar, setLastBar] = useState<{ close: number; prevClose: number; datetime: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState<PeriodOption>(PERIOD_OPTIONS[0]);
  // 0AMV 只有日线（独立表 kline_0amv 无 period 列、不参与物化）。
  // 切到这类标的时把周期强制拉回日线——**光禁用下拉框不够**：
  // 受控模式（Watchlist 传 currentIndex）会带着非日线的 period 进来，
  // 后端 market.query_klines 对它直接抛 ValueError -> HTTP 500。
  // 这里兜住所有入口，比在 UI 层逐个禁用可靠。
  useEffect(() => {
    if (isDailyOnly(currentIndex.code) && period.period.type !== 'day') {
      setPeriod(PERIOD_OPTIONS[0]);
    }
  }, [currentIndex.code, period.period.type]);
  // 复权方式：默认"不复权"，与后端库内原值一致。
  // 后端 /api/index/kline 已支持（query 参数 adj），因子查询时从 pre_close 现算。
  // 切换会走下面那个 effect 的 setSymbol → resetData → 重发 init，
  // 而 adjustRef 在**更早**声明的 effect 里已经更新（effect 按声明顺序跑），
  // 所以 getBars 读到的一定是新值。
  const [adjust, setAdjust] = useState<AdjustType>('none');
  // 版面比例：两条分界线的位置（横线 = 主图/副图，竖线 = K 线/右列）。
  // 持久化在后端 app_config（top_ratio / right_ratio），首帧先用默认值，
  // fetchConfig 回来后再覆盖 —— 见下面读设置那个 effect。
  const [ratio, setRatio] = useState<LayoutRatio>(DEFAULT_RATIO);
  // K 线区实测尺寸。比例 → 像素要用它换算，同时供设置弹窗显示实际生效值。
  const [areaSize, setAreaSize] = useState({ h: 0, w: 0 });
  // 主图右侧价格 y 轴的宽度，klinecharts 运行时算出来的。
  // overlay 要靠它让开刻度，否则齿轮会压在价格上。
  const [yAxisW, setYAxisW] = useState(0);
  // 主图现价线（最后一根收盘价那条横线 + 右侧价格标签）是否显示。
  //
  // 默认值只存在**这一份**，后端 app_config 表里没有这一行 = 用户没改过 = true。
  // （库里存的是 'true'/'false' 字符串，后端读出来已经是布尔，见 app_config._parse。）
  const [showPriceLine, setShowPriceLine] = useState(true);
  // 配置还在路上时先别动 chart：用户存的是 false 的话，
  // 先按默认 true 画一帧再关掉会闪一下。拿到值再一次性应用。
  const configLoadedRef = useRef(false);
  // 主图十字线位置：
  //   - 有值（非 null） → 用 [0, index] 的 K 线算历史筹码分布（成本分布演化）
  //   - null           → 鼠标不在图表上，筹码图默认显示"最新一个交易日"
  const [crosshairIndex, setCrosshairIndex] = useState<number | null>(null);
  // 主图悬浮 OHLC 浮窗：null = 隐藏
  // prevClose = 前一根 K 线的收盘（昨收），浮窗拿它算涨跌并给每行定红绿；
  // 翻到最早一根时没有前一根，为 null，此时浮窗不做涨跌配色。
  const [tooltipData, setTooltipData] = useState<
    {
      kLine: KCKLineData;
      prevClose: number | null;
      extra?: ExtraBars;
      x: number;
      containerWidth: number;
    } | null
  >(null);
  const [error, setError] = useState<string | null>(null);

  // 同步 ref
  useEffect(() => { currentIndexRef.current = currentIndex; }, [currentIndex]);
  useEffect(() => { periodRef.current = period; }, [period]);
  useEffect(() => { adjustRef.current = adjust; }, [adjust]);

  // 现价线开关落到 klinecharts 样式上。
  // 必须挂在 chart 初始化 effect **之后**（effect 按声明顺序执行，chartRef 在上面
  // 那个 effect 里才被赋值），这里返回的 chart 已经存在。setStyles 内部会重画，
  // 不用自己再 chart.resize()。
  //
  // configLoadedRef 兜住首次挂载：那会儿 showPriceLine 还是默认值，
  // 用户存的可能是 false，这时候画一帧再关掉会闪。等 fetchConfig 回来后由下面
  // 那个 effect 统一补一次。
  useEffect(() => {
    if (!configLoadedRef.current) return;
    const chart = chartRef.current;
    if (!chart) return;
    setLastPriceMarkVisible(chart, showPriceLine);
  }, [showPriceLine]);

  /** 设置弹窗勾选框变化 → 立刻落到后端。 */
  const onShowPriceLineChange = (v: boolean) => {
    setShowPriceLine(v);
    saveConfig({ show_price_line: v }).catch(() => {
      // 存失败不回滚界面：这是个锦上添花的持久化，
      // 因为它失败就让用户的操作看着"没生效"反而更糟。
    });
  };

  /* ─────────────── 版面比例：比例 → 像素 ─────────────── */

  // K 线区实测高度，供 applySubHeight / ResizeObserver 共用。
  const areaHRef = useRef(0);
  const subPaneHeightRef = useRef(0);
  const ratioRef = useRef<LayoutRatio>(ratio);
  /** 上次真正写进 chart 的副图高，用来挡 resize 回环 */
  const lastAppliedRef = useRef(0);

  /** 把副图高写进 klinecharts。
   *
   * 只能设副图，不能设主图：klinecharts 的 pane 分配规则
   * （dist/index.esm.js:14804）把 candle_pane 选成 flexible pane 吸收余数，
   * 主图的 options.height 会被静默忽略。横线位置完全由副图高决定。
   *
   * 同值 <1px 直接跳过：setPaneOptions 会触发重排 → 容器 resize →
   * ResizeObserver 再算一遍，不设阈值就成回环。
   *
   * chart 还没建好时只记值不写 —— 但**不能**在这里就 update lastAppliedRef，
   * 否则 init effect 里第一次真正建 chart 的那次调用会被阈值挡掉。
   */
  const applySubHeight = useCallback((px: number) => {
    subPaneHeightRef.current = px;
    const chart = chartRef.current;
    if (!chart) return;
    if (lastAppliedRef.current !== 0 && Math.abs(px - lastAppliedRef.current) < 1) return;
    lastAppliedRef.current = px;
    for (const ind of chart.getIndicators()) {
      if (ind.paneId === CANDLE_PANE_ID) continue;
      chart.setPaneOptions({ id: ind.paneId, height: px });
    }
  }, []);

  /** 比例变了：立刻应用到 klinecharts（拖动中每帧都要跟手）。不落库。 */
  const applyRatio = useCallback((next: LayoutRatio) => {
    ratioRef.current = next;
    setRatio(next);
    if (areaHRef.current > 0) {
      applySubHeight(subPaneHeight(areaHRef.current, next.topRatio));
    }
  }, [applySubHeight]);

  /** Slider 松手：写库（存的是收敛到 3 位小数的值）。 */
  const handleRatioCommit = useCallback((next: LayoutRatio) => {
    applyRatio(next);
    saveConfig(ratioToConfig(next)).catch(() => {
      // 存失败不回滚界面：版面已经按新比例画对了，为一个锦上添花的持久化
      // 让用户看着"没生效"反而更糟。刷新后会回到库里那个旧值。
    });
  }, [applyRatio]);

  /* ─────────────── 容器尺寸：比例的换算基准 ─────────────── */

  useEffect(() => {
    const el = containerRef.current;
    // 宽度基准是 .chart-row（= K 线区 + 右列），**不能**用 K 线区自己的宽度：
    // 右列宽是从 row 宽 × 比例算出来的，若拿 K 线区宽去算就成了循环引用 ——
    // 2026-10-12 实测踩过：设 22.5% 结果右列只占 18.4%，且 kline 每缩一次比例再偏一点。
    const row = el?.closest('.chart-row') as HTMLElement | null;
    if (!el || !row) return;
    let raf = 0;

    const measure = () => {
      raf = 0;
      const h = el.clientHeight;
      const w = row.clientWidth;
      if (h <= 0 || w <= 0) return;
      areaHRef.current = h;

      // 右侧价格 y 轴宽度 = 容器右边缘 − 主图绘图区 canvas 右边缘。
      // 取最靠上那组 canvas 里最宽的一个（klinecharts 每个 pane 有多层 canvas）。
      let axis = 0;
      const canvases = el.querySelectorAll('canvas');
      if (canvases.length) {
        let top = Infinity;
        let topRight = 0;
        const box = el.getBoundingClientRect();
        canvases.forEach((c) => {
          const r = c.getBoundingClientRect();
          if (r.height <= 0) return;
          const y = Math.round(r.y - box.top);
          if (y < top) { top = y; topRight = r.right; }
        });
        if (Number.isFinite(top)) axis = Math.max(0, Math.round(box.right - topRight));
      }

      setAreaSize((prev) => (prev.h === h && prev.w === w ? prev : { h, w }));
      setYAxisW((prev) => (prev === axis ? prev : axis));
      applySubHeight(subPaneHeight(h, ratioRef.current.topRatio));
    };

    const schedule = () => {
      // rAF 合并：一次 resize 事件可能连着触发多次
      if (raf) return;
      raf = requestAnimationFrame(measure);
    };

    const ro = new ResizeObserver(schedule);
    ro.observe(el);
    ro.observe(row);
    measure();
    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [applySubHeight]);

  // 读后端存的设置。声明位置有讲究：
  //   - 必须在 applyRatio / applySubHeight 之后（它们是本 effect 的依赖）
  //   - 也必须在 chart 初始化 effect 之前（effect 按声明顺序跑，这里要能改初始布局）
  // useEffect(() => { ... }, [applyRatio]);
  useEffect(() => {
    let cancelled = false;
    fetchConfig()
      .then((cfg) => {
        if (cancelled) return;
        const value = cfg.show_price_line !== false;
        configLoadedRef.current = true;
        setShowPriceLine(value);
        // 版面比例（2026-10-13 起从 localStorage 迁到 app_config）：
        // 库里没有这两个键 = 用户没改过 = 保持默认值。
        // 比例只影响布局不影响数据，所以不必像周期那样等它回来才发请求。
        applyRatio(ratioFromConfig(cfg));
        // 值跟默认值相同时 setState 不会触发重渲染，上面那个 effect 也就不会跑，
        // 所以这里必须直接补一次应用，否则「存的就是 true」这条路永远画不出来。
        const chart = chartRef.current;
        if (chart) setLastPriceMarkVisible(chart, value);
      })
      .catch(() => {
        if (cancelled) return;
        // 读不到就用默认值，别把图卡住不放。顺手放开门闩，
        // 后面用户手动切开关时仍然能生效。
        configLoadedRef.current = true;
      });
    return () => { cancelled = true; };
  }, [applyRatio]);

  // 初始化 chart 实例（仅一次）
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = init(containerRef.current, { styles: DARK_THEME as never });
    if (!chart) return;

    // X 轴**十字线标签**（跟随光标那个带底色的日期）改成跟悬浮窗日期行一样的
    // 「2026/08/07周五」。它由 klinecharts 内建，默认走 PeriodTypeCrosshairTooltipFormat
    // 的 'YYYY-MM-DD' 模板 → 显示成 "2026-08-07"，跟浮窗的斜杠+星期割裂开。
    // 走公开 API chart.setFormatter，不是改私有字段：FormatDateType 正好能区分
    //   'crosshair' = ① 十字线标签（要改）
    //   'xAxis'    = 底部那排普通刻度（保持 2026-01-28 原样）
    //   'tooltip'  = 内建蜡烛浮窗（本项目用自己的 CrosshairTooltip，不显示）
    // 月线/年线模板里没有 DD（粒度只到月/年，硬塞"周五"是错的），交回默认实现。
    // ⚠️ 必须把**函数本身**取出来存成常量，不能存 `chart.getFormatter()` 返回的
    // 对象：setFormatter 内部是 `merge(this._formatter, formatter)`，就地替换
    // `formatDate`。存对象的话，fallback 分支里 `prev.formatDate` 读到的已经是
    // 我自己 → 无限递归 → 底部普通刻度整排画不出来（2026-10-08 实测踩过）。
    const defaultFormatDate = chart.getFormatter().formatDate;
    chart.setFormatter({
      formatDate: (p) => {
        if (p.type !== 'crosshair' || !p.template.includes('DD')) return defaultFormatDate(p);
        // ⚠️ `p.timestamp` 在空白区是**外推出来的假日期**，必须夹住。
        // klinecharts 的 `setCrosshair` 把 `dataIndex` clamp 到末根（所以浮窗数据是对的），
        // 但 `timestamp` 字段用的是**没 clamp 的** `realDataIndex`，走
        // `dataIndexToTimestamp` 的越界分支：`lastTimestamp + span × diff`，
        // 也就是**按天线性外推**，凭空造出根本不存在的交易日。
        // 实测：末根 2026-10-12，鼠标进空白区后标签变成 10-13；
        // 空白区更宽时能一路推到 `2026/10/17 周六`（A 股周六不开市）。
        const list = chart.getDataList();
        const lastTs = list.length ? list[list.length - 1].timestamp : undefined;
        const ts = typeof lastTs === 'number' && p.timestamp > lastTs ? lastTs : p.timestamp;
        return formatBarDate(ts);
      },
    });

    chart.createIndicator({ name: 'MA', calcParams: [20], paneId: 'candle_pane', id: 'MA20' }, false);
    chart.createIndicator('VOL', true);
    chart.createIndicator({ name: 'MACD', figures: MACD_FIGURES as IndicatorFigure[] }, true);
    chart.createIndicator('KDJ', true);
    // 第 4 个副图：当前是占位留白，后面会换成真实指标。
    // 用一个内置指标名（这里挑 'BOLL'）让 klinecharts 真的渲染出一个空 pane，
    // 满足"主图 + 筹码同高" 这条约束。
    chart.createIndicator('BOLL', true);

    // 副图高度：由版面比例反推，见下面 applySubHeight 的注释。
    applySubHeight(subPaneHeightRef.current);

    // 1) VOL 副图的成交量是大数（亿级），保留 klinecharts 默认 K / M / B 单位后缀（"100M" / "1B"），
    //    这样 y 轴宽度比 raw 整数（"700,000,000"）窄得多，整页更紧凑。所以不覆盖 formatBigNumber。

    // 2) 把 MACD/KDJ 副图 y 轴上 "-60.0000"、"0.0000" 这种精度冗余抹零。
    //    klinecharts y 轴 tick 用 displayValueToText(value, precision) 渲染，
    //    默认是 value.toFixed(precision)，indicator 默认 precision=4。
    //    覆盖成 parseFloat(...toFixed(precision))，自动去掉末尾的 0。
    const stripTrailingZeros = (value: number, precision: number) => {
      const fixed = Number(value).toFixed(precision);
      return parseFloat(fixed).toString();
    };
    for (const yAxis of chart.getYAxes({})) {
      (yAxis as unknown as { override: (o: object) => void }).override({
        displayValueToText: stripTrailingZeros,
      });
    }

    chart.setDataLoader({
      // klinecharts v10 原生支持游标式加载，不需要自写状态机。
      //
      // 注意 forward / backward 的命名指的是**滚动方向**，不是时间方向，很容易搞反：
      //   'forward'  → data.concat(_dataList)，前插 = 加载**更早**的历史
      //                 触发条件 from === 0（可视区左端顶到已加载最早的一根）
      //   'backward' → _dataList.concat(data)，后接 = 加载**更新的**数据
      //                 触发条件 to === totalBarCount（可视区右端顶到已加载最新的一根）
      // 见 node_modules/klinecharts/dist/index.esm.js:13463-13477 与 13598-13603。
      //
      // 本项目只要「更早的历史」，所以只接 init 和 forward，并且 always 回
      // more.backward = false：init 取的永远是库内最新，右边没有更新的东西要拉，
      // 回 true 会让 klinecharts 在首屏就自动发一次无意义的请求。
      getBars: async (params) => {
        const myId = ++requestIdRef.current;
        const ticker = params.symbol?.ticker || currentIndexRef.current.code;
        const isInit = params.type === 'init';

        // backward（更新数据）/ update（subscribeBar 推送）都不接。
        // 但**必须回调**，否则 klinecharts 的 _loading 永远解不掉，后续加载全被吞。
        if (!isInit && params.type !== 'forward') {
          params.callback([], NO_MORE);
          return;
        }

        if (isInit) {
          earliestRef.current = null;
          extraRef.current.clear();
          // 除权日也要清：换标的/换周期后旧 timestamp 留在集合里，
          // 会让新图上不该出现的 K 线凭空多个 `$`（不同标的的 timestamp 不撞，
          // 但同一标的换周期后旧周期的除权日会混进来）。
          exDatesRef.current.clear();
          // 先清空：换标的/换周期的那一瞬间，上一只票的价格还挂在头上是错的。
          setLastBar(null);
        }

        // 没有游标就没法往前翻（正常流程 earliest 一定有值，这里纯防御）。
        // 回 NO_MORE 把 forward 判定关掉：只回空数组是不够的，
        // more 会沿用上一次的 true，它会立刻再问一轮，无限空转。
        if (!isInit && !earliestRef.current) {
          params.callback([], NO_MORE);
          return;
        }

        // 向历史翻页走闭区间查（end_date 含当天），末根必然与已加载集合重叠 1 根，
        // 所以请求数 +1，扣掉重叠后正好新增 PAGE_BARS 根，不多花一根。
        const count = isInit ? INITIAL_BARS : PAGE_BARS + 1;

        try {
          const json = await fetchKlines(ticker, {
            category: periodRef.current.category,
            count,
            adj: adjustRef.current,
            endDate: isInit ? undefined : (earliestRef.current ?? undefined),
          });
          // 期间切了标的/周期：setSymbol/setPeriod 走的是 klinecharts 的 resetData()，
          // 它自己会把 _loading 复位并重发一次 init，所以这里直接丢弃、不回调，
          // 既不会写脏数据，也不会把新 init 的 _loading 提前解掉。
          if (myId !== requestIdRef.current) return;
          if (json.code !== 0) {
            setError(json.error || 'K 线加载失败');
            params.callback([], NO_MORE);
            return;
          }
          setError(null);

          const cursor = earliestRef.current;
          const rows: ApiKLine[] = json.data || [];
          // 去重：闭区间查回来的末根 == 游标当天，已在图上。klinecharts 的 forward
          // 是无脑 data.concat(_dataList)，既不去重也不排序，不剔掉就会在图上出现
          // 两根连续的同一根 K 线。datetime 是 YYYY-MM-DD，字符串比较即时间比较。
          const fresh = isInit ? rows : rows.filter((r) => r.datetime < cursor!);

          // 头部报价 = 最新一根收盘，涨跌幅基准 = 它的前一根收盘。
          // rows 按 datetime 升序（下面 earliestRef 取 fresh[0] 就依赖这个前提）。
          // 不足 2 根算不出涨跌，置 null，头部不显示报价。
          if (isInit) {
            const tail = rows.slice(-2);
            setLastBar(
              tail.length === 2
                ? { close: tail[1].close, prevClose: tail[0].close, datetime: tail[1].datetime }
                : null,
            );
          }

          if (fresh.length > 0) earliestRef.current = fresh[0].datetime;

          // 不设上限，一直翻到 truncated=false（库里最早的那根）为止。
          // 单只 A 股全历史 5000~6000 根，实测 5432 根只占 878KB 堆内存，
          // 内存从来不是瓶颈，所以没必要设上限把用户挡在半路。
          // 档位对照见 docs/kline-bar-counts.md。
          const hasEarlier = json.truncated === true;

          const list = fresh
            .map((k) => toChartData(k, extraRef.current))
            .filter((d): d is NonNullable<typeof d> => d !== null) as KCKLineData[];

          // 收集本页的除权日。**累积**而不是覆盖：向前翻历史是「前插」，
          // 新一页装的是更早的 K 线，已加载的那部分除权日还得留着。
          // 值用后端给的 xdxr_date（真实除权日），不自己从 timestamp 反推——
          // 周线上标记落在周五，除权日可能是周三，反推出来的高亮对不上。
          for (const r of rows) {
            if (!r.is_xdxr || !r.xdxr_date) continue;
            const ts = Date.parse(`${r.datetime}T00:00:00+08:00`);
            if (!Number.isNaN(ts)) exDatesRef.current.set(ts, r.xdxr_date);
          }

          // backward 恒 false：右侧永远没有更新的数据（init 每次都取到库内最新），
          // 回 true 会让 klinecharts 在首屏右端就自动再问一轮。
          params.callback(list, { backward: false, forward: hasEarlier });

          // 标记必须等 callback 之后重建：klinecharts 是先把返回的 list 拼进
          // _dataList，再回调外围的 getBars，此时 getDataList() 才是合并后的完整集合。
          //
          // ⚠️ 自己包一层 try：它**不能**冒泡到上面的 catch。那个 catch 会再调一次
          // `params.callback([], NO_MORE)`，而 klinecharts 的 callback 是同步执行
          // `_addData` 的（index.esm.js:13615-13618），二次回调等于把刚灌进去的
          // K 线整体清空——「画标记失败」会表现成「K 线图整个空了」，极难排查。
          try {
            renderExDividendMarkers(chart, exDatesRef.current);
          } catch (e) {
            // 标记只是附加信息，失败不该影响行情。吞掉并清掉已建的标记即可。
            console.warn('[exDividend] 渲染除权标记失败', e);
            clearExDividendMarkers(chart);
          }

          if (isInit) {
            // 数据首次落库后，klinecharts 内部 pane heights 真正生效。
            // 此时再 resize 一次保证 pane heights 重新分配（init 时容器可能
            // 高度还没稳定）。
            requestAnimationFrame(() => chart.resize());
          }
        } catch (e) {
          if (myId !== requestIdRef.current) return;
          setError((e as Error).message);
          // 出错也回 NO_MORE：宁可停住让用户重连，也不要让 klinecharts 立刻
          // 再触发一轮 backward 把同一个错误打满。
          params.callback([], NO_MORE);
        }
      },
    });

    chart.setSymbol({ ticker: currentIndexRef.current.code, pricePrecision: 2, volumePrecision: 0 });
    chart.setPeriod(periodRef.current.period);

    // 订阅十字线：同时驱动两件事——
    //   1) ChipDistribution：按 hover 位置的 dataIndex 切片重算成本分布（国内行情软件
    //      "成本分布演化"的标准交互）。
    //   2) CrosshairTooltip：浮窗显示当前 K 线的 OHLCV，根据十字线 x 位置自适应左右。
    //
    // ⚠️ 这里**必须**用 klinecharts 内部算好的十字线数据，不能自己按 x 做线性换算。
    // 自己算的那版有两处硬伤（2026-10-08 实测）：
    //   1. barWidth 用「容器宽 / 可见根数」算，扣不掉 chart 左右 padding 和右侧
    //      y 轴宽度，误差随 x 增大而累积 —— 末端能偏 12 根（≈10 个交易日），
    //      悬停在最右侧几根时浮窗日期会「卡住」不变（实测卡在 09-18，真实 09-30）。
    //   2. getVisibleRange().to 会越界：实测 to=500 而 dataList.length=500，
    //      索引最大只有 499，可见根数因此多算 1 根。
    // getCrosshair() 里已有 clamp 后的 kLineData / dataIndex，直接取即可。
    chart.subscribeAction('onCrosshairChange', (data: unknown) => {
      const x = (data as { x?: number })?.x;
      const chartInst = chartRef.current;
      const container = containerRef.current;
      if (typeof x !== 'number' || Number.isNaN(x) || !chartInst || !container) {
        tooltipShownRef.current = false;
        setCrosshairIndex(null);
        setTooltipData(null);
        return;
      }
      const store = getChartStore(chartInst);
      const crosshair = store?.getCrosshair?.();
      const kLine = crosshair?.kLineData;
      const lastIdx = chartInst.getDataList().length - 1;
      // 空白区判据用**几何坐标**，不用 realDataIndex：实测 x 超过最后一根后
      // coordinateToDataIndex 会被钉死在 lastIdx（499），再往右仍是 499，
      // 拿它当判据会把大片空白区误判成「在最后一根上」（实测 95% 处仍读 499）。
      // lastX + halfBar 才是最后一根 K 线的右边界：实测 lastX=788、halfBar=5，
      // x=777 仍要显示、x=796 就该隐藏。
      const halfBar = store?.getBarSpace?.()?.halfBar ?? 5;
      const lastX = store?.dataIndexToCoordinate?.(lastIdx);
      const inBlank = typeof lastX === 'number' && x > lastX + halfBar;
      // 空白区（末根右侧）**不整套消失，而是钉在最后一根**：klinecharts 已经把
      // kLineData clamp 到末根，所以这里只要把 index 也钉到 lastIdx，
      // 浮窗与筹码就都停在最后一根，符合「鼠标在图上就指向末根」。
      //
      // 早先这里是 early return（隐藏浮窗 + crosshairIndex 置 null），
      // 后果是筹码退化成"只看最后一根"、获利比例恒 100%（实测 1.5% → 100%），
      // 视觉上就是「鼠标一往右筹码就散架」。
      const dataIdx = inBlank ? lastIdx : crosshair?.dataIndex ?? lastIdx;
      const clamped = Math.max(0, Math.min(dataIdx, lastIdx));
      if (!kLine) {
        tooltipShownRef.current = false;
        setCrosshairIndex(null);
        setTooltipData(null);
        return;
      }
      // 浮窗一亮就代表「我已经指着这根 K 线了」→ 顺手把焦点收过来，
      // 这样左右方向键不用再点一下图就能直接切 K 线。
      //
      // 为什么不能靠 mousedown 拿焦点：klinecharts 只在 **点击**时让内层容器
      // 拿到焦点（那个 div 自带 tabIndex=1），单纯悬停不会给任何元素焦点。
      //
      // 只在「浮窗刚出现」这一刻抢（上升沿）：浮窗显示期间用户 Tab 去别处办事，
      // 鼠标又在图里轻微挪一下，不该把焦点抢回来。
      if (!tooltipShownRef.current) {
        tooltipShownRef.current = true;
        container.focus({ preventScroll: true });
      }
      setCrosshairIndex(clamped);
      // 昨收 = 前一根 K 线的收盘。不能取 dataList[clamped-1] 之外的地方：
      // loadedList 里每根都已按 trade_date 去重排序，索引 -1 就是紧邻的前一交易日。
      // 翻到最早一根（index 0）时没有前一根，给 null 而不是拿 open 顶替。
      const prevBar = clamped > 0 ? chartInst.getDataList()[clamped - 1] : null;
      setTooltipData({
        kLine,
        prevClose: prevBar ? prevBar.close : null,
        // 库里有就用库里的（Tushare pro.daily / sync_znz 算出的权威口径）；
        // 取不到（派生周期表、现场聚合）才是 undefined，交给浮窗自己兜底算。
        extra: extraRef.current.get(kLine.timestamp),
        x,
        containerWidth: container.clientWidth || 1,
      });
    });

    chartRef.current = chart;
    setLoading(false);

    // ── 拖动改横向：左键拖不动，右键拖才行 ──
    //
    // klinecharts 的左键拖动滚动是内建的，滚动量在 mousemove 里落到
    // store.scroll()（index.esm.js:13741）。而它只给了一个**全局总闸**
    // setScrollEnabled —— 一关，滚轮横滚、Shift+←/→ 热键、乃至方向键切 K 线
    // 用的 scrollToDataIndex 都会被一起关掉（scrollByDistance / scrollToDataIndex
    // 最终都调 store.scroll）。所以这里不能常驻关，只能掐掉"左键拖"这一条路径：
    // 按下那一瞬关、松开立刻开。
    //
    // 不用跟 klinecharts 抢 mousedown 的执行顺序：它的 mousedown 只记
    // _startScrollCoordinate 并 startScroll()，真正滚动发生在之后的 mousemove。
    // 只要在下一次 mousemove 之前把总闸关掉就够了，而同一个元素上的监听器
    // 无论谁先注册都会在本次 dispatch 内跑完。
    // 右键拖动判定阈值（px）：mousedown 到 mouseup 之间累计位移超过它才算拖动。
    // 不设阈值的话，纯右键单击被手抖 / 边框 resize 蹭到 1~2px 就会滚一下。
    const DRAG_THRESHOLD = 3;
    let dragStartX: number | null = null;
    let dragAccum = 0;
    let dragged = false;

    const chartEl = containerRef.current;
    // 容器原点：框选的坐标要换算到容器局部（clientX/Y 是视口的），而且
    // 拖动中途容器可能因右侧面板开合而挪位置，所以每次都现取而不是按下时缓存。
    const localPoint = (e: MouseEvent) => {
      const r = chartEl.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };
    /** 把矩形写进 DOM。不进 React —— 拖动时每次 mousemove 都 setState 会重渲染整棵树。 */
    const paintSelBox = (r: SelRect | null) => {
      const el = selBoxRef.current;
      if (!el) return;
      if (!r) { el.style.display = 'none'; return; }
      el.style.display = 'block';
      el.style.left = `${r.x}px`;
      el.style.top = `${r.y}px`;
      el.style.width = `${r.w}px`;
      el.style.height = `${r.h}px`;
    };
    // 撤框选：DOM 矩形（可能正处在"绕过 React 直接改"的状态）+ React state + 索引区间。
    // 三者都要动，只清其中一个都会留下不一致的残骸。
    const clearSel = () => { paintSelBox(null); clearSelection(); };

    const onDragMouseDown = (e: MouseEvent) => {
      // 点在框选菜单上就别启动框选。菜单是 .kline-container 的子元素，
      // mousedown 照样冒到 chartEl：要是照常起手，这次按下会被 onDragMouseUp
      // 判成"左键单击"→ clearSel()，selRangeRef 在 click 触发 onZoomIn **之前**
      // 就被清空了，点「区域放大」于是静默无效（菜单倒是消失了，像点成功了一样）。
      const t = e.target as Node | null;
      if (t && selMenuRef.current?.contains(t)) return;
      if (e.button === 0) {
        chart.setScrollEnabled(false);
        // 左键框选：记起点并把空框显示出来。
        // 内建左键拖动滚动已经靠上面的总闸掐掉了，不会跟框选打架。
        const p = localPoint(e);
        selDragRef.current = { x0: p.x, y0: p.y, x: p.x, y: p.y };
        paintSelBox({ x: p.x, y: p.y, w: 0, h: 0 });
      } else if (e.button === 2) {
        // 右键按下：klinecharts 在 mouseDownEvent 里对 button===2 是直接 return 的
        // （只发一次 mouseRightClickEvent，不会 startScroll），所以右键拖动整条路
        // 都是空的，自己实现即可，不会跟它打架。
        dragStartX = e.clientX;
        dragAccum = 0;
        dragged = false;
      }
    };
    const onDragMouseMove = (e: MouseEvent) => {
      // 左键框选
      const sel = selDragRef.current;
      if (sel) {
        const p = localPoint(e);
        sel.x = p.x;
        sel.y = p.y;
        // 钳进容器：往上/往左拖出图外时，框不该跑到别的面板上去。
        // 必须取**容器**的尺寸 —— 不能读 selBoxRef.current：那是矩形自己，
        // 它的 clientWidth 就是刚画上去的框宽（起手时为 0），拿它当上限会把
        // 所有坐标都钳成 0，框永远 0x0，要等 mouseup 后 React 写入真实尺寸才显形。
        const maxW = chartEl.clientWidth;
        const maxH = chartEl.clientHeight;
        const x0 = Math.max(0, Math.min(sel.x0, maxW));
        const y0 = Math.max(0, Math.min(sel.y0, maxH));
        const x1 = Math.max(0, Math.min(sel.x, maxW));
        const y1 = Math.max(0, Math.min(sel.y, maxH));
        paintSelBox({ x: Math.min(x0, x1), y: Math.min(y0, y1), w: Math.abs(x1 - x0), h: Math.abs(y1 - y0) });
        return;
      }

      if (dragStartX === null) return;
      const dx = e.clientX - dragStartX;
      if (dx === 0) return;
      dragStartX = e.clientX;
      dragAccum += dx;
      if (!dragged && Math.abs(dragAccum) < DRAG_THRESHOLD) return;
      dragged = true;
      // 增量式滚动：每次只滚这一小段，累加误差小。
      // 符号：往右拖要看更早的 K 线，所以直接传 dx（正距离）。
      // StoreImp.scroll 里 _lastBarRightSideDiffBarCount = start - distance/bar
      // （index.esm.js:13747），正距离把最后一根推向右边缘 = 视口右移 = 更早；
      // 它回吐的 onScroll distance 也等于传入值本身（13756 行），符号不翻转。
      chart.scrollByDistance(dx);
    };
    const onDragMouseUp = (e: MouseEvent) => {
      if (e.button === 0) {
        chart.setScrollEnabled(true);
        const sel = selDragRef.current;
        selDragRef.current = null;
        if (!sel) return;
        const w = Math.abs(sel.x - sel.x0);
        const h = Math.abs(sel.y - sel.y0);
        if (w < MIN_SEL || h < MIN_SEL) {
          // 位移不够 = 左键单击，不留框。同时把上一次框选也撤掉，
          // 这样「点一下空白处清掉旧框」是同一条路径，不用单开分支。
          clearSel();
          return;
        }
        const rect: SelRect = {
          x: Math.min(sel.x, sel.x0),
          y: Math.min(sel.y, sel.y0),
          w,
          h,
        };
        // 同时把框的左右边缘换算成 dataIndex，「区域放大」直接拿它切片。
        // 坐标系与 dataIndexToCoordinate 一致（都是容器局部 x），所以能直接喂进去；
        // 两端再夹进 [0, length-1]，否则框到空白区时会算出越界下标。
        const store = getChartStore(chart);
        const total = chart.getDataList().length;
        const toIdx = store?.coordinateToDataIndex?.(rect.x + rect.w);
        const fromIdx = store?.coordinateToDataIndex?.(rect.x);
        if (typeof fromIdx === 'number' && typeof toIdx === 'number' && total > 0) {
          const a = Math.max(0, Math.min(Math.min(fromIdx, toIdx), total - 1));
          const b = Math.max(0, Math.min(Math.max(fromIdx, toIdx), total - 1));
          selRangeRef.current = { from: a, to: b };
        } else {
          selRangeRef.current = null;
        }
        setSelRect(rect);
        return;
      }
      dragStartX = null;
    };
    const onContextMenu = (e: MouseEvent) => {
      // 无条件拦：右键已改成拖动，浏览器系统菜单会在拖到一半时弹出来。
      // 只在图内拦截（监听挂在 chartEl 上），页面其他区域右键菜单不受影响。
      e.preventDefault();
    };

    // 点框外 = 取消框选。挂 document 捕获阶段：要赶在业务元素的 mousedown 之前
    // 把旧框撤掉，否则「在图上按下」会先触发一次清除、再被新框覆盖，中途闪一下。
    // 命中框内或菜单内则放过（点菜单不算取消）。
    const onDocMouseDown = (e: MouseEvent) => {
      // 不能读 React 的 selRect：这个 effect 依赖是 []，闭包里的 selRect 永远是
      // 首帧那个 null。改成直接问 DOM —— style.display 就是框的开关。
      const box = selBoxRef.current;
      if (!box || box.style.display === 'none') return;
      const t = e.target as Node | null;
      if (t && (box.contains(t) || selMenuRef.current?.contains(t))) return;
      clearSel();
    };

    chartEl?.addEventListener('mousedown', onDragMouseDown);
    chartEl?.addEventListener('contextmenu', onContextMenu);
    document.addEventListener('mousedown', onDocMouseDown, true);
    // 挂在 window 上：鼠标拖到图外再松开也要把左键的总闸恢复，
    // 否则一次拖出窗口的左键按下会把滚动永久禁掉。
    window.addEventListener('mousemove', onDragMouseMove);
    window.addEventListener('mouseup', onDragMouseUp);

    // 监听容器尺寸变化（窗口大小、侧栏开关、Viewport 切换等），
    // klinecharts 不会自动监听 ResizeObserver，需手动调用 resize() 重画。
    // 同时观察 chart-frame-body（layout chain 上层）和 kline-container（最终容器），
    // 这样多层 flex/grid 链中任一层尺寸变化都能正确触发重画，避免 init 时
    // 容器 0 高 / 半高被锁住后 layout chain 后续拉伸了 chart 没跟上。
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          chart.resize();
        }
      }
    });
    ro.observe(containerRef.current);
    const chartBodyEl = containerRef.current?.closest('.chart-frame-body');
    if (chartBodyEl) ro.observe(chartBodyEl);

    // 兜底：layout chain 在 init 时可能还没完全 settle（flex/grid 链需要
    // 几个 render frame 才稳定）。再额外触发几次 resize 强制跟上。
    const t1 = window.setTimeout(() => chart.resize(), 50);
    const t2 = window.setTimeout(() => chart.resize(), 200);
    const t3 = window.setTimeout(() => chart.resize(), 600);

    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      window.clearTimeout(t3);
      chartEl?.removeEventListener('mousedown', onDragMouseDown);
      chartEl?.removeEventListener('contextmenu', onContextMenu);
      document.removeEventListener('mousedown', onDocMouseDown, true);
      window.removeEventListener('mousemove', onDragMouseMove);
      window.removeEventListener('mouseup', onDragMouseUp);
      ro.disconnect();
      // 标记是挂在 chart 上的 overlay，chart 一 dispose 就全没了；
      // 这里显式清一次是为了让 chartRef 与 overlay 列表的约定看得见，
      // 也避免 dispose 抛异常时把标记留在下一次挂载的图上。
      clearExDividendMarkers(chart);
      if (containerRef.current) dispose(containerRef.current);
      chartRef.current = null;
    };
  }, []);

  // 除权标记的 overlay 模板 + 点击回调。挂载时做一次就够：
  // 模板注册走模块级 registered 幂等保护，点击回调只用到两个 setState（引用恒定）。
  useEffect(() => {
    registerExDividendOverlay();
    setExDividendClickHandler((date) => {
      setXdxrActive(date);
      setXdxrOpen(true);
    });
    return () => setExDividendClickHandler(null);
  }, []);

  // 指数 / 周期 / 复权切换：触发 chart 重新加载
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.setSymbol({ ticker: currentIndex.code, pricePrecision: 2, volumePrecision: 0 });
    chart.setPeriod(period.period);
  }, [currentIndex, period, adjust]);

  /** 方向键：把主图十字线左右挪一根。
   *
   *  不自己算 x，而是取 dataIndex 再换算回去（x = dataIndexToCoordinate(next)）：
   *  这样传进 setCrosshair 的 x 和它内部 coordinateToDataIndex 的输入严格互逆，
   *  不会出现「指到相邻根」的边界误差——这正是之前自己按 x 做线性换算踩过的坑。 */
  const moveCrosshair = (delta: number) => {
    const chart = chartRef.current;
    const store = getChartStore(chart);
    if (!chart || !store?.setCrosshair) return;
    const lastIdx = chart.getDataList().length - 1;
    if (lastIdx < 0) return;

    const cur = store.getCrosshair?.();
    // x 存在才说明十字线真在图上。klinecharts 复位时会把 x 清成 undefined
    // （setCrosshair() 不带参），那种残留的 dataIndex 不能当成"停在这根"。
    const curIdx =
      cur && typeof cur.x === 'number' && typeof cur.dataIndex === 'number'
        ? Math.max(0, Math.min(cur.dataIndex, lastIdx))
        : null;
    // 第一次按键只把十字线放到最新一根上，不额外再走一格：
    // 既符合通达信的手感，也能让人先看清"现在停在哪儿"再往回翻。
    const next = curIdx === null ? lastIdx : Math.max(0, Math.min(curIdx + delta, lastIdx));
    if (curIdx !== null && next === curIdx) return; // 已经到头，别做无谓的重绘

    // 目标根不在可视区就先滚进来，否则十字线会跑到屏幕外，看着像"按了没反应"。
    // animationDuration 传 0（不动画），连按时才跟得上手。
    const visible = chart.getVisibleRange();
    if (next < visible.from || next > visible.to) {
      chart.scrollToDataIndex(next, 0);
    }

    const x = store.dataIndexToCoordinate?.(next);
    if (typeof x !== 'number' || Number.isNaN(x)) return;
    // y 优先沿用上一次（悬停时定的），横向线就不跟着乱跳；
    // 上次的十字线若在别的 pane，那个 y 是那个 pane 的局部值，不能搬过来，
    // 一律按收盘价重算。
    const y =
      cur?.paneId === CANDLE_PANE_ID && typeof cur.y === 'number'
        ? cur.y
        : candlePaneY(chart, next);
    store.setCrosshair({ x, y: y ?? undefined, paneId: CANDLE_PANE_ID });
  };

  /** 「区域放大」：把框选的那一段放大到铺满绘图区。
   *
   *  ⚠️ 关键：**不换数据**，只调 barSpace 和视口。
   *  早先的实现是 `_addData(slice, 'init')` 把数据**替换**成那 n 根，
   *  于是"永远只能看到这几根"——放大之后再缩小、再拖动，区间外的 K 线
   *  永远回不来。区域放大要的是"看得更清楚"，不是"只剩这些"。
   *  保留全量数据，缩小就能退回全景、拖动就能看到区间外的 K 线。
   *
   *  两步：
   *   ① setBarSpace(fill)     —— 间距缩到 n 根正好铺满绘图区
   *   ② setOffsetRightDistance —— 把视口挪到该区间，靠左对齐
   *
   *  ② 的算法（见 setOffsetRightDistance，index.esm.js:13692-13694）：
   *  它内部存的是 diff = distance / barSpace，而可视区右端
   *  to = round(diff + 数据根数)（13557 行）。要让区间第一根落在左边缘，
   *  就要视区宽可视根数 → to = from + 可视根数，反解出
   *  diff = from + 可视根数 − 数据根数。
   *
   *  ⚠️ diff **可以是负数**，别加 Math.max(0, ·)：
   *  框选区间通常在数据中部，算出来必然是负的，钳成 0 会让视口直接跳到
   *  最右端（实测视口跑到了 07-12~08-06，而框选是 06-14~07-08）。
   *  负 diff 正是"最后一根右边有空白、视口偏左"的正常表达，
   *  _adjustVisibleRange 允许的范围是 [−数据根数, 可视根数]（13549-13556 行）。
   *
   *  ⚠️ 这里也**不要**再减 1：可视根数是小数（绘图区宽/barSpace），
   *  to = round(from + 可视根数 + 0.5) 才是对的；多减一个会让左端多露一根
   *  （实测视口 06-11~07-08，框选 06-14~07-08，左边多出 06-11）。 */
  const onZoomIn = () => {
    const chart = chartRef.current;
    const range = selRangeRef.current;
    const store = getChartStore(chart);
    if (!chart || !store || !range) return;
    const list = chart.getDataList();
    const from = Math.max(0, Math.min(range.from, list.length - 1));
    const to = Math.max(from, Math.min(range.to, list.length - 1));
    const count = to - from + 1;
    // 少于这么多根放大没意义（糊成一片竖线），直接不动。
    if (count < MIN_ZOOM_BARS) return;

    const fill = barSpaceToFill(chart, count);
    if (fill === null) return;
    chart.setBarSpace(fill);
    const totalSpace = store._totalBarSpace ?? 0;
    if (totalSpace > 0) {
      const visibleCount = totalSpace / fill;
      const diff = from + visibleCount - list.length;
      store.setOffsetRightDistance?.(diff * fill, true);
    }
    // 框和菜单撤掉：放大后那些像素坐标已经指向别的 K 线了。
    clearSelection();
  };

  /** 「区域统计」：把当前框选区间的 dataIndex 快照给弹窗。
   *
   *  区间要在**这一刻**从 selRangeRef 拷进 state：弹窗的数值由它算，
   *  ref 变了不会触发重渲染，只留 ref 的话弹窗拿不到新值。
   *  弹窗开着时框选会被清掉（点遮罩关闭那次的 mousedown 走到了框外判断），
   *  但 statsRange 已经存下来了，重新打开再框选即可。 */
  const onRegionStats = () => {
    const chart = chartRef.current;
    const range = selRangeRef.current;
    if (!chart || !range) return;
    const len = chart.getDataList().length;
    if (len === 0) return;
    const from = Math.max(0, Math.min(range.from, len - 1));
    const to = Math.max(from, Math.min(range.to, len - 1));
    setStatsRange({ from, to });
  };

  /** 图表容器的键盘处理：← / → 切换十字线。
   *
   *  只拦裸方向键。Shift+← / Shift+→ 是 klinecharts 自带的"横向滚动图表"
   *  （Event 里注册在 document 上的 hotkey，见 index.esm.js:2043-2072），
   *  所以带 Shift 的一律放行、不 preventDefault，让它照常冒泡到库去。 */
  const onChartKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    // 不拦住的话浏览器会拿方向键横滚整个页面。
    e.preventDefault();
    e.stopPropagation();
    moveCrosshair(e.key === 'ArrowRight' ? 1 : -1);
  };

  /** 焦点离开图表时收掉十字线。
   *
   *  必须自己清 React state：klinecharts 的 setCrosshair() 复位虽然会把内部
   *  crosshair 清空，但那条路径**不会**触发 onCrosshairChange（StoreImp 里要求
   *  paneId 是字符串才发事件，复位后 paneId 是 undefined），不手动清的话浮窗会僵在
   *  上一根 K 线上。
   *
   *  ⚠️ React 的 onBlur 底层是**冒泡**的 focusout：焦点在本 div 和 klinecharts
   *  内层容器之间来回挪时（悬停把焦点收到本 div → 点击又被内层抢走）
   *  也会从本 div 冒下来。不看 relatedTarget 就会把刚出现的浮窗又抹掉。 */
  const onChartBlur = (e: React.FocusEvent<HTMLDivElement>) => {
    if (e.relatedTarget && e.currentTarget.contains(e.relatedTarget)) return;
    tooltipShownRef.current = false;
    getChartStore(chartRef.current)?.setCrosshair?.();
    setCrosshairIndex(null);
    setTooltipData(null);
  };

  /** 鼠标移出图表区（主图 + 副图）时收掉浮窗。
   *
   *  为什么 onBlur 不够、必须单独挂 mouseleave：klinecharts 的 mouseLeaveEvent 走的是
   *  `setCrosshair()` **无参复位**，复位后 `paneId` 是 `undefined`，而 StoreImp 发
   *  `onCrosshairChange` 的条件里有 `isString(this._crosshair.paneId)`——那条路
   *  **根本不发事件**，React state 不会更新，浮窗会僵在离开前的位置上（实测停在 07-29）。
   *  onBlur 只能兜住「焦点离开」，纯鼠标移出不经过它。
   *
   *  只清浮窗（tooltipData），**不动 crosshairIndex**：离开后筹码仍定在最后一根，
   *  而 crosshairIndex 保持末根值正好就是这个语义。
   *
   *  这里不必调 `setCrosshair()`——klinecharts 自己已经在 mouseLeaveEvent 里清了内部
   *  十字线（画面上的十字线会消失），我们只补它没发的那次事件。 */
  const onChartMouseLeave = () => {
    tooltipShownRef.current = false;
    setTooltipData(null);
  };

  // 实时行情轮询
  useEffect(() => {
    const fetchQuoteOnce = async () => {
      try {
        const url = `${API_BASE}/api/index/quote?code=${encodeURIComponent(currentIndex.code)}`;
        const resp = await fetch(url);
        const json = await resp.json();
        if (json.code === 0 && json.data) {
          setQuote(json.data);
        }
      } catch {
        // 静默
      }
    };
    fetchQuoteOnce();
    if (fetchQuoteTimerRef.current) clearInterval(fetchQuoteTimerRef.current);
    fetchQuoteTimerRef.current = window.setInterval(fetchQuoteOnce, 5000);
    return () => {
      if (fetchQuoteTimerRef.current) clearInterval(fetchQuoteTimerRef.current);
    };
  }, [currentIndex]);

  // 头部报价一律以**前一根 K 线收盘**（昨收）为基准，不是 open。
  // 旧代码误把 open（当日开盘价）当昨收，算出来的涨跌幅跟行情对不上；
  // 实时快照的 open 还会因为集合竞价返回 0，更不能拿来当基准。
  // 数据源也从 quote（通达信快照）换成了 lastBar（K 线末根），理由见 lastBar 的注释。
  const prevClose = lastBar?.prevClose ?? 0;
  const changePct = prevClose > 0 && lastBar ? ((lastBar.close - prevClose) / prevClose) * 100 : null;
  const changePoints = prevClose > 0 && lastBar ? lastBar.close - prevClose : null;
  const isUp = changePct !== null && changePct >= 0;
  const priceTip = lastBar ? `库内最新 K 线 · ${lastBar.datetime}` : '行情来源未知';

  // 框选菜单默认挂在框的**右下角外侧**（跟行情软件一致）。框贴到容器边时翻到
  // 内侧，否则菜单会被裁掉、或盖到右列面板上去。
  let selMenuPos: { left: number; top: number } | null = null;
  if (selRect) {
    const cw = containerRef.current?.clientWidth ?? 0;
    const ch = containerRef.current?.clientHeight ?? 0;
    const flipX = cw > 0 && selRect.x + selRect.w + SEL_MENU_W > cw;
    const flipY = ch > 0 && selRect.y + selRect.h + SEL_MENU_H > ch;
    selMenuPos = {
      left: flipX ? selRect.x - SEL_MENU_W : selRect.x + selRect.w,
      top: flipY ? selRect.y + selRect.h - SEL_MENU_H : selRect.y + selRect.h,
    };
  }

  return (
    // --sub-chart-h / --x-axis-h / --sub-charts-total 三个都走 inline：
    // 副图高度是「比例 × 容器实测高」算出来的，CSS 里那份只是首帧兜底。
    // sub-charts-total 必须**整个值**在这里重算：CSS :root 里那个
    // calc(4 * var(--sub-chart-h) + ...) 是在 :root 那一层就把 var() 替换掉的，
    // 覆盖本层的 --sub-chart-h 影响不到它，右列底边会跟副图底边差几条分隔线。
    <div
      className="market-chart-page"
      style={
        {
          '--sub-chart-h': `${subPaneHeight(areaSize.h, ratio.topRatio)}px`,
          '--x-axis-h': `${X_AXIS_H}px`,
          '--sub-charts-total': `${subChartsTotalHeight(areaSize.h, ratio.topRatio)}px`,
        } as CSSProperties
      }
    >
      <ChartFrame
        className="market-chart-frame"
        rightWidth={Math.max(120, Math.round(areaSize.w * ratio.rightRatio))}
        loading={loading}
        loadingTip="加载中…"
        error={error}
      >
        <div className="chart-row">
          <div
            ref={containerRef}
            className="kline-container"
            // --chart-y-axis-w：主图右侧价格刻度的实测宽度，overlay 靠它让开。
            style={{ '--chart-y-axis-w': `${yAxisW}px` } as CSSProperties}
            // tabIndex: 0 让这个 div 能被 Tab 聚焦，方向键才有处可去。
            // 鼠标点击不用管：klinecharts 自己的内层容器带 tabIndex=1，
            // 点一下就聚焦到它，而它是本 div 的后代，keydown 照样冒泡上来。
            tabIndex={0}
            onKeyDown={onChartKeyDown}
            onBlur={onChartBlur}
            onMouseLeave={onChartMouseLeave}
          >
            {/* 周期 / 复权 / 设置：浮在主图顶部右侧，跟 canvas 上画的 "MA20: xx"
                同一行。图例是 canvas 画的插不进 DOM，只能浮层；--chart-y-axis-w
                让开右侧价格刻度（实测值，不是猜的）。 */}
            <ChartTools
              currentIndex={currentIndex}
              period={period}
              setPeriod={setPeriod}
              adjust={adjust}
              setAdjust={setAdjust}
              showPriceLine={showPriceLine}
              setShowPriceLine={onShowPriceLineChange}
              ratio={ratio}
              onRatioPreview={applyRatio}
              onRatioCommit={handleRatioCommit}
              areaH={areaSize.h}
              areaW={areaSize.w}
            />

            {/* 自绘的十字线悬浮 OHLC 浮窗：替代 klinecharts 默认的 candle.tooltip。
                位置由 CrosshairTooltip 根据 x > containerWidth/2 决定 left/right。 */}
            <CrosshairTooltip data={tooltipData} />

            {/* 左键框选的矩形。拖动中由 paintSelBox 直接改 inline style（不进 React），
                松手后 React 再接管这里的 style 一次性对齐。pointer-events: none —— 它
                只是视觉层，一旦吃事件就会挡住 klinecharts 的十字线 hover。 */}
            <div
              ref={selBoxRef}
              className="sel-box"
              style={{ display: 'none', left: selRect?.x, top: selRect?.y, width: selRect?.w, height: selRect?.h }}
            />

            {/* 框选完成后贴在框右下角的操作菜单。 */}
            {selRect && selMenuPos && (
              <div ref={selMenuRef} className="sel-menu" style={selMenuPos}>
                <div className="sel-menu-item" onClick={onZoomIn}>区域放大</div>
                <div className="sel-menu-item" onClick={onRegionStats}>区域统计</div>
              </div>
            )}

            {/* 区域统计弹窗。放这里而不是 .market-chart-page 下：那边是 flex 容器，
                万一 Modal 不走 portal 渲染成真实子节点会挤动布局。
                bars 传全量、from/to 传区间，切片和统计都在弹窗里做。 */}
            <RegionStatsModal
              open={statsRange !== null}
              onClose={() => setStatsRange(null)}
              bars={chartRef.current?.getDataList() ?? NO_BARS}
              from={statsRange?.from ?? 0}
              to={statsRange?.to ?? 0}
            />

            {/* 除息除权记录弹窗。点主图下方的 `$` 标记打开，
                xdxrActive 传本次点中的日期，由弹窗高亮并滚到该行。 */}
            <XdxrModal
              open={xdxrOpen}
              code={currentIndex.code}
              name={currentIndex.name}
              activeDate={xdxrActive}
              onClose={() => {
                setXdxrOpen(false);
                setXdxrActive(null);
              }}
            />
          </div>
          <div className="chart-right-column">
            {/* 右上：宽度平分两列（1fr 1fr），左筹码右标识。
                原先这里只有筹码面板，标识/报价在页面顶部那条 48px header 行里；
                header 行取消后内容搬到了右半列。 */}
            <div className="chart-right-top">
              <div className="chart-right-top-chip">
                <ChipDistribution
                  code={currentIndex.code}
                  category={period.category}
                  count={DEFAULT_BAR_COUNT}
                  adjust={adjust}
                  currentPrice={quote?.close ?? 0}
                  symbolName={currentIndex.name}
                  upToIndex={crosshairIndex}
                />
              </div>
              <div className="chart-right-top-info">
                <SymbolInfo
                  currentIndex={currentIndex}
                  price={lastBar?.close ?? null}
                  priceTip={priceTip}
                  isUp={isUp}
                  changePct={changePct}
                  changePoints={changePoints}
                />
              </div>
            </div>
            <div className="chart-right-bottom" />
          </div>
        </div>
      </ChartFrame>
    </div>
  );
}

/** 一根 K 线在库里才有、klinecharts 又不认的字段。按 timestamp 索引，
 *  十字线回调拿 kLine.timestamp 来取。 */
interface ExtraBars {
  /** 库里记录的涨跌两列。 */
  chgAmt: number | null;
  chgPct: number | null;
  /** 交易所给的已除权昨收。**只有日线有**，浮窗昨收栏优先用它。
   *  派生周期恒为 null，回落到前一根 close（值本来就一样）。 */
  preClose: number | null;
  /** 该桶内的真实除权日（`is_xdxr` 为真时才有）。浮窗据此标注。 */
  xdxrDate?: string;
}

function toChartData(k: ApiKLine, extra: Map<number, ExtraBars>) {
  const ts = Date.parse(`${k.datetime}T00:00:00+08:00`);
  if (Number.isNaN(ts)) return null;
  // 涨跌两列不是 klinecharts 的概念，挂成自定义字段也会被 setData 丢掉，
  // 所以按 timestamp 另存一份，供十字线回调查。
  if (k.chg_amt !== null || k.chg_pct !== null || k.pre_close != null
      || k.xdxr_date) {
    extra.set(ts, {
      chgAmt: k.chg_amt ?? null,
      chgPct: k.chg_pct ?? null,
      preClose: k.pre_close ?? null,
      xdxrDate: k.xdxr_date,
    });
  }
  return {
    timestamp: ts,
    open: k.open,
    high: k.high,
    low: k.low,
    close: k.close,
    volume: k.volume,
    turnover: k.turnover,
  };
}
