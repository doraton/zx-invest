"""MySQL → K 线查询。

K 线分两张表：``kline_day`` 是唯一真相源，直接读表；``kline_period`` 把
``week`` / ``month`` / ``quarter`` / ``year`` 四个派生周期合并在一张表里、
用 ``period`` 列区分，由 ``sync.py`` 用 SQL 从日线**物化**，
``trade_date`` 取周期**最后一个交易日**
（与通达信原生非日线的日期语义一致，见 docs/architecture.md §5.2）。

查询时若派生周期表内尚无该周期数据（还没跑过物化，或该标的没同步过），
自动回退到「查询时用窗口函数现场聚合」，口径与物化完全一致。
"""
import aiomysql

from server import db, kline_repo, stock_repo
from server.db import KLINE_PERIODS, PERIOD_DAY

PERIODS = KLINE_PERIODS

#: 单次请求返回的根数硬上限。
#: 1000 根 ≈ 4.1 年日线（按 A 股 238 交易日/年折算），单次响应 gzip 后约 30KB。
#: 原先这里写死 5000，是为分表前的单表 kline 设的；分表后主键已是 (code, trade_date)，
#: 行更窄，1000 足够且能把「前端误传超大 count」的后果控制住。
#: 真正被前端整屏显示的上限是另一个数（已加载总量），别和这里混。
MAX_COUNT = 1000

#: 涨跌两列。**只对** db.TABLES_WITH_CHANGE_COLS 里的表 SELECT。
#: 逗号算进常量里，模板里就不用留空位。
#: 2026-10-13 起 kline_period 也在该元组内（此前它缺 chg_amt，整组被跳过）。
_CHANGE_COLS = ", chg_amt, chg_pct"

#: ``pre_close`` 只下发给 **kline_day**。它是交易所给的**已除权**昨收，
#: 浮窗要拿它当「昨收」栏——否则除权日会用前一根原始 close 当基准，
#: 和库里 ``chg_pct`` 对不上（2026-06-09：图 +0.04%、数字 +0.95%）。
#: 派生周期不需要：它的昨收就是前一根 close，前端从数组里直接取。
_PRE_CLOSE_SELECT = ", pre_close"


def _period_chg_cols(base_col: str = "prev_raw") -> str:
    """周期 bar 的 ``chg_amt`` / ``chg_pct`` 两个 SELECT 项。

    ⚠️ 这里的 ``{chg_cols}`` **必须放在最外层、聚合之后的派生表上**，
    因为涨跌基准要拿**上一个桶**的 close（``LAG``），在桶内窗口里根本看不到邻居。

    口径与 :func:`kline_repo._materialize_sql`（落库版）**严格一致**——
    两条路径必须同口径，否则同一个接口里「读物化行」和「现场聚合行」
    会给出不同的涨跌幅。

    :param base_col: **基准列名**，由调用方的派生表产出。
        - ``prev_raw``（默认，_AGGREGATE_BARS 复权档无关的那条路）：
          上一根桶的原始收盘。与物化表同口径。
        - ``pre_close``（_ADJ_AGGREGATE_BARS）：已折算到复权价位的基准。

    - 基准为 NULL 时两列都写 NULL（最早那个桶，``LAG`` 天然给 NULL）。
    - 两列都按**已舍入**的 close 与基准现算，收窄精度后
      ``round(a,2) - round(b,2) != round(a - b, 2)``，顺序不能反。
    """
    return f"""
           CASE WHEN {base_col} IS NULL OR {base_col} = 0 THEN NULL
                ELSE ROUND(close - {base_col}, 2) END AS chg_amt,
           CASE WHEN {base_col} IS NULL OR {base_col} = 0 THEN NULL
                ELSE ROUND((close / {base_col} - 1) * 100, 2) END AS chg_pct"""


#: 复权档的基准列（_ADJ_AGGREGATE_BARS 用）：折算到复权价位的「上一周期收盘」。
#: ⚠️ 必须在内层算：MySQL 不允许在同一个 SELECT 列表里引用另一项的别名，
#:    而 _period_chg_cols 的 CASE 正是引用它。
#: ⚠️ 这里**不再套 ROUND**：expr 里已经带了 ROUND(...,2)——那是给**原始价位**的
#:    基准做舍入（与物化表同口径）。复权路径的 expr 末尾还要乘 fac 换算到复权口径，
#:    若这里再舍一次，前复权价只有 0.90 量级，2 位小数直接吃掉 0.2 个百分点
#:    （实测 2007-01-26 那周 chg_pct 从 2.12 歪到 2.32）。
_PRE_CLOSE_COL = """CASE WHEN {expr} IS NULL THEN NULL ELSE ({expr}) END AS pre_close"""

#: 不复权 / 回退路径的基准列（_AGGREGATE_BARS 用）：直接 LAG 上一根**桶**的收盘。
#: 别名是 ``prev_raw`` 而不是 ``pre_close`` —— 派生周期表 2026-10-13 起没有
#: pre_close 列了，这只是个派生表里的中间量，算完即弃、不落库。
#: 最早那个桶由 LAG 天然给 NULL，不需要额外 CASE。
_LAG_PREV_RAW = "LAG(close) OVER (ORDER BY trade_date) AS prev_raw"


_SELECT_BARS = """
SELECT trade_date, open, high, low, close, volume, amount{change_cols}
FROM {table} WHERE code=%s{where}
ORDER BY trade_date {order} LIMIT %s
"""

