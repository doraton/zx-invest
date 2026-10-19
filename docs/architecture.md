# zx-stock 架构说明

> A 股 K 线查看 + 指南针专有指标（活跃市值 0AMV）看板。
>
> 核心约束：**Windows-only + 依赖客户端**（通达信 / 指南针），无外部公开 API 数据。

**相关文档**

| 文档 | 内容 |
|---|---|
| [tushare-reference.md](tushare-reference.md) | tushare数据接入 |
| [znz-active-mv-ocr-sync.md](znz-active-mv-ocr-sync.md) | 活跃市值数据接入 |
| [database-schema.md](database-schema.md) | 数据库设计 |
| [kline-bar-counts.md](kline-bar-counts.md) | K 线显示设计 |
| [adjustment-principle.md](adjustment-principle.md) | 除权除息原理 |
| [pre-close-display-plan.md](pre-close-display-plan.md) | 浮窗昨收口径 |
| [adjust-factor-sources.md](adjust-factor-sources.md) | 复权因子来源 |
| [formulas.md](formulas.md) | 公式计算 |
| [TODO.md](TODO.md) | 待办 |

**其他文档**

| 文档 | 内容 |
|---|---|
| [pytdx-reference.md](../others/pytdx-reference.md) | pytdx数据接入 |
| [tdx2db-reference.md](../others/tdx2db-reference.md) | tdx2db数据接入 |
| [tx-reference.md](../others/tx-reference.md) | 腾讯自选股数据接入 |

---

## 0. 进度快照

| 模块 | 状态 |
|------|------|
| 行情页 / 自选页 K 线 + 筹码分布 | ✅ 已完成（首页的行情图已于 2026-10-08 按要求移除，首页保留为空白页与侧栏入口） |
| 多页面路由（11 项）+ 左侧导航 | ✅ 已完成 |
| A 股搜索 + 加入分组 | ✅ 已完成（后端待接 ES）|
| **数据源多源 → 单客户端依赖重构** | 🔄 MVP 已通：通达信直连 → MySQL → 读库接口。个股日线全历史 + 周/月/季/年物化 + 基本面 + 除权除息已跑通并验收 |
| 指南针 0AMV 数据接入 | ✅ 本地文件（`day.vdat`）已落地入库 8220 行；OCR 方案仍未实施 |
| 分钟线 / 亮色主题 / 行业板块 | ❌ 未做 |

---

## 1. 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Python 3.11+ / FastAPI / uvicorn / **aiomysql** / **aiohttp** |
| 数据库 | **MySQL 8.0**（K 线 / 股票主数据 / 同步状态） |
| 搜索 | **Elasticsearch 8.x** + IK + pinyin |
| 客户端集成 | pytdx（直连通达信）/ pywinauto（指南针 UI 自动化）/ paddleocr + opencv（0AMV） |
| 前端 | React 19 / Vite 8 / antd 6 / klinecharts 10 / react-router-dom 7 |
| 包管理 | uv（server） / npm（web） |

---

## 2. 系统架构

```
                  Browser (Vite :3201，端口被占时 vite 自动顺延)
                  React + antd + klinecharts
                            │ HTTP
                            ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ FastAPI (uvicorn :3301)                                       │
 │   ├─ /api/index/kline   → MySQL 查询                        │
 │   ├─ /api/stock/search  → MySQL LIKE 查询（ES 未接入）      │
 │   ├─ /api/admin/sync    → 手动触发                          │
 │   └─ lifespan hook → 只建连接池 + 校验表结构                  │
 │      （**不启动任何 ETL / 调度器**，见 §4.1）                 │
 └─────────────────────┬───────────────────────────────────────┘
                       │
        ┌──────────────┼─────────────────┐
        ▼              ▼                 ▼
 ┌──────────┐   ┌──────────────┐   ┌────────────────┐
 │  MySQL   │   │      ES      │   │  同步脚本(CLI) │
 │ kline_day│   │   /stock     │   │  tools/sync_*  │
 │kline_per.│   │  ⚠️ 未接入    │   │  手动跑         │
 │kline_0amv│   │  全项目零依赖 │   │  **无调度器**   │
 │ stock_*  │   └──────────────┘   └────────────────┘
 │app_config│
 └──────────┘
        ▲ ▲
        │ │
        │ └────── 同步脚本（手动 CLI，无定时）──────┐
        │                                       │
        │ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
        │ │ 通达信客户端 │ │ 指南针本地   │ │ 腾讯 HTTP   │
        │ │ (pytdx 直连)│ │ 文件 day.vdat│ │ (K 线 fallback)│
        │ └──────────────┘ │ ✅ 已落地   │ └──────────────┘
        │                  └──────────────┘
        │                        │
        │                        │ 屏幕渲染（OCR 方案，❌ 未实施）
        │                        ▼
        │                 ┌──────────────┐
        │                 │ paddleocr    │
        │                 │ opencv       │
        │                 │ pywinauto    │
        │                 └──────────────┘
        │
        └──── 必须 Windows + 客户端保持登录 + 前台可见 ────┘
```

