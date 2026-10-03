import { useEffect, useRef, useState } from 'react';
import { Card, Select, Spin, Row, Col, Tag, Radio } from 'antd';
import { init, dispose, type Chart, type KLineData as KCKLineData } from 'klinecharts';
import './MarketChart.css';

const API_BASE = 'http://localhost:8310';

interface ApiKLine {
  datetime: string;
  open: number;
  high: number;
  low: number;
  close: number;
  amount: number;
}

interface QuoteData {
  open: number;
  close: number;
  high: number;
  low: number;
}

interface IndexInfo {
  code: string;       // 完整代码：sh000001
  name: string;       // 全名：上证指数
  short: string;      // 简称：上证
}

type PeriodType = 'day' | 'week' | 'month';

const PERIOD_OPTIONS = [
  { label: '日K', period: { type: 'day' as PeriodType, span: 1 }, category: 4 },
  { label: '周K', period: { type: 'week' as PeriodType, span: 1 }, category: 5 },
  { label: '月K', period: { type: 'month' as PeriodType, span: 1 }, category: 6 },
];

const RANGE_OPTIONS = [
  { label: '近1K', count: 200 },
  { label: '近5K', count: 1000 },
  { label: '全部', count: -1 },
];

const INDICES: IndexInfo[] = [
  { code: 'sh000001', name: '上证指数', short: '上证' },
  { code: 'sz399001', name: '深证成指', short: '深证' },
  { code: 'sz399006', name: '创业板指', short: '创业板' },
  { code: 'sh000688', name: '科创50',   short: '科创50' },
];

const DARK_THEME = {
  background: { color: '#0d1117' },
  grid: {
    show: true,
    horizontal: { show: true, color: '#1c2128' },
    vertical: { show: true, color: '#1c2128' },
  },
  candle: {
    bar: {
      upColor: '#ef5350',
      downColor: '#26a69a',
      noChangeColor: '#888888',
      upBorderColor: '#ef5350',
      downBorderColor: '#26a69a',
      noChangeBorderColor: '#888888',
      upWickColor: '#ef5350',
      downWickColor: '#26a69a',
      noChangeWickColor: '#888888',
    },
    priceMark: {
      last: {
        line: { color: '#888', dashedValue: [4, 4], size: 1 },
        text: { color: '#fff', size: 12 },
      },
    },
  },
  indicator: {
    tooltip: {
      text: { color: '#e6e6e6' },
      custom: [
        { title: '时间', value: '{time}' },
        { title: '开', value: '{open}' },
        { title: '收', value: '{close}' },
        { title: '高', value: '{high}' },
        { title: '低', value: '{low}' },
        { title: '量', value: '{volume}' },
      ],
    },
    lines: [
      { style: 'solid', smooth: false, size: 1, dashedValue: [0, 0], color: '#FFA500' },
      { style: 'solid', smooth: false, size: 1, dashedValue: [0, 0], color: '#935EBD' },
      { style: 'solid', smooth: false, size: 1, dashedValue: [0, 0], color: '#1677FF' },
    ],
  },
};

