"""Execution Layer Service — adh_execution_layers / adh_workspace_execution_layers CRUD."""

import json
import logging
import os
from datetime import datetime

from backend.common.db import execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize(row: dict) -> dict:
    """解析 config / allowed_tools / capabilities / tools JSON、datetime 转 ISO 字符串."""
    for json_field, default in (
        ("config", {}),
        ("allowed_tools", None),
        ("capabilities", []),
        ("tools", []),
    ):
        val = row.get(json_field)
        if isinstance(val, str):
            try:
                row[json_field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                if json_field in ("allowed_tools", "config"):
                    raise ValueError("Harness 权限配置格式错误")
                row[json_field] = default
        elif val is None:
            row[json_field] = default
    for k in ("created_at", "updated_at", "health_check_at", "last_heartbeat_at", "registered_at"):
        if hasattr(row.get(k), "isoformat"):
            row[k] = row[k].isoformat()
    return row


# ── 执行层 CRUD ────────────────────────────────────────────────────

def list_layers() -> list[dict]:
    rows = execute_query("SELECT * FROM adh_execution_layers ORDER BY id")
    return [_normalize(r) for r in rows]


def get_layer(layer_id: int, raw_config: bool = False) -> dict | None:
    row = execute_query(
        "SELECT * FROM adh_execution_layers WHERE id = %s", (layer_id,), fetchone=True
    )
    if not row:
        return None
    normalized = _normalize(row)
    if raw_config:
        # 合并写入时需要拿到**未归一**的原始 config（_normalize 会把 config 变成新 dict，
        # 并把层字段与绑定字段平铺出来，不适合当合并基准）。
        cfg = row.get("config")
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except ValueError:
                cfg = {}
        normalized["config"] = cfg if isinstance(cfg, dict) else {}
    return normalized


def get_layer_by_name(name: str) -> dict | None:
    row = execute_query(
        "SELECT * FROM adh_execution_layers WHERE name = %s", (name,), fetchone=True
    )
    return _normalize(row) if row else None


def create_layer(data: dict) -> int:
    now = _now()
    return execute_insert(
        """INSERT INTO adh_execution_layers
           (name, display_name, description, layer_type, config, status, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            data["name"],
            data.get("display_name", ""),
            data.get("description", ""),
            data["layer_type"],
            json.dumps(data.get("config") or {}, ensure_ascii=False),
            data.get("status", "active"),
            now, now,
        ),
    )


def update_layer(layer_id: int, data: dict) -> bool:
    updates = ["updated_at = %s"]
    params = [_now()]
    for field in ("name", "display_name", "description", "layer_type", "status"):
        if field in data and data[field] is not None:
            updates.append(f"{field} = %s")
            params.append(data[field])
    if "config" in data and data["config"] is not None:
        # 浅合并而不是整体覆盖：管理页表单只携带它认识的几个键，旧行为会把不在表单里的
        # 键（最关键的是 `mode="sdk"`）直接抹掉，结果是“什么都没改，只是点了一下保存”，
        # 下次问答/定时任务就报“该 CLI 执行层不支持 AS-BOT 安全执行”。
        # 要真删某个键请显式传 null（JSON null 会写回，不是丢弃）。
        incoming = data["config"] if isinstance(data["config"], dict) else {}
        merged = dict(get_layer(layer_id, raw_config=True) or {})
        base = merged.get("config") if isinstance(merged.get("config"), dict) else {}
        merged_cfg = dict(base)
        for key, value in incoming.items():
            if value is None:
                merged_cfg.pop(key, None)
            else:
                merged_cfg[key] = value
        updates.append("config = %s")
        params.append(json.dumps(merged_cfg, ensure_ascii=False))
    params.append(layer_id)
    execute_write(
        f"UPDATE adh_execution_layers SET {', '.join(updates)} WHERE id = %s", params
    )
    return True


def record_health(layer_id: int, status: str, message: str):
    """记录健康检查结果."""
    execute_write(
        """UPDATE adh_execution_layers
           SET health_check_at = %s, last_test_status = %s, last_test_message = %s
           WHERE id = %s""",
        (_now(), status, message[:2000], layer_id),
    )


def delete_layer(layer_id: int) -> bool:
    execute_write(
        "DELETE FROM adh_workspace_execution_layers WHERE execution_layer_id = %s",
        (layer_id,),
    )
    execute_write("DELETE FROM adh_execution_layers WHERE id = %s", (layer_id,))
    return True


# ── 注册与发现 (Phase 3) ─────────────────────────────────────────

#: 自注册执行层心跳超时窗口(秒)。超过此窗口未上报心跳视为不健康。
HEARTBEAT_TIMEOUT = int(os.getenv("EXEC_LAYER_HEARTBEAT_TIMEOUT", "90"))


def _parse_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _row_healthy(row: dict) -> bool:
    """判断执行层是否健康可用。

    - 非 active 一律不健康
    - builtin / 手工配置的层:active 即健康(进程内可达)
    - 自注册层(source=self):需在心跳窗口内上报过心跳
    """
    if row.get("status") != "active":
        return False
    if row.get("source") != "self":
        return True
    hb = _parse_dt(row.get("last_heartbeat_at"))
    if hb is None:
        return False
    return (datetime.now() - hb).total_seconds() <= HEARTBEAT_TIMEOUT


def register_layer(data: dict) -> int:
    """自注册执行层(按 name upsert),刷新心跳与能力/工具目录."""
    now = _now()
    name = data["name"]
    capabilities = json.dumps(data.get("capabilities") or [], ensure_ascii=False)
    tools = json.dumps(data.get("tools") or [], ensure_ascii=False)
    endpoint_url = data.get("endpoint_url", "")
    config = json.dumps(data.get("config") or {}, ensure_ascii=False)
    display_name = data.get("display_name", "")
    description = data.get("description", "")
    layer_type = data.get("layer_type", "remote")

    existing = get_layer_by_name(name)
    if existing:
        execute_write(
            """UPDATE adh_execution_layers
               SET display_name=%s, description=%s, layer_type=%s, config=%s,
                   capabilities=%s, tools=%s, endpoint_url=%s,
                   source='self', status='active', last_heartbeat_at=%s, updated_at=%s
               WHERE id=%s""",
            (display_name or existing.get("display_name") or "",
             description or existing.get("description") or "",
             layer_type, config, capabilities, tools, endpoint_url, now, now,
             existing["id"]),
        )
        return existing["id"]

    return execute_insert(
        """INSERT INTO adh_execution_layers
           (name, display_name, description, layer_type, config, status,
            capabilities, tools, endpoint_url, source, last_heartbeat_at, registered_at,
            created_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,'active',%s,%s,%s,'self',%s,%s,%s,%s)""",
        (name, display_name, description, layer_type, config,
         capabilities, tools, endpoint_url, now, now, now, now),
    )


def heartbeat(name: str = "", layer_id: int = 0, tools: list | None = None) -> bool:
    """刷新心跳;可选同时更新工具目录."""
    row = get_layer(layer_id) if layer_id else (get_layer_by_name(name) if name else None)
    if not row:
        return False
    now = _now()
    if tools is not None:
        execute_write(
            "UPDATE adh_execution_layers SET status='active', last_heartbeat_at=%s, tools=%s, updated_at=%s WHERE id=%s",
            (now, json.dumps(tools, ensure_ascii=False), now, row["id"]),
        )
    else:
        execute_write(
            "UPDATE adh_execution_layers SET status='active', last_heartbeat_at=%s, updated_at=%s WHERE id=%s",
            (now, now, row["id"]),
        )
    return True


def deregister(name: str = "", layer_id: int = 0) -> bool:
    """注销自注册执行层(置为 inactive)。仅对 source=self 生效,避免误下线手工层."""
    row = get_layer(layer_id) if layer_id else (get_layer_by_name(name) if name else None)
    if not row or row.get("source") != "self":
        return False
    execute_write(
        "UPDATE adh_execution_layers SET status='inactive', updated_at=%s WHERE id=%s",
        (_now(), row["id"]),
    )
    return True


def record_tools(layer_id: int, tools: list) -> bool:
    execute_write(
        "UPDATE adh_execution_layers SET tools=%s, updated_at=%s WHERE id=%s",
        (json.dumps(tools or [], ensure_ascii=False), _now(), layer_id),
    )
    return True


def is_healthy_layer(row: dict) -> bool:
    """公开健康判定(供调度层过滤 stale 的自注册执行层)."""
    normalized = _normalize(dict(row))
    return _row_healthy(normalized)


def get_default_external_layer() -> dict | None:
    """系统默认外部执行层。

    用于 chat Agent 模式在工作空间未显式绑定外部层时的兆底解析:
    取第一个 healthy 且 active 的执行层(按 id 升序,通常为 qoder)。
    """
    for r in list_layers():
        if not _row_healthy(r):
            continue
        return r
    return None


def pick_active_layer(workspace_id: int) -> dict | None:
    """默认执行层的**唯一实现**（问答派发 / 上下文压缩 / 定时分析 / 模型候选共用）。

    口径（执行层全局生效，2026-09 已拍板“不按工作空间绑定”）：
    取 active + healthy 的层 → `is_default` 优先 → 否则取清单首条（`list_layers` 已按 id 升序，
    所以旧行为“id 最小的 active 层”完整保留）。

    为什么不读 `adh_workspace_execution_layers`：那张表的写入 API 已退役（代码里
    注明“执行层全局生效”），表内行是冻结遗留数据。以前本函数“先看绑定、绑不到再回退
    全局”与 `manager.resolve_workspace_layer` 的“直接全局”并存，是同一件事的双口径：
    冻结行会悄悄把某个空间钉在旧层上，而管理页上根本改不到。现在两边归一，
    默认层真值源 = `adh_execution_layers.is_default`（可管理页配）。

    `workspace_id` 参数保留兼容签名（多处调用传它），但不参与解析。
    """
    candidates: list[dict] = []
    for row in list_layers():
        if row.get("status") != "active":
            continue
        if not is_healthy_layer(row):
            logger.warning("Skipping unhealthy execution layer: %s", row.get("name"))
            continue
        candidates.append(row)
    if not candidates:
        return None
    return next((r for r in candidates if int(r.get("is_default") or 0) == 1), candidates[0])


def set_default_layer(layer_id: int) -> dict:
    """把指定执行层设为系统默认层（全表至多一行为 1）。

    只有 active 层能当默认层：停用层当默认会让所有未绑层的 AS-BOT 直接不可用。
    返回实际生效的层（调用方据此回显，不拿“请求里的 id”当结果讲）。"""
    row = get_layer(layer_id)
    if not row:
        raise ValueError(f"Harness 不存在: id={layer_id}")
    if row.get("status") != "active":
        raise ValueError(
            f"Harness「{row.get('display_name') or row.get('name')}」未启用，不能设为默认 Harness。"
            "请先启用它。")
    execute_write("UPDATE adh_execution_layers SET is_default = 0")
    execute_write("UPDATE adh_execution_layers SET is_default = 1, updated_at = %s WHERE id = %s",
                  (_now(), layer_id))
    _invalidate_layer_caches()
    return get_layer(layer_id) or row


def _invalidate_layer_caches() -> None:
    """执行层配置变更后的缓存失效（选层结果与模型候选都依赖它）。"""
    try:
        from backend.common.ttl_cache import exec_layer_cache
        exec_layer_cache.invalidate(None)   # 全局生效：影响所有工作空间
    except Exception as exc:  # noqa: BLE001 — 缓存失效失败不该把配置写成“没生效”
        logger.warning("执行层缓存失效失败（配置已落库，最多 5min 后自动收敛）: %s", exc)


class LayerSelectionError(ValueError):
    """AS-BOT 绑定的执行层不可用（对外可读、可操作；不静默换层）。"""


def pick_layer_for_as_bot(as_bot: dict | None, workspace_id: int) -> dict | None:
    """选层口径的唯一入口（问答 / 上下文压缩 / 定时分析 / 模型候选）。

    规则：
    1. AS-BOT 未绑执行层（NULL/0）→ 走默认执行层（`pick_active_layer`），行为与从前一致；
    2. AS-BOT 绑了层 → 该层必须 **存在 + active + healthy**，否则抛 `LayerSelectionError`
       说清原因。**绝不静默退回默认层**：两条层的模型通道（Qoder Credits vs 供应商直连）
       与能力上限都不同，默默换层等于给用户错的答案。

    执行层全局生效（不按工作空间绑定，绑定表已退役），所以这里不再检查绑定关系；
    也不允许“层已停用但绑着照旧跑”——那会把已下架的通道继续用在生产会话上。
    """
    layer_id = int((as_bot or {}).get("execution_layer_id") or 0)
    if not layer_id:
        return pick_active_layer(workspace_id)

    row = get_layer(layer_id)
    label = (row or {}).get("display_name") or (row or {}).get("name") or f"id={layer_id}"
    if not row:
        raise LayerSelectionError(
            f"AS-BOT 绑定的 Harness 已不存在（{label}）。请在「AS-BOT」重新选择 Harness，"
            "或改为跟随默认 Harness。")
    if row.get("status") != "active":
        raise LayerSelectionError(
            f"AS-BOT 绑定的 Harness「{label}」已停用。请在「Harness」页启用它，"
            "或为 AS-BOT 换一条可用 Harness。")
    if not is_healthy_layer(row):
        raise LayerSelectionError(
            f"AS-BOT 绑定的 Harness「{label}」健康检查未通过"
            f"（{row.get('last_test_message') or '未在心跳窗口内上报'}）。")
    return row


def discover_layers(capability: str = "") -> list[dict]:
    """返回健康可用的执行层列表,可按能力标签过滤."""
    rows = list_layers()
    result = []
    for r in rows:
        if not _row_healthy(r):
            continue
        if capability:
            caps = r.get("capabilities") or []
            if capability not in caps:
                continue
        r["healthy"] = True
        result.append(r)
    return result


# ── 工作空间绑定 ───────────────────────────────────────────────────

def get_workspace_layers(workspace_id: int) -> list[dict]:
    rows = execute_query(
        """SELECT l.*, b.is_default, b.priority, b.allowed_tools
           FROM adh_workspace_execution_layers b
           JOIN adh_execution_layers l ON l.id = b.execution_layer_id
           WHERE b.workspace_id = %s
           ORDER BY b.priority DESC, l.id""",
        (workspace_id,),
    )
    return [_normalize(r) for r in rows]


def set_workspace_layers(workspace_id: int, bindings: list[dict]):
    """全量替换工作空间的执行层绑定.

    Args:
        bindings: [{"execution_layer_id": int, "is_default": bool,
                    "priority": int, "allowed_tools": [str]}]
    """
    execute_write(
        "DELETE FROM adh_workspace_execution_layers WHERE workspace_id = %s",
        (workspace_id,),
    )
    default_set = False
    for b in bindings:
        is_default = bool(b.get("is_default")) and not default_set
        default_set = default_set or is_default
        from backend.modules.mind.execution.tool_catalog import parse_allowed_tools
        allowed = None if b.get("allowed_tools") is None else parse_allowed_tools(b["allowed_tools"])
        execute_write(
            """INSERT INTO adh_workspace_execution_layers
               (workspace_id, execution_layer_id, is_default, priority, allowed_tools)
               VALUES (%s, %s, %s, %s, %s)""",
            (workspace_id, b["execution_layer_id"], 1 if is_default else 0,
             int(b.get("priority", 0)),
             json.dumps(allowed, ensure_ascii=False) if allowed is not None else None),
        )
