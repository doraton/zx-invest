/**
 * MarketChart 用到的所有"图表配置"常量与类型：
 * - 周期（日线/周线/月线/季线/年线）
 * - 复权（不复权/前复权/后复权）
 * - K 线根数（DEFAULT_BAR_COUNT，固定值，用户不再切换）
 * - 右栏模式（筹码/基本面）
 *
 * 拆出来是为了 ChartHeader（components/）和 MarketChart（pages/）
 * 共享同一份定义，避免循环 import。
 */

/**
 * klinecharts v10 的 PeriodType 只有 second/minute/hour/day/week/month/year，
 * **没有 quarter**。季线的标准做法是 month × span=3（klinecharts 自带周期条
 * 的"季K"也是这么定义的），所以这里 type 里也没有 quarter。
 */
export type PeriodType = 'day' | 'week' | 'month' | 'year';

export interface PeriodOption {
  label: string;
  /** 传给 klinecharts chart.setPeriod()，决定 X 轴标签与指标周期语义 */
  period: { type: PeriodType; span: number };
  /** tdx category: 4=日, 5=周, 6=月, 7=季, 8=年 */
  category: number;
}

/**
 * 复权方式。
 * none = 不复权（库里原值）
 * qfq  = 前复权（末根不动，历史往前压）
 * hfq  = 后复权（上市首日不动，历史往后抬）
 *
 * 2026-10-12 起后端 `/api/index/kline` 真的支持了（query 参数名是 `adj`，
 * 见 server/src/server/market.py 的 `ADJ_MODES` / `Adjustment`）。
 * 因子不在库里落列，查询时从 `kline_day.pre_close` 现算。
 *
 * 默认值仍是 none：界面上显示的复权方式 = 数据实际用的复权方式。
 * 0AMV 这类自研指数没有 pre_close，三种模式对它都是同一份数据（市值加总口径
 * 本来就不存在除权），下拉框不置灰但切了不会有任何变化。
 */
export type AdjustType = 'none' | 'qfq' | 'hfq';

export interface AdjustOption {
  label: string;
  value: AdjustType;
}

export const ADJUST_OPTIONS: AdjustOption[] = [
  { label: '不复权', value: 'none' },
  { label: '前复权', value: 'qfq' },
  { label: '后复权', value: 'hfq' },
];

// 原 RightPanelMode（'chip' | 'fundamentals'）已于 2026-10-12 删除：
// 筹码/基本面 的切换 Radio 随 header 行一起撤掉，右列只剩筹码一种模式。

export const PERIOD_OPTIONS: PeriodOption[] = [
  { label: '日线', period: { type: 'day', span: 1 }, category: 4 },
  { label: '周线', period: { type: 'week', span: 1 }, category: 5 },
  { label: '月线', period: { type: 'month', span: 1 }, category: 6 },
  { label: '季线', period: { type: 'month', span: 3 }, category: 7 },
  { label: '年线', period: { type: 'year', span: 1 }, category: 8 },
];

/**
 * K 线根数（主图与筹码面板共用）。
 * 头部的"范围"下拉框已按需求移除，改为固定值：日线取近 200 根，
 * 周/月/季/年线请求同样的上限——库里没那么多数据时后端自然返回全部。
 */
export const DEFAULT_BAR_COUNT = 200;

/** 上证/深证指数代码规则（A 股大盘指数统一编码），用于切换 "指数/个股" Tag。
 *
 *  点可有可无：`sh000001`（旧形态）和 `SH.000001`（2026-10-09 起的全库唯一
 *  形态）都要认，否则存量自选里的老 code 会被误标成「个股行情」。 */
export const INDEX_CODE_RE = /^(sh|sz)\.?(000|399)\d{3}$/i;