export default function MarketChart() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<Chart | null>(null);
  const requestIdRef = useRef(0);
  const fetchQuoteTimerRef = useRef<number | undefined>(undefined);

  // 同步 ref（让 setDataLoader 内的 fetch 能读到最新 React state，避免闭包陷阱）
  const currentIndexRef = useRef<IndexInfo>(INDICES[0]);
  const periodRef = useRef(PERIOD_OPTIONS[0]);
  const rangeRef = useRef(RANGE_OPTIONS[0]);

  const [currentIndex, setCurrentIndex] = useState<IndexInfo>(INDICES[0]);
  const [quote, setQuote] = useState<QuoteData | null>(null);
  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState(PERIOD_OPTIONS[0]);
  const [range, setRange] = useState(RANGE_OPTIONS[0]);
  const [updateTime, setUpdateTime] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  // 保持 ref 同步
  useEffect(() => { currentIndexRef.current = currentIndex; }, [currentIndex]);
  useEffect(() => { periodRef.current = period; }, [period]);
  useEffect(() => { rangeRef.current = range; }, [range]);

  // 初始化 chart 实例（仅一次）
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = init(containerRef.current, { styles: DARK_THEME as never });
    if (!chart) return;

    chart.createIndicator({ name: 'MA', calcParams: [20], paneId: 'candle_pane', id: 'MA20' }, false);
    chart.createIndicator('VOL', true);
    chart.createIndicator('MACD', true);

    chart.setDataLoader({
      getBars: async (params) => {
        if (params.type !== 'init') {
          params.callback([], false);
          return;
        }
        const myId = ++requestIdRef.current;
        // 从 params.symbol 读 ticker（切换指数时 chart 会传新 symbol）
        const ticker = params.symbol?.ticker || currentIndexRef.current.code;
        try {
          const url = `${API_BASE}/api/index/kline?source=akshare&code=${encodeURIComponent(ticker)}&category=${periodRef.current.category}&count=${rangeRef.current.count}`;
          const resp = await fetch(url);
          const json = await resp.json();
          if (myId !== requestIdRef.current) return;
          if (json.code !== 0) {
            setError(json.error || 'K 线加载失败');
            params.callback([], false);
            return;
          }
          setError(null);
          const data: ApiKLine[] = json.data || [];
          const list = data
            .map(toChartData)
            .filter((d): d is NonNullable<typeof d> => d !== null) as KCKLineData[];
          params.callback(list, false);
        } catch (e) {
          if (myId !== requestIdRef.current) return;
          setError((e as Error).message);
          params.callback([], false);
        }
      },
    });

    chart.setSymbol({ ticker: currentIndexRef.current.code, pricePrecision: 2, volumePrecision: 0 });
    chart.setPeriod(periodRef.current.period);

    chartRef.current = chart;
    setLoading(false);

    return () => {
      if (containerRef.current) dispose(containerRef.current);
      chartRef.current = null;
    };
  }, []);

  // 指数切换 / 周期切换 / 范围切换：触发 chart 重新加载
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.setSymbol({ ticker: currentIndex.code, pricePrecision: 2, volumePrecision: 0 });
    chart.setPeriod(period.period);
  }, [currentIndex, period, range]);

  // 实时行情轮询（跟着当前指数切换）
  useEffect(() => {
    const fetchQuoteOnce = async () => {
      try {
        const url = `${API_BASE}/api/index/quote?source=akshare&code=${encodeURIComponent(currentIndex.code)}`;
        const resp = await fetch(url);
        const json = await resp.json();
        if (json.code === 0 && json.data) {
          setQuote(json.data);
          setUpdateTime(new Date().toLocaleTimeString('zh-CN', { hour12: false }));
        }
      } catch {
        // 静默
      }
    };
    fetchQuoteOnce();
    if (fetchQuoteTimerRef.current) clearInterval(fetchQuoteTimerRef.current);
    fetchQuoteTimerRef.current = window.setInterval(fetchQuoteOnce, 5000);
    return () => {
      if (fetchQuoteTimerRef.current) clearInterval(fetchQuoteTimerRef.current);
    };
  }, [currentIndex]);

  const changePct = quote ? ((quote.close - quote.open) / quote.open * 100) : null;
  const isUp = changePct !== null && changePct >= 0;

  return (
    <div className="market-chart-page">
      <Card
        className="chart-card"
        title={
          <div className="header-title">
            <Radio.Group
              value={currentIndex.code}
              onChange={(e) => {
                const next = INDICES.find((i) => i.code === e.target.value);
                if (next) setCurrentIndex(next);
              }}
              optionType="button"
              buttonStyle="solid"
              size="small"
              style={{ marginRight: 12 }}
            >
              {INDICES.map((idx) => (
                <Radio.Button key={idx.code} value={idx.code}>{idx.short}</Radio.Button>
              ))}
            </Radio.Group>
            <span className="symbol-name">{currentIndex.name}</span>
            <Tag color="blue" style={{ marginLeft: 8 }}>指数行情</Tag>
          </div>
        }
        extra={
          <div className="header-controls">
            <Select
              size="small"
              value={range.label}
              onChange={(v) => setRange(RANGE_OPTIONS.find((o) => o.label === v)!)}
              options={RANGE_OPTIONS.map((o) => ({ label: o.label, value: o.label }))}
              style={{ width: 90, marginRight: 8 }}
            />
            <Select
              size="small"
              value={period.label}
              onChange={(v) => setPeriod(PERIOD_OPTIONS.find((o) => o.label === v)!)}
              options={PERIOD_OPTIONS.map((o) => ({ label: o.label, value: o.label }))}
              style={{ width: 90 }}
            />
          </div>
        }
      >
        {quote && (
          <Row gutter={16} className="quote-row">
            <Col>
              <span className="label">当前点位</span>
              <span className={`value ${isUp ? 'up' : 'down'}`}>{quote.close.toFixed(2)}</span>
            </Col>
            <Col>
              <span className="label">涨跌幅</span>
              <span className={`value ${isUp ? 'up' : 'down'}`}>
                {changePct !== null ? `${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%` : '--'}
              </span>
            </Col>
            <Col>
              <span className="label">开盘</span>
              <span className="value">{quote.open.toFixed(2)}</span>
            </Col>
            <Col>
              <span className="label">最高</span>
              <span className="value up">{quote.high.toFixed(2)}</span>
            </Col>
            <Col>
              <span className="label">最低</span>
              <span className="value down">{quote.low.toFixed(2)}</span>
            </Col>
            <Col>
              <span className="label">更新时间</span>
              <span className="value">{updateTime || '--'}</span>
            </Col>
          </Row>
        )}

        <Spin spinning={loading} tip="加载中...">
          <div ref={containerRef} className="kline-container" />
        </Spin>

        {error && <div className="error-tip">⚠ {error}</div>}
      </Card>
    </div>
  );
}

function toChartData(k: ApiKLine) {
  const ts = Date.parse(`${k.datetime}T00:00:00+08:00`);
  if (Number.isNaN(ts)) return null;
  return {
    timestamp: ts,
    open: k.open,
    high: k.high,
    low: k.low,
    close: k.close,
    volume: k.amount,
  };
}