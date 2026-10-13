# zx-stock 待办清单

> ← 返回 [架构说明](architecture.md)
>
> 与架构文档分离独立维护。每次实现完一项请打勾 + 标注完成日期。

---

## 状态总览

| 模块 | 状态 |
|------|------|
| 行情页 / 自选页 K 线（6 个内置标的）+ 筹码分布 | ✅（首页行情图 2026-10-08 已移除，首页留空） |
| 多页面路由 + 左侧导航 | ✅ |
| A 股搜索 + 加入分组 | ✅（2026-10-08 已接 `stock_info`，拼音首字母可搜） |
| **数据源重构：多源 → 客户端依赖（MySQL + ES）** | 🔄 MVP 已通（个股日线全历史 + 四周期 + 基本面 + 除权除息）|
| 指南针 0AMV OCR 同步 | 📝 |
| 分钟线 / 亮色主题 / 行业板块 / 8 个占位页 | ❌ |

图例：✅ 已完成 / 🔄 进行中 / 📝 已设计未实施 / ❌ 未开始

---

## P0 — 阻塞核心体验

### 数据源重构：多源 → 客户端依赖

#### MVP（已完成 2026-10-05）

- [x] 新增 `db.py`（aiomysql 连接池 + schema init）完成于 2026-10-05
- [x] 新增 `external/tdx_kline.py`（pytdx 直连 + parseResponse 修复）完成于 2026-10-05
- [x] 新增 `kline_repo.py`（MySQL CRUD / 批量 upsert）完成于 2026-10-05
- [x] 新增 `sync.py` + `tools/sync_kline.py`（手动同步 CLI）完成于 2026-10-05
- [x] 新增 `market.py`（日线直读 + 周/月 K 聚合）完成于 2026-10-05
- [x] 改 `main.py`：全部接口改读 MySQL + lifespan 建池 完成于 2026-10-05
- [x] 前端去掉 `source=akshare` 参数（4 处）完成于 2026-10-05
- [x] 依赖加 `aiomysql` 完成于 2026-10-05

MVP 交付范围：上证指数 `sh000001` 日线 30 根（2026-08-19 ~ 2026-09-30）。

验收：落库数据与本地通达信 `.day` 文件逐字段 30/30 一致；周/月 K 聚合逐根校验通过。

**踩坑记录（重要，2026-10-05 修正过一次，结论被推翻重写）**：

1. **K 线报文尾部保留字段的长度不固定，不能写死。**
   实测 `extra` 长度：个股 0 字节、指数 4 字节。
   pytdx 原版写死 0 → 指数逐根错位；网上流传的「补 4 字节」补丁 → 个股越界读出垃圾日期。
   两个方向都很隐蔽，因为价格靠基址递推，看着一直正常，只有日期会露馅。
   现按候选长度 `(0,4,8,12,16,20)` 试探，取第一个「能走完 ret_count 根 +
   正好消费完整个 body_buf + 时间元组严格单调」的布局。
2. **`get_security_bars` 会把异常吞掉返回 None。**
   `TdxHq_API(...)` 默认 `raise_exception=False`，`@update_last_ack_time`
   捕获所有异常后返回 `None`，于是「解析崩溃」和「服务端没数据」完全无法区分。
   曾据此误判「免费节点不提供个股 K 线」，实际 8 台全通（已用本地 `.day` 逐根验证）。
   **现在所有连接一律 `raise_exception=True`。**
3. pytdx 内置 104 个 hq_hosts 里仅 `117.34.114.13~27`（共 8 个）能真正返回 K 线，
   已固化到 `config.TDX_HOSTS`。**指数与个股在这 8 台上可用性完全相同。**
4. **非日线（周/月/季/年）的 `vol` 是「手」，日线是「股」**，精确差 100 倍，
   指数和个股都一样。OHLC 与 `amount` 在所有周期都正确。
   这不是数据损坏而是单位差异，但同一列混用两种单位是隐蔽陷阱，
   故统一「只拉日线 + SQL 物化」。⚠️ 落库后全库 `volume` 统一为「**手**」——
   原始日线 `vol`（股）÷ 100，不是「股」。判据：`amount×1000 ÷ (volume×100)` 应落在
   当日 `[low, high]` 内（恒生电子 2026-10-12 得 19.56，区间 [19.40, 19.75] ✓）。
