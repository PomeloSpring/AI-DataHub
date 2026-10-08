"""Celery Application — task queue instance + Beat multi-instance lock.

Usage:
    # Start Worker (can run multiple instances):
    celery -A backend.modules.flow.tasks.celery_app worker -Q scheduled,default -l info -c 4

    # Start Beat (single instance, Redis lock prevents duplicates):
    celery -A backend.modules.flow.tasks.celery_app beat -l info

    # Optional monitoring:
    celery -A backend.modules.flow.tasks.celery_app flower
"""

import logging
import os
from backend.common import config as _config  # 加载统一环境配置

import redis
from celery import Celery

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

app = Celery("adh_tasks")

app.conf.update(
    # Broker & Backend
    broker_url=REDIS_URL,
    result_backend=REDIS_URL,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=3,
    task_publish_retry=False,
    broker_transport_options={"socket_connect_timeout": 2, "socket_timeout": 2,
                              "visibility_timeout": 3700},
    result_backend_transport_options={"socket_connect_timeout": 2, "socket_timeout": 2},
    imports=("backend.modules.flow.tasks.executor", "backend.modules.flow.tasks.dag_tasks",
             "backend.modules.flow.tasks.eval_tasks"),
    beat_scheduler="backend.modules.flow.tasks.beat_schedule:DatabaseScheduler",

    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timezone
    timezone="Asia/Shanghai",
    enable_utc=False,

    # Worker behavior
    task_acks_late=True,              # Ack after execution, not before
    worker_prefetch_multiplier=1,     # Fetch one task at a time
    worker_hijack_root_logger=False,  # Don't override app logging

    # Timeouts
    task_soft_time_limit=600,         # Soft timeout: 10 min
    task_time_limit=660,              # Hard timeout: 11 min

    # Retry
    task_default_retry_delay=60,      # Retry after 60s
    task_max_retries=3,

    # Result expiry
    result_expires=86400,             # Results kept for 24h

    # Queues
    task_queues={
        "scheduled": {"exchange": "scheduled", "routing_key": "scheduled"},
        "default": {"exchange": "default", "routing_key": "default"},
    },
    task_default_queue="default",
    task_routes={
        "backend.modules.flow.tasks.executor.*": {"queue": "scheduled"},
        # 显式命名的报表生成任务（按名派发，见 core/task_runtime.send_task）
        "flow.generate_report": {"queue": "scheduled"},
        # 评测任务耗时不定（检索/LLM 集），与定时分析同走 scheduled 队列；
        # 不注册进 imports 的话 worker 启动时看不到它，投递会报 unregistered task。
        "backend.modules.flow.tasks.eval_tasks.*": {"queue": "scheduled"},
    },
)

# Auto-discover tasks in executor module
app.autodiscover_tasks(["backend.modules.flow.tasks"])


# ── Beat Multi-Instance Lock ───────────────────────────────────────

BEAT_LOCK_KEY = "adh_celery_beat:lease:v2"
BEAT_LOCK_TTL = 15


class BeatLease:
    """短租约持续续期；丢锁/Redis 故障暂停派发，备用实例可在租约到期后接管。"""
    def __init__(self, client=None, key=BEAT_LOCK_KEY, ttl=BEAT_LOCK_TTL):
        self.client = client or redis.from_url(REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        self.lock = self.client.lock(key, timeout=ttl, blocking=False, thread_local=False)
        self.held = False

    def renew(self):
        try:
            if self.held:
                try:
                    self.lock.reacquire()
                    return True
                except redis.exceptions.LockNotOwnedError:
                    self.held = False
            self.held = bool(self.lock.acquire(blocking=False))
            return self.held
        except redis.exceptions.RedisError:
            self.held = False
            logger.warning("Beat 租约服务不可用，暂停派发")
            return False

    def close(self):
        if self.held:
            try:
                self.lock.release()
            except redis.exceptions.RedisError:
                logger.warning("Beat 租约释放失败，等待自动过期")
            self.held = False
