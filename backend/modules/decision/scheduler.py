"""决策建议的定时出口（主动预警）：按树配置的 Cron 跑一次完整评估，命中才通知。

分工（分层契约 §0：不新增 L2 横向 import 边）：

    flow/tasks/decision_tasks.py  ← Celery 任务壳（调度域只持有"壳"和 beat 条目）
            │ task_runtime.call("decision.scheduled_run")
            ▼
    decision/scheduler.py（本文件）← 业务编排：身份派生、逐站求值、是否发通知
            │ task_runtime.call("task.send_notification")
            ▼
    flow/tasks/executor.py        ← 通知发送口（渠道/模板/脱敏口径全平台一份）

三条硬规矩：

1. **只有 ``verdict='hit'`` 才发通知**。无命中不发垃圾消息；partial/skipped 只在
   实例里留原因，不打扰人（它们不是"要处置"，是"判不了"，得由人去决策中心看）。
2. **通知正文不含业务内容**（指标名/阈值/实测值/建议正文）。变量面只有
   ``decision_hit_count`` 一个执行元信息 + ``report_link``；建议详情走链接指向的
   实例页，那一页由 ``decision:read`` 把关。这是
   ``test_message_template_notification.py`` 一直在守的那条线。
3. **身份用发布人，能力实时继承**：定时评估没有请求者，就按"谁发布的就是谁的
   口径"用 ``published_by``（回落 ``created_by``）；可查范围仍由该用户的角色授权
   实时派生（waker-datasource-domain §1），不新建第二套授权，也不拿 admin 兜底。
   发布人已停用/无任何授权源 → 显式记一条未通知原因，不发、不猜。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from backend.modules.decision import engine, service

logger = logging.getLogger(__name__)


def _base_url() -> str:
    """与定时报告链接同一口径：ADH_BASE_URL 最高优先，否则前端监听口。"""
    from backend.common import config as _cfg
    return (os.getenv("ADH_BASE_URL") or f"http://localhost:{_cfg.frontend_port()}").rstrip("/")


def _owner_identity(tree_row: dict) -> tuple[int, str]:
    """评估身份 = 发布人（回落创建人/最后修改人）。回 ``(user_id, username)``。"""
    for key in ("published_by", "created_by", "updated_by"):
        uid = int(tree_row.get(key) or 0)
        if uid > 0:
            # 用户读取走 L0 common.auth（带 60s 缓存的同一份口径，viz/report_access 同此）：
            # 不横向 import modules.auth（分层契约 §0 禁 L2 横向边）
            from backend.common.auth import get_user_by_id
            row = get_user_by_id(uid) or {}
            if str(row.get("status") or "active") == "active":
                return uid, str(row.get("username") or "")
            logger.warning("[decision] 定时评估身份已停用 tree=%s user=%s",
                           tree_row.get("tree_key"), uid)
            return 0, ""
    return 0, ""


def _sites_for(user_id: int, workspace_id: int, tree_row: dict) -> list[str]:
    """这棵树本次要跑哪些站：树声明的 apply_sites ∩ 发布人可查站点。

    交集为空不是错误，但必须说清是"树限定了别的站"还是"发布人已查不到这些站"，
    否则配置的人会以为预警坏了。
    """
    from backend.core import site_service

    authorized = [s for s in (site_service.authorized_sites(user_id, workspace_id) or [])
                  if isinstance(s, dict) and s.get("site_code")
                  and not int(s.get("is_aggregate") or 0)]
    codes = [str(s.get("site_code")) for s in authorized]
    declared = [str((r or {}).get("site_code") or "").strip()
                for r in (tree_row.get("apply_sites") or [])]
    declared = [c for c in declared if c]
    if not declared:
        return codes
    return [c for c in codes if c in declared]


def run_scheduled(tree_id: int, run_key: str = "") -> dict:
    """跑一棵已发布树并按需通知，回摘要（供任务日志与排障）。"""
    row = service.get_tree_by_id(int(tree_id or 0))
    if not row:
        return {"skipped": "决策树不存在或已被删除，本次定时评估未执行"}
    if str(row.get("status") or "") != "active" or not int(row.get("is_active") or 1):
        return {"skipped": f"决策树「{row.get('name')}」当前是 {row.get('status')}，不参与定时评估"}

    uid, username = _owner_identity(row)
    workspace_id = int(row.get("workspace_id") or 0)
    if not uid:
        return {"skipped": "发布人已停用或未找到，定时评估不代为挑选身份"}

    from backend.core import site_service
    from backend.core.role_service import role_service
    allowed_ids = sorted({int(i) for i in (role_service.get_user_allowed_datasources(
        uid, workspace_id) or []) if str(i).strip() != ""})
    if not allowed_ids:
        return {"skipped": f"发布人「{username}」当前没有任何已授权数据源，定时评估无法取数"}

    sites = _sites_for(uid, workspace_id, row)
    if not sites:
        return {"skipped": "该树的适用站点与发布人可查站点没有交集，本次未评估"}

    items: list[dict[str, Any]] = []
    for code in sites:
        ds_id = site_service.resolve_site_to_source(code, allowed_ids)
        ctx = engine.EvalContext(
            mode="full", user_id=uid, username=username, role="",
            workspace_id=workspace_id, datasource_id=int(ds_id or 0),
            site_code=code, trigger_type="scheduled", run_key=run_key)
        outcome = engine.evaluate_tree(row, ctx)
        run = service.record_run(row, outcome, ctx)
        items.append(service.project_run(run, outcome))

    hits = [i for i in items if i.get("verdict") == "hit"]
    summary = {"evaluated": len(items), "hit_count": len(hits),
               "sites": sites, "tree_key": row.get("tree_key"),
               "notified": False}
    if not hits:
        # 无命中/未命中都不发通知：partial/skipped 的原因在实例里，不靠 IM 催
        summary["notify_skipped_reason"] = (
            "没有命中，不发送通知" if items else
            "本轮站点均跳过，原因见各实例 verdict_reason")
        return summary

    notify_channel_id = int(row.get("notify_channel_id") or 0)
    if not notify_channel_id:
        summary["notify_skipped_reason"] = "该树未配置通知渠道，命中结果只在决策中心可见"
        return summary

    from backend.core import task_runtime

    # 渠道归属校核：发送口会拦"任务空间≠渠道空间"。全局口径树（workspace_id=0）
    # 本身没有属主空间，这里按渠道自己的空间发（不是绕过校核，而是本来就不存
    # 在第二个空间）；空间树仍按自己的空间走，拿不到渠道就显式说清不发假通知。
    channel_service = task_runtime.call("task.scheduled_service")
    channel = channel_service.get_channel(notify_channel_id) or {}
    if not channel or not channel.get("is_active"):
        summary["notify_skipped_reason"] = (
            f"通知渠道 #{notify_channel_id} 不存在或已停用，命中结果未送达")
        return summary
    link_ws = workspace_id or int(channel.get("workspace_id") or 0)

    link = f"{_base_url()}/ws/{link_ws}/decisions"
    task = {"channel_id": notify_channel_id,
            "workspace_id": int(channel.get("workspace_id") or 0) or link_ws,
            "name": row.get("name") or row.get("tree_key") or ""}
    # results 只带 verdict 字段：build_context 由此算出 decision_hit_count，
    # 指标名/阈值/建议正文一律不进通知（护栏与模板变量面同口径）。
    results = [{"verdict": i.get("verdict")} for i in items]
    status = task_runtime.call("task.send_notification", task, results, report_link=link)
    summary["notified"] = status == "sent"
    summary["notify_status"] = status
    if status != "sent":
        summary["notify_skipped_reason"] = f"通知发送状态={status}（渠道不可用/模板被删/发送失败）"
        logger.warning("[decision] 定时预警未送达 tree=%s channel=%s status=%s",
                       row.get("tree_key"), notify_channel_id, status)
    return summary


def scheduled_trees() -> list[dict]:
    """第五个调度源要覆盖的树：已发布 + 启用 + 配了 Cron。

    只读 `adh_decision_trees` 的调度三字段，不碰 `adh_scheduled_tasks.task_type`
    （那仍只有 agent 一种形态，是既有产品决策，两套调度不互相冒充）。
    """
    return service.scheduled_tree_rows()


def dispatched(run_key: str) -> bool:
    """Beat 重投/多实例下的墙钟分钟幂等判定（由调度域经回调表调用）。"""
    return service.dispatched_by_run_key(run_key)