> ⚠️ 本图 2026-10-13 按代码订正过三处：① `/api/indicator/active_mv` 路由**不存在**（已删）
> ② 调度器（daily 16:30 / weekly 17:00）**从未接入**，`lifespan` 只建连接池；
> ③ ES **未接入**，`/api/stock/search` 是纯 LIKE 实现，全项目零 elasticsearch 依赖。
> 另 0AMV 走的是**指南针本地文件**（`day.vdat`）而非屏幕 OCR。

---

## 3. 数据源策略

**核心原则：客户端依赖，外部 HTTP 源作交叉校验与 fallback。**

> ⚠️ 2026-10-05 修正：本节原表把腾讯写成 fallback 的**唯一**外部域，且备注
> 「腾讯 `qt.gtimg.cn` 实测解析未跑通」。两条都已过时——
> 腾讯接口实测**完全可用**（见 `external/tx_kline.py`），
> 且对照实验证明它**不能**单独承担 K 线主源职责（见 §3.4）。
> 现改为**三源互校**。

| 数据类型 | 主源 | 交叉校验 / fallback | 备注 |
|---------|------|--------|------|
| A 股列表 | 本地通达信 `shs.tnf`/`szs.tnf` | 腾讯快照第 2 段 / 东财 | pytdx `get_security_list` **实测 8/8 节点返回 None**，原表主源不成立 |
| A 股 K 线 | 通达信 pytdx | 东方财富 `push2his` ✅ 已验证一致<br>腾讯 `fqkline` ⚠️ 见 §3.4 | 首次全量 + 每日增量 |
| A 股搜索 | ES 索引（来自 MySQL）| 腾讯 / 东财 HTTP | 拼音 / 中文 / 模糊 |
| 0AMV（活跃市值）| 指南针 OCR 截图 | — | 唯一来源，详细见 `znz-active-mv-ocr-sync.md` |
| 行情（实时 OHLC）| 通达信 pytdx 快照 `get_security_quotes` | 腾讯 `qt.gtimg.cn` ✅ 已跑通 | 已接入，见 `external/tdx_quote.py` / `tx_kline.py` |
| 基本面 | 通达信 pytdx `get_finance_info` | 东财 F10 | 37 字段，仅最新一期，见 §3.3 |
| **复权 K 线** | **腾讯 / 东财 `fqt` 参数原生支持** | — | **pytdx 不支持**，这是腾讯唯一的硬优势 |

### 3.1 pytdx 直连实测（2026-10-05）

> 📄 本节的完整专项档案（含**上游已 archived** 的关键事实、能力矩阵全表、
> 我们做过的改造清单、验收证据、被推翻过的 5 条错误结论、应急预案）见
> [pytdx-reference.md](../others/pytdx-reference.md)。

pytdx 是**个人开源项目**（作者 RainX，https://github.com/rainx/pytdx ），
通达信本身不发布协议文档，pytdx 的报文结构全部靠抓包逆向。
这决定了本节的所有结论都**必须实测，不能照搬**——包括网上流传的「补 4 字节」补丁。

**① 可用服务器。** pytdx 内置 `hq_hosts` 共 104 个，全量扫描后：

| 结果 | 数量 | 说明 |
|------|------|------|
| 连接成功且能返回 K 线 | **8** | 全部是 `117.34.114.13/14/15/16/17/18/20/27:7709`（国泰君安） |
| 连接成功但任何接口都无数据 | 25 | — |
| 连不上（超时/断连） | 71 | — |

8 台可用服务器数据经交叉比对完全一致。清单固化在 `server/src/server/config.py:TDX_HOSTS`。
**指数与个股在这 8 台上的可用性完全相同**，不存在「只服务指数」的限制。

> ⚠️ 曾一度误判「免费节点不提供个股 K 线」，根因见 ②。
> 复现命令：`others/sweep_stock_bars.py`（现已确认 8 台个股全通）。

**② K 线报文尾部有个长度不固定的保留字段，写死任何值都只对一半标的成立。**

记录布局（变长）：

```
date(4)                 日线以上是 uint32 YYYYMMDD；分钟线是 (zipday, tminutes)
open/close/high/low     varint，相对前一记录基址的差值
vol    (4)              自定义浮点编码，pytdx.helper.get_volume 解
amount (4)              同上
extra  (N)              尾部保留字段，**长度因标的而异**
```

实测 `extra` 长度（`others/probe_bar_layout.py`）：

| 标的 | body 长度 | 正确的 extra | 备注 |
|------|-----------|-------------|------|
| sh000001（指数） | 135 | **4** | |
| sh600570（个股） | 96 | **0** | |
| sh600519（个股） | 111 | **0** | |
| sz000001（个股） | 92 | **0** | |

所以：**pytdx 原版（写死 0）只对个股正确，指数逐根错位；
网上流传的「补 4 字节」补丁只对指数正确，个股越界读出垃圾日期。**

