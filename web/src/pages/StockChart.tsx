import { useState, useEffect, useCallback } from 'react';
import { Card, Spin, Select, Typography, Row, Col } from 'antd';
import {
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import './StockChart.css';

const { Title } = Typography;

interface KLineData {
  datetime: string;
  open: number;
  high: number;
  low: number;
  close: number;
  /** 成交量（手）。字段名与库列 `volume` 一致。 */
  volume: number;
}

const CATEGORY_MAP: Record<string, number> = {
  '日K': 4,
  '周K': 5,
};

import { API_BASE } from '../config/api';

export default function StockChart() {
  const [klineData, setKlineData] = useState<KLineData[]>([]);
  const [loading, setLoading] = useState(false);
  const [category, setCategory] = useState('日K');
  const [quote, setQuote] = useState<{ last: number; open: number; high: number; low: number } | null>(null);

  const fetchKline = useCallback(async () => {
    setLoading(true);
    try {
      const catValue = CATEGORY_MAP[category];
      const res = await fetch(`${API_BASE}/api/index/kline?category=${catValue}&count=100`);
      const json = await res.json();
      if (json.code === 0) {
        setKlineData(json.data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [category]);

  const fetchQuote = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/index/quote`);
      const json = await res.json();
      if (json.code === 0 && json.data) {
        setQuote({
          last: json.data.close,
          open: json.data.open,
          high: json.data.high,
          low: json.data.low,
        });
      }
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    fetchKline();
  }, [fetchKline]);

  useEffect(() => {
    fetchQuote();
    const interval = setInterval(fetchQuote, 5000);
    return () => clearInterval(interval);
  }, [fetchQuote]);

  const change = quote ? ((quote.last - quote.open) / quote.open * 100).toFixed(2) : '--';
  const isUp = Number(change) >= 0;

  return (
    <div className="stock-chart-page">
      <Card
        title={<Title level={3}>上证指数</Title>}
        extra={
          <Select
            value={category}
            onChange={v => { setCategory(v); }}
            options={Object.keys(CATEGORY_MAP).map(k => ({ label: k, value: k }))}
            style={{ width: 120 }}
          />
        }
      >
        {quote && (
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col><span className="label">当前点位</span><span className={`value ${isUp ? 'up' : 'down'}`}>{quote.last.toFixed(2)}</span></Col>
            <Col><span className="label">涨跌幅</span><span className={`value ${isUp ? 'up' : 'down'}`}>{change}%</span></Col>
            <Col><span className="label">开盘</span><span className="value">{quote.open.toFixed(2)}</span></Col>
            <Col><span className="label">最高</span><span className="value up">{quote.high.toFixed(2)}</span></Col>
            <Col><span className="label">最低</span><span className="value down">{quote.low.toFixed(2)}</span></Col>
          </Row>
        )}

        <Spin spinning={loading}>
          <div style={{ height: 500 }}>
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={klineData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="datetime" tick={{ fontSize: 10 }} />
                <YAxis domain={['auto', 'auto']} />
                <Tooltip />
                <Legend />
                <Bar dataKey="volume" fill="#82ca9d" name="成交量" yAxisId="volume" />
                <Line type="monotone" dataKey="close" stroke="#8884d8" name="收盘价" dot={false} yAxisId="price" />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Spin>
      </Card>
    </div>
  );
}