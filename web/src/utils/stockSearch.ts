import { API_BASE } from '../config/api';

export interface StockSearchResult {
  code: string;       // 完整带前缀代码，例如 SH.600570（2026-10-09 起 stock_info 的落库形态）
  raw_code: string;   // 6 位代码，例如 600570
  name: string;       // 中文名称
}

interface ApiResponse {
  code: number;
  data?: StockSearchResult[];
  query?: string;
  error?: string;
}

export async function searchStocks(query: string, limit = 30): Promise<StockSearchResult[]> {
  const q = (query || '').trim();
  if (!q) return [];
  const url = `${API_BASE}/api/stock/search?q=${encodeURIComponent(q)}&limit=${limit}`;
  try {
    const resp = await fetch(url);
    const json = (await resp.json()) as ApiResponse;
    if (json.code !== 0) return [];
    return json.data || [];
  } catch {
    return [];
  }
}