# 现场聚合（不复权 / 回退路径）：与 kline_repo._materialize_sql 同构，只是
# 不落库、直接取结果。永远从日线表出发。**口径必须与物化表严格一致**——
# 同一个接口里「读物化行」和「现场聚合行」要给出同一个答案。
#
# 两层结构不能压平：涨跌的基准要拿**上一个桶**的 close（LAG），
# 必须先做完桶内聚合、再在外层 LAG，压平了桶内窗口就看不到邻居。
#
# ⚠️ 与物化表一样**不产出 pre_close**：那是「上一根 close」的同义词，
#    纯冗余。基准只以派生表中间量 prev_raw 参与 chg 计算，算完即弃。
#
# ⚠️ start_date / end_date 过滤**必须放在最外层**（而不是日线子查询上）：
#    请求区间里第一个桶的基准来自区间**之外**的上一个桶，
#    内层先过滤会让那个桶的 LAG 取到 NULL、涨跌两列整列丢头。
_AGGREGATE_BARS = """
SELECT trade_date, open, high, low, close, volume, amount,
       {chg_cols}
FROM (
    SELECT b.*, {prev_close_col}
    FROM (
        SELECT DISTINCT code, MAX(trade_date) OVER w AS trade_date,
               FIRST_VALUE(open)  OVER w AS open, MAX(high) OVER w AS high,
               MIN(low) OVER w AS low, LAST_VALUE(close) OVER w AS close,
               SUM(volume) OVER w AS volume, SUM(amount) OVER w AS amount,
               SUM(ah_vol) OVER w AS ah_vol, SUM(ah_amt) OVER w AS ah_amt
        FROM (SELECT *, {bucket} AS pk FROM {src} WHERE code=%(code)s) g
        WINDOW w AS (PARTITION BY pk ORDER BY trade_date
                     ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
    ) b
) x
{where}
ORDER BY trade_date {order}
LIMIT %s
"""

#: 带复权的派生周期：**先逐根日线乘因子，再聚合**。
#
# 与 _AGGREGATE_BARS 的差别只有 ``* fac`` / ``/ fac`` 这几处，但顺序是本质的：
# 物化表存的是**未复权**聚合值，若先聚合再整体乘一个因子，桶内含除权日时
# MAX/MIN 取到的是「除权前的最高价」和「除权后的最低价」——两者不在同一口径，
# 乘哪个系数都不对（实测 2021-07-16 那一周 high 虚高 34%）。
# 只有桶内无除权日时两种顺序才等价（因子在桶内恒定，max(k·h)=k·max(h)）。
#
# ``fct`` 是现算出来的因子表（每个除权日一行），按 ``td <= trade_date`` 取最近一条，
# 所以表里存的已经是**最终因子**：hfq 用累积因子本身，qfq 已除以全库末根因子。
# 聚合完不必再调 _apply_adj。
#
# ⚠️ 桶级那层**必须带 DISTINCT**（窗口函数按整帧算出来，桶内每行取值相同，
#    不去重的话 N 行会进到下一层的 LAG 里，同桶内互相成为「上一桶」，
#    pre_close 会变成自己的 close、chg_pct 恒为 0）。
#    不复权那条路（_AGGREGATE_BARS）的 bucket CTE 本来就带 DISTINCT。
_ADJ_AGGREGATE_BARS = """
WITH fct AS ({fct})
SELECT trade_date, open, high, low, close, volume, amount,
       pre_close,
       {chg_cols}
FROM (
    SELECT p.*, {pre_close_col}
    FROM (
        SELECT b.*,
               -- ⚠️ LAG 的是**原始（未折算）收盘**，不是折算后的。
               --    chg_pct 必须与物化表逐根同值，而物化表是
               --        close[原始] / ROUND(close[上桶末]×f[上桶末]/f[本桶末], 2) − 1
               --    基准在**原始价位**上先舍入再参与比值。
               --    若直接 LAG 折算后的 close 再 ROUND(...,2)：前复权价只有
               --    2.5 量级，保留 2 位小数就等于丢掉 0.4 分之一个百分点
               --    （实测 2008-05-30 那周 chg_pct 从 -2.77 歪到 -2.83）。
               LAG(last_raw_close) OVER (ORDER BY trade_date) AS prev_raw_close,
               LAG(fac_last)        OVER (ORDER BY trade_date) AS fac_prev
        FROM (
            SELECT DISTINCT MAX(trade_date) OVER w AS trade_date,
                   FIRST_VALUE(open)  OVER w AS open, MAX(high) OVER w AS high,
                   MIN(low) OVER w AS low, LAST_VALUE(close) OVER w AS close,
                   LAST_VALUE(raw_close) OVER w AS last_raw_close,
                   SUM(volume) OVER w AS volume, SUM(amount) OVER w AS amount,
                   LAST_VALUE(fac) OVER w AS fac_last
            FROM (
                SELECT k.trade_date,
                       k.open * fac AS open, k.high * fac AS high,
                       k.low * fac AS low, k.close * fac AS close,
                       k.volume / fac AS volume, k.amount AS amount,
                       -- raw_close / fac 要透传到桶级，LAG 用它算上一桶原始收盘
                       k.close AS raw_close, fac,
                       {bucket} AS pk
                FROM (
                    SELECT g.trade_date, g.open, g.high, g.low, g.close,
                           g.volume, g.amount,
                           COALESCE((SELECT f.fac FROM fct f
                                      WHERE f.td <= g.trade_date
                                      ORDER BY f.td DESC LIMIT 1), 1) AS fac
                    FROM {src} g WHERE g.code=%s
                ) k
            ) t
            WINDOW w AS (PARTITION BY pk ORDER BY trade_date
                         ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
        ) b
    ) p
) q
{where}
ORDER BY trade_date {order}
LIMIT %s
"""


def _parse_date(s: str | None) -> str | None:
    """把 YYYY-MM-DD / YYYYMMDD 归一成 YYYY-MM-DD；非法值返回 None 走全量。"""
    if not s:
        return None
    t = str(s).strip().replace("/", "-")
    if len(t) == 8 and t.isdigit():
        return f"{t[:4]}-{t[4:6]}-{t[6:]}"
    return t if len(t) == 10 and t[4] == "-" and t[7] == "-" else None


