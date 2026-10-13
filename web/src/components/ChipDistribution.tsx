import { useEffect, useMemo, useRef, useState } from 'react';
import './ChipDistribution.css';
import { API_BASE } from '../config/api';

interface KLine {
  datetime: string;
  open: number;
  high: number;
  low: number;
  close: number;
  /** 成交量（手 / 0AMV 的点）。字段名与库列 `volume` 一致。 */
  volume: number;
}

interface Bucket {
  price: number;
  volume: number;
}

interface Props {
  code: string;
  category: number;
  /** 近多少根 K 线计算筹码（默认近 1K = 200 根） */
  count?: number;
  /** 复权方式：'none' = 不复权（后端当前口径），'qfq' = 前复权，'hfq' = 后复权。
   *  与主图共用同一个值，保证"图上的价"和"筹码的成本价"是同一套口径。 */
  adjust?: 'none' | 'qfq' | 'hfq';
  /** 当前价，用于分盈/亏色 */
  currentPrice: number;
  /** 主图十字线位置：
   *  - 有值（非 null）→ 算 dataIndex <= i 的 K 线（历史成本分布演化）
   *  - null / 未传   → 鼠标已移出图表区，十字线与浮窗消失，但成本分布**仍定在最后一根**
   *                    （= 全量 `[0, last]`），不是「只看最后一根」 */
  upToIndex?: number | null;
  /** 当前指数 / 个股名（顶部展示用） */
  symbolName?: string;
}

const LEVELS = 80;

/**
 * 计算筹码分布：把每根 K 线的成交量按当日 [low, high] 区间均匀分配到价格桶，
 * 再把所有天的贡献累加。
 */
function computeChips(klines: KLine[]): Bucket[] {
  if (klines.length === 0) return [];
  let minLow = Infinity;
  let maxHigh = -Infinity;
  for (const k of klines) {
    if (k.low < minLow) minLow = k.low;
    if (k.high > maxHigh) maxHigh = k.high;
  }
  if (maxHigh <= minLow) return [];
  const step = (maxHigh - minLow) / LEVELS;
  const buckets = new Array<number>(LEVELS).fill(0);
  for (const k of klines) {
    const start = Math.max(0, Math.floor((k.low - minLow) / step));
    const end = Math.min(LEVELS - 1, Math.floor((k.high - minLow) / step));
    if (end < start) continue;
    const slice = end - start + 1;
    const each = k.volume / slice;
    for (let i = start; i <= end; i++) {
      buckets[i] += each;
    }
  }
  return buckets.map((vol, i) => ({ price: minLow + i * step, volume: vol }));
}

