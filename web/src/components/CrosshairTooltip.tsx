import type { KLineData } from 'klinecharts';
import { formatBarDate } from '../utils/formatDate';

interface Props {
  /** null = 隐藏浮窗（无 hover 或 crosshair 已离开 chart） */
  data: {
    kLine: KLineData;
    prevClose: number | null;
    /** 库里才有、klinecharts 不认的字段。三个来源都有：日线（kline_day，官方除权口径）、
     *  派生周期（kline_period，原始口径）、复权现场聚合（现算）。
     *  全都拿不到时才回落到本地按前一根 close 算。 */
    extra?: {
      chgAmt: number | null;
      chgPct: number | null;
      /** 交易所给的已除权昨收。**只有日线有**；派生周期恒为 null，
       *  回落到前一根 close（值本来就一样，见 docs/pre-close-display-plan.md）。 */
      preClose: number | null;
      /** 该桶内的真实除权日。除权日的昨收不会提示任何异常，只能靠这一行说明。 */
      xdxrDate?: string;
    };
    x: number;
    containerWidth: number;
  } | null;
  /** 价格小数位（指数 2 / 个股 2） */
  pricePrecision?: number;
  /** 成交量小数位（指数 0 / 个股视股票而定） */
  volumePrecision?: number;
}

function formatVolume(v: number, precision: number): string {
  // 大数压缩：和 klinecharts 默认 y 轴 VOL 显示保持一致（K/M/B）
  if (Math.abs(v) >= 1e9) return `${(v / 1e9).toFixed(precision)}B`;
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(precision)}M`;
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(precision)}K`;
  return v.toFixed(precision);
}

