import { useMemo } from 'react';
import { Modal } from 'antd';
import { LeftOutlined, RightOutlined } from '@ant-design/icons';
import type { KLineData as KCKLineData } from 'klinecharts';
import { formatBarDate } from '../utils/formatDate';
import './RegionStatsModal.css';

/** 涨跌着色。A 股涨红跌绿，与项目里蜡烛、十字线浮窗保持同一套
 *  （见 MarketChart.css 的 `.crosshair-tooltip .val.up/.down`）。 */
type Tone = 'up' | 'down' | 'flat';

interface StatCell {
  label: string;
  tone: Tone;
  /** 已格式化的展示文本（不是原始数值）。 */
  text: string;
}

/** 框选区间的统计结果。区间非法（空 / from > to）时整个是 null。 */
interface RegionStats {
  /** 区间首根的时间戳 / 末根的。 */
  startTs: number;
  endTs: number;
  count: number;
  start: number;
  high: number;
  low: number;
  final: number;
  /** 涨跌价 = 最终价 − 起始价。 */
  chg: number;
  /** 涨跌幅 %，与 chg 同号。 */
  chgPct: number;
  /** 收盘高于前一根收盘的根数 / 低于的根数。 */
  up: number;
  down: number;
}

/**
 * 按 dataIndex 区间统计题头各项。
 *
 * 口径：
 *   起始价 = 区间**首根收盘**，最终价 = 区间**末根收盘**
 *     （用收盘不用开盘：两者同在参考图里对不上时无法区分，收盘与「最终价」对称、
 *      也和涨跌幅的分母自洽。）
 *   涨跌幅 = (最终 − 起始) / 起始 —— 参考图 23.08 → 21.43 正是 −7.15%。
 *   最高/最低价 = 区间内 high 的最大值 / low 的最小值。
 *   上涨/下跌 = 收盘与**前一根收盘**比较。
 *     ⚠️ 区间首根的前收取自区间外（all[from−1]），所以它同样参与统计；
 *     from=0（前面没有数据）时跳过它。这也是参考图里 10 + 14 = 24 而
 *     总个数是 25 的由来。
 */
function computeRegionStats(all: KCKLineData[], from: number, to: number): RegionStats | null {
  if (all.length === 0) return null;
  const lo = Math.max(0, Math.min(from, all.length - 1));
  const hi = Math.max(lo, Math.min(to, all.length - 1));
  const bars = all.slice(lo, hi + 1);
  if (bars.length === 0) return null;

  let high = -Infinity;
  let low = Infinity;
  for (const b of bars) {
    if (b.high > high) high = b.high;
    if (b.low < low) low = b.low;
  }

  let up = 0;
  let down = 0;
  for (let i = lo; i <= hi; i++) {
    const prev = all[i - 1]?.close;
    if (typeof prev !== 'number' || !Number.isFinite(prev)) continue;
    const close = all[i].close;
    if (close > prev) up++;
    else if (close < prev) down++;
  }

  const start = bars[0].close;
  const final = bars[bars.length - 1].close;
  const chg = final - start;
  return {
    startTs: bars[0].timestamp,
    endTs: bars[bars.length - 1].timestamp,
    count: bars.length,
    start,
    high,
    low,
    final,
    chg,
    chgPct: start > 0 ? (chg / start) * 100 : 0,
    up,
    down,
  };
}

/** 与基准比较得出着色：高于基准红、低于基准绿、相等或缺数据走中性。 */
function toneVs(v: number | null | undefined, base: number | null | undefined): Tone {
  if (typeof v !== 'number' || typeof base !== 'number') return 'flat';
  if (!Number.isFinite(v) || !Number.isFinite(base)) return 'flat';
  return v > base ? 'up' : v < base ? 'down' : 'flat';
}

/** 缺数据 / 非有限值统一占位。 */
const PLACEHOLDER = '--';

function fmtNum(v: number | null | undefined, digits = 2): string {
  return typeof v === 'number' && Number.isFinite(v) ? v.toFixed(digits) : PLACEHOLDER;
}