5. **K 线报文是旧→新升序**，`start` 是从最新往回数的偏移量。
   翻页要倒序拼接**页**、保持页内顺序；整体反转会把每页内部也倒过来。
6. 聚合周/月 K 时 open 必须取「周期内首个交易日的 open」，**不能 `MIN(open)`**；
   窗口函数写 `LAST_VALUE` 必须显式加 `ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING`。
7. **接口字段名一律跟库列对齐，不要为了迁就图形库而改名**。库 `volume`（成交量）
   对外就叫 `volume`，库 `amount`（成交额）对外叫 `turnover`（图形库原生名）。
   曾反过来把 `volume` 塞进 `amount` 键，接入方第一眼必然读反。
8. **`external/tdx_local.py`（读本机 vipdoc `.day`）是长期依赖，不是调试遗留**——
   逆向协议没有报错这个安全网，只有拿 `.day` 逐根比对才能发现数据错了。
9. **TDX 的 category 里 7/8 不是季/年线**（2026-10-05 修下拉框时发现）。
   实测：`4=日K 5=周K 6=月K 10=季K 11=年K`，而 `7=5分钟 8=15分钟`。
   `web/src/utils/chartOptions.ts` 里「4=日,5=周,6=月,**7=季,8=年**」的注释与
   `PERIOD_OPTIONS` 取值都是错的；后端 `main.py` 的 `PERIOD_BY_CATEGORY` 当时也只映射了
   4/5/6，于是 **季线/年线一路 `.get()` 落空、静默回落成日线**——
   界面上「切换成功但图还是日线」，不报错、不空、不掉数据，最难发现。
   已修：后端补 `10/11`（协议正确值）并把 `7/8` 留作兼容别名。
   **注意 `category` 是前端契约值、不是线协议**，修前端时把 `PERIOD_OPTIONS`
   改成 10/11 即可，后端两种都收。
   教训：这类「静默兜底」比报错危险得多，兜底至少要能在日志/返回值上区分开。
10. **「两个数据源一致」不能证明数据是对的**（2026-10-05 对照实验实测）。
    pytdx × 腾讯 18018 根日线里，4 只**互不相关**的股票在 **2011-12-13/14/15、
    2014-04-23** 这几个日期上同步出现 10%–80% 的成交量分歧——这排除了「行情本身」。
    引入第三方东方财富仲裁后判定：**pytdx 对、腾讯错**。
    推论：如果当初只有一个源，这批错数据会一路静默入库，而任何单源自校验都发现不了。
    **交叉校验必须是独立厂商链路**，同一个供应商的两条管道不算。
    另外注意：开、高两根 18018 根逐位相同，反而是「解析器没错」的最强证据——
    因为 pytdx 的价格是相对基址递推的，真错会整段漂移，不会只错在低/收。

#### 恒生电子 MVP（已完成 2026-10-05）

- [x] `db.py` 增建 `stock_info` / `stock_finance` / `stock_xdxr` / `ref_industry` / `ref_province` 完成于 2026-10-05
- [x] `external/tdx_finance.py`（`get_finance_info` / `get_xdxr_info`）完成于 2026-10-05
- [x] `external/tdx_local.py`（本地 `.day` 读取，作交叉校验锚点与回退源）完成于 2026-10-05
- [x] `stock_repo.py`（三表写入层）完成于 2026-10-05
- [x] `kline_repo.materialize_periods()` 窗口函数物化周/月/季/年 完成于 2026-10-05
- [x] `sync.sync_stock()` 完整链路 + `tools/sync_stock.py` 完成于 2026-10-05
- [x] `market.py` 改为优先读物化行、无则回退窗口函数聚合 完成于 2026-10-05
- [x] `tdx_kline.fetch_history()` 分页拉全历史 完成于 2026-10-05
- [x] 验收脚本：`verify_against_local` / `verify_stock` / `verify_query_paths` / `probe_bar_layout` / `probe_categories` 完成于 2026-10-05

