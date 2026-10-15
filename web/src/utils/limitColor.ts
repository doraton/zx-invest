/**
 * K 线颜色配置：普通涨跌 + 涨跌停，共四种颜色。
 *
 * 为什么需要单独一个模块
 * ----------------------
 * klinecharts v10 **没有逐根 K 线着色的 API**。蜡烛颜色在
 * ``CandleBarView.drawImp`` 里三选一（node_modules/klinecharts/dist/index.esm.js
 * 的 6547-6561 行）：
 *
 * ```js
 * if (close_1 > comparePrice)      colors[0] = styles_1.upColor;
 * else if (close_1 < comparePrice) colors[0] = styles_1.downColor;
 * else                             colors[0] = styles_1.noChangeColor;
 * ```
 *
 * 判定依据只有 close 与「前收 / 当前 open」的大小关系，``compareRule``
 * 只有 ``current_open`` 一个可切换值，**没有按根覆盖的入口**。所以想让
 * 「只有涨停那几根变色」，改 ``upColor`` 做不到——那会把所有阳线一起改掉。
 *
 * 走的是官方支持的另一条路：``registerIndicator`` 的 ``figures[].styles``
 * 允许传**函数**，回调里能拿到该根的 ``data.current`` 与 ``data.prev``，
 * 按根返回颜色。SAR 指标就是这么做的（同文件 4667-4675 行），
 * 属于库本身的用法，不是 hack。
 *
 * 前收盘从哪来
 * ------------
 * 库的 ``pre_close`` **不能直接用**：``toChartData`` 已经验证过，
 * 自定义字段挂不上 KLineData（会被 ``setData`` 丢掉），所以那几列是
 * 另存在 ``extraRef`` 里给十字线回调用的。这里同样拿不到。
 *
 * 改用 klinecharts 内建的 ``prev``（上一根 K 线对象）。这与「涨跌停按
 * 前收盘算」的口径一致，且不需要额外数据通道。代价是**除权日会判错**：
 * 除权日的真实前收盘是已除权价，而 ``prev.close`` 是未除权的原始收盘，
 * 两者不等。不过此时 ``close`` 也不可能等于 ``prev.close × 1.1``，
 * 判定结果仍是「非涨跌停」——**错不错得一致**，不会把普通 K 线误判成封板。
 *
 * 判定范围
 * --------
 * 目前**只支持主板**（``SH.600xxx`` / ``SZ.000xxx`` 一类）：
 *   - 创业板 ``SZ.300xxx``、科创板 ``SH.688xxx`` 是 ±20%
 *   - ST / *ST 是 ±5%，而 ``stock_info`` 里没有 ST 标记字段，识别不了
 *   - 指数（含 ``hysz`` 活跃市值）没有涨跌停概念
 * 这三类一律不判定，也就不会出现「活跃市值单日跌 4% 被当成跌停」这类误判。
 */
import { registerIndicator } from 'klinecharts';
import type { Chart } from 'klinecharts';

/** 主图 pane id，即 klinecharts 的 PaneId.CANDLE。 */
const CANDLE_PANE_ID = 'candle_pane';

/** 指标名。``chart.overrideIndicator`` / ``getIndicators`` 按这个名字定位。 */
export const LIMIT_COLOR_INDICATOR = 'limitColor';

/** 主板涨跌幅限制。创业板/科创板 ±20%、ST ±5% 本期不支持。 */
const MAIN_BOARD_LIMIT = 0.1;

/** 浮点容差：判定「收盘价等于理论涨跌停价」时用 1 分，避免二进制误差把真封板判掉。 */
const PRICE_EPSILON = 0.011;

/** 标记圆点距主图底边的像素。
 *
 * ⚠️ 必须与 ``exDividendMarkers.ts`` 的 ``BOTTOM_INSET`` **取同一个值**（都是 14）：
 * 涨跌停点要落在除权「Q」标记那一行，两个数字不一致就会错开几像素。
 *
 * 用「距底边」而不是「相对收盘价的偏移」：那一行是**事件标记带**，位置由 pane
 * 包围盒决定、与价格无关。跟着价格走的话标记会散落在 K 线中间，缩放时还会漂。 */
const MARK_BOTTOM_INSET = 14;

/** 四种颜色的默认值。
 *
 * 涨跌停色与普通涨跌色**同色系但更饱和**，封板时一眼能看出「这只不一样」，
 * 又不引入新色相（涨停红与阳线红同族，跌停绿与阴线绿同族）。
 *
 * ⚠️ 跌停色不用取色图上的浅绿（#80FF80）：在深色背景上浅绿对比度不足，
 * 浅绿与阴线绿分属不同亮度区，一眼看去像「另一种颜色」而非同族强调。 */
