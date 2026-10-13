"""指数 / 指标的数据层（``index_info`` 名录 + ``index_daily`` 统一数据表）。

表结构
------
- ``index_info``  6 条名录：代码、名称、单位、形态、来源
- ``index_daily`` 6 个标的的日频数据，**全在一张表**

为什么不把指数塞进 ``stock`` 表
------------------------------
``stock`` 的语义是「沪深主板 / 创业板 / 科创板的**个股**名录」，由
``tools/backfill_stock_universe.py`` 按代码段白名单维护。那个脚本每次跑都会
**删掉白名单外的所有行**，而 ``sh000xxx`` / ``sz399xxx`` 都不在白名单里，指数
放进去下一次回填就被删光。加上 ``sh000001``（上证指数）和 ``sz000001``
（平安银行）代码段相同、市场前缀不同，白名单逻辑在这上面已经踩过一次坑。

同一张表里混着三种量纲
----------------------
这批标的的**值不是一个东西**：

==============  ==========  ==========  ==================================
code            unit        shape       实际含义
==============  ==========  ==========  ==================================
sh000001        point       ohlc        指数点位，3000~3600
sz399001        point       ohlc        指数点位，约 10000
sz399006        point       ohlc        指数点位，约 2000
sh000688        point       ohlc        指数点位，约 1000
0AMV            yuan        ohlc        活跃市值**金额**；code 首字符是数字 0
mom             score       single      情绪**分数**，0-100，无开高低收
==============  ==========  ==========  ==================================

所以任何取数 SQL 都**必须带 ``WHERE code = %s``**：多 code 直接聚合会把
3000 点和 1.5e14 元加在一起。要解读数字先 JOIN ``index_info`` 看 ``value_unit``。
只有 ``close_value`` 是 6 个标的都有的（宝妈指数是单值指标，一天一个分数）。
"""
import aiomysql

from server import db

#: ``index_info`` 的种子数据。**这是名录，不是行情**——行情在下面 3 张数据表里。
#:
#: pinyin 沿用 ``stock_repo.pinyin_initials`` 的算法（NFKC 归一 + pypinyin），
#: 手写在这里是为了让这张表在没有 pypinyin 依赖的环境里也能建起来。
#:
#: 2026-10-09 起表里不再有 ``source`` / ``sort_order`` / ``short_name`` 三列，
#: 简称和展示顺序由前端 ``web/src/utils/indices.ts`` 的 INDICES 负责。各标的的
#: 数据出处改记在 remark 里：
#:   上证 / 深成 / 创业板 / 科创50  通达信行情源
#:   0AMV                           指南针客户端 0AMV（见 docs/znz-active-mv-ocr-sync.md）
#:   mom                            宝妈指数线上面板 https://xmkinc.github.io/mom-index
SEED_INDEXES: list[dict] = [
    {
        "code": "SH.000001", "name": "上证指数",
        "pinyin": "szzs", "value_unit": "point", "data_shape": "ohlc",
        "remark": "沪市综合指数，A 股大盘基准；通达信行情源",
    },
    {
        "code": "SZ.399001", "name": "深证成指",
        "pinyin": "szcz", "value_unit": "point", "data_shape": "ohlc",
        "remark": "深市代表性指数，500 只成分；通达信行情源",
    },
    {
        "code": "SZ.399006", "name": "创业板指",
        "pinyin": "cybz", "value_unit": "point", "data_shape": "ohlc",
        "remark": "创业板 100 只成分；通达信行情源",
    },
    {
        "code": "SH.000688", "name": "科创50",
        "pinyin": "kc50", "value_unit": "point", "data_shape": "ohlc",
        "remark": "科创板 50 只成分；通达信行情源",
    },
    {
        "code": "0AMV", "name": "活跃市值指数",
        "pinyin": "hysz", "value_unit": "yuan", "data_shape": "ohlc",
        "remark": "指南针 0AMV（code 首字符是数字 0），全市场活跃筹码流通市值加总；"
                  "单位口径待确认，非指数点位",
    },
    {
        "code": "mom", "name": "宝妈指数",
        "pinyin": "bmzs", "value_unit": "score", "data_shape": "single",
        "remark": "公开论坛讨论的新手语言情绪分数 0-100，反向指标；无 OHLC；"
                  "线上面板 https://xmkinc.github.io/mom-index",
    },
]

_UPSERT_INDEX = """
INSERT INTO index_info (
    code, name, pinyin, value_unit, data_shape, enabled, remark, updated_at
)
VALUES (%(code)s, %(name)s, %(pinyin)s, %(value_unit)s,
        %(data_shape)s, %(enabled)s, %(remark)s, NOW())
ON DUPLICATE KEY UPDATE
    name=VALUES(name), pinyin=VALUES(pinyin), value_unit=VALUES(value_unit),
    data_shape=VALUES(data_shape),
    -- enabled 不覆盖：用户停用某个指数后重跑种子数据不应该把它偷偷打开
    remark=VALUES(remark), updated_at=NOW()
"""


async def upsert_index(row: dict) -> None:
    """写入 / 更新一条指数名录。``enabled`` 缺省为 1。"""
    payload = {**row, "enabled": row.get("enabled", 1)}
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_UPSERT_INDEX, payload)