交付结果（`sh600570`）：日线 4000 根（2010-01-04 ~ 2026-09-30）+ week 848 / month 201 /
quarter 67 / year 17 + `stock_info` 1 行 + `stock_finance` 1 行（2026-08-26，19 字段）+
`stock_xdxr` 46 行。验收：日线与本地 `.day` 逐根一致；四周期与 Python 侧重算逐根一致；
查询双路径口径一致；日线 OHLC 关系违例 0 条。

> 2026-10-08 补注：以上 4000 根是 pytdx 时期的交付量。
> 2026-10-13 更正：`tsdb` 已于 2026-10-09 整库改名为 **`zx_invest`**，本机现在**只有这一个业务库**
> （`SHOW DATABASES` 里已无 `tsdb` / `mysqldb` / `tq-stock`）。库里已由 Tushare 重拉全历史：
> **日线 5435 根（2003-12-16 ~ 2026-10-12）**，`code` 唯一形态是 `SH.600570`（**大写带点**）。
> 派生周期**已于 2026-10-13 完成物化**（week 1150 / month 275 / quarter 93 / year 24），
> 「暂不物化、按需走窗口函数聚合」这个决策**已作废**——不物化时 `chg_amt` 压根没有这一列。
> ⚠️ `code` 格式是第二个坑：用小写 `sh600570` 查库返回 **0 行**，不报错。

#### 剩余（待实施）

- [ ] 删旧代码：`data_source.py` / `pytdx_service.py`（当前已不被引用）
- [x] 股票名称来源 —— 2026-10-05 解决：读本地通达信 `T0002/hq_cache/{shs,szs}.tnf`
      （`tdx_local.load_name_map()`，8896 条），`tools/backfill_names.py` 回填。
      坑见 database-schema.md §7.3.1：全角Ａ、内嵌空格、同码不同义
- [ ] 行业/省份名称翻译（`get_block_info` 返回空）—— ⚠️ 这已是**从零新建**而非"补空表"：
      `ref_industry` / `ref_province` 两表已于 2026-10-07 **删除**（见 database-schema.md §4），
      库和 `db.py` 里都不存在，要做就得重新设计表结构。
      —— 根因已查明：旧版通达信二进制 `block_zs/gn/fg.dat` 在新版客户端已不存在，pytdx BlockReader 失效。
      方案与本机文件清单见 [tdx2db-reference.md](../others/tdx2db-reference.md) §2.1（7 个文件本机全部齐全），
      做法是移植其 `blocks.py`（240 行 / 零新增依赖 / MIT）而非引入整个库
- [ ] 扩展标的：4 个内置指数 + 全 A 股列表 + 自选股
- [ ] 增量调度：日线只 upsert 不删除，按受影响周期键精确重算；另需 `rebuild_derived(code, period)`
- [x] 新增 `external/tx_kline.py`（腾讯 HTTP 源）完成于 2026-10-05
      —— 但**不能当 K 线主源**：对照实验（architecture.md §3.4）证明它有 0.11%
      的根带着 30%–80% 量差，且四只无关个股在同批日期上同步出错，
      东财仲裁判定**腾讯错、pytdx 对**。它的正确用途是「复权」+「实时快照 + 名称」。
- [ ] 腾讯只用作复权补齐：pytdx 无 qfq/hfq，用 `tx_kline.fetch_period(adjust=...)` 取
- [ ] 重新评估东方财富 `push2his` 稳定性（曾因 RemoteDisconnected 弃用，但它是目前
      唯一能提供成交额的第三方源，可作仲裁锚点）
- [ ] ES 索引 mapping + IK + pinyin 插件 + 搜索接口接上
- [ ] 调度器：每日 16:30 增量 + 每周日 17:00 重新枚举
- [ ] 公司资料板块（`get_company_info_*`）按需拉取 + 短 TTL 缓存，不入库

