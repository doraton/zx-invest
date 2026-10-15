"""K 线 / sync_state 的 MySQL 读写。

K 线分两张表：``kline_day``（日线，唯一真相源，直接拉取）与 ``kline_period``
（week / month / quarter / year 四个派生周期合并在一张，用 ``period`` 列区分）。

- ``kline_day`` 没有 ``period`` 列，由 :func:`upsert_klines` 写入
- ``kline_period`` 由 :func:`materialize_periods` 从日线**物化**，
  ``trade_date`` 取周期**最后一个交易日**（与通达信原生非日线语义一致，
  见 docs/architecture.md §5.2）

所有 SQL 的表名都经 :func:`server.db.kline_table` 解析，不要在别处拼前缀。
查派生周期时必须额外带 ``AND period=%s``——用 :func:`period_filter` 生成，
别手写。
"""
import aiomysql

from server import db
from server.db import DERIVED_PERIODS  # noqa: F401  向后兼容：旧代码从本模块取

#: 周期 -> 分桶表达式。季度用 CONCAT(年,'-Q',季)。
PERIOD_BUCKET_SQL = {
    "week": "DATE_FORMAT(trade_date, '%%x-W%%v')",
    "month": "DATE_FORMAT(trade_date, '%%Y-%%m')",
    "quarter": "CONCAT(YEAR(trade_date), '-Q', QUARTER(trade_date))",
    "year": "CAST(YEAR(trade_date) AS CHAR)",
}

_BATCH_SIZE = 500

# INSERT 的列清单，**顺序与 db._KLINE_DDL / _KLINE_PERIOD_DDL 一致**
# （kline_day 去掉 ah_vol / ah_amt——那条路径的源数据不带盘后量额；
#  派生周期再多一个 period + ah_vol / ah_amt）。
# 列是按名写的，所以顺序不影响正确性，但**下面 values 元组的顺序必须严格对齐**，
# 而让两处都贴着 DDL 写，才不会出现「元组里 chg_amt 和 volume 悄悄错位」这类
# 只在真跑起来才炸的错误。
# pre_close / chg_pct 是可选列：Tushare / 东财会给，pytdx / 腾讯不给。
# 统一走 COALESCE(VALUES(x), x)，让「本次没带」退化成「保留库中原值」，
# 而不是把已有值覆盖成 NULL。
_COLS = ("code, trade_date, open, high, low, close, pre_close, chg_pct, "
          "volume, amount")
#: 派生周期表多一个 period 列 + chg_amt + 盘后量额两列。
#: ⚠️ **没有** pre_close（2026-10-13 删）：它就是上一根 bar 的 close，纯冗余，
#:    涨跌两列在物化 SQL 里用派生表的中间量算完即弃，不落库。
_COLS_PERIOD = ("period, code, trade_date, open, high, low, close, "
                "chg_pct, volume, amount, chg_amt, ah_vol, ah_amt")
#: ON DUPLICATE KEY UPDATE。**日线专用**（含 pre_close）。
#: 派生周期表 2026-10-13 起没有 pre_close 列，走 :data:`_UPDATE_PERIOD`。
_UPDATE = """
    open=VALUES(open), high=VALUES(high), low=VALUES(low), close=VALUES(close),
    pre_close=COALESCE(VALUES(pre_close), pre_close),
    chg_pct=COALESCE(VALUES(chg_pct), chg_pct)
"""
_UPDATE_PERIOD = """
    open=VALUES(open), high=VALUES(high), low=VALUES(low), close=VALUES(close),
    chg_pct=COALESCE(VALUES(chg_pct), chg_pct),
    chg_amt=COALESCE(VALUES(chg_amt), chg_amt)
"""


def period_filter(period: str, alias: str = "") -> tuple[str, list]:
    """返回 (追加到 WHERE 的片段, 追加到 params 的值)。

    日线表没有 period 列，返回空；派生周期表必须带 ``AND period=%s``，
    否则会一次读出该标的全部四个周期的行。

    :param alias: 表别名。**只要 SQL 里 JOIN 了别的也有 period 列的表
        （如 ``sync_state``），就必须传**，否则 MySQL 报
        ``Column 'period' in where clause is ambiguous``。
    """
    if period == db.PERIOD_DAY:
        return "", []
    col = f"{alias}.period" if alias else "period"
    return f" AND {col}=%s", [period]


