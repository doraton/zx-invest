# 数据库表结构说明

> ← 返回 [架构说明](architecture.md)
>
> 记录日期：2026-10-09
> 工作库：**`zx_invest`**（9 张表，原名 `tsdb`，2026-10-09 改名）
> 关联：[architecture.md](architecture.md)（主架构）· [tushare-reference.md](tushare-reference.md)（主数据源）

本文说明 **`zx_invest`** 每张表是干什么的、数据填到什么程度、为什么留或不留。
字段设计的来龙去脉见本文 §7（自 `architecture.md §5` 迁入）。

> 建表 DDL 的**唯一真相源**是 `server/src/server/db.py` 的 `SCHEMA_STATEMENTS`。
> 本文的表结构若与 `SHOW FULL COLUMNS` 不一致，以库为准、以 DDL 为准，并回来修本文。

---

## 0. 库的定位

主数据源是 **Tushare**，`zx_invest` 是唯一承载实盘数据的库。

当前积分 170，能稳定批量调用的接口只有 `daily`，其余接口要么受限（1~5 次/天）要么无权限。
这个约束直接决定了下文每一张表的填充进度。

---

## 1. 十一张表速查

> 行数为 2026-10-14 实测快照（`SHOW TABLES` + `COUNT(*)`）。

| 表 | 行数 | 列数 | 职责 | 状态 |
|---|---|---|---|---|
| `kline_day` | 5435 | 13 | **日线，唯一真相源** | ✅ 全历史已灌满 |
| `kline_period` | 1542 | 13 | 周/月/季/年线（物化） | ✅ 已物化 |
| `kline_0amv` | 8220 | 10 | 指南针 0AMV 日线 | ✅ 独立 DDL，列齐备 |
| `app_config` | 3 | 3 | 应用级设置（key-value） | ✅ 前端显示类设置持久化 |
| `index_info` | 6 | 8 | 指数 / 指标**名录** | ⚠️ 只有名录，无行情表 |
| `stock_info` | 5225 | 11 | 股票主档 | ✅ 已灌全市场 |
| `stock_xdxr` | 47 | 13 | 除权除息事件 | ✅ `SH.600570` 47 行 |
| `stock_finance` | 0 | 21 | 财务快照 | ❌ 空，权限不足 |
| `sync_state` | 1 | 7 | 增量水位 | ✅ 无孤儿 |
| `watch_group` | 1 | 4 | 自选分组 | ✅ 只有兜底组 `default` |
| `watch_item` | 0 | 3 | 自选条目 ↔ 分组 | ✅ 表已建，尚未加自选 |

**列注释（COMMENT）覆盖实测**：`kline_day` 6/13、`kline_0amv` 4/10、`kline_period` **1/14**，
`watch_group` / `watch_item` **全部齐备**，其余 6 张为 **0**。
`kline_day` 的 `open/high/low/close/pre_close/chg_amt/chg_pct` 六列无注释，
`kline_period` 只有 `code` 一列有 —— 见 §6 欠账。

### 1.1 当前数据分布

| 内容 | 现状 |
|---|---|
| `kline_day` | `SH.600570` **5435 行**（2003-12-16 ~ 2026-10-12）—— 全历史，唯一在用的 code |
| `kline_period` | `SH.600570` week 1150 / month 275 / quarter 93 / year 24，合计 1542 |
| `kline_0amv` | `0AMV` **8220 行**（1993-01-03 ~ 2026-10-09）；源文件 `day.vdat` 已到 2026-10-12，待跑 §3 增量 |
| `stock_info` | 5225 行，沪主板 `600/601/603/605`、科创板 `688`、深主板 `000/001/002/003`、创业板 `300/301` |
| `stock_xdxr` | 47 行，其中 **23 条**对应真正的除权除息（其余为股本变化等中间态，见 [adjust-factor-sources.md](adjust-factor-sources.md) §3.2） |
| `index_info` | 4 个通达信指数（`SH.000001` / `SZ.399001` / `SZ.399006` / `SH.000688`）+ 2 个自研（`0AMV` / **`MOM`**，大写） |

### 1.2 `code` 格式已收口（2026-10-09）

**全库唯一形态：`SH.600570`（大写 + 点 + 6 位）**，定义在后端
`server/external/tdx_kline.canonical_code`，前端 `web/src/utils/indices.ts` 的
`canonicalCode()` 是它的同构实现。**两边必须一致**，任何新增写入方都走这个函数。

| 表 | 形态 | 行数 |
|---|---|---|
| `kline_day` / `sync_state` / `stock_info` / `index_info`（4 个通达信指数） | **`SH.600570`** | 1 + 1 + 5225 + 4 |
| `index_info` 的自研指标 | **`0AMV` / `MOM`**（无交易所前缀，**大写**，不参与该约定） | 2 |
| `kline_0amv` | **`0AMV`**（同上） | 8220 |

`canonical_code` 认得三种等价写法并归一：`sh600570` / `SH.600570` / `600570.SH`。
**认不出来的一律原样返回**，不做任何改写——`mom` 一旦被顺手 `.upper()` 成 `MOM`，
`index_info` 里就再也查不到了。

收口前是两个写入方各造各的（`sync_tushare.py` 写 `SH.600570`，
`backfill_stock_universe.py` 写 `sh600570`），「谁最后跑谁说了算」，
导致 `kline_day` 和 `stock_info` 的 JOIN 长期接不上、行情列表里名称恒为空。
现在两边都走 `canonical_code`。

前端 `INDEX_CODE_RE` 写成 `/^(sh|sz)\.?(000|399)\d{3}$/i`，**点可有可无**，
这样存量自选里的老 code（`sh000001`）不会被误标成「个股行情」。

---

## 2. K 线：日线一张 + 派生周期一张

```
kline_day     日线，唯一真相源，来自 Tushare
kline_period  week / month / quarter / year 合并，用 period 列区分
```

### 2.1 为什么不拆 5 张表


合并理由：week / month / quarter / year 是**同层派生物**——都直接来自 day，彼此之间没有派生关系。
用 period 列正交化，一张表就够，四张表只是把同一个维度拆成了四份元数据。

