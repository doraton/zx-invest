import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Input, Modal, Radio, Empty } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import MarketChart from './MarketChart';
import { canonicalCode, findIndex, type IndexInfo } from '../utils/indices';
import {
  loadWatchlist,
  getAllGroups,
  createUserGroup,
  subscribeWatchlist,
  type WatchItem,
  type WatchGroup,
} from '../utils/watchlist';
import { searchStocks } from '../utils/stockSearch';
import { formatSymbolCode } from '../utils/formatCode';
import './WatchlistPage.css';
import { API_BASE } from '../config/api';

/** 后端 /api/index/quote 返回的单条报价。prev_close 用于算当天涨跌幅。 */
interface QuoteSnapshot {
  open: number;
  close: number;
  high: number;
  low: number;
  prev_close: number;
  change: number;
  change_pct: number;
  source?: 'tdx-live' | 'db';
  server_time?: string;
  datetime?: string;
}

/** 把任意来源的"条目"归一成 IndexInfo（chart 组件依赖此类型）。 */
function asIndex(item: { code: string; name: string }): IndexInfo {
  return { code: item.code, name: item.name, short: item.name };
}

/**
 * 兜底构造：标的不在自选里时（例如通过 /watchlist?code=xxx 直接跳进来），
 * 用 INDICES 常量补齐元信息。
 *
 * 必须走 findIndex 而不是手搓字面量：0AMV 标了 dailyOnly，丢了它周期下拉
 * 就不会禁用周/月/季/年，用户一点就打到后端那个"自研指数只支持日线"的 500。
 * name/short 优先取自选缓存（用户可能改过显示名），取不到才回落到名录。
 */
function fallbackIndex(code: string, name?: string | null): IndexInfo {
  const preset = findIndex(code);
  return {
    code,
    name: name ?? preset?.name ?? code,
    short: name ?? preset?.short ?? code,
    dailyOnly: preset?.dailyOnly,
  };
}

/** 纵向两行：上面最新价（2 位小数），下面涨跌方向符 + 涨跌幅。 */
function QuoteBadge({ quote }: { quote: QuoteSnapshot | null }) {
  if (!quote || !quote.prev_close) {
    return (
      <div className="watchlist-item-quote">
        <span className="quote-loading">—</span>
      </div>
    );
  }
  // 涨跌**不再前端算**（2026-10-10）：/api/index/quotes 已经把 change /
  // change_pct 算好了——库内路径直取 kline_day.chg_amt / chg_pct（已除权口径），
  // 实时路径由后端按 last_close 现算。前端只负责配色和格式化。
  const change = quote.change;
  const pct = quote.change_pct;
  const cls = change > 0 ? 'up' : change < 0 ? 'down' : '';
  const sign = change >= 0 ? '+' : '-';
  return (
    <div className={`watchlist-item-quote ${cls}`}>
      <div className="quote-price">{quote.close.toFixed(2)}</div>
      <div className="quote-change">
        <span className="quote-arrow">{sign}</span>
        <span>{Math.abs(pct).toFixed(2)}%</span>
      </div>
    </div>
  );
}