async def query_klines(code: str, period: str = PERIOD_DAY, count: int = 250,
                       start_date: str | None = None,
                       end_date: str | None = None,
                       adj: str = "none") -> tuple[list[dict], bool]:
    """按时间升序返回 K 线。返回 (bars, truncated)。

    游标语义（前端向左加载历史时用）：

    - 都不传                     -> 库里**最近** count 根
    - 只传 end_date              -> 该日期**及之前**的 count 根（往历史方向翻页）
    - 只传 start_date            -> 该日期**及之后**的 count 根（补最新数据）
    - 两个都传                   -> 区间内，最多 count 根（闭区间）
    - 两者都给                   -> 起止日期都降级为最新时，等同于不传

    :param count: 返回根数上限，硬上限 ``MAX_COUNT``；超出只截断并置 ``truncated``，
        不报错。前端必须检查 truncated，否则会以为那就是全部。
    :param adj: ``none`` / ``qfq`` / ``hfq``，见 :data:`ADJ_MODES`。
        非法值抛 ValueError，不静默回落成不复权。
    """
    if adj not in ADJ_MODES:
        raise ValueError(f"不支持的复权模式: {adj!r}（可选 {', '.join(ADJ_MODES)}）")
    # 表名按 (code, period) 决定：自研指数（0AMV）有独立表，不在 kline_day 里。
    # 漏掉这一步会让 query_klines('0AMV') 查 kline_day -> 返回 0 bars。
    # kline_table 内部会对未知周期抛 ValueError，这里不再重复校验。
    table = db.table_for_code(code, period)
    dedicated = db.is_dedicated_index(code)
    if dedicated and period != PERIOD_DAY:
        # kline_0amv 无 period 列，也不参与物化；现场聚合会退化成查 kline_day，
        # 对 0AMV 永远返回空。直接报错比静默返回空列表好排查。
        raise ValueError(
            f"{code} 只有日线，不支持派生周期 {period!r}（kline_0amv 无 period 列，"
            f"也不参与物化）")
    n = max(1, min(int(count), MAX_COUNT))
    sd, ed = _parse_date(start_date), _parse_date(end_date)

    where, params = [], [code]
    if sd and ed and sd > ed:          # 用户把起止写反了，交换而不是报错
        sd, ed = ed, sd
    if sd:
        where.append("AND trade_date >= %s")
        params.append(sd)
    if ed:
        where.append("AND trade_date <= %s")
        params.append(ed)
    where_sql = (" " + " ".join(where)) if where else ""

    # 向前翻历史从尾部截，反之从头截；都取完再统一成正序返回给前端。
    order = "ASC" if sd and not ed else "DESC"
    params.append(n)

    # 多取一根用来判断是否还有更早/更新的数据（truncated），代价是 1 行。
    fetch = n + 1
    params[-1] = fetch

    # 读库路径要按 period 列收窄——派生周期表里同一交易日有 4 个周期的 bar。
    # 但**现场聚合永远从 kline_day 起**，那张表没有 period 列，所以这两条 SQL
    # 的 WHERE 不能共用：共用会让聚合 SQL 报 Unknown column 'period'。
    # pfilter 为空表示日线，此时 where_read_sql / params_read 与原路径完全一致。
    # 必须在 fetch 就位之后再复制 params，否则末位日期参数会被 fetch 顶掉。
    pfilter, pfilter_params = kline_repo.period_filter(period)
    where_read_sql = pfilter + where_sql if pfilter else where_sql
    params_read = [code, *pfilter_params, *params[1:]]

    # 涨跌两列只有部分表有（见 db.TABLES_WITH_CHANGE_COLS）。
    # pre_close 只有 kline_day 有：kline_0amv 没「除权昨收」这概念，
    # kline_period 那列 2026-10-13 已删（它恒等于上一根 close，纯冗余）。
    # 无条件 SELECT 会 Unknown column，所以按表分别拼。
    change_cols = _CHANGE_COLS if table in db.TABLES_WITH_CHANGE_COLS else ""
    if table == db.KLINE_DAY_TABLE:
        change_cols = _PRE_CLOSE_SELECT + change_cols

    # 复权因子来自 kline_day：日线在聚合之后乘因子（_apply_adj），
    # 派生周期必须在聚合**之前**逐根乘（_ADJ_AGGREGATE_BARS）。
    adjustment = await load_adjustment(code)
    # 除权标记判据：周期 bar 的 trade_date 是桶内最后一个交易日，
    # 所以要问的是「它那个桶里有没有除权日」，不是「这一行是不是除权日」。
    # 顺带把**真实**除权日带出去，弹窗高亮要用它而不是桶尾日期。
    ex_buckets = await _ex_bucket_dates(code, period, adjustment)

    def tag(rows: list[dict], rev: bool) -> tuple[list[dict], bool]:
        # 多取的那一根（fetch = n+1）只用来置 truncated，不能进 bars
        truncated = len(rows) > n
        bars = _to_bars(rows[:n], rev)
        for b in bars:
            xd = ex_buckets.get(b["datetime"])
            b["is_xdxr"] = xd is not None
            if xd is not None:
                b["xdxr_date"] = xd
        return bars, truncated

    # 复权派生周期：不能读物化表（那是未复权聚合值），走带因子的现场聚合。
    # 代价是每次都扫全量日线，但只有除权日那几十行需要因子，SQL 里一次查完。
    if period != PERIOD_DAY and adj != "none" and adjustment.has_ex:
        bucket = kline_repo.PERIOD_BUCKET_SQL.get(period)
        if bucket is None:
            raise ValueError(f"不支持的派生周期: {period!r}")
        sql = _ADJ_AGGREGATE_BARS.format(
            fct=_factor_cte_sql(adjustment, adj), bucket=bucket,
            # 这条路上每根日线已乘 fac，桶末 close 就是**折算后**的收盘，
            # 它本身就是上桶末在本桶末口径下的值，直接当 pre_close 用。
            # 因子里已含 qfq 的分母，但 chg_pct 是比值、分母约掉，三种模式同值。
            # 基准先在**原始价位**上舍入、再乘 fac_cur 折算到复权口径，
            # 这样 chg_pct / chg_amt 与物化表逐根同值（两处都是同一条公式）。
            pre_close_col=_PRE_CLOSE_COL.format(
                expr="ROUND(p.prev_raw_close * p.fac_prev / p.fac_last, 2) "
                     "* p.fac_last"),
            # 复权档的基准列名叫 pre_close（见 _PRE_CLOSE_COL），
            # 与不复权那条路的 prev_raw 不同名，两条路径不能混用。
            chg_cols=_period_chg_cols(base_col="pre_close"),
            src=db.kline_table(PERIOD_DAY), where=where_sql, order=order)
        async with db.pool().acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # 注意 {where} 现在挂在**外层**（见模板注释）：基准的
                # LAG 需要区间之外的上一个桶，内层过滤会让首根基准变 NULL。
                await cur.execute(sql, (code, *params[1:len(params) - 1], fetch))
                data = await cur.fetchall()
        # 因子已在 SQL 里作用到每根日线并聚合成型，这里不能再 _apply_adj。
        return tag(data, order == "DESC")

    def finish(rows: list[dict], rev: bool) -> tuple[list[dict], bool]:
        bars, truncated = tag(rows, rev)
        return _apply_adj(bars, adjustment, adj), truncated

    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            if period != PERIOD_DAY:
                # 探空必须带 period：表里有月线行不代表有周线行，
                # 只按 code 探会让所有非日线周期都误判成「已物化」而跳过现场聚合。
                await cur.execute(
                    f"SELECT 1 FROM {table} WHERE code=%s{pfilter} LIMIT 1",
                    [code, *pfilter_params])
                if await cur.fetchone() is None:
                    bucket = kline_repo.PERIOD_BUCKET_SQL.get(period)
                    if bucket is None:
                        raise ValueError(f"不支持的派生周期: {period!r}")
                    # pre_close = 上一根桶的收盘（LAG），原始未复权，
                    # 与 kline_repo._materialize_sql 落库的那份**逐字同口径**。
                    # 具名占位符 %(code)s 在这里转成位置占位符。
                    agg = _AGGREGATE_BARS.format(
                        src=db.kline_table(PERIOD_DAY), bucket=bucket,
                        prev_close_col=_LAG_PREV_RAW,
                        chg_cols=_period_chg_cols(),
                        where=where_sql, order=order)
                    await cur.execute(
                        agg.replace("%(code)s", "%s"),
                        (code, *params[1:len(params) - 1], fetch))
                    return finish(await cur.fetchall(), order == "DESC")

            await cur.execute(
                _SELECT_BARS.format(table=table, change_cols=change_cols,
                                    where=where_read_sql, order=order),
                              params_read)
            data = await cur.fetchall()
    return finish(data, order == "DESC")


