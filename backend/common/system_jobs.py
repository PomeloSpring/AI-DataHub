"""系统内置任务注册与运行状态（共享层）。

事实源是本文件的 BUILTIN_JOBS 注册表（代码声明内置任务的名称/职责/周期）；
`adh_system_jobs` 表只承载**运行态**与**人工暂停开关**，供任务监控页展示与操作。
多实例部署下暂停开关与运行状态一律落共享元库（distributed-first §1），不做进程内缓存写路径。

约定：
- 内置任务可**暂停**（is_active=0，执行器开头检查），**不可移除**（注册表由代码维护）。
- 运行结果落库是旁路观测：失败只告警不抛出，不影响内置任务主逻辑（护栏 §9）。
"""

import logging

from backend.common.db import execute_query, execute_write

logger = logging.getLogger(__name__)

# ── 注册表（事实源）────────────────────────────────────────────────
BUILTIN_JOBS: dict = {
    "runs_reconcile": {
        "name": "运行对账与卡死清理",
        "description": "对账定时任务执行实例与报表生成：超过租约/超时的卡死任务标记为超时并释放，"
                       "避免异常任务卡住队列",
        "schedule_desc": "每 60 秒",
        "owner_service": "dataflow",
    },
    "kb_sync_reconcile": {
        "name": "知识库同步对账",
        "description": "周期比对本体模型版本与知识库同步水位线，对漂移/失败的模型重推脱敏语义文档",
        "schedule_desc": "每 10 分钟",
        "owner_service": "datacatalog",
    },
}


def ensure_seeded() -> None:
    """把注册表同步进 adh_system_jobs（只写名称/职责/周期，不覆盖运行态与暂停开关）。"""
    for job_key, meta in BUILTIN_JOBS.items():
        try:
            execute_write(
                "INSERT INTO adh_system_jobs (job_key, name, description, schedule_desc, owner_service) "
                "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE name=VALUES(name), "
                "description=VALUES(description), schedule_desc=VALUES(schedule_desc), "
                "owner_service=VALUES(owner_service)",
                (job_key, meta["name"], meta["description"], meta["schedule_desc"], meta["owner_service"]),
            )
        except Exception as exc:  # noqa: BLE001 — 播种是旁路，失败显式告警而非静默
            logger.warning("[SystemJobs] 播种内置任务 %s 失败: %s", job_key, exc)


def is_job_active(job_key: str) -> bool:
    """内置任务是否处于启用态（人工暂停开关）。

    状态不可读时按**未暂停**执行并在日志告警：对账/清理类任务幂等，宁可多跑一轮
    也不让暂停开关的读失败静默停摆安全任务；异常事实显式进日志可诊断。
    """
    if job_key not in BUILTIN_JOBS:
        raise ValueError(f"未注册的内置任务: {job_key}")
    try:
        rows = execute_query("SELECT is_active FROM adh_system_jobs WHERE job_key=%s", (job_key,))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SystemJobs] 读取 %s 暂停开关失败，按未暂停执行: %s", job_key, exc)
        return True
    if not rows:
        return True
    return int(rows[0].get("is_active") or 0) == 1


def record_run(job_key: str, status: str, summary: str = "") -> None:
    """落一次内置任务运行结果（旁路观测，失败只告警）。"""
    if job_key not in BUILTIN_JOBS:
        raise ValueError(f"未注册的内置任务: {job_key}")
    if status not in ("success", "failed"):
        raise ValueError(f"内置任务状态不合法: {status}")
    try:
        execute_write(
            "INSERT INTO adh_system_jobs (job_key, name, description, schedule_desc, owner_service, "
            "last_run_at, last_status, last_result, run_count) "
            "VALUES (%s,%s,%s,%s,%s,NOW(),%s,%s,1) ON DUPLICATE KEY UPDATE "
            "last_run_at=NOW(), last_status=VALUES(last_status), last_result=VALUES(last_result), "
            "run_count=run_count+1",
            (job_key, BUILTIN_JOBS[job_key]["name"], BUILTIN_JOBS[job_key]["description"],
             BUILTIN_JOBS[job_key]["schedule_desc"], BUILTIN_JOBS[job_key]["owner_service"],
             status, str(summary or "")[:500]),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SystemJobs] 记录 %s 运行结果失败: %s", job_key, exc)


def set_paused(job_key: str, paused: bool, actor: str = "") -> dict:
    """暂停/恢复内置任务。返回更新后的任务视图；未注册键显式报错（不静默忽略）。"""
    if job_key not in BUILTIN_JOBS:
        raise ValueError(f"未注册的内置任务: {job_key}")
    ensure_seeded()
    execute_write(
        "UPDATE adh_system_jobs SET is_active=%s, paused_at=IF(%s=1,NOW(),NULL), paused_by=%s, "
        "last_status=IF(%s=1,'paused',last_status) WHERE job_key=%s",
        (0 if paused else 1, 0 if paused else 1, str(actor or "")[:64], 0 if paused else 1, job_key),
    )
    rows = list_jobs()
    for row in rows:
        if row["job_key"] == job_key:
            return row
    raise ValueError(f"内置任务状态写入后不可读: {job_key}")


def list_jobs() -> list:
    """注册表 + 运行态合并视图（任务监控页消费）。"""
    ensure_seeded()
    try:
        rows = execute_query(
            "SELECT job_key, is_active, paused_at, paused_by, last_run_at, last_status, last_result, run_count "
            "FROM adh_system_jobs")
    except Exception as exc:  # noqa: BLE001 — 表未迁移等：显式暴露给调用方而不是回退假数据
        raise RuntimeError(f"内置任务状态不可读: {exc}") from exc
    state = {r["job_key"]: r for r in rows or []}
    merged = []
    for job_key, meta in BUILTIN_JOBS.items():
        row = {**meta, "job_key": job_key, "source": "system", "removable": False,
               "is_active": 1, "paused_at": None, "paused_by": "", "last_run_at": None,
               "last_status": "", "last_result": "", "run_count": 0}
        row.update(state.get(job_key) or {})
        merged.append(row)
    return merged