export default function CrosshairTooltip({ data, pricePrecision = 2, volumePrecision = 0 }: Props) {
  if (!data) return null;
  const { kLine, prevClose, extra, x, containerWidth } = data;

  // 昨收基准：**库里存了就用库里的**。这是内部基准，**不单独显示成一行**——
  // 浮窗只摆今开/最高/最低/收盘，昨收只用来给这四行定红绿、以及兜底算涨跌。
  //
  // 但它必须是库里的值而不是前一根 close，否则除权日浮窗自相矛盾：
  // 2026-06-09 用前一根 close 22.26 算出来是 +0.04%，库里 chg_pct 却是 +0.95%。
  //   日线     kline_day.pre_close = 交易所给的已除权昨收（22.06），与库里的
  //            涨跌两列同一个分母，两者才自洽
  //   派生周期 kline_period 没有这一列（恒等于前一根 close，纯冗余已删）→ 回落 prevClose
  //   复权档   pre_close 与四价一起乘过因子，与图上价格同一基准
  // 一个 ?? 同时覆盖三种情况，不需要「当前是哪种周期」的分支。
  const baseClose = extra?.preClose ?? prevClose;

  // 涨跌优先取**库里存的** chg_amt / chg_pct，不在前端重算。
  // 三个来源的口径**不一样**，所以不存在一个「到处都对」的本地算法：
  //   kline_day      官方 pct_chg，基准是**已除权**的 pre_close
  //   kline_period   原始口径，pre_close 就是上一根 close（表内自洽）
  //   复权现场聚合   折算到复权价之后再算
  // 前端不知道当前是哪一条，统一走「库里给什么就显示什么」——
  // 展示层不做口径判断，这是唯一能在三种来源下都正确的做法。
  //
  // 本地算法只作最后兜底，三个来源都拿不到时（比如首根没有上一根）才用。
  // 兜底也用 baseClose，保证「昨收 + 涨跌 = 今价」在浮窗内部恒成立。
  const localChange = baseClose ? kLine.close - baseClose : null;
  const localPct = baseClose && localChange !== null
    ? (localChange / baseClose) * 100
    : null;

  const change = extra?.chgAmt ?? localChange;
  const changePct = extra?.chgPct ?? localPct;

  /** 与昨收基准比较得出配色 class：A 股习惯涨红跌绿，跟 ChartHeader 一致。
   *  比的是 baseClose（= 库里 pre_close ?? 前一根 close）——**昨收虽然不占一行**，
   *  它仍是这四行着色的唯一基准，所以除权日的颜色不会被未除权的跳空带偏。 */
  const vs = (v: number) => {
    if (baseClose === null) return '';
    if (v > baseClose) return ' up';
    if (v < baseClose) return ' down';
    return '';
  };
  /** 涨跌两行本身就是差值/比率，符号即方向，直接用符号判。 */
  const bySign = (v: number | null) => {
    if (v === null || v === 0) return '';
    return v > 0 ? ' up' : ' down';
  };

  // 根据十字线 x 位置动态切换 anchor，跟随光标跳动：
  //   - 十字线在容器中心偏右 → tooltip 钉在容器左上（不挡右半区蜡烛）
  //   - 十字线在容器中心偏左 → tooltip 钉在容器右上（不挡左半区蜡烛）
  //   - top 固定 28px = 1 号红线下方 + MA legend 行下方（不遮主图参数）
  //   - anchor-right 时 right: 100px 让出 y 轴价格标签区（不超出指标右边）
  const onRightHalf = x > containerWidth / 2;
  const p = pricePrecision;
  return (
    <div
      className={`crosshair-tooltip ${onRightHalf ? 'anchor-left' : 'anchor-right'}`}
      role="tooltip"
    >
      {/* 日期独占一行、不带题头，整行居中当标题 */}
      <div className="row row-date"><span className="val date">{formatBarDate(kLine.timestamp)}</span></div>
      {/* 开/高/低/收四行与昨收基准比较后着色（涨红跌绿，同 ChartHeader）；
          昨收基准本身不显示，涨跌两行按符号着色。 */}
      <div className="row"><span className="lbl">开盘价</span> <span className={`val${vs(kLine.open)}`}>{kLine.open.toFixed(p)}</span></div>
      <div className="row"><span className="lbl">最高价</span> <span className={`val${vs(kLine.high)}`}>{kLine.high.toFixed(p)}</span></div>
      <div className="row"><span className="lbl">最低价</span> <span className={`val${vs(kLine.low)}`}>{kLine.low.toFixed(p)}</span></div>
      <div className="row"><span className="lbl">收盘价</span> <span className={`val${vs(kLine.close)}`}>{kLine.close.toFixed(p)}</span></div>
      <div className="row"><span className="lbl">涨跌价</span> <span className={`val${bySign(change)}`}>{change === null ? '' : `${change >= 0 ? '+' : ''}${change.toFixed(p)}`}</span></div>
      <div className="row"><span className="lbl">涨跌幅</span> <span className={`val${bySign(changePct)}`}>{changePct === null ? '' : `${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%`}</span></div>
      {/* 除权标注：**只有周期K 需要**，且是刚需。
          周期 bar 的昨收恒等于上一根 close，数字和平常没有任何区别，
          用户只会把基准下调造成的跳空当成暴跌（2008-05 月线 −34.17%，实际 5-28 送转）。
          日线不需要靠这一行解释 —— 它的昨收显示 22.06、图上前一根 22.26，
          用户自己就看得出这天动过基准了。 */}
      {extra?.xdxrDate && (
        <div className="row row-xdxr"><span className="val">除权 {extra.xdxrDate} · 图为原始价</span></div>
      )}
      {/* 成交量 / 成交额都直接来自库里（klinecharts 的 volume / turnover 两个
          自带字段，原样穿过 setData），不再自己算，也不再留空占位。
          换手率仍是占位：它 = 成交量 / 流通股本，而 stock_info.float_share
          当前全是 NULL，得先补流通股本才算得出来。 */}
      <div className="row"><span className="lbl">成交量</span> <span className="val">{formatVolume(kLine.volume ?? 0, volumePrecision)}</span></div>
      <div className="row"><span className="lbl">成交额</span> <span className="val">{formatVolume(kLine.turnover ?? 0, volumePrecision)}</span></div>
      <div className="row"><span className="lbl">换手率</span> <span className="val" /></div>
    </div>
  );
}