async def seed_indexes() -> int:
    """写入 6 条种子名录，返回实际处理的条数。幂等，可反复执行。"""
    for row in SEED_INDEXES:
        await upsert_index(row)
    return len(SEED_INDEXES)


async def list_indexes(enabled_only: bool = True) -> list[dict]:
    """列出指数名录，按 ``code`` 排。

    2026-10-09 前是 ``ORDER BY sort_order, code``；``sort_order`` 那一列已删
    （展示顺序交给前端 INDICES），这里退化成 code 序。
    """
    sql = "SELECT * FROM index_info"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY code"
    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql)
            return [_fmt_row(r) for r in await cur.fetchall()]


async def get_index(code: str) -> dict | None:
    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM index_info WHERE code=%s", (code,))
            row = await cur.fetchone()
    return _fmt_row(row) if row else None


# ---------------------------------------------------------------------------
# 统一数据表 index_daily
# ---------------------------------------------------------------------------

_UPSERT_DAILY = """
INSERT INTO index_daily (
    code, trade_date, open_value, high_value, low_value, close_value,
    volume, amount, sample_size, confidence, source, fetch_at
)
VALUES (%(code)s, %(trade_date)s, %(open_value)s, %(high_value)s, %(low_value)s,
        %(close_value)s, %(volume)s, %(amount)s, %(sample_size)s, %(confidence)s,
        %(source)s, NOW())
ON DUPLICATE KEY UPDATE
    open_value=VALUES(open_value), high_value=VALUES(high_value),
    low_value=VALUES(low_value), close_value=VALUES(close_value),
    volume=VALUES(volume), amount=VALUES(amount),
    sample_size=VALUES(sample_size), confidence=VALUES(confidence),
    source=VALUES(source), fetch_at=NOW()
"""


#: ``index_daily`` 的全部列。写入前用它把缺失的键补成 None——
#: 直接把调用方的 dict 丢给具名占位符的话，少传一个可选列就是
#: ``KeyError: 'volume'``，而不是「这列留空」。
_DAILY_COLUMNS = (
    "code", "trade_date", "open_value", "high_value", "low_value",
    "close_value", "volume", "amount", "sample_size", "confidence", "source",
)


def _daily_payload(row: dict) -> dict:
    """补齐所有列的缺省值。

    ``code`` / ``trade_date`` / ``close_value`` 缺失会变成 None 并被 MySQL 拒掉
    （NOT NULL 列），所以缺这三个请显式报错而不是让 aiomysql 抛一个难懂的
    IntegrityError。
    """
    missing = [c for c in ("code", "trade_date", "close_value") if row.get(c) is None]
    if missing:
        raise ValueError(f"index_daily 缺少必填字段: {missing}（收到 {sorted(row)}）")
    payload = {c: row.get(c) for c in _DAILY_COLUMNS}
    payload["source"] = payload["source"] or "tdx"
    return payload


async def upsert_daily(row: dict) -> None:
    """写入 / 更新一条日频数据。

    ``row`` 必须含 ``code`` / ``trade_date`` / ``close_value``；其余列缺省为
    NULL。**直接覆盖**而不是 COALESCE——重跑同步时「这次没取到值」和
    「上次取到了」要能区分开，覆盖掉才不会留下上次的陈旧值。
    """
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_UPSERT_DAILY, _daily_payload(row))


async def upsert_daily_batch(rows: list[dict]) -> int:
    """批量写入日频数据。"""
    if not rows:
        return 0
    payload = [_daily_payload(r) for r in rows]
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(_UPSERT_DAILY, payload)
    return len(payload)


async def list_daily(code: str, limit: int = 500,
                    end_date: str | None = None) -> list[dict]:
    """按 code 取日频数据，**日期升序**返回。

    ``code`` 是必填的——这张表混着三种量纲，跨 code 聚合没有意义。
    """
    sql = "SELECT * FROM index_daily WHERE code=%s"
    args: list = [code]
    if end_date:
        sql += " AND trade_date <= %s"
        args.append(end_date)
    sql += " ORDER BY trade_date DESC LIMIT %s"
    args.append(limit)
    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            rows = await cur.fetchall()
    return [_fmt_row(r) for r in reversed(rows)]


async def count_daily() -> dict[str, int]:
    """每个 code 各有多少行。用来一眼看出「哪个指数还没接数据源」。"""
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT code, COUNT(*) FROM index_daily GROUP BY code")
            got = {r[0]: int(r[1]) for r in await cur.fetchall()}
    # 名录里有的都返回，没数据源的显式 0
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT code FROM index_info ORDER BY code")
            allcodes = [r[0] for r in await cur.fetchall()]
    return {c: got.get(c, 0) for c in allcodes}


def _fmt_row(row: dict) -> dict:
    """datetime / Decimal 转成前端友好的类型。"""
    out = dict(row)
    for k, v in out.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat(sep=" ") if hasattr(v, "hour") else v.isoformat()
        elif hasattr(v, "quantize"):  # Decimal
            out[k] = float(v)
    return out
