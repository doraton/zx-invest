/** K 线日期的统一展示格式。
 *
 *  两处共用，格式只在这里定义一次，保证永远长得一样：
 *    - 悬浮窗的日期行（components/CrosshairTooltip.tsx）
 *    - X 轴十字线标签（klinecharts 内建，经 chart.setFormatter 接管，
 *      见 pages/MarketChart.tsx）
 *  这两处显示的是同一根 K 线，一旦格式分叉就会出现"浮窗写 08/07、
 *  轴上写 2026-08-07"这种对不上的割裂感。
 *
 *  与 formatCode.ts 同理放在 utils 下：ChartHeader / WatchlistPage /
 *  MarketChart 都要用，不属于任何单个组件。
 */

/** getDay(): 0=周日 … 6=周六。用「周四」两字而不是「星期四」——四字会撑宽浮窗。 */
const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

/**
 * 2026/08/07周五
 *
 * `withWeekday = false` 时只给 2026/08/07——区域统计弹窗的起止日期框用这个：
 * 那里的宽度就够放下日期本身，带星期会被挤。日期部分两处完全一致，
 * 只有星期是可选的。
 */
export function formatBarDate(ts: number, withWeekday = true): string {
  const d = new Date(ts);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  // 斜杠 + 不留空格：比 "YYYY-MM-DD 周三" 少 2 个字符，浮窗窄一点。
  // 星期和日期用同一套 getter，时区口径必然一致（两套混用会差一天）。
  return withWeekday ? `${yyyy}/${mm}/${dd}${WEEKDAYS[d.getDay()]}` : `${yyyy}/${mm}/${dd}`;
}
