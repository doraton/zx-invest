# others —— 停用存档

2026-10-09 整理。这里的东西**一次性探测产物与外部数据源调研档案，全部不再维护**。

留在这里只为「结论还有参考价值」，不是可运行的功能模块。协议漂移、接口变更、上游仓库改版都不会回头修。

## 为什么不删

探测脚本的**结论**被写进了正式代码的注释和 `docs/architecture.md`（例如 pytdx 只服务指数的节点范围、`extra` 字段长度、日线 vs 周线 vol 差 100 倍）。脚本本身留着，是为了将来真要重新验证时有个起点，而不是从零再探一遍。

## 内容

**外部数据源档案（Markdown）**

> `tushare-reference.md` 原先也在本目录，因 Tushare 已是**在用主数据源**
> （见 `../docs/database-schema.md`），于 2026-10-09 移至 `../docs/tushare-reference.md`。
> `znz-active-mv-ocr-sync.md` 同期移至 `../docs/`，它是活跃市值（0AMV）的取数方案。
> 本目录只留**未接入 / 停用 / 仅备查**的数据源。

| 文件 | 对象 | 结论摘要 |
|---|---|---|
| `pytdx-reference.md` | pytdx / 通达信协议逆向 | 主力数据源。104 个内置节点只有 8 个能返回个股 K 线 |
| `tdx2db-reference.md` | tdx2db | 本机通达信 vipdoc 解析，主要用于交叉校验真值 |
| `tx-reference.md` | 腾讯证券 HTTP | 对照实验用 |

**探测脚本（Python，一次性）**

- `probe_bar_layout.py` / `probe_categories.py` / `probe_stock_raw.py` / `probe_pytdx_capability.py`
  —— pytdx 报文结构与能力边界摸底
- `sweep_tdx_hosts.py` / `sweep_stock_bars.py`
  —— 全节点扫描，找出哪些能返回个股 K 线
- `verify_against_local.py` / `compare_sources.py` / `recap_capability.py`
  —— pytdx 库 vs 本机 `.day` 逐根对账（交叉校验的锚点）
- `probe_em*.py`（东财）/ `probe_sina*.py`（新浪）/ `probe_ts*.py`（Tushare）/ `probe_tx.py`（腾讯）/ `probe_znz_ui.py`（指南针）
  —— 其他数据源的可行性探测，均未接入生产
- `probe_api.py` / `probe_indices.py` —— 本地接口与指数标的临时排查

**残留临时脚本**（`_` 开头，本来就该随用随删，之前误提交进了仓库）

`_c2.py` `_check_state.py` `_ddl.py` `_drop_ref.py` `_probe_index.py` `_probe_stock.py` `_verify_drop.py`
以及 `_ts_chip.json` `_ts_ratelimit.json` 两份探测输出。

## 怎么跑（如果真要重跑）

脚本里的路径已全部改成 `others/xxx.py`，在 `server/` 目录下执行：

```powershell
cd server
..\server\.venv\Scripts\python.exe -X utf8 -W ignore ..\others\probe_categories.py sh600570
```

注意这些脚本直接写死连 `127.0.0.1:3306` 的 root 账号，**跑之前想清楚**，别顺手对生产库执行。