#### 主图 K 线游标式加载（已完成 2026-10-08）

- [x] 后端 `market.query_klines` 支持 `start_date` / `end_date` 闭区间游标 + `truncated` 标记 完成于 2026-10-08
- [x] 前端 `getBars` 分派 `init` / `forward`，首屏 500 根、每次 +500、一直翻到库里最早一根 完成于 2026-10-08
- [x] `API_BASE` 收进 `web/src/config/api.ts`，消除 5 处硬编码 完成于 2026-10-08
- [x] 新增 `docs/kline-bar-counts.md`（根数档位对照，8 档已逐条对库核验）完成于 2026-10-08

验收：`sh600570` 全历史 5432 根（2003-12-16 ~ 2026-09-30）经 11 次请求翻完
（1 次首屏 + 10 次翻页），累计 5432 根 / 重复 0 / 严格递增 /
前 1000 根与库内真值零差异；逐页断言「返回末根 == 游标」且「去重后正好少 1 根」全部通过。

**踩坑记录（2026-10-08）**：

1. **klinecharts 的 `forward` / `backward` 指的是滚动方向，不是时间方向。**
   `'forward'` 做 `data.concat(_dataList)`（**前插** = 加载**更早**的历史），
   `'backward'` 做 `_dataList.concat(data)`（**后接** = 加载**更新的**数据），与字面直觉相反。
   按直觉用 `'backward'` 拉历史 → 旧数据被接到数组末尾，X 轴出现
   `2026-07 → 2026-09 → 2024-09` 这种回跳。类型签名和命名都不给信息，
   **只有读 `_addData` 的 switch 分支才能判出来**。
2. **前插不动 `_lastBarRightSideDiffBarCount`、后接要减去新增长度**，两者都是为了保住视口。
   正因为前插不动，插完 `from` 会从 0 变成已插入长度，所以**不会连锁触发下一轮**。
3. **闭区间游标必然重叠 1 根**，而 klinecharts 的 concat 既不去重也不排序，
   必须自己按 datetime 剔掉，否则图上出现两根连续的同一根 K 线。
4. **游标必须存「已加载集合的全局最小日期」**，不能存「本次返回数组的第 0 项」——
   后者往回翻时随页面上移，等于游标没动，会一直卡在同一页。
   游标状态必须放 `ref` 不能放 `state`：`state` 写入要等下一次渲染才可见，
   连续两次翻页会拿到同一个旧游标。
5. **「内存上限」这个说法当时是错的。** 实测 `KLineData` 约 166 B/根，
   全历史 5432 根仅 878 KB，浏览器完全扛得住；真正随根数放大的是
   klinecharts 每次前插都要对整个 `_dataList` 重算 7 条指标序列再重绘 canvas（CPU，不是内存）。
   已删除 `MAX_LOADED_BARS` 上限。内存要到全市场规模（1728 只 × 5000 根 ≈ 1.4 GB）才紧张。

**遗留项**：

- [ ] **筹码分布口径与主图对不上**（优先，属显示正确性）
      `ChipDistribution` 仍写死 `DEFAULT_BAR_COUNT = 200` 独立取数，
      而主图现在能一路翻到 5432 根，两者不是同一个回看窗口，
      用户看到的成本分布不是当前图表区间的真实成本分布。
      改法：把主图已加载区间（起止日期 / 已加载根数）传给面板，走同一套游标取数。
- [ ] 切换标的/周期会多发一次 init 请求（纯性能浪费，无功能影响）
      `setSymbol()` 与 `setPeriod()` 各自触发 `resetData()` → 各自发一次 init，
      第一次的结果被 `myId !== requestIdRef.current` 守卫丢弃。
      首次挂载**不受影响**（`_period` 初始为 null，`_processDataLoad` 的 `isValid` 不成立，
      要等 `setPeriod` 设好才触发），只在切换时发生。
      改法：只在 ticker 变化时调 `setSymbol`、只在 period 变化时调 `setPeriod`。

#### 键盘精灵（已完成 2026-10-08）