/** 带符号：正数补 `+`，和行情页 `+0.66` 的写法一致。 */
function fmtSigned(v: number | null | undefined, digits = 2): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return PLACEHOLDER;
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)}`;
}

interface Props {
  open: boolean;
  onClose: () => void;
  /** chart.getDataList() 全量。弹窗自己按区间切——步进箭头改区间时不必重新喂数据。 */
  bars: KCKLineData[];
  /** 框选区间的 dataIndex，闭区间。 */
  from: number;
  to: number;
}

/** 框选 K 线后的「区域统计」弹窗。 */
export default function RegionStatsModal({ open, onClose, bars, from, to }: Props) {
  const stats = useMemo(() => computeRegionStats(bars, from, to), [bars, from, to]);

  // 第一列：起止价、区间高低点、涨跌。
  // 最高/最低价按「与起始价比」着色，与参考图一致（最高价高于起始价 → 红）。
  const colLeft: StatCell[] = [
    { label: '起始价', tone: 'flat', text: fmtNum(stats?.start) },
    { label: '最高价', tone: toneVs(stats?.high, stats?.start), text: fmtNum(stats?.high) },
    { label: '最低价', tone: toneVs(stats?.low, stats?.start), text: fmtNum(stats?.low) },
    { label: '最终价', tone: toneVs(stats?.final, stats?.start), text: fmtNum(stats?.final) },
    { label: '涨跌价', tone: toneVs(stats?.chg, 0), text: fmtSigned(stats?.chg) },
    {
      label: '涨跌幅',
      tone: toneVs(stats?.chgPct, 0),
      text: stats ? `${fmtSigned(stats.chgPct)}%` : PLACEHOLDER,
    },
  ];

  // 第二列：涨跌根数，恒定红 / 绿。
  const colRight: StatCell[] = [
    { label: '上涨', tone: 'up', text: stats ? String(stats.up) : PLACEHOLDER },
    { label: '下跌', tone: 'down', text: stats ? String(stats.down) : PLACEHOLDER },
  ];

  return (
    <Modal open={open} onCancel={onClose} footer={null} title="区域统计" width={490} className="region-stats-modal">
      <div className="rs-wrap">
        {/* 第一行：起止日期（各带左右步进箭头）+ 总个数 */}
        <div className="rs-range-row">
          <span className="rs-range-label">开始</span>
          <span className="rs-date">
            <span className="rs-date-text">{stats ? formatBarDate(stats.startTs, false) : '--/--/--'}</span>
            <span className="rs-step" aria-hidden="true">
              <LeftOutlined />
            </span>
            <span className="rs-step" aria-hidden="true">
              <RightOutlined />
            </span>
          </span>

          <span className="rs-range-label">结束</span>
          <span className="rs-date">
            <span className="rs-date-text">{stats ? formatBarDate(stats.endTs, false) : '--/--/--'}</span>
            <span className="rs-step" aria-hidden="true">
              <LeftOutlined />
            </span>
            <span className="rs-step" aria-hidden="true">
              <RightOutlined />
            </span>
          </span>

          <span className="rs-range-label">总个数:</span>
          <span className="rs-range-value">{stats ? String(stats.count) : PLACEHOLDER}</span>
        </div>

        {/* 主体两列。第一列 6 行、第二列 2 行，右列下面 4 行留空（不补占位内容）。
            ⚠️ 必须是两个独立的列容器，不能是一个 2 列 grid 把两列数组拼起来铺：
            grid 默认**行**优先填充，[起始价,最高价,…,上涨,下跌] 这样进去，
            「最高价」会落到第 1 行的右列，第二列整体错位。 */}
        <div className="rs-cols">
          {[colLeft, colRight].map((col, ci) => (
            <div key={ci} className="rs-col">
              {col.map((cell) => (
                <div key={cell.label} className="rs-cell">
                  <span className="rs-cell-label">{cell.label}</span>
                  <span className={`rs-cell-value rs-${cell.tone}`}>{cell.text}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </Modal>
  );
}