export const DEFAULT_CANDLE_COLORS: CandleColors = {
  up: '#ef5350',            // 普通阳线（沿用 DARK_THEME 原有值）
  down: '#26a69a',          // 普通阴线
  limitUp: '#FF3B30',       // 涨停（正红）
  limitDown: '#34C759',     // 跌停（正绿）
};

export interface CandleColors {
  /** 普通阳线。 */
  up: string;
  /** 普通阴线。 */
  down: string;
  /** 涨停。 */
  limitUp: string;
  /** 跌停。 */
  limitDown: string;
}

/** 涨跌停判定结果。 */
export type LimitKind = 'up' | 'down' | null;

/** 单根 K 线的 OHLC（``calc`` 的入参，字段与 klinecharts 的 KLineData 一致）。 */
interface Ohlc {
  open: number;
  high: number;
  low: number;
  close: number;
}

/** ``styles`` 回调拿到的相邻三根视图：prev / current / next。
 *
 * ⚠️ 这里的 current 是**指标 calc 的输出**（``{limitMark, isDown}``），
 * 不是原始 K 线——index.esm.js:3141-3144 三个值都取自 ``indicator.result``。
 * 所以涨跌停判定必须在 ``calc`` 里做完（那里才拿得到原始 OHLC），
 * 这里只负责按 ``isDown`` 选色。
 *
 * 另：``styles`` 回调拿到的就是 ``result[dataIndex]`` 本身，而 calc 的返回值
 * 与 ``dataList`` **按下标一一对齐**，所以能按下标准确还原「当前这根是哪一种」，
 * 不需要任何调用顺序假设。 */
interface BarView {
  current?: { limitMark?: number; isDown?: number } | null;
  prev?: { limitMark?: number; isDown?: number } | null;
  next?: { limitMark?: number; isDown?: number } | null;
}

/** 是否主板普通股。创业板/科创板/北交所/指数一律 false。
 *
 * ⚠️ code 形态必须是**大写带点**的 `SH.600570`——这是 ``indices.ts`` 里
 * ``IndexInfo.code`` 的约定，也是 stock_info / index_info 的落库形态
 * （``canonicalCode`` 的产物）。写成 ``sh600570`` 会让**所有**标的都判不出主板，
 * 表现是「涨跌停变色开关打开但一根都不变色」，而且不报错、极难定位。
 *
 * 各板块代码段：
 *   - 沪主板 600/601/603/605  → ±10%
 *   - 深主板 000/001/002      → ±10%（002 原中小板，已并入深主板）
 *   - 创业板 300/301          → ±20%，不判定
 *   - 科创板 688/689          → ±20%，不判定
 */
const MAIN_BOARD_RE = /^(SH\.(?:600|601|603|605)|SZ\.(?:000|001|002))\d{3}$/i;

export function isMainBoard(code: string): boolean {
  return MAIN_BOARD_RE.test(code.trim());
}

/** 理论涨停价 = 前收 × 1.1，四舍五入到分。
 *
 * 必须 round 到 2 位：交易所公布的是**取整后**的价格，用未取整的
 * ``preClose * 1.1`` 去比永远差几分钱，真封板会被判成「没封」。
 */
export function limitUpPrice(preClose: number): number {
  return Math.round(preClose * (1 + MAIN_BOARD_LIMIT) * 100) / 100;
}

/** 理论跌停价 = 前收 × 0.9，四舍五入到分。 */
export function limitDownPrice(preClose: number): number {
  return Math.round(preClose * (1 - MAIN_BOARD_LIMIT) * 100) / 100;
}

/**
 * 判断单根 K 线是否涨跌停。
 *
 * 条件是两个，缺一不可：
 *   1. 收盘价 == 理论涨跌停价（容差 1 分）
 *   2. 收盘价 == 当日最高 / 最低 —— 盘中触及但收盘回落的**不算封板**
 *
 * 用 300 根真实数据验过：78 个涨停样本命中 68，漏掉的 10 个里 9 个是
 * 「盘中触及但收盘没封住」（close < high），按定义就不算，属正确行为。
 * 300 个普通大波动交易日误判 0 个。
 *
 * @param bar       当根 OHLC
 * @param prevClose 前一根的收盘（= 前收盘）
 * @param code      标的代码，用来排除创业板/科创板/指数
 */
export function detectLimit(
  bar: Ohlc | undefined | null,
  prevClose: number | undefined | null,
  code: string,
): LimitKind {
  if (!bar || !isMainBoard(code)) return null;
  if (typeof prevClose !== 'number' || prevClose <= 0) return null;
  const { close, high, low } = bar;
  if (typeof close !== 'number' || typeof high !== 'number' || typeof low !== 'number') {
    return null;
  }

  if (Math.abs(close - limitUpPrice(prevClose)) < PRICE_EPSILON && close === high) {
    return 'up';
  }
  if (Math.abs(close - limitDownPrice(prevClose)) < PRICE_EPSILON && close === low) {
    return 'down';
  }
  return null;
}

