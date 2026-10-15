#!/bin/bash
# zx-stock 定时同步安装 / 检查工具。
#
#   ./setup-daily-sync.sh check     只读体检，不改任何东西
#   ./setup-daily-sync.sh install   装依赖 + 重建派生周期（清陈旧行）
#   ./setup-daily-sync.sh test      手动跑一次每日任务（验证能拉通）
#   ./setup-daily-sync.sh cron      安装/更新 crontab 条目
#   ./setup-daily-sync.sh status    看最近一次同步结果 + 数据健康度
#
# 约定：代码由发布流程送到 $BASE，config.txt（Tushare token）由你放。
# 本脚本**不碰** config.txt 的内容，也不打印它。
set -u

BASE=/home/doraton/zx-deploy
PY="$BASE/venv/bin/python"
ENVF="$BASE/zx-stock.env"
CRON_TAG="# >>> zx-stock daily sync >>>"
CRON_TAG_END="# <<< zx-stock daily sync <<<"

c_red()  { printf '\033[31m%s\033[0m\n' "$*"; }
c_grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
c_yel()  { printf '\033[33m%s\033[0m\n' "$*"; }
ok()     { c_grn "  [OK]   $*"; }
warn()   { c_yel "  [WARN] $*"; }
bad()    { c_red "  [FAIL] $*"; }
head1()  { printf '\n\033[1m=== %s ===\033[0m\n' "$*"; }

# ---- 陈旧行检测：派生周期表行数 vs 从日线算出的桶数 ----
# 两者不等就是有「被取代却没删掉」的旧桶行。接口会因此返回同一周多根假 K 线。
# 2026-10-14 修掉之前，服务器上 week 就多出 1 行（1151 vs 1150）。
check_stale_rows() {
    set -a; . "$ENVF"; set +a
    # ⚠️ cwd 必须是 $BASE/server，因为下面 Python 里的 sys.path 写的是 "src"。
    #    cd 到 server/src 的话根本没有 src 子目录，import server.db 会直接失败。
    cd "$BASE/server" || { bad "cd $BASE/server"; return 1; }
    "$PY" - <<'PYEOF'
import asyncio, sys
sys.path.insert(0, "src")
from server import db

CODE = "SH.600570"
BUCKETS = {
    "week":    "YEARWEEK(trade_date,1)",
    "month":   "CONCAT(YEAR(trade_date),LPAD(MONTH(trade_date),2,'0'))",
    "quarter": "CONCAT(YEAR(trade_date),QUARTER(trade_date))",
    "year":    "YEAR(trade_date)",
}

async def main():
    await db.init_pool()
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            for p, expr in BUCKETS.items():
                await cur.execute(
                    "SELECT COUNT(*) FROM kline_period WHERE code=%s AND period=%s",
                    (CODE, p))
                stored = (await cur.fetchone())[0]
                await cur.execute(
                    "SELECT COUNT(DISTINCT " + expr + ") FROM kline_day WHERE code=%s",
                    (CODE,))
                buckets = (await cur.fetchone())[0]
                d = stored - buckets
                mark = "OK  " if d == 0 else "STALE"
                print(f"  {mark} {p:<8} 表内={stored:<6} 日线桶={buckets:<6} 差={d}")
    await db.close_pool()

asyncio.run(main())
PYEOF
}

