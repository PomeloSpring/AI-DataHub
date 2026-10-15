"""memory 工具组 — 用户记忆（项目专属：偏好/口径/习惯，不存会话上下文）。

记忆模型：固定槽位（单值覆盖）+ 浮动条目（上限 5 条，超限 fail-loud）。
严禁存跨 session 会话上下文（"正在做 X"、"上次问了 Y"）——避免换对话框串味。

交互契约（写进工具描述，要求 LLM 遵守）：
- 只记与数据分析/取数/展示相关的稳定事实
- 严禁记录会话状态/临时上下文
- 固定槽位同 slot_key 覆盖，浮动条目上限 5 条，超限先删再存
- 单条 content 不超过 300 字
"""

import logging
import re
from typing import Annotated, Optional

from backend.modules.mind.execution.sdk_tools.catalog_tools import _text

logger = logging.getLogger(__name__)

# 固定槽位定义（slot_key → 显示名），总数固定 = prompt 注入长度恒定可计算
FIXED_SLOTS = {
    "business_domain": "业务域",
    "preferred_chart": "默认图表",
    "default_time_grain": "时间粒度",
    "output_format": "输出格式",
    "number_precision": "数字精度",
    "role_context": "角色背景",
}

# 浮动条目类别（slot_key → 显示名），共享 5 条上限
FLOATING_CATEGORIES = {
    "correction": "口径纠错",
    "workflow": "工作习惯",
}

# 浮动条目硬上限
FLOATING_LIMIT = 5

# 单条 content 字符上限
CONTENT_MAX = 300

# 会话状态词（禁止存入记忆）
_SESSION_STATE_PATTERN = re.compile(
    r"(正在|上次|刚才|刚刚|前面|之前那个|当前会话|这个对话|上一轮|前面那)", re.IGNORECASE
)


def _ctx_user_id() -> int:
    """当前会话用户 ID; 无上下文返回 0。"""
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    ctx = get_execution_context()
    return int(getattr(ctx, "user_id", 0) or 0)


def _validate_content(content: str) -> Optional[str]:
    """校验记忆内容：非空、长度、禁止会话状态词。返回错误信息或 None。"""
    content = (content or "").strip()
    if not content:
        return "记忆内容不能为空"
    if len(content) > CONTENT_MAX:
        return f"记忆内容超过 {CONTENT_MAX} 字符上限，请精简"
    if _SESSION_STATE_PATTERN.search(content):
        return "禁止记录会话状态/临时上下文（如\"正在做 X\"、\"上次问了 Y\"），只能记稳定事实（偏好/口径/习惯）"
    return None


async def save_memory(args):
    """保存用户记忆（固定槽位覆盖 或 浮动条目新增）。

    固定槽位(slot_key in FIXED_SLOTS)：同 slot_key 直接覆盖旧值。
    浮动条目(slot_key in FLOATING_CATEGORIES)：新增一行，总数上限 5 条。
    """
    uid = _ctx_user_id()
    if not uid:
        return _text({"error": "缺少用户上下文"}, is_error=True)
    slot_key = (args.get("slot_key") or "").strip()
    content = (args.get("content") or "").strip()
    source = (args.get("source") or "auto").strip() or "auto"

    if not slot_key:
        return _text({"error": "缺少 slot_key"}, is_error=True)
    if slot_key not in FIXED_SLOTS and slot_key not in FLOATING_CATEGORIES:
        return _text({
            "error": f"未知 slot_key: {slot_key}",
            "fixed_slots": list(FIXED_SLOTS.keys()),
            "floating_categories": list(FLOATING_CATEGORIES.keys()),
        }, is_error=True)

    err = _validate_content(content)
    if err:
        return _text({"error": err}, is_error=True)

    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            if slot_key in FLOATING_CATEGORIES:
                # 浮动条目限量检查（fail-loud，不静默淘汰）
                cur.execute(
                    "SELECT COUNT(*) AS c FROM adh_user_memory WHERE user_id=%s AND slot_key IN ({})".format(
                        ",".join(["%s"] * len(FLOATING_CATEGORIES))
                    ),
                    (uid, *FLOATING_CATEGORIES.keys()))
                row = cur.fetchone()
                count = int((row or {}).get("c") or 0)
                # 新增时若已达上限则拒绝（已有同 id 更新不算新增）
                existing_id = args.get("id")
                if count >= FLOATING_LIMIT and not existing_id:
                    return _text({
                        "error": f"浮动条目已达上限 {FLOATING_LIMIT} 条，请先用 delete_memory 清理后再保存",
                        "current_count": count,
                    }, is_error=True)
                if existing_id:
                    cur.execute(
                        "UPDATE adh_user_memory SET content=%s, source=%s, updated_at=NOW() "
                        "WHERE id=%s AND user_id=%s",
                        (content, source, int(existing_id), uid))
                else:
                    cur.execute(
                        "INSERT INTO adh_user_memory (user_id, slot_key, content, source) VALUES (%s,%s,%s,%s)",
                        (uid, slot_key, content, source))
            else:
                # 固定槽位：upsert 覆盖
                cur.execute(
                    "INSERT INTO adh_user_memory (user_id, slot_key, content, source) VALUES (%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE content=VALUES(content), source=VALUES(source), updated_at=NOW()",
                    (uid, slot_key, content, source))
        conn.commit()
    finally:
        conn.close()
    return _text({"saved": True, "slot_key": slot_key, "content": content})


