"""数据源抽象层。

提供统一接口 `DataSource`，具体实现可插拔。当前内置：
- AkshareDataSource: AKShare + 新浪接口（默认，无需 token / 通达信）
- TqcenterDataSource: 通达信客户端 tqcenter
- PytdxDataSource: pytdx 直连

通过 `DataSourceFactory` 注册 / 创建 / 列表。
"""
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Type


@dataclass
class KLineData:
    datetime: str
    open: float
    high: float
    low: float
    close: float
    amount: float  # 注：akshare 数据源使用 volume 填充此字段


@dataclass
class QuoteData:
    open: float
    close: float
    high: float
    low: float
    prev_close: float = 0.0  # 昨收；与 close 之差 = 当天涨跌


class DataSource(ABC):
    @abstractmethod
    def get_index_quote(self, market: int = 0, code: str = "000001") -> Optional[QuoteData]: ...

    @abstractmethod
    def get_index_kline(self, market: int = 0, code: str = "000001",
                        category: int = 4, count: int = 200) -> List[KLineData]: ...

    @abstractmethod
    def search_stocks(self, query: str, limit: int = 30) -> List[Dict[str, str]]: ...


# ========================== AKShare + 新浪兜底 ==========================

class _SinaQuoteHelper:
    """新浪 hq.sinajs.cn 实时行情兜底（akshare 实时接口频繁改版）。"""

    _FIELD_OPEN = 3
    _FIELD_CLOSE = 1
    _FIELD_HIGH = 4
    _FIELD_LOW = 5
    _FIELD_PREV_CLOSE = 2  # 昨收

    @classmethod
    def _to_sina_symbol(cls, code: str) -> str:
        if code.startswith(('sh', 'sz', 'SH', 'SZ')):
            return code.lower()
        return 'sh' + code

    @classmethod
    def fetch(cls, code: str = "000001") -> Optional[QuoteData]:
        import urllib.request
        url = f"http://hq.sinajs.cn/list={cls._to_sina_symbol(code)}"
        req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn/'})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode('gbk')
        except Exception as e:
            import logging
            logging.warning(f"[SinaQuoteHelper] fetch failed: {e}")
            return None
        try:
            payload = body.split('"')[1]
            fields = payload.split(',')
            return QuoteData(
                open=float(fields[cls._FIELD_OPEN]),
                close=float(fields[cls._FIELD_CLOSE]),
                high=float(fields[cls._FIELD_HIGH]),
                low=float(fields[cls._FIELD_LOW]),
                prev_close=float(fields[cls._FIELD_PREV_CLOSE]),
            )
        except (IndexError, ValueError) as e:
            import logging
            logging.warning(f"[SinaQuoteHelper] parse failed: {e}")
            return None


