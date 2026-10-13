import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Select, Button, Modal, Checkbox, Slider } from 'antd';
import { HeartOutlined, HeartFilled, SettingOutlined } from '@ant-design/icons';
import { isDailyOnly, type IndexInfo } from '../utils/indices';
import {
  PERIOD_OPTIONS,
  ADJUST_OPTIONS,
  type AdjustType,
  type PeriodOption,
} from '../utils/chartOptions';
import {
  TOP_RATIO_RANGE,
  RIGHT_RATIO_RANGE,
  mainPaneHeight,
  type LayoutRatio,
} from '../utils/layout';
import { formatSymbolCode } from '../utils/formatCode';
import {
  addWatch,
  removeWatch,
  loadWatchlist,
  subscribeWatchlist,
  isInUserWatchlist,
  getAllGroups,
  type GroupKey,
  type WatchGroup,
} from '../utils/watchlist';
import './ChartHeader.css';

export interface SymbolInfoProps {
  currentIndex: IndexInfo;
  /** 头部报价：K 线**最新一根**的收盘价。null = 数据还没到（切换标的中 / 首次加载）。
   *
   *  这里刻意不用 `/api/index/quote`：那是通达信实时快照，跟图上最后一根
   *  不是一回事（2026-10-08 实测：快照 18.83，图上 09-30 收盘 19.40，
   *  头部和图表/浮窗三处数字互相打架）。 */
  price: number | null;
  /** 报价溯源说明，挂在报价数字上做 title —— 让"这个价是哪来的"一眼可见。 */
  priceTip: string;
  isUp: boolean;
  changePct: number | null;
  changePoints: number | null;
}

export interface ChartToolsProps {
  currentIndex: IndexInfo;
  period: PeriodOption;
  setPeriod: (p: PeriodOption) => void;
  /** 当前复权方式（不复权 / 前复权 / 后复权）。 */
  adjust: AdjustType;
  setAdjust: (a: AdjustType) => void;
  /** 主图现价线（最后一根收盘价那条横线 + 右侧价格标签）是否显示。 */
  showPriceLine: boolean;
  setShowPriceLine: (v: boolean) => void;
  /** 当前版面比例（两根线的位置） */
  ratio: LayoutRatio;
  /** Slider 拖动过程中：只改本地 state，保证拖动跟手，不打后端。 */
  onRatioPreview: (r: LayoutRatio) => void;
  /** Slider 松手时：写 app_config。 */
  onRatioCommit: (r: LayoutRatio) => void;
  /** K 线区实测尺寸，用来把比例换算成像素显示给用户 */
  areaH: number;
  areaW: number;
}

/**
 * 2026-10-12 版面改造后，头部从「一条 48px 的 header 行」拆成了两半：
 *
 *   - `SymbolInfo` → 搬进右列上部的右半列（筹码区右列），单行横排
 *   - `ChartTools` → 变成浮在主图顶部右侧的 overlay，跟 MA 图例同一行
 *
 * 原来这里是 `.chart-frame-header-left` 里的 1fr 1fr 1fr 三等分，每段 596px
 * 已经挤满；搬进均分后的窄列里只能单行排，所以三个 ch-section 容器也一并撤掉了。
 */

/** 当前 code 是否已在自选（自选不再预置任何标的，用户加过才算）。 */
function checkInWatchlist(code: string): boolean {
  return isInUserWatchlist(code);
}

