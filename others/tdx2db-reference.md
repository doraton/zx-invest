# tdx2db 参考

> 记录日期：2026-10-05
> 仓库：[xbfighting/tdx2db](https://github.com/xbfighting/tdx2db) ★163 · MIT · Python
> 仓库规模：40 个文件 · 创建 2025-06-13 · 最近推送 2026-09-25（活跃）
> 关联：[pytdx-reference.md](pytdx-reference.md) ·
> [tx-reference.md](tx-reference.md) ·
> [architecture.md §3.4](../docs/architecture.md) 对照实验

---

## 0. 一句话定位

`tdx2db` 把本地通达信 `vipdoc`（日/分钟线）和 `T0002/hq_cache/`
（板块/股本/名称）整合成一套**自包含的本地数据库管道**——
4 张表（daily / minute5/15/30/60 / stock_info / block_stock_relation），
Python 直装直跑，MySQL/PostgreSQL/SQLite 都支持。
**不复权**，设计决策，永不改变。

---

## 1. 这是什么

定位「把通达信本地数据变成你自己的 SQL 数据库资产」——
读本地 `vipdoc` 文件（`.day` 日线 / `.lc1`.lc5` 分钟线）
+ `T0002/hq_cache/` 下的板块与股本文件，增量写入 PostgreSQL / MySQL / SQLite。

四层管道，单向数据流：`reader` → `processor` → `storage` + `blocks`。

| 表 | 内容 | 唯一约束 |
|----|------|---------|
| `daily_data` | 日线 OHLCV + 均线列 | (code, date) |
| `minute5/15/30/60_data` | 分钟线（15/30/60 由 5 分钟重采样） | (code, datetime) |
| `stock_info` | 真实名称 + 总股本/流通A股 + 上市日期 | code |
| `block_stock_relation` | 板块-个股关系（行业/概念/指数/地区/风格/特殊） | (block_type, block_name, code) |

收录：深市 `000/001/002/300/301`，沪市 `60xxxx/688xxx`。
不含北交所、ETF、指数。
**不复权**，作者把它列为设计决策、永不改变（见 [issue #2](https://github.com/xbfighting/tdx2db/issues/2)）。

---

## 2. 优点

### 2.1 自带板块关系 —— 这是它最大的长板

它解决了**所有网络源都补不了的板块数据缺口**：
行业（881 研究行业，一/二/三级）/ 概念 / 指数 / 地区 / 风格 / 特殊，
全量快照，来自通达信本地的板块文件。

而且作者在 `blocks.py` 注释里直接给出了根因——

> 旧版通达信的二进制 `block_zs/gn/fg.dat` 在新版已不存在，
> pytdx BlockReader 不适用。

也就是说 pytdx 的 `get_block_info` 为什么返回空，并不是节点不行，
而是**新版 TDX 把那些老文件去掉了**。

它改读这些文件，**本机全部齐全**（2026-10-05 验证）：

| 文件 | 大小 | 用途 |
|------|------|------|
| `T0002/hq_cache/tdxhy.cfg` | 150,550 | 个股 → 行业码 |
| `T0002/hq_cache/tdxzs3.cfg` | 32,730 | 行业名，类别 12 = 881 研究行业（多级前缀匹配，一/二/三级各一行） |
| `T0002/hq_cache/tdxzs.cfg` | 18,272 | 地区名，类别 3 |
| `T0002/hq_cache/base.dbf` | 3,986,880 | 地区码 `DY` 字段 + 总股本/流通A股 |
| `T0002/hq_cache/infoharbor_block.dat` | 741,518 | 概念 / 指数 / 风格（GBK 文本） |
| `T0002/hq_cache/spblock.dat` | 317,152 | 中证500/1000 等跨市场指数成分（**仅此处有**） |
| `T0002/hq_cache/infoharbor_ex.code` | 182,465 | 真实股票名称 |

### 2.2 流通股本 `ltag` —— 换手率的分母

`base.dbf` 的 `LTAG` 字段（万股，随盘后数据更新）。
FAQ 给了换手率公式：`成交量 / 流通A股`（注意万股→股的单位换算）。

### 2.3 第三个股票名称源 —— 可用于交叉校验

`infoharbor_ex.code` 是我们已知的**第三个**本地名称来源
（另两个是从 `shs.tnf`/`szs.tnf` 解析出来的）。

按「两个源一致不能证明数据是对的」的原则，
`infoharbor_ex.code` × `shs.tnf` 可以直接做一次独立源互校——
两者都是通达信自己写的，但路径与生成时机不同。

### 2.4 增量同步工程化做得好

- 逐股票**精确增量**（不是全量重写）
- 唯一约束内建；老库需跑一次迁移脚本
  - PostgreSQL：缺约束 → 增量写入**全部失败**
  - MySQL/SQLite：缺约束 → **静默累积重复数据**
- `status` 命令自检 + warnings 提示衍生表覆盖缺口
- **同步完整性校验**：当日行数与前一交易日对比，偏差超 5% 即判定同步不完整
  （作者原话：`MAX(date)` 是今天**不代表全市场都进来了**）

### 2.5 均线预计算

`ma5/ma10/ma60/ma250` + 斐波那契窗口 `ma13/ma21/ma34/ma55/ma89/ma144/ma233`（服务缠论分析）。
上市不足窗口天数时为 NULL。

### 2.6 附带的文档纪律值得学

仓库自带 `AGENTS.md`（schema、典型查询、**陷阱清单**）和
Claude Code skill（`.claude/skills/tdx2db-query/`），
让任何 AI agent 在仓库目录下都能直接正确查库、无需人工解释。

### 2.7 `blocks.py` 本身：240 行、零新增依赖

```python
# tdx2db/blocks.py  240 行
import struct
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import pandas as pd
from .logger import logger
```

MIT 协议、240 行、只依赖标准库 + pandas。
可以**作为参考实现移植**到任何项目里。

### 2.8 活跃且规范

- 创建 2025-06-13，最近推送 2026-09-25（活跃）
- GitHub Actions CI（`python-package.yml`）
- 测试覆盖 reader/processor/storage/cli/config/blocks

---

## 3. 缺点 / 已知局限

### 3.1 必须本地装通达信，且要「盘后数据下载」

代码完全依赖**本机**通达信客户端。`reader.py` 用 `pytdx.reader` 模块
读 `vipdoc/`，文件不存在就读不到。

而且 FAQ 直说：「**TDX 默认只缓存你看过的股票**，必须先在 TDX 里执行
『盘后数据下载』才能用」——这是个**手动依赖**，服务器/容器化部署
就是个硬约束。

### 3.2 历史深度有限 —— 99% 的 `.day` 只到 2020-01

本机实测扫描：

| 指标 | 值 |
|------|-----|
| `.day` 文件数 | 9467（接近全市场） |
| 起始于 2020 年及以后 | **9369（99.0%）** |
| 起始于 2020 年之前 | 98（1.0%） |

也就是说「覆盖面几乎全市场，但历史只有 6.5 年」。
要补 2010–2019 的历史得另找源。

### 3.3 不复权

作者明确「不复权，且默认口径不会改变（设计决策，见 issue #2）」。
复权请在消费端用涨跌幅复权 factor 法自行处理
（AGENTS.md 写了完整公式）。

### 3.4 不收录指数 / ETF / 北交所

当前 A 股筛选规则只收深市 `000/001/002/300/301` 和沪市 `60/688`。

### 3.5 跨表 code 格式不一致（已知陷阱）

作者自己在 AGENTS.md 里写了：

> **`stock_info.code` 带市场前缀（`sz000001`），行情表是 6 位纯数字（`000001`）。**
> 用 `LIKE 'sh688%'` 查 `daily_data` 会静默零匹配。
> 跨表 JOIN 需 `RIGHT(stock_info.code, 6)`

也就是说这个坑**作者知道、有文档**，但对没读文档的人来说仍然隐蔽。

### 3.6 MySQL 唯一约束缺失会静默累积重复数据

AGENTS.md 警告：

> **MySQL/SQLite：缺约束 → 静默累积重复数据**。
> v0.2.0 之前建的表没有约束的，必须执行一次迁移脚本。

PG 上缺约束会直接报错，反而更安全；
MySQL/SQLite 上缺约束**不报错**，这就是为什么这种 bug 在 MySQL
项目里特别难发现。

### 3.7 板块表每次同步全量替换，无历史版本

> 每次同步为全量替换快照（无历史版本）

如果需要追溯板块关系变化（比如某只股票从「沪深300」被剔除），
**当前版本不保留这种历史**。

### 3.8 停牌日处理有坑

> 停牌日必须严格 `date = X` 等值匹配：想"取不到就用前一天"时
> 要显式写出回退逻辑，`<=` 取最末行会静默错配到停牌前一天。

### 3.9 同步不完整会静默算出子集

> `MAX(date)` 已是今天不代表全市场都进来了
> 当日行数与前一交易日对比，偏差超 5% 说明同步不完整

作者给了校验 SQL，但需要自己跑。**同步脚本本身不告警**。

---

## 4. 本机依赖文件清单

| 文件 | 大小 | 用途 |
|------|------|------|
| `T0002/hq_cache/infoharbor_block.dat` | 741,518 | 概念/指数/风格 |
| `T0002/hq_cache/tdxhy.cfg` | 150,550 | 个股→行业码 |
| `T0002/hq_cache/tdxzs3.cfg` | 32,730 | 881 研究行业名 |
| `T0002/hq_cache/tdxzs.cfg` | 18,272 | 地区名 |
| `T0002/hq_cache/spblock.dat` | 317,152 | 跨市场指数成分 |
| `T0002/hq_cache/base.dbf` | 3,986,880 | 地区码 + 总股本/流通A股 |
| `T0002/hq_cache/infoharbor_ex.code` | 182,465 | 真实股票名称 |
| `vipdoc/sh/lday/*.day` | 9.5GB+ | 沪市日线 |
| `vipdoc/sz/lday/*.day` | 9.0GB+ | 深市日线 |

7/7 `T0002/hq_cache/` 文件本机齐全（2026-10-05 验证）。

---

## 5. 复现命令

```powershell
cd D:\repo\03_pers-proj\zx-stock\server

# 参考性安装（不引入作为依赖）：
pip install tdx2db
# 或 poetry add tdx2db

# 环境配置（.env）
TDX_PATH=D:\prod-forces\finance\tdx
DB_URL=mysql+aiomysql://root:pass@127.0.0.1:3306/mysqldb
# 或 postgresql+psycopg / sqlite

# 日线增量同步
tdx2db daily --db-only --auto-start --incremental

# 分钟线增量
tdx2db minutes --db-only --auto-start --incremental

# 板块数据（仅 DB 模式，不读 TDX）
tdx2db blocks --db-only

# 状态检查
tdx2db status --json
```

---

## 6. 同类项目横向对比（tdx2db 自带）

| 项目 | 数据来源 | 产出形态 | 状态 |
|------|----------|---------|------|
| **tdx2db** | 本地 vipdoc 文件 | PostgreSQL / MySQL / SQLite（增量同步） | 活跃 |
| [easy_tdx](https://github.com/handsomejustin/easy_tdx) | 通达信协议在线直连 | DataFrame / JSON / REST（含指标、回测） | 活跃 |
| [tdx-api](https://github.com/oficcejo/tdx-api) | 通达信协议在线直连 | Docker 部署的 REST 实时接口 | 活跃 |
| [rustdx](https://github.com/zjp-CN/rustdx) | 本地 `.day` + 东方财富 | CSV / ClickHouse / MongoDB（含复权因子） | 低频维护 |
| [mootdx](https://github.com/mootdx/mootdx) / [pytdx](https://github.com/rainx/pytdx) | 本地文件 + 在线协议 | DataFrame 读取库 | **已停更** |

补充实测（2026-10-05 通过 GitHub API 核对）：

- `rainx/pytdx` ★1551 · 最后 push **2020-04-15** · 仓库已 **archived**
- `mootdx/mootdx` ★2406 · 最后 push 2024-07-16
- `zjp-CN/rustdx` ★278 · 最后 push 2026-04-08
- `xbfighting/tdx2db` ★163 · 最后 push 2026-09-25

**没有一家拿 pytdx 当主源。**

---

## 7. 复权方案（顺带记录）

tdx2db 与 rustdx 两家独立采用同一口径，写在 tdx2db 的 `AGENTS.md`「需要复权时」一节：

1. 从除权除息信息算出除权日调整后的 **preclose**
2. **`factor = 每日 (close / preclose) 累乘`**；
   增量维护时 `新 factor = 昨日 factor × (今收 / 今前收)`，**永不重算历史**
3. `前复权价 = factor × (最新 close / 最新 factor)**

注意：该算法与券商软件的前复权存在小的口径/浮点差异
（rustdx issue #32/#37 有讨论），做形态对比时留意；做收益率计算完全够用。

---

## 8. 一句话复盘

tdx2db 是**唯一一个自带的板块和股本数据的本地化方案**，
不需要任何外部网络源，MIT / 活跃 / 文档全。
但它**强依赖本地通达信客户端**，且**历史只到 2020 年**——
这两条与「做长周期量化」这个需求正交。

把它作为整库使用或者只移植 `blocks.py`，都是合理的姿势；
**哪种姿势合不合适，要看当下项目的形态，不是 tdx2db 本身的问题。**
