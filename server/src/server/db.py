"""MySQL 连接池与建表。

K 线分两类表：``kline_day`` 是日线，由数据源**直接拉取**，唯一真相源；
``kline_period`` 把 ``week`` / ``month`` / ``quarter`` / ``year`` 四个**派生**
周期合并在一张表，用 ``period`` 列区分，全部由日线物化，口径见
docs/architecture.md §5.2。

周期到表名的唯一解析入口是 :func:`kline_table`；所有 SQL 都必须经它取表名，
不要在别处拼 ``kline_`` 前缀。注意它只返回表名——查派生周期时还要自行补
``AND period=%s``，因为日线表没有这一列。
"""
import aiomysql

from server import config

_pool: aiomysql.Pool | None = None

#: 全部 K 线周期，顺序即「真相源 -> 派生」。
KLINE_PERIODS = ("day", "week", "month", "quarter", "year")

#: 唯一真相源。
PERIOD_DAY = "day"

#: 由日线物化出来的周期。
DERIVED_PERIODS = ("week", "month", "quarter", "year")

#: 日线表（唯一真相源），由数据源直接拉取。
KLINE_DAY_TABLE = "kline_day"

#: 派生周期表。week / month / quarter / year **全在一张**，用 ``period`` 列区分。
#: 这四个周期彼此之间没有派生关系（都是日线的直接派生物），所以合并同层、
#: 用列区分，而不是拆成四张结构相同的表。
KLINE_PERIOD_TABLE = "kline_period"


def kline_table(period: str) -> str:
    """周期 -> K 线表名。

    这是**唯一**能把周期映射到表的地方。注意日线与派生周期**分属两张表**：
    日线表没有 ``period`` 列，派生周期表有。所有查派生周期的 SQL 都必须
    自行补 ``AND period=%s``——本函数只给表名，不给过滤条件。
    """
    if period == PERIOD_DAY:
        return KLINE_DAY_TABLE
    if period in DERIVED_PERIODS:
        return KLINE_PERIOD_TABLE
    raise ValueError(
        f"不支持的周期: {period!r}（可选：{', '.join(KLINE_PERIODS)}）")


