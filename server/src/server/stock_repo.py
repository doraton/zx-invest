"""stock_info / stock_finance / stock_xdxr 的 MySQL 读写。

写入层刻意做得很薄：``external/tdx_finance.py`` 负责把 pytdx 的拼音字段
翻成可读英文，这里只负责 upsert 与查询，不做业务判断。
表结构见 docs/architecture.md §5.3。
"""
import datetime
import decimal
import unicodedata

import aiomysql

from server import db

_UPSERT_STOCK = """
INSERT INTO stock_info (code, raw_code, market, name, pinyin, ipo_date, total_share,
                   float_share, industry_code, province_code, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
ON DUPLICATE KEY UPDATE
    raw_code=VALUES(raw_code), market=VALUES(market),
    name=COALESCE(VALUES(name), name),
    pinyin=COALESCE(VALUES(pinyin), pinyin),
    ipo_date=COALESCE(VALUES(ipo_date), ipo_date),
    total_share=COALESCE(VALUES(total_share), total_share),
    float_share=COALESCE(VALUES(float_share), float_share),
    industry_code=COALESCE(VALUES(industry_code), industry_code),
    province_code=COALESCE(VALUES(province_code), province_code),
    updated_at=NOW()
"""
# name / ipo_date 等一律 COALESCE：新源端没给值时保留旧值，
# 否则一次拉取失败就会把库里已有的信息抹成 NULL。

_UPSERT_FINANCE = """
INSERT INTO stock_finance (
    code, report_date, total_asset, current_asset, fixed_asset, intangible_asset,
    total_liability, current_liability, net_asset,
    main_revenue, operating_profit, total_profit, net_profit, after_tax_profit,
    undistributed_profit, op_cash_flow, net_cash_flow,
    inventory, accounts_receivable, shareholder_count, per_share_net_asset
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON DUPLICATE KEY UPDATE
    total_asset=VALUES(total_asset), current_asset=VALUES(current_asset),
    fixed_asset=VALUES(fixed_asset), intangible_asset=VALUES(intangible_asset),
    total_liability=VALUES(total_liability), current_liability=VALUES(current_liability),
    net_asset=VALUES(net_asset), main_revenue=VALUES(main_revenue),
    operating_profit=VALUES(operating_profit), total_profit=VALUES(total_profit),
    net_profit=VALUES(net_profit), after_tax_profit=VALUES(after_tax_profit),
    undistributed_profit=VALUES(undistributed_profit), op_cash_flow=VALUES(op_cash_flow),
    net_cash_flow=VALUES(net_cash_flow), inventory=VALUES(inventory),
    accounts_receivable=VALUES(accounts_receivable),
    shareholder_count=VALUES(shareholder_count),
    per_share_net_asset=VALUES(per_share_net_asset)
"""

_FINANCE_COLUMNS = (
    "total_asset", "current_asset", "fixed_asset", "intangible_asset",
    "total_liability", "current_liability", "net_asset",
    "main_revenue", "operating_profit", "total_profit", "net_profit",
    "after_tax_profit", "undistributed_profit", "op_cash_flow", "net_cash_flow",
    "inventory", "accounts_receivable", "shareholder_count", "per_share_net_asset",
)

_UPSERT_XDXR = """
INSERT INTO stock_xdxr (
    code, ex_date, category, name, bonus_ratio, allot_ratio, cash_dividend,
    reduce_ratio, allot_price, share_before, share_after, float_before, float_after
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    name=VALUES(name), bonus_ratio=VALUES(bonus_ratio), allot_ratio=VALUES(allot_ratio),
    cash_dividend=VALUES(cash_dividend), reduce_ratio=VALUES(reduce_ratio),
    allot_price=VALUES(allot_price), share_before=VALUES(share_before),
    share_after=VALUES(share_after), float_before=VALUES(float_before),
    float_after=VALUES(float_after)
"""


async def upsert_stock(row: dict) -> None:
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_UPSERT_STOCK, (
                row["code"], row["raw_code"], row["market"], row.get("name"),
                row.get("pinyin"),
                row.get("ipo_date"), row.get("total_share"), row.get("float_share"),
                row.get("industry_code"), row.get("province_code")))