全局按 `/` 唤起搜索面板，输代码 / 汉字 / 拼音首字母，回车跳到自选页查看。
行情页点任意一行也走同一条路径。

**收录口径**（`stock_info` 表只装这些）：
- 沪主板 `600/601/603/605`、科创板 `688`
- 深主板 `000/001/002/003`（002/003 是原中小板，2021-04 已并入主板）、创业板 `300/301`
- 排除：指数、基金/ETF/LOF、B 股、债券、回购

**代码段必须按「市场前缀 + 代码段」判断**，不能只看 6 位码：
`sh000001` 是上证指数而 `sz000001` 是平安银行，同一段码跨市场含义完全相反。

- [x] `stock_info` 表加 `pinyin` 列（幂等迁移，`db.py MIGRATIONS`）
- [x] `stock_repo.pinyin_initials()` — pypinyin 首字母 + **NFKC 归一化**
- [x] 全市场名录回填 `tools/backfill_stock_universe.py`（白名单式裁剪，幂等）
- [x] `search_stocks` 支持拼音匹配 + 前缀命中优先排序
- [x] 全局 `/` 快捷键（焦点守卫 + ↑↓ + Enter + Esc）
- [x] 侧栏可见入口（快捷键本身不可发现，界面上必须画出来）
- [x] 自选页读 `?code=`，显示「正在查看，尚未加入自选」提示条 + 加入按钮
- [x] 行情页行点击跳转（**不自动加入自选**）

验收（真实 Edge 跑 19 项全过）：`hsdz`→恒生电子 · `恒生`→恒生电子+华恒生物 ·
`600570`→恒生电子 · `510300`/`sh000001`/`sz399001` 全部无命中 · `↑↓` 移动高亮 ·
Enter 跳 `/watchlist?code=sh688639` · 左侧自选列表不新增条目 · 输入框内打 `/` 不劫持成快捷键。

**踩坑**

1. **两个 `useEffect` 同批跑会互相覆盖。** 从行情页跳进来时 `selectedCode` 初始为 `null`，
   于是「默认选中第一条自选」的 effect 误判成「用户还没选」，把 URL 指定的标的覆盖成
   自选列表第一条。两个 effect 读的是**同一次 render 的 state**，后写的赢。
   **只在自选非空时触发** —— 空库测试根本碰不到。
   改法：`if (!selectedCode && !codeParam && items.length > 0)`。
2. **`pypinyin` 对全角字母不做 ASCII 折叠。** `str.lower()` 只降 ASCII，
   全角 `Ａ`（U+FF21，TDX 名称表里真实存在，见 `tdx_local` 注释）降完还是全角 `ａ`，
   于是 `Ａ股指数` 的拼音变成 `ａgzs`，用户敲半角 `agzs` 一条都搜不到。
   必须 `unicodedata.normalize('NFKC', s).lower()`。
3. **pymysql 不能混用裸 `%s` 和具名 `%(name)s`。** 它把 dict 展开成值列表，
   同一个 key 出现几次就展开几次，混用时占位符总数超过实际值个数，
   直接报 `TypeError: not enough arguments for format string`。
4. **工具脚本只调 `init_pool()` 会漏跑迁移。** `init_pool()` 只建连接池，
   建表/补列在 `init_schema()`。少了这步用到新加的列会报 `1054 Unknown column`。
5. **`.watchlist-main` 加兄弟元素会挤扁图。** `market-chart-page` 是 `height:100vh`
   自包含的，塞进 flex 列容器必须配 `height:auto; flex:1 1 auto; min-height:0`。

### 界面精简（已完成 2026-10-08）

- [x] **首页留空**：去掉首页那张 K 线图，页面本身和侧栏「首页」项**都保留**，
      `/` 不做重定向。原图和自选页能力完全重复（自选页右侧是同一个
      `MarketChart`，还多出自选列表和分组）。
