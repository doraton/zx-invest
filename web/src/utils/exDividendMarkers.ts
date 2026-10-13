/**
 * 主图下方的「除息除权」标记（蓝色 `$`）。
 *
 * klinecharts v10 **没有内建的除权标记 API**（dist/index.d.ts 里搜 dividend /
 * adjust / exRight 全无命中），所以走自定义 overlay。
 *
 * 为什么 overlay 能画在「pane 底部」而不是跟着价格走：
 * klinecharts 每帧重画 overlay 前都会重新调一次 `createPointFigures`（见
 * index.esm.js:8937-8940 `_drawOverlay` → `getFigures` → 8984），并把
 *   - `coordinates[0].x` = `dataIndexToCoordinate(dataIndex)`（8913-8936）——**每帧重算**，所以滚动/缩放会自动跟随；
 *   - `bounding` = 当前 pane 的实时包围盒。
 * 因此只要把 y 写死成 `bounding.height - N`，标记就一直贴在主图底边。
 * 用 `point.value` 反算 y 是错的：它会跟着可见价格区间上下漂。
 *
 * 点击：`_drawOverlay` 之后 `drawFigures` 会给每个返回的 figure 挂
 * `_createFigureEvents(overlay, ...)`（index.esm.js:8953），所以
 * `createPointFigures` 返回的图元天然可点，overlay 上的 `onClick` 一定会触发。
 */
import { registerOverlay } from 'klinecharts';
import type { Chart, KLineData } from 'klinecharts';

/** overlay 名（klinecharts 内以字符串索引注册表）。 */
export const EX_DIVIDEND_OVERLAY = 'exDividend';

/** 主图 pane id，即 klinecharts 的 PaneId.CANDLE。 */
const CANDLE_PANE_ID = 'candle_pane';

/** 标记距主图底边的像素。图标直径 14px（= MARKER_R*2），上留 7px 呼吸位。 */
const BOTTOM_INSET = 14;

/* 图标尺寸与配色全部是从参考图**逐像素量出来的**，不是估的
 * （当时写了个纯 Python PNG 解码脚本量的，量完已删）。
 *
 *   主色 rgb(75, 146, 239) = #4B92EF，最亮处 #4F9AFD
 *   外接框 14x14 px → 外圈圆环半径 7.0
 *   径向剖面：r≈7 覆盖率 0.67（圆环）、r≈5 覆盖率 0.04（环间空隙）、
 *             r≈4 覆盖率 0.79（字母 Q 的碗）、碗内 r<3 接近 0
 *   即「圆环 r=7 + 里面一个 Q」，整体直径 14px。
 *
 * 原图最外圈那圈很淡的像素（强度 8~45）是辉光，**没有复刻**：
 * 按要求只保留圆环和 Q 两层，不要蓝色底色。
 *
 * ⚠️ 圆环线宽**故意比原图细**：原图圆环实测 2px，但 Q 是字体渲染的，
 * size=10 bold 的 Q 笔画只有 ~1.5px，两者并排时圆环明显更粗、显笨重。
 * 现按「圆环线宽 ≈ Q 的笔画粗细」对齐，取 1.5px（canvas lineWidth 允许小数）。 */
const MARKER_R = 7;      // 外圈圆环半径
const MARKER_SW = 1.5;   // 圆环线宽：对齐 Q 的笔画粗细（原图实测 2，这里调细）
const MARKER_Q = 10;     // 字母 Q 字号（参考图里 Q 字形约 8x8px）

const MARKER_COLOR = '#4b92ef';

/** 本模块已创建的 overlay 不单独记账：按 name 批量删即可（见 clearExDividendMarkers）。 */

/** overlay 的 extendData：带上被点的除权日，点击回调从这里取。 */
interface ExOverlayData {
  date: string;
}

/** 业务侧回调：点中某个除权日。由 setExDividendClickHandler 注入（避免循环依赖）。 */
let clickHandler: ((date: string) => void) | null = null;

export function setExDividendClickHandler(fn: (date: string) => void | null): void {
  clickHandler = fn;
}

/**
 * 注册 overlay 模板。**幂等**，可以在每次挂载时都调。
 *
 * `registerOverlay` 用对象字面量当注册表 key，同名重复注册会抛
 * 「already exists」，所以这里用模块级 flag 兜住 React 严格模式下的双次挂载。
 */
