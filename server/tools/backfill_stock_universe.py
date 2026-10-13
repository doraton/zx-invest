"""把本地通达信名称表里的**沪深主板 / 创业板 / 科创板**股票灌进 ``stock_info`` 表。

与 ``backfill_stock_from_klines.py`` 的分工
----------------------------------------
两个脚本看起来在做同一件事，其实目标完全不同，**别合并**：

======================  ==========================  ==========================
脚本                    写入范围                    服务于
======================  ==========================  ==========================
backfill_stock_from_    只有 ``kline_day`` 里真的    行情列表的「名称」补充
klines.py               有过行情的代码（当前 1 只）
本脚本                  本地 tnf 里的沪深 A 股        搜索 / 键盘精灵的**召
                        全部代码段（约 5200 只）    回空间**
======================  ==========================  ==========================

收录口径
--------
tnf 里约 8896 条，但**只有沪深主板 / 创业板 / 科创板的股票**进表
（代码段见 ``_A_SHARE_PREFIXES``）。指数、基金/ETF/LOF、B 股、债券、
回购一律排除——否则键盘精灵输 ``510300`` 会搜出 ETF，输一个债券代码
也会被当成「股票」。

所以「stock_info 表约 5200 行、kline_day 只有 1 行」不是脏数据：
两张表回答的是两个不同的问题——

- ``stock_info`` 回答「**沪深 A 股有哪些票**」（名录）
- ``kline_day`` 回答「**我装了哪些票的行情**」（数据）

行情列表（``/api/stock/list``）以 ``kline_day`` 为准，不会因为本脚本
灌了全市场就多出几千行。名录与数据分离是有意为之：先能搜到、点进去
再提示「还没装这只票的行情」，比「搜不到」友好得多。

数据来源与已知限制
------------------
名称来自本地通达信客户端缓存 ``T0002/hq_cache/{shs,szs}.tnf``
（``tdx_local.load_name_map()``），不吃任何网络额度。拼音首字母由
``stock_repo.pinyin_initials``（pypinyin）从名称算出。

**这份名录是本地快照，不是权威名录**：

1. 反映的是「你本机通达信客户端最后一次更新行情的时刻」，不是今天。
   换台机器跑，出来的结果可能不同。
2. **区分不出退市 / 暂停上市的代码**——tnf 只是「代码 → 简称」对照表，
   没有在市标记，代码段和正常个股一模一样。
3. 代码段表是按前缀硬编码的，规则来自交易所现行代码分配，不随时间更新。

权威名录应该用 Tushare ``stock_basic``（``docs/architecture.md`` §5.3
本来就有规划），它的 ``list_status`` 能区分退市。本脚本是 Tushare 额度
耗尽期间的替代品。

用法::

    .\\.venv\\Scripts\\python.exe tools\\backfill_stock_universe.py --dry-run
    .\\.venv\\Scripts\\python.exe tools\\backfill_stock_universe.py
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("ZX_MYSQL_DB", "zx_invest")

import aiomysql  # noqa: E402

from server import db, stock_repo  # noqa: E402
from server.external import tdx_local  # noqa: E402
from server.external.tdx_kline import canonical_code, split_code  # noqa: E402

#: ``stock_info`` 表只收录**沪深主板 / 创业板 / 科创板**的股票。
#:
#: tnf 里一共约 8896 条，但其中只有约一半是 A 股个股，其余是指数、基金、
#: B 股、债券、回购——这些进了 ``stock_info`` 会让键盘精灵搜出 ``510300`` 这种
#: ETF，搜「股票」却搜到债券。
#:
#: **必须按「市场前缀 + 代码段」判断，不能只看 6 位码**：
#: ``sh000001`` 是上证指数而 ``sz000001`` 是平安银行——同一段码跨市场
#: 含义完全相反，只按代码前缀过滤会把这只票误删。
#:
#:   沪主板 600/601/603/605 · 科创板 688
#:   深主板 000/001/002/003（002/003 是原中小板，2021-04 已并入主板）
#:         创业板 300/301
#:
#: 已知的做不到：**退市 / 暂停上市的代码区分不出来**。tnf 只是
#: 「代码 → 简称」对照表，没有在市标记，代码段也和正常个股完全一样。
#: 要剔除只能靠 Tushare ``stock_basic`` 的 ``list_status``（有额度后由
#: ``sync_stock`` 处理）或行情源。
_A_SHARE_PREFIXES = {
    "sh": frozenset({"600", "601", "603", "605", "688"}),
    "sz": frozenset({"000", "001", "002", "003", "300", "301"}),
}


def _prune_sql() -> str:
    """把 ``stock_info`` 表裁到白名单。**白名单式删除**（NOT 白名单），不用黑名单。

    生成 ``code NOT LIKE 'sh600%' AND ...``：新加的代码段默认安全，
    只有明确在白名单里的才留得住。反过来写黑名单（``DELETE WHERE code LIKE
    'sh5%' OR ...``）漏写一段就会静默删掉正常的股票。
    """
    likes = " AND ".join(f"code NOT LIKE '{p}{seg}%'"
                         for p, segs in _A_SHARE_PREFIXES.items()
                         for seg in sorted(segs))
    return f"DELETE FROM stock_info WHERE {likes}"


def collect_rows() -> list[dict]:
    """把 tnf 里**沪深主板 / 创业板 / 科创板**的证券转成 ``stock_info`` 行（含拼音首字母）。

    见 :data:`_A_SHARE_PREFIXES` 的过滤口径。
    """
    name_map = tdx_local.load_name_map()
    rows: list[dict] = []
    kept = skipped = 0
    # 同一个 6 位代码在 sh / sz 的 tnf 里各自独立，不跨市场合并
    # （000002 在 shs.tnf 是上证A股指数、在 szs.tnf 是万科A）。
    for prefix, table in name_map.items():
        for raw, name in table.items():
            if raw[:3] not in _A_SHARE_PREFIXES.get(prefix, ()):
                skipped += 1
                continue
            # 落库 code 走 canonical_code 归一成 SH.600570 —— 这是全库唯一形态。
            # 此前这里直接存 f"{prefix}{raw}"（sh600570，小写无点），而
            # sync_tushare.py 存的是 SH.600570，两个写入方各造各的，
            # 导致 stock_info 和 kline_day 的 code 接不上。
            code = canonical_code(f"{prefix}{raw}")
            try:
                market, raw_from_split = split_code(code)
            except ValueError:
                # split_code 认不出的代码段直接跳过，免得 market 列拿到错值——
                # 那会让按 market 过滤的接口出静默错误。
                continue
            kept += 1
            rows.append({
                "code": code,
                "raw_code": raw_from_split,
                "market": market,
                "name": name,
                "pinyin": stock_repo.pinyin_initials(name),
                # 以下几项没有来源，留 NULL；Tushare 额度恢复后由
                # sync_stock 补齐（upsert 用 COALESCE，不会被 NULL 覆盖）。
                "ipo_date": None,
                "total_share": None,
                "float_share": None,
                "industry_code": None,
                "province_code": None,
            })
    print(f"  白名单命中 {kept} 条，按前缀排除 {skipped} 条"
          f"（指数 / 基金 / B股 / 债券 / 回购等）")
    return rows


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="只打印统计，不落库")
    ap.add_argument("--limit", type=int, default=0, help="只回填前 N 条（调试用，0=全部）")
    args = ap.parse_args()

    rows = collect_rows()
    by_prefix: dict[str, int] = {}
    for r in rows:
        p = r["code"][:2]
        by_prefix[p] = by_prefix.get(p, 0) + 1
    print(f"tnf 解析出的证券: {len(rows)} 条  {by_prefix}")
    no_name = sum(1 for r in rows if not r["name"])
    no_pinyin = sum(1 for r in rows if not r["pinyin"])
    print(f"缺名称: {no_name}    缺拼音: {no_pinyin}")

    if args.limit > 0:
        rows = rows[: args.limit]
        print(f"--limit {args.limit}：本次只处理前 {len(rows)} 条")

    for r in rows[:5]:
        print(f"  样例: {r['code']}  {r['name']}  market={r['market']}  pinyin={r['pinyin']}")

    if args.dry_run:
        print("\n--dry-run：未写入。")
        return 0
    if not rows:
        print("\n没有可写入的行——tnf 目录是不是不对？检查 config.TDX_HQ_CACHE。")
        return 1

    await db.init_pool()
    try:
        # 必须调 init_schema()：init_pool() 只建连接池，不跑建表/补列。
        # 少了这步，用到新加的 stock_info.pinyin 列时会直接报
        # 1054 Unknown column 'pinyin'。
        applied = await db.init_schema()
        if applied:
            print("本次执行的 schema 迁移：")
            for s in applied:
                print(f"  {s}")

        async with db.pool().acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT COUNT(*) FROM stock_info")
                before = (await cur.fetchone())[0]

        # 先裁后插。裁剪是白名单式 DELETE，每次跑都幂等——
        # 早期版本没加前缀过滤，往库里灌过 8896 条（含 3671 条指数/基金/债券），
        # 这一步就是用来把那些行清掉的。不裁的话键盘精灵会搜出 ETF。
        async with db.pool().acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(_prune_sql())
                pruned = cur.rowcount
        print(f"按白名单裁剪: 删除 {pruned} 条非 A 股个股行"
              f"（{before} -> {before - pruned}）")

        # 批量 executemany，不走 upsert_stock 逐条 acquire：
        # 五千多条逐条往返要几分钟，批量一次几秒。
        # 语句与 stock_repo._UPSERT_STOCK 同源（同样只更新非空值）。
        sql = """
            INSERT INTO stock_info (code, raw_code, market, name, pinyin, ipo_date,
                               total_share, float_share, industry_code, province_code,
                               updated_at)
            VALUES (%s, %s, %s, %s, %s, NULL, NULL, NULL, NULL, NULL, NOW())
            ON DUPLICATE KEY UPDATE
                raw_code=VALUES(raw_code), market=VALUES(market),
                name=COALESCE(VALUES(name), name),
                pinyin=COALESCE(VALUES(pinyin), pinyin),
                updated_at=NOW()
        """
        payload = [(r["code"], r["raw_code"], r["market"], r["name"], r["pinyin"])
                   for r in rows]
        async with db.pool().acquire() as conn:
            async with conn.cursor() as cur:
                for i in range(0, len(payload), 500):
                    await cur.executemany(sql, payload[i:i + 500])
                    print(f"  已写入 {min(i + 500, len(payload))}/{len(payload)}")
            await conn.commit()
        print(f"\n已写入 {len(rows)} 条（upsert）。")

        # 回读确认，不靠「以为写进去了」
        async with db.pool().acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT COUNT(*) AS n, COUNT(name) AS n_name, COUNT(pinyin) AS n_py "
                    "FROM stock_info")
                stat = await cur.fetchone()
                await cur.execute(
                    "SELECT code, raw_code, market, name, pinyin FROM stock_info "
                    "WHERE pinyin IS NOT NULL ORDER BY code LIMIT 5")
                sample = await cur.fetchall()

        print(f"stock_info 表: {before} -> {stat['n']} 行"
              f"（有名称 {stat['n_name']}，有拼音 {stat['n_py']}）")
        print("回读样例：")
        for r in sample:
            print(f"  {r['code']}  {r['name']}  pinyin={r['pinyin']}  market={r['market']}")

        # 端到端自检：走真实调用路径（stock_repo.search_stocks），不复制 SQL。
        # 反例（must_miss）比正例更重要——它们证明白名单过滤真的生效了，
        # 而不是「过滤代码写了但压根没执行」。
        print("\n自检（走 search_stocks 真实路径）：")
        ok = True
        for probe, must_hit in (
            ("hsdz", "恒生电子"),      # 拼音首字母
            ("恒生", "恒生电子"),       # 汉字子串
            ("600570", "sh600570"),    # 6 位数字码
            ("pfyh", "浦发银行"),       # 另一只，验拼音不是只对一只生效
            ("sz000001", "平安银行"),   # 深主板：验证 sz000 段没被沪指数规则误删
        ):
            hits = await stock_repo.search_stocks(probe, limit=5)
            summary = ", ".join(f"{h['code']} {h['name']}" for h in hits) or "无命中"
            mark = ""
            if must_hit:
                got = any(must_hit in f"{h['code']}{h['name']}" for h in hits)
                mark = "  OK" if got else "  <<< 没命中，异常"
                ok = ok and got
            print(f"  [命中] {probe!r:10} -> {summary}{mark}")

        for probe in ("510300", "sh000001", "sz399001"):
            hits = await stock_repo.search_stocks(probe, limit=5)
            if hits:
                ok = False
                print(f"  [排除] {probe!r:10} -> 仍命中 "
                      + ", ".join(f"{h['code']} {h['name']}" for h in hits)
                      + "   <<< 白名单没生效")
            else:
                print(f"  [排除] {probe!r:10} -> 无命中  OK")

        if not ok:
            print("\n自检未通过。")
            return 1
        return 0
    finally:
        await db.close_pool()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))