"""会话上下文用量（进度条唯一数据源）与手动压缩。

为什么单独一个模块：
- 上下文占用的**权威口径只有一处** —— SDK `get_context_usage()`（等价 CLI `/context` 视图，
  返回的是百分比而非 token 数）。平台不自造 token 估算、不写第二套阈值，否则前端显示的
  数字和 CLI 自己判断要不要压缩的依据会分叉（"条满了却没压缩"或"没满就压缩"都无从解释）。
- 执行层客户端是**每轮新建**（`resume=sdk_session_id`），轮间没有活客户端可问，所以占用值必须
  在"本来就有连接"的回合内采集后落库，前端读最近一次采集值。从未测量时返回
  `available=false` + 原因，**不返回 0%**（0% 会被读成"还有很大余量"，是假信号）。

自动压缩不在本模块触发：阈值与触发时机归 CLI autoCompact（用户口径：交给 LLM/SDK 控制）。
本模块只做两件事 —— 把占用/阈值显示出来，以及把"压缩正在发生"通过 PreCompact/PostCompact
hook 变成用户可见事件（禁止静默，见 no-silent-degradation）。

手动压缩走 `/compact` 斜杠命令（打包 CLI 内确认存在该命令），并**复用问答回合同一套执行权
仲裁**（`adh_agent_sessions.status='running'` + execution_token + owner/sdk 进程身份核验）：
- 任务进行中天然不可压缩（同一把锁，不需要新造状态让下游各处认不得）；
- 压缩中途崩溃时，既有的 `_dead_execution_evidence`/`reconcile_stale_sessions` 能据进程证据
  回收执行权，不会留下谁也解不开的锁。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from backend.common.db import execute_query, execute_write
from backend.modules.mind.execution.session_workspace import run_owned_sync

logger = logging.getLogger(__name__)

# CLI /context 的分类键 → 面向用户的名称（前后端同源，不各写一份）
CATEGORY_LABELS: dict[str, str] = {
    "system_prompt": "系统提示词",
    "system_tools": "系统工具",
    "skills": "技能",
    "messages": "对话消息",
    "other": "其他",
    "free_space": "剩余空间",
    "auto_compact": "自动压缩预留",
}

# 手动压缩的墙钟上限：压缩本身是一次模型调用，但用户点它是要立刻看到效果的。
# 不沿用问答回合的 300s —— 跑满只会让用户以为按钮坏了。
COMPACT_TIMEOUT_S = float(os.getenv("CONTEXT_COMPACT_TIMEOUT_SECONDS", "120"))

# 执行权被占用时的可操作解释（不把内部状态名丢给用户）
_BUSY_REASON = {
    "running": "本轮任务进行中，等它结束或先停止后再压缩",
    "deleting": "该会话正在删除或等待清理重试，暂时不能压缩",
    "closed": "该会话已清空，请新建会话",
    "interrupted": "上一轮执行已中断，先发一条消息恢复会话再压缩",
}


class ContextCompactNotFound(RuntimeError):
    """会话/执行映射不存在或无权访问（REST 层转 404）。"""


class ContextCompactBusy(RuntimeError):
    """执行权被占用或没有可压缩的上下文（REST 层转 409）。"""


class ContextCompactFailed(RuntimeError):
    """压缩确实没做成（REST 层转 502）。不得伪装成功。"""


def _pct(value: Any) -> Optional[float]:
    """百分比归一：非数/负数/超界一律视为不可用（不猜、不夹到 0 或 100 冒充真值）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0 or value > 100:
        return None
    return round(float(value), 2)


