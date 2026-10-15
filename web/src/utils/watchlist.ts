import { canonicalCode, findIndex, INDICES } from './indices';
import { API_BASE } from '../config/api';

/** 自选条目所属的分组。分组全部由用户创建，没有内置分组。
 *  `default`（"其他"）是唯一的兜底 ID：未归类 / 旧数据的自选都落在这里。
 *  前后端两边都必须认这个 id——后端常量在 `server/src/server/watchlist.py`。 */
export type GroupKey = string;

/** 自选条目。
 *
 *  ⚠️ `name` **不落库**。它只是内存里的一份尽力而为的副本：加自选那一刻
 *  由调用方给，刷新后从 INDICES 猜，猜不到就是 code。权威名称统一走
 *  `/api/stock/list`（stock_info ∪ index_info 一条 SQL 全给），由
 *  `WatchlistPage` 的 dbNames 回填补齐——见那里记录的历史事故。
 *
 *  旧版本的两个字段已删：`addedAt`（全项目无任何 UI 读取）和 `note`
 *  （定义了但从来没写过）。库里没有它们的数据源，留着只会骗人。
 */
export interface WatchItem {
  code: string;   // SH.600570 / 0AMV，大写带市场前缀
  name: string;   // 尽力而为的显示名，可能就是 code
  /** 这只股票所属的分组列表。一只股票可以同时存在于多个分组里。 */
  groups: GroupKey[];
}

/** 自选分组定义。分组完全由用户创建，代码里不预置任何分组。 */
export interface WatchGroup {
  id: string;
  label: string;
}

/* ──────────── 内存 store ────────────
 *
 * 数据源是后端 `watch_group` + `watch_item`（2026-10-14 起不再用 localStorage）。
 * 落库理由和 `utils/layout.ts` 的版面比例一样：**两处真相迟早漂移**。
 *
 * 所有导出保持**同步**签名不变，所以 WatchlistPage / ChartHeader 不用改读取方式
 * ——它们照旧 `useState(() => loadWatchlist())` 照旧 `subscribeWatchlist`。
 * 代价是首屏那一帧自选列表是空的，`initWatchlist()` 拉回来后 notify 会自动补上。
 *
 * 写入一律「乐观更新 + 失败回滚」：加自选是高频操作，等后端往返会让爱心按钮
 * 有明显延迟。回滚会让 UI 弹回原状，用户看得见失败，不至于以为加成功了。
 */

let items: WatchItem[] = [];
let groups: WatchGroup[] = [];

/** 后端保证 default 恒存在且恒排最后；这里是同一条规则的第二道保险，
 *  免得哪天后端漏了种子行，前端就渲染不出「其他」这个 tab。 */
function ensureDefault(list: WatchGroup[]): WatchGroup[] {
  if (list.some((g) => g.id === 'default')) return list;
  return [...list, { id: 'default', label: '其他' }];
}

/** 接口返回的裸条目 → WatchItem。name 猜不到就是 code。 */
function toItems(raw: unknown): WatchItem[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((r): r is { code: string; groups?: unknown } =>
      !!r && typeof r === 'object' && typeof (r as { code?: unknown }).code === 'string')
    .map((r) => ({
      code: r.code,
      name: findIndex(r.code)?.name ?? r.code,
      groups: Array.isArray(r.groups) ? r.groups.filter((g): g is string => typeof g === 'string') : ['default'],
    }));
}

/** 应用启动时调一次：拉全量自选 + 分组，写进内存并通知订阅者。 */
export async function initWatchlist(): Promise<void> {
  try {
    const r = await fetch(`${API_BASE}/api/watchlist`);
    const j = await r.json();
    if (j?.code !== 0 || !j?.data) throw new Error(j?.error || '读取自选失败');
    items = toItems(j.data.items);
    groups = ensureDefault(Array.isArray(j.data.groups) ? j.data.groups : []);
    notifyWatchlistChanged();
  } catch (e) {
    // 拉不到就保持空列表，不阻塞页面渲染——自选挂了不该挡住看 K 线
    console.error('[watchlist] 加载自选失败', e);
  }
}

/* ──────────── 订阅/通知：跨组件同步 ──────────── */

const listeners = new Set<() => void>();

function notifyWatchlistChanged(): void {
  listeners.forEach((fn) => {
    try {
      fn();
    } catch {
      // swallow listener errors
    }
  });
}