# 日线表：列顺序**严格对齐 Tushare pro.daily 的输出字段顺序**
# （见 docs/tushare-reference.md §2.1 官网原文）：
#   ts_code, trade_date, open, high, low, close, pre_close, change,
#   pct_chg, vol, amount, ah_vol, ah_amount
# 改名 2 处：ts_code → code（项目内统一小写带市场前缀）；
#           change → chg_amt（`change` 是 MySQL 保留字，且与 chg_pct 对称）
# 单位换算：**不做**。vol 直接存手、amount 直接存千元，与 Tushare 原生口径一致，
# 免去每个读取方都要记得 ×100 / ×1000 的负担。
#   1 手 = 100 股          （成交量）
#   1 千元 = 1000 元（人民币）（成交额）
# 列名与顺序刻意贴着源接口，方便逐字段核对。
#
# ⚠️ 命名约束：`change` 被 MySQL 保留（ALTER TABLE ... CHANGE COLUMN 语法），
#    任何 SQL 里引用它都得加反引号 —— 与当年 `index` 表名那个坑同源。
#    故列名取 `chg_amt`，涨跌幅取 `chg_pct`，两个涨跌列统一 `chg_` 前缀。
#    同理 `vol` 也不作为列名（取 `volume`）。
#
# `_KLINE_DDL` 的 ah_vol / ah_amt **可空**（2026-10-13）：盘后固定交易不是每天都发生，
# 没有就是 NULL。0AMV 没有「盘后固定交易」这回事，用自己的 _KLINE_0AMV_DDL，不带这两列。
#
# 价格类列一律 DECIMAL(12,2) / chg_pct DECIMAL(10,2)（2026-10-09 从 4 位收窄）。
# 两条依据，缺一不可：
#   1. 源数据是 float32。0AMV 常用量级 ~152000 处，float32 的最小可分辨间隔
#      （ulp）= 2^17 × 2^-23 = 0.0156 —— **源数据本身只能分辨到 0.0156**，
#      存 4 位等于声称精度比来源细 150 倍，那 3、4 位小数是浮点残渣不是信息。
#   2. 行情软件显示涨跌幅只到 2 位（+1.13%），存更多没有出口。
# ⚠️ 别再"顺手"改回 4 位。想改之前先看一眼 ulp，别只看列名。
#
# ⚠️ 收窄精度会让派生列和价格列失去自洽：`round(a,2) - round(b,2)` 不等于
#    `round(a - b, 2)`，最多差一个末位单位（0.01）。所以 **chg_amt / chg_pct
#    必须按"已舍入的 close"重算**，否则 kline_0amv 实测有 184/8218 行
#    chg_amt ≠ close - 前一根 close。改精度的同一次迁移里要一并重算。
#
# 量额列的精度按**单位**定，不是随手抄的：
#   volume / ah_vol   手    -> DECIMAL(20,2)  2 位小数
#   amount / ah_amt   千元  -> DECIMAL(20,3)  3 位小数 = 精确到 1 元
# 千位制单位天然要多一位小数才落到「元」，这比统一 4 位有实际含义。
#
# ⚠️ 三张 K 线表的量额列各不相同，**不要互相参照**：
#   kline_day    volume/ah_vol (20,2)、amount/ah_amt (20,3)，单位 手 / 千元
#   kline_period volume/ah_vol (20,2)、amount/ah_amt (20,3)（周期内求和；
#                 0.01 的若干倍仍是 0.01 的倍数，求和不破坏精度）
#   kline_0amv   volume/amount BIGINT，单位 点（实测全是整数）
#
# ⚠️ 精度变更**不会**被 _run_migrations 传播——那个函数只 ADD 缺失的列，
#    不 MODIFY 已存在但类型不对的列。所以改精度必须同时手工 ALTER 线上表，
#    只改 DDL 会留下一张「schema 说 2 位、库里还是 4 位」的表。
_KLINE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    code        VARCHAR(16)  NOT NULL           COMMENT '标的代码，大写带市场前缀，SH.600570（见 tdx_kline.canonical_code）',
    trade_date  DATE         NOT NULL           COMMENT '交易日',
    open        DECIMAL(12,2) NOT NULL           COMMENT '开盘价，不复权',
    high        DECIMAL(12,2) NOT NULL           COMMENT '最高价，不复权',
    low         DECIMAL(12,2) NOT NULL           COMMENT '最低价，不复权',
    close       DECIMAL(12,2) NOT NULL           COMMENT '收盘价，不复权',
    pre_close   DECIMAL(12,2) NULL               COMMENT '已除权的前收盘，复权因子与筹码分布的关键输入；精度必须与 close 一致，否则 chg_amt = close - pre_close 会在舍入后不成立',
    chg_amt     DECIMAL(12,2) NULL               COMMENT '涨跌额，严格等于 close - pre_close（按已舍入的两列现算，不要用未舍入的原值）',
    chg_pct     DECIMAL(10,2) NULL               COMMENT '涨跌幅 %，基准是 pre_close；行情软件只显示到 2 位，存更多没有出口',
    volume      DECIMAL(20,2) NOT NULL DEFAULT 0 COMMENT '成交量，单位手（1 手 = 100 股），2 位小数',
    amount      DECIMAL(20,3) NOT NULL DEFAULT 0 COMMENT '成交额，单位千元（1 千元 = 1000 元），3 位小数 = 精确到 1 元',
    ah_vol      DECIMAL(20,2) NULL               COMMENT '盘后固定成交量，单位手（1 手 = 100 股）；NULL = 当日无盘后交易，不是 0',
    ah_amt      DECIMAL(20,3) NULL               COMMENT '盘后固定成交额，单位千元（1 千元 = 1000 元）；NULL = 当日无盘后交易，不是 0',
    -- 无二级索引：InnoDB 主键即聚簇索引，(code, trade_date) 已覆盖全部查询；
    -- MySQL 8 对升序聚簇索引支持反向扫描，ORDER BY trade_date DESC 无需额外索引
    -- （实测 EXPLAIN：ASC / DESC / 区间三类查询全部走 PRIMARY）。
    PRIMARY KEY (code, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# 指南针 0AMV（活跃市值）专属表：**不**复用 _KLINE_DDL。
#
# 与 kline_day 的三处结构差异，都是 0AMV 的业务性质决定的，不是偷懒：
#   1. 无 pre_close —— 0AMV 是「活跃筹码流通市值加总」，股本变动时价格反向等比、
#      市值恒定，不存在需要修正的除权断层，前收就是前一交易日 close（§1 of
#      docs/znz-active-mv-ocr-sync.md）。故只有 chg_amt / chg_pct，没有昨收列。
#   2. 无 ah_vol / ah_amt —— 没有「盘后固定交易」这回事。
#   3. volume / amount **不是**成交量额 —— 这两列存的是 day.vdat 里的原始值，
#      量纲与 0AMV 指数不同口径（见 docs/znz-active-mv-ocr-sync.md §2.1.3）。
#      列注释因此不写「手 / 千元」，避免与 kline_day 的同名列产生语义混淆。
#
# 值域校验：close 最大约 2.75e5、amount 最大约 3.3e12，均在 DECIMAL 容量内。
_KLINE_0AMV_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    code        VARCHAR(16)  NOT NULL           COMMENT '标的代码，0AMV 固定为 0AMV（首字符是数字 0）',
    trade_date  DATE         NOT NULL           COMMENT '交易日',
    open        DECIMAL(12,2) NOT NULL           COMMENT '开盘值，不复权（0AMV 无除权）',
    high        DECIMAL(12,2) NOT NULL           COMMENT '最高值，不复权',
    low         DECIMAL(12,2) NOT NULL           COMMENT '最低值，不复权',
    close       DECIMAL(12,2) NOT NULL           COMMENT '收盘值，不复权',
    chg_amt     DECIMAL(12,2) NULL               COMMENT '涨跌额，严格等于 close - 前一交易日 close（按已舍入的两列现算）',
    chg_pct     DECIMAL(10,2) NULL               COMMENT '涨跌幅 % = (close - 前一交易日 close) / 前一交易日 close；行情软件只显示到 2 位',
    volume      BIGINT       NOT NULL DEFAULT 0 COMMENT 'day.vdat 原始 volume，单位：点',
    amount      BIGINT       NOT NULL DEFAULT 0 COMMENT 'day.vdat 原始 amount，单位：点',
    -- 无二级索引：InnoDB 主键即聚簇索引，(code, trade_date) 已覆盖全部查询。
    PRIMARY KEY (code, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# 派生周期表：比日线表多一个 period 列，**核心列顺序与日线表逐列对齐**
# （code 之后插 period，其余完全一致），便于两端字段序号对齐。
#
# ⚠️ 2026-10-13 起 chg_amt / chg_pct 两列**正式启用**（此前 chg_pct 在 DDL 里
#    但从无写入，是死列；chg_amt 压根没这一列）。**原始口径**：基准是上一根
#    bar 的 close（物化 SQL 里的 LAG 中间量，不落库），与本表 OHLC 同一口径。
#    跨除权的周期会显示原始跳空——这是对的，adj=none 下图上画的就是原始价，
#    两者必须对得上。要看连续涨幅切前复权，那条路现算、不读本表。
#    同日 `pre_close` 列已删（见下方 DDL 的警告）。
#
#    ⚠️ 这里曾经存过「除权调整口径」（当时想与 kline_day.chg_pct 对齐），
#    已撤掉：那个口径和本表的原始 OHLC 不是一份东西，adj=none 时接口原样下发，
#    同一根 K 线会「图 +259.87%、数字 +369.75%」，年线上差 110 个百分点。
#
# ⚠️ 2026-10-13 起**也记录 ah_vol / ah_amt**（周期内求和，**可空**）：
#    桶内全无盘后交易时 SUM 返回 NULL，不会退化成 0。
#    注意它们目前**只写不读**：market._to_bars 不下发、前端零引用，kline_day 上
#    那两列同样如此。留着是为了日后要出「成交额中盘后占比」时不用重拉全历史。
#
# 精度与 kline_day 逐列对齐（2026-10-13 从 4 位收窄）。收窄前实测 1542 行里
#    价格/chg_pct/volume 的第 3 位、amount/ah_amt 的第 4 位**全为 0**，一行都没用上。
#    量额列的精度按**单位**定，与日线同：
#      volume / ah_vol   手   -> DECIMAL(20,2)
#      amount / ah_amt   千元 -> DECIMAL(20,3) = 精确到 1 元
#    周期内求和不破坏精度：0.01 的若干倍仍是 0.01 的倍数。
#
# ⚠️ 收窄精度后 chg_amt / chg_pct 必须按**已舍入**的 close 与基准现算
#    （`round(a,2) - round(b,2) != round(a - b, 2)`），见 _KLINE_DDL 顶部的警告。
#    chg_pct 是除法，必须显式 ROUND。
#
# ⚠️ 2026-10-13 删掉了 `pre_close` 列（14 → 13）：它恒等于上一根 bar 的 close，
#    完全能从表内 `LAG(close)` 推导，是纯冗余。涨跌两列在物化 SQL 里用派生表的
#    中间量算完即弃，不落库。**日线的 `pre_close` 语义完全不同**（已除权的昨收，
#    官方口径、推不出来、复权因子就靠它），那张表必须留着。
_KLINE_PERIOD_DDL = """
CREATE TABLE IF NOT EXISTS kline_period (
    code        VARCHAR(16)  NOT NULL           COMMENT '标的代码，大写带市场前缀，SH.600570',
    period      VARCHAR(8)   NOT NULL           COMMENT '派生周期：week / month / quarter / year',
    trade_date  DATE         NOT NULL           COMMENT '该周期最后一个交易日',
    open        DECIMAL(12,2) NOT NULL           COMMENT '周期首个交易日的开盘价',
    high        DECIMAL(12,2) NOT NULL           COMMENT '周期内最高价',
    low         DECIMAL(12,2) NOT NULL           COMMENT '周期内最低价',
    close       DECIMAL(12,2) NOT NULL           COMMENT '周期最后一个交易日的收盘价',
    chg_amt     DECIMAL(12,2) NULL               COMMENT '涨跌额，close - 上一周期 close；第一个周期为 NULL',
    chg_pct     DECIMAL(10,2) NULL               COMMENT '涨跌幅 %，基准是上一周期 close；第一个周期为 NULL',
    volume      DECIMAL(20,2) NOT NULL DEFAULT 0 COMMENT '成交量，单位手（1 手 = 100 股），周期内求和',
    amount      DECIMAL(20,3) NOT NULL DEFAULT 0 COMMENT '成交额，单位千元（1 千元 = 1000 元），周期内求和',
    ah_vol      DECIMAL(20,2) NULL               COMMENT '盘后固定成交量，单位手，周期内求和；NULL = 该周期内无盘后交易（目前只写不读）',
    ah_amt      DECIMAL(20,3) NULL               COMMENT '盘后固定成交额，单位千元，周期内求和；NULL = 该周期内无盘后交易（目前只写不读）',
    -- period 进主键：(code, trade_date) 不够——同一交易日在 week/month/quarter/
    -- year 四个桶里各有一根 bar，不带 period 会互相覆盖。
    PRIMARY KEY (code, period, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

#: 指南针 0AMV（活跃市值）专属日线表。表名**不在** KLINE_PERIODS 里：
#: 它不是通达信/腾讯那套「按周期分表」的成员，只是借用同一份 _KLINE_DDL 结构。
#: 见 docs/znz-active-mv-ocr-sync.md。
KLINE_0AMV_TABLE = "kline_0amv"

#: 哪些 K 线表**确实有** ``chg_amt`` / ``chg_pct`` 两列（判据是各自 DDL，别靠印象）。
#:
#: ``kline_day`` / ``kline_0amv`` 两张日线表都有，且是权威口径——前者直接来自
#: Tushare pro.daily，后者由 sync_znz 按「已舍入 close 减前一根 close」现算。
#:
#: ``kline_period`` 2026-10-13 起也是**整对都有**（此前 chg_amt 缺列、chg_pct 从无
#: 写入，读路径只能整组跳过，周期线的涨跌一律由前端现算）。现在由
#: :func:`kline_repo.materialize_periods` 从日线物化，基准是桶首日的日线
#: ``pre_close``（已除权），所以跨除权的周期不再出现假跳空。
#:
#: :func:`market.query_klines` 读库时按这个元组决定要不要 SELECT 它们——
#: 无条件加上会拿到 Unknown column（该判据原本就是为 0AMV / 周期表缺 chg_amt 而设的）。
TABLES_WITH_CHANGE_COLS = (KLINE_DAY_TABLE, KLINE_0AMV_TABLE, KLINE_PERIOD_TABLE)

#: 哪些 K 线表是**复权因子的来源**——注意语义是「来源」不是「有这一列」。
#:
#: 只有 ``kline_day`` 是来源：它是 Tushare pro.daily 直接给的「**已除权**前收盘」。
#: ``kline_0amv`` 刻意不设这一列：0AMV 是各股流通市值加总出来的**指数**，
#: 股本变动时价格反向等比、市值恒定，本来就没有除权除息这回事。
#: ``kline_period`` 自 2026-10-13 起**没有** ``pre_close`` 列了（原先有，但它
#: 恒等于上一根 bar 的 close，纯冗余，同日删除）。它从来不是因子输入，
#: 故不在此元组内——放进来会让 :func:`market.load_adjustment` 去按周期重算因子。
#:
#: :func:`market.load_adjustment` 要按它决定是否计算复权因子；
#: 无条件查会拿到 Unknown column 'pre_close'。
TABLES_WITH_PRE_CLOSE = (KLINE_DAY_TABLE,)

#: 存在**独立 K 线表**、不与个股共表的自研指数 code。
#:
#: 这些 code 的行情不在 ``kline_day`` 里，所以凡是「按 code 决定查哪张表」的地方
#: （例如 market.query_klines）必须走 :func:`table_for_code`，否则会查错表拿到空结果。
#: 实测教训：不加这个分支时 ``query_klines('0AMV')`` 返回 0 bars，而库里明明有 8219 行。
#:
#: 首字符是**数字 0**，不是字母 O。
DEDICATED_INDEX_CODES = ("0AMV",)


def is_dedicated_index(code: str) -> bool:
    """该 code 是否有独立 K 线表（不走 ``kline_day``）。"""
    return code in DEDICATED_INDEX_CODES


def table_for_code(code: str, period: str = PERIOD_DAY) -> str:
    """``(code, period)`` -> K 线表名。

    日线请求先看 code 是否是自研指数：是就返回它的专属表，否则回落到
    :func:`kline_table` 的通用映射。派生周期对自研指数**暂不支持**——
    ``kline_0amv`` 没有 ``period`` 列，也不参与 :func:`kline_repo.materialize_periods`。
    """
    if period == PERIOD_DAY and is_dedicated_index(code):
        return KLINE_0AMV_TABLE
    return kline_table(period)


SCHEMA_STATEMENTS = [
    # ---- K 线：日线一张（真相源）+ 派生周期一张（week/month/quarter/year 合并）----
    _KLINE_DDL.format(table=KLINE_DAY_TABLE),
    _KLINE_PERIOD_DDL,
    # ---- 指南针 0AMV 日线：独立结构（无 pre_close / 无 ah_*），见 _KLINE_0AMV_DDL ----
    _KLINE_0AMV_DDL.format(table=KLINE_0AMV_TABLE),
    """
    CREATE TABLE IF NOT EXISTS sync_state (
        code        VARCHAR(16) NOT NULL,
        period      VARCHAR(8)  NOT NULL,
        last_sync   DATETIME    NULL,
        first_date  DATE        NULL,
        last_date   DATE        NULL,
        status      VARCHAR(16) NOT NULL DEFAULT 'pending',
        message     VARCHAR(255) NULL,
        PRIMARY KEY (code, period)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # ---- 基本面（表设计见 docs/architecture.md §5.3）----
    #
    # 表名带 _info 后缀是为了跟 index_info 成对，且避开保留字——
    # 早先叫 `stock` / `index`，`index` 是 MySQL 保留字，每条 SQL 都得加反引号，
    # 忘一处就是 1064 syntax error。三个库（mysqldb / tq-stock / zx_invest）同批改名。
    """
    CREATE TABLE IF NOT EXISTS stock_info (
        code          VARCHAR(16) NOT NULL PRIMARY KEY,   -- sh600570
        raw_code      VARCHAR(8)  NOT NULL,               -- 600570
        market        TINYINT     NOT NULL,               -- 0=深 1=沪
        name          VARCHAR(64) NULL,                   -- 本地通达信 tnf；Tushare 有额度后由 sync_stock 覆盖
        pinyin        VARCHAR(32) NULL,                   -- 名称拼音首字母，恒生电子 -> hsdz，供键盘精灵模糊匹配
        ipo_date      DATE        NULL,                   -- get_finance_info.ipo_date
        total_share   DECIMAL(20,4) NULL,                 -- 总股本
        float_share   DECIMAL(20,4) NULL,                 -- 流通股本
        industry_code INT         NULL,                   -- 裸编码，get_block_info 不可用
        province_code INT         NULL,                   -- 裸编码
        updated_at    DATETIME    NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_finance (
        code        VARCHAR(16) NOT NULL,
        report_date DATE        NOT NULL,   -- get_finance_info.updated_date
        -- 资产负债
        total_asset        DECIMAL(20,4), current_asset        DECIMAL(20,4),
        fixed_asset        DECIMAL(20,4), intangible_asset     DECIMAL(20,4),
        total_liability    DECIMAL(20,4), current_liability    DECIMAL(20,4),
        net_asset          DECIMAL(20,4),
        -- 损益
        main_revenue       DECIMAL(20,4), operating_profit     DECIMAL(20,4),
        total_profit       DECIMAL(20,4), net_profit           DECIMAL(20,4),
        after_tax_profit   DECIMAL(20,4), undistributed_profit DECIMAL(20,4),
        -- 现金流
        op_cash_flow  DECIMAL(20,4), net_cash_flow       DECIMAL(20,4),
        -- 其他
        inventory      DECIMAL(20,4), accounts_receivable DECIMAL(20,4),
        shareholder_count   BIGINT,      per_share_net_asset  DECIMAL(12,4),
        PRIMARY KEY (code, report_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_xdxr (
        code        VARCHAR(16) NOT NULL,
        ex_date     DATE        NOT NULL,
        -- category 在 PK 里，MySQL 要求主键列一律 NOT NULL；缺失时用 0 占位
        category    TINYINT     NOT NULL DEFAULT 0,
        name        VARCHAR(64) NULL,
        -- 比例字段量纲统一为「每 10 股」
        bonus_ratio   DECIMAL(12,6) NULL,  -- songzhuangu 送转股
        allot_ratio   DECIMAL(12,6) NULL,  -- peigu 配股
        cash_dividend DECIMAL(12,6) NULL,  -- fenhong 现金分红
        reduce_ratio  DECIMAL(12,6) NULL,  -- suogu 缩股
        allot_price   DECIMAL(12,4) NULL,  -- peigujia 配股价
        share_before  DECIMAL(20,4) NULL,  -- qianzongguben  前总股本
        share_after   DECIMAL(20,4) NULL,  -- houzongguben   后总股本
        float_before  DECIMAL(20,4) NULL,  -- panqianliutong 前流通股本
        float_after   DECIMAL(20,4) NULL,  -- panhouliutong  后流通股本
        PRIMARY KEY (code, ex_date, category)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # ---- 指数与指标：单表 `index_info`，只装名录 ----
    #
    # 表名带 _info 后缀是为了跟 stock_info 成对。早先叫 `index`，那是 MySQL
    # 保留字，每条 SQL 都得写反引号，漏一处就是 1064 syntax error。
    #
    # 为什么指数**不进 stock_info 表**：stock_info 的语义是「沪深主板/创业板/
    # 科创板个股名录」，由 tools/backfill_stock_universe.py 按代码段白名单维护。
    # 那个脚本每次跑都会删掉白名单外的所有行，而 sh000xxx / sz399xxx 都不在
    # 白名单里，指数塞进去下次回填就被删光。而且 sh000001 是指数、sz000001
    # 是平安银行，跨市场同码段含义相反，白名单逻辑本身已经在这上面踩过一次。
    #
    # 这张表**只有名录，没有行情数据列**。三个量纲混在一列 concept 里，
    # 光看数字认不出来：
    #   sh000001 point  指数点位，约 3000~3600
    #   0AMV     yuan   活跃市值**金额**；code 首字符是**数字 0**
    #   mom      score  情绪**分数**，0-100，没有开高低收
    # 所以任何取数 SQL 都必须带 `WHERE code = %s`，不要对多 code 直接聚合。
    """
    CREATE TABLE IF NOT EXISTS index_info (
        code        VARCHAR(32) NOT NULL PRIMARY KEY,   -- sh000001 / sz399001 / 0AMV / mom
        name        VARCHAR(64) NOT NULL,               -- 上证指数 / 活跃市值指数 / 宝妈指数
        pinyin      VARCHAR(32) NULL,                   -- 拼音首字母，供键盘精灵搜索
        value_unit  VARCHAR(16) NOT NULL,               -- point=点位 yuan=元 score=0-100分
        data_shape  VARCHAR(16) NOT NULL,               -- ohlc=有开高低收 single=单值
        enabled     TINYINT     NOT NULL DEFAULT 1,     -- 0=停用，前端不展示但保留数据
        remark      VARCHAR(255) NULL,
        updated_at  DATETIME    NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # 2026-10-09 精简掉 3 列 + 改 1 个列名（库与本 DDL 同批变更）：
    #   short_name  删。简称只在两个地方需要，都不在这张表：
    #               前端 web/src/utils/indices.ts 自带一份 short 映射，
    #               API 也不吐 short。存一份必然和前端那份漂移。
    #   full_name   改名 name。原名跟 stock_info 的 name 不一致，
    #               而这张表和那张表本来就是成对的（都是"名录"语义）。
    #   source      删。只是个手工维护的字符串标识，没有外键、没有代码读它；
    #               数据出处记在 remark 里更贴近实际语义。
    #   sort_order  删。前端展示顺序属于前端的事（INDICES 数组已经定死了顺序），
    #               存在库里就得有人维护，而 seed_indexes 每次跑都会把它覆写回去。
    #   ⚠ 删列后所有 ORDER BY 一律退化成 ORDER BY code，行情列表页的指数顺序
    #     会从「上证→深成→创业板→科创50→0AMV→宝妈」变成按 code 字母序。

    # ref_industry / ref_province 已于 2026-10-07 删除。
    # 原因：三个库中从未写入过一行数据；没有任何 API 读取；唯一写入函数
    # upsert_ref 从未被调用。更重要的是 tdx2db 的板块数据是
    # 「板块↔个股多对多关系」（136461 条），而这两张表是
    # 「编码→名称」的一对一映射（code INT PK），结构根本不承载该语义。
    # 将来若要做行业/概念筛选，应新建板块关系表，而不是复活这两张映射表。
    #
    # 原先还有一张 kline_backup（verify_query_paths.py 的「临时台账」），已于
    # 2026-10-07 一并删除：代码里从未往它写过数据。verify_query_paths.py:36 的
    # INSERT 目标写的是 kline 本身（该行注释「备份到 kline_backup」与 SQL 不符），
    # 恢复用的是删除前抓在内存里的 last 字典。删表不影响脚本行为。

    # ---- 应用级配置（key-value），给前端显示设置持久化用 ----
    #
    # 目前只有一项：show_price_line —— 主图现价线显不显示。
    # 纯内存 state 刷新页面就没了，用户改过的设置得手动改回去，所以落库。
    #
    # 为什么是 key-value 而不是「一个设置项一列」：显示设置会持续增加，
    # 一列一设置就得每次 ALTER TABLE；key-value 加一项只是 INSERT 一行，
    # 接口和前端都不用动。
    #
    # 为什么没有 user_id 列：项目还没有用户系统，是单人的本机工具。
    # 真要上多用户，给这张表加一列 user_id、主键改成 (user_id, item_key) 就行，
    # 结构不用推翻。
    #
    # item_value 一律存字符串：设置项类型各异（布尔 / 数字 / JSON），
    # 统一按文本存、由前端解析，比在库里给每种类型分列省事。
    # 用 NOT NULL 是为了让「配错了」能报错，而不是和 NULL 混成"没配过"。
    #
    # ⚠ 行不存在 = 用前端默认值。所以首次使用**不需要**预置种子行，
    #   空表也能正常工作。
    """
    CREATE TABLE IF NOT EXISTS app_config (
        item_key   VARCHAR(64)  NOT NULL PRIMARY KEY,  -- 设置项名，如 show_price_line
        item_value VARCHAR(255) NOT NULL,               -- 值，统一存字符串，由前端解析
        updated_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]

# 幂等加列迁移。CREATE TABLE IF NOT EXISTS 不会给已存在的表补列，
# 而 MySQL 语法没有 ADD COLUMN IF NOT EXISTS，因此先查 information_schema 再决定。
MIGRATIONS: list[tuple[str, str, str]] = [
    ("sync_state", "first_date", "DATE NULL"),
    # 拼音首字母：键盘精灵要能输 "hsdz" 命中「恒生电子」。
    # 生成方式见 stock_repo.pinyin_initials（pypinyin），只依赖 stock_info.name，
    # 所以 name 被 sync_stock 覆盖后重跑一次回填脚本即可同步。
    ("stock_info", "pinyin", "VARCHAR(32) NULL"),
    # pre_close / chg_pct 已写进各自的 _KLINE_*_DDL 基础 DDL，新库开箱即有。
    # 这里仍按每张表登记一次，只为兜住「表已存在但列还没加」的中间状态
    # （例如分表改造期间手工建过表）。检查走 information_schema，幂等。
    #
    # ⚠️ **两张表的列集合刻意不同**，别再用「for col in (a, b) for table in (d, p)」
    # 的写法一把梭——那样删一侧 DDL 时很容易只改一半，迁移会**把已删的列悄悄加回来**。
    # 2026-10-13 就踩过：kline_period 已删 pre_close，这个循环没同步，
    # 任何进程跑一次 init_schema() 就把它复活（ADD COLUMN 落在末位，肉眼看像新列）。
    #   kline_day    有 pre_close（已除权昨收，官方值，推不出来）
    #   kline_period 无 pre_close（恒等于上一根 close，纯冗余，浮窗回落到前一根 close）
    # chg_pct 两张表都有，仍走循环。
    (KLINE_DAY_TABLE, "pre_close", "DECIMAL(12,4) NULL"),
    *[
        (table, "chg_pct", "DECIMAL(10,4) NULL")
        for table in (KLINE_DAY_TABLE, KLINE_PERIOD_TABLE)
    ],
    # 2026-10-13：kline_period 启用 chg_amt，此前整表没有这一列。
    # 与 pre_close / chg_pct 配套——前端 CrosshairTooltip 是**逐字段**回落的
    # （extra?.chgAmt ?? localChange），只给 chg_pct 不给 chg_amt 会让同一行
    # 显示「涨跌幅 +2.20%、涨跌额 -23.42」这种自相矛盾的两行，比全错更糟。
    (KLINE_PERIOD_TABLE, "chg_amt", "DECIMAL(12,2) NULL"),
    # 2026-10-13：kline_period 也记录盘后固定交易量额（周期内求和）。
    # 精度按单位定，与 kline_day 同：手 -> 2 位、千元 -> 3 位（精确到 1 元）。
    # ⚠️ 目前**只写不读**（_to_bars 不下发、前端零引用），kline_day 上那两列
    #    同样如此。留着是为了日后要出「盘后成交占比」时不用重拉全历史。
    (KLINE_PERIOD_TABLE, "ah_vol", "DECIMAL(20,2) NULL"),
    (KLINE_PERIOD_TABLE, "ah_amt", "DECIMAL(20,3) NULL"),
    # 2026-10-09 补齐 pro.daily 的三个字段，列序随之对齐官方接口。
    *[
        (KLINE_DAY_TABLE, col, coldef)
        for col, coldef in (
            # chg_amt ≡ close - pre_close，实测与源接口差值恒为 0，纯冗余但为对齐字段序保留
            ("chg_amt", "DECIMAL(12,2) NULL"),
            # 盘后固定交易量额。⚠️ 可空：**盘后交易不是每天都发生**，
            # 没有就是 NULL，不是 0——两者语义不同，0 会被误读成「有交易但为零」。
            ("ah_vol", "DECIMAL(20,2) NULL"),
            ("ah_amt", "DECIMAL(20,3) NULL"),
        )
    ],
]


async def _run_migrations(cur) -> list[str]:
    """对已存在的表补齐新列，返回实际执行的语句列表。"""
    applied = []
    for table, column, coldef in MIGRATIONS:
        await cur.execute(
            """SELECT COUNT(*) AS n FROM information_schema.columns
               WHERE table_schema=DATABASE() AND table_name=%s AND column_name=%s""",
            (table, column))
        row = await cur.fetchone()
        if row and int(row[0]) == 0:
            # 表名和列名都加反引号：拼接点不加就等于赌它们永远不是保留字。
            # `index`（表名）和 `change`（列名）已经各赌输过一次。
            stmt = f"ALTER TABLE `{table}` ADD COLUMN `{column}` {coldef}"
            await cur.execute(stmt)
            applied.append(stmt)
    return applied


async def init_pool() -> aiomysql.Pool:
    """创建全局连接池（幂等）。"""
    global _pool
    if _pool is None:
        _pool = await aiomysql.create_pool(
            minsize=config.MYSQL_POOL_MIN,
            maxsize=config.MYSQL_POOL_MAX,
            autocommit=True,
            charset="utf8mb4",
            **config.dsn(),
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None


def pool() -> aiomysql.Pool:
    if _pool is None:
        raise RuntimeError("连接池未初始化，请先 await init_pool()")
    return _pool


async def init_schema() -> list[str]:
    """建表 + 补列（均幂等）。返回本次实际执行的迁移语句。"""
    async with pool().acquire() as conn:
        async with conn.cursor() as cur:
            for stmt in SCHEMA_STATEMENTS:
                await cur.execute(stmt)
            return await _run_migrations(cur)


async def healthcheck() -> bool:
    try:
        async with pool().acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()
        return True
    except Exception:
        return False