- [x] 自选页工具栏「+ 加股票」按钮删除
- [x] 自选页空态 Empty 图标 +「搜索股票加入自选」按钮删除
- [x] 连带清理死代码：`AddStockModal` 组件、`addModalOpen` state、
      `.add-modal-*` / `.watchlist-empty` 样式、未使用的 import
      （`Tag` / `Spin` / `SearchOutlined` / `INDICES` / `StockSearchResult`）
- [x] 侧栏 NavLink 恢复 `end={item.path === '/'}` —— 去掉首页后一度删掉，
      留空首页必须加回，否则 `/` 会在所有子路由下都高亮

**加股票的入口统一收到侧栏的「搜索股票」按钮（键盘精灵）**。原来有三份入口
（工具栏按钮、空态引导、侧栏搜索），指向同一件事；删掉前两处后只剩一份。

验收（真实 Edge 跑 21 项全过）：`/` 停留原路由不跳转 · 主区域 `innerHTML`
恰为 `<div class="home-page"></div>` · 无 canvas · 侧栏「首页」存在且高亮 ·
自选页无「加股票」/ 无 Empty / 无 AddStockModal · 工具栏只剩分组切换 ·
`/` 仍能唤起键盘精灵 · `hsdz` 仍命中恒生电子 · Enter 仍能跳转 ·
行情表仍有行。

### 指数与指标数据层（建表已完成 2026-10-08，数据源未接）

指数**独立成表，不进 `stock_info`**。最终收敛为 **1 张表**，**只有名录、没有行情数据列**。

| 表 | 内容 | 行数 |
|---|---|---|
| `index_info` | 指数名录（6 条） | **6** |

> 早先叫 `index`，是 **MySQL 保留字**——每条 SQL 都得写反引号，漏一处就是
> `1064 syntax error`。2026-10-08 与 `stock` → `stock_info` 一并改名，
> 两个表名成对。现在 SQL 里仍保留反引号（`FROM \`index_info\``），习惯性防呆。

名录（**code 全是大写带点**，照着这个敲 `WHERE code=` 才查得到）：

| code | name | pinyin | value_unit |
|---|---|---|---|
| `SH.000001` | 上证指数 | `szzs` | `point` |
| `SZ.399001` | 深证成指 | `szcz` | `point` |
| `SZ.399006` | 创业板指 | `cybz` | `point` |
| `SH.000688` | 科创50 | `kc50` | `point` |
| `0AMV` | 活跃市值 | `hysz` | `point` |
| `MOM` | 宝妈指数 | `bmzs` | `score` |

⚠️ 早先这里把 **`hysz` 当成了 code**、把 0AMV 的量纲写成 `yuan`，两者都错：
`hysz` / `bmzs` 是 `pinyin` 列的值，code 是 `0AMV` / `MOM`；
`0AMV` 的 `value_unit` 是 **`point`**，不是 `yuan`（库里 5 个 point、1 个 score）。

**同一张表里混着两种量纲 —— 将来接数据源时必须记住：**

```
上证/深成/创业板/科创50/活跃市值   point   指数点位 / 市值点位
宝妈指数 MOM                      score   0-100 情绪分     —— 连开高低收都没有
```

- 取数 SQL **必须带 `WHERE code = %s`**，多 code 直接聚合会把 3000 点和
  1.5e14 元加在一起。解读数字前先看 `value_unit`。
- 宝妈指数是单值指标（`data_shape='single'`），一天一个 0-100 分数，
  没有开高低收的概念；`ohlc` 的标的才有 OHLC 四列。

**为什么指数不进 `stock_info`**：`backfill_stock_universe.py` 每次跑会**删掉代码段白名单外的
所有行**，`sh000xxx` / `sz399xxx` 都不在白名单，指数放进去下次回填就被删光。
另外 `sh000001` 是指数而 `sz000001` 是平安银行，跨市场同码段含义相反，
白名单逻辑在这上面已经踩过一次坑。