class AkshareDataSource(DataSource):
    """东方财富 HTTP 接口 + 新浪实时行情，免费无需 token，稳定不依赖 py_mini_racer。

    注：历史名称保留 'akshare'，实际实现改为直接 HTTP，避免 akshare 1.18+
    对 py_mini_racer 的依赖在 CPython 3.14 上崩溃的问题。
    """

    # category: 4=日K, 5=周K, 6=月K
    _klt_map = {4: 'day', 5: 'week', 6: 'month'}
    _MAX_PER_REQ = 800  # 腾讯接口单次最多返回条数

    @staticmethod
    def _to_tx_symbol(code: str) -> str:
        """000001 → sh000001。"""
        if code.startswith(('sh', 'sz', 'SH', 'SZ')):
            return code.lower()
        return 'sh' + code

    def _fetch_raw_kline(self, code: str, period: str, count: int):
        """直接调腾讯财经 K 线接口。

        返回: [(date, open, close, high, low, volume), ...]
        字段顺序固定为：date,open,close,high,low,volume。
        """
        import urllib.request
        import json
        symbol = self._to_tx_symbol(code)
        url = (
            f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
            f'?param={symbol},{period},,,{min(count, self._MAX_PER_REQ)},qfq'
        )
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
        payload = json.loads(body)
        if payload.get('code') != 0:
            return []
        info = (payload.get('data') or {}).get(symbol) or {}
        # 优先 qfq 字段（指数无复权但接口一致）；fallback 到非复权
        key = f'qfq{period}' if f'qfq{period}' in info else period
        rows = info.get(key) or []
        result = []
        for parts in rows:
            if len(parts) < 6:
                continue
            try:
                result.append((
                    parts[0],
                    float(parts[1]),  # open
                    float(parts[2]),  # close
                    float(parts[3]),  # high
                    float(parts[4]),  # low
                    float(parts[5]),  # volume
                ))
            except (ValueError, IndexError):
                continue
        return result

    def get_index_quote(self, market: int = 0, code: str = "000001") -> Optional[QuoteData]:
        # 新浪接口（akshare 实时接口在 1.18+ 已失效；新浪稳）
        quote = _SinaQuoteHelper.fetch(code)
        if quote is not None:
            return quote
        # 回退：腾讯最近一根 K 线作为 quote
        try:
            rows = self._fetch_raw_kline(code, period='day', count=1)
            if not rows:
                return None
            _, op, cl, hi, lo, _ = rows[-1]
            return QuoteData(open=op, close=cl, high=hi, low=lo)
        except Exception as e:
            import logging
            logging.warning(f"[AkshareDataSource] get_index_quote fallback failed: {e}")
            return None

    def get_index_kline(self, market: int = 0, code: str = "000001",
                        category: int = 4, count: int = 200) -> List[KLineData]:
        try:
            period = self._klt_map.get(category, 'day')
            # count=-1 表示全部；分批拉直到拿满
            if count is None or count < 0:
                count = 9000
            rows = self._fetch_raw_kline(code, period=period, count=min(count, self._MAX_PER_REQ))
            if not rows:
                return []

            result: List[KLineData] = []
            for date, op, cl, hi, lo, vol in rows[-count:]:
                result.append(KLineData(
                    datetime=date,
                    open=op,
                    high=hi,
                    low=lo,
                    close=cl,
                    amount=vol,
                ))
            return result
        except Exception as e:
            import logging
            logging.warning(f"[AkshareDataSource] get_index_kline failed: {e}")
            return []

    # ---------- A 股搜索 ----------

    @staticmethod
    def _add_market_prefix(raw_code: str) -> str:
        """根据 6 位代码补 sh/sz 前缀。A 股规则：
        - 6/9 开头 → 上交所 (sh)
        - 0/3 开头 → 深交所 (sz)
        - 4/8 开头 → 北交所 (bj)
        """
        if raw_code.startswith(('6', '9')):
            return f"sh{raw_code}"
        if raw_code.startswith(('0', '3')):
            return f"sz{raw_code}"
        if raw_code.startswith(('4', '8')):
            return f"bj{raw_code}"
        return f"sh{raw_code}"

    @staticmethod
    def _query_eastmoney_suggest(query: str, count: int = 30) -> List[Dict[str, str]]:
        """调用东方财富 suggest 接口（GBK 编码）按代码或名称搜索。
        返回仅含沪深 A 股（沪A/深A）的标准化字典列表。
        """
        import json
        import logging
        import urllib.parse
        import urllib.request

        q = (query or '').strip()
        if not q:
            return []

        url = (
            "https://searchadapter.eastmoney.com/api/suggest/get"
            f"?input={urllib.parse.quote(q)}&type=14&count={max(count, 5)}"
        )
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://quote.eastmoney.com/',
        })
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                # 响应 UTF-8（部分接口历史返回 GBK，但当前 suggest 端点是 UTF-8）
                body = resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            logging.warning(f"[AkshareDataSource] _query_eastmoney_suggest failed: {e}")
            return []
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            logging.warning(f"[AkshareDataSource] suggest JSON decode failed: {e}")
            return []
        table = (payload.get('QuotationCodeTable') or {})
        rows = table.get('Data') or []
        result: List[Dict[str, str]] = []
        for row in rows:
            classify = str(row.get('Classify') or '')
            # 只保留沪深 A 股，排除港股/债券/指数/基金/期货
            if classify != 'AStock':
                continue
            raw = str(row.get('Code') or '').strip()
            name = str(row.get('Name') or '').strip()
            if len(raw) != 6 or not raw.isdigit():
                continue
            if not name:
                continue
            result.append({
                'code': AkshareDataSource._add_market_prefix(raw),
                'raw_code': raw,
                'name': name,
            })
            if len(result) >= count:
                break
        return result

    def search_stocks(self, query: str, limit: int = 30) -> List[Dict[str, str]]:
        """按代码或名称搜索 A 股。空查询返回空列表。"""
        return self._query_eastmoney_suggest(query, count=limit)


# ========================== 通达信 tqcenter ==========================

