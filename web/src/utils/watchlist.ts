import { canonicalCode, INDICES } from './indices';

const STORAGE_KEY = 'zx-stock:watchlist';
const GROUPS_STORAGE_KEY = 'zx-stock:watch-groups';

/** 自选条目所属的分组。分组全部由用户创建，没有内置分组。
 *  `default`（"其他"）是唯一的兜底 ID：未归类 / 旧数据的自选都落在这里。 */
export type GroupKey = string;

export interface WatchItem {
  code: string;       // sh000001
  name: string;       // 上证指数
  addedAt: number;    // 时间戳
  note?: string;
  /** 这只股票所属的分组列表。一只股票可以同时存在于多个分组里。 */
  groups: GroupKey[];
}

/**
 * 自选分组定义。分组完全由用户创建，代码里不预置任何分组。
 *  - id: 唯一标识
 *  - label: 显示名
 */
export interface WatchGroup {
  id: string;
  label: string;
}

/** 旧版本内置的 'indices' 分组已下线：曾属于它的自选统一回落到兜底分组 'default'。 */
const LEGACY_BUILT_IN_GROUP = 'indices';

/** 把已下线的内置分组 id 归一到兜底分组，避免条目变成任何 tab 都看不到的孤儿。 */
function remapLegacyGroups(groups: GroupKey[]): GroupKey[] {
  const next = groups.map((g) => (g === LEGACY_BUILT_IN_GROUP ? 'default' : g));
  return Array.from(new Set(next));
}

function isWatchItem(x: unknown): x is WatchItem {
  return !!x && typeof x === 'object' && typeof (x as WatchItem).code === 'string';
}

/** 把任意对象归一成 WatchItem。兼容旧结构（`group?: string` 单值）。 */
function normalizeItem(raw: unknown): WatchItem | null {
  if (!isWatchItem(raw)) return null;
  let groups: GroupKey[];
  if (Array.isArray((raw as any).groups)) {
    groups = (raw as any).groups.filter((g: unknown) => typeof g === 'string');
  } else if (typeof (raw as any).group === 'string') {
    // 旧结构（单值 group）迁移到新结构（groups 数组）
    groups = [(raw as any).group as string];
  } else {
    // 没有任何分组 → 归到 "其他"（default）
    groups = ['default'];
  }
  if (groups.length === 0) groups = ['default'];
  return {
    // 迁移：2026-10-09 起 code 统一成 SH.600570，早期加的自选存的是 sh600570。
    // 不归一的话，同一只票会跟新加的那条并存成两条（去重/移除都按 code 精确
    // 比较），而且 name 会退化成一串代码。0AMV / mom 原样不动。
    code: canonicalCode(raw.code),
    name: typeof (raw as any).name === 'string' ? (raw as any).name : raw.code,
    addedAt: typeof (raw as any).addedAt === 'number' ? (raw as any).addedAt : Date.now(),
    note: typeof (raw as any).note === 'string' ? (raw as any).note : undefined,
    groups: Array.from(new Set(groups)),
  };
}

function isWatchGroup(x: unknown): x is WatchGroup {
  return !!x && typeof x === 'object'
    && typeof (x as WatchGroup).id === 'string'
    && typeof (x as WatchGroup).label === 'string';
}

export function loadWatchlist(): WatchItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr)) return [];
    const items = arr
      .map((rawItem) => normalizeItem(rawItem))
      .filter((x): x is WatchItem => x !== null);
    // 去重：同 code 只保留第一条（含 groups 信息）；多条同 code 通常是旧数据迁移产生的。
    const seen = new Set<string>();
    const dedup: WatchItem[] = [];
    for (const it of items) {
      if (seen.has(it.code)) continue;
      seen.add(it.code);
      // 迁移：内置 'indices' 分组下线后，原本挂在它下面的自选回落到 'default'。
      // 只在内存里归一，下次 saveWatchlist 时自然落盘，自愈不重复处理。
      dedup.push({ ...it, groups: remapLegacyGroups(it.groups) });
    }
    return dedup;
  } catch {
    return [];
  }
}

export function saveWatchlist(items: WatchItem[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  } catch {
    // ignore quota / private-mode errors
  }
}

/* ──────────── 订阅/通知：跨组件同步 localStorage 变更 ──────────── */

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

/**
 * 把一只股票加入某个分组。
 *  - 若该 code 已存在于 watchlist 且尚未在该分组：把分组追加到它的 groups 数组（不删其他分组）
 *  - 若该 code 已存在且已在该分组：no-op（返回当前列表）
 *  - 若该 code 不存在：新增一条 WatchItem，groups=[group]
 */