export default function WatchlistPage() {
  // 当前所有自选（全部来自用户自己添加，代码里不再预置任何标的）。
  const [items, setItems] = useState<WatchItem[]>(() => loadWatchlist());
  // 订阅 watchlist 变更（ChartHeader 勾 / 取消时会触发），自动同步 items。
  useEffect(() => {
    return subscribeWatchlist(() => {
      setItems(loadWatchlist());
      setGroups(getAllGroups());
    });
  }, []);
  // 当前在右侧行情页显示的 code。和左侧列表项选中状态共用，但解耦：
  //   - watchlist 变更（包括用户在 ChartHeader 取消当前显示股票的自选）时，
  //     selectedCode 不被重置 —— 这样右侧行情页始终保留当前那一只股票的 K 线，
  //     即使用户把它从自选里删了也不会跳走 / 消失。
  //   - selectedName 是配套缓存：当 selectedCode 对应的条目已经从 watchlist 移除时，
  //     仍能用缓存的 name 构造 IndexInfo 继续渲染 MarketChart。
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  // 当前选中的 tab 分组 id（用户自己创建的分组，兜底 'default' = "其他"）。
  const [group, setGroup] = useState<string>('default');
  // 所有可见分组（用户创建）。初始从 utils 同步加载，分组变动时再 refresh。
  const [groups, setGroups] = useState<WatchGroup[]>(() => getAllGroups());
  // "新建分组"模态框开关。
  const [newGroupOpen, setNewGroupOpen] = useState(false);
  const [newGroupLabel, setNewGroupLabel] = useState('');
  // 实时快照：code -> QuoteSnapshot；null 表示加载中/失败。
  const [quotes, setQuotes] = useState<Record<string, QuoteSnapshot | null>>({});

  // 自选条目的 name **以数据库为准**（code -> 库里查到的名称）。
  //
  // 自选表 2026-10-14 起落库时**根本不存 name**（utils/watchlist.ts 的 WatchItem
  // 里那份只是内存里的尽力而为副本），所以这里必须补一次权威名称。
  // 历史教训：曾经 name 是加自选那一刻冻进 localStorage 的，而 0AMV 早期加进去
  // 时后端还没返回名称，addWatch 存进去的就是 code 本身，列表渲染成
  // `it.name` / `formatSymbolCode(it.code)` 两行，于是上下两行都是 `0AMV`。
  //
  // /api/stock/list 一条 SQL 同时吐出 stock_info 和 index_info 两张名录的
  // name，所以一次请求就够覆盖全部自选，不必逐个 searchStocks。
  const [dbNames, setDbNames] = useState<Record<string, string>>({});

  useEffect(() => {
    let alive = true;
    fetch(`${API_BASE}/api/stock/list`)
      .then((r) => r.json())
      .then((j) => {
        if (!alive || j?.code !== 0 || !Array.isArray(j.data)) return;
        const map: Record<string, string> = {};
        for (const row of j.data) {
          if (row?.code && row?.name) map[canonicalCode(row.code)] = row.name;
        }
        setDbNames(map);
      })
      .catch(() => {
        /* 查不到就沿用内存里那份，不阻塞渲染 */
      });
    return () => {
      alive = false;
    };
  }, []);

  // 名称回填：拿到库里的权威 name 后覆盖本地那份。
  //
  // 改在 items 上而不是只在渲染处兜一层，是因为 name 不止列表在用：
  // selectedIndex -> asIndex() 也吃 items[].name，右侧图表头部读的就是它。
  // 只改渲染处会出现"列表叫活跃市值指数、图头还叫 0AMV"的新分裂。
  //
  // 用函数式更新 + 无变化返回 prev：这个 effect 依赖 dbNames 而 setItems 会
  // 触发重渲染，若直接返回新数组就会自激成死循环。
  useEffect(() => {
    if (Object.keys(dbNames).length === 0) return;
    setItems((prev) => {
      let changed = false;
      const next = prev.map((x) => {
        const hit = dbNames[x.code];
        if (hit && hit !== x.name) {
          changed = true;
          return { ...x, name: hit };
        }
        return x;
      });
      return changed ? next : prev;
    });
  }, [dbNames]);

  // ---- 从行情页 / 键盘精灵跳进来时带的 ?code= ----
  // URL 只带 code 不带 name：中文名 encode 后又长又丑，而且刷新页面时
  // 还能靠 code 重新查到名称，比把名字冻在 URL 里更可靠。
  const [searchParams, setSearchParams] = useSearchParams();
  const codeParam = searchParams.get('code');

  useEffect(() => {
    if (!codeParam) return;
    setSelectedCode(codeParam);
    const inItems = items.find((x) => x.code === codeParam);
    if (inItems) {
      setSelectedName(inItems.name);
      return;
    }
    // 不在自选里 —— 先清掉名字占位，再查一次后端拿权威名称。
    setSelectedName(null);
    let alive = true;
    searchStocks(codeParam, 1)
      .then((r) => {
        if (alive && r[0]?.name) setSelectedName(r[0].name);
      })
      .catch(() => {
        /* 查不到就留 null，渲染时回落显示代码 */
      });
    return () => {
      alive = false;
    };
  }, [codeParam, items]);
  // items 在依赖里是有意的：跳转来的票后来被加进自选时，这里会把
  // selectedName 从「后端查来的」同步成本地 items 里的那份（可能带分组）。

  // 默认选中第一条自选（用户自己添加的第一只）。
  // 注意：watchlist 变化时**不重置** selectedCode —— 用户从 ChartHeader 取消当前显示
  // 股票的自选后，右侧行情页继续显示该股票（selectedName cache 兜底构造 IndexInfo）。
  //
  // `!codeParam` 这道判断是**必须的**，不是保险起见：
  // 从行情页 / 键盘精灵跳进来时 URL 带 ?code=xxx，而本 effect 和上面那个
  // 「读 URL 参数」的 effect 是在同一批里跑的——两个 effect 读到的都是
  // **同一次 render 的 state**，此时 selectedCode 还是 null。所以不加这道
  // 判断，本 effect 会误判成「用户还没选」，把刚设进去的 URL 标的覆盖成
  // 自选列表的第一条。结果：自选非空时，跳转过来的票打不开。
  // （自选为空时 items.length 也是 0，本 effect 不执行，所以这个 bug 只在
  //   「已经有自选」时才出现——空库测试根本碰不到。）
  useEffect(() => {
    if (!selectedCode && !codeParam && items.length > 0) {
      setSelectedCode(items[0].code);
      setSelectedName(items[0].name);
    }
  }, [items, selectedCode, codeParam]);

  // 当前选中的"标的"。
  //  - 优先从 items 里找（拿到最新 name、保持类型一致）
  //  - 找不到时（例如用户刚取消收藏），用 selectedName cache 构造一个 IndexInfo，
  //    右侧 MarketChart 继续渲染，行情页面不会消失 / 跳走。
  const selectedIndex: IndexInfo | null = useMemo(() => {
    if (!selectedCode) return null;
    const fromItems = items.find((x) => x.code === selectedCode);
    if (fromItems) return asIndex(fromItems);
    return fallbackIndex(selectedCode, selectedName);
  }, [items, selectedCode, selectedName]);

  // 按分组过滤展示：只显示 groups 包含当前分组 id 的条目。
  // 一只股票可以同时属于多个分组，所以同一只股票可能在多个 tab 下出现。
  const filteredItems = useMemo(() => {
    return items.filter((x) => x.groups.includes(group));
  }, [items, group]);

  // 拉取全部自选的报价；30 秒刷新一次。走 /api/index/quotes 批量接口
  // （MVP 阶段后端就是逐个并发取；等标的多了会换成一次连接取回全部）。
  // 依赖 [items]：用户新增 / 删除股票时即时重拉，新股票会在下一轮
  // （或首次进页后 ≤30s）就显示价格 + 涨跌幅。
  useEffect(() => {
    let alive = true;

    /** 打一轮批量报价。`live` 决定「优先实时快照」还是「强制只读库」。 */
    const fetchQuotes = async (
      codes: string[],
      live: boolean,
    ): Promise<Record<string, QuoteSnapshot>> => {
      const q = new URLSearchParams({ codes: codes.join(','), live: String(live) });
      const r = await fetch(`${API_BASE}/api/index/quotes?${q.toString()}`);
      const j = await r.json();
      if (j?.code !== 0 || !j.data) return {};
      return j.data as Record<string, QuoteSnapshot>;
    };

    const fetchAll = async () => {
      const codes = items.map((x) => x.code);
      if (codes.length === 0) {
        setQuotes({});
        return;
      }
      try {
        // 列表的数取**库内最新一根 K 线**，跟右侧图表头部的口径一致（2026-10-08 改）。
        // 之前走实时快照：收盘后它停在当日盘中价（实测 18.83 / -2.94%），
        // 而图上最后一根是 09-30 收 19.40，同一个页面上两个数互相打架。
        const fromDb = await fetchQuotes(codes, false);
        // 指数在库里**没有** K 线（index_info 只有名录列），读库这一轮会被直接
        // 丢掉（后端对拿不到的 code 是省略而不是返回 null）。所以再补一轮实时，
        // 否则列表里的指数行会齐刷刷变成「—」，等于把指数仅剩的数据也弄没了。
        const missing = codes.filter((c) => !(c in fromDb));
        const fromLive = missing.length ? await fetchQuotes(missing, true) : {};
        if (!alive) return;
        // 两条路都取不到的 code 显式置 null，列表里显示 "—" 而不是沿用上一轮的旧价。
        const next: Record<string, QuoteSnapshot | null> = {};
        for (const code of codes) {
          next[code] = fromDb[code] ?? fromLive[code] ?? null;
        }
        setQuotes(next);
      } catch {
        if (alive) setQuotes({});
      }
    };
    fetchAll();
    const timer = window.setInterval(fetchAll, 30_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [items]);

  /** 提交新建分组：trim 后非空才调 createUserGroup，刷新本地缓存与当前选中 tab。
   *
   *  createUserGroup 自 2026-10-14 起是**异步**的：分组 id 改由后端生成
   *  （前端造 id 有碰撞风险，且只有后端能看见全部分组，同名去重也得在那边做）。
   *  await 之前不能 setGroup——那时还没有 g.id。 */
  const handleCreateGroup = async () => {
    const label = newGroupLabel.trim();
    if (!label) return;
    let g: WatchGroup;
    try {
      g = await createUserGroup(label);
    } catch (e) {
      window.alert((e as Error).message || '新建分组失败');
      return;
    }
    setGroups(getAllGroups());
    setNewGroupOpen(false);
    setNewGroupLabel('');
    setGroup(g.id);
  };

  return (
    <div className="watchlist-page">
      <div className="watchlist-body">
        <aside className="watchlist-side">
          {/* 工具栏只剩分组切换。原来这里还有个「加股票」按钮打开
              AddStockModal 搜索全市场，2026-10-08 删掉了——加股票的入口
              统一收到侧栏的「搜索股票」按钮（键盘精灵）上了，两个入口
              做同一件事，留在这一侧只是冗余。 */}
          <div className="watchlist-toolbar">
            <Radio.Group
              value={group}
              onChange={(e) => setGroup(e.target.value as string)}
              optionType="button"
              buttonStyle="solid"
              size="small"
            >
              {groups.map((g) => (
                <Radio.Button key={g.id} value={g.id}>{g.label}</Radio.Button>
              ))}
              {/* "+ 新建分组"：点击打开新建分组 Modal，真正可用。 */}
              <Radio.Button
                value="__add"
                onClick={(e) => {
                  e.preventDefault();
                  setNewGroupLabel('');
                  setNewGroupOpen(true);
                }}
              >
                <PlusOutlined />
              </Radio.Button>
            </Radio.Group>
          </div>

          <div className="watchlist-list">
            {/* 空自选时整个列表区留空，不再画 Empty 图标 + 引导按钮。
                2026-10-08 删除：那两处引导和工具栏「加股票」按钮、侧栏
                「搜索股票」按钮指向的是同一件事，三份入口里留一份就够。 */}
            {filteredItems.map((it) => (
              <div
                key={it.code}
                className={`watchlist-item${selectedCode === it.code ? ' active' : ''}`}
                onClick={() => {
                  setSelectedCode(it.code);
                  setSelectedName(it.name);
                  // 手动切换时清掉 ?code=，让 URL 只保留「从外部跳进来」
                  // 这件事的痕迹。否则刷新页面会被旧参数拉回上一只票。
                  setSearchParams({});
                }}
              >
                <div className="watchlist-item-main">
                  <div className="watchlist-item-name">{it.name}</div>
                  <div className="watchlist-item-code">{formatSymbolCode(it.code)}</div>
                </div>
                {/* 右侧统一显示 QuoteBadge（指数和个股共用同一套样式，没有删除按钮） */}
                <QuoteBadge quote={quotes[it.code] ?? null} />
              </div>
            ))}
          </div>
        </aside>

        <section className="watchlist-main">
          {selectedIndex ? (
            <MarketChart currentIndex={selectedIndex} hideSwitcher />
          ) : (
            <div className="watchlist-placeholder">
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  <span style={{ color: '#768390' }}>选择左侧指数 / 股票查看 K 线</span>
                }
              />
            </div>
          )}
        </section>
      </div>

      {/* 新建分组 Modal：从工具栏 "+" 按钮触发。回车 / 点确认提交。 */}
      <Modal
        title="新建分组"
        open={newGroupOpen}
        onCancel={() => setNewGroupOpen(false)}
        onOk={handleCreateGroup}
        okText="创建"
        cancelText="取消"
        okButtonProps={{ disabled: !newGroupLabel.trim() }}
        destroyOnHidden
        getContainer={() => document.body}
      >
        <Input
          autoFocus
          placeholder="分组名（如：中线持仓 / 短线观察）"
          value={newGroupLabel}
          onChange={(e) => setNewGroupLabel(e.target.value)}
          onPressEnter={handleCreateGroup}
          maxLength={16}
          allowClear
        />
        <div style={{ color: '#768390', fontSize: 12, marginTop: 8 }}>
          同名会自动去重为「name (2)」。
        </div>
      </Modal>
    </div>
  );
}
