"""memory 工具组 — 用户记忆（项目专属：偏好/口径/习惯，不存会话上下文）。

存储与校验的**单一事实源**是 ``backend/core/memory_service.py``（REST /api/memory
与本工具组共用它，避免两侧规则漂移）。本模块只负责把 SDK 工具入参翻译成服务调用，
并把 ValueError 转成带原因的可诊断错误回给 LLM（fail-loud，不静默丢弃）。

记忆模型：固定槽位（单值覆盖）+ 浮动条目（两类共享 5 条上限，超限拒绝）。
严禁存跨 session 会话上下文（"正在做 X"、"上次问了 Y"）—— 记忆按 user_id 归属、
跨会话注入提示词，会话措辞一旦入库就会污染后续无关会话。

交互契约（写进工具描述，要求 LLM 遵守）：
- 只记与数据分析/取数/展示相关的**稳定事实**
- 严禁记录会话状态/临时上下文/一次提问的具体内容/查询结果数值
- 固定槽位同 slot_key 覆盖，浮动条目上限 5 条，超限先删再存
- 单条 content 不超过 300 字
- 写入成功后必须向用户复述"已记住什么"（用户可见，不做静默学习）
"""

import logging
from typing import Annotated, Optional

from backend.core import memory_service
from backend.modules.mind.execution.sdk_tools.catalog_tools import _text

logger = logging.getLogger(__name__)

# 常量再导出（唯一事实源在 memory_service，这里只是给工具描述/旧引用取用）
FIXED_SLOTS = memory_service.FIXED_SLOTS
FLOATING_CATEGORIES = memory_service.FLOATING_CATEGORIES
FLOATING_LIMIT = memory_service.FLOATING_LIMIT
CONTENT_MAX = memory_service.CONTENT_MAX


def _ctx_user_id() -> int:
    """当前会话用户 ID; 无上下文返回 0。"""
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    ctx = get_execution_context()
    return int(getattr(ctx, "user_id", 0) or 0)


async def save_memory(args):
    """保存用户记忆（固定槽位覆盖 或 浮动条目新增/更新）。"""
    uid = _ctx_user_id()
    if not uid:
        return _text({"error": "缺少用户上下文"}, is_error=True)
    try:
        result = memory_service.save_entry(
            uid,
            args.get("slot_key") or "",
            args.get("content") or "",
            source=(args.get("source") or "auto").strip() or "auto",
            entry_id=args.get("id"),
        )
    except ValueError as exc:
        # 校验/上限/未知槽位 → 把原因原样回抛，让 LLM 纠正后重试或告知用户
        return _text({"error": str(exc)}, is_error=True)
    return _text(result)


async def list_memory(args):
    """列出当前用户的全部记忆（只读，按用户隔离）。"""
    uid = _ctx_user_id()
    if not uid:
        return _text({"error": "缺少用户上下文"}, is_error=True)
    rows = memory_service.list_entries(uid)
    fixed, floating = memory_service.split_entries(rows)
    return _text({
        "count": len(rows),
        "fixed_slots": [{"slot_key": r["slot_key"], "label": FIXED_SLOTS.get(r["slot_key"], r["slot_key"]),
                          "content": r["content"], "id": r["id"]} for r in fixed],
        "floating_entries": [{"slot_key": r["slot_key"], "label": FLOATING_CATEGORIES.get(r["slot_key"], r["slot_key"]),
                               "content": r["content"], "id": r["id"], "entry_no": r.get("entry_no")}
                              for r in floating],
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
    deleted = memory_service.delete_entry(uid, int(memory_id))
    if not deleted:
        return _text({"error": "记忆不存在或无权删除"}, is_error=True)
    return _text({"deleted": True, "id": int(memory_id)})


TOOL_SPECS = [
    {
        "name": "save_memory",
        "description": (
            "保存用户记忆（跨会话稳定偏好/口径/习惯）。固定槽位("
            + "/".join(FIXED_SLOTS.keys()) + ")同名覆盖；"
            f"浮动条目(correction=口径纠错/workflow=工作习惯)两类合计上限 {FLOATING_LIMIT} 条，"
            "超限先用 delete_memory 清理。传 id 则更新该条浮动条目(id 来自 list_memory)。"
            "严禁记录会话状态/临时上下文(\"正在做 X\"/\"上次问了 Y\"/本次提问内容)与查询结果数值——"
            "记忆会注入后续所有会话的提示词，写入这类内容会造成串味与错误推断。"
            f"单条不超过 {CONTENT_MAX} 字；保存成功后向用户复述已记住的内容。"
        ),
        "schema": {
            "slot_key": Annotated[str, "固定槽位名或浮动条目类别（见工具描述）"],
            "content": Annotated[str, "记忆内容（跨会话稳定事实，不超过 300 字）"],
            "source": Annotated[Optional[str], "来源标记：auto=LLM学习(默认) / manual=用户显式要求记录"],
            "id": Annotated[Optional[int], "更新已有浮动条目时传其 id（来自 list_memory）；新增不传"],
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
