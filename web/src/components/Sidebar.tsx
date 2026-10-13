import { NavLink } from 'react-router-dom';
import { SearchOutlined } from '@ant-design/icons';
import './Sidebar.css';

interface NavItem {
  key: string;
  label: string;
  path: string;
  icon?: string;
}

/**
 * 侧栏顺序即数组顺序，前两项是实际有内容的页面（首页当前留空），
 * 其余 8 个是尚未实施的占位页（对应 docs/TODO.md 的 P1 段），
 * 保留入口是为了让路线图在界面上可见。
 *
 * 2026-10-08：去掉了首页里的行情图，**首页页面与这一项都保留**。
 * 原来的 K 线图和自选页能力重复（自选页右侧是同一个 MarketChart），
 * 但空页面保留了菜单结构的可扩展性。
 */
const NAV_ITEMS: NavItem[] = [
  { key: 'home', label: '首页', path: '/' },
  { key: 'market', label: '行情', path: '/market' },
  { key: 'watchlist', label: '自选', path: '/watchlist' },
  { key: 'condition', label: '条件检索', path: '/condition' },
  { key: 'trade-review', label: '交易复盘', path: '/trade-review' },
  { key: 'stock-detail', label: '个股详情', path: '/stock-detail' },
  { key: 'industry', label: '行业板块', path: '/industry' },
  { key: 'etf', label: 'ETF专区', path: '/etf' },
  { key: 'history-sample', label: '历史样本统计', path: '/history-sample' },
  { key: 'history-backtest', label: '历史回测', path: '/history-backtest' },
  { key: 'simulator', label: '模拟训练', path: '/simulator' },
];

interface SidebarProps {
  /** 唤起键盘精灵（由 Layout 持有 open 状态）。 */
  onSearch: () => void;
}

export default function Sidebar({ onSearch }: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        {/* logo 占位。目前没有图形资源，先画一个空白方框把位置和尺寸占住——
            以后放上图片只要把这一格换掉，右侧的主副标题不用动。 */}
        <span className="logo-image" aria-hidden="true" />
        <div className="logo-text">
          <div className="logo-mark">知行投资</div>
          {/* 副标题：产品英文名。logo-mark 是蓝色主标题，这行压成灰色小字做层级。 */}
          <div className="logo-title">zx-invest</div>
        </div>
      </div>
      {/* 键盘精灵的可见入口。快捷键 `/` 本身不可发现——界面上没有这个提示，
          用户就永远不知道有它。放一个按钮 + 键位角标，两条路都通。 */}
      <button type="button" className="sidebar-search" onClick={onSearch}>
        <SearchOutlined />
        <span className="sidebar-search-label">搜索股票</span>
        <kbd className="sidebar-search-kbd">/</kbd>
      </button>
      <nav className="sidebar-nav">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.key}
            to={item.path}
            // end 只给首页：否则 `/` 会在所有子路由下都高亮
            end={item.path === '/'}
            className={({ isActive }) => `sidebar-item${isActive ? ' active' : ''}`}
          >
            <span className="sidebar-icon" aria-hidden>
              {item.icon ?? '·'}
            </span>
            <span className="sidebar-label">{item.label}</span>
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}