async def upsert_finance(row: dict) -> None:
    """写财务快照。PK=(code, report_date)，同报告期覆盖更新。"""
    if not row.get("report_date"):
        raise ValueError(f"{row.get('code')} 的财务快照缺 report_date，无法入库")
    values = [row["code"], row["report_date"]] + [row.get(c) for c in _FINANCE_COLUMNS]
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_UPSERT_FINANCE, values)


async def upsert_xdxr(rows: list[dict]) -> int:
    """写除权除息历史（批量）。返回影响行数。"""
    if not rows:
        return 0
    # category 在 PK 里，源端缺 category 时用 0 占位，避免整条写不进去
    values = [tuple(r.get(c) for c in (
        "code", "ex_date", "category", "name", "bonus_ratio", "allot_ratio",
        "cash_dividend", "reduce_ratio", "allot_price", "share_before",
        "share_after", "float_before", "float_after")) for r in rows]
    values = [v[:2] + (v[2] if v[2] is not None else 0,) + v[3:] for v in values]
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(_UPSERT_XDXR, values)
            return cur.rowcount


def _fmt_row(row: dict) -> dict:
    """DB 行 → JSON 友好字典。

    ``Decimal`` 不能被 FastAPI 的 JSONResponse 序列化（接口会 500），
    统一转成 ``float``；``datetime``/``date`` 转 ISO 字符串。
    """
    out = {}
    for k, v in row.items():
        if isinstance(v, (datetime.date, datetime.datetime)):
            out[k] = v.isoformat()
        elif isinstance(v, decimal.Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out


async def get_stock(code: str) -> dict | None:
    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM stock_info WHERE code=%s", (code,))
            row = await cur.fetchone()
    return _fmt_row(row) if row else None


async def get_finance(code: str, report_date: str | None = None) -> dict | None:
    """取财务快照；不给 report_date 则取最新一期。"""
    sql = "SELECT * FROM stock_finance WHERE code=%s"
    args: list = [code]
    if report_date:
        sql += " AND report_date=%s"
        args.append(report_date)
    sql += " ORDER BY report_date DESC LIMIT 1"
    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, tuple(args))
            row = await cur.fetchone()
    return _fmt_row(row) if row else None


async def list_finance(code: str) -> list[dict]:
    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM stock_finance WHERE code=%s ORDER BY report_date DESC", (code,))
            rows = await cur.fetchall()
    return [_fmt_row(r) for r in rows]


async def list_xdxr(code: str) -> list[dict]:
    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM stock_xdxr WHERE code=%s ORDER BY ex_date DESC", (code,))
            rows = await cur.fetchall()
    return [_fmt_row(r) for r in rows]


async def upsert_ref(table: str, rows: list[dict]) -> int:
    """[已于 2026-10-07 废弃] 写 ref_industry / ref_province 映射表。

    这两张表连同其写入逻辑已删除，原因见 db.py SCHEMA_STATEMENTS 中的说明。
    保留此函数仅为让历史调用方得到明确报错，而不是静默写坏数据。
    """
    raise NotImplementedError(
        f'ref_industry / ref_province 已删除（2026-10-07）。'
        f'板块关系是多对多数据，请新建板块关系表，不要用 code->name 映射表。'
    )


# 搜索：按 6 位数字码 / 代码前缀 / 名称 / 拼音首字母四种方式匹配。
#
# 排序把「前缀命中」排在「中间包含」前面：用户敲 600 时想要的是 600570，
# 而不是藏在一堆 1600570 里的 600570。命中档位依次为
#   0 数字码前缀  1 名称前缀  2 拼音前缀  3 其余（任意位置的包含）
# 同档内仍按 raw_code 排，保证结果稳定、可复现。
_SEARCH_SQL = """
SELECT code, raw_code, name, pinyin, market, industry_code, province_code, ipo_date,
       total_share, float_share
FROM stock_info
WHERE raw_code LIKE %(like)s OR code LIKE %(like)s
   OR name LIKE %(like)s OR pinyin LIKE %(like)s
ORDER BY CASE
             WHEN raw_code LIKE %(pre)s THEN 0
             WHEN name     LIKE %(pre)s THEN 1
             WHEN pinyin   LIKE %(pre)s THEN 2
             ELSE 3
         END,
         raw_code
LIMIT %(limit)s
"""
# 全部用具名参数：**不能混用裸 %s 和 %(name)s**。pymysql 会把 dict 展开成
# 值列表，同一个 key 出现几次就展开几次，所以混用时占位符总数会超过实际
# 传入的值个数，直接报 "not enough arguments for format string"。


