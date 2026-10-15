"""自选股（``watch_group`` + ``watch_item``）的读写。

2026-10-14 起自选从浏览器 localStorage 挪到这里。落库的理由和 ``app_config``
一样——**两处真相迟早漂移**，见 ``web/src/utils/layout.ts`` 的模块头，
版面比例已经做过同一件事。

三处必须知道的约定：

1. **没有「自选本体」表**，只有分组表和关系表。自选条目自身不带数据：
   ``name`` **不落库**（前端从 ``/api/stock/list`` 统一解析，见
   ``WatchlistPage`` 的 dbNames 回填），``addedAt`` 全项目无人读取。
   于是「票还在但不属于任何组」这种孤儿状态在数据上**无法表示**——
   删完某个组的关联行，票自然消失，不需要额外判断「要不要连本体一起删」。

2. **``code`` 必须先过** :func:`~server.external.tdx_kline.canonical_code`。
   不归一的话 ``sh600570`` 会跟 ``SH.600570`` 插成两行；``stock_info``
   已经为这件事付过一次代价（见 canonical_code 的 docstring）。

3. **兜底组 ``default`` 恒存在且恒排最后。** 它是唯一的内置分组（"其他"），
   由 :func:`ensure_default_group` 在启动时幂等补齐，
   :func:`create_group` 也不会覆盖它。
"""
import logging
import time
import uuid
from typing import Any

from server import db
from server.external.tdx_kline import canonical_code

#: 唯一的内置分组 / 兜底组。所有未归类的自选落在这里。
#: 前端 utils/watchlist.ts 的 getAllGroups 也认这个 id，两边必须一致。
DEFAULT_GROUP_ID = "default"
DEFAULT_GROUP_LABEL = "其他"


async def ensure_default_group() -> bool:
    """幂等补齐兜底组。返回是否真的插入了一行。

    走 ``INSERT IGNORE``：主键冲突就是「已经有了」，这是正常状态而不是错误。
    """
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT IGNORE INTO watch_group (id, label) VALUES (%s, %s)",
                (DEFAULT_GROUP_ID, DEFAULT_GROUP_LABEL),
            )
            # ⚠ aiomysql 的 cur.rowcount 是 int 属性，不是协程。
            return cur.rowcount > 0


async def list_all() -> dict[str, Any]:
    """读全部分组 + 全部自选条目。

    分组顺序：``default`` 恒最后，其余按创建时间。与前端 getAllGroups()
    的规则逐条对齐，两边不会打架。

    条目顺序：**首次加入的时间**。一只票在 N 个组就有 N 个 ``added_at``，
    取 MIN 才对。这里不在 SQL 里 GROUP BY，而是直接
    ``ORDER BY added_at, code`` 后在 Python 里「首次遇到即定序」——
    得到的顺序恰好等于 MIN，代码也更好读（同一路子见 market._to_bars）。
    ``code`` 是并列时的确定性兜底。
    """
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT id, label FROM watch_group
                   ORDER BY (id = %s) ASC, created_at ASC, id ASC""",
                (DEFAULT_GROUP_ID,),
            )
            groups = [{"id": str(r[0]), "label": str(r[1])} for r in await cur.fetchall()]

            await cur.execute(
                "SELECT code, group_id FROM watch_item ORDER BY added_at ASC, code ASC, group_id ASC"
            )
            rows = await cur.fetchall()

    order: list[str] = []
    by_code: dict[str, list[str]] = {}
    for code, group_id in rows:
        code = str(code)
        if code not in by_code:
            by_code[code] = []
            order.append(code)
        by_code[code].append(str(group_id))

    return {
        "groups": groups,
        "items": [{"code": c, "groups": by_code[c]} for c in order],
    }


async def add_item(code: str, group_id: str) -> int:
    """把一只票加进某个分组。返回 1 = 新增，0 = 本来就在该分组里。

    幂等靠主键 ``(code, group_id)`` + ``INSERT IGNORE``，所以重复加自选
    不会报错、也不会插出第二行——前端反复点爱心是安全的。

    分组不存在时抛 ``ValueError``。**不静默忽略**：那样加自选会「看起来
    成功」但刷新就没了，比报错难查得多。
    """
    code = canonical_code(code)
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id FROM watch_group WHERE id = %s", (group_id,))
            if await cur.fetchone() is None:
                raise ValueError(f"分组不存在：{group_id}")
            await cur.execute(
                "INSERT IGNORE INTO watch_item (code, group_id) VALUES (%s, %s)",
                (code, group_id),
            )
            return cur.rowcount


async def remove_item(code: str, group_id: str | None = None) -> int:
    """移除自选。返回删除的行数。

    ``group_id is None`` → 整只票从自选消失；
    否则只从该分组摘掉，摘完没组了票自然消失（没有本体表可删，见模块头）。

    不存在的 code / group 同样返回 0，同样不报错：取消勾选一个已经不在
    里面的票是幂等操作，不是异常。
    """
    code = canonical_code(code)
    sql = "DELETE FROM watch_item WHERE code = %s"
    args: tuple[Any, ...] = (code,)
    if group_id is not None:
        sql += " AND group_id = %s"
        args = (code, group_id)
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            return cur.rowcount


async def create_group(label: str) -> dict[str, Any]:
    """新建分组，返回 ``{id, label}``（label 是**去重后**的最终名）。

    同名自动去重成「名称 (2)」「名称 (3)」…——这条规则原来在前端
    ``createUserGroup`` 里，现在挪到后端：只有后端能看见全部分组，
    前端看到的可能还没加载完，靠它去重会漏。

    id 由后端生成（前端造 id 有碰撞风险，且后端才是权威）。
    """
    trimmed = label.strip()
    if not trimmed:
        raise ValueError("分组名不能为空")

    async with db.pool().acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor() as cur:
                # 取**全部** label，不只是等于 trimmed 的那些：候选名是
                # 「名称 (2)」，它跟 trimmed 不相等，按 label 等值查是查不到的。
                # 分组数是十几个，全量取回来构集合最省心，也不会漏判。
                await cur.execute("SELECT label FROM watch_group")
                taken = {str(r[0]) for r in await cur.fetchall()}
                final_label = trimmed
                n = 2
                while final_label in taken:
                    final_label = f"{trimmed} ({n})"
                    n += 1

                group_id = f"g_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
                await cur.execute(
                    "INSERT INTO watch_group (id, label) VALUES (%s, %s)",
                    (group_id, final_label),
                )
            await conn.commit()
        except Exception:
            await conn.rollback()
            logging.exception("[watchlist] 新建分组失败，已回滚")
            raise
    return {"id": group_id, "label": final_label}