def _opt_float(v) -> float | None:
    """DB 值 -> float | None。``None`` 是**有效值**（首行无前一根就没涨跌），
    不能当 0 返回——0 会被读成「当日没涨跌」。"""
    return None if v is None else float(v)


# ---------------------------------------------------------------------------
# 复权
# ---------------------------------------------------------------------------
#: 复权模式。``none``=不复权（库里原值）；``qfq``=前复权（末根不动，历史往前压）；
#: ``hfq``=后复权（首根不动，历史往后抬）。
#:
#: **因子不落库**，查询时现算。累积量落库会破坏 upsert 的列独立性——
#: 每天新拉一根都要重写全表因子，一次历史纠错就会静默污染后面所有行。
#: 详见 docs/adjustment-principle.md。
ADJ_MODES = ("none", "qfq", "hfq")

#: 判定「除权日」的容差。pre_close 与 close 都是 DECIMAL(…, 4)，非除权日
#: 理论上应严格相等，但四舍五入后仍可能差 1e-5 量级；容差过紧会把普通交易日
#: 误判成除权日（凭空多出一个标记和一段错因子），过松则漏掉小额定息。
_EX_EPS = 1e-4

#: 取除权日及其复权比值。
#:
#: ``LAG`` 必须包在子查询里、且子查询的 WHERE **只按 code 过滤**：
#: 若在外层先滤 ``pre_close IS NOT NULL`` 再算 LAG，前一根就会跳过那些行，
#: 除权日的 prev_close 取错成更早的一根 close，比值整个失真。
#:
#: 服务端扫全量行但只回传除权日（恒生电子 23 行 / 5435 行），
#: 网络上只走结果集，Python 侧再累乘成因子。
_EX_RATIOS_SQL = """
SELECT trade_date, pre_close, prev_close, prev_close / pre_close AS ratio
FROM (
    SELECT trade_date, close, pre_close,
           LAG(close) OVER (ORDER BY trade_date) AS prev_close
    FROM {table} WHERE code=%s
) t
WHERE pre_close IS NOT NULL AND pre_close > 0 AND prev_close IS NOT NULL
  AND ABS(prev_close - pre_close) > %s
ORDER BY trade_date
"""