async def list_memory(args):
    """列出当前用户的全部记忆（只读，按用户隔离）。"""
    uid = _ctx_user_id()
    if not uid:
        return _text({"error": "缺少用户上下文"}, is_error=True)
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, slot_key, content, source, updated_at FROM adh_user_memory "
                "WHERE user_id=%s ORDER BY slot_key", (uid,))
            rows = cur.fetchall()
    finally:
        conn.close()
    fixed = [r for r in rows if r["slot_key"] in FIXED_SLOTS]
    floating = [r for r in rows if r["slot_key"] in FLOATING_CATEGORIES]
    return _text({
        "count": len(rows),
        "fixed_slots": [{"slot_key": r["slot_key"], "label": FIXED_SLOTS.get(r["slot_key"], r["slot_key"]),
                          "content": r["content"], "id": r["id"]} for r in fixed],
        "floating_entries": [{"slot_key": r["slot_key"], "label": FLOATING_CATEGORIES.get(r["slot_key"], r["slot_key"]),
                               "content": r["content"], "id": r["id"]} for r in floating],
        "floating_limit": FLOATING_LIMIT,
    })


async def delete_memory(args):
    """删除指定记忆（只能删自己的，fail-closed）。"""
    uid = _ctx_user_id()
    if not uid:
        return _text({"error": "缺少用户上下文"}, is_error=True)
    memory_id = args.get("id")
    if not memory_id:
        return _text({"error": "缺少 id（来自 list_memory）"}, is_error=True)
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_user_memory WHERE id=%s AND user_id=%s", (int(memory_id), uid))
            deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    if not deleted:
        return _text({"error": "记忆不存在或无权删除"}, is_error=True)
    return _text({"deleted": True, "id": int(memory_id)})


TOOL_SPECS = [
    {
        "name": "save_memory",
        "description": (
            "保存用户记忆（稳定偏好/口径/习惯）。固定槽位(business_domain/preferred_chart/"
            "default_time_grain/output_format/number_precision/role_context)同名覆盖；"
            "浮动条目(correction=口径纠错/workflow=工作习惯)上限 5 条，超限先用 delete_memory 清理。"
            "严禁记录会话状态/临时上下文（\"正在做 X\"、\"上次问了 Y\"），只记跨会话稳定事实。"
            "单条不超过 300 字。"
        ),
        "schema": {
            "slot_key": Annotated[str, "固定槽位名或浮动条目类别（见工具描述）"],
            "content": Annotated[str, "记忆内容（稳定事实，不超过 300 字）"],
            "source": Annotated[Optional[str], "来源标记：auto=LLM学习(默认) / manual=用户显式"],
        },
        "handler": save_memory,
    },
    {
        "name": "list_memory",
        "description": "列出当前用户的全部记忆（固定槽位 + 浮动条目，只读）。",
        "schema": {},
        "handler": list_memory,
    },
    {
        "name": "delete_memory",
        "description": "删除指定记忆（只能删自己的）。id 来自 list_memory 返回。",
        "schema": {
            "id": Annotated[int, "记忆 ID（来自 list_memory）"],
        },
        "handler": delete_memory,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}
_MUTATING = {"save_memory", "delete_memory"}


def build_memory_server(backend: str = "qoder", tool_names=None):
    """构建 memory 进程内 MCP server（qoder / claude）。tool_names 给定时按 AS-BOT 逐工具粒度注册。"""
    from backend.modules.mind.execution.sdk_tools.compat import make_server, make_tool

    specs = list(TOOL_SPECS)
    if tool_names is not None:
        selected = set(tool_names)
        specs = [s for s in specs if s["name"] in selected]
    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"], s["handler"],
                  annotations=None if s["name"] in _MUTATING else READONLY_ANNOTATIONS)
        for s in specs
    ]
    return make_server(backend, "datahub_memory", tools)