def pinyin_initials(name: str | None) -> str | None:
    """中文名 → 拼音首字母小写。``恒生电子`` → ``hsdz``。

    键盘精灵的核心手感：输 ``hsdz`` 就能命中「恒生电子」，而不是必须打全名。

    非汉字字符按原样保留小写，所以 ``ST 浦发`` 这类带前缀的简称
    搜 ``stpf`` 也能命中——通达信里也是这个行为。

    **必须做 NFKC 归一化**：TDX 名称表里有全角字母（``000002 万科Ａ``
    的末字是 U+FF21 全角 Ａ，见 ``tdx_local`` 里的踩坑注释）。而
    ``str.lower()`` 只降 ASCII 大写，全角 ``Ａ.lower()`` 仍是全角 ``ａ``，
    于是拼音会变成 ``ａgzs``，用户敲半角 ``agzs`` 一条都搜不到。
    NFKC 会把全角字母/数字折成半角，正好解决这个问题。

    空名返回 ``None``（存 NULL 而不是空串，否则 ``pinyin LIKE '%%'``
    会把所有行都匹配上）。
    """
    if not name:
        return None
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:  # 没装 pypinyin 时退化为「原名小写」，不至于整个回填脚本崩掉
        return _ascii_fold(name) or None
    parts = lazy_pinyin(name, style=Style.FIRST_LETTER, errors="default")
    out = "".join(p.strip() for p in parts if p and p.strip())
    return _ascii_fold(out) or None


def _ascii_fold(s: str) -> str:
    """NFKC 归一 + 小写。全角 Ａ -> a，全角数字 １ -> 1。"""
    return unicodedata.normalize("NFKC", s).lower()


async def search_stocks(q: str, limit: int = 30) -> list[dict]:
    """搜索证券。支持 600570 / sh600570 / 名称子串 / 拼音首字母（hsdz → 恒生电子）。

    纯 MySQL LIKE，够 MVP 用；ES 索引就绪后切到 ES。
    """
    q = (q or "").strip()
    if not q:
        return []
    limit = max(1, min(int(limit), 200))
    like = f"%{q}%"
    pre = f"{q}%"
    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(_SEARCH_SQL,
                              {"like": like, "pre": pre, "limit": limit})
            rows = await cur.fetchall()
    out = []
    for r in rows:
        d = _fmt_row(r)
        # name 为 NULL 时回落到 raw_code，界面上不至于显示空白
        d["name"] = d.get("name") or d.get("raw_code")
        out.append(d)
    return out


async def backfill_names(overwrite: bool = False) -> tuple[int, int]:
    """用本地通达信名称缓存回填 ``stock_info.name`` 与 ``stock_info.pinyin``。

    证券简称没有网络来源（pytdx 的 ``get_security_list`` 在所有可用节点都返回
    None），只能读本地 TDX 客户端的 ``T0002/hq_cache/{shs,szs}.tnf``。

    只填空值：``overwrite=False``（默认）时跳过已有名称，避免用本地缓存
    盖掉库里更权威的来源。返回 ``(已更新, 未命中)``。

    拼音每次都重算（不受 ``overwrite`` 控制）：它是 name 的纯函数，
    name 没变结果就不会变，重算的成本只是几千次字符串拼接。
    """
    from server.external import tdx_local  # 局部导入：只有回填时才会去读 10MB 的 tnf

    async with db.pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            sql = "SELECT code, name FROM stock_info"
            if not overwrite:
                sql += " WHERE name IS NULL OR name = ''"
            await cur.execute(sql)
            rows = await cur.fetchall()

    updates: list[tuple[str, str | None, str]] = []
    missed = 0
    for r in rows:
        name = tdx_local.get_security_name(r["code"])
        if name:
            updates.append((name, pinyin_initials(name), r["code"]))
        else:
            missed += 1
    if updates:
        async with db.pool().acquire() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "UPDATE stock_info SET name=%s, pinyin=%s, updated_at=NOW() WHERE code=%s",
                    updates)
    return len(updates), missed