class Adjustment:
    """一只标的的复权因子与除权日。**查询时现算，不落库。**

    只存除权日那几十行（``ex_dates``），因子按需二分查找：

    - :meth:`factor` 返回**累积**因子，方向递增（每遇一次除权乘 ``prev_close/pre_close``）。
      取倒数方向（``pre_close/prev_close``）会让后复权在除权日**向下断**，
      与 docs/adjustment-principle.md §1 的局部 ratio 定义正好相反，
      照 §3 连乘会前后颠倒——这是文档与实现必须对齐的一个点。
    - 前复权再额外除以**窗口内最后一根**的因子，使末根 close 与库中原值相等。
    """

    __slots__ = ("ex_dates", "_dates", "_cum", "last_date")

    def __init__(self, ex_dates: list[dict], last_date: str | None = None):
        # ex_dates: [{date, ratio, pre_close, prev_close, factor}]，按日期升序
        self.ex_dates = ex_dates
        self._dates = [e["date"] for e in ex_dates]
        self._cum = [e["factor"] for e in ex_dates]
        self.last_date = last_date

    @property
    def has_ex(self) -> bool:
        return bool(self.ex_dates)

    def factor(self, date: str) -> float:
        """截至 ``date``（含当天）的累积因子。日期之前没有除权日则为 1.0。"""
        import bisect
        i = bisect.bisect_right(self._dates, date)
        return self._cum[i - 1] if i else 1.0

    def is_ex_date(self, date: str) -> bool:
        import bisect
        i = bisect.bisect_right(self._dates, date)
        return bool(i and self._dates[i - 1] == date)

    def qfq_base(self) -> float:
        """前复权的分母：**全库最后一根**的累积因子。

        锚定「本次返回窗口的最后一根」会让同一根 bar 随请求窗口漂移——
        实测同一段时间、窗口末根取除权日的前一天 vs 当天，1000 根里 999 根的
        前复权价不同、最大差 40%。前端往回翻页时两批数据基准不同，
        接缝处会裂开一个假跳空。锚定全库最新交易日则每根 bar 的值唯一且稳定。
        """
        return self.factor(self.last_date) if self.last_date else 1.0


async def load_adjustment(code: str) -> Adjustment:
    """读 ``kline_day`` 的 pre_close，算出该标的的复权因子与除权日。

    无 ``pre_close`` 的标的（0AMV 自研指数）直接返回空 —— 市值加总口径不存在除权。
    """
    table = db.kline_table(PERIOD_DAY)
    if table not in db.TABLES_WITH_PRE_CLOSE or db.is_dedicated_index(code):
        return Adjustment([])
    last_sql = f"SELECT MAX(trade_date) AS last_date FROM {table} WHERE code=%s"

    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(_EX_RATIOS_SQL.format(table=table), [code, _EX_EPS])
            rows = await cur.fetchall()
            # 前复权基准：全库最后一个交易日。走 (code, trade_date) 主键的索引最大值，
            # 跟主查询共用同一个连接借还周期，不额外往返。
            await cur.execute(last_sql, [code])
            last_row = await cur.fetchone()

    fmt = lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v)  # noqa: E731
    ex: list[dict] = []
    cum = 1.0
    for r in rows:
        ratio = float(r["ratio"])
        cum *= ratio
        ex.append({
            "date": fmt(r["trade_date"]),
            "ratio": ratio,
            "factor": cum,
            "pre_close": float(r["pre_close"]),
            "prev_close": float(r["prev_close"]),
        })
    last = last_row.get("last_date") if last_row else None
    return Adjustment(ex, fmt(last) if last else None)


def _factor_cte_sql(adj: Adjustment, mode: str) -> str:
    """把累积因子摊成一张「除权日 -> 最终因子」的 SQL 表，喂给 _ADJ_AGGREGATE_BARS。

    一次除权只多一行（恒生电子 23 行 / 5435 行日线），比在 SQL 里做窗口累乘省事，
    也不必把几千根日线拉到 Python 侧再聚合。

    表里存的是**最终因子**：hfq 就是累积因子，qfq 已除以 :meth:`Adjustment.qfq_base`
    （全库末根因子），所以聚合完不必再回头调 _apply_adj。
    """
    base = adj.qfq_base() if mode == "qfq" else 1.0
    # 哨兵行：首个除权日**之前**的日线也要有因子，否则子查询返回 NULL。
    # qfq 下这一段是 1/base 而不是 1 —— 漏掉它会让 2004-06-04 之前的日线
    # 整整高出一个 base 倍（恒生电子 32.6 倍），周线看不出来（月/季/年线全都错）。
    parts = ["SELECT CAST('1900-01-01' AS DATE) AS td, %r AS fac" % (1.0 / base)]
    for e in adj.ex_dates:
        fac = e["factor"] / base
        parts.append("SELECT CAST('%s' AS DATE) AS td, %r AS fac" % (e["date"], fac))
    return " UNION ALL ".join(parts)


async def _ex_bucket_dates(code: str, period: str, adj: Adjustment) -> dict[str, str]:
    """含除权日的周期桶 -> ``{桶尾交易日: 该桶内的除权日}``。

    周期 bar 的 trade_date 是**桶内最后一个交易日**（通达信语义），
    所以「这一行要不要标除权」等价于「它那个桶里有没有除权日」，
    而不是 ``is_ex_date(trade_date)``——后者只在除权日恰好落在桶尾时才成立。
    实测恒生电子 23 个除权日按桶尾判只有 week 6 / month 1 / quarter 0 / year 0：
    除权日集中在 5~6 月，正好不是月末、季末、年末。

    值取桶内**最早**的那个除权日。同一个桶含多个除权日是可能的
    （一个自然月里两次分红），标记只有一个位置，取最早的即可。

    返回真实除权日而不只是「有没有」，是因为弹窗要按它高亮那一条——
    周线上标记落在周五、除权日可能��周三，两者不是同一天。
    """
    if not adj.has_ex:
        return {}
    if period == PERIOD_DAY:
        return {e["date"]: e["date"] for e in adj.ex_dates}
    bucket = kline_repo.PERIOD_BUCKET_SQL.get(period)
    if bucket is None:
        raise ValueError(f"不支持的派生周期: {period!r}")
    marks = ",".join(["%s"] * len(adj.ex_dates))
    # 注意**不能**写成 `WHERE trade_date IN (除权日...)`：那样每组里只剩除权日
    # 自己那几行，MAX 取到的仍是除权日本身。正确做法是按桶分组全量聚合，
    # 再用 HAVING 挑出「组内出现过除权日」的桶。
    #
    # ⚠️ params 的顺序是**占位符在 SQL 文本里出现的先后**，不是子句顺序：
    # ex_dates 那组 %s 写在 SELECT 子句里，位置比 WHERE 的 code=%s **靠前**。
    # 写成 [code, *ex_dates] 不会报错，只会静默地把 code 绑进 IN 列表、
    # 把最后一个除权日绑给 WHERE，结果恒为空集（桶映射恒 0，标记全丢）。
    sql = (f"SELECT MAX(trade_date) AS trade_date, "
           f"MIN(CASE WHEN trade_date IN ({marks}) THEN trade_date END) AS first_ex "
           f"FROM {db.kline_table(PERIOD_DAY)} WHERE code=%s GROUP BY {bucket} "
           f"HAVING first_ex IS NOT NULL")
    params = [*[e["date"] for e in adj.ex_dates], code]
    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
    fmt = lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v)  # noqa: E731
    return {fmt(r["trade_date"]): fmt(r["first_ex"]) for r in rows}