**③ 解析异常会被静默吞掉，这是 ② 长期没被发现的原因。**
`get_security_bars` 被 `@update_last_ack_time` 装饰器包着，
`TdxHq_API(...)` 默认 `raise_exception=False` 时它**捕获所有异常并返回 `None`**，
于是在调用方看来「解析器崩溃」和「服务端没数据」完全一样。

本项目的处理（`server/src/server/external/tdx_kline.py`）：

1. 所有连接都用 `TdxHq_API(raise_exception=True)`，失败带原始异常暴露出来；
2. **不写死 extra 长度**，而是按候选值 `(0,4,8,12,16,20)` 逐个试探，
   取第一个满足「能走完 ret_count 根 + **正好消费完整个 body_buf** + 结果合法」的布局；
3. 「结果合法」= 年份在 1990~2100、`high >= low > 0`、时间元组严格单调。
   时间元组带时分，是为了接受分钟线一天多根的情况。

验证：`others/verify_against_local.py`，
4 个标的（1 指数 + 3 个股）× 800 根与本地 `.day` 逐根比对，**全部一致**。

### 3.2 pytdx 数据能力矩阵（实测）

复现命令：`others/probe_categories.py`、`others/recap_capability.py`。
基准为「把本地 `.day` 按同口径聚合」，与各周期原生返回逐根比对。

| 周期 | category | OHLC 吻合 | vol 倍 | amt 倍 | 判定 |
|------|----------|-----------|--------|--------|------|
| 日K | 4 | 24/24 | 1.0000 | 1.0000 | ✅ 完全可用（vol 单位=股） |
| 周K | 5 | 24/24 | **0.0100** | 1.0000 | ⚠️ vol 单位=手 |
| 月K | 6 | 24/24 | **0.0100** | 1.0000 | ⚠️ vol 单位=手 |
| 季K | 10 | 24/24 | **0.0100** | 1.0000 | ⚠️ vol 单位=手 |
| 年K | 11 | 7/7 | **0.0100** | 1.0000 | ⚠️ vol 单位=手 |
| 5/15/30/60/1 分 | 0/1/2/3/8 | 全部正常 | — | — | ✅ 解析正常 |

**非日线的 `vol` 恒为日线的 1/100，且在个股与指数上都是精确的 0.0100。**

这不是「量纲错误 / 数据损坏」，而是**单位差异**：
日线 `vol` 返回**股**，非日线返回**手**（1 手 = 100 股）。
OHLC 与 `amount`（元）在所有周期上都完全正确。

**为什么不直接用原生周/月/季/年线？**
不是因为取不到，而是：同一列 `volume` 会在不同周期混用两种单位
（日线=股、非日线=手），读代码的人无法从字段名看出这个陷阱，必须处处记得乘 100。
而「只拉日线 + SQL 物化」的方案里，`volume` 全库统一为**股**，
口径自洽、增量可精确维护，且不依赖对上游单位约定的记忆。
物化结果与原生比对：OHLC 24/24 吻合，量额按 100 倍换算后吻合（见 [database-schema.md §7.2](database-schema.md)）。

> ⚠️ 拉 `category` 10/11 时 **count 必须给足**（本项目用 400）。count 过小（如 60）时
> 服务器返回稀疏数据、间隔被拉长，看起来像日期乱序，曾被误判为「数据损坏」。

其它数据能力：

| 能力 | 接口 | 实测结果 |
|------|------|----------|
| 财务快照 | `get_finance_info` | ✅ 37 字段，**仅最新一期**（无日期参数） |
| 公司资料 | `get_company_info_category/content` | ⚠️ 板块列表返回 None，内容需逐个试探 |
| 除权除息 | `get_xdxr_info` | ✅ 46 条（sh600570 全历史） |
| 实时快照 | `get_security_quotes` | ✅ 注意会**回显**（见经验 13） |
| 证券列表 | `get_security_list` | ❌ 这 8 个节点均返回 None |
| 行业/省份翻译 | `get_block_info` | ❌ 返回空，只能存裸编码 |
| 筹码原料·逐笔 | `get_transaction_data` | ❌ None（只有分时 240 条） |
| 副图指标 | — | ❌ 无任何 indicator/macd/kdj/boll/rsi 接口 |


### 3.3 「不支持」的准确含义

- **副图指标**：界面上有（VOL/MACD/KDJ/BOLL），但由前端 klinecharts 现算，pytdx 不提供。
- **筹码分布**：界面上有，但是 `ChipDistribution.tsx` 的「成交量在 [low,high] 区间均摊」近似；
  真实成本分布需要逐笔成交 + 换手率衰减，而逐笔接口拿不到。**短期无更好的替代方案。**

### 3.4 pytdx × 腾讯 对照实验（2026-10-05）

**目的**：验证「用腾讯 HTTP 做 fallback」这个架构假设是否成立。

