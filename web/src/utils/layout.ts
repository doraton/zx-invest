/**
 * 版面比例：两条分界线的位置。
 *
 *   topRatio   横线距上比例 = 主图 pane 高 ÷ 可分配高度
 *   rightRatio 竖线距右比例 = 右列宽 ÷ chart-row 宽
 *
 * 持久化在后端 `app_config` 表（键 `top_ratio` / `right_ratio`），
 * 走已有的 GET/PUT /api/config，**不再用 localStorage** ——
 * 两处真相迟早漂移，后端 app_config.py 的模块头也是这么要求的。
 * 库里没有这两个键 = 用户没改过 = 用这里的默认值。
 *
 * klinecharts 的 pane 高度分配规则（dist/index.esm.js:14802-14823）：
 * 先扣掉 X 轴和分隔线得到 remaining，然后**除主图外的 pane 按 options.height 固定，
 * 主图 pane 吸收全部余数**（14804 行优先选 id === CANDLE 的那个）。
 *
 * 所以横线位置不能直接设主图高，只能由 4 个副图高反推 —— 这也是
 * `subPaneHeight` 存在的原因。设主图高会被静默忽略。
 */

/**
 * klinecharts 的 X 轴实测高度 = axisLine 1 + tickLine.length 3
 * + tickText.marginStart 4 + marginEnd 4 + tickText.size 12。
 * 导出是因为 MarketChart 要用它算 --sub-charts-total，让右列底边和副图对齐。
 * 改了 DARK_THEME.xAxis 里任何一项，这个数都要跟着重算。
 */
export const X_AXIS_H = 24;

const SEPARATOR_H = 1;
/** 4 个副图（VOL / MACD / KDJ / BOLL占位）+ 1 个主图 */
const SUB_PANE_COUNT = 4;
const CONTENT_PANE_COUNT = SUB_PANE_COUNT + 1;

export const DEFAULT_TOP_RATIO = 0.42;
export const DEFAULT_RIGHT_RATIO = 0.225;
export const TOP_RATIO_RANGE: [number, number] = [0.25, 0.65];
export const RIGHT_RATIO_RANGE: [number, number] = [0.15, 0.45];

export interface LayoutRatio {
  topRatio: number;
  rightRatio: number;
}

/** app_config 里对应的两个键。与 LayoutRatio 的字段一一对应。 */
export const RATIO_CONFIG_KEYS = {
  topRatio: 'top_ratio',
  rightRatio: 'right_ratio',
} as const;

export const DEFAULT_RATIO: LayoutRatio = {
  topRatio: DEFAULT_TOP_RATIO,
  rightRatio: DEFAULT_RIGHT_RATIO,
};

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

function coerce(v: unknown, fallback: number, range: [number, number]): number {
  // 后端 _parse 已把 "0.42" 还原成 number，但库里也可能被手改成字符串 / 空串
  const n = typeof v === 'string' ? Number(v) : v;
  if (typeof n !== 'number' || !Number.isFinite(n)) return fallback;
  return clamp(n, range[0], range[1]);
}

/**
 * 从后端 app_config 的返回值构造比例对象。
 * 键不存在（用户没配过）走默认值，值非法（被手改过）夹回合法区间。
 */
export function ratioFromConfig(cfg: {
  [RATIO_CONFIG_KEYS.topRatio]?: unknown;
  [RATIO_CONFIG_KEYS.rightRatio]?: unknown;
}): LayoutRatio {
  return {
    topRatio: coerce(cfg[RATIO_CONFIG_KEYS.topRatio], DEFAULT_TOP_RATIO, TOP_RATIO_RANGE),
    rightRatio: coerce(cfg[RATIO_CONFIG_KEYS.rightRatio], DEFAULT_RIGHT_RATIO, RIGHT_RATIO_RANGE),
  };
}

/**
 * 比例 → 落库值。统一收敛到 3 位小数：
 * Slider step 是 0.005，而 0.42 + 0.005 在 JS 里是 0.42500000000000004，
 * 原样存下去后读回来会略微偏出刻度，Slider 也会跟着顿一下。
 */
export function ratioToConfig(r: LayoutRatio): {
  [RATIO_CONFIG_KEYS.topRatio]: number;
  [RATIO_CONFIG_KEYS.rightRatio]: number;
} {
  const r3 = (v: number) => Math.round(v * 1000) / 1000;
  return {
    [RATIO_CONFIG_KEYS.topRatio]: r3(coerce(r.topRatio, DEFAULT_TOP_RATIO, TOP_RATIO_RANGE)),
    [RATIO_CONFIG_KEYS.rightRatio]: r3(coerce(r.rightRatio, DEFAULT_RIGHT_RATIO, RIGHT_RATIO_RANGE)),
  };
}

/** K 线区高 → 能分给 pane 的高度（已扣掉 X 轴和 4 条分隔线） */
export function distributableHeight(areaH: number): number {
  return Math.max(areaH - X_AXIS_H - (CONTENT_PANE_COUNT - 1) * SEPARATOR_H, 0);
}

/** 比例 → 每个副图的像素高。主图由 klinecharts 吸收余数得出，不用管。 */
export function subPaneHeight(areaH: number, topRatio: number): number {
  const dist = distributableHeight(areaH);
  return Math.max(30, Math.round((dist * (1 - topRatio)) / SUB_PANE_COUNT));
}

/**
 * 主图 pane 的实际高度。
 * 不直接用 dist × topRatio —— 副图高取整后会有零头，主图拿的是
 * 「dist − 4 × 取整后的副图高」，两者能差 1~2px。设置弹窗显示这个数才准。
 */
export function mainPaneHeight(areaH: number, topRatio: number): number {
  const dist = distributableHeight(areaH);
  return Math.max(0, dist - subPaneHeight(areaH, topRatio) * SUB_PANE_COUNT);
}

/**
 * 副图区在右列占的总高 = 4 个副图 + X 轴 + 4 条 pane 分隔线。
 *
 * 必须**整个值**写进 --sub-charts-total，不能只写 4×副图 + X 轴：
 * 分隔线那 4px 同样是占位的，少算就会让右列底边比副图底边高 4px，
 * 红线对不齐。也不能指望 CSS 里那个 calc(4 * var(--sub-chart-h) + var(--x-axis-h))
 * 自动跟上 —— 自定义属性是在**声明它的那一层**（:root）就把 var() 替换掉的，
 * 不是在使用点替换，所以覆盖 .market-chart-page 上的 --sub-chart-h 根本影响不到它。
 */
export function subChartsTotalHeight(areaH: number, topRatio: number): number {
  return subPaneHeight(areaH, topRatio) * SUB_PANE_COUNT + X_AXIS_H + (CONTENT_PANE_COUNT - 1) * SEPARATOR_H;
}