export interface IndexInfo {
  code: string;   // 完整代码：SH.600570 / SH.000001 / 0AMV
                  // 有交易所前缀的一律走 SH.xxxxxx 大写带点（= stock_info /
                  // index_info 的落库形态，也是 tdx_kline.canonical_code 的产物）；
                  // 0AMV 这类自研标的没有前缀，不参与这个约定。
  name: string;   // 全名：上证指数
  short: string;  // 简称：上证
  /**
   * 是否只支持日线。0AMV 走独立表 kline_0amv，该表无 period 列、
   * 也不参与物化，后端对非 day 周期会直接抛错（见 market.query_klines）。
   * 标了这个的标的，周期选择器要禁用周/月/季/年。
   */
  dailyOnly?: boolean;
}

/**
 * 可切换标的。**数组第一项是默认选中项**（MarketChart / Watchlist 都用它兜底）。
 *
 * 恒生电子排在第一位是 MVP 演示需要：它的数据最完整——
 * 日线 2010-01-04~2026-09-30 已入库（MySQL kline 表），
 * 周/月/季/年由日线物化，基本面（2026-08-26）与除权除息（46 条）同步落库。
 *
 * `0AMV` 活跃市值来自指南针本地文件（day.vdat），**独立表 kline_0amv**，
 * 8219 根日线 1993-01-03~2026-10-08。注意它的 code 首字符是**数字 0**，
 * 且**只有日线**——请求 week/month 会被后端拒绝（该表无 period 列、不参与物化）。
 */
export const INDICES: IndexInfo[] = [
  { code: 'SH.600570', name: '恒生电子', short: '恒生' },
  { code: '0AMV', name: '活跃市值指数', short: '活跃市值', dailyOnly: true },
  { code: 'SH.000001', name: '上证指数', short: '上证' },
  { code: 'SZ.399001', name: '深证成指', short: '深证' },
  { code: 'SZ.399006', name: '创业板指', short: '创业板' },
  { code: 'SH.000688', name: '科创50',   short: '科创50' },
];

/**
 * 把任意写法的 code 归一成全库唯一形态 `SH.600570`（大写带点）。
 *
 * 和后端 `server/external/tdx_kline.canonical_code` 同构，两边必须保持一致。
 *
 * 为什么前端也需要一份：localStorage 里存着用户早期加的自选，那时 code 还是
 * `sh600570` / `sh000001`。2026-10-09 收口后 INDICES 全是大写带点，
 * `i.code === code` 精确匹配就会失配——**表现是自选里的票名字变成一串代码**
 * （`上证指数` 显示成 `sh000001`）。所以凡是拿 code 去 INDICES 里查的地方，
 * 都要先过这个函数。
 *
 * 认不出来的（`0AMV` / `mom`）**原样返回**：它们没有交易所前缀，
 * 大小写就是身份的一部分，不能顺手大写。
 */
export function canonicalCode(code: string): string {
  const c = code.trim();
  const m = /^(SH|SZ)\.?(\d{6})$/i.exec(c);
  return m ? `${m[1].toUpperCase()}.${m[2]}` : c;
}

export function findIndex(code: string): IndexInfo | undefined {
  const want = canonicalCode(code);
  return INDICES.find((i) => canonicalCode(i.code) === want);
}

/** 该标的是否只能看日线（true 时周期选择器要禁用其余周期）。 */
export function isDailyOnly(code: string): boolean {
  return findIndex(code)?.dailyOnly === true;
}