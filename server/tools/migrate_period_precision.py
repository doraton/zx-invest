"""kline_period 精度收窄：与 kline_day 逐列对齐。

背景
----
``kline_day`` 在 2026-10-09 已从 4 位收窄到 2 位（价格）/ 3 位（成交额），
``kline_period`` 没跟上，一直是全表 4 位。收窄前实测 1542 行里：

    open/high/low/close/pre_close/chg_pct/volume  第 3 位   全为 0
    amount                                        第 4 位   全为 0

也就是说那些位数**一行都没用上**，纯占位。0AMV 那条依据同样适用：
源数据是 float32，0AMV 常用量级 ~152000 处 ulp = 0.0156，存 4 位等于
声称精度比来源细 150 倍。

⚠️ 为什么不能只改 DDL
--------------------
``db._run_migrations`` 只会 ADD 缺失的列，**不会 MODIFY 已存在但类型不对的列**
（见 db.py 顶部「精度变更不会被 _run_migrations 传播」）。只改 ``db.py`` 的
``_KLINE_PERIOD_DDL`` 会留下一张「schema 说 2 位、库里还是 4 位」的表。

⚠️ 为什么现在做
--------------
改 DECIMAL 精度是 COPY 算法，会**重建整张表**。现在全库只有 1542 行，
代价接近 0；等铺满全市场（百万行）再改就是一次要命的重建。
且收窄是**无损**的——超位值实测为 0 行，MySQL 转换时不会丢任何信息。

⚠️ 为什么必须和物化重跑放在一起
------------------------------
精度收窄后 ``round(a,2) - round(b,2) != round(a - b, 2)``，
``chg_amt`` / ``chg_pct`` 必须按**已舍入**的 close / pre_close 现算。
本脚本收窄完会重跑一遍全量物化（``materialize_periods`` 本身就是全量重算），
把三列按新精度重写一遍。

用法::

    .\\.venv\\Scripts\\python.exe -W ignore tools\\migrate_period_precision.py
    .\\.venv\\Scripts\\python.exe -W ignore tools\\migrate_period_precision.py --dry-run
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server import db, kline_repo  # noqa: E402

#: 列名 -> 目标类型。与 kline_day 逐列对齐（见 db.py `_KLINE_DDL`）。
TARGET_TYPES = {
    "open": "DECIMAL(12,2)",
    "high": "DECIMAL(12,2)",
    "low": "DECIMAL(12,2)",
    "close": "DECIMAL(12,2)",
    "pre_close": "DECIMAL(12,2)",
    "chg_amt": "DECIMAL(12,2)",
    "chg_pct": "DECIMAL(10,2)",
    "volume": "DECIMAL(20,2)",
    "amount": "DECIMAL(20,3)",
    "ah_vol": "DECIMAL(20,2)",
    "ah_amt": "DECIMAL(20,3)",
}

#: 目标列序 = **kline_day 逐列照抄**，只在 code 之后插一个 period。
#:
#: ⚠️ ``chg_amt`` / ``ah_vol`` / ``ah_amt`` 是 2026-10-13 用 ADD COLUMN 补的，
#:    **一律追加在表尾**，与 ``db._KLINE_PERIOD_DDL`` 写的顺序不符，
#:    也破坏了「核心列顺序与 kline_day 保持一致」那条约定。
#:    MySQL 没有 MODIFY POSITION，只能 ``MODIFY ... AFTER <列>``（会重排整表）。
COLUMN_ORDER = ("code", "period", "trade_date", "open", "high", "low", "close",
                "pre_close", "chg_amt", "chg_pct", "volume", "amount",
                "ah_vol", "ah_amt")

#: 目标小数位，用于「有多少行会真的丢信息」的预检。
TARGET_SCALE = {"amount": 3, "ah_amt": 3}
DEFAULT_SCALE = 2

#: 非量价列的原始定义（列序归位时 MODIFY 要重写完整定义，不能只写量价列）。
_PASSTHROUGH = {
    "code": "VARCHAR(16) NOT NULL",
    "period": "VARCHAR(8) NOT NULL",
    "trade_date": "DATE NOT NULL",
}
#: 无 DEFAULT 的 NOT NULL 列，列序归位时补回 DEFAULT 0。
#: ⚠️ ah_vol / ah_amt **不在这里**——两列可空（盘后交易不是每天都发生），
#:    补 DEFAULT 0 会把「没有」重新写成「零」。
_DEFAULTS = {"volume": " DEFAULT 0", "amount": " DEFAULT 0"}


def _coldef(col: str) -> str:
    """列的完整定义（类型 + 可空 + 默认值），列序归位时给 MODIFY 用。"""
    if col in _PASSTHROUGH:
        return _PASSTHROUGH[col]
    tail = " NOT NULL" + _DEFAULTS.get(col, "") if col in _DEFAULTS else " NULL"
    return f"{TARGET_TYPES[col]}{tail}"


async def main(dry_run: bool) -> int:
    await db.init_pool()
    print("== 1. 跑 init_schema（补 chg_amt 等缺失列）==")
    await db.init_schema()
    print("   已执行。下面的列清单是权威核对依据"
          "（ADD COLUMN 会把新列追加到表尾，列序与 DDL 不同但不影响按名取列）")

    table = db.KLINE_PERIOD_TABLE

    print("\n== 2. 预检：有多少行的值超出目标精度 ==")
    lossy_total = 0
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            for col, typ in TARGET_TYPES.items():
                scale = TARGET_SCALE.get(col, DEFAULT_SCALE)
                await cur.execute(
                    f"SELECT COUNT(*) FROM {table} "
                    f"WHERE {col} IS NOT NULL AND {col} <> ROUND({col}, {scale})")
                lossy = (await cur.fetchone())[0]
                lossy_total += lossy
                await cur.execute(
                    "SELECT column_type FROM information_schema.columns "
                    "WHERE table_schema=DATABASE() AND table_name=%s AND column_name=%s",
                    (table, col))
                row = await cur.fetchone()
                cur_type = row[0] if row else "(缺列)"
                flag = "  <-- 会丢数据!" if lossy else ""
                print(f"   {col:10s} {cur_type:16s} -> {typ:14s} "
                      f"超位 {lossy} 行{flag}")
            if lossy_total and not dry_run:
                print("\n   ⚠️ 存在超位行，收窄会丢精度。先跑 --dry-run 确认。")
                await db.close_pool()
                return 1

    if dry_run:
        print("\n--dry-run：未执行 ALTER")
        await db.close_pool()
        return 0

    print("\n== 3. ALTER 收窄 ==")
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            for col, typ in TARGET_TYPES.items():
                await cur.execute(
                    "SELECT column_type FROM information_schema.columns "
                    "WHERE table_schema=DATABASE() AND table_name=%s AND column_name=%s",
                    (table, col))
                row = await cur.fetchone()
                if row is None:
                    print(f"   {col:10s} 列不存在，跳过")
                    continue
                if row[0].lower() == typ.lower():
                    print(f"   {col:10s} 已是 {typ}，跳过")
                    continue
                # 列名加反引号：change 是 MySQL 保留字，本项目的既有约定。
                # 用 _coldef 而不是光写类型：NOT NULL DEFAULT 0 的量额列
                # 若只写类型+NULL，会把 DEFAULT 和 NOT NULL 一起丢掉。
                await cur.execute(
                    f"ALTER TABLE `{table}` MODIFY COLUMN `{col}` {_coldef(col)}")
                print(f"   {col:10s} {row[0]} -> {_coldef(col)}")

    print("\n== 4. 列序归位（ADD COLUMN 会把 chg_amt 追加到表尾）==")
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name=%s "
                "ORDER BY ordinal_position", (table,))
            before = [r[0] for r in await cur.fetchall()]
        print(f"   当前 {before}")
        if before != list(COLUMN_ORDER):
            # 逐列「挪到 prev 之后」，按目标顺序从左往右走一遍即可。
            for i, col in enumerate(COLUMN_ORDER):
                if i == 0:
                    continue
                prev = COLUMN_ORDER[i - 1]
                if before == list(COLUMN_ORDER):
                    break
                async with db.pool().acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            f"ALTER TABLE `{table}` MODIFY COLUMN `{col}` "
                            f"{_coldef(col)} AFTER `{prev}`")
                print(f"   {col} -> AFTER {prev}")
        async with db.pool().acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=DATABASE() AND table_name=%s "
                    "ORDER BY ordinal_position", (table,))
                after = [r[0] for r in await cur.fetchall()]
        print(f"   结果 {after}")
        ok = after == list(COLUMN_ORDER)
        print(f"   {'列序已与 db._KLINE_PERIOD_DDL 一致 ✅' if ok else '⚠️ 仍未对齐'}")

    print("\n== 5. 重跑全量物化（pre_close / chg_amt / chg_pct 按新精度重算）==")
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT DISTINCT code FROM {table} ORDER BY code")
            codes = [r[0] for r in await cur.fetchall()]
    for code in codes:
        n = await kline_repo.materialize_periods(code)
        total = sum(n.values())
        detail = " ".join(f"{k}={v}" for k, v in sorted(n.items()))
        print(f"   {code}: {total} 行 ({detail})")

    print(f"\n完成。收窄了 {len(TARGET_TYPES)} 列，物化重跑 {len(codes)} 个标的。")
    await db.close_pool()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只预检不改表")
    sys.exit(asyncio.run(main(ap.parse_args().dry_run)))