def _apply_adj(bars: list[dict], adj: Adjustment, mode: str) -> list[dict]:
    """把复权因子作用到 bars 上（就地改并返回同一 list）。

    只用于**日线**：派生周期走 :func:`_aggregate_adj_period`，在聚合之前就把因子
    作用到每一根日线上——「先聚合再整体乘」在桶内含除权日时是错的（见该函数）。

    - 价格四价（open/high/low/close）乘因子
    - ``pre_close`` **同样乘因子**（2026-10-13 新下发）——它是浮窗「昨收」栏的值，
      不跟着缩放的话 qfq 下昨收停在 22.06 而价格已压到 21.7x，**又分叉一次**
    - ``volume``（成交量）**反向**缩放，
      因为复权后价格变高、同一笔金额对应的股数变少
    - ``turnover``（成交额）**不变**
    - ``chg_amt`` 跟着价格一起缩放；``chg_pct`` **原样保留**——
      库里的 chg_pct 基准是「已除权 pre_close」，三种模式下语义都成立：
      qfq[t]/qfq[t-1] = (close[t]/close[t-1]) × (prev_close/pre_close)
                        = close[t]/pre_close = 1 + chg_pct，除权日当天也严格相等
      （5435 根日线全量对账，不一致 0 条）
    """
    if mode == "none" or not bars or not adj.has_ex:
        return bars
    base = adj.qfq_base() if mode == "qfq" else 1.0
    for b in bars:
        f = adj.factor(b["datetime"]) / base
        for k in ("open", "high", "low", "close"):
            b[k] = round(b[k] * f, 4)
        if b.get("pre_close") is not None:
            b["pre_close"] = round(b["pre_close"] * f, 4)
        if b.get("chg_amt") is not None:
            b["chg_amt"] = round(b["chg_amt"] * f, 4)
        # hfq 用绝对因子（起点为 1），qfq 用相对因子；
        # 成交量反向：价格因子 f => 股数因子 1/f
        b["volume"] = round(b["volume"] / f, 2) if f else b["volume"]
    return bars


def _to_bars(rows: list[dict], reverse: bool) -> list[dict]:
    """DB 行 → 前端 K 线结构。``reverse=True`` 把倒序结果翻成时间升序。

    ``chg_amt`` / ``chg_pct`` 恒定出现在输出里（没有时为 ``None``），前端据此
    判断"库里有权威值就用、没有才自己算"。三个来源都会给到这两列：
    ``kline_day``（Tushare 官方 ``pct_chg``）、``kline_period``（物化）、
    复权现场聚合（现算）。

    ``pre_close`` **只有日线有**（``kline_0amv`` 没这概念，``kline_period`` 那列
    已删）。前端浮窗的「昨收」栏用它；取不到时回落前一根 ``close``。
    """
    out = []
    for r in rows:
        out.append({
            "datetime": r["trade_date"].isoformat()
            if hasattr(r["trade_date"], "isoformat") else str(r["trade_date"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            # 字段名直接用库列名：volume = 成交量，turnover = 成交额（库列 amount）。
            # turnover 恰好也是 klinecharts 的原生字段名，前端 toChartData 能整条透传，
            # 不必再做一次改名映射。
            "volume": float(r["volume"]),
            "turnover": float(r["amount"]),
            "pre_close": _opt_float(r.get("pre_close")),
            "chg_amt": _opt_float(r.get("chg_amt")),
            "chg_pct": _opt_float(r.get("chg_pct")),
        })
    return out[::-1] if reverse else out


#: ``stock_xdxr`` 的比例列是**每 10 股**口径（pytdx get_xdxr_info 的原样语义，
#: 见 external/tdx_finance.py 的模块注释）。库内存源口径、不做换算，
#: 「每10股」这层话术在拼文案时才出现——这也是为什么文案放在接口层而不是前端。
_XDXR_PER_10 = "每10股"


def _xdxr_text(row: dict) -> str:
    """一条 stock_xdxr 记录 -> 「每10股派X元，送Y股」。

    顺序按 A 股习惯：**先派息后送转**（「派2.00元，送3.00股」）。
    三个字段全为 0/None 时返回空串，由调用方决定怎么兜底。
    """
    parts = []
    cash = _opt_float(row.get("cash_dividend")) or 0.0
    bonus = _opt_float(row.get("bonus_ratio")) or 0.0
    allot = _opt_float(row.get("allot_ratio")) or 0.0
    if cash:
        parts.append(f"派{cash:.2f}元")
    if bonus:
        parts.append(f"送{bonus:.2f}股")
    if allot:
        parts.append(f"配{allot:.2f}股")
    return f"{_XDXR_PER_10}" + "，".join(parts) if parts else ""


async def query_xdxr_records(code: str) -> list[dict]:
    """该标的的**除权日**清单，供前端弹窗展示。按日期倒序（最近的在前）。

    列表的骨架来自 :func:`load_adjustment`（即 ``pre_close`` 判定出的真实除权日），
    而不是 ``stock_xdxr`` 全表——后者混着大量「股本变化 / 送配股上市」记录
    （恒生电子 47 条里只有 23 条是真正的除权除息），它们不影响价格，
    混进弹窗会让每一条都点不开、也不该点。

    两表是**互补**关系而不是主次：``pre_close`` 决定「哪天除权」（价格事实），
    ``stock_xdxr`` 决定「除的是啥」（派多少、送多少）。所以会出现
    「有标记但没文案」的正常中间态——库里没同步过除权除息时就是这样。
    """
    adj = await load_adjustment(code)
    if not adj.has_ex:
        return []
    raw = await stock_repo.list_xdxr(code)
    by_date: dict[str, list[dict]] = {}
    for r in raw:
        d = r.get("ex_date")
        d = d.isoformat() if hasattr(d, "isoformat") else str(d)
        by_date.setdefault(d, []).append(r)

    out: list[dict] = []
    for e in sorted(adj.ex_dates, key=lambda x: x["date"], reverse=True):
        details = by_date.get(e["date"], [])
        texts = [t for t in (_xdxr_text(r) for r in details) if t]
        out.append({
            "date": e["date"],
            "text": "；".join(texts),
            "pre_close": round(e["prev_close"], 4),    # 除权前收盘（未除权）
            "adj_pre_close": round(e["pre_close"], 4), # 库里的 pre_close（已除权）
            "factor": round(e["factor"], 6),
            "ratio": round(e["ratio"], 6),
            "details": [{
                "category": r.get("category"),
                "name": r.get("name"),
                "bonus_ratio": _opt_float(r.get("bonus_ratio")),
                "allot_ratio": _opt_float(r.get("allot_ratio")),
                "cash_dividend": _opt_float(r.get("cash_dividend")),
            } for r in details],
        })
    return out


async def query_quote(code: str, period: str = PERIOD_DAY) -> dict | None:
    """最新一根 + 前一根收盘。

    涨跌两列**直取库里**（query_klines 的 _to_bars 已经带出来），基准是
    Tushare 的已除权 pre_close。只有派生周期线才回落成「close - 前一根 close」
    ——库里没那两列，而周期涨跌幅的语义本来就是相对上一周期。
    """
    bars, _ = await query_klines(code, period=period, count=2)
    if not bars:
        return None
    last = bars[-1]
    prev_close = bars[-2]["close"] if len(bars) >= 2 else last["open"]
    chg_amt = last.get("chg_amt")
    chg_pct = last.get("chg_pct")
    if chg_amt is None and prev_close:
        chg_amt = round(last["close"] - prev_close, 4)
        chg_pct = round((last["close"] - prev_close) / prev_close * 100, 4)
    return {
        "open": last["open"],
        "close": last["close"],
        "high": last["high"],
        "low": last["low"],
        "prev_close": prev_close,
        "datetime": last["datetime"],
        # 首行（库里没有涨跌值且无前一根）给 0.0 而不是 None——
        # 上游 _with_change / 前端都按数值用，None 会让 p.toFixed 直接炸。
        "change": 0.0 if chg_amt is None else chg_amt,
        "change_pct": 0.0 if chg_pct is None else chg_pct,
    }


async def available_codes() -> list[dict]:
    """库中已有数据的标的 + 同步状态。
    按周期逐表聚合（每张表一条简单 GROUP BY），再在 Python 里合并——
    比一条五路 UNION 带 sync_state 的 LEFT JOIN 更好读，也不用手写常量列别名。

    周期表聚合出的 ``first_date``/``last_date`` 是权威值（覆盖全部历史）；
    ``sync_state`` 里同名两列是同步时落库的快照，可用 ``*_recorded`` 对照，
    两者不一致说明 sync_state 之后没再同步过。
    """
    out: list[dict] = []
    fmt = lambda v: v.isoformat() if hasattr(v, "isoformat") else v  # noqa: E731

    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            for period in KLINE_PERIODS:
                # 占位符顺序：JOIN 子句里的 s.period 在 SQL 文本中先于 WHERE 里的
                # k.period，所以 params 是 [sync_state 周期, 本表周期]。
                # 日线表没有 period 列，pfilter 为空、参数也少一个。
                # alias="k" 必需：sync_state 也有 period 列，不限定会报
                # Column 'period' in where clause is ambiguous。
                pfilter, pfilter_params = kline_repo.period_filter(period, alias="k")
                await cur.execute(
                    f"""
                    SELECT k.code, MIN(k.trade_date) AS first_date,
                           MAX(k.trade_date) AS last_date, COUNT(*) AS bars,
                           s.status, s.last_sync,
                           s.first_date AS first_date_recorded,
                           s.last_date  AS last_date_recorded
                    FROM {db.kline_table(period)} k
                    LEFT JOIN sync_state s ON s.code = k.code AND s.period = %s
                    WHERE 1=1{pfilter}
                    GROUP BY k.code, s.status, s.last_sync,
                             s.first_date, s.last_date
                    ORDER BY k.code
                    """, [period, *pfilter_params])
                for r in await cur.fetchall():
                    out.append({
                        "code": r["code"], "period": period,
                        "first_date": fmt(r["first_date"]),
                        "last_date": fmt(r["last_date"]),
                        "bars": int(r["bars"]), "status": r["status"],
                        "last_sync": fmt(r["last_sync"]),
                        "first_date_recorded": fmt(r["first_date_recorded"]),
                        "last_date_recorded": fmt(r["last_date_recorded"]),
                    })
    return out


# 行情列表的取数拆成两支，最后 UNION ALL 合并排序：
#
#   1. kline_day —— 「我们真的有行情」的标的。涨跌两列（chg_amt / chg_pct）
#      **直取库里的**，基准是 Tushare 的已除权 pre_close；早先是用 rn=2 那根的
#      close 现场算，那是未除权的前收盘，除权日会多出一个假跳空。
#      rn<=2 现在只为回填 prev_close。名称等元信息 LEFT JOIN stock_info 补
#      （**以 kline_day 为准而不是 stock_info 表**：stock_info 是元信息表，
#      Tushare stock_basic 额度耗尽时可以是空表，而 kline_day 才是
#      「我们真的有行情」的权威）。
#   2. index_info —— 6 条指数/指标**名录**。这张表只有名录没有行情数据列，
#      所以 close/open/bar_count/日期全部为 NULL，前端显示「—」。
#      name 直接取 index_info.name（即原来的 full_name）。短名 short_name
#      已于 2026-10-09 删除，简短名由前端 INDICES 负责，所以这一列现在
#      显示的是全称（活跃市值指数 / 宝妈指数），不再显示简称。
#
# 排序：指数排前面，个股按代码。指数固定 6 条放前面，
# 否则一条 sh600570 的行情会把它们淹没在下面。
# 指数之间原按 sort_order 排（10/20/30/40/50/60），该列已随表结构精简删掉，
# 现在退化成按 code 排，顺序变成 0AMV / mom / sh000001 / sh000688 /
# sz399001 / sz399006。
#
# 表名一律加反引号：`index` 是 MySQL 保留字，早先就叫这个，每条 SQL 都得
# 加引号，漏一处就是 1064 syntax error。表已改名 index_info，但拼裸表名
# 不值得赌。
_LIST_STOCKS_SQL = """
WITH ranked AS (
    SELECT code, trade_date, open, close, pre_close, chg_amt, chg_pct,
           ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn,
           COUNT(*)     OVER (PARTITION BY code) AS bar_count,
           MIN(trade_date) OVER (PARTITION BY code) AS first_date
    FROM {table}
),
joined AS (
    SELECT r.code, r.rn, r.trade_date, r.open, r.close, r.pre_close,
           r.chg_amt, r.chg_pct,
           r.bar_count, r.first_date, s.name, s.market, s.industry_code,
           s.updated_at, 0 AS is_index
    FROM ranked r
    LEFT JOIN stock_info s ON s.code = r.code
    WHERE r.rn <= 2
),
idx AS (
    SELECT code, 1 AS rn, NULL AS trade_date, NULL AS open, NULL AS close,
           NULL AS pre_close, NULL AS chg_amt, NULL AS chg_pct,
           0 AS bar_count, NULL AS first_date,
           name, NULL AS market, NULL AS industry_code,
           updated_at, 1 AS is_index
    FROM `index_info`
    WHERE enabled = 1
)
SELECT * FROM joined
UNION ALL
SELECT * FROM idx
ORDER BY is_index DESC, code, rn
"""


def _num(v) -> float | None:
    """DECIMAL/None -> float/None。

    不能直接 ``float(r["close"])``：指数行来自 index_info 名录，close 恒为
    NULL，float(None) 直接抛 TypeError，整个行情页 500。
    """
    return None if v is None else float(v)


async def list_stocks() -> list[dict]:
    """行情列表 = 库里有行情的标的（最新一根 + 涨跌幅）+ 全部启用中的指数名录。

    名称取不到时 ``name`` 为 ``None``，由前端兜底。指数行的行情列一律 ``None``。

    ``chg_amt`` / ``chg_pct`` **直取库里**，不在这里算（2026-10-10 改）。
    早先是用 rn=2 那根的 close 当基准现场算，等于拿**未除权**的前收盘当昨收——
    除权日会凭空多出一个跳空。库里那两列的基准是 Tushare pro.daily 给的
    ``pre_close``（已除权），与行情软件显示的涨跌幅同一口径。

    rn<=2 仍然要取，但**只**为了回填 ``prev_close``（接口契约里的字段）。
    rn=2 那行的 chg_amt / chg_pct 一律丢弃——它们是「前一根」的涨跌，
    不是最新一根的。

    ``updated_at`` 取自 stock_info / index_info——**是名录的更新时间，不是行情的
    同步时间**。kline_day / kline_0amv 两张表压根没有这一列；要真知道"这根 K 线
    什么时候写进来的"得给它们加列（见 docs/database-schema.md）。
    """
    sql = _LIST_STOCKS_SQL.format(table=db.kline_table(PERIOD_DAY))
    fmt = lambda v: v.isoformat() if hasattr(v, "isoformat") else v  # noqa: E731
    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql)
            rows = await cur.fetchall()

    out: list[dict] = []
    for r in rows:
        if r["rn"] == 1:
            out.append({
                "code": r["code"], "name": r["name"], "market": r["market"],
                "industry_code": r["industry_code"],
                "last_date": fmt(r["trade_date"]), "first_date": fmt(r["first_date"]),
                "bar_count": int(r["bar_count"]),
                "close": _num(r["close"]), "open": _num(r["open"]),
                "prev_close": None,
                "change": _num(r["chg_amt"]), "change_pct": _num(r["chg_pct"]),
                "updated_at": fmt(r["updated_at"]),
                "is_index": bool(r["is_index"]),
            })
        elif r["rn"] == 2 and out and out[-1]["code"] == r["code"]:
            # 前一根只用来填 prev_close，涨跌一律以上面 rn=1 那行的库值为准。
            out[-1]["prev_close"] = _num(r["close"])
    return out