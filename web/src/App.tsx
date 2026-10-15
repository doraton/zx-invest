import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { useEffect } from 'react';
import Layout from './components/Layout';
import HomePage from './pages/HomePage';
import MarketPage from './pages/MarketPage';
import WatchlistPage from './pages/WatchlistPage';
import PlaceholderPage from './pages/PlaceholderPage';
import { initWatchlist } from './utils/watchlist';
import './App.css';

function App() {
  // 自选自 2026-10-14 起存在后端（watch_group / watch_item），启动时拉一次。
  // 拉回来之前各页面的自选列表是空的，initWatchlist 内部 notify 一次即可补上。
  // 不 await：拉不到也不该挡住渲染，失败只在 console 里报错。
  useEffect(() => {
    void initWatchlist();
  }, []);

  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          {/* 首页已按要求**留空**（只去掉了其中的行情图，页面和侧栏入口都保留）。 */}
          <Route path="/" element={<HomePage />} />
          <Route path="/market" element={<MarketPage />} />
          <Route path="/watchlist" element={<WatchlistPage />} />
          <Route path="/condition" element={<PlaceholderPage title="条件检索" />} />
          <Route path="/trade-review" element={<PlaceholderPage title="交易复盘" />} />
          <Route path="/stock-detail" element={<PlaceholderPage title="个股详情" />} />
          <Route path="/industry" element={<PlaceholderPage title="行业板块" />} />
          <Route path="/etf" element={<PlaceholderPage title="ETF 专区" />} />
          <Route path="/history-sample" element={<PlaceholderPage title="历史样本统计" />} />
          <Route path="/history-backtest" element={<PlaceholderPage title="历史回测" />} />
          <Route path="/simulator" element={<PlaceholderPage title="模拟训练" />} />
          <Route path="*" element={<PlaceholderPage title="页面不存在" hint="404 Not Found" />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}

export default App;