但**不能把 day 也混进去**：真相源和缓存必须物理隔离，
`kline_day` 丢了要重拉全历史（数年数据），`kline_period` 随时能重算。

### 2.2 `kline_day` 的 13 列

| 列 | 类型 | 含义 |
|---|---|---|
| `code` | `VARCHAR(16)` | 标的代码 |
| `trade_date` | `DATE` | 交易日 |
| `open` `high` `low` `close` | `DECIMAL(12,2)` | **不复权**原始价 |
| `pre_close` | `DECIMAL(12,2)` NULL | **已除权**的前收盘 |
| `chg_amt` | `DECIMAL(12,2)` NULL | 涨跌额 ≡ `close - pre_close`（按**已舍入**的两列现算） |
| `chg_pct` | `DECIMAL(10,2)` NULL | 涨跌幅 %，基准是 `pre_close` |
| `volume` | `DECIMAL(20,2)` | **手**（1 手 = 100 股），2 位小数 |
| `amount` | `DECIMAL(20,3)` | **千元**（1 千元 = 1000 元），3 位小数 = 精确到 1 元 |
| `ah_vol` | `DECIMAL(20,2)` **NULL** | 盘后固定成交量，**手**；`NULL` = 当日无盘后交易 |
| `ah_amt` | `DECIMAL(20,3)` **NULL** | 盘后固定成交额，**千元**，精确到 1 元；`NULL` = 当日无盘后交易 |

列顺序**严格对齐 Tushare `pro.daily` 的输出字段序**，方便逐字段对账。
三处改名：`ts_code → code`、`change → chg_amt`（`change` 是 MySQL 保留字）、
`pct_chg → chg_pct`；两个涨跌列统一 `chg_` 前缀。

```sql
PRIMARY KEY (code, trade_date)   -- 无二级索引，见 §2.4
```

### 2.3 `kline_period` 的 14 列

比日线表多一个 `period` 列，其余 13 列**与 `kline_day` 逐列同名同序同精度**。
2026-10-13 起 `chg_amt` / `ah_vol` / `ah_amt` 三列才真正**存在并有值**——
此前 `pre_close` / `chg_pct` 是「DDL 里有、从无写入」的两列死列，
后三列则压根没建；同时精度从 4 位收窄到与日线一致。

| 列 | 类型 | 含义 |
|---|---|---|
| `period` | `VARCHAR(8)` | week / month / quarter / year |
| `trade_date` | `DATE` | 该周期**最后一个交易日**（`close` 对应那天） |
| `open` `high` `low` `close` | `DECIMAL(12,2)` | **不复权**原始价，由日线聚合 |
| `pre_close` | `DECIMAL(12,2)` NULL | **上一周期收盘**（原始值），即上一根 bar 的 `close`；第一个周期 NULL |
| `chg_amt` | `DECIMAL(12,2)` NULL | 涨跌额 ≡ `close - pre_close` |
| `chg_pct` | `DECIMAL(10,2)` NULL | 涨跌幅 %，基准是 `pre_close` |
| `volume` `amount` | `DECIMAL(20,2)` / `(20,3)` | 手 / 千元，周期内求和 |
| `ah_vol` `ah_amt` | `DECIMAL(20,2)` / `(20,3)` **NULL** | 盘后量额，周期内求和；**桶内全无盘后交易时为 NULL**（**只写不读**） |

```sql
PRIMARY KEY (code, period, trade_date)
```

`period` 进主键的原因：同一交易日在 week / month / quarter / year 四个桶里
**各有一根 bar**，不带 period 会互相覆盖。

**涨跌三列是原始口径**，与本表 OHLC 同源（2026-10-13 从「除权调整口径」改回）。
跨除权的周期会显示**原始跳空**（恒生电子 2007 年线 +259.87%），这是对的：
`adj=none` 下图上画的就是原始价，两者必须对得上。要看连续涨幅切前复权——
那条路在 `market._ADJ_AGGREGATE_BARS` 现算，不读本表。

> ⚠️ 曾经存过「除权调整后的上一周期收盘」（`close[上桶末]×f[上桶末]/f[本桶末]`），
> 折算本身没错（2021-07 真实涨幅 -16.29 就算对了），**但存的位置错了**：
> 那个口径和本表的原始 OHLC 不是一份东西，而 `adj=none` 时接口原样下发这两列，
> 同一根 K 线会「图 +259.87%、数字 +369.75%」，年线上差 110 个百分点。
> 教训：验证不能拿自己定义的参照物当标准——当时正是拿「复权后真实涨幅」
> 验「复权后真实涨幅」，1150 根全过。

⚠️ **两张表的 `pre_close` 语义不同**，别当成同一个东西：

| | 含义 | 可推导？ |
|---|---|---|
| `kline_day.pre_close` | **已除权**的昨收（官方口径） | ❌ 拿不到，复权因子就靠它算 |
| `kline_period.pre_close` | **原始**的上一周期收盘 | ✅ 就是上一根 `close` |

### 2.4 为什么没有二级索引

InnoDB 主键即**聚簇索引**，物理顺序就是 `(code, trade_date)`。
MySQL 8 对升序聚簇索引支持**反向扫描**，`ORDER BY trade_date DESC` 不需要额外索引。

实测（8.0.46）三类查询全部命中 PRIMARY：

| 查询 | Extra |
|---|---|
| `ORDER BY trade_date DESC LIMIT n` | `Backward index scan` |
| `ORDER BY trade_date ASC LIMIT n` | — |
| `trade_date BETWEEN ...` | `Using where` |

旧的 `idx_code_period_date` **一次都没被选中**，等于白付存储 + 每次插入多维护一棵 B+ 树，已去掉。

### 2.5 `kline_0amv` 的结构（已收口）

这张表**不再复用 `_KLINE_DDL`**，改用独立的 `_KLINE_0AMV_DDL`（`db.py` 里单列），
建表语句由 `SCHEMA_STATEMENTS` 一次性注册。

实测 `SHOW CREATE TABLE kline_0amv`：**10 列** = `code, trade_date, open, high, low, close,
chg_amt, chg_pct, volume, amount`。

