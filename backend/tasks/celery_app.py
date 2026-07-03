"""Celery Application — task queue instance + Beat multi-instance lock.

Usage:
    # Start Worker (can run multiple instances):
    celery -A backend.tasks.celery_app worker -Q scheduled,default -l info -c 4

    # Start Beat (single instance, Redis lock prevents duplicates):
    celery -A backend.tasks.celery_app beat -l info

    # Optional monitoring:
    celery -A backend.tasks.celery_app flower
"""

import logging
import os
import socket

import redis
from celery import Celery
from celery.signals import beat_init

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

app = Celery("adh_tasks")

app.conf.update(
    # Broker & Backend
    broker_url=REDIS_URL,
    result_backend=REDIS_URL,
    broker_connection_retry_on_startup=True,

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
        "backend.tasks.executor.*": {"queue": "scheduled"},
    },
)

# Auto-discover tasks in executor module
app.autodiscover_tasks(["backend.tasks"])


# ── Beat Multi-Instance Lock ───────────────────────────────────────

BEAT_LOCK_KEY = "adh_celery_beat:lock"
BEAT_LOCK_TTL = 86400  # 24 hours


@beat_init.connect
def acquire_beat_lock(sender, **kwargs):
    """On Beat startup, acquire a Redis lock to prevent duplicate schedulers.

    If another Beat instance already holds the lock, shut down this instance's
    scheduler immediately. The lock auto-expires after BEAT_LOCK_TTL seconds.
    """
    try:
        r = redis.from_url(REDIS_URL, socket_connect_timeout=5)
        hostname = sender.hostname or socket.gethostname()

        acquired = r.set(BEAT_LOCK_KEY, hostname, nx=True, ex=BEAT_LOCK_TTL)
        if acquired:
            logger.info("[Beat] Lock acquired by %s", hostname)
        else:
            existing = r.get(BEAT_LOCK_KEY)
            if isinstance(existing, bytes):
                existing = existing.decode()
            logger.warning(
                "[Beat] Lock held by %s, shutting down scheduler on %s",
                existing, hostname,
            )
            sender.scheduler.shutdown()
    except redis.ConnectionError as e:
        # If Redis is down, let Beat run anyway (better than no scheduling)
        logger.warning("[Beat] Redis unavailable (%s), running without lock", e)
