/**
 * 把后端的 "sh000001" / "sz399001" / "SH.600570" / "sh600570" 这种代码，
 * 渲染成 "SH.000001" / "SZ.399001" / "SH.600570" 这种**带分隔符的大写**形态——
 * 这是国内行情软件常见的写法，前缀和市场一目了然。
 *
 * 2026-10-09 起 stock_info 的落库形态已经是 `SH.600570`（大写带点），
 * 所以这个函数对**个股**退化成原样返回；它仍然要认旧的 `sh600570`
 * （通达信 .tnf、`index_info` 的指数码），以及没前缀的裸代码。
 *
 * 兜底规则：
 *  - 完全匹配 `^(SH|SZ)\d{6}$` 时插一个 `.` 分隔；
 *  - 匹配 `^(SH|SZ)\.\d{6}$` 时原样返回（已是目标形态）；
 *  - 其余情况只 `.toUpperCase()` 后原样返回，
 *    不强行加 `.`，避免误把没前缀的代码弄成 "600570" → "60.0570"。
 */
export function formatSymbolCode(code: string): string {
  const upper = code.toUpperCase();
  const m = /^(SH|SZ)(\d{6})$/.exec(upper);
  if (m) return `${m[1]}.${m[2]}`;
  return upper;
}