| 差异点 | 说明 |
|---|---|
| `chg_amt` | **已存在**（`decimal(12,2) NULL`），8219/8220 行有值，仅首根 1993-01-03 为 NULL |
| `ah_vol` / `ah_amt` | **不补，也不该补**——指南针 0AMV 没有「盘后固定交易」这回事 |
| 列注释 | 已有 4 列（`code` / `trade_date` / `volume` / `amount`） |
| `volume` / `amount` | 是 `BIGINT`（不是日线的 `DECIMAL(20,2)` / `(20,3)`），COMMENT「单位：点」——**量纲与 K 线表不同，勿互相参照** |

⚠️ 早先这里记的是「复用 `_KLINE_DDL`、缺 3 列、182 行数据」。
那是 0AMV 接入早期（复用 DDL 时代）的状态，**库和代码都已不再如此**，本节于 2026-10-13 重写。

### 2.6 `pre_close` 为什么必须随 OHLC 一起落库

它是「已除权」的前收盘，是自算复权因子（`close × adj_factor / 最新 adj_factor`）
和筹码分布（三角形分布衰减法的输入）的关键。

`adj_factor` 受 Tushare 频次限制拉不全（全市场需 83 小时以上，不可行），
一旦丢了 `pre_close` 只能重拉全历史才能补，属于**不可逆的数据损失**。

**写入一律用 `COALESCE(VALUES(x), x)`**：本次没带值时保留库中原值，不覆盖成 NULL。

---

## 3. 其余表

### 3.1 `stock_info` — 股票主档 ✅ 5225 行

一行一只股票，是 `/api/stock/search` 的**唯一数据来源**
（搜索按 `raw_code` / `code` / `name` / `pinyin` 四路 LIKE）。

| 列 | 含义 | 来源 |
|---|---|---|
| `code` | `SH.600570` | 全库唯一形态，5225 行已于 2026-10-09 由 `sh600570` 迁过来（见 §1.2） |
| `raw_code` | `600570` | — |
| `market` | 0=深 1=沪 | — |
| `name` | 证券简称 | 本地通达信 `T0002/hq_cache/{shs,szs}.tnf` |
| `pinyin` | 拼音首字母 | pypinyin + NFKC 归一 |
| `ipo_date` / `total_share` / `float_share` | 上市日期 / 总股本 / 流通股本 | 待从 `tdx2db` 搬 |

`name` / `pinyin` 已零缺失。**证券简称没有网络来源**（pytdx 的 `get_security_list`
在所有可用节点都返回 `None`），只能读通达信客户端的名称缓存，解析细节见 §7.3.1。

`ipo_date` / 股本 / 流通股本仍可**零 API 成本**从 `tdx2db.stock_info` 搬过来
（5229 只的 `list_date` / 总股本 / 流通股本，离线即可）。

### 3.2 `stock_xdxr` — 除权除息事件 ✅ 已填 47 行

```sql
PRIMARY KEY (code, ex_date, category)
```

- `category` 在主键里，MySQL 要求主键列一律 `NOT NULL`，缺失时用 `0` 占位
- 比例字段量纲统一为「**每 10 股**」；`cash_dividend` 是**元/股**
- 取数状态（2026-10-13 实测）：`bonus_ratio` / `allot_ratio` / `cash_dividend`
  各 **24 行**有值；**前后股本 4 列也有 24 行真实值**
  （如 2026-09-30 `share_before=189364.7969 → share_after=189662.5469`）；
  `reduce_ratio` 47 行全 NULL、`allot_price` 24 行为 `0.0000` / 23 行为 NULL
- 数据源是 Tushare `dividend`（**只按 `ex_date` 非空过滤，其余全插**），
  所以表里混着「股本变化」等非除权除息的中间态——**47 行里只有 23 条是真除权断点**
  （`category` 分布 `1→24 / 2→7 / 5→15 / 9→1`），见 [adjust-factor-sources.md](adjust-factor-sources.md) §3.2

**三个用途**：① 校验 `kline_day.pre_close` 是否被上游正确除权（**已在用**）
② 自算复权因子的交叉验证 ③ 筹码分布中送转那天的成本调整。

### 3.3 `sync_state` — 增量水位 ⚠️

```sql
PRIMARY KEY (code, period)
```

K 线虽然合并成两张表，这张状态表**仍以 `(code, period)` 为联合主键**：
状态本来就按周期分行，而且 `market.available_codes()` 要拿它做 JOIN。

`first_date` / `last_date` 取自对应周期表的聚合（覆盖**全部**历史），不是本次拉取窗口——
否则增量同步会把区间越收越窄。写 `running` / `failed` 时用 `COALESCE` 保留旧值。

✅ 当前 **1 行、无孤儿**：`SH.600570` / `day` / `last_date = 2026-10-12` / `status = ok`。
早先那条 `sh600570` 孤儿（指向的 K 线行已随清空消失，会让增量同步误判成
「已同步到更早的日期」）已不在表里——**不是某次收口删的**，那次实测就只有 1 行。

### 3.4 `stock_finance` — 财务快照 ❌ 空表

```sql
PRIMARY KEY (code, report_date)
```

21 列，三张财务报表压平成「一行一个报告期」：资产负债 7 / 损益 6 / 现金流 2
+ 存货应收等 4 + `shareholder_count` / `per_share_net_asset`。

**短期不会有数据**：已决定暂不需要，且 Tushare 170 积分调不了
`income` / `balancesheet` / `cashflow` / `fina_indicator`。

### 3.5 `index_info` — 指标名录 ⚠️ 只有名录

8 列：`code` / `name` / `pinyin` / `value_unit` / `data_shape` / `enabled` / `remark` / `updated_at`。
存的是**指标定义**，**不是行情数据**。真正的指数日线表 `index_daily` 在 `index_repo.py`
里有数据层代码，但**表从未被创建**，属于悬空实现。

**2026-10-09 精简**（库与 `db.py` DDL 同批变更，一条原子 `ALTER`）：

| 变更 | 列 | 原因 |
|---|---|---|
| 删除 | `short_name` | 简称只有两处要，都不在这张表：前端 `web/src/utils/indices.ts` 自带一份 `short`，API 也不吐 `short`。库里存一份必然和前端漂移。 |
| 改名 | `full_name` → `name` | 原名跟 `stock_info.name` 不一致，而这两张表本来就是成对的（都是名录语义）。 |
| 删除 | `source` | 纯字符串标识，无外键、无代码读取。出处信息并进了 `remark`。 |
| 删除 | `sort_order` | 展示顺序是前端的事（`INDICES` 数组已定死顺序）；留在库里就得有人维护，而 `seed_indexes()` 每次跑都会覆写它。 |

