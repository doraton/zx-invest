# 每日日K 定时同步 · 部署说明

> 目标：周一~周五 18:00 自动拉恒生电子（`SH.600570`）日线入 MySQL，随即重建派生周期。
> 代码由发布流程送到 `/home/doraton/zx-deploy`，本文只讲定时任务这一层。

## 相关文档

| 文档 | 内容 |
|---|---|
| [database-schema.md](../docs/database-schema.md) | 两张 K 线表结构、派生周期物化 |
| [tushare-reference.md](../docs/tushare-reference.md) | Tushare 接口与配额 |
| [pre-close-display-plan.md](../docs/pre-close-display-plan.md) | 昨收口径与派生周期列 |

## 文件职责

| 文件 | 作用 |
|---|---|
| `run-daily-sync.sh` | 每日任务主体，cron 调它 |
| `run-weekly-meta.sh` | 周日刷新除权与名录 |
| `setup-daily-sync.sh` | 体检 / 安装 / 测试 / 装 crontab |

**crontab 里不写日期表达式**：cron 要求 `%` 转义，`$(date +%Y%m%d)` 直接写在
crontab 行里会变成字面量。日期计算放在脚本里，crontab 只有固定路径。

## 部署步骤

发布流程把 `server/` 送到 `/home/doraton/zx-deploy` 后：

```bash
cd /home/doraton/zx-deploy
cp server/deploy/*.sh .           # 三个脚本提到 BASE 下
chmod +x run-daily-sync.sh run-weekly-meta.sh
```

然后把 Tushare token 放好 —— **脚本从项目根读它**（`parents[2]`）：

```bash
cp /path/to/config.txt /home/doraton/zx-deploy/config.txt
chmod 600 /home/doraton/zx-deploy/config.txt
```

体检 → 安装 → 试跑 → 挂定时：

```bash
./setup-daily-sync.sh check      # 只读，看缺什么
./setup-daily-sync.sh install    # 装 tushare + 重建派生周期清陈旧行
./setup-daily-sync.sh test       # 手动跑一次，确认拉得通
./setup-daily-sync.sh cron       # 写入 crontab
./setup-daily-sync.sh status     # 之后随时看结果
```

## 排期

| 时间 | 任务 | 内容 |
|---|---|---|
| 周一~周五 18:00 | `run-daily-sync.sh` | 日线增量 + 物化周期 |
| 周日 03:00 | `run-weekly-meta.sh` | `stock_xdxr` / `stock_info` + 核对除权 |

18:00 是收盘后 3 小时，此时 Tushare 当日数据已稳定。
周日那次是因为 `dividend` / `stock_basic` 都限 5 次/天，没法每天调 ——
**每日任务带 `--skip-meta` 会连除权记录一起跳过**，若某天分红除权，
日线补上了而 `stock_xdxr` 补不上，复权因子就不变，前复权价会一直错且不报错。

## 两个必须显式传的参数

| 参数 | 不传的后果 |
|---|---|
| `--end` | 默认值 `20260930` 早已过期，**静默拉不到数据且不报错** |
| `--skip-meta` | 每日调用会撞上 5 次/天 的配额，触发 65s 退避 |

## 数据健康度

`setup-daily-sync.sh` 的每个子命令都会顺带跑一次**陈旧行检测**：

```
  OK    week     表内=1151  日线桶=1151  差=0
```

派生周期的 `trade_date` 是**桶内最后一个交易日**，本周每多一个交易日桶尾就变一次。
若物化用 upsert，桶尾一换就成了新主键，一天一行、一周五行，接口会返回同一周五根假 K 线。
2026-10-14 已把物化改成「先删后插」（`kline_repo.materialize_periods_on`），
所以差值必须恒为 0。**不为 0 就是出问题了**，别只看接口有没有报错。

## 部署后必须确认

| # | 事项 | 判据 |
|---|---|---|
| 1 | 服务器代码含本次修复 | `check` 第 4 节按**特征**判定（物化里有 `DELETE FROM`、`MIGRATIONS` 用单表登记 `pre_close`）。旧 `db.py` 的 `MIGRATIONS` 会在 `init_schema()` 时把已删除的 `kline_period.pre_close` 加回来 |
| 2 | `kline_period` 仍是 13 列 | `check` 第 6 节。不是就说明被旧迁移复活了 |
| 3 | 陈旧行差值为 0 | `check` 第 5 节 |
| 4 | 后端重启后仍正常 | 推了新代码后要重启 `run-backend.sh` 拉起的 uvicorn |
| 5 | cron 服务在跑 | `systemctl status cron`。服务器 `Linger=no`，注销后 cron 不一定被拉起 |

## 已知边界

- **A 股节假日**：限周一~周五，节假日跑空无害（拉不到新日期，upsert 0 行）。
- **本地库与服务器库是两份**。本机 `sync_tushare.py` 默认连本机 `zx_invest`，
  服务器连的是容器里的 `mysql-8`。两边各自独立，别以为推一次两边都会更新。
- **0AMV 不在此任务内**：指南针数据只在本机（`ZX_COMPASS_ROOT`），
  服务器永远拉不到，需要另一条本机任务。