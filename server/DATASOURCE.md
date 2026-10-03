# 数据源切换说明

## 架构

```
DataSource (抽象基类)
    ├── PytdxDataSource     # 通达信（默认）
    ├── AkshareDataSource   # AKShare
    └── EfinanceDataSource  # EFinance
```

所有数据源实现统一接口，输出标准化格式，无需关心上游数据差异。

## API 切换

在请求后端接口时通过 `source` 参数指定数据源：

| 数据源 | source 值 | 示例 |
|--------|-----------|------|
| 通达信 | `pytdx` | `GET /api/index/kline?category=4&source=pytdx` |
| AKShare | `akshare` | `GET /api/index/kline?category=4&source=akshare` |
| EFinance | `efinance` | `GET /api/index/kline?category=4&source=efinance` |

```bash
# 查看当前K线（默认pytdx）
curl "http://localhost:8310/api/index/kline?category=4&count=10"

# 切换到akshare
curl "http://localhost:8310/api/index/kline?category=4&source=akshare"

# 切换到efinance
curl "http://localhost:8310/api/index/kline?source=efinance"

# 查看所有可用数据源
curl "http://localhost:8310/api/datasource/list"
```

## 代码层面使用

```python
from server.data_source import DataSourceFactory, get_data_source

# 创建指定数据源
ds = DataSourceFactory.create('akshare')
kline = ds.get_index_kline(category=4, count=100)

# 切换全局默认数据源（后续 API 调用自动使用）
from server.data_source import set_data_source
set_data_source(DataSourceFactory.create('efinance'))
```

## 注册新数据源

```python
from server.data_source import DataSourceFactory, DataSource

class MyDataSource(DataSource):
    def get_index_quote(self, market=1, code="000001"):
        ...

    def get_index_kline(self, market=1, code="000001", category=4, count=100):
        ...

DataSourceFactory.register('mysource', MyDataSource)
ds = DataSourceFactory.create('mysource')
```

## 安装数据源依赖

```bash
# AKShare
pip install akshare

# EFinance
pip install efinance
```

## K线周期 category 参数

| category | 含义 |
|----------|------|
| 4 | 日K |
| 5 | 周K |
| 6 | 月K |
| 8 | 1分钟 |
| 9 | 1分钟 |
| 10 | 5分钟 |
| 11 | 15分钟 |
| 12 | 30分钟 |
| 13 | 1小时 |