两处可见副作用：

1. **行情列表页指数行的名称从简称变成全称**（`market.py` 的 `idx` CTE 原先取
   `short_name AS name`，现取 `name`）——「活跃市值」→「活跃市值指数」，「宝妈」→「宝妈指数」。
2. **指数之间的排序退化成按 `code`**（原 `sort_order` 为 10/20/30/40/50/60）。
   `/api/stock/list` 现在返回 `0AMV → MOM → SH.000001 → SH.000688 → SZ.399001 → SZ.399006`，
   上证不再排第一。要恢复原顺序得在前端排序，或把 `sort_order` 加回来。

### 3.6 `app_config` — 应用级设置 ✅

```sql
PRIMARY KEY (item_key)
-- item_key VARCHAR(64) / item_value VARCHAR(255) NOT NULL
-- item_name VARCHAR(128) NULL / updated_at TIMESTAMP（DEFAULT CURRENT_TIMESTAMP ON UPDATE）
```

一张 key-value 表，给前端的**显示类设置**做持久化（localStorage 会在换浏览器/清缓存时丢，
两处真相又会漂，所以统一落库）。

**读写方**：`server/src/server/app_config.py`，接口 `GET /api/config` / `PUT /api/config`。

| 键 | 含义 |
|---|---|
| `show_price_line` | 主图现价线显不显示（bool） |
| `top_ratio` | 主图占 K 线区的比例（float，范围 0.25~0.65，默认 0.42） |
| `right_ratio` | 右列占行宽的比例（float，范围 0.15~0.45，默认 0.225） |
| `chart_period` | K 线周期（**`PERIOD_OPTIONS` 的下标**，字符串 `"0"`~`"4"`，默认 `"0"` 日线）—— 2026-10-14 加 |
| `chart_adjust` | 复权口径（`none` / `qfq` / `hfq`，默认 `none`）—— 2026-10-14 加 |

`item_name` 列（2026-10-14 加）记的是「这个设置项是什么意思」，给人看的——
直接 `SELECT *` 时不用翻代码就知道 `chart_period=3` 是季线。

**这一列的真相源是 `app_config.py` 的 `ITEM_NAMES` 字典**，库里存的是它的投影：
`upsert_all` 随值一起写，`sync_item_names()` 在启动时幂等回填（只有与现值不同才 UPDATE）。
新增设置项**必须**在 `ITEM_NAMES` 登记一行，否则那行的 `item_name` 是 NULL；
手工改这一列会被下次启动覆盖回去。库里存在但没登记的键保持 NULL——是未知设置项，
留 NULL 比编一个假说明诚实。

⚠️ 两条约定：`GET` **只返回库里真实存在的键**，没配过的直接不出现在返回值里，
由前端用自己的默认值兜底（后端再存一份默认值就成了两处真相）；
`PUT` 只 upsert 传进来的键，**不删**没传的。前端 Slider 因此必须拆
`onChange`（本地预览）/ `onChangeComplete`（落库），否则一次拖动会发几十个 PUT。

**`chart_period` 存下标而不是 `period.type`**：月线与季线的 `type` **都是 `'month'`**
（季线靠 `span: 3` 区分，见 `web/src/utils/chartOptions.ts`），存 type 会把季线读成月线。
另注意 `_parse` 会把 `"0"` 这种字符串还原成**整数**，所以前端 `periodFromConfig`
number / string 两种都得认。

⚠️ 这两个键与 `top_ratio` / `right_ratio` 不同：比例只影响布局，而**周期与复权决定取什么数据**。
所以 `MarketChart` 的 chart 初始化 effect 必须等 `fetchConfig` 回来才放行
（`configLoaded` state 门闩），否则会先按默认的「日线 + 不复权」发一次 500 根请求、
再按库里口径重取一次——多一次请求，还闪一帧错口径，正好抵消持久化的意义。

### 3.7 `watch_group` + `watch_item` — 自选股 ✅ 2026-10-14 新增

```sql
CREATE TABLE watch_group (
    id, label, created_at DATETIME(3), updated_at DATETIME(3),
    PRIMARY KEY (id)
);
CREATE TABLE watch_item (
    code, group_id, added_at DATETIME(3),
    PRIMARY KEY (code, group_id),
    KEY idx_group (group_id, code)
);
```

自选此前整个存在浏览器 `localStorage`（`zx-stock:watchlist` / `zx-stock:watch-groups`），
2026-10-14 起落库。理由与 `app_config` 同源：**两处真相迟早漂移**。
旧的 localStorage 数据**未迁移**（用户决定从空库开始），两个 key 已不再读写。

**读写方**：`server/src/server/watchlist.py`，
接口 `GET /api/watchlist`、`POST /api/watchlist/items`、
`DELETE /api/watchlist/items/{code}?group=`、`POST /api/watchlist/groups`。

#### 为什么只有两张，没有「自选本体」表

自选条目**自身不带任何数据**：

- **`name` 不落库** —— 统一从 `/api/stock/list`（`stock_info` ∪ `index_info` 一条 SQL）
  解析，见 `WatchlistPage` 的 dbNames 回填。落第二份只会重新制造名称漂移。
  这不是洁癖：`indices.ts` 与 `WatchlistPage` 的历史注释都记着「自选里的票名字变成一串代码」
  这次事故，成因正是 name 快照被冻在 localStorage 里。
- **`addedAt` 无处可存** —— 全项目没有任何 UI 读它（`grep web/src` 只命中
  `utils/watchlist.ts` 内部的写入与迁移逻辑）。已从 `WatchItem` 类型删除。

于是「票还在但不属于任何组」这种孤儿状态在数据上**无法表示**：
删完某个组的关联行，票自然消失，不需要额外判断「要不要连本体一起删」。
这正是省掉第三张表的收益。

#### 为什么不能用 `stock_info` 顶替

自选里可以有 `stock_info` 里**根本没有行**的标的。实测（2026-10-14）：