**控制变量做法**：建两个 schema 完全相同的库——`mysqldb`（pytdx 源）与
`tq-stock`（腾讯源）。两库跑**同一份** `kline_repo.upsert_klines` 和
**同一个** `kline_repo.materialize_periods()`，只靠 `ZX_MYSQL_DB` 切库，
因此「数据源」是唯一变量。

⚠️ **本节的复现命令已失效**（2026-10-13 更正）：对照用的 `mysqldb` / `tq-stock`
两个库**本机已不存在**，且 `tools/compare_sources.py` 这个脚本**已被删除**
（`server/tools/` 下无此文件）。下表是当时的实验结论，仅作记录保留，无法直接复跑。

```bash
# ↓↓↓ 以下三条为当时的原始命令，仅存档，当前不可复跑（见上方更正）
tools\\init_db.py  --db tq-stock
tools\\sync_tx.py  sh600570 --db tq-stock          # 腾讯 → tq-stock
tools\\sync_stock.py sh600570                       # pytdx → mysqldb
tools\\compare_sources.py sh600570                  # 逐根 diff（脚本已删）
```

**样本**：5 只标的、18018 根日线（4 只 4000 根 + 宁德时代 2018 根）。

| 指标 | 结果 |
|------|------|
| 交易日历对齐 | **18018/18018，两库都没有单边独有的日期** |
| 开盘价 | **0 处差异**（18018 根逐位相同） |
| 最高价 | 1 处差异（恒生电子，偏差 0.06） |
| 收盘/最低价 | 20 处差异（0.11%），最大偏差 1.20 |
| 成交量「取整级」差异 | 94%（腾讯按整手存，TDX 精确到股，单根固有 <99 股误差） |
| 成交量**实质**分歧（≥1%）| **20 根（0.11%）** |
| 成交额 | pytdx 18018 根全有；**腾讯 0 根全无** |

#### 关键发现一：分歧日期在无关个股上完全同步

| 日期 | 恒生电子 | 贵州茅台 | 工商银行 | 平安银行 |
|------|---------|---------|---------|---------|
| 2011-12-13 量差 | 81.7% | 48.2% | 40.9% | 67.2% |
| 2011-12-14 | 37.7% | 11.9% | 19.0% | 20.3% |
| 2011-12-15 | 28.7% | 31.7% | 50.1% | 47.5% |
| 2014-04-23 | 3.8% | 3.8% | 2.9% | 1.8% |

四只毫不相干的股票在**同一批日期**上出现大幅分歧——这不可能是行情本身，
只能是**数据链路层面的系统性产物**。宁德时代（2018 上市）**零差异**，
说明 2018 年之后两源一致，问题集中在更早的历史段。

#### 关键发现二：三源仲裁后，pytdx 正确、腾讯错

本地 `.day` 只覆盖 2020 年以后，仲裁不了 2011 年。引入**第三个独立厂商**
东方财富 `push2his` 后定案：

| 日期 | 东方财富 | pytdx | 腾讯 |
|------|---------|-------|------|
| 2011-12-13 收盘 | 11.32 | 11.32 | 11.80 |
| 2011-12-13 成交量 | 11,213,200 | 11,213,220 | 2,047,600 |
| 2011-12-13 成交额 | 128,610,658 | 128,610,656 | *（无此字段）* |

**三比二，pytdx 与东财一致，腾讯错。** 附带一个自洽性证据：
pytdx 的 `amount / volume = 11.47` 落在它自己的 `[low, high] = [11.00, 12.11]` 内，
数值合理；而腾讯声称当日最低 11.79，若按其成交量根本不可能出现 11 元的价格。

#### 结论

1. **腾讯不能单独承担 K 线主源**——0.11% 的根数带着 30%–80% 的量差，
   且集中在特定日期、无法用「取整」解释。这类错误不报错、不为空，
   只会安静地污染指标。
2. **腾讯的独有价值是复权**：`fq`/`hfq` 参数原生支持 qfq/hfq，
   pytdx 完全没有。这是 pytdx 的真实短板，应该用它补，而不是反过来。
3. **腾讯的硬缺口是成交额**：K 线行只有 6 个字段，不含成交额。
   本项目**不会**用 OHLC×volume 反推——那是编数据。
4. **东方财富可作第三方仲裁源**（含成交额），但历史上因
   `RemoteDisconnected` 不稳定被弃用，稳定性需重新评估。
5. **对照实验本身要保留**：它证明「两个源一致」不能作为正确性证明——
   如果当初只有 pytdx 一个源，2011-12 那批数据会一路错下去而无人发现。

### 弃用项

