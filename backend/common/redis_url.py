"""Redis 连接串装配（唯一真值源）—— 防 NOAUTH 静默降级。

配置面允许两种写法：整串 `REDIS_URL`，或分项 `REDIS_HOST/PORT/DB/PASSWORD`。
历史缺陷：`REDIS_URL` 里不带凭据、密码只写在 `REDIS_PASSWORD` 时，所有走
`redis.from_url(REDIS_URL)` 的通道（统一缓存 / Celery broker+result backend /
图谱重建互斥锁 / 监控探测）一律被服务端拒 `NOAUTH`（"Authentication required."）：
- 缓存侧只留一行 WARNING，把失败掩成"无缓存"，跑起来照常出数，没人发现；
- Celery 侧 worker 根本连不上 broker、beat 租约丢失暂停派发，定时任务整体停摆。

口径：凭据只有一个装配口 —— URL 已自带 userinfo 时以 URL 为准；URL 缺凭据且
`REDIS_PASSWORD` 有值时注入（密码按 URL 规则转义）。展示一律过 `redact_redis_url`，
凭据不出服务端（security-guardrails §7）。
"""

import os
from urllib.parse import quote, urlsplit, urlunsplit

__all__ = ["effective_redis_url", "redact_redis_url", "describe_redis_target"]


def effective_redis_url(url: str = None, password: str = None) -> str:
    """装配可用的 Redis 连接串：URL 缺凭据时用分项密码补全。

    Args:
        url: 显式连接串；None 时读 `REDIS_URL`，仍为空则由 HOST/PORT/DB 组装。
        password: 显式密码；None 时读 `REDIS_PASSWORD`。
    """
    url = (url if url is not None else os.getenv("REDIS_URL", "")).strip()
    password = (password if password is not None else os.getenv("REDIS_PASSWORD", "")).strip()
    if not url:
        host = os.getenv("REDIS_HOST", "localhost")
        port = os.getenv("REDIS_PORT", "6379")
        db = os.getenv("REDIS_DB", "0")
        url = f"redis://{host}:{port}/{db}"
    if not password:
        return url
    parts = urlsplit(url)
    if not parts.scheme.startswith("redis"):
        return url  # 非 Redis 连接串（如 sentinel/自定义）不猜测语义
    if parts.password or parts.username:
        return url  # URL 已自带凭据，以 URL 为准，不用分项覆盖
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f":{quote(password, safe='')}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def redact_redis_url(url: str) -> str:
    """抹掉连接串中的凭据，供日志/监控页展示（保留 host:port/db 便于排查）。"""
    url = (url or "").strip()
    parts = urlsplit(url)
    if not parts.password:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    user = parts.username or ""
    netloc = f"{user}:***@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def describe_redis_target(url: str) -> str:
    """面向展示/日志的地址（`host:port/db`），不携任何凭据。"""
    parts = urlsplit((url or "").strip())
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return f"{host}{parts.path}" if parts.path else host