```
index_info 的 6 个 code 在 stock_info 里查 name  → 全部 NULL
stock_info 里名字含「指数」/「成指」的行数          → 0 行
stock_info 的 code 前缀                            → SZ 2906 + SH 2319（清一色个股）
```

`0AMV` / `MOM` 都在 `index_info` 而不在 `stock_info`，且 `0AMV` **确实被加进过自选**
（`WatchlistPage.tsx` 留着案底）。拿 `stock_info` 当条目表，这两行就加不进去。

#### 三个易踩的点

| 点 | 原因 |
|---|---|
| 主键是 `(code, group_id)` | 兼做幂等：重复加同一组走 `INSERT IGNORE` 返回 0，不报错也不插第二行 |
| `created_at` / `added_at` 都是 `DATETIME(3)` | MySQL 的 `datetime` 不写小数秒只有**秒**精度，同一秒建的两个分组排序会跳 |
| 列表顺序 = **首次**加入时间 | 一只票在 N 个组就有 N 个 `added_at`，取 MIN 才对。`watchlist.list_all` 不用 GROUP BY，直接 `ORDER BY added_at, code` 后在 Python 里「首次遇到即定序」，`code` 是并列时的确定性兜底 |

`default`（「其他」）是唯一内置分组，由 `ensure_default_group()` 在**启动时**幂等补齐，
`create_group` 不会覆盖它；`GET` 恒把它排在末尾，前端 `getAllGroups()` 是同一条规则的第二道保险。

同名分组自动去重成「名称 (2)」这条规则已从前端 `createUserGroup` **挪到后端**
（只有后端能看见全部分组，前端看到的可能还没加载完）。分组 id 也改由后端生成。

前端 `web/src/utils/watchlist.ts` 是个内存 store，**所有导出保持同步签名不变**
（只有 `createUserGroup` 变成 async），所以 `WatchlistPage` / `ChartHeader`
的读取方式不用动。写入一律「乐观更新 + 失败回滚」，应用启动时 `initWatchlist()` 拉一次。

---

## 4. 已删除的表

### `kline_backup` — 2026-10-07 删除

**代码里从未往它写过数据**，是一张空壳。

设计意图见 `verify_query_paths.py`：它要验证「读库路径」与「回退聚合路径」口径一致，
做法是临时删掉某周期的最后一根物化行、比对、再放回去。`db.py` 的注释声称这行
「先挪到 kline_backup，比对完再删」，但**代码从来没这么做过**：

- `verify_query_paths.py` 的 `INSERT` 目标写的是 `kline` 本身，不是 `kline_backup`
  （该行末尾的 `# 备份到 kline_backup` 注释与 SQL 不符）
- 恢复用的是删除前抓在内存里的 `last` 字典，不是备份表
- 那条恒删 0 行的 `DELETE FROM kline_backup` 也已随脚本重写移除

⚠️ **2026-10-13 更正**：早先这里写「`verify_query_paths.py:36` / `:58`」「全项目引用点仅四处
（`db.py` 建表、`show_db.py` 计数、`analyze_tsdb.py` 表名清单、那句 DELETE）」——
该脚本已于 2026-10-13 重写，`grep kline_backup` 在 `server/` 下**只剩 `db.py` 的注释**，
`show_db.py` 也不含它。结论不变（表确已删除，删表不影响脚本行为），只是取证细节失效了。

### `ref_industry` / `ref_province` — 2026-10-07 删除

已从库中 `DROP TABLE`，同时从 `db.py::SCHEMA_STATEMENTS` 移除 DDL，
`stock_repo.py::upsert_ref` 改为抛 `NotImplementedError`
（保留函数体，让历史调用方得到明确报错而不是静默失效）。

**删除理由**（三条，逐层递进）：

1. **零价值**：从未写入过一行数据；没有任何 API 读取；唯一写入函数 `upsert_ref` 从未被调用。
2. **设计前提已失效**：原设计是 `get_block_info` 可用时的「编码 → 名称」映射表，
   但该接口已确认不可用，表一直建不起来也填不满。
3. **结构根本不承载真实语义**：这两张表是 `code INT PK` 的一对一映射，
   而板块数据实际是「板块 ↔ 个股多对多关系」。一只股票属于多个板块，塞不进一对一映射表。

### `tsdb` — 2026-10-09 整库改名

MySQL 8.0 **不支持 `RENAME DATABASE`**（该语法 5.7 已移除），
实际做法是：`CREATE DATABASE zx_invest` → 逐表 `CREATE TABLE ... LIKE` + `INSERT ... SELECT`
→ 8 张表行数逐一比对通过 → 才 `DROP DATABASE tsdb`。

---

## 5. 跨表约定（接新数据源时必须遵守）

### 5.1 K 线表名经 `db.kline_table()` 解析

```python
from server import db
db.kline_table("day")    # -> "kline_day"
db.kline_table("week")   # -> "kline_period"
```

**所有 SQL 都必须经它取表名**，不要在别处拼 `kline_` 前缀。
传入未知周期会抛 `ValueError`。

### 5.2 `volume` 全库统一为「手」

1 手 = 100 股。Tushare 原生就是手，**原样落库不换算**；
通达信日线返回**股**，需 ÷100（通达信**非日线返回手**，所以 `tdx_kline._to_rows`
按 period 分别处理，不能一刀切）。

**任何新数据源的入口，第一件事就是确认并换算单位。**

### 5.3 `amount` 全库统一为「千元」

1 千元 = 1000 元。Tushare 原生就是千元，原样落库；
通达信返回**元**，需 ÷1000；腾讯不提供成交额，一律记 0（不反推——那是编数据）。

### 5.4 `code` 形态

| 场景 | 形态 | 例 |
|---|---|---|
| 数据库 / 后端内部 | **统一**：`SH.600570`（§1.2 的 `canonical_code`） | `SH.600570` |
| Tushare 接口 | 数字在前、大写后缀（`canonical_code` 认得并归一） | `600570.SH` |
| 通达信 `.tnf` | 小写无点（`canonical_code` 认得并归一） | `sh600570` |
| 前端展示 | 大写带点（`formatCode` 兜底，存储已是该形态） | `SH.600570` |

