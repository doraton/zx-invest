#!/bin/bash
# zx-stock 每日日K增量同步 —— 由 crontab 在周一~周五 18:00 调用。
#
# 为什么单独一个脚本、而不是把命令行直接写进 crontab：
#   1. crontab 里 **% 必须转义成 %**，`$(date +%Y%m%d)` 会变成字面量而永远拿到
#      今天的日期（甚至可能让 cron 解析报错）。放进脚本就没这个坑。
#   2. 数据库口令从 zx-stock.env 读，不落进 crontab 文件。
#   3. 可以手动执行同一条命令来验证，和定时跑的是同一份逻辑。
#
# 用法（一般不用手动跑，交给 cron）：
#   /home/doraton/zx-deploy/run-daily-sync.sh
set -u

BASE=/home/doraton/zx-deploy
cd "$BASE/server" || { echo "cd 失败: $BASE/server"; exit 1; }

# 数据库连接参数在后端同一个 env 文件里，键名 ZX_MYSQL_*。
# 不设的话脚本会退回去读本机默认配置，连不上容器里的 mysql-8。
# 这里必须判存在：`.` 失败时 bash 不会中止（没开 set -e），
# 结果就是拿着默认配置去连库，报一个跟真实原因无关的连接错误。
ENVF="$BASE/zx-stock.env"
[ -f "$ENVF" ] || { echo "缺少 $ENVF（数据库连接参数）"; exit 1; }
set -a
. "$ENVF"
set +a

# ---- 防止上一次没跑完就叠一个（拉数 + 物化在 1500 行这个量级很快，
#      但网络卡住时可能挂很久）。抢不到锁就直接退出，不报错。----
LOCK="$BASE/.run-daily-sync.lock"
exec 9>"$LOCK"
if command -v flock >/dev/null 2>&1; then
  flock -n 9 || { echo "[$(date '+%F %T')] 上一次还没结束，本次跳过"; exit 0; }
fi

# ---- 回看 15 天 ----
# 日线是 upsert（幂等），重拉已入库的日期只是覆盖，不会产生重复行。
# 回看的意义是**自愈**：某天机器关机、网络断了没拉成，后面几天会自动补上。
# 15 天足够覆盖一个长假；再长也只是白拉，不会错。
START=$(date -d '15 days ago' +%Y%m%d)
END=$(date +%Y%m%d)

echo "[$(date '+%F %T')] 开始同步 $START ~ $END"

# --end 必须显式传：脚本默认值是 20260930，早就过期了，
#   不传会静默拉不到任何近期数据，而且**不报错**。
# --skip-meta 跳过 stock_basic / dividend，这两个各限 5 次/天；
#   除权记录 stock_xdxr 由每周日的 run-weekly-meta.sh 负责刷新。
"$BASE/venv/bin/python" tools/sync_tushare.py \
    --code 600570.SH \
    --start "$START" \
    --end "$END" \
    --skip-meta
rc=$?

echo "[$(date '+%F %T')] 结束 rc=$rc"
exit $rc