do_check() {
    head1 "1. 目录与脚本"
    for f in "$ENVF" "$PY" "$BASE/server/tools/sync_tushare.py" \
             "$BASE/run-daily-sync.sh" "$BASE/run-weekly-meta.sh"; do
        [ -e "$f" ] && ok "存在 $f" || bad "缺失 $f"
    done

    head1 "2. Tushare token"
    if [ -f "$BASE/config.txt" ]; then
        # 只报存在性和首行长度，不打印内容
        n=$(head -1 "$BASE/config.txt" | tr -d '\r\n' | wc -c)
        if [ "$n" -gt 20 ]; then ok "config.txt 存在，首行 ${n} 字符"
        else bad "config.txt 首行只有 ${n} 字符，不像 token"; fi
        perm=$(stat -c '%a' "$BASE/config.txt")
        if [ "$perm" = "600" ] || [ "$perm" = "400" ]; then ok "权限 $perm"
        else warn "权限 $perm，建议 chmod 600（含 token）"; fi
    else
        bad "config.txt 不在 $BASE"
        echo "        sync_tushare.py 从项目根读它（parents[2]）。"
        echo "        把本机 zx-stock/config.txt 放到这个路径即可。"
    fi

    head1 "3. Python 依赖"
    for m in aiomysql pandas pymysql tushare; do
        if "$PY" -c "import $m" 2>/dev/null; then ok "$m"; else bad "$m 未安装"; fi
    done

    head1 "4. 代码版本（按特征判定，不比对 md5 —— 后续谁再改一版就会误报）"
    DBPY="$BASE/server/src/server/db.py"
    KRPY="$BASE/server/src/server/kline_repo.py"
    if [ -f "$KRPY" ]; then
        # 修复特征：物化改成「先删后插」，函数体里应有 DELETE FROM
        if awk '/^async def materialize_periods_on/,/^async def upsert_klines/' "$KRPY" \
             | grep -q 'DELETE FROM'; then
            ok "kline_repo.py 物化含「先删后插」"
        else
            bad "kline_repo.py 仍是旧版（物化只 upsert、不删旧桶）"
            echo "        增量同步会每天留下陈旧桶行，接口返回同一周多根假 K 线。"
        fi
        if grep -q 'await conn.commit()' "$KRPY"; then
            ok "materialize_periods 有 commit"
        else
            warn "materialize_periods 里没找到 commit —— 连接归还池时会被回滚"
        fi
    else
        bad "kline_repo.py 不存在"
    fi
    if [ -f "$DBPY" ]; then
        # 判据要唯一。db.py 里有多处 "for col, coldef in (" 循环
        # （新版也有，那是给 kline_day 单表补 chg_amt / ah_* 用的），
        # 盯循环会把新版误判成旧版。
        #
        # 真正区分新旧的是 **pre_close 的写法**：
        #   旧版  ("pre_close", "DECIMAL(12,4) NULL")  在列元组里 → 两张表都补
        #   新版  (KLINE_DAY_TABLE, "pre_close", ...) 单独登记一行 → 只补日线
        if grep -q 'KLINE_DAY_TABLE, "pre_close"' "$DBPY"; then
            ok "db.py：pre_close 已按单表登记（新版）"
        elif grep -q 'KLINE_PERIOD_TABLE, "pre_close"' "$DBPY"; then
            bad "代码里仍有给 kline_period 补 pre_close 的条目"
            echo "        后端一重启跑 init_schema()，这列会被加回来。"
        else
            bad "db.py 的 MIGRATIONS 结构不认识，既不像新版也不像旧版"
            echo "        人工确认一下 pre_close 补列那段。"
        fi
    else
        bad "db.py 不存在"
    fi

    head1 "5. 派生周期陈旧行"
    check_stale_rows

    head1 "6. 数据库与后端"
    set -a; . "$ENVF"; set +a
    cd "$BASE/server" || { bad "cd $BASE/server"; return 1; }
    "$PY" - <<'PYEOF' 2>&1 | grep -v Warning
import asyncio, sys
sys.path.insert(0, "src")
from server import db
async def main():
    await db.init_pool()
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM information_schema.COLUMNS "
                              "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='kline_period'")
            n = (await cur.fetchone())[0]
            print(f"  kline_period 列数 = {n}"
                  + ("  (OK，13 列)" if n == 13 else "  (⚠️ 应为 13，多了 pre_close?)"))
            await cur.execute("SELECT COUNT(*),MAX(trade_date) FROM kline_day "
                              "WHERE code='SH.600570'")
            c, mx = await cur.fetchone()
            print(f"  kline_day SH.600570 = {c} 行，最新 {mx}")
    await db.close_pool()
asyncio.run(main())
PYEOF
    echo "  后端进程："
    pgrep -af 'uvicorn server.main' | head -1 | cut -c1-78
    pgrep -f 'uvicorn server.main' >/dev/null || echo "    （未运行 —— cron 跑同步不需要它，但页面会打不开）"
}