⚠️ 跨库导入时注意：外部 `tdx2db` 库内部 `stock_info.code` 带前缀而 `daily_data.code` 不带。

### 5.5 `pre_close` 是「已除权」的

不是「昨天收盘价」。上游数据源给的就是除权后的值，**直接落库，不要自己再除一次**。

### 5.6 缺失值分两类，不能混

| 列 | NaN 时 |
|---|---|
| `pre_close` / `chg_amt` / `chg_pct` | 写 `NULL`，且用 `COALESCE(VALUES(x), x)` 保留旧值 |
| `ah_vol` / `ah_amt` | 写 **`NULL`**（列可空，「没有盘后交易」与「成交为零」语义不同） |

判空要用 `r.get(x) == r.get(x)` 这种 NaN 自比较，**不能写 `v or 0`**——
`bool(nan)` 是 True，`nan or 0` 会把 NaN 原样传下去，pymysql 直接抛
`nan can not be used with MySQL`。

---

## 6. 当前欠账

按优先级排：

| 优先级 | 事项 | 成本 |
|---|---|---|
| ~~高~~ | ~~`code` 格式收口~~ ✅ 2026-10-09 完成（`stock_info` 5225 行 + `index_info` 4 个通达信指数已迁到 `SH.xxxxxx`） | 已完成 |
| ~~高~~ | ~~清掉 `sync_state` 里 `sh600570` 那条孤儿~~ ✅ 表里已无此行（实测 1 行，非本次删除） | 已完成 |
| **高** | `stock_info` 从 `tdx2db` 补 `ipo_date` / 股本 / 流通股本 | 零 API |
| 高 | 修 `sync_market.py`，让批量拉取也写 `sync_state` | 零 API |
| ~~中~~ | ~~`kline_period` 重新物化~~ ✅ 2026-10-13 完成（week 1150 / month 275 / quarter 93 / year 24） | 已完成 |
| ~~中~~ | ~~`kline_0amv` 补 `chg_amt` / `ah_vol` / `ah_amt` 三列 + 注释~~ ✅ 2026-10-13 作废：`chg_amt` 早已在库里（8219/8220 行有值），`ah_*` 是**设计上不该有**的（0AMV 无盘后固定交易），注释也已补 4 列 | 已作废 |
| 中 | `kline_period` 的 `ah_vol` / `ah_amt` 目前**只写不读**，出「盘后占比」时再下发 | 零 API |
| 中 | `stock_xdxr` 的 `reduce_ratio` / `allot_price` 补齐（现为全 NULL / 0.0000） | 不占 Tushare 配额 |
| 中 | 补列注释：`kline_day` 缺 6 列、`kline_period` 缺 13 列、`kline_0amv` 缺 6 列、其余 6 张表为 0 | ALTER，零成本 |
| 低 | `stock_finance` 解锁（需购 Tushare 高积分档） | 付费 |
| 低 | `index_daily` 要么建表要么删掉悬空实现 | — |
| 低 | `kline_0amv` 跑 §3 的每日增量（源文件 `day.vdat` 已到 2026-10-12，库只到 10-09，差 1 根） | 零 API |

---

## 7. 表设计决策存档

> 本节自 `architecture.md §5` 迁入，记录**为什么这么设计**，不是当前结构的速查。

### 7.1 完整 DDL

见 `server/src/server/db.py`（`SCHEMA_STATEMENTS` 为唯一真相源）。核心两张：