export function addWatch(item: { code: string; name: string; group: GroupKey; note?: string }): WatchItem[] {
  const list = loadWatchlist();
  // 入参也要归一：list 里是归一后的形态，若入参没归一，
  // 下面 `x.code === code` 会判不相等，把同一只票插成第二条。
  const code = canonicalCode(item.code);
  const { name, group, note } = item;
  const existing = list.find((x) => x.code === code);
  if (existing) {
    if (existing.groups.includes(group)) return list;
    const next = list.map((x) =>
      x.code === code
        ? { ...x, groups: Array.from(new Set([...x.groups, group])) }
        : x,
    );
    saveWatchlist(next);
    notifyWatchlistChanged();
    return next;
  }
  const next: WatchItem[] = [
    ...list,
    { code, name, addedAt: Date.now(), note, groups: [group] },
  ];
  saveWatchlist(next);
  notifyWatchlistChanged();
  return next;
}

/**
 * 从 watchlist 移除一只股票（或仅从某个分组里移除）。
 *  - `removeWatch(code)`：把该 code 的所有分组都移除（整只股票从自选消失）
 *  - `removeWatch(code, groupId)`：只从指定分组移除；若这是它的最后一个分组，等价于整只股票移除
 */
export function removeWatch(code: string, groupId?: GroupKey): WatchItem[] {
  const list = loadWatchlist();
  // 入参同样要归一，否则传 sh600570 过来会一条都删不掉。
  const target = canonicalCode(code);
  let next: WatchItem[];
  if (groupId === undefined) {
    next = list.filter((x) => x.code !== target);
  } else {
    next = [];
    for (const x of list) {
      if (x.code !== target) {
        next.push(x);
        continue;
      }
      const remainingGroups = x.groups.filter((g) => g !== groupId);
      if (remainingGroups.length > 0) {
        next.push({ ...x, groups: remainingGroups });
      }
      // remainingGroups.length === 0 → 整只股票从自选消失（不再 push）
    }
  }
  saveWatchlist(next);
  notifyWatchlistChanged();
  return next;
}

/**
 * 判断 code 是否已在用户的自选（localStorage）里。
 * 自选不再预填任何标的，所以这里等价于"用户自己加过"。
 */
export function isInUserWatchlist(code: string): boolean {
  const target = canonicalCode(code);
  return loadWatchlist().some((x) => x.code === target);
}

/**
 * 判断 code 是否在某个分组里。
 */
export function isInGroup(code: string, groupId: GroupKey): boolean {
  const target = canonicalCode(code);
  return loadWatchlist().some((x) => x.code === target && x.groups.includes(groupId));
}

export function knownIndices(): typeof INDICES {
  return INDICES;
}

/* ──────────── 分组管理 ──────────── */

function loadUserGroups(): WatchGroup[] {
  try {
    const raw = localStorage.getItem(GROUPS_STORAGE_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr)) return [];
    return arr.filter(isWatchGroup);
  } catch {
    return [];
  }
}

function saveUserGroups(groups: WatchGroup[]): void {
  try {
    localStorage.setItem(GROUPS_STORAGE_KEY, JSON.stringify(groups));
  } catch {
    // ignore quota / private-mode errors
  }
}

/**
 * 返回所有可见的分组（用于 WatchlistPage tab 列表 / 爱心按钮的分组勾选弹窗）：
 *   - 全部是用户自己创建的分组，按存储顺序排列
 *   - 兜底保证末尾有一个 'default'（label = "其他"），未归类的自选落在这里
 */
export function getAllGroups(): WatchGroup[] {
  const userGroups = loadUserGroups();
  const hasDefault = userGroups.some((g) => g.id === 'default');
  return hasDefault ? userGroups : [...userGroups, { id: 'default', label: '其他' }];
}

/**
 * 创建新分组，返回新分组的完整定义（id 已生成）。
 * 同名会自动去重为 "name (2)" / "name (3)" ...。
 * 抛错场景：label 为空或仅含空白。
 */
export function createUserGroup(label: string): WatchGroup {
  const trimmed = label.trim();
  if (!trimmed) throw new Error('分组名不能为空');
  const userGroups = loadUserGroups();
  // 同名去重：name / name (#2) / name (#3) ...
  let finalLabel = trimmed;
  const existingLabels = new Set(userGroups.map((g) => g.label));
  if (existingLabels.has(finalLabel)) {
    let n = 2;
    while (existingLabels.has(`${trimmed} (${n})`)) n++;
    finalLabel = `${trimmed} (${n})`;
  }
  const id = `g_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  // 新分组插在兜底分组前面，保证 'default'（"其他"）始终排在最后
  const next: WatchGroup[] = [
    ...userGroups.filter((g) => g.id !== 'default'),
    { id, label: finalLabel },
    { id: 'default', label: '其他' },
  ];
  saveUserGroups(next);
  notifyWatchlistChanged();
  return { id, label: finalLabel };
}