/**
 * 涨跌停标记色。
 *
 * 刻意做成**模块级可变状态**而不是每次注册时传进去：``figures[].styles``
 * 回调由 klinecharts 反复调用，它没有「配色变更」的钩子可挂，
 * 只能靠闭包外的变量传递最新值。
 */
let limitColors: { up: string; down: string } = {
  up: DEFAULT_CANDLE_COLORS.limitUp,
  down: DEFAULT_CANDLE_COLORS.limitDown,
};

/** 供测试读回当前生效的涨跌停色。 */
export function getLimitColors(): { up: string; down: string } {
  return { ...limitColors };
}

/**
 * 注册涨跌停着色指标。**只需调用一次**（内部由 klinecharts 按名去重）。
 *
 * 图元用 ``circle``，落在**主图底部的标记行**上——和 ``exDividendMarkers``
 * 的除权「Q」同一行、同一 inset。主图底部是「事件标记带」：除权、涨跌停这类
 * 按天发生的事件都标在那里，既不压 K 线，也不用跟着价格漂。
 *
 * 刻意**不显示图例**（title 置 false）：主图左上角那行图例是所有主图指标共享的
 * （MA5 / MA20…），插一条自己的会把原来的挤掉。
 *
 * 「不画」靠 calc 返回 ``undefined`` 实现，不是靠透明色——见 calc 的注释。
 *
 * @param getCode 取当前标的 code 的回调。着色指标拿不到 symbol，故由外部闭包提供。
 */
export function registerLimitColorIndicator(getCode: () => string) {
  registerIndicator({
    name: LIMIT_COLOR_INDICATOR,
    shouldOhlc: false,
    precision: 2,
    figures: [
      {
        key: 'limitMark',
        /**
         * title 必须是**非字符串**，图例才不会画。
         *
         * 主图的左上角图例是所有 indicator 共享的一行（MA5 / MA20 / VOL…），
         * 本指标挂在 candle_pane 上会往里插一条自己的，图上就变成
         * 「limitColor 涨跌停: 37.48」把原来的 MA 挤掉。
         *
         * 过滤点在 index.esm.js:7273 `if (isString(figure.title))`——
         * 只有 title 是字符串才进 legends 数组。写 '' 不行（空串仍会画 value，
         * 见 7220 行只对 title 做了长度判断），直接把 title 置为 false 最干净：
         * 既不进 legends，也不占用图例行的排版空间。
         */
        title: false as unknown as string,
        type: 'circle',
        styles: ({ data }: { data: BarView }) => {
          // calc 用**另一个 key** 编码涨跌（isDown: 1），limitMark 保持正数收盘价。
          // 不能靠负号编码：负值在「标记钉在底边」的画法下没有意义，
          // 也会让 styles 回调的取色判断失去依据。
          return {
            color: data.current?.isDown ? limitColors.down : limitColors.up,
          };
        },
        /**
         * 把圆点**钉在主图底部那一行**，与除权「Q」标记同行。
         *
         * klinecharts 自己算的 y 是收盘价那根线（index.esm.js:8096 `valueY`，
         * 8111 行 `__assign({x, y: valueY, r}, attrs)`），我返回的 y 会覆盖它。
         * 但 `valueY` 是外层变量、**没传进回调**，而且按价格定位也不对——
         * 标记带要的是固定行，不是「这根 K 线的某个相对位置」。
         *
         * 两个 `bounding` 是**同一个对象**：指标图元的 `attrs` 回调
         * （:8100 形参）和 overlay 的 ``createPointFigures``（:8983
         * ``widget.getBounding()``）都取自 ``pane.getBounding()``。inset 取
         * 同一个常数，两种标记就在同一条水平线上。
         */
        attrs: ({ bounding }: { bounding?: { height?: number } }) => {
          const height = bounding?.height;
          // 拿不到 pane 包围盒时不要瞎钉 y，交给 klinecharts 用收盘价那根线兜底。
          if (typeof height !== 'number') return {};
          return { y: height - MARK_BOTTOM_INSET };
        },
      },
    ],
    /**
     * 指标值 = 涨跌停那天的收盘价，其余天一律 **undefined**。
     *
     * ⚠️ 这里**必须返回真实数值**才能画出来：klinecharts 画图元前有硬门槛
     * （index.esm.js:8095 `if (isValid(currentData?.[figure.key]))`），
     * 不满足就整根跳过。
     *
     * ⚠️ 值本身**不再用于定位**（y 已由 attrs 钉到标记行），它现在只承担
     * 「这一根要不要画」这一个职责。保留收盘价而不是随便写个 1，是为了让
     * 日后想改成「跟价格走」时不用重新想这个字段的含义。
     *
     * ⚠️⚠️ 「不画」**必须用 undefined，不能用 NaN**。isValid 的实现是：
     * ```js
     * function isValid(value) { return value !== null && value !== undefined; }
     * ```
     * NaN 不是 null 也不是 undefined，**isValid(NaN) === true**——
     * 用 NaN 当哨兵的话每一根都会画出一个圆点。
     *
     * `isDown` 单独一个 key 编码涨跌：styles 回调拿到的 current 就是
     * `indicator.result[dataIndex]`（index.esm.js:3142-3144），按下标准确还原，
     * 不依赖任何调用顺序假设。
     */
    calc: (dataList: Ohlc[]) => {
      const code = getCode();
      return dataList.map((bar, i) => {
        const kind = detectLimit(bar, dataList[i - 1]?.close, code);
        if (!kind) return { limitMark: undefined, isDown: 0 };
        return { limitMark: bar.close, isDown: kind === 'down' ? 1 : 0 };
      });
    },
  });
}