```sql
CREATE TABLE kline_day (
    code        VARCHAR(16)  NOT NULL           COMMENT '标的代码，大写带市场前缀，SH.600570',
    trade_date  DATE         NOT NULL           COMMENT '交易日',
    open        DECIMAL(12,2) NOT NULL           COMMENT '开盘价，不复权',
    high        DECIMAL(12,2) NOT NULL           COMMENT '最高价，不复权',
    low         DECIMAL(12,2) NOT NULL           COMMENT '最低价，不复权',
    close       DECIMAL(12,2) NOT NULL           COMMENT '收盘价，不复权',
    pre_close   DECIMAL(12,2) NULL               COMMENT '已除权的前收盘，复权因子与筹码分布的关键输入',
    chg_amt     DECIMAL(12,2) NULL               COMMENT '涨跌额，等于 close - pre_close',
    chg_pct     DECIMAL(10,2) NULL               COMMENT '涨跌幅 %，基准是 pre_close',
    volume      DECIMAL(20,2) NOT NULL DEFAULT 0 COMMENT '成交量，单位手（1 手 = 100 股），2 位小数',
    amount      DECIMAL(20,3) NOT NULL DEFAULT 0 COMMENT '成交额，单位千元（1 千元 = 1000 元），3 位小数 = 精确到 1 元',
    ah_vol      DECIMAL(20,2) NULL               COMMENT '盘后固定成交量，单位手（1 手 = 100 股）；NULL = 当日无盘后交易',
    ah_amt      DECIMAL(20,3) NULL               COMMENT '盘后固定成交额，单位千元（1 千元 = 1000 元）；NULL = 当日无盘后交易',
    PRIMARY KEY (code, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

> `kline_day` 的 COMMENT 里 `code` 写的是「小写带市场前缀」，那是迁入时保留的原文，
> 与 §1.2 的现状（`SH.600570`）不符，改格式时一并修掉。

```sql
CREATE TABLE kline_period (
    code        VARCHAR(16)  NOT NULL           COMMENT '标的代码，大写带市场前缀，SH.600570',
    period      VARCHAR(8)   NOT NULL           COMMENT '派生周期：week / month / quarter / year',
    trade_date  DATE         NOT NULL           COMMENT '该周期最后一个交易日',
    open        DECIMAL(12,2) NOT NULL           COMMENT '周期首个交易日的开盘价',
    high        DECIMAL(12,2) NOT NULL           COMMENT '周期内最高价',
    low         DECIMAL(12,2) NOT NULL           COMMENT '周期内最低价',
    close       DECIMAL(12,2) NOT NULL           COMMENT '周期最后一个交易日的收盘价',
    pre_close   DECIMAL(12,2) NULL               COMMENT '上一周期收盘（未复权原始值）；第一个周期为 NULL',
    chg_amt     DECIMAL(12,2) NULL               COMMENT '涨跌额，close - pre_close；第一个周期为 NULL',
    chg_pct     DECIMAL(10,2) NULL               COMMENT '涨跌幅 %，基准是 pre_close；第一个周期为 NULL',
    volume      DECIMAL(20,2) NOT NULL DEFAULT 0 COMMENT '成交量，单位手，周期内求和',
    amount      DECIMAL(20,3) NOT NULL DEFAULT 0 COMMENT '成交额，单位千元，周期内求和',
    ah_vol      DECIMAL(20,2) NULL               COMMENT '盘后固定成交量，单位手，周期内求和；NULL = 该周期内无盘后交易（目前只写不读）',
    ah_amt      DECIMAL(20,3) NULL               COMMENT '盘后固定成交额，单位千元，周期内求和；NULL = 该周期内无盘后交易（目前只写不读）',
    PRIMARY KEY (code, period, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**列序与 `kline_day` 逐列对齐**（`code` 之后插 `period`）：新增列一律用
`ALTER TABLE ... ADD COLUMN ... AFTER <前驱>` 插到指定位置，
不能用默认的追加到表尾，否则 `INSERT ... SELECT` 的列对应关系会静默错位。

⚠️ **上面这段是目标 DDL，库内尚未完全同步**（2026-10-13 实测）：

1. 库里 `kline_period` **只有 `code` 一列带 COMMENT**，其余 13 列（含 `period` 与全部价格/量额列）**都没有注释**。
2. `db.py` 里 `pre_close` 的 DDL 注释仍写「**除权调整后的**上一周期收盘」，
   与同文件 §2.3 的说明、与 `kline_repo` 的 `LAG(close)` 实际实现、与库内数据
   （1538/1542 行 `pre_close` 恰等于上一根 `close`，4 个首根 NULL）**三处都相反**。
   现存库因 `CREATE TABLE IF NOT EXISTS` 不会改动，所以现在看不出来，
   但**新建库会把错误注释固化下来**。建新库前应先订正 `db.py` 这一行。

### 7.2 表设计：周/月/季/年线

#### 决策：物化落库，日线为唯一真相源

| 方案 | 优点 | 缺点 | 取舍 |
|---|---|---|---|
| A. 只存日线，查询时聚合 | 单一数据源，天然无口径冲突 | 每次周 K 查询要扫全量日线 | 保留为**回退路径** |
| **B. 全部物化落库** | 查询只扫目标周期 | 需保持物化产物同步 | **当前采用** |
| C. 直接存通达信原生周/月/季/年线 | 不用自己算 | `volume` 单位与日线不同（手 vs 股） | 不采用 |

物化与原生的等价性已实测（`others/probe_categories.py`）：
OHLC 24/24 吻合，`amount` 1.0000 吻合，`volume` 按 100 倍换算后吻合。

查询侧保留 A 作为回退：库内没有物化行时 `market.query_klines`
自动用窗口函数现场聚合，两条路径口径已验证一致（`tools/verify_query_paths.py`）。

#### 落库形态

`trade_date` 统一取「周期**最后一个交易日**」——`close` 对应的那天，语义最自然，
且与 pytdx 原生周/月/季/年线的返回日期一致。
注意早期实现曾用 `MIN(trade_date)`（周期首个交易日），物化时必须统一改掉。

⚠️ **正因取桶尾，物化必须「先删后插」，不能增量 upsert。**
本周每多一个交易日，桶尾就变一次（周一 → 周二 → … → 周五），
而主键是 `(code, period, trade_date)`，桶尾一换就成了新主键 ——
upsert 会把每一版都留成独立行，一天一行、一周五行，
接口于是返回同一周的五根假 K 线。
`materialize_periods_on()` 因此先 `DELETE` 掉该周期的全部行，再从日线全量重建：
1542 行的量级是毫秒级，换来「表里永远是当前桶集合」这条不用维护的保证。
真相源是日线，删掉的每一行都会原样重建。

> 2026-10-14 实测：插入同周下一根日线后，week 桶尾由 `2026-10-12` 挪到 `2026-10-13`，
> 行数仍是 1150；再插入下周一那根才 +1 → 1151。

#### 物化 SQL

`kline_repo.materialize_periods()` 用**一条 SQL** 完成一个周期，两层嵌套：

```sql
INSERT INTO kline_period (code, period, trade_date, open, high, low, close,
                          pre_close, chg_amt, chg_pct, volume, amount, ah_vol, ah_amt)
SELECT code, %(period)s, trade_date, open, high, low, close,
       pre_close,
       ROUND(close - pre_close, 2),
       ROUND((close / pre_close - 1) * 100, 2),
       volume, amount, ah_vol, ah_amt
FROM (
    SELECT b.*, LAG(close) OVER (ORDER BY trade_date) AS pre_close   -- 原始口径
    FROM (
        SELECT DISTINCT code, MAX(trade_date) OVER w AS trade_date,
               FIRST_VALUE(open) OVER w AS open, MAX(high) OVER w AS high,
               MIN(low) OVER w AS low, LAST_VALUE(close) OVER w AS close,
               SUM(volume) OVER w AS volume, SUM(amount) OVER w AS amount,
               SUM(ah_vol) OVER w AS ah_vol, SUM(ah_amt) OVER w AS ah_amt
        FROM (SELECT *, {分桶键} AS pk FROM kline_day WHERE code=%(code)s) g
        WINDOW w AS (PARTITION BY pk ORDER BY trade_date
                     ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
    ) b
) x
ON DUPLICATE KEY UPDATE ...
```

`open` 取 `FIRST_VALUE`（**不能 `MIN(open)`**，那会取到周期内最低开盘价），
`close` 取 `LAST_VALUE`；窗口必须显式写
`ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING`，
否则 `LAST_VALUE` 默认只到当前行，close 会全错。

四个必须记住的点：

1. **两层不能压平**。`pre_close` 的 `LAG` 要拿**上一个桶**的 close，
   桶内窗口看不到邻居，压平了 `pre_close` 整列会丢。
2. **桶级那层必须带 `DISTINCT`**。窗口函数按整帧算出来、桶内每行取值相同，
   不去重的话 N 行会进到外层 `LAG`，同桶内互相成为「上一桶」，
   `pre_close` 变成自己的 close、`chg_pct` 恒为 0。
3. **一律用具名占位符**（`%(code)s` / `%(period)s`）。位置占位符按**在 SQL 文本里
   出现的先后**绑定而非子句顺序，写反了**不报错**，只会静默返回空集。
4. **`chg_amt` / `chg_pct` 按已舍入的 `close` / `pre_close` 现算**——
   `round(a,2) - round(b,2) != round(a - b, 2)`。

`SUM(ah_vol)` 的 NULL 语义是**自动**的：桶内日线全是 NULL 时 `SUM` 返回 NULL，
部分有值时跳过 NULL 求和。所以「该周期有没有盘后交易」不需要额外判据，
**前提是日线那两列已经用 NULL 而不是 0 表示「没有」**。

> ⚠️ **MySQL `ROUND` 与 Python `round` 的约定不同**：MySQL 的 DECIMAL ROUND
> 是四舍五入**远离零**，Python 内置 `round()` 是 banker's rounding。
> 精确值 `-11.625` 时 MySQL 给 `-11.63`、Python 给 `-11.62`（实测 2008-06-13
> 那周）。写校验脚本时必须用 `Decimal.quantize(ROUND_HALF_UP)` 对齐，
> 否则会把对的判成错。

> 这份 SQL 是**唯一一份**：`materialize_periods` 与 `tools/sync_tushare.py`
> 都经 `kline_repo.materialize_periods_on()` 取（`_materialize_sql()` 拼装）。
> 早先那两处各抄了一份，只改了一边的 chg 计算时极难发现。

#### 周期边界

| 周期 | 分桶键 | 说明 |
|---|---|---|
| 周 | ISO 周 `%x-W%v` | 周一为一周之首 |
| 月 | `%Y-%m` | 自然月 |
| 季 | `%Y-Q[1-4]` | 1-3 / 4-6 / 7-9 / 10-12 月 |
| 年 | `%Y` | 自然年 |

#### 一致性维护

日线只做 upsert 不删除，因此**受影响周期集合可精确计算**：
某日被写入后，仅需重算它所属的那一个周 / 月 / 季 / 年。

```
写日线 → 收集写入日期涉及的周期键 → 逐个重算 → upsert 派生周期
```

另需 `rebuild_derived(code, period)` 全量重算接口，用于纠偏与首次全量。
**规则：日线永远是真相源，任何时候都可以从日线重建全部派生周期。**

### 7.3 表设计：基本面

`get_finance_info` 一次返回 37 个字段，但它们**分属两类语义**，必须拆表：

- 证券主数据（变动极少）：股本、上市日期、行业 / 省份码
- 财务快照（每报告期变）：资产负债、利润表

拆成 `stock_info`（1 行 / 标的）与 `stock_finance`（多行 / 标的，PK 预留 `report_date`）。
字段名已从 pytdx 的拼音（`jingzichan` / `zhuyingshouru`…）改为可读英文。

#### 7.3.1 证券简称：本地通达信 `.tnf`

`stock.name` 是本项目唯一**没有网络来源**的字段——pytdx 全部接口都不返回名称。
唯一可用的来源是通达信客户端自己的名称缓存：

```
T0002/hq_cache/shs.tnf    沪市，约 9.9 MB
T0002/hq_cache/szs.tnf    深市，约 8.8 MB
```

**记录布局**（按 6 位数字代码定位，逆向自实测）：

```
<6 字节 ASCII 数字代码><若干 NUL 填充><GBK 名称><NUL>
```

填充长度未文档化，实测两个文件里统一是 **25 字节**。实现里不写死：
向前最多跳 48 个 NUL 再取名，换 TDX 版本不会立刻失效。

**三个必须处理的字符坑**（都是实测踩到的）：

| 代码 | 名称 | 坑 |
|---|---|---|
| `000002` | `万 科Ａ` | 末字是**全角** `Ａ`（U+FF21），不是 ASCII `A` |
| `000858` | `五 粮 液` | GBK 字节里嵌了 ASCII 空格 `0x20` |
| ST / *ST | — | 含 ASCII 字母与星号 |

所以字符集校验必须同时放行 CJK、ASCII 字母、**全角字母**、星号和空格，
并在解码后 `re.sub(r"\s+", "", name)` 去掉内嵌空格。

**同码不同义**：同一个 6 位代码在两个文件里可能指向不同标的——
`000002` 在 `shs.tnf` 里是「上证A股指数」，在 `szs.tnf` 里是「万科A」。
因此**按市场分别建表、不做跨市场合并**，否则 sz000002 会被写成指数名。

实测规模：沪 4670 条 + 深 4226 条 = 8896 条（含指数、基金、债券，不只 A 股）。
读一次约 1–2 s，故 `tdx_local.load_name_map()` 按目录路径做模块级缓存。

回填入口：`tools/backfill_names.py`（默认只填空值，`--overwrite` 才覆盖）。

### 7.4 基本面的三个已知缺口

| 缺口 | 现状 | 方案 |
|---|---|---|
| **证券名称** | ✅ 已解决 | 见 §7.3.1 |
| **行业 / 省份名称** | 只拿到编码（industry=24、province=28…），`get_block_info` 返回空无法翻译 | 板块数据实为多对多关系，`ref_*` 一对一表承载不了，已删；待重新设计 |
| **历史报告期** | `get_finance_info(market, code)` 无日期参数，只能取最新一期 | PK 已含 `report_date`，将来找到历史接口直接写入即可 |

### 7.5 16 个公司资料板块不落库

`get_company_info_category` 单板块最长 38 万字，全 A 全量入库体积大且低频。
做成 `/api/stock/info?code=&section=` 按需查询 + 短 TTL 缓存更合适。

### 7.6 入库顺序建议

1. `stock_info` 主表（一次 `get_finance_info` 同时拿到股本 / 上市日期 / 行业码）
2. `stock_finance`（同一次调用的其余字段）
3. `stock_xdxr`（另一次调用）
4. 名称用 `tools/backfill_names.py` 从本地 `.tnf` 回填（见 §7.3.1）；
   行业 / 省份映射仍**留作独立课题**，不阻塞 1-3