export default function ChipDistribution(props: Props) {
  const { code, category, count = 200, adjust = 'none', currentPrice, symbolName, upToIndex } = props;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [buckets, setBuckets] = useState<Bucket[] | null>(null);
  const [rawKlines, setRawKlines] = useState<KLine[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<{ price: number; volume: number; ratio: number } | null>(null);
  const [redrawTick, setRedrawTick] = useState(0);

  // 拉 K 线、算筹码
  useEffect(() => {
    let aborted = false;
    setError(null);
    (async () => {
      try {
        const url = `${API_BASE}/api/index/kline?code=${encodeURIComponent(code)}&category=${category}&count=${count}&adjust=${adjust}`;
        const resp = await fetch(url);
        const json = await resp.json();
        if (aborted) return;
        if (json.code !== 0) {
          setError(json.error || '筹码数据加载失败');
          setRawKlines([]);
          setBuckets(null);
          return;
        }
        const raw: KLine[] = json.data || [];
        setRawKlines(raw);
        // 初次加载也直接算末根视角（= 全量 [0, last]），避免先闪一下
        // 「只看最后一根」那版图形（获利比例会从 1.5% 闪成 100%）再被下面的 effect 改掉。
        setBuckets(computeChips(raw));
      } catch (e) {
        if (!aborted) setError((e as Error).message);
      }
    })();
    return () => {
      aborted = true;
    };
  }, [code, category, count, adjust]);

  // 主图十字线移动 / 离开时，基于已加载的全量 K 线重新算成本分布：
  //   - upToIndex 有值 → 用 [0, upToIndex] 的历史 K 线（成本分布演化）
  //   - upToIndex 为 null（鼠标已离开图表区）→ 仍然定在最后一根，即等价于 upToIndex = last
  //
  // ⚠️ null **不能**退化成 `rawKlines.slice(-1)`：只用最后一根算筹码时
  // minLow/maxHeight 会缩到单日区间（如 19.40~19.75），80 个桶全挤在 0.35 元里，
  // 现价几乎必然落在区间内 → 获利比例恒 100%，与「悬停末根」的结果完全不同
  // （实测 1.5% vs 100%），视觉上就是筹码散架。
  useEffect(() => {
    if (rawKlines.length === 0) return;
    const end = upToIndex == null ? rawKlines.length : upToIndex + 1;
    setBuckets(computeChips(rawKlines.slice(0, Math.max(0, Math.min(end, rawKlines.length)))));
  }, [upToIndex, rawKlines]);

  // 画布尺寸：单独用一个 effect + ResizeObserver 跟踪 wrap 尺寸。
// 关键：不要在每次 hover 重绘里都 set canvas.style.width，否则 dpr × pixel-size 会被反复
// 写回宽高、和 CSS width: 100% 互相冲突，layout 在 hover 期间会来回抖动导致整列变宽。
// 这里只把 canvas.width/canvas.height（位图像素）按 dpr 调整，display 尺寸完全交给 CSS。
  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapperRef.current;
    if (!canvas || !wrap) return;
    const dpr = window.devicePixelRatio || 1;
    const apply = () => {
      const rect = wrap.getBoundingClientRect();
      const w = Math.max(rect.width, 100);
      const h = Math.max(rect.height, 100);
      // 只改位图像素，display 尺寸交给 CSS（width: 100%）和 flex 控制
      const targetW = Math.floor(w * dpr);
      const targetH = Math.floor(h * dpr);
      if (canvas.width !== targetW) canvas.width = targetW;
      if (canvas.height !== targetH) canvas.height = targetH;
      // 把 CSS 像素尺寸记到 dataset 供绘制使用
      canvas.dataset.cw = String(w);
      canvas.dataset.ch = String(h);
      canvas.dataset.dpr = String(dpr);
      // 触发一次重绘
      setRedrawTick((t) => t + 1);
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(wrap);
    return () => ro.disconnect();
  }, []);

  // 鼠标移动：拾取最近的价格桶
  useEffect(() => {
    const wrap = wrapperRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    const onMove = (e: MouseEvent) => {
      if (!buckets || buckets.length === 0) {
        setHover(null);
        return;
      }
      const rect = canvas.getBoundingClientRect();
      const y = e.clientY - rect.top;
      const topPad = 28;
      const bottomPad = 36;
      const usable = rect.height - topPad - bottomPad;
      if (y < topPad || y > rect.height - bottomPad) {
        setHover(null);
        return;
      }
      const idx = Math.max(0, Math.min(buckets.length - 1, Math.floor(((y - topPad) / usable) * buckets.length)));
      const b = buckets[idx];
      const maxV = Math.max(...buckets.map((d) => d.volume));
      setHover({ price: b.price, volume: b.volume, ratio: maxV > 0 ? b.volume / maxV : 0 });
    };
    const onLeave = () => setHover(null);
    wrap.addEventListener('mousemove', onMove);
    wrap.addEventListener('mouseleave', onLeave);
    return () => {
      wrap.removeEventListener('mousemove', onMove);
      wrap.removeEventListener('mouseleave', onLeave);
    };
  }, [buckets]);

  // 画布渲染：依赖 buckets / currentPrice / error / hover / symbolName / redrawTick；
  // 不再触碰 canvas.width / canvas.style.width / canvas.style.height
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const w = Number(canvas.dataset.cw) || canvas.clientWidth || 100;
    const h = Number(canvas.dataset.ch) || canvas.clientHeight || 100;
    const dpr = Number(canvas.dataset.dpr) || window.devicePixelRatio || 1;
    const c = canvas.getContext('2d')!;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);

    // 背景
    c.fillStyle = '#22272e';
    c.fillRect(0, 0, w, h);

    if (!buckets || buckets.length === 0) {
      c.fillStyle = '#768390';
      c.font = '12px Consolas, Monaco, monospace';
      c.textAlign = 'center';
      c.fillText(error ? `⚠ ${error}` : '加载中…', w / 2, h / 2);
      return;
    }

    const maxV = Math.max(...buckets.map((d) => d.volume));
    if (maxV <= 0) return;

    const padding = { top: 28, bottom: 36, left: 56, right: 16 };
    const plotW = w - padding.left - padding.right;
    const plotH = h - padding.top - padding.bottom;
    const barH = plotH / buckets.length;

    // 网格
    c.strokeStyle = '#373e47';
    c.lineWidth = 1;
    for (let i = 1; i <= 4; i++) {
      const y = padding.top + (plotH * i) / 5;
      c.beginPath();
      c.moveTo(padding.left, y);
      c.lineTo(w - padding.right, y);
      c.stroke();
    }

    // 横条
    for (let i = 0; i < buckets.length; i++) {
      const b = buckets[i];
      const y = padding.top + i * barH;
      const len = (b.volume / maxV) * plotW;
      // 获利盘：cost <= currentPrice（红）；套牢盘：cost > currentPrice（绿）
      c.fillStyle = b.price <= currentPrice ? '#ef5350' : '#26a69a';
      c.fillRect(padding.left, y, len, Math.max(barH - 0.5, 0.5));
    }

    // 当前价横线
    const curIdx = buckets.findIndex((d) => d.price >= currentPrice);
    if (curIdx >= 0) {
      const y = padding.top + curIdx * barH + barH / 2;
      c.strokeStyle = '#FFA500';
      c.lineWidth = 1;
      c.setLineDash([4, 4]);
      c.beginPath();
      c.moveTo(padding.left, y);
      c.lineTo(w - padding.right, y);
      c.stroke();
      c.setLineDash([]);
      c.fillStyle = '#FFA500';
      c.font = '11px Consolas, Monaco, monospace';
      c.textAlign = 'left';
      c.fillText(`现 ${currentPrice.toFixed(2)}`, padding.left + 4, y - 4);
    }

    // 价格刻度
    c.fillStyle = '#768390';
    c.font = '10px Consolas, Monaco, monospace';
    c.textAlign = 'right';
    const tickCount = 6;
    for (let i = 0; i <= tickCount; i++) {
      const idx = Math.min(buckets.length - 1, Math.floor((i / tickCount) * (buckets.length - 1)));
      const y = padding.top + idx * barH + barH / 2 + 3;
      c.fillText(buckets[idx].price.toFixed(0), padding.left - 4, y);
    }

    // 底部 x 轴（百分比）
    c.textAlign = 'left';
    c.fillStyle = '#6e7781';
    for (let i = 0; i <= 4; i++) {
      const x = padding.left + (plotW * i) / 4;
      const pct = (i / 4) * 100;
      c.fillText(`${pct.toFixed(0)}%`, x, h - 18);
    }

    // 不再画 "筹码分布 · 上证指数" 标题 — card head 已经显示了证券名 + 行情，
    // 这里只画分布柱 + 底部百分比刻度，避免冗余白字。
    c.textAlign = 'left';

    // 鼠标 hover 提示线
    if (hover) {
      const idx = buckets.findIndex((b) => Math.abs(b.price - hover.price) < (buckets[1]?.price - buckets[0]?.price || 1));
      if (idx >= 0) {
        const y = padding.top + idx * barH + barH / 2;
        c.strokeStyle = '#adbac7';
        c.setLineDash([2, 2]);
        c.beginPath();
        c.moveTo(padding.left, y);
        c.lineTo(w - padding.right, y);
        c.stroke();
        c.setLineDash([]);
      }
    }
  }, [buckets, currentPrice, error, hover, symbolName, redrawTick]);

  // 汇总数据：90% 集中度、获利比例
  const summary = useMemo(() => {
    if (!buckets || buckets.length === 0) return null;
    const total = buckets.reduce((s, b) => s + b.volume, 0);
    const profit = buckets.filter((b) => b.price <= currentPrice).reduce((s, b) => s + b.volume, 0);
    const ratio = total > 0 ? profit / total : 0;
    return { total, profit, ratio };
  }, [buckets, currentPrice]);

  return (
    <div ref={wrapperRef} className="chip-distribution">
      {summary && (
        <div className="chip-summary">
          <span className="chip-profit">获利 {(summary.ratio * 100).toFixed(1)}%</span>
          <span className="chip-loss">套牢 {((1 - summary.ratio) * 100).toFixed(1)}%</span>
          {hover && (
            <span className="chip-hover">
              {hover.price.toFixed(2)} · 占比 {(hover.ratio * 100).toFixed(1)}%
            </span>
          )}
        </div>
      )}
      <canvas ref={canvasRef} className="chip-canvas" />
    </div>
  );
}