| 源 | 原因 |
|---|---|
| 东方财富 K 线 | **2026-10-07 实测：已不可用**。`push2his` / `push2his2` / 全部编号节点 6/6 次重试均拿不到 JSON（`RemoteDisconnected` 或返首页 HTML），原 §3.4 的「东财仲裁锚点」能力已失效 |
| 东方财富 `datacenter-web` | **可用**（除权除息 `RPT_SHAREBONUS_DET` 6/6 稳定），已实测恒生电子 24 条除权除息记录；这是 pytdx / tdx2db / 腾讯都缺的**除权除息原始表** |
| 东方财富搜索 | 用户决定不依赖本方案 |
| 新浪 | GBK + HTML 解析，引入双编码 |
| akshare | py_mini_racer + Python 3.14 崩溃 |
| pytdx 备用之外的所有时点 K 线 | 单源原则（该原则已被 §3.4 的对照实验修正为**多源互校**） |
| tdx2db 作为整库依赖 | 表结构与我们多周期设计冲突，且只读本地 `.day` 补不了 2020 年前的历史；
仅移植其 `blocks.py` 解析板块与股本，详见 `../others/tdx2db-reference.md` |
| Tushare 暂未接入 | 全清单与定价见 `tushare-reference.md`。需注意其**积分与独立权限是两套体系**：
200 元/年拿全 A 股历史日线 + 财务三表 + 复权因子，但**分钟线要另付 2000 元/年** |

---

## 4. 同步策略

### 4.1 三档任务

| 任务 | 触发 | 范围 | 频率 |
|------|------|------|------|
| 首次全量 | FastAPI 启动 + MySQL 空 | 全 A 股列表 + 26 年 K 线 + ES 索引 | 一次性 |
| 每日增量 | 调度器 16:30 | 遍历 MySQL `stock_info` → 拉最近 10 根 K 线 | 每日 |
| 每周重新枚举 | 调度器周日 17:00 | pytdx 全 A 股列表 → diff → 新增/退市 | 每周 |

**当前实现状态（2026-10-05）**：上表是目标态，实际只落地了手动同步——
`tools/sync_kline.py` CLI 或 `POST /api/admin/sync`，标的固定上证指数、周期固定日线、
范围最近 30 根。**调度器（apscheduler）尚未接入，全量/增量/重新枚举均未实现**，
不要以为服务启动会自动拉数据。用法见 §8.1。

同步走 `ON DUPLICATE KEY UPDATE` 批量 upsert（每批 500 行），因此**可安全重复执行**：
同一 `(code, period, trade_date)` 覆盖更新，复跑不会产生重复行。

### 4.2 0AMV 特殊

指南针**本地文件** `day.vdat` → 解析 → MySQL `kline_0amv`，详见
[znz-active-mv-ocr-sync.md](znz-active-mv-ocr-sync.md)。OCR 截图方案（paddleocr/opencv/
pywinauto）已设计但**未实施**，当前没在跑。

---

## 5. 跨表约定（已迁出）

> ⚠️ 本节内容在 2026 年早些时候已迁到
> [database-schema.md](database-schema.md) §5「跨表约定」。这里保留一个转发小节，
> 是因为**代码里有多处注释指向 `architecture.md §5.2 / §5.3`**（`db.py`、`kline_repo.py`、
> `market.py`、`main.py`、`stock_repo.py`、`tools/backfill_stock_universe.py` 共 7 处）——
> 章节直接删掉会让这些引用全部悬空。

| 原引用 | 现位置 |
|---|---|
| §5.1 量纲总则 | [database-schema.md §5](database-schema.md) |
| §5.2 `volume` 统一为**手** | [database-schema.md §5](database-schema.md)（§5.3 `amount` 统一为**千元**） |
| §5.3 基本面表设计 | [database-schema.md §7.3](database-schema.md) |

长期方向是把代码注释里的 `architecture.md §5.x` 一并改指 `database-schema.md §5.x`，
改完即可删除本转发小节。

---

## 6. 前端结构

```
web/src/
├── components/
│   ├── Layout.tsx          # 整体布局（Sidebar + main）
│   ├── Sidebar.tsx         # 11 项菜单（首页/行情/自选/条件/复盘/详情/板块/ETF/样本/回测/模拟）
│   ├── ChartFrame.tsx      # 自写图表外壳（单段 grid，header 行已删）
│   ├── ChartHeader.tsx     # SymbolInfo + ChartTools（含设置弹窗、版面比例 Slider）
│   ├── ChipDistribution.tsx# 自绘 canvas 筹码分布（80 桶，十字线切片）
│   ├── CrosshairTooltip.tsx # 自绘 OHLC 浮窗（量额直取库里）
│   ├── XdxrModal.tsx       # 除权除息弹窗
│   ├── RegionStatsModal.tsx# 区域统计弹窗
│   └── StockSprite.tsx     # 个股精灵图
├── pages/
│   ├── HomePage.tsx        # 首页 → 2026-10-08 起故意留空（行情图已移除）
│   ├── MarketPage.tsx      # 主行情页（挂 MarketChart）
│   ├── MarketChart.tsx     # K 线主组件（受控/非受控双轨）
│   ├── WatchlistPage.tsx   # 自选股（左侧列表 + 右侧 K 线）
│   ├── PlaceholderPage.tsx # 8 个占位页共用
│   └── StockChart.tsx      # 旧 Recharts 实现（未引用，可删）
├── utils/
│   ├── indices.ts          # 6 个内置标的常量（4 指数 + 0AMV + 宝妈）
│   ├── chartOptions.ts     # PERIOD / RANGE（RightPanelMode 已随筹码切换器删除）
│   ├── layout.ts           # 版面比例常量与反推公式（落库 app_config）
│   ├── exDividendMarkers.ts# klinecharts 自定义除权标记 overlay
│   ├── watchlist.ts        # 自选股 + 分组（内存 store，数据落库 watch_group/watch_item）
│   ├── stockSearch.ts      # → /api/stock/search
│   └── formatCode.ts       # sh000001 → SH.000001
└── config/
    └── api.ts              # API_BASE 与全部类型定义（KLineBar 等）
```