/** 证券标识 + 报价 + Tag + 自选爱心。原来是 header 的 sec-1，现居右列右半。 */
export function SymbolInfo({
  currentIndex,
  price,
  priceTip,
  isUp,
  changePct,
  changePoints,
}: SymbolInfoProps) {
  // 当前 code 是否已在自选（任何分组都算"已加"）。
  const [inWatchlist, setInWatchlist] = useState(() => checkInWatchlist(currentIndex.code));
  // 当前标的在每个分组里的归属（按 groupId -> boolean 维护）。一只股票可以同时属于多个分组。
  const [membership, setMembership] = useState<Record<string, boolean>>(() => {
    const items = loadWatchlist();
    const me = items.find((x) => x.code === currentIndex.code);
    return me?.groups.reduce<Record<string, boolean>>((acc, g) => {
      acc[g] = true;
      return acc;
    }, {}) ?? {};
  });

  // 切换 code / watchlist 变更时，重新读 localStorage 同步所有归属状态。
  const refreshMembership = useCallback(() => {
    const items = loadWatchlist();
    const me = items.find((x) => x.code === currentIndex.code);
    setInWatchlist(!!me);
    setMembership(
      me?.groups.reduce<Record<string, boolean>>((acc, g) => {
        acc[g] = true;
        return acc;
      }, {}) ?? {},
    );
  }, [currentIndex.code]);

  useEffect(() => {
    refreshMembership();
  }, [refreshMembership]);

  // 订阅 watchlist 变更（add/removeWatch 会 notify），自动同步本地 state。
  useEffect(() => {
    return subscribeWatchlist(refreshMembership);
  }, [refreshMembership]);

  /* ──── 分组选择 dropdown（点击爱心时弹出）────
     自己实现一个轻量 dropdown（不依赖 antd Popover 的 trigger）：
       - 用一个 buttonRef 锚定爱心按钮，picker 用 fixed 定位 + getBoundingClientRect 计算位置。
       - 外部点击 / Esc 关闭 picker。
       - 弹窗里每个分组一行 checkbox（全部是用户自己创建的分组 + 兜底"其他"）
         - 勾 = addWatch，取消 = removeWatch，立即生效
       - 没有"添加"按钮——勾选动作就是提交。 */
  const [pickerOpen, setPickerOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const pickerRef = useRef<HTMLDivElement>(null);
  const [pickerPos, setPickerPos] = useState<{ top: number; left: number } | null>(null);

  /** 打开 picker 时定位（爱心按钮正下方）+ 注册外部点击 / Esc 关闭。 */
  useEffect(() => {
    if (!pickerOpen) return;
    // 计算 picker 位置：紧贴爱心左下，水平对齐爱心左边界
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) {
      setPickerPos({ top: rect.bottom + 4, left: rect.left });
    }
    const onDocClick = (e: MouseEvent) => {
      // 点在 picker 内 / 爱心按钮上都不关闭；其他都关
      const t = e.target as Node | null;
      if (!t) return;
      if (pickerRef.current?.contains(t)) return;
      if (buttonRef.current?.contains(t)) return;
      setPickerOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPickerOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [pickerOpen]);

  /** 爱心点击：无论已加 / 未加，都打开 picker 让用户调整各分组勾选状态。
   *  - 未加态：picker 里全部未勾，用户勾上想要的分组 → 加入
   *  - 已加态：picker 里已显示当前所在的分组，用户可取消某些分组（保留其他）
   *    再次点爱心 = 关 picker（在 document mousedown 监听里处理）。
   *    这样避免"已加态点爱心 = 一键清空所有分组"的误触。 */
  const handleHeartClick = () => {
    setPickerOpen((v) => !v);
  };

  /** 弹窗里展示的分组列表：用户自己创建的分组 + 兜底 'default'（"其他"）。 */
  const groups: WatchGroup[] = useMemo(() => getAllGroups(), [pickerOpen, inWatchlist]);

  /** 勾 / 取消某个分组：勾上 addWatch，取消 removeWatch(code, groupId)，立即生效。
   *  一只股票可以同时存在于多个分组——所以"取消其中一个分组"不会影响它在其他分组里的状态。 */
  const handleToggleGroup = (groupId: GroupKey) => {
    const inThisGroup = !!membership[groupId];
    if (inThisGroup) {
      removeWatch(currentIndex.code, groupId);
    } else {
      addWatch({
        code: currentIndex.code,
        name: currentIndex.name,
        group: groupId,
      });
    }
  };

  return (
    <div className="symbol-info">
      {/* 三行，每行一个内容块（2026-10-13）：
          1 代码+名称 / 2 报价 / 3 自选爱心。
          原来靠 flex-wrap 自然换行，行数随列宽漂移，右列一变宽爱心就挤回报价那行去了。 */}
      <div className="symbol-info-row">
        <span className="symbol-code">{formatSymbolCode(currentIndex.code)}</span>
        <span className="symbol-name">{currentIndex.name}</span>
      </div>
      {price !== null && (
        <span className={`header-quote ${isUp ? 'up' : 'down'}`} title={priceTip}>
          <span className="header-quote-price">{price.toFixed(2)}</span>
          {/* 涨跌额：只显示数值，不带"点"后缀（2026-10-08 要求）。
              2026-10-09 起排到涨跌幅前面（原本是「涨跌幅 → 涨跌额」）。 */}
          <span className="header-quote-points">
            {changePoints !== null
              ? `${changePoints >= 0 ? '+' : ''}${changePoints.toFixed(2)}`
              : '--'}
          </span>
          <span className="header-quote-pct">
            {changePct !== null
              ? `${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%`
              : '--'}
          </span>
        </span>
      )}
      {/* 自选按钮。2026-10-13 起不再显示「个股行情 / 指数行情」Tag——
          列宽只有右列的一半，Tag 挤掉位置不划算，指数/个股从 code 前缀就能看出来。
          用一颗放大的爱心 + 右下角叠加的 +/- 符号区分两种状态：
            - 未加：♡ ＋（灰）嵌在右下角；点击 → 打开分组选择 dropdown
            - 已加：♥ −（红）嵌在右下角；点击 → 直接移除自选
          ± 用 absolute 定位、不画 circle、不换色，跟爱心融成一个整体。
          指数和个股一视同仁，不做特殊处理。

          分组选择 dropdown 锚在爱心按钮正下方，每个分组一行 checkbox：
            - 勾上 = addWatch 到该分组
            - 取消 = removeWatch
          没有"添加"按钮——勾选动作就是提交。 */}
      <Button
        ref={buttonRef}
        size="small"
        type="default"
        onClick={handleHeartClick}
        className={`watchlist-toggle-btn${inWatchlist ? ' is-added' : ''}`}
        aria-label={inWatchlist ? '从自选移除' : '添加到自选'}
        title={inWatchlist ? '从自选移除' : '添加到自选'}
      >
        <span className="watchlist-toggle-content">
          {inWatchlist ? <HeartFilled /> : <HeartOutlined />}
          <span className="watchlist-toggle-marker">
            {inWatchlist ? '−' : '+'}
          </span>
        </span>
      </Button>
      {pickerOpen && pickerPos && (
        <div
          ref={pickerRef}
          className="watchlist-picker"
          style={{ position: 'fixed', top: pickerPos.top, left: pickerPos.left }}
        >
          <div className="watchlist-picker-title">分组（可多选）</div>
          <div className="watchlist-picker-list">
            {groups.map((g) => {
              const checked = !!membership[g.id];
              // 每个 checkbox 独立勾选：一只股票可以同时属于多个分组。
              return (
                <label
                  key={g.id}
                  className={`watchlist-picker-row${checked ? ' checked' : ''}`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => handleToggleGroup(g.id)}
                    className="watchlist-picker-row-input"
                  />
                  <span className="watchlist-picker-row-mark" />
                  <span className="watchlist-picker-row-label">{g.label}</span>
                </label>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * 周期 / 复权 / 设置。2026-10-12 起浮在主图顶部右侧，跟 klinecharts 画在
 * canvas 上的 "MA20: xx" 图例同一行。
 *
 * 必须是浮层：图例是 canvas 画的，插不进 DOM。代价是要让开右侧价格 y 轴 ——
 * `--chart-y-axis-w` 由 MarketChart 实测主图 canvas 右边缘后写在 inline style 上。
 *
 * ⚠️ `.kline-container` 上挂着「←/→ 切 K 线」的 onKeyDown，而 Select 就在容器内，
 * 下拉打开后按 ←/→ 会冒泡上去把十字线一起挪走 —— 所以这里整个容器
 * onKeyDown 一次性 stopPropagation，而不是逐个控件处理（漏一个就复现）。
 *
 * 反过来，设置 Modal 走 portal 挂在 body 上，**不在 .kline-container 内**，
 * 所以弹窗里 Slider 的方向键不会触发切 K 线，不用额外处理。
 */
export function ChartTools({
  currentIndex,
  period,
  setPeriod,
  adjust,
  setAdjust,
  showPriceLine,
  setShowPriceLine,
  ratio,
  onRatioPreview,
  onRatioCommit,
  areaH,
  areaW,
}: ChartToolsProps) {
  // 设置弹窗开关。state 留在本组件：Modal 挂在这里，位置在 overlay 内部，
  // 走 portal 后实际渲染到 body，不受 overlay 定位上下文影响。
  const [settingsOpen, setSettingsOpen] = useState(false);

  // 0AMV 只有日线（独立表 kline_0amv 无 period 列、不参与物化），
  // 其余周期会让后端 query_klines 抛错，这里直接把选项置灰。
  const dailyOnly = isDailyOnly(currentIndex.code);

  // 比例 → 像素，只为在 Slider 右侧显示「实际生效值」。
  // 真值由 klinecharts 按自己的余数分配算出，这里用的是同一套公式
  // （utils/layout.ts 的 mainPaneHeight，和 setPaneOptions 喂进去的是同一条路径）。
  const mainPx = mainPaneHeight(areaH, ratio.topRatio);
  const rightPx = Math.round(areaW * ratio.rightRatio);
  const pct = (v: number) => `${Math.round(v * 1000) / 10}%`;

  return (
    <div className="chart-tools" onKeyDown={(e) => e.stopPropagation()}>
      <Select
        size="small"
        value={period.label}
        onChange={(v) => setPeriod(PERIOD_OPTIONS.find((o) => o.label === v)!)}
        options={PERIOD_OPTIONS.map((o) => ({
          label: o.label,
          value: o.label,
          disabled: dailyOnly && o.period.type !== 'day',
        }))}
        style={{ width: 80 }}
      />
      <Select
        size="small"
        value={adjust}
        onChange={(v) => setAdjust(v as AdjustType)}
        options={ADJUST_OPTIONS.map((o) => ({ label: o.label, value: o.value }))}
        style={{ width: 80 }}
      />
      <Button
        size="small"
        type="text"
        className="ch-settings-btn"
        icon={<SettingOutlined />}
        title="设置"
        aria-label="设置"
        onClick={() => setSettingsOpen(true)}
      />
      {/* 设置弹窗。antd v6 的 Modal 默认就是页面居中的遮罩弹窗，不用再传 centered。 */}
      <Modal
        open={settingsOpen}
        onCancel={() => setSettingsOpen(false)}
        footer={null}
        title="设置"
        width={420}
      >
        <Checkbox
          checked={showPriceLine}
          onChange={(e) => setShowPriceLine(e.target.checked)}
        >
          现价线
        </Checkbox>

        <div className="settings-divider" />

        <div className="settings-group-title">版面</div>
        <div className="settings-row">
          <span className="settings-label">横线距上</span>
          <Slider
            className="settings-slider"
            min={TOP_RATIO_RANGE[0]}
            max={TOP_RATIO_RANGE[1]}
            step={0.005}
            value={ratio.topRatio}
            // onChange 是拖动过程中每帧都触发 —— 这里只改本地 state，让版面跟手；
            // 写库放到 onChangeComplete（松手）里，否则一次拖动会打出几十个 PUT。
            onChange={(v) => onRatioPreview({ ...ratio, topRatio: v as number })}
            onChangeComplete={(v) => onRatioCommit({ ...ratio, topRatio: v as number })}
            tooltip={{ formatter: (v) => pct(v ?? 0) }}
          />
          <span className="settings-value">
            {pct(ratio.topRatio)} · {mainPx}px
          </span>
        </div>
        <div className="settings-row">
          <span className="settings-label">竖线距右</span>
          <Slider
            className="settings-slider"
            min={RIGHT_RATIO_RANGE[0]}
            max={RIGHT_RATIO_RANGE[1]}
            step={0.005}
            value={ratio.rightRatio}
            onChange={(v) => onRatioPreview({ ...ratio, rightRatio: v as number })}
            onChangeComplete={(v) => onRatioCommit({ ...ratio, rightRatio: v as number })}
            tooltip={{ formatter: (v) => pct(v ?? 0) }}
          />
          <span className="settings-value">
            {pct(ratio.rightRatio)} · {rightPx}px
          </span>
        </div>
        <div className="settings-hint">
          横线 = 主图与副图的分界，竖线 = K 线与右列的分界。
          左下四个副图平分高度，右上两列平分宽度。
        </div>
      </Modal>
    </div>
  );
}