/** 该主图上是否已挂着着色指标。
 *
 * ``getIndicators`` / ``removeIndicator`` 收的是 ``IndicatorFilter``
 * （``Partial<Pick<Indicator, 'id'|'paneId'|'name'>>``），**不是位置参数**，
 * 所以按 ``{paneId, name}`` 两个字段一起过滤。 */
function hasLimitIndicator(chart: Chart): boolean {
  const list = chart.getIndicators?.({ paneId: CANDLE_PANE_ID, name: LIMIT_COLOR_INDICATOR }) ?? [];
  return list.length > 0;
}

/** 增删着色指标。开关切换走这里，不重建 chart。 */
export function setLimitColorEnabled(chart: Chart, enabled: boolean) {
  const has = hasLimitIndicator(chart);
  if (enabled && !has) {
    chart.createIndicator({ paneId: CANDLE_PANE_ID, name: LIMIT_COLOR_INDICATOR }, false);
  } else if (!enabled && has) {
    chart.removeIndicator({ paneId: CANDLE_PANE_ID, name: LIMIT_COLOR_INDICATOR });
  }
}

/**
 * 应用四种颜色。
 *
 * 前两种（普通涨跌）走 ``setStyles``——它们是 klinecharts 的全局 up/down 槽位，
 * 改了就全局生效，符合预期。涨跌停两种没有对应的全局槽位（原因见文件头），
 * 只能更新模块级变量，由着色指标的 styles 回调读取。
 */
export function applyCandleColors(chart: Chart, colors: CandleColors) {
  const { up, down } = colors;
  (chart as unknown as { setStyles: (v: unknown) => void }).setStyles({
    candle: {
      bar: {
        upColor: up,
        downColor: down,
        upBorderColor: up,
        downBorderColor: down,
        upWickColor: up,
        downWickColor: down,
      },
    },
  });
  limitColors = { up: colors.limitUp, down: colors.limitDown };
}

/** app_config 里四个颜色各自的键。改动时后端 app_config.ITEM_NAMES 也要同步登记。 */
const CONFIG_KEY_BY_COLOR: Record<keyof CandleColors, string> = {
  up: 'candle_up',
  down: 'candle_down',
  limitUp: 'candle_limit_up',
  limitDown: 'candle_limit_down',
};

/** 取某个颜色对应的 app_config 键名（供保存时拼键）。 */
export function colorConfigKey(key: keyof CandleColors): string {
  return CONFIG_KEY_BY_COLOR[key];
}

/** 从 app_config 读回颜色，缺项回落到默认值。
 *
 * ⚠️ 必须是**逐项**兜底：app_config 里没配过的键不会出现在返回值里，
 * 而用户在设置里只改了涨停跌停色时，另外两项也要保住 DARK_THEME 的原值。
 */
export function colorsFromConfig(cfg: Record<string, unknown>): CandleColors {
  const pick = (key: keyof CandleColors): string => {
    const fallback = DEFAULT_CANDLE_COLORS[key];
    const v = cfg[CONFIG_KEY_BY_COLOR[key]];
    // 只接受合法 hex。后端存的是字符串（app_config._parse 对 "#ff3b30"
    // 转 int/float 失败会原样返回），脏值一律回落默认色，不往 canvas 传。
    return typeof v === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(v) ? v : fallback;
  };
  return { up: pick('up'), down: pick('down'), limitUp: pick('limitUp'), limitDown: pick('limitDown') };
}