**关键模式**：MarketChart 受控/非受控双轨（首页独立 / WatchlistPage 嵌入）；ref 镜像 state 解决 getBars 闭包陷阱；订阅模式替代 React Context 同步自选股。

---

## 7. API

| Method | Path | 用途 |
|--------|------|------|
| GET | `/api/index/kline?code=&category=&count=&period=&start_date=&end_date=&adj=` | K 线（MySQL）。category **4/5/6/10/11** = 日/周/月/季/年（7/8 为兼容前端旧取值的别名）；`period` 直接给 `day/week/month/…` 优先于 category；`adj=none\|qfq\|hfq` 默认 `none`；`count` 默认 250、上限 1000 |
| GET | `/api/index/quote?code=&live=&period=` | 最新报价 + 昨收 + 涨跌幅（通达信实时快照，失败回落 MySQL；`live=false` 强制只读库） |
| GET | `/api/index/quotes?codes=&live=` | 批量报价，自选股列表用（MVP 阶段逐个并发取） |
| GET | `/api/index/list` | 内置指数列表 |
| GET | `/api/stock/list` | 库里已有行情数据的全部标的 |
| GET | `/api/stock/search?q=&limit=` | A 股搜索。**纯 SQL `LIKE` 实现**（拼音声母已落 `stock_info.pinyin`）；ES 就绪后再切，届时补拼音/模糊召回 |
| GET | `/api/stock/xdxr?code=` | 除权除息记录列表（弹窗用） |
| POST | `/api/admin/sync?code=&days=&period=` | 手动触发通达信同步 |
| GET | `/api/admin/status` | 同步进度 / 健康度 |
| GET | `/api/config` | 读应用级设置（只返回库里真实存在的键） |
| PUT | `/api/config` | 写应用级设置（只 upsert 传进来的键，不删） |
| GET | `/api/watchlist` | 读全部分组 + 自选条目（`default` 恒排最后；条目顺序 = 首次加入时间） |
| POST | `/api/watchlist/items` | 加自选 `{code, group}`，幂等 |
| DELETE | `/api/watchlist/items/{code}?group=` | 移除；不给 `group` 就是整只移除 |
| POST | `/api/watchlist/groups` | 新建分组 `{label}`，返回去重后的 `{id, label}` |
| GET | `/health` | 健康检查（含 db 连通）——**不走** `{code,data}` 包装 |

返回结构：`{ code: 0, data: ... }` / 失败 `{ code: 1, error: ... }`；
K 线接口顶层另带 `truncated`（按 `end_date` 查时被 `count` 截断，前端必须读）。

K 线每根字段（11 个）：
`datetime / open / high / low / close / volume / turnover / chg_amt / chg_pct / is_xdxr / xdxr_date`。

- `volume` 是**成交量**（手），`turnover` 是**成交额**（千元，库列名 `amount`，对外取图形库原生名以便原样透传）
- `chg_amt` / `chg_pct` 无值时为 `null`，前端据此判断"库里有权威值就用、没有才自己算"
- `is_xdxr` 该周期内是否发生过除权；`xdxr_date` 为真时给出**真实的除权日**（YYYY-MM-DD）——
  周期 bar 的 `datetime` 是桶内最后一个交易日，两者常常不是同一天，弹窗高亮必须用后者

### 7.1 当前已同步的数据（MVP）

| code | period | 范围 | 根数 |
|------|--------|------|------|
| `SH.600570`（恒生电子） | day | 2003-12-16 ~ 2026-10-12 | 5435 |
| `SH.600570` | week | 2003-12-19 ~ 2026-10-12 | 1150 |
| `SH.600570` | month | 2003-12-31 ~ 2026-10-12 | 275 |
| `SH.600570` | quarter | 2003-12-31 ~ 2026-10-12 | 93 |
| `SH.600570` | year | 2003-12-31 ~ 2026-10-12 | 24 |

> ⚠️ `code` 全库唯一形态是 **`SH.600570`**（**大写 + 点 + 6 位**，2026-10-09 收口）。
> 早先这里写的 `sh600570`（小写无点）已不是库里的值，照着 `WHERE code='sh600570'` 查**返回 0 行**。
>
> 上表为 2026-10-13 实测。库里目前**只有 `SH.600570` 一个标的**——`sh000001` 等指数
> **一根 K 线都没有**，早期 MVP 那 30 根已被清空。另有 `kline_0amv` 表 8220 行（`0AMV`）。
> 取实时值：`.\.venv\Scripts\python.exe tools\show_db.py`

