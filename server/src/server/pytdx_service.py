from pytdx.hq import TdxHq_API
from pytdx.params import TDXParams


class PytdxService:
    def __init__(self, ip: str = "119.97.133.154", port: int = 7709):
        self.ip = ip
        self.port = port
        self._api = None

    def connect(self):
        self._api = TdxHq_API()
        self._api.connect(self.ip, self.port)
        return self

    def disconnect(self):
        if self._api:
            self._api.disconnect()
            self._api = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *args):
        self.disconnect()

    def get_index_quote(self, market: int = 1, code: str = "000001"):
        """获取上证指数实时行情"""
        with self:
            # 获取K线数据取最新一根来拼实时行情
            bars = self._api.get_security_bars(
                TDXParams.KLINE_TYPE_DAILY, market, code, 0, 1
            )
        if bars and len(bars) > 0:
            d = bars[-1]
            return {
                "open": d["open"],
                "close": d["close"],
                "high": d["high"],
                "low": d["low"],
                "amount": d.get("amount", 0),
            }
        return None

    def get_index_kline(self, market: int = 1, code: str = "000001",
                        category: int = TDXParams.KLINE_TYPE_DAILY,
                        count: int = 100) -> list:
        """获取上证指数K线数据"""
        with self:
            data = self._api.get_security_bars(category, market, code, 0, count)
        return [
            {
                "datetime": d["datetime"],
                "open": d["open"],
                "high": d["high"],
                "low": d["low"],
                "close": d["close"],
                "amount": d.get("amount", 0),
            }
            for d in (data or [])
        ]


service = PytdxService()