def _num(value: Any) -> Optional[float]:
    """Token 计数归一：非数/负数当缺失（不用 0 冒充“测到 0 个”）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return float(value)


# Claude Code CLI 的 get_context_usage 与 Qoder 网关是**两套结构**（实测 CLI 2.1.277）：
#   占用     percentage(0-100 整数) + totalTokens/maxTokens  vs  contextWindow.usedPercentage
#   分类     categories[].name(英文)/.tokens/.kind        vs  categories[].type/.percentage
#   自动压缩  autoCompactThreshold(**Token 数**)/isAutoCompactEnabled
#                                             vs  autoCompact.thresholdPercentage/.enabled
# 不归一就会拿 Qoder 的键去读 claude 返回体 → 取不到占用 → 进度条永远 --%。
_CLAUDE_CATEGORY_TYPES = {
    "system prompt": "system_prompt",
    "system tools": "system_tools",
    "tools": "system_tools",
    "skills": "skills",
    "messages": "messages",
    "free space": "free_space",
    "autocompact buffer": "auto_compact",
    # MCP 工具/记忆文件/子代理在平台分类里没有对应档，归“其他”但不丢行
    "mcp tools": "other",
    "memory files": "other",
    "agents": "other",
}


def _snapshot_from_claude(usage: dict) -> dict:
    """Claude CLI 返回体 → 平台快照（只取同源字段，不做跨源估算）。"""
    total = _num(usage.get("totalTokens"))
    window = _num(usage.get("maxTokens")) or _num(usage.get("rawMaxTokens"))
    used = _pct(usage.get("percentage"))
    if used is None and total is not None and window:
        # 同一返回体里的等价表达（CLI 的 percentage 是取整值，比值更精确），不是外部推断
        used = _pct(total / window * 100)
    if used is None:
        raise ValueError("Harness 未返回上下文占用百分比（claude 返回体缺 percentage 与 totalTokens/maxTokens）")
    threshold = None
    raw_threshold = _num(usage.get("autoCompactThreshold"))
    if raw_threshold is not None and window:
        # claude 给的是 Token 阈值（如 167000），不换算就会显示成 167000%
        threshold = _pct(raw_threshold / window * 100)
    categories: list[dict] = []
    for item in usage.get("categories") or []:
        name = str((item or {}).get("name") or "").strip()
        tokens = _num((item or {}).get("tokens"))
        if not name or tokens is None or not window:
            continue
        ctype = _CLAUDE_CATEGORY_TYPES.get(name.lower(), "other")
        pct = _pct(tokens / window * 100)
        if pct is None:
            continue
        categories.append({"type": ctype, "label": CATEGORY_LABELS.get(ctype, name), "percentage": pct})
    return {
        "used_percentage": used,
        "threshold_percentage": threshold,
        "auto_compact_enabled": bool(usage.get("isAutoCompactEnabled")),
        "categories": categories,
        "model": str(usage.get("model") or "")[:64],
        # claude 返回体没有 messageCount 等价字段：不拿别的数充数，直接记 0 并在此说明
        "message_count": 0,
    }


def snapshot_from_usage(usage: dict, *, backend: str = "qoder") -> dict:
    """`get_context_usage()` 返回体 → 落库快照（按后端分派，只留展示与判定需要的字段）。

    占用百分比缺失时显式抛错：调用方据此写 `context_error`，而不是把"测不到"记成"占用 0%"。
    未知后端同样报错：拿一套键名去解另一套返回体只会得到“没数据”，现场完全看不出来。
    """
    usage = usage or {}
    if backend == "claude":
        return _snapshot_from_claude(usage)
    if backend != "qoder":
        raise ValueError(f"未知的 Harness 后端，无法归一上下文占用: {backend!r}")
    window = usage.get("contextWindow") or {}
    auto = usage.get("autoCompact") or {}
    categories: list[dict] = []
    for item in usage.get("categories") or []:
        ctype = str((item or {}).get("type") or "")
        pct = _pct((item or {}).get("percentage"))
        if not ctype or pct is None:
            continue
        categories.append({"type": ctype, "label": CATEGORY_LABELS.get(ctype, ctype),
                           "percentage": pct})
    used = _pct(window.get("usedPercentage"))
    if used is None:
        raise ValueError("Harness 未返回上下文占用百分比")
    session = usage.get("session") or {}
    return {
        "used_percentage": used,
        "threshold_percentage": _pct(auto.get("thresholdPercentage")),
        "auto_compact_enabled": bool(auto.get("enabled")),
        "categories": categories,
        "model": str(usage.get("model") or "")[:64],
        "message_count": int(session.get("messageCount") or 0),
    }


def save_snapshot(conversation_id: int, snapshot: dict) -> None:
    """采集成功：写占用并把上次的失败原因清空（不留"旧错误 + 新数据"的混合态）。"""
    execute_write(
        "UPDATE adh_agent_sessions SET context_used_pct=%s, context_threshold_pct=%s,"
        " context_categories=%s, context_model=%s, context_measured_at=UTC_TIMESTAMP(6),"
        " context_error=NULL, updated_at=UTC_TIMESTAMP(6) WHERE conversation_id=%s",
        (snapshot["used_percentage"], snapshot.get("threshold_percentage"),
         json.dumps(snapshot.get("categories") or [], ensure_ascii=False),
         snapshot.get("model") or "", conversation_id))


def save_measure_error(conversation_id: int, reason: str) -> None:
    """采集失败也要留痕：前端据此说"测量失败（原因）"，而不是"尚未测量"。"""
    execute_write("UPDATE adh_agent_sessions SET context_error=%s WHERE conversation_id=%s",
                  (str(reason or "")[:255], conversation_id))


async def collect_and_save(client, conversation_id: int, *, backend: str = "qoder") -> Optional[dict]:
    """轮末采集。**旁路**：任何失败只落 `context_error` + 告警，绝不抛进主链路。

    但失败不是"没人知道"：`context_error` 会被 `get_usage` 回给前端显示，
    采集点缺失（未测量）与采集失败是两种可区分状态。

    `backend` 决定按哪套返回体解析：两个后端的 `get_context_usage` 字段名完全不同，
    写死一套就会让另一套永远“测不到”（实测缺陷：直连通道进度条长期 --%）。
    落库走 `run_owned_sync`：元数据库在跨公网 RTT 上，直接在事件循环里跑同步 SQL 会把
    整台 web 进程的其他流式回合一起卡住。
    """
    if client is None or not conversation_id:
        return None
    try:
        usage = await client.get_context_usage()
        snapshot = snapshot_from_usage(usage or {}, backend=backend)
    except Exception as exc:  # noqa: BLE001 — 观测/展示旁路，不得影响取数主链路
        logger.warning("上下文占用采集失败（不影响本轮结果）: conversation=%s err=%s",
                       conversation_id, exc)
        try:
            await run_owned_sync(save_measure_error, conversation_id, str(exc))
        except Exception:  # noqa: BLE001 — 连错误都写不进库时只进日志，不套娃抛
            logger.exception("上下文采集失败原因落库失败: conversation=%s", conversation_id)
        return None
    try:
        await run_owned_sync(save_snapshot, conversation_id, snapshot)
    except Exception as exc:  # noqa: BLE001
        logger.warning("上下文占用落库失败: conversation=%s err=%s", conversation_id, exc)
        return snapshot
    return snapshot


def get_usage(conversation_id: int, user_id: int) -> dict:
    """读回最近一次采集值（进度条数据源）。

    三态必须可区分，不能都显示成"0%"或都显示成"正常"：
    - 从未测量（`context_measured_at` 为空）→ `available=false` + "尚未测量"，`error` 为空；
    - 测过但上次失败 → 仍回上一次成功值，并附 `error`（用户看得见"这次没刷新出来"）；
    - 采集失败且从未成功 → `available=false` + `error`（前端据此区分"没数据可显示"与"显示不出来"：
      前者不渲染进度条，后者必须留着报错，不能一起藏掉）；
    - 正常 → 占用/阈值/分类明细 + `busy`（执行权状态，前端据此禁用压缩按钮）。
    """
    row = execute_query(
        """SELECT s.status, s.context_used_pct, s.context_threshold_pct, s.context_categories,
                  s.context_model, s.context_measured_at, s.context_error, s.compact_count,
                  s.last_compact_at
           FROM adh_agent_sessions s
           JOIN adh_conversations c ON c.id = s.conversation_id
           WHERE s.conversation_id=%s AND c.user_id=%s""",
        (conversation_id, user_id), fetchone=True)
    if not row:
        # 查不到行只有两种原因（会话不存在 / 不属当前用户），两者都不对外区分（不泄露他人会话存在性），
        # 但文案不能写成“本会话尚未执行过回合”——那会把“无权访问”误导成“多发一句就有了”。
        return {"available": False, "reason": "没有可显示的上下文占用（会话不存在、已删除或不属于当前用户）",
                "error": "", "used_percentage": None, "threshold_percentage": None, "categories": [],
                "auto_compact_enabled": False, "compact_count": 0, "busy": False, "model": ""}
    categories = row.get("context_categories")
    if isinstance(categories, str):
        try:
            categories = json.loads(categories or "[]")
        except json.JSONDecodeError:
            categories = []
    measured = row.get("context_measured_at")
    used = _pct(row.get("context_used_pct"))
    available = bool(measured) and used is not None
    # `error` 是机器可判的"采集失败事实"（成功采集时 save_snapshot 会把它清成 NULL）：
    # 前端靠它区分"还没测过（可以不显示）"与"显示不出来（必须留着报错）"，不靠比对中文文案。
    error = str(row.get("context_error") or "").strip()
    reason = "" if available else (error or "尚未测量（发送一轮后开始测量）")
    return {
        "available": available,
        "reason": reason,
        "error": error,
        "used_percentage": used if available else None,
        "threshold_percentage": _pct(row.get("context_threshold_pct")),
        "categories": categories or [],
        "auto_compact_enabled": bool(row.get("context_threshold_pct") is not None),
        "measured_at": measured.isoformat() if hasattr(measured, "isoformat") else (measured or ""),
        "compact_count": int(row.get("compact_count") or 0),
        "last_compact_at": (row.get("last_compact_at") or ""),
        "model": row.get("context_model") or "",
        # busy = 服务端对"这一会儿不能压缩"的权威判定，不靠前端自己猜 loading
        "busy": str(row.get("status") or "") in ("running", "deleting"),
    }


def note_compaction(conversation_id: int) -> int:
    """压缩计数 +1 并返回新值（用户要看得见"这个会话已经压过几次"）。"""
    execute_write("UPDATE adh_agent_sessions SET compact_count=compact_count+1,"
                  " last_compact_at=UTC_TIMESTAMP(6), updated_at=UTC_TIMESTAMP(6)"
                  " WHERE conversation_id=%s", (conversation_id,))
    row = execute_query("SELECT compact_count FROM adh_agent_sessions WHERE conversation_id=%s",
                        (conversation_id,), fetchone=True)
    return int((row or {}).get("compact_count") or 0)


def _load_compact_target(conversation_id: int, user_id: int) -> tuple[dict, dict]:
    """会话行 + 执行映射行（属主校验在此做，不信任请求体里的身份字段）。"""
    conv = execute_query(
        "SELECT id, user_id, workspace_id, datasource_id, as_bot_key FROM adh_conversations"
        " WHERE id=%s AND user_id=%s", (conversation_id, user_id), fetchone=True)
    if not conv:
        raise ContextCompactNotFound("会话不存在或无权访问")
    session = execute_query(
        "SELECT * FROM adh_agent_sessions WHERE conversation_id=%s AND user_id=%s",
        (conversation_id, user_id), fetchone=True)
    if not session:
        raise ContextCompactBusy("该会话还没有执行工作区（尚未对话过），没有可压缩的上下文")
    return conv, session


def _acquire_execution_right(session_key: str) -> str:
    """拿执行权：`idle` 且无残留 token 才允许（与 claim_session 同一条件更新纪律）。

    拿不到就显式拒绝并给出可操作原因，**不退化成"无锁直接压缩"**——并发跑两个 SDK 客户端
    操作同一个 resume 会话会互相踩状态。
    """
    token = uuid.uuid4().hex
    changed = execute_write(
        "UPDATE adh_agent_sessions SET status='running',execution_token=%s,version=version+1,"
        "owner_pid=%s,owner_start=%s,sdk_pid=NULL,sdk_start=NULL,updated_at=UTC_TIMESTAMP(6)"
        " WHERE session_key=%s AND status='idle' AND execution_token IS NULL",
        (token, os.getpid(), _process_identity(os.getpid()), session_key))
    if changed != 1:
        row = execute_query("SELECT status, execution_token FROM adh_agent_sessions"
                            " WHERE session_key=%s", (session_key,), fetchone=True) or {}
        status = str(row.get("status") or "")
        reason = _BUSY_REASON.get(status)
        if not reason and row.get("execution_token"):
            reason = "上一轮执行权尚未确认释放，请先停止本轮或稍后重试"
        raise ContextCompactBusy(reason or "会话执行状态冲突，请稍后重试")
    return token


def _process_identity(pid: int) -> str:
    from backend.modules.mind.execution.session_workspace import process_identity
    return process_identity(pid)


def _compact_options(sdk, adapter, workspace: Path, runtime_dir: Path, resume_id: str,
                     events: dict):
    """压缩回合的 SDK options：与问答同一 cwd/env/会话参数，但**不暴露任何工具**。

    压缩是会话内部整理动作，不该有取数/写文件能力；`can_use_tool` 一律拒绝是纵深防御
    （即便 CLI 侧误发起工具调用也不会触达数据源）。

    两个后端都要支持：压缩必须跑在“本轮问答用的同一条执行层/同一个模型通道”上，
    否则 claude 直连会话会被丢回 Qoder 网关去压缩（计费口径与转录都不一致）。
    """
    from backend.modules.mind.execution.secure_sdk import (
        DEFAULT_MAX_TOOL_ROUNDS, _sdk_cli_path, resolve_access_token)
    from backend.modules.mind.execution.llm_resources import resolve_effective_model

    env = {k: None for k in os.environ if k not in ("PATH", "LANG", "LC_ALL")}
    home = str(runtime_dir)
    env.update({"HOME": home, "XDG_CONFIG_HOME": home,
                "TMPDIR": str(runtime_dir / "tmp"), "QODERCN_CONFIG_DIR": home + "/qoder-cn"})

    async def deny_all(name, args, context):
        return sdk.PermissionResultDeny(message="上下文压缩回合不允许调用工具")

    async def on_pre_compact(data, tool_use_id, context):
        events["trigger"] = str((data or {}).get("trigger") or "") or events.get("trigger", "")
        return {}

    async def on_post_compact(data, tool_use_id, context):
        events["compacted"] = True
        events["trigger"] = str((data or {}).get("trigger") or "") or events.get("trigger", "")
        events["summary_chars"] = len(str((data or {}).get("compact_summary") or ""))
        return {}

    # 上限与问答同一个键、同一个缺省常量（写死 20 会变成“改了 Harness 只有一边生效”的双口径）
    max_turns = int((getattr(adapter, "config", None) or {}).get("max_turns") or DEFAULT_MAX_TOOL_ROUNDS)
    shared = dict(tools=[], allowed_tools=[], permission_mode="default", setting_sources=[],
                  cwd=str(workspace), add_dirs=[], env=env, mcp_servers={}, agents={},
                  system_prompt="", max_turns=max_turns, include_partial_messages=False,
                  can_use_tool=deny_all,
                  hooks={"PreCompact": [sdk.HookMatcher(hooks=[on_pre_compact])],
                         "PostCompact": [sdk.HookMatcher(hooks=[on_post_compact])]})

    # 与问答同一个四级链（见 llm_resources.resolve_effective_model）。回合级传空是
    # 诚实的：压缩是会话内部整理动作，本轮没有人在现场选模型，从 Harness 级起算。
    model = resolve_effective_model("", getattr(adapter, "model", "")) or None
    if (getattr(adapter, "backend", "") or "qoder") == "claude":
        # claude 后端：凭据与会话参数走与问答同一个实现（apply_direct_channel），
        # 不在此重写一份“怎么归一地址 / 首轮还是续会话” 的规则。
        from backend.modules.mind.execution import anthropic_direct as ad
        from backend.modules.mind.execution.sdk_tools.compat import sdk_options_class
        direct = ad.apply_direct_channel(env, home, model, resume_id or "")
        direct.pop("_adh_channel", None)
        direct.pop("_adh_new_cli_session", None)
        return sdk_options_class(sdk)(**shared, cli_path=ad.resolve_claude_cli_path(
            (getattr(adapter, "config", None) or {}).get("cli_path") or ""), **direct)

    token = resolve_access_token((getattr(adapter, "config", None) or {}).get("env") or {})
    if not token:
        raise ContextCompactFailed("Harness 凭据未配置(QODERCN_PERSONAL_ACCESS_TOKEN)，无法压缩")
    from qodercn_agent_sdk.auth import AccessTokenAuthOptions
    return sdk.QoderAgentOptions(**shared, resume=resume_id or None, cli_path=_sdk_cli_path(adapter),
                                 model=model,
                                 auth=AccessTokenAuthOptions(access_token=token),
                                 strict_mcp_config=True, allowed_mcp_server_names=[], skills=[])


async def _drive_compact(client, events: dict) -> str:
    """发 `/compact` 并排空响应流，返回 CLI 侧会话 id（压缩可能滚动到新 id，必须回写）。"""
    await client.query("/compact")
    session_id = ""
    final = None
    async for msg in client.receive_response():
        if type(msg).__name__ == "ResultMessage":
            final = msg
            session_id = str(getattr(msg, "session_id", "") or "") or session_id
    if final is None:
        raise ContextCompactFailed("压缩未返回结果：Harness 响应流提前结束")
    if getattr(final, "is_error", False):
        # 原始细节只进日志（护栏 §7），对外给可行动的中文原因
        logger.warning("上下文压缩被 CLI 判为错误: subtype=%s errors=%s",
                       getattr(final, "subtype", ""), getattr(final, "errors", None))
        raise ContextCompactFailed("模型服务未能完成压缩，请稍后重试")
    return session_id


def _compact_as_bot(conv: dict, ws_id: int, user_role: str) -> dict:
    """取会话所属 AS-BOT（只为读它绑定的执行层，能力面仍由原有链路裁决）。

    解析失败不静默当成“没绑层”回退默认层：两条层的模型通道与转录不同，回退会让
    压缩跑在另一个会话历史上（表现为“压了但甚至没降”或 resume 报错）。直接抛，
    由 REST 层映射成可见错误。
    """
    from backend.modules.mind.execution import as_bots

    key = str(conv.get("as_bot_key") or "")
    candidates = as_bots.resolve_as_bots(ws_id, user_role or "", key, int(conv.get("user_id") or 0))
    return as_bots.default_as_bot(candidates) or {}


async def compact_conversation(conversation_id: int, user_id: int, user_role: str = "") -> dict:
    """手动压缩本会话的模型上下文；返回压缩前后占用（前端据此立刻更新进度条）。

    失败一律显式抛（409/502/404 由 REST 层映射），不回 `success=true` —— 用户点了按钮
    却"看起来成功了但条没降"是最坏的结果。
    """
    from backend.modules.mind.execution import service as exec_service
    from backend.modules.mind.execution.manager import get_execution_layer_manager
    from backend.modules.mind.execution.sdk_tools.compat import load_sdk, sdk_client_class
    from backend.modules.mind.execution.session_workspace import (
        SessionWorkspace, locate_existing_session, workspace_base)

    conv, session = await run_owned_sync(_load_compact_target, conversation_id, user_id)
    ws_id = int(conv.get("workspace_id") or 0)
    if ws_id:
        # 授权按会话归属校验（与 validate_conversation 同口径），不按请求体里的入口空间
        from backend.common.auth import authorize_workspace
        authorize_workspace({"user_id": user_id, "role": user_role or ""}, ws_id)
    before = _pct(session.get("context_used_pct"))

    token = await run_owned_sync(_acquire_execution_right, session["session_key"])
    runtime = None
    client = None
    events: dict = {"compacted": False, "trigger": "", "summary_chars": 0}
    try:
        root = locate_existing_session(workspace_base(), session)
        layer = await run_owned_sync(
            exec_service.pick_layer_for_as_bot,
            await run_owned_sync(_compact_as_bot, conv, ws_id, user_role), ws_id)
        if not layer:
            raise ContextCompactFailed("未找到可用的外部 Harness（qoder 层缺失或未启用），无法压缩")
        adapter = get_execution_layer_manager().build_adapter(layer)
        # 压缩用的后端可能已不是会话登记的后端（管理员换了默认 Harness、期间没聊过天）。
        # 转录指针按后端各存一份（backend_sessions），所以这里与 claim_session 同口径取
        # **当前后端**的指针；拿不到就传空，让 CLI 自建会话——绝不拿别后端的 id 去 resume
        # （实测 Claude 对不存在的 id 直接报 "No conversation found"）。
        compact_backend = str(getattr(adapter, "backend", "") or "qoder")
        from backend.modules.mind.execution.session_workspace import normalize_backend_sessions
        resume_id = (normalize_backend_sessions(session.get("backend_sessions")).get(compact_backend)
                     or ((session.get("sdk_session_id") or "")
                         # 槽位属于哪个后端只看登记列：不同后端绝不拿它的 id 去 resume
                         # （迁移未应用的旧行也守这条，宁可重建转录，不要报 No conversation found）
                         if str(session.get("backend") or "") == compact_backend else ""))
        runtime = SessionWorkspace(session["session_key"], token, Path(root), None, None, resume_id,
                                   # backend 必须传：finish() 按后端把压缩后的新指针记进 map，
                                   # 不传就只有槽位更新、map 停留在压缩前的旧 id（两边分叉）。
                                   backend=compact_backend)
        sdk = load_sdk(compact_backend)
        options = _compact_options(sdk, adapter, runtime.workspace, runtime.runtime_dir,
                                   runtime.sdk_session_id, events)
        client = sdk_client_class(sdk)(options=options)
        await asyncio.wait_for(client.connect(), COMPACT_TIMEOUT_S)
        # 进程身份登记：崩溃后回收执行权的证据链要靠它（不登记就会留下解不开的锁）
        await run_owned_sync(runtime.record_client, client)
        new_session_id = await asyncio.wait_for(_drive_compact(client, events), COMPACT_TIMEOUT_S)
        snapshot = await collect_and_save(client, conversation_id,
                                          backend=getattr(adapter, "backend", "") or "qoder")
        after = (snapshot or {}).get("used_percentage")

        # 压缩是否真发生：CLI 的 PostCompact 事件是权威凭据；没有事件就要求占用确实下降。
        # 两者都没有 = 按钮没起作用，必须报错而不是"静悄悄地成功"。
        dropped = before is not None and after is not None and after < before - 0.5
        if not events["compacted"] and not dropped:
            detail = f"（占用 {before}% → {after}%）" if after is not None else "（未能读取压缩后占用）"
            raise ContextCompactFailed(
                f"Harness 未确认压缩{detail}：可能上下文还太短没有可压缩的内容，或该会话尚无历史")

        count = await run_owned_sync(note_compaction, conversation_id)
        try:
            await client.disconnect()
        except Exception as exc:  # noqa: BLE001 — 断连失败交给下面的存活核验兜底，不掩盖
            logger.warning("压缩客户端断开异常（继续核验进程是否已退出）: %s", exc)
        client = None
        await run_owned_sync(runtime.confirm_stopped)
        await run_owned_sync(runtime.finish, new_session_id, True)
        runtime = None
        return {
            "before_percentage": before,
            "after_percentage": after,
            "saved_percentage": (round(before - after, 2) if before is not None and after is not None else None),
            "compact_count": count,
            "confirmed": bool(events["compacted"]),
            "trigger": events.get("trigger") or "manual",
            "threshold_percentage": (snapshot or {}).get("threshold_percentage"),
            "categories": (snapshot or {}).get("categories") or [],
        }
    except ContextCompactFailed:
        raise
    except asyncio.TimeoutError:
        raise ContextCompactFailed(
            f"压缩超过 {int(COMPACT_TIMEOUT_S)} 秒未完成，已中止；请稍后重试或新建会话") from None
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001 — 收尾断连失败不掩盖，但不再向上抛
                logger.exception("压缩客户端收尾断开失败")
        # 未走到成功释放（异常/超时）时，把执行权退回 interrupted：与问答回合异常同构，
        # 下一轮消息会据此丢弃中断的 SDK 会话并从干净上下文继续。
        if runtime is not None and not runtime.closed:
            try:
                await run_owned_sync(runtime.finish, "", False)
            except Exception as exc:  # noqa: BLE001 — 释放失败保持 running，由回收证据链处理
                logger.error("压缩执行权释放失败（保持阻断，等待存活核验回收）: session=%s err=%s",
                             session["session_key"], exc)