`SH.600570` 的四个非日周期**由日线物化**而来（`trade_date` = 周期最后一个交易日），
不是单独向通达信拉的。基本面与除权除息同步入库：

| 表 | 行数 | 内容 |
|----|------|------|
| `stock_info` | 5225 | 全市场名录（沪深主板/创业板/科创板），名称+拼音零缺失 |
| `stock_finance` | 0 | ❌ 空表，Tushare 权限不足 |
| `stock_xdxr` | 47 | 2004-06-04 ~ 2026-09-30，其中 **23 条**对应真正的除权除息，其余为股本变化等中间态 |
| `app_config` | 3 | 前端显示类设置（`show_price_line` / `top_ratio` / `right_ratio`） |
| `watch_group` | 1 | 自选分组（只有兜底组 `default`「其他」） |
| `watch_item` | 0 | 自选条目 ↔ 分组（2026-10-14 建表，暂无数据） |

补数据：

```bash
# 完整链路（推荐）
.\.venv\Scripts\python.exe -W ignore tools\sync_stock.py SH.600570
# 只要日线
curl -X POST "http://127.0.0.1:3301/api/admin/sync?code=SZ.399001&days=30"
```

两个接口的当前形态（第一个已可用，不是"未就绪"）：

| 接口 | 当前行为 | 备注 |
|------|----------|------|
| `/api/stock/search` | ✅ **已接库**（2026-10-05）：`stock_repo.search_stocks()` 按 `raw_code` / `code` / `name` / `pinyin` LIKE，参数名是 `q`（**不是** `keyword`） | 纯 MySQL LIKE 够 MVP 用；**ES 未接入**，全项目零 elasticsearch 依赖。索引就绪后再换后端 |
| `/api/index/kline` 周/月/季/年 | 不复权：**优先读库内物化行**，无物化行时回退到查询时窗口函数聚合。带 `adj=qfq\|hfq` 时**不复用物化表**，一律从日线逐根乘因子再分桶 | 已可用，覆盖已同步标的 |

`stock_info.name` 已不再是 NULL：pytdx 没有名称接口，改从本地通达信
`T0002/hq_cache/{shs,szs}.tnf` 解析（见 [database-schema.md §7.3.1](database-schema.md)），
用 `tools/backfill_names.py` 回填。搜「恒生」现已能命中 `SH.600570`。

---

## 8. 部署运行

```bash
# 1. 启动 MySQL + ES（本机已有容器：mysql-8 / elasticsearch-8 / redis-7）
docker ps                       # 确认 mysql-8 在跑，映射 127.0.0.1:3306
# docker compose up -d mysql elasticsearch   # compose 文件待补

# 2. 启动指南针客户端（必装 + 登录 + 前台）
# 3. 通达信客户端无需启动——pytdx 直连服务器，与客户端进程无关

# 4. 后端（lifespan 自动建连接池 + 建表）
cd server
uv pip install aiomysql         # 首次
.\.venv\Scripts\python.exe -W ignore -m uvicorn server.main:app --host 127.0.0.1 --port 3301 --app-dir src

# 5. 前端
cd web && npm run dev
# 浏览器打开 http://localhost:3201
```

**启动顺序**：docker（MySQL）→ 后端 → 前端。通达信客户端不必启动。

### 8.1 拉数据（MVP）

```bash
cd server
# 单标的完整链路：日线全历史 → 物化周/月/季/年 → 基本面 → 除权除息
.\.venv\Scripts\python.exe -W ignore tools\sync_stock.py sh600570

# 早期 MVP：单标的 + 日线 + 最近 N 根（默认 sh000001）
.\.venv\Scripts\python.exe -W ignore tools\sync_kline.py --days 30 --show 30

# 调试用：只拉不写库
.\.venv\Scripts\python.exe -W ignore tools\sync_kline.py --dry-run --days 30

# 或通过 HTTP 触发
curl -X POST "http://127.0.0.1:3301/api/admin/sync?code=sh000001&days=30"
```

`tools/sync_stock.py` 的日线来源默认 `auto`（先 pytdx，失败回退本地 vipdoc），
可用 `--source pytdx|local` 强制指定。

### 8.2 验收脚本

```bash
# 拉取层：pytdx vs 本地 .day 逐根比对（不涉及数据库）
.\.venv\Scripts\python.exe -W ignore others\verify_against_local.py --count 800

# 落库层：库内日线 + 四个派生周期 + 基本面端到端验收
.\.venv\Scripts\python.exe -W ignore tools\verify_stock.py sh600570

# 查询层：读库路径 vs 回退聚合路径口径是否一致
.\.venv\Scripts\python.exe -W ignore tools\verify_query_paths.py sh600570

# 协议层：报文 extra 长度探测
.\.venv\Scripts\python.exe -W ignore others\probe_bar_layout.py --count 30 --max-skip 24
.\.venv\Scripts\python.exe -W ignore others\probe_categories.py sh600570

# 服务器：重新扫描可用节点
.\.venv\Scripts\python.exe -W ignore others\sweep_stock_bars.py

# 展示：把库内同步结果打成人可读汇总
.\.venv\Scripts\python.exe -W ignore tools\show_db.py
```

