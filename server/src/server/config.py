"""集中配置：MySQL 连接 + 通达信可用服务器池。

所有取值均可用环境变量覆盖，本地默认值对应本机 docker 容器。
"""
import os
from pathlib import Path

# ---------------------------------------------------------------- MySQL
MYSQL_HOST = os.getenv("ZX_MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("ZX_MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("ZX_MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("ZX_MYSQL_PASSWORD", "dba126543")
MYSQL_DB = os.getenv("ZX_MYSQL_DB", "zx_invest")
MYSQL_POOL_MIN = int(os.getenv("ZX_MYSQL_POOL_MIN", "1"))
MYSQL_POOL_MAX = int(os.getenv("ZX_MYSQL_POOL_MAX", "5"))


def dsn() -> dict:
    return {
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "user": MYSQL_USER,
        "password": MYSQL_PASSWORD,
        "db": MYSQL_DB,
    }


# ---------------------------------------------------------------- 通达信
# pytdx 内置 hq_hosts 共 104 个，实测仅以下 8 个能真正返回 K 线数据
# （2026-10-05 扫描结果；其余节点连接成功但 get_security_bars 返回 None）。
# 数据经 8 台交叉比对完全一致。
TDX_HOSTS: list[tuple[str, int]] = [
    ("117.34.114.13", 7709),
    ("117.34.114.14", 7709),
    ("117.34.114.15", 7709),
    ("117.34.114.16", 7709),
    ("117.34.114.17", 7709),
    ("117.34.114.18", 7709),
    ("117.34.114.20", 7709),
    ("117.34.114.27", 7709),
]

TDX_CONNECT_TIMEOUT = 6.0

# 单次请求最多取多少根日线。800 是通达信协议上限。
TDX_MAX_BARS_PER_CALL = 800

# MVP 目标标的：上证指数
MVP_TARGET = "sh000001"

# 本地通达信 vipdoc，仅用于交叉校验，不参与同步链路。
TDX_VIPDOC = Path(os.getenv("ZX_TDX_VIPDOC", r"D:\prod-forces\finance\tdx\vipdoc"))

# 本地通达信证券名称缓存目录（shs.tnf / szs.tnf），证券简称的唯一来源。
# pytdx 全线没有名称接口（get_security_list 返回 None），只能从本地 TDX 客户端取。
TDX_HQ_CACHE = Path(
    os.getenv("ZX_TDX_HQ_CACHE", str(TDX_VIPDOC.parent / "T0002" / "hq_cache"))
)
