import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Modal, Spin, Empty } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { searchStocks, type StockSearchResult } from '../utils/stockSearch';
import { formatSymbolCode } from '../utils/formatCode';
import './StockSprite.css';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * 全局「键盘精灵」：按 `/` 唤起（也可点侧栏的搜索按钮），输代码 / 汉字 /
 * 拼音首字母，回车跳到自选页看它。
 *
 * 受控而非内部 state：打开方式有两个（键盘快捷键、侧栏按钮），
 * 都由 Layout 持有同一个 open 状态，避免两处各存一份导致不同步。
 *
 * 为什么跳自选页而不是个股详情
 * ----------------------------
 * 自选页右侧就是完整的 MarketChart（受控模式），而且它**已经支持渲染
 * 一只不在自选里的股票**——见 WatchlistPage 的 selectedCode 兜底分支。
 * 选中后写进 URL query 即可，不会在自选列表里凭空多出一项。
 *
 * 为什么用 `event.code` 也认 `event.key`
 * -------------------------------------
 * `key` 会受输入法/布局影响（中文输入法下按 `/` 出来的 `key` 可能不是
 * `'/'`），`code` 是物理键位更稳；但只认 `code` 会在只派发 `key` 的
 * 环境里静默失灵，所以两个都判。
 */
export default function StockSprite({ open, onOpenChange }: Props) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<StockSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const navigate = useNavigate();

  // ---- 全局 `/` 快捷键 ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // code 和 key 都认：`code` 是物理键位（不受输入法影响，最可靠），
      // 但有些环境（部分 IME、程序化派发的事件）只给 `key` 不给 `code`，
      // 只判 code 会让快捷键在这些场景下静默失灵。
      const isSlash = e.code === 'Slash' || e.key === '/';
      if (!isSlash || e.ctrlKey || e.metaKey || e.altKey) return;
      // 正在输入框里打字时 `/` 必须原样输出，不能劫持成快捷键。
      // 这个守卫跟 klinecharts 自己的做法一致（见它的 _boundKeyBoardDownEvent）。
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || t?.isContentEditable) return;
      e.preventDefault();
      onOpenChange(!open);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onOpenChange]);

  // 打开后聚焦输入框。
  // 必须用 Modal 的 afterOpenChange（动画结束）而不是 useEffect + rAF：
  // destroyOnHidden 下 Modal 内容是动画结束后才挂载的，rAF 跑的时候
  // input 还不存在，focus() 落在 null 上，什么都不会发生。
  const focusInput = useCallback(() => {
    inputRef.current?.focus();
  }, []);

  // 每次打开都重置
  useEffect(() => {
    if (open) {
      setQuery('');
      setResults([]);
      setActive(0);
    }
  }, [open]);

  // ---- 搜索（200ms debounce） ----
  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    if (!q) {
      setResults([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    let alive = true;
    const timer = window.setTimeout(async () => {
      try {
        const r = await searchStocks(q, 30);
        if (alive) {
          setResults(r);
          setActive(0);
        }
      } finally {
        if (alive) setLoading(false);
      }
    }, 200);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [query, open]);

  const pick = useCallback(
    (r: StockSearchResult) => {
      onOpenChange(false);
      // 只带 query 参数跳转，不调用 addWatch——加入自选是用户自己的决定，
      // 由自选页上的「加入自选」按钮触发。
      navigate(`/watchlist?code=${encodeURIComponent(r.code)}`);
    },
    [navigate, onOpenChange],
  );

  const onInputKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (results.length) setActive((i) => (i + 1) % results.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (results.length) setActive((i) => (i - 1 + results.length) % results.length);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const r = results[active];
      // 结果为空时不吞掉回车——否则输入框里打换行就没反应了
      if (r) pick(r);
    }
  };

  return (
    <Modal
      open={open}
      onCancel={() => onOpenChange(false)}
      afterOpenChange={(opened) => {
        if (opened) focusInput();
      }}
      footer={null}
      width={520}
      destroyOnHidden
      title={
        <span className="sprite-title">
          键盘精灵
          <span className="sprite-hint">↑↓ 选择 · Enter 打开 · Esc 关闭</span>
        </span>
      }
      // 必须挂 body：.app-main 有布局约束，留在原地会被裁掉
      getContainer={() => document.body}
      className="stock-sprite-modal"
    >
      <div className="sprite-input-wrap">
        <SearchOutlined className="sprite-input-icon" />
        <input
          ref={inputRef}
          className="sprite-input"
          value={query}
          placeholder="代码 / 名称 / 拼音首字母（如 600570 · 恒生电子 · hsdz）"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onInputKeyDown}
          spellCheck={false}
          autoComplete="off"
        />
      </div>

      <div className="sprite-results">
        {loading && results.length === 0 ? (
          <div className="sprite-loading">
            <Spin size="small" />
          </div>
        ) : query.trim() ? (
          results.length === 0 ? (
            <div className="sprite-empty">
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={<span style={{ color: '#768390' }}>没有匹配的股票</span>}
              />
            </div>
          ) : (
            results.map((r, i) => (
              <div
                key={r.code}
                className={`sprite-row${i === active ? ' active' : ''}`}
                // 鼠标移过就把键盘选中项跟过去，否则 ↓ 一下高亮会跳回原来的行
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(r)}
              >
                <span className="sprite-row-name">{r.name}</span>
                <span className="sprite-row-code">{formatSymbolCode(r.code)}</span>
              </div>
            ))
          )
        ) : (
          <div className="sprite-empty">
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <span style={{ color: '#768390' }}>
                  只收录沪深主板 / 创业板 / 科创板
                </span>
              }
            />
          </div>
        )}
      </div>
    </Modal>
  );
}