def _materialize_sql(dst: str, src: str, bucket: str) -> str:
    """派生周期物化 SQL。**唯一一份**，``materialize_periods`` 与
    ``tools/sync_tushare.py`` 的同款实现都从这里取——那两处曾经各抄一份，
    口径一旦漂移（比如只改了一边的 chg 计算）就极难发现。

    :param dst: 目标表（恒为 ``kline_period``）
    :param src: 来源表（恒为 ``kline_day``，派生周期的唯一真相源）
    :param bucket: 分桶表达式，见 :data:`PERIOD_BUCKET_SQL`

    ⚠️ 涨跌两列是**原始口径**，与本表的 OHLC 同源：

    - 基准是上一根 bar 的收盘（``LAG(close)``，未复权），
      但它**只作为派生表里的中间量 ``prev_raw`` 存在，不落库** ——
      本表 2026-10-13 起没有 ``pre_close`` 列了（那列就是上一根 close，纯冗余）
    - ``chg_amt`` / ``chg_pct`` 由它现算，**按已舍入的 close 与基准**——
      收窄精度后 ``round(a,2) - round(b,2) != round(a - b, 2)``，顺序不能反
    - 最早那个周期由 ``LAG`` 天然给 NULL，三列一律 NULL，不需要额外判据

    **不要**在这里折算因子（2026-10-13 撤掉，之前折算过）。折算口径本身是对的，
    但它和本表的原始 OHLC 不是一份东西：``adj=none`` 时接口原样下发这两列，
    用户会看到同一根 K 线上「图是原始跳空 +260%、数字是折算后 +370%」。
    折算口径的涨跌走 :func:`market._ADJ_AGGREGATE_BARS`（复权档）现算，
    那条路本来就不读本表。
    """
    return f"""
    INSERT INTO {dst} (code, period, trade_date, open, high, low, close,
                       chg_amt, chg_pct, volume, amount, ah_vol, ah_amt)
    SELECT code, %(period)s AS period, trade_date, open, high, low, close,
           -- ⚠️ 本表**没有** pre_close 列（2026-10-13 删掉，它就是上一根 close，
           --    纯冗余）。上一周期收盘只作为派生表里的中间量 prev_raw 参与计算。
           CASE WHEN prev_raw IS NULL THEN NULL
                ELSE ROUND(close - prev_raw, 2) END,
           CASE WHEN prev_raw IS NULL OR prev_raw = 0 THEN NULL
                ELSE ROUND((close / prev_raw - 1) * 100, 2) END,
           volume, amount, ah_vol, ah_amt
    FROM (
        -- LAG 必须在**桶级**那一层的外面：桶内窗口看不到邻居。
        -- 「第一个周期没有上一周期」由 LAG 天然给 NULL，不需要额外判据。
        SELECT b.*, LAG(close) OVER (ORDER BY trade_date) AS prev_raw
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
    ON DUPLICATE KEY UPDATE
        open=VALUES(open), high=VALUES(high), low=VALUES(low),
        close=VALUES(close),
        chg_amt=VALUES(chg_amt), chg_pct=VALUES(chg_pct),
        volume=VALUES(volume), amount=VALUES(amount),
        ah_vol=VALUES(ah_vol), ah_amt=VALUES(ah_amt)
    """


async def materialize_periods(code: str, periods: tuple[str, ...] = DERIVED_PERIODS) -> dict:
    """从日线重算并 upsert 派生周期。返回 {period: 写入行数}。

    用窗口函数在一条 SQL 内完成：整段分桶的 FIRST_VALUE/LAST_VALUE 取首末收盘，
    MAX/MIN 取高低，SUM 求量额，DISTINCT 把每个分桶收敛成一行。

    **涨跌三列的口径**（2026-10-13 启用，此前 pre_close / chg_pct 是两列死列）：

    - ``pre_close`` = **上一根 bar 的收盘**（``LAG(close)``），原始未复权，
      与本表 OHLC 同一口径
    - ``chg_amt`` / ``chg_pct`` 由它现算，**按已舍入的 close / pre_close**——
      收窄精度后 ``round(a,2) - round(b,2) != round(a - b, 2)``，顺序不能反
    - 最早那个周期没有「上一周期」，``LAG`` 天然给 NULL，三列一律 NULL
    - 跨除权的周期里这个涨跌幅**会显示原始跳空**（恒生电子 2007 年线 +259.87%），
      这是正确的：``adj=none`` 下图上画的就是原始价，两者必须对得上。
      要看连续涨幅切前复权——那条路由 :func:`market._ADJ_AGGREGATE_BARS` 现算，
      不读本表。

    ⚠️ 这里曾经折算过因子（``close[上桶末] × f[上桶末] / f[本桶末]``），
    折算本身没错（2021-07 真实涨幅 -16.29 就算对了），但存的位置错了：
    折算口径与本表的原始 OHLC 不是一份东西，``adj=none`` 时接口原样下发，
    同一根 K 线会「图 +259.87%、数字 +369.75%」，年线上差 110 个百分点。
    教训：验证时不能拿自己定义的参照物当标准（当时正是拿「复权后真实涨幅」
    验「复权后真实涨幅」，1150 根全过）。
    """
    out: dict[str, int] = {}
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            written = await materialize_periods_on(
                cur, code, periods, print_fn=None)
            out.update(written)
        # ⚠️ 必须提交。aiomysql 默认 autocommit=False，连接归还池时未提交事务会被
        #    **回滚**——本函数原来根本没 commit，单独调用等于什么都没做
        #    （库里那些行是 tools/sync_tushare.py 调 materialize_periods_on 之后
        #    自己 commit 才落下去的）。
        #    物化改成「先 DELETE 再 INSERT」之后这一步成了**要害**：
        #    不提交就会连 DELETE 一起回滚，状态反而是安全的；
        #    但只要哪天有人把 DELETE 挪到事务外单独执行，缺 commit 就是丢数据。
        await conn.commit()
    return out


