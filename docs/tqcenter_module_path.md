# tqcenter 模块路径说明

## 引用方式

`tqcenter` 模块通过通达信客户端（Tdx）启动时自动注入到 `sys.path` 实现引用。

路径：`D:/prod-forces/finance/tdx-tq/PYPlugins/user/tqcenter.py`

## 工作原理

1. 通达信客户端启动时，会将 `D:/prod-forces/finance/tdx-tq/PYPlugins/user/` 自动添加到 Python 的 `sys.path`
2. 因此在此环境下 `from tqcenter import tq` 可以正常找到模块
3. 其他 Python 环境（如直接运行 `python xxx.py`）无法直接引用，会报 `ModuleNotFoundError`

## 启动方式

FastAPI 服务**必须通过通达信客户端启动**，不能直接用 `uvicorn` 运行。

### 通过 Tdx 客户端启动

将服务代码通过 Tdx 的 Python 环境执行，例如在 Tdx 的策略编辑器或插件机制中运行 `server/src/server/main.py`。

### 直接运行（不可行）

```bash
# 这会失败，tqcenter 不在 sys.path 中
python -m uvicorn server.main:app
# 错误: ModuleNotFoundError: No module named 'tqcenter'
```

## 使用约束

- `tq.initialize(__file__)` 必须通过通达信客户端启动的 Python 进程调用
- 独立运行 `python -c "import tqcenter"` 会失败
- 若通达信客户端未运行，初始化失败但被静默捕获（`except`），导致 `get_index_quote` / `get_index_kline` 返回 `None` / `[]`