#!/bin/bash
# zx-stock 每周元数据刷新 —— 由 crontab 在周日 03:00 调用。
#
# 为什么需要它：每日任务带 --skip-meta，会**连除权记录 stock_xdxr 一起跳过**。
# 而 Tushare 的 dividend 接口和 stock_basic 都是 5 次/天，没法每天调。
#
# 后果是：某天如果发生分红除权（恒生电子年年都在 5、6 月），
# 日线能补上、stock_xdxr 补不上 → 复权因子不变 →
# **前复权价格会一直是错的**，而且不报错、图上看着「像对的」。
# 所以每周兜一次全量，把这两个表刷到最新。
#
# 5 次/天是**按自然日重置**的，不是滚动窗口，
# 所以周日这次调用不影响周一~周五 18:00 的日线任务。
set -u

BASE=/home/doraton/zx-deploy
cd "$BASE/server" || { echo "cd 失败: $BASE/server"; exit 1; }

ENVF="$BASE/zx-stock.env"
[ -f "$ENVF" ] || { echo "缺少 $ENVF（数据库连接参数）"; exit 1; }
set -a
. "$ENVF"
set +a

LOCK="$BASE/.run-weekly-meta.lock"
exec 9>"$LOCK"
if command -v flock >/dev/null 2>&1; then
  flock -n 9 || { echo "[$(date '+%F %T')] 上一次还没结束，本次跳过"; exit 0; }
fi

# 这里**不带** --skip-meta。重点是 stock_xdxr / stock_info，
# 日期窗口只影响日线重拉，取最近 7 天足够（周日那几天本来也没新交易日）。
START=$(date -d '7 days ago' +%Y%m%d)
END=$(date +%Y%m%d)

echo "[$(date '+%F %T')] 刷新元数据 + 补日线 $START ~ $END"

"$BASE/venv/bin/python" tools/sync_tushare.py \
    --code 600570.SH \
    --start "$START" \
    --end "$END"
rc=$?

# ---- 顺带核对 stock_xdxr 与日线里的除权日是否对得上 ----
# 两条独立来源：xdxr 表里的 category=1（除权除息），
# 与日线里 pre_close ≠ 前一根 close 的日子。
# 对不上说明有个除权日漏了 —— 那正是复权价算错的场景，**要看见**。
"$BASE/venv/bin/python" - <<'PYEOF'
import asyncio, sys
sys.path.insert(0, "src")
from server import db

CODE = "SH.600570"

async def main():
    await db.init_pool()
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT ex_date FROM stock_xdxr WHERE code=%s AND category=1", (CODE,))
            xdxr = {r[0].isoformat() for r in await cur.fetchall()}
            await cur.execute(
                "SELECT trade_date FROM (SELECT trade_date, pre_close,"
                " LAG(close) OVER (ORDER BY trade_date) AS prev FROM kline_day"
                " WHERE code=%s) t WHERE pre_close IS NOT NULL AND pre_close <> prev",
                (CODE,))
            from_kline = {r[0].isoformat() for r in await cur.fetchall()}
    await db.close_pool()

    only_xdxr = sorted(xdxr - from_kline)
    only_kline = sorted(from_kline - xdxr)
    print(f"[xdxr 核对] xdxr={len(xdxr)} 日线判定={len(from_kline)}")
    if only_xdxr:
        # 这类是正常的：xdxr 记了但当天日线没判出跳空，通常是那天落在数据区边缘
        # （没有前一根可比）或当天没交易。列出来供人工判断。
        print(f"[xdxr 核对] 仅 xdxr 有: {only_xdxr}")
    if only_kline:
        print(f"[xdxr 核对] ⚠️ 仅日线判出跳空（xdxr 缺）: {only_kline}")
        print("           这类会让复权因子算错，请检查 stock_xdxr 是否没同步上。")
    if not only_kline:
        print("[xdxr 核对] ✅ 无缺失")

asyncio.run(main())
PYEOF

echo "[$(date '+%F %T')] 结束 rc=$rc"
exit $rc