do_install() {
    # ⚠️ 先拦一道：清陈旧行依赖新版的「先删后插」。
    #    在旧代码上跑 materialize_periods 只会 upsert，陈旧行一行都不会少，
    #    用户却会以为「装好了」——这比直接报错更难查。
    KRPY="$BASE/server/src/server/kline_repo.py"
    if [ ! -f "$KRPY" ] || \
       ! awk '/^async def materialize_periods_on/,/^async def upsert_klines/' "$KRPY" \
            | grep -q 'DELETE FROM'; then
        bad "kline_repo.py 还是旧版（物化只 upsert，不删旧桶）"
        echo "        先把新版代码发布到 $BASE/server，再重跑本命令。"
        echo "        在旧代码上重建不会清掉任何陈旧行。"
        exit 1
    fi

    head1 "安装 tushare"
    "$BASE/venv/bin/pip" install -q tushare 2>&1 | tail -3
    "$PY" -c "import tushare; print('  tushare', tushare.__version__, '安装完成')"

    head1 "给脚本加执行权限"
    chmod +x "$BASE/run-daily-sync.sh" "$BASE/run-weekly-meta.sh" 2>/dev/null

    head1 "重建派生周期（清陈旧行）"
    echo "  这会 DELETE 掉该 code 四周期的全部行再从日线重建。"
    echo "  真相源是日线，删掉的每行都会原样重建；行数对不上才说明有别的问题。"
    set -a; . "$ENVF"; set +a
    cd "$BASE/server" || { bad "cd $BASE/server"; exit 1; }
    "$PY" - <<'PYEOF'
import asyncio, sys
sys.path.insert(0, "src")
from server import db
from server.kline_repo import materialize_periods
async def main():
    await db.init_pool()
    w = await materialize_periods("SH.600570")
    print("  写入行数:", w)
    await db.close_pool()
asyncio.run(main())
PYEOF
    echo
    check_stale_rows
}

do_test() {
    head1 "手动跑一次每日任务"
    "$BASE/run-daily-sync.sh"
    echo
    check_stale_rows
}

do_cron() {
    head1 "安装 crontab"
    tmp=$(mktemp)
    crontab -l 2>/dev/null | grep -vF "$CRON_TAG" | grep -vF "$CRON_TAG_END" > "$tmp" \
        || true
    {
        echo "$CRON_TAG"
        echo "# 周一~周五 18:00 拉日K（收盘后 3 小时，此时 Tushare 数据已稳定）并物化周期"
        echo "# % 必须转义，所以日期计算放在 run-daily-sync.sh 里，这里只写死路径"
        echo "0 18 * * 1-5 $BASE/run-daily-sync.sh >> $BASE/sync-daily.log 2>&1"
        echo ""
        echo "# 周日 03:00 刷新 stock_xdxr / stock_info（各限 5 次/天，没法每天调）"
        echo "# 不带 --skip-meta，见脚本头部注释里的原因"
        echo "0 3 * * 0 $BASE/run-weekly-meta.sh >> $BASE/sync-meta.log 2>&1"
        echo "$CRON_TAG_END"
    } >> "$tmp"
    crontab "$tmp" && ok "已安装" || bad "crontab 写入失败"
    rm -f "$tmp"
    echo
    echo "--- 当前 crontab（末尾 14 行）---"
    crontab -l 2>/dev/null | tail -14
    echo
    warn "服务器 Linger=no，crontab 只在已登录会话里由 cron 服务执行，"
    warn "确认 cron 服务在跑：systemctl status cron"
}

do_status() {
    head1 "最近同步"
    if [ -f "$BASE/sync-daily.log" ]; then
        echo "--- sync-daily.log（末 25 行）---"
        tail -25 "$BASE/sync-daily.log"
    else
        warn "sync-daily.log 还不存在（定时任务没跑过）"
    fi
    if [ -f "$BASE/sync-meta.log" ]; then
        echo "--- sync-meta.log（末 10 行）---"
        tail -10 "$BASE/sync-meta.log"
    fi
    head1 "sync_state"
    set -a; . "$ENVF"; set +a
    cd "$BASE/server" || { bad "cd $BASE/server"; return 1; }
    "$PY" - <<'PYEOF' 2>&1 | grep -v Warning
import asyncio, sys
sys.path.insert(0, "src")
from server import db
async def main():
    await db.init_pool()
    async with db.pool().acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT period,last_sync,first_date,last_date,status "
                              "FROM sync_state ORDER BY period")
            print(f"  {'period':<9}{'last_sync':<21}{'范围':<26}{'状态'}")
            for p, ls, f, l, s in await cur.fetchall():
                print(f"  {p:<9}{str(ls):<21}{str(f)+' ~ '+str(l):<26}{s}")
    await db.close_pool()
asyncio.run(main())
PYEOF
    echo
    check_stale_rows
}

case "${1:-check}" in
    check)   do_check ;;
    install) do_install ;;
    test)    do_test ;;
    cron)    do_cron ;;
    status)  do_status ;;
    *)       echo "用法: $0 {check|install|test|cron|status}"; exit 2 ;;
esac