class TqcenterDataSource(DataSource):
    """通达信客户端(tqcenter)数据源，依赖 Tdx 客户端启动 Python。"""

    _initialized = False
    _tq = None

    MARKET_SH = 0
    MARKET_SZ = 1

    _fields_quote = ['Open', 'Close', 'High', 'Low']
    _fields_kline = ['Open', 'Close', 'High', 'Low', 'Datetime', 'Amount']
    _period_map = {4: '1d', 5: '1w', 6: '1M'}

    @classmethod
    def _ensure_init(cls):
        if not cls._initialized:
            try:
                from tqcenter import tq
                tq.initialize(__file__)
                cls._tq = tq
                cls._initialized = True
                import logging
                logging.warning("[TqcenterDataSource] initialized successfully")
            except Exception as e:
                import logging
                logging.warning(f"[TqcenterDataSource] init failed: {e}")
                cls._initialized = True
                cls._tq = None

    def _get_tq(self):
        self._ensure_init()
        return self._tq

    def _market_code(self, market: int, code: str) -> str:
        suffix = ".SH" if market == self.MARKET_SH else ".SZ"
        return f"{code}{suffix}"

    def get_index_quote(self, market: int = 0, code: str = "000001") -> Optional[QuoteData]:
        try:
            tq = self._get_tq()
            if tq is None:
                return None
            from datetime import date
            today = date.today().strftime('%Y%m%d')
            stock_code = self._market_code(market, code)
            df = tq.get_market_data(
                field_list=self._fields_quote,
                stock_list=[stock_code],
                start_time=today,
                end_time=today,
                period='1d',
            )
            if df is None or df.empty:
                return None
            row = df.iloc[-1]
            return QuoteData(
                open=float(row['Open']),
                close=float(row['Close']),
                high=float(row['High']),
                low=float(row['Low']),
            )
        except Exception as e:
            import logging
            logging.warning(f"[TqcenterDataSource] get_index_quote failed: {e}")
            return None

    def get_index_kline(self, market: int = 0, code: str = "000001",
                        category: int = 4, count: int = 200) -> List[KLineData]:
        try:
            tq = self._get_tq()
            if tq is None:
                return []
            stock_code = self._market_code(market, code)
            period = self._period_map.get(category, '1d')
            df = tq.get_market_data(
                field_list=self._fields_kline,
                stock_list=[stock_code],
                start_time='',
                end_time='',
                period=period,
            )
            if df is None or df.empty:
                return []
            df = df.tail(count)
            return [
                KLineData(
                    datetime=str(row['Datetime']),
                    open=float(row['Open']),
                    high=float(row['High']),
                    low=float(row['Low']),
                    close=float(row['Close']),
                    amount=float(row.get('Amount', 0)),
                )
                for _, row in df.iterrows()
            ]
        except Exception:
            return []

    def search_stocks(self, query: str, limit: int = 30) -> List[Dict[str, str]]:
        # Tqcenter 暂无简易搜索入口，回退到 akshare 缓存
        return AkshareDataSource().search_stocks(query, limit)


# ========================== Pytdx ==========================

class PytdxDataSource(DataSource):
    """pytdx 直连通达信服务器数据源（备用）。"""

    def __init__(self, ip: str = "119.97.133.154", port: int = 7709):
        self.ip = ip
        self.port = port
        self._api = None

    def _connect(self):
        from pytdx.hq import TdxHq_API
        if self._api is None:
            self._api = TdxHq_API()
            self._api.connect(self.ip, self.port)

    def _disconnect(self):
        if self._api is not None:
            try:
                self._api.disconnect()
            finally:
                self._api = None

    def get_index_quote(self, market: int = 0, code: str = "000001") -> Optional[QuoteData]:
        try:
            from pytdx.params import TDXParams
            self._connect()
            bars = self._api.get_security_bars(
                TDXParams.KLINE_TYPE_DAILY, market, code, 0, 1
            )
            if not bars:
                return None
            d = bars[-1]
            return QuoteData(
                open=float(d["open"]),
                close=float(d["close"]),
                high=float(d["high"]),
                low=float(d["low"]),
            )
        except Exception as e:
            import logging
            logging.warning(f"[PytdxDataSource] get_index_quote failed: {e}")
            return None
        finally:
            self._disconnect()

    def search_stocks(self, query: str, limit: int = 30) -> List[Dict[str, str]]:
        # Pytdx 暂无简易搜索入口，回退到 akshare 缓存
        return AkshareDataSource().search_stocks(query, limit)

    def get_index_kline(self, market: int = 0, code: str = "000001",
                        category: int = 4, count: int = 200) -> List[KLineData]:
        try:
            from pytdx.params import TDXParams
            self._connect()
            data = self._api.get_security_bars(category, market, code, 0, count)
            return [
                KLineData(
                    datetime=str(d["datetime"]),
                    open=float(d["open"]),
                    high=float(d["high"]),
                    low=float(d["low"]),
                    close=float(d["close"]),
                    amount=float(d.get("amount", 0)),
                )
                for d in (data or [])
            ]
        except Exception as e:
            import logging
            logging.warning(f"[PytdxDataSource] get_index_kline failed: {e}")
            return []
        finally:
            self._disconnect()


# ========================== 工厂 + 单例 ==========================

class DataSourceFactory:
    """数据源工厂：注册 / 创建 / 列表。"""

    _registry: Dict[str, Type[DataSource]] = {
        'akshare': AkshareDataSource,
        'tqcenter': TqcenterDataSource,
        'pytdx': PytdxDataSource,
    }

    @classmethod
    def register(cls, name: str, source_class: Type[DataSource]) -> None:
        cls._registry[name] = source_class

    @classmethod
    def create(cls, name: str = 'akshare', **kwargs) -> DataSource:
        if name not in cls._registry:
            raise ValueError(
                f"Unknown data source: {name!r}. "
                f"Available: {cls.list_available()}"
            )
        return cls._registry[name](**kwargs)

    @classmethod
    def list_available(cls) -> List[str]:
        return list(cls._registry.keys())


_default_source: Optional[DataSource] = None


def get_data_source(name: str = 'akshare', **kwargs) -> DataSource:
    """创建指定数据源（同时设为默认）。"""
    global _default_source
    _default_source = DataSourceFactory.create(name, **kwargs)
    return _default_source


def get_default_source() -> DataSource:
    """获取默认数据源（首次为 akshare）。"""
    global _default_source
    if _default_source is None:
        _default_source = DataSourceFactory.create('akshare')
    return _default_source