- [x] `db.py` SCHEMA_STATEMENTS：单表 `index_info`
- [x] `/api/stock/list` UNION `index_info`，行情页显示 6 个指数（行情列显示 `—`）
- [ ] **接官方指数数据源**（4 个指数灌日频）
- [ ] **接活跃市值**（code `0AMV`；本地文件方案已落地入库 8220 行，OCR 方案见 `znz-active-mv-ocr-sync.md`）
- [ ] **接宝妈指数**（code `MOM`，线上面板已有：
      `https://xmkinc.github.io/mom-index`，另有 GitHub 源码 `mihang123/mom-index`）
- [ ] 接上行情后，`/api/index/...` 路由 + 前端指数详情

**遗留待清理**：`server/src/server/index_repo.py` 与 `server/tools/init_index_tables.py`
仍按旧的 2 表设计写（引用了**已不存在的** `index_daily`）。两者都**未被任何代码 import**
（`main.py` 的 `/api/index/list` 返回硬编码的 `INDICES` 常量），不影响运行时。
要么删掉，要么按单表 `index_info` 重写。

**踩坑**

1. `await` 优先级低于 `[]`：`{await cur.fetchone()[0]}` 会解析成 `await (fetchone()[0])`，
   报 `'Future' object is not subscriptable`。必须先 `n = (await cur.fetchone())[0]`。
2. 工具脚本只调 `init_pool()` 会漏跑建表 —— `init_pool()` 只建连接池，
   建表/补列在 `init_schema()`。少了这步用到新表直接报 `1054 Unknown table`。
3. **具名占位符的 dict 必须补齐所有键**，否则缺列时抛 `KeyError` 而不是「这列留空」。
4. **改已有表的列宽 / NULL 约束必须 DROP 重建**。`CREATE TABLE IF NOT EXISTS`
   不会改已存在的表。**这也是为什么改名比改表结构安全**：`RENAME TABLE` 是原子 DDL，
   多组改名任一失败则全部回滚。

### 指南针 0AMV OCR 同步

> 详见 `znz-active-mv-ocr-sync.md`（必读）

- [ ] 安装依赖：paddleocr / opencv-python / pillow / pywinauto / psutil / apscheduler
- [ ] 编写截图模块：pywinauto 接指南针 → 主图 Pane 截图
- [ ] 编写 OCR 模块：Y 轴 / X 轴 / K 线柱体三段 ROI 解析
- [ ] 编写增量同步：每日 17:30 拉今日 → upsert
- [ ] 编写冷启动：自动翻页 80 次 → 拉全历史
- [ ] 编写 watchdog：客户端进程 + 图表可见性
- [ ] 编写告警：Bark 推送（客户端掉线 / 同步失败）
- [ ] MySQL `indicator_active_mv` + `sync_znz_indicator` 建表
- [ ] FastAPI `/api/indicator/active_mv` 路由

---

## P1 — 完整性

### 8 个占位页实现

- [ ] 条件检索 `/condition`
- [ ] 交易复盘 `/trade-review`
- [ ] 个股详情 `/stock-detail`
- [ ] 行业板块 `/industry`
- [ ] ETF 专区 `/etf`
- [ ] 历史样本统计 `/history-sample`
- [ ] 历史回测 `/history-backtest`
- [ ] 模拟训练 `/simulator`

### 客户端管理

- [ ] watchdog 守护进程（指南针 + 通达信）
- [ ] Windows 计划任务：开机自动登录 + 启动指南针 + 启动通达信 + 启动 FastAPI
- [ ] 自动重连 / 自动重登脚本
- [ ] 客户端升级后的字段重探测脚本

---

## P2 — 体验

- [ ] 分钟线（1m / 5m / 15m / 30m / 60m）
- [ ] 亮色主题（klinecharts `light` theme）
- [ ] 大盘多指数 mini 图
- [ ] 错误 / 空状态 UI 完善
- [ ] 拖拽图表、平移股票、回测样例
- [ ] K 线对比 / 多标的重叠

---

## 维护说明

- ✅ 完成一项 → 勾选 + 在该项末尾加 `完成于 YYYY-MM-DD`
- 🔄 进行中 → 移到对应 P0/P1 段落后保持
- 新增项 → 放在对应 P 段最后，注明"为何加"
- 估算时间 ≥ 1 天的项需在子清单里拆分