> 交叉校验的锚点是本机通达信 `vipdoc`（`config.TDX_VIPDOC`）。
> pytdx 是逆向协议，解析出错时不会报错，**只有拿 `.day` 逐根比对才能发现**，
> 所以 `external/tdx_local.py` 不是调试遗留，而是长期依赖。

---

## 10. 关键模式与教训

1. **客户端依赖是放大器**——0AMV 决定必须装客户端，架构必须围绕"客户端必须运行"展开
2. **ref 镜像 state**——MarketChart 用 ref 解决 klinecharts getBars 闭包陷阱
3. **请求 ID 防过期**——切指数 / 周期时旧请求响应被丢弃
4. **受控/非受控双轨**——同一 MarketChart 既独立使用也被 WatchlistPage 嵌入
5. **StrictMode 必须关闭**——klinecharts v10 二次 init 报错
6. **canvas DPR 不写 style.width**——避免与 CSS flex 抖动
7. **订阅模式替代 Context**——watchlist 跨组件同步轻量级
8. **pytdx 不能直接信**——它抓的是逆向协议，解析器对 K 线报文尾部保留字段的长度**写死为 0**，而该长度因标的而异（实测个股 0 / 指数 4）。写死 0 则指数逐根错位，网上流传的「补 4 字节」补丁则让个股越界。**两个方向的错位都很隐蔽**：价格因基址递推看着正常，只有日期会露馅。正解是按候选长度试探，用「正好消费完整个报文 + 日期合法」自证
9. **更隐蔽的是它把异常吞了**——`get_security_bars` 被 `@update_last_ack_time` 包着，默认 `raise_exception=False` 时捕获所有异常返回 `None`，于是「解析崩溃」和「服务端没数据」长得一模一样。曾经据此误判「免费节点不提供个股 K 线」，实际 8 台全通。**凡是 pytdx 返回 None，一律先用 `raise_exception=True` 看真实异常**
10. **通达信服务器大面积失效**——104 个内置节点仅 8 个能返回 K 线，务必用 `others/sweep_stock_bars.py` 实测而非照抄 `hq_hosts`
11. **非日线的 `vol` 是「手」，日线是「股」**——精确差 100 倍，个股与指数都一样；OHLC 与 `amount` 在所有周期都正确。同一列混用两种单位是个隐蔽陷阱，本项目因此统一「**只拉日线 + SQL 物化**」。⚠️ 注意区分：落库后全库 `volume` 统一为「**手**」（原始日线 `vol` ÷ 100），不是「股」——`db.py` 的 DDL 注释是「单位手（1 手 = 100 股）」。判据：`amount×1000 ÷ (volume×100)` 应当落在当日 `[low, high]` 内（恒生电子 2026-10-12 = 19.56，区间 [19.40, 19.75] ✓；若按股算是 1955.93，明显荒谬）
12. **周期聚合不能用 MIN(open)**——周/月 K 的 open 要取周期内首个交易日的 open，`MIN(open)` 会取到周内最低开盘价
13. **K 线只读库，报价走实时快照**——K 线从 MySQL 读（保证前端拿到的就是刚同步的数据）；但**报价必须联动盘中行情**，只读库会让头部价格停在"最后一根已入库日 K"，没同步的那几天就是个固定值。故 `/api/index/quote` 走通达信 `get_security_quotes` 快照，失败自动回落库并用 `source` 字段如实标注
14. **涨跌幅以昨收为基准，不是开盘价**——快照的 `last_close` 才是昨收；拿 `open` 当基准会算出对不上的涨跌幅，且集合竞价期间 `open` 会是 0
15. **通达信快照会回显**——请求不存在的代码时，部分节点把该 market 第一只票的数据原样回显（`code` 仍回显请求值）：实测 `get_security_quotes([(1,'999999')])` 返回上证指数的 3842.19，`get_security_bars` 同样如此，因此**不能用它做存在性校验**。识别办法：同一次快照里附带问一次该 market 的基准指数，5 个价位字段逐字段相同且代码不同即判为回显丢弃
16. **交叉校验要有独立锚点**——逆向协议没有「报错」这个安全网，数据错了不会自动暴露。必须保留本机通达信 `.day` 作为真值来源，每次改动解析逻辑都用它逐根比对（`others/verify_against_local.py`），否则下一次协议漂移会静悄悄地把错误数据写进库
17. **K 线报文是「旧→新」升序，不是降序**——`start` 是从最新往回数的偏移量，页内升序、页间降序。翻页时要倒序拼接**页**、保持页内顺序，整体反转会把每页内部也倒过来