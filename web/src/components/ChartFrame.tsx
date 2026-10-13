import type { CSSProperties, ReactNode } from 'react';
import './ChartFrame.css';

interface Props {
  /** 右列宽度（kline / chip 分界），默认 280px */
  rightWidth?: number | string;
  /** body 区域是否显示 loading 覆盖层 */
  loading?: boolean;
  loadingTip?: string;
  /** 错误提示文本 */
  error?: string | null;
  className?: string;
  children: ReactNode;
}

/**
 * 自写的图表外壳：body 单段布局（原来还有一条 48px 的 header 行，
 * 2026-10-12 版面改造时取消——头部内容搬进了右列，腾出的高度还给 K 线区）。
 *
 * - `--chart-right-w` 由 rightWidth 走 inline style 注入，注意它会覆盖
 *   ChartFrame.css 里的同名声明，所以改宽度要改这个 prop，不是改 CSS。
 * - 不依赖 antd 内部 className
 */
export default function ChartFrame({
  rightWidth = 280,
  loading = false,
  loadingTip = '加载中…',
  error = null,
  className,
  children,
}: Props) {
  const style = {
    ['--chart-right-w' as never]: typeof rightWidth === 'number' ? `${rightWidth}px` : rightWidth,
  } as CSSProperties;

  const cls = ['chart-frame', className].filter(Boolean).join(' ');

  return (
    <div className={cls} style={style}>
      <div className="chart-frame-body">
        {loading && <div className="chart-frame-loading">{loadingTip}</div>}
        {children}
      </div>
      {error && <div className="chart-frame-error">⚠ {error}</div>}
    </div>
  );
}