let registered = false;
export function registerExDividendOverlay(): void {
  if (registered) return;
  registered = true;
  registerOverlay<ExOverlayData>({
    name: EX_DIVIDEND_OVERLAY,
    /** 0 = 不进入画点流程，直接由 createPointFigures 出图。 */
    totalStep: 0,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    /** 标记不是可拖拽的对象，锁死免得用户把它拖走。 */
    lock: true,
    createPointFigures: ({ coordinates, bounding }) => {
      const x = coordinates[0]?.x;
      if (typeof x !== 'number' || !Number.isFinite(x)) return [];
      const y = bounding.height - BOTTOM_INSET;
      return [
        // 两个图元叠出「蓝色圆环 + 蓝色 Q」，尺寸/配色见上面的量图注释。
        // 曾经还有一层半透明蓝 halo（r=13）当辉光，已按要求去掉——
        // 它会让图标带一块蓝色底色，压在深色 K 线上看着发脏。
        //
        // 两个图元都会自动挂上点击事件（见文件头 drawFigures 的说明），
        // 点圆环、点 Q 触发的是同一个 onClick。
        {
          key: 'ring',
          type: 'circle',
          attrs: { x, y, r: MARKER_R },
          styles: { style: 'stroke', borderColor: MARKER_COLOR, borderSize: MARKER_SW },
        },
        {
          key: 'q',
          type: 'text',
          attrs: { x, y, text: 'Q', align: 'center', baseline: 'middle' },
          styles: {
            style: 'fill',
            color: MARKER_COLOR,
            size: MARKER_Q,
            weight: 'bold',
            // ⚠️ **必须显式给 backgroundColor**。klinecharts 的默认 overlay text
            // 样式里根本没有这个键（index.esm.js:11460-11476 只有 color/border*），
            // 而 `drawText` 画字之前会先 `drawRect(..., {color: styles.backgroundColor})`
            // 当底衬。传 undefined 时 `drawCircle` 的 solid 判据
            // `(!isString(color) || !isTransparent(color))` 会判 true，
            // `ctx.fillStyle = undefined` 是无效赋值 → canvas 静默沿用上一次的
            // fillStyle（当前画布上任意一个 fill 图元的颜色，可能是蜡烛的红/绿）。
            // 结果是每个 Q 后面多一块来路不明的方块。
            backgroundColor: 'transparent',
            // 内建 padding 是四边各 4px，会让命中区比字形大一圈。
            // 对齐不受影响（getTextRect 的 startX = x - width/2，左右 padding 对称），
            // 这里清零只是把点击命中区收紧到字形本身。
            paddingLeft: 0,
            paddingTop: 0,
            paddingRight: 0,
            paddingBottom: 0,
          },
        },
      ];
    },
    onClick: ({ overlay }) => {
      const date = (overlay as unknown as { extendData?: ExOverlayData }).extendData?.date;
      if (date) clickHandler?.(date);
    },
  });
}

/**
 * 清掉全部除权标记。
 *
 * 用 `removeOverlay({ name })` 按名批量删，而不是自己记 id 数组：
 * `OverlayFilter` 支持按 name 过滤（index.d.ts:979 / 1146），
 * 少维护一份与图表状态并行的列表，也就不会出现「chart 已 dispose 还在删 id」。
 */
export function clearExDividendMarkers(chart: Chart | null): void {
  if (!chart) return;
  try {
    chart.removeOverlay({ name: EX_DIVIDEND_OVERLAY });
  } catch {
    // chart 已 dispose 时会抛；此时标记跟着画布一起没了，无所谓。
  }
}

/**
 * 按当前图上的数据重建全部除权标记。
 *
 * 必须在**每次数据变动后**调（包括向前翻历史）：forward 是「前插」，
 * 已加载区里每根的 dataIndex 都会整体后移，按旧下标建的 overlay 全部错位。
 * 与其增量维护下标，不如拿 `chart.getDataList()` + 已累计的除权日
 * 整体重算——最多几十个标记，代价可忽略。
 *
 * @param chart    图表实例
 * @param exDates  已加载范围内所有除权日的 ``K 线 timestamp -> 真实除权日``
 *                 （跨翻页累计）。值必须来自后端 ``xdxr_date``，不要在前端
 *                 用 timestamp 反推：周线上标记落在**周五**，而除权日可能是周三，
 *                 两者不是同一天，反推出来的高亮项会一条都对不上。
 */
export function renderExDividendMarkers(chart: Chart, exDates: Map<number, string>): void {
  clearExDividendMarkers(chart);
  const dataList = chart.getDataList() as KLineData[];
  for (let i = 0; i < dataList.length; i++) {
    const date = exDates.get(dataList[i].timestamp);
    if (!date) continue;
    const id = chart.createOverlay<ExOverlayData>({
      name: EX_DIVIDEND_OVERLAY,
      paneId: CANDLE_PANE_ID,
      points: [{ dataIndex: i }],
      extendData: { date },
      lock: true,
      zLevel: 100,
      // 悬停时不要临时抬到最上层：标记是背景信息，
      // 抬起来反而会盖住同一位置的蜡烛。
      fixedZLevel: true,
    });
    // createOverlay 传单个值时返回 string，传数组时返回数组，两种都要收下。
    if (Array.isArray(id)) continue;
    void id;
  }
}