/** 订阅 watchlist 变更（同 tab 内有效）。返回 unsubscribe 函数。 */
export function subscribeWatchlist(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/* ──────────── 读 ──────────── */

export function loadWatchlist(): WatchItem[] {
  return items;
}

/** 返回所有可见的分组（WatchlistPage tab 列表 / 爱心按钮的分组勾选弹窗）：
 *  用户创建的那些 + 末尾的 'default'（"其他"）。 */
export function getAllGroups(): WatchGroup[] {
  return ensureDefault(groups);
}

/**
 * 判断 code 是否已在用户的自选里。
 * 自选不再预填任何标的，所以这里等价于"用户自己加过"。
 */
export function isInUserWatchlist(code: string): boolean {
  const target = canonicalCode(code);
  return items.some((x) => x.code === target);
}

/** 判断 code 是否在某个分组里。 */
export function isInGroup(code: string, groupId: GroupKey): boolean {
  const target = canonicalCode(code);
  return items.some((x) => x.code === target && x.groups.includes(groupId));
}

export function knownIndices(): typeof INDICES {
  return INDICES;
}

/* ──────────── 写 ──────────── */

/** 落库失败时把内存状态弹回去的兜底。
 *  只 console.error 不弹窗：加自选是高频操作，后端挂了的话每次都 alert
 *  会烦死人；心图标弹回原状本身就是反馈。 */
function rollback(prevItems: WatchItem[], prevGroups: WatchGroup[], action: string): void {
  items = prevItems;
  groups = prevGroups;
  notifyWatchlistChanged();
  console.error(`[watchlist] ${action}失败，已回滚到本地状态`);
}

/**
 * 把一只股票加入某个分组。
 *  - 若该 code 已存在于 watchlist 且尚未在该分组：把分组追加到它的 groups 数组（不删其他分组）
 *  - 若该 code 已存在且已在该分组：no-op（返回当前列表，连请求都不发）
 *  - 若该 code 不存在：新增一条 WatchItem，groups=[group]
 *
 * 返回更新后的列表（与旧签名一致，尽管调用方基本不用返回值）。
 */
export function addWatch(item: { code: string; name: string; group: GroupKey }): WatchItem[] {
  // 入参也要归一：内存里是归一后的形态，若入参没归一，
  // 下面 `x.code === code` 会判不相等，把同一只票插成第二条。
  const code = canonicalCode(item.code);
  const { name, group } = item;

  const existing = items.find((x) => x.code === code);
  if (existing?.groups.includes(group)) return items;

  const prevItems = items;
  items = existing
    ? items.map((x) =>
        x.code === code
          ? { ...x, groups: Array.from(new Set([...x.groups, group])) }
          : x,
      )
    : [...items, { code, name, groups: [group] }];
  notifyWatchlistChanged();

  void (async () => {
    try {
      const r = await fetch(`${API_BASE}/api/watchlist/items`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, group }),
      });
      const j = await r.json();
      if (j?.code !== 0) throw new Error(j?.error || '未知错误');
    } catch {
      rollback(prevItems, groups, `把 ${code} 加入分组`);
    }
  })();

  return items;
}

/**
 * 从 watchlist 移除一只股票（或仅从某个分组里移除）。
 *  - `removeWatch(code)`：把该 code 的所有分组都移除（整只股票从自选消失）
 *  - `removeWatch(code, groupId)`：只从指定分组移除；若这是它的最后一个分组，
 *    库里没有「本体表」可删，那只票自然就消失了（见后端 watchlist.py 模块头）
 */
export function removeWatch(code: string, groupId?: GroupKey): WatchItem[] {
  const target = canonicalCode(code);

  const prevItems = items;
  if (groupId === undefined) {
    items = items.filter((x) => x.code !== target);
  } else {
    items = items.flatMap((x) => {
      if (x.code !== target) return [x];
      const remainingGroups = x.groups.filter((g) => g !== groupId);
      // 剩 0 个组 → 整只股票从自选消失（不再 push）
      return remainingGroups.length > 0 ? [{ ...x, groups: remainingGroups }] : [];
    });
  }
  notifyWatchlistChanged();

  const q = groupId === undefined ? '' : `?group=${encodeURIComponent(groupId)}`;
  void (async () => {
    try {
      const r = await fetch(`${API_BASE}/api/watchlist/items/${encodeURIComponent(target)}${q}`, {
        method: 'DELETE',
      });
      const j = await r.json();
      if (j?.code !== 0) throw new Error(j?.error || '未知错误');
    } catch {
      rollback(prevItems, groups, `从自选移除 ${target}`);
    }
  })();

  return items;
}

/* ──────────── 分组管理 ──────────── */

/**
 * 创建新分组，返回新分组的完整定义（id 由后端生成）。
 *
 * 同名去重（`名称 (2)` / `名称 (3)` …）**已经挪到后端**了：只有后端能看见
 * 全部分组，前端看到的可能还没加载完，靠前端去重会漏。前端只负责把后端
 * 返回的**最终** label 拿来用。
 *
 * 现在是**异步**的（要等后端发 id），调用方记得 await。
 * 抛错场景：label 为空 / 仅含空白，或后端出错。
 */
export async function createUserGroup(label: string): Promise<WatchGroup> {
  const trimmed = label.trim();
  if (!trimmed) throw new Error('分组名不能为空');

  const r = await fetch(`${API_BASE}/api/watchlist/groups`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label: trimmed }),
  });
  const j = await r.json();
  if (j?.code !== 0 || !j?.data) throw new Error(j?.error || '新建分组失败');

  const created: WatchGroup = { id: String(j.data.id), label: String(j.data.label) };
  // ⚠️ 新组要插在 default **前面**：直接 append 到末尾会让「其他」不再是最后一个
  // tab（后端返回的顺序是 default 恒末尾，本地这里必须跟它一致）。
  groups = ensureDefault([
    ...groups.filter((g) => g.id !== 'default' && g.id !== created.id),
    created,
  ]);
  notifyWatchlistChanged();
  return created;
}