async def materialize_periods_on(cur, code: str,
                                 periods: tuple[str, ...] = DERIVED_PERIODS,
                                 print_fn=None) -> dict:
    """在**已有 cursor** 上跑物化，供 tools/sync_tushare.py 复用同一份 SQL。

    物化方式是「**先清空该周期、再从日线全量重建**」，不是增量 upsert。
    原因见 :func:`_materialize_sql` 末尾的说明：桶的 ``trade_date`` 是
    **桶内最后一个交易日**，本周每多一个交易日，桶尾就变一次，
    「按 (code, period, trade_date) upsert」会把每一版都留成独立行 ——
    一天一行，一周五行，接口于是返回同一周五根假 K 线。

    全删全插在 1542 行这个量级上是毫秒级，换来的是「表里永远是当前桶集合」
    这一条不用维护的保证。真相源是日线，删掉的每一行都会原样重建。

    :param print_fn: 可选的进度回调（sync_tushare 要打印每周期行数）。
    """
    out: dict[str, int] = {}
    src = db.kline_table(db.PERIOD_DAY)
    for period in periods:
        bucket = PERIOD_BUCKET_SQL.get(period)
        if bucket is None:
            raise ValueError(f"不支持的派生周期: {period!r}")
        dst = db.kline_table(period)
        # DELETE 与 INSERT 在**同一个游标、同一事务**里：中途抛异常会连 DELETE
        # 一起回滚，派生周期表保持调用前的状态，不会出现「清空了但没重建」。
        await cur.execute(
            f"DELETE FROM {dst} WHERE code=%(code)s AND period=%(period)s",
            {"code": code, "period": period})
        sql = _materialize_sql(dst, src, bucket)
        # 具名占位符，顺序无所谓（见 _EX_FACTOR_CTE 的警告）。
        await cur.execute(sql, {"code": code, "period": period})
        # 物化改成「先删后插」后，rowcount 就是**真实写入行数**了 ——
        # 此前那句「不是写入行数」的警告只对 upsert 成立
        #（ON DUPLICATE KEY UPDATE 下更新记 2、值没变记 0，会翻倍或归零）。
        # 全删全插不再有重复键，行数与 COUNT 目标表必然一致。
        n = max(cur.rowcount, 0)
        out[period] = n
        if print_fn:
            print_fn(period, n)
    return out


