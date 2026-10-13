"""应用级配置（``app_config`` 表）的读写。

一张 key-value 表，给前端的**显示类设置**持久化用 —— 目前只有一项：
``show_price_line``（主图现价线显不显示）。纯内存 state 刷新页面就没了，
用户改过的设置得一次次手动改回去。

两个关键约定：

1. **默认值不归这里管。** :func:`get_all` 只返回库里真实存在的键，
   没配过的键直接不出现在返回值里，由前端用自己的默认值兜底。
   后端再存一份默认值就成了两处真相，改一处忘一处很难查。

2. **值统一以字符串落库。** 设置项类型各异（布尔 / 数字 / JSON），
   在库里不分列，读取时由 :func:`_parse` 尽量还原成 JSON 类型。
"""
import json
import logging
from typing import Any

from server import db


def _parse(raw: str) -> Any:
    """把存库的字符串还原成 JSON 值。

    只做明确无歧义的还原，转不了就保持字符串：

    ==========================  =========
    库里的值                     返回
    ==========================  =========
    ``"true"`` / ``"false"``    布尔（不分大小写，也容忍 ``"True"``）
    能被 ``int()`` 完整解析      整数
    能被 ``float()`` 完整解析    浮点数
    其余                         原样字符串
    ==========================  =========

    副作用要知道：某个设置项如果**本来就是字符串** ``"123"`` 或 ``"true"``，
    读出来会变成数字 / 布尔。当前只有布尔型的显示设置，碰不到这种冲突；
    真出现需求时正确做法是按设置项名登记类型，而不是往这里堆特判。
    """
    s = raw.strip()
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return raw


def _dump(value: Any) -> str:
    """:func:`_parse` 的反向。JSON 值 -> 存库字符串。"""
    # ⚠ bool 必须在 int 前面判：Python 里 True 是 int 的子类，
    # 不先拦下来会被写成 "1"，读回来变成数字 1 而不是布尔。
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


async def get_all() -> dict[str, Any]:
    """读全部配置项。**没配过的键不会出现在返回值里**（见模块头的约定 1）。"""
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT item_key, item_value FROM app_config")
            rows = await cur.fetchall()
    return {str(key): _parse(value) for key, value in rows}


async def upsert_all(values: dict[str, Any]) -> int:
    """写入配置项，返回受影响行数（新增 + 更新合计）。

    **只 upsert 传进来的键，不删除没传的。** 起初想做成"全量覆盖"
    —— 前端总是 PUT 完整的设置集，覆盖语义更干净。但一次误发 ``{}``
    就会把所有设置清空，而"删除某个设置"本身并不是用户要做的操作
    （恢复默认 = 删掉那一行，暂不提供删除接口），所以收敛成只增改。
    """
    if not values:
        return 0

    affected = 0
    async with db.pool().acquire() as conn:
        # 连接池是 autocommit 的，多行写入要手动开事务，
        # 否则第 3 键写失败会留下"前两个改了第三个没改"的半截状态。
        await conn.begin()
        try:
            async with conn.cursor() as cur:
                for key, value in values.items():
                    await cur.execute(
                        """INSERT INTO app_config (item_key, item_value) VALUES (%s, %s)
                           ON DUPLICATE KEY UPDATE item_value = VALUES(item_value)""",
                        (str(key), _dump(value)))
                    # ⚠ aiomysql 的 cur.rowcount 是**普通 int 属性**，不是协程。
                    #   写成 `await cur.rowcount` 会报
                    #   "TypeError: 'int' object can't be awaited"，与 kline_repo /
                    #   stock_repo 里的既有用法保持一致。
                    affected += cur.rowcount
            await conn.commit()
        except Exception:
            await conn.rollback()
            logging.exception("[config] 写入失败，已回滚")
            raise
    return affected
