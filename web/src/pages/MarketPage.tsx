import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Empty, Spin, Typography } from 'antd';
import { fetchStockList, type StockRow } from '../config/api';
import { findIndex } from '../utils/indices';
import './MarketPage.css';

const { Text } = Typography;

function fmtDate(d: string | null | undefined): string {
  return d ?? '—';
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
}

/** 涨跌额：与 fmtPct 同规则，只是没有 % 后缀。 */
function fmtChg(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}`;
}

/**
 * 更新时间（``stock_info.updated_at`` / ``index_info.updated_at``）只取到「日」。
 *
 * 后端给的是完整 ISO 串（``2026-10-09T14:13:21``），这列叫「更新日期」，
 * 而且整列宽要留给数字列——截掉时分还能省一半宽度。代价是同一天内多次同步
 * 看不出先后，真要分辨得另开一列。
 */
function fmtDay(iso: string | null | undefined): string {
  if (!iso) return '—';
  return iso.slice(0, 10);
}

/**
 * 证券名称的三级兜底。
 *
 * 1. 后端 ``stock.name`` —— 权威来源。2026-10-08 起由
 *    ``tools/backfill_stock_from_klines.py`` 按「有行情的代码」回填（来源是本地
 *    通达信 T0002/hq_cache 的 tnf 名称表），Tushare ``stock_basic`` 有额度后
 *    再由 ``sync_stock`` 补齐行业等字段。
 * 2. 前端 ``INDICES`` —— 只对内置的那几个标的命中，属于过渡兜底，
 *    不是数据源。等 ``stock`` 表覆盖全部标的后这一级自然不再触发。
 * 3. 6 位数字码 —— 总比空白强。
 */
function displayName(row: StockRow): string {
  return row.name ?? findIndex(row.code)?.name ?? row.code.slice(2);
}

function changeClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '';
  return v > 0 ? 'up' : v < 0 ? 'down' : '';
}

export default function MarketPage() {
  const [rows, setRows] = useState<StockRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchStockList();
      if (res.code !== 0) {
        setError(res.error || '加载失败');
        setRows([]);
        return;
      }
      setRows(res.data ?? []);
    } catch (e) {
      setError((e as Error).message);
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="market-page">
      <div className="market-header">
        <div className="market-header-left">
          <h1 className="market-title">行情</h1>
          <div className="market-stats">
            <span className="stat-item">
              标的 <b>{rows.length}</b> 只
            </span>
          </div>
        </div>
      </div>

      <div className="market-body">
        {error && (
          <div className="market-error">
            ⚠ {error}
            <Text type="secondary" className="market-error-hint">
              请确认后端已在 8310 端口运行（需要 ZX_MYSQL_DB=zx_invest）。
            </Text>
          </div>
        )}

        {loading && rows.length === 0 ? (
          <div className="market-loading">
            <Spin description="加载中…" />
          </div>
        ) : rows.length === 0 && !error ? (
          <div className="market-empty">
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <div>
                  <div className="market-empty-title">库里还没有行情数据</div>
                  <div className="market-empty-hint">
                    先跑一次同步，例如：
                    <code>tools\sync_tushare.py --db zx_invest --code 600570.SH --skip-meta</code>
                  </div>
                </div>
              }
            />
          </div>
        ) : (
          <table className="market-table">
            <thead>
              <tr>
                <th className="col-code">代码</th>
                <th className="col-name">名称</th>
                <th className="col-num">最新价</th>
                <th className="col-num">涨跌额</th>
                <th className="col-num">涨跌幅</th>
                <th className="col-date">最后交易日</th>
                <th className="col-date">更新日期</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.code}
                  className="market-row"
                  title="点开在自选页查看这只票的行情（不会自动加入自选）"
                  // 只跳自选页、不调 addWatch：加入自选是用户自己的决定。
                  onClick={() => navigate(`/watchlist?code=${encodeURIComponent(r.code)}`)}
                >
                  <td className="col-code">{r.code}</td>
                  <td className="col-name">{displayName(r)}</td>
                  <td className="col-num">{r.close?.toFixed(2) ?? '—'}</td>
                  <td className={`col-num ${changeClass(r.change)}`}>{fmtChg(r.change)}</td>
                  <td className={`col-num ${changeClass(r.change_pct)}`}>
                    {fmtPct(r.change_pct)}
                  </td>
                  <td className="col-date">{fmtDate(r.last_date)}</td>
                  <td className="col-date">{fmtDay(r.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}