async def upsert_klines(rows: list[dict]) -> int:
    """批量写入 K 线，同一主键覆盖更新。返回影响行数。

    按 ``rows[i]["period"]`` 分组后各自写入对应的表；缺 ``period`` 时默认落日线。
    主键：日线是 ``(code, trade_date)``，派生周期是 ``(code, period, trade_date)``。
    """
    if not rows:
        return 0
    by_period: dict[str, list[dict]] = {}
    for r in rows:
        by_period.setdefault(r.get("period") or db.PERIOD_DAY, []).append(r)

    total = 0
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            for period, group in by_period.items():
                # 日线表没有 period 列；派生周期表的 period 既是列也是分组键，
                # 列顺序必须与下面的 values 元组严格一致（_COLS_PERIOD 把 period 放最前）
                is_day = period == db.PERIOD_DAY
                cols = _COLS if is_day else _COLS_PERIOD
                head = "" if is_day else "%s,"
                # 日线 10 个占位符，派生周期 13 个（多 period / chg_amt / ah_vol / ah_amt）
                marks = ",".join(["%s"] * (10 if is_day else 13))
                sql = (f"INSERT INTO {db.kline_table(period)} ({cols}) "
                       f"VALUES ({head}{marks}) "
                       f"ON DUPLICATE KEY UPDATE "
                       f"{_UPDATE if is_day else _UPDATE_PERIOD}")
                for i in range(0, len(group), _BATCH_SIZE):
                    # ⚠️ 元组顺序必须与上面的 cols **逐列对齐**（cols 是按名写的，
                    #    顺序错了 SQL 也不报错，只会安静地把值写进隔壁列）。
                    values = [
                        (r["code"], r["trade_date"], r["open"], r["high"],
                         r["low"], r["close"], r.get("pre_close"),
                         r.get("chg_pct"), r["volume"], r["amount"]) if is_day
                        else (period, r["code"], r["trade_date"], r["open"],
                              r["high"], r["low"], r["close"],
                              r.get("chg_pct"),
                              r["volume"], r["amount"],
                              # 派生周期的权威口径由 materialize_periods 从日线物化；
                              # 这条路径只在拿到 pytdx 原生周期 bar 时才会走到，
                              # 那里没有涨跌与盘后量额。缺省保持 **None**：
                              # ah_* 可空，「没有」就是 NULL，写 0 会被误读成
                              # 「有盘后交易但成交为零」。
                              r.get("chg_amt"), r.get("ah_vol"),
                              r.get("ah_amt"))
                        for r in group[i:i + _BATCH_SIZE]
                    ]
                    await cur.executemany(sql, values)
                    total += cur.rowcount
    return total


async def set_sync_state(code: str, period: str, status: str,
                         first_date: str | None = None,
                         last_date: str | None = None,
                         message: str | None = None) -> None:
    """写同步状态。

    :param first_date: 库内该标的(period)的最早交易日。
    :param last_date: 库内该标的(period)的最晚交易日。
    两者都应由对应周期表聚合得出（见 :func:`get_range`），而非仅取本次拉取窗口。
    """
    # COALESCE 是必需的：running / failed 状态不传日期，
    # 直接 VALUES(x) 覆盖会把已记录的区间抹成 NULL（同步中途失败就丢历史范围）。
    sql = """
    INSERT INTO sync_state (code, period, last_sync, first_date, last_date, status, message)
    VALUES (%s, %s, NOW(), %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        last_sync=VALUES(last_sync),
        first_date=COALESCE(VALUES(first_date), first_date),
        last_date=COALESCE(VALUES(last_date), last_date),
        status=VALUES(status), message=VALUES(message)
    """
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                sql, (code, period, first_date, last_date, status, (message or "")[:255] or None))


async def count_klines(code: str, period: str) -> int:
    extra, extra_params = period_filter(period)
    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                f"SELECT COUNT(*) AS n FROM {db.kline_table(period)} "
                f"WHERE code=%s{extra}", [code, *extra_params])
            row = await cur.fetchone()
    return int(row["n"])


async def get_range(code: str, period: str) -> tuple[str | None, str | None, int]:
    """返回 (最早日期, 最新日期, 行数)。"""
    extra, extra_params = period_filter(period)
    sql = f"""
    SELECT MIN(trade_date) AS mn, MAX(trade_date) AS mx, COUNT(*) AS n
    FROM {db.kline_table(period)} WHERE code=%s{extra}
    """
    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, [code, *extra_params])
            row = await cur.fetchone()
    fmt = lambda v: v.isoformat() if hasattr(v, "isoformat") else v  # noqa: E731
    return fmt(row["mn"]), fmt(row["mx"]), int(row["n"])


async def list_recent(code: str, period: str, limit: int = 10) -> list[dict]:
    """按日期倒序取最近若干根，字段与前端 K 线一致。"""
    extra, extra_params = period_filter(period)
    sql = f"""
    SELECT trade_date, open, high, low, close, volume, amount
    FROM {db.kline_table(period)} WHERE code=%s{extra}
    ORDER BY trade_date DESC LIMIT %s
    """
    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, [code, *extra_params, limit])
            rows = await cur.fetchall()
    out = []
    for r in rows:
        d = r["trade_date"]
        out.append({
            "datetime": d.isoformat() if hasattr(d, "isoformat") else str(d),
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "volume": float(r["volume"]), "amount": float(r["amount"]),
        })
    return out