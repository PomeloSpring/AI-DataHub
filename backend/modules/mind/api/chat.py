"""Chat API — Send messages, manage conversations.

消息派发 qoder 执行层(agent 模式);quick 单发管道已退役。
"""

import json
import logging
import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from backend.common.auth import (
    get_current_user, authorize_workspace, get_file_user, resolve_user_default_workspace_id,
)
from backend.common.ttl_cache import conversation_cache
from backend.models.schemas import UserInfo

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 会话缓存辅助 ────────────────────────────────────────────────────────

def _conv_list_key(user_id: int, workspace_id: int, as_bot_key: str) -> str:
    return f"list:{user_id}:{workspace_id}:{as_bot_key}"


def _conv_detail_key(conv_id: int, msg_limit: int = 0, before_seq: int = -1) -> str:
    """详情缓存键必须带分页口径：漏了参数会把“最近 60 条”当整份缓存住，翻页永远读不到旧页。"""
    return f"detail:{conv_id}|{int(msg_limit)}|{int(before_seq)}"


def _invalidate_conv_cache(user_id: int, conv_id: int = 0) -> None:
    """失效该用户的列表缓存 + 指定会话详情缓存（详情有多个分页键，按前缀清）。"""
    conversation_cache.invalidate_prefix(f"list:{user_id}:")
    if conv_id:
        conversation_cache.invalidate_prefix(f"detail:{conv_id}|")


# ── Request/Response Models ──────────────────────────────────────────

class SendMessageRequest(BaseModel):
    question: str
    history: Optional[list[dict]] = []
    datasource_id: Optional[int] = 0
    model_id: Optional[int] = None
    pipeline_mode: Optional[str] = "agent"  # agent(quick 管道已退役)
    retrieval_strategy: Optional[str] = None
    workspace_id: Optional[int] = 0
    attachments: list[dict] = []  # 服务端解析回填的上传附件(send_payload);JSON 请求不得携带
    model_ref: Optional[str] = ""  # 执行层运行时模型(如 provider/model_name)
    session_id: Optional[str] = ""  # 执行层会话 ID(SDK 多轮对话 resume)
    conversation_id: Optional[int] = 0  # chat 会话 ID(qoder 长对话池 key)
    as_bot_key: Optional[str] = ""  # 聊天端选定的 AS-BOT
    report_theme: Optional[str] = ""  # 报告交付主题 id(前端已将"跟随"解析为当前 App 主题; 空=回落默认)
    site_code: Optional[str] = ""  # 会话查询站点(adh_sites.site_code); 只影响默认取数范围, 不改变权限裁决
    # 前置意图漏斗：True = 用户点「转深度分析」显式绕过分流。
    # 它只能关"要不要叫模型"，不能关任何数据/权限护栏（护栏仍由执行层统一入口施加）。
    force_agent: Optional[bool] = False
    # 聊天输入框 `/` 选中的技能名（只影响提示词展开哪份作业细则，不是权限开关）
    selected_skills: Optional[list[str]] = []
    # 聊天输入框 `/` 选中的分析场景 id（与 /slash-options 下发的 scenes 同源）：
    # 服务端按“场景所绑主体”裁决后强制注入查询方案，无权/不存在的连同原因告知用户。
    selected_scenes: Optional[list[str]] = []
    # 聊天输入框 `@` 选中的文件引用 [{source: asset|session, ref}]：服务端在同一收口点
    # 展开成 attachments（归档资产从对象存储取回落盘 uploads/；会话文件按既有路径校验）
    cloud_files: Optional[list[dict]] = []
    # `/` 选中的指令 key（只接受 slash_commands 已登记项，未知 key 直接拒）
    slash_command: Optional[str] = ""
    # 输入框快速入口（slash_commands.MODES：analysis / knowledge）：只决定本轮主路走哪份
    # 作业细则，不是权限开关，也不屏蔽另一条路的能力（数据分析照样可以检索知识库）。
    chat_mode: Optional[str] = ""


def _assert_slash_command(key: str) -> None:
    """未知指令显式拒（不静默当成“没选”），否则用户点了菜单却没人执行。

    客户端动作类指令（kind=action，如清空当前会话）不得当作本轮指令上送：
    它不是模型手里的能力，下发只会得到一句“我这边不能清空会话”的回复，而用户
    看到的是“选了没生效”。这类动作由前端就地执行，拒回时把原因说清。
    """
    if not key:
        return
    from backend.modules.mind.execution import slash_commands
    if not slash_commands.is_known_command(key):
        raise HTTPException(status_code=400,
                            detail=f"未知的快捷指令「{key}」，请在输入框用 / 重新选择")
    if slash_commands.command_kind(key) == "action":
        label = next((c["label"] for c in slash_commands.list_commands() if c["key"] == key), key)
        raise HTTPException(
            status_code=400,
            detail=f"「{label}」是会话操作：在 / 面板里选中即执行，不作为本轮指令下发")


def _assert_chat_mode(mode: str) -> None:
    """未知问答模式同样显式拒（不静默回落默认模式）。"""
    if not mode:
        return
    from backend.modules.mind.execution import slash_commands
    if not slash_commands.is_known_mode(mode):
        # 候选名从同一份 MODES 拿（label 是业务名），不在此写死模式名：
        # 否则改一个地方就会给用户报出一个已经不存在的选择项。
        names = "、".join(m["label"] for m in slash_commands.list_modes())
        raise HTTPException(status_code=400,
                            detail=f"未知的问答入口「{mode}」，请重新选择（可选：{names}）")


# ── Chat Send (Streaming) ────────────────────────────────────────────

@router.post("/send/stream")
async def chat_send_stream(
    request: Request,
    user: UserInfo = Depends(get_current_user),
):
    """Send a message with SSE streaming response.

    消息派发 qoder 执行层(agent 模式)。
    请求体双模: JSON(无附件)或 multipart(payload + files,附件随消息上传落盘会话工作区)。
    """
    from backend.modules.mind.api.send_payload import parse_send_request
    from backend.modules.mind.services.chat_service import ChatService

    from backend.modules.mind.execution.session_workspace import preflight_request
    from starlette.concurrency import run_in_threadpool
    req = await parse_send_request(request, SendMessageRequest, user_id=int(user["user_id"]))
    _assert_slash_command(req.slash_command or "")
    _assert_chat_mode(req.chat_mode or "")
    # preflight 已经解析过一次生效策略，直接透传给漏斗（不重复查权限/工具配置）
    policy = await run_in_threadpool(preflight_request, req, user)
    service = ChatService()
    return StreamingResponse(
        service.stream_query(
            question=req.question,
            history=req.history or [],
            datasource_id=req.datasource_id or 0,
            model_id=req.model_id,
            pipeline_mode=req.pipeline_mode or "agent",
            retrieval_strategy=req.retrieval_strategy,
            workspace_id=req.workspace_id or 0,
            user_id=user["user_id"],
            username=user["username"],
            request=request,
            attachments=req.attachments or [],
            model_ref=req.model_ref or "",
            session_id=req.session_id or "",
            conversation_id=req.conversation_id or 0,
            user_role=user.get("role") or "",
            as_bot_key=req.as_bot_key or "",
            report_theme=req.report_theme or "",
            site_code=req.site_code or "",
            force_agent=bool(req.force_agent),
            selected_skills=list(req.selected_skills or []),
            selected_scenes=list(req.selected_scenes or []),
            slash_command=req.slash_command or "",
            chat_mode=req.chat_mode or "",
            policy=policy,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Chat 可用 AS-BOT 清单 ──────────────────────────────────

@router.get("/as-bots")
def list_chat_as_bots(
    workspace_id: int = Query(0, description="Workspace ID"),
    user: UserInfo = Depends(get_current_user),
):
    """返回当前 (工作空间 + 用户角色) 可选的 AS-BOT 清单(含各自的可用模型).

    与运行时 resolve_as_bots 解析口径一致(工作空间绑定回退全局 + 角色白名单),
    确保聊天端选择器展示的候选与后端实际允许的集合对齐。
    """
    from backend.modules.mind.execution import as_bots as as_bot_service

    cache_key = f"as_bots:{workspace_id}:{user.get('role', '')}"
    cached = conversation_cache.get(cache_key)
    if cached is not None:
        return cached

    authorize_workspace(user, workspace_id)
    resolved = as_bot_service.resolve_as_bots(workspace_id, user.get("role") or "", user_id=user["user_id"],
                                              include_unavailable=True)
    result = [
        {
            "id": w.get("id"),
            "as_bot_key": w.get("as_bot_key"),
            "name": w.get("name"),
            "display_name": w.get("display_name") or w.get("name"),
            "description": w.get("description") or "",
            "models": w.get("models") or [],
            "is_default": bool(w.get("is_default")),
            "available": w.get("available", True),
            "unavailable_reason": w.get("unavailable_reason", ""),
        }
        for w in resolved
    ]
    conversation_cache.set(cache_key, result)
    return result


# ── Chat 输入框「/」快捷检索候选 ─────────────────────────

@router.get("/slash-options")
def list_chat_slash_options(
    workspace_id: int = Query(0, description="Workspace ID"),
    as_bot_key: str = Query("", description="会话已选 AS-BOT（空=按角色解析全部生效 AS-BOT 合并）"),
    user: UserInfo = Depends(get_current_user),
):
    """返回智能问数输入框用 `/` 可检索选择的候选：场景 / 技能 / 定时任务 / 快捷指令。

    口径与运行时一致，不给前端假选项：
    * **场景**只列「当前身份有权使用的分析场景」——场景按所绑**主体**（本体对象）授权，
      主体不在角色授权数据源覆盖范围内就不候选，也不会在提示词里命中（同源判定，
      见 backend/core/scenario_access.py）；因权限被隐去的条数会如实告知（hidden_count），
      不静默少返；
    * **技能**只列「当前生效 AS-BOT 实际绑定的那些」——未绑的技能选了也不会展开作业
      细则（提示词会显式告知用户“没按该技能执行”，见 prompt_composer），所以不候选；
    * **定时任务**只列本人（属主）在本工作空间的 Agent 任务，不越权枚举他人任务；
      SQL 任务暂不开放（与任务调度页同一口径）。
    返回体只含业务可读名称与描述，不含数据源/内部 id（护栏 §7）。
    """
    from backend.modules.mind.execution import as_bots as as_bot_service
    from backend.modules.mind.execution import slash_commands

    authorize_workspace(user, workspace_id)
    resolved = as_bot_service.resolve_as_bots(workspace_id, user.get("role") or "",
                                             user_id=user["user_id"])
    if as_bot_key:
        resolved = [w for w in resolved if w.get("as_bot_key") == as_bot_key] or resolved
    names = as_bot_service.collect_skill_names(resolved)
    skills = [
        {"name": s.get("name"), "display_name": s.get("display_name") or s.get("name"),
         "description": (s.get("description") or "").strip()}
        for s in as_bot_service.load_skills(names)
    ]

    tasks: list[dict] = []
    try:
        from backend.common.db import execute_query
        rows = execute_query(
            "SELECT id, name, task_config, cron_expression FROM adh_scheduled_tasks "
            "WHERE owner_id=%s AND task_type='agent' "
            "AND (%s = 0 OR workspace_id IN (0, %s)) "
            "ORDER BY updated_at DESC LIMIT 50",
            (int(user["user_id"]), int(workspace_id or 0), int(workspace_id or 0))) or []
        for row in rows:
            cfg = row.get("task_config")
            if isinstance(cfg, str):
                cfg = json.loads(cfg or "{}")
            questions = (cfg or {}).get("questions") or []
            first = next((q for q in questions if isinstance(q, dict) and (q.get("question") or "").strip()),
                         None)
            if not first:
                continue    # 没有问题正文的任务无法复用，不候选（不给出选了没内容的项）
            tasks.append({
                "id": int(row["id"]),
                "name": row.get("name") or "",
                "question": str(first.get("question") or "").strip(),
                "cron_expression": row.get("cron_expression") or "",
            })
    except Exception as e:  # noqa: BLE001 — 候选清单不可用必须可诊断，不得静默回空集当“没有任务”
        logger.warning("slash-options 定时任务候选加载失败: %s", e)
        raise HTTPException(status_code=503,
                            detail="定时任务候选加载失败，请重试（技能与指令仍可用）") from None

    # 分析场景候选：与运行时注入同一主体判定（不能只过滤展示层）
    scenes: list[dict] = []
    scenes_hidden = 0
    try:
        from backend.core import scenario_access
        from backend.modules.mind.execution.models import ExecutionContext
        from backend.modules.mind.execution.quick_paths import list_visible_paths
        from backend.modules.mind.execution.tool_policy import is_system_scope, resolve_policy

        _ctx = ExecutionContext(user_id=user["user_id"], user_role=user.get("role") or "",
                               workspace_id=int(workspace_id or 0),
                               extra={"as_bot_key": as_bot_key or ""})
        try:
            _scope = is_system_scope(resolve_policy(_ctx))
        except Exception:  # noqa: BLE001 — 拿不到策略就按无系统能力处理（fail-closed）
            logger.warning("slash-options 系统能力面判定失败，本轮场景候选不包系统主体场景")
            _scope = False
        allowed_subjects = scenario_access.visible_subject_keys(
            int(user["user_id"]), int(workspace_id or 0),
            role=user.get("role") or "", system_scope=_scope)
        scenes, scenes_hidden = list_visible_paths(allowed_subjects=allowed_subjects,
                                                  workspace_id=int(workspace_id or 0))
    except Exception as e:  # noqa: BLE001 — 场景候选不可用不能拖死整个面板，但必须可诊断
        logger.warning("slash-options 分析场景候选加载失败（技能与任务仍可用）: %s", e)

    return {"skills": skills, "tasks": tasks, "commands": slash_commands.list_commands(),
            "modes": slash_commands.list_modes(), "scenes": scenes,
            "scenes_hidden": scenes_hidden}


# ── 会话上下文用量 / 手动压缩 / 并发容量 ──────────────────────

class ContextCompactRequest(BaseModel):
    """手动压缩只需会话 id：身份一律服务端解析（护栏 §2），不接受任何用户传入的 user/角色字段。"""
    conversation_id: int


@router.get("/context-usage")
def get_chat_context_usage(
    conversation_id: int = Query(..., description="会话 ID"),
    user: UserInfo = Depends(get_current_user),
):
    """本会话最近一次采集的上下文占用（聊天框进度条的唯一数据源）。

    口径：数值来自执行层 SDK 的 `get_context_usage()`（即 CLI `/context` 同源），平台
    不自算 token、不写第二套阈值。从未测量时 `available=false` 且**不回 0%**（0% 会被
    读成“还有很大余量”，是假信号）；只回业务可读百分比与分类名，不含
    sdk_session_id / 主机 / 内部 id（护栏 §7）。
    """
    from backend.modules.mind.execution import context_usage
    return context_usage.get_usage(int(conversation_id), int(user["user_id"]))


@router.post("/context-compact")
async def post_chat_context_compact(
    req: ContextCompactRequest,
    user: UserInfo = Depends(get_current_user),
):
    """手动压缩本会话的模型上下文（任务进行中天然不可压缩）。

    互斥不造第二套锁：压缩与问答回合共用 `adh_agent_sessions` 的执行权条件更新，
    所以“本轮任务进行中”拿不到锁 → 409（带可读原因）；反方向（压缩中发新消息）
    照旧被现有 preflight 409 拦住。
    失败一律显式报错，不回 `success=true`：用户点了按钮却“看着成功了但条没降”是最坏结果。
    """
    from backend.modules.mind.execution import context_usage
    conv_id = int(req.conversation_id or 0)
    if conv_id <= 0:
        raise HTTPException(status_code=400, detail="缺少会话 id，无法定位要压缩的上下文")
    try:
        return await context_usage.compact_conversation(
            conv_id, int(user["user_id"]), user.get("role") or "")
    except context_usage.ContextCompactNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except context_usage.ContextCompactBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc),
                            headers={"Retry-After": "15"}) from None
    except context_usage.ContextCompactFailed as exc:
        logger.error("上下文压缩失败: conversation=%s err=%s", conv_id, exc)
        raise HTTPException(status_code=502, detail=str(exc),
                            headers={"Retry-After": "30"}) from None


@router.get("/capacity")
def get_chat_capacity(user: UserInfo = Depends(get_current_user)):
    """当前并发水位（limit/active/queued/预计等待）+ 账号 credit 配额。

    两者**分开**不混谈：排队是“平台现在人多”，配额是“账号还剩多少 credit”，
    合成一条提示用户就无从判断下一步。quota 只读缓存（不起 CLI 子进程），未采到
    则明说未采到。本端点不依赖工作空间参数，仅登录用户可见聚合数字（护栏 §2/§7）。
    """
    from backend.modules.mind.execution import capacity
    from backend.modules.mind.execution.qoder_quota import peek_quota
    snapshot = capacity.capacity_snapshot()
    snapshot["quota"] = peek_quota()
    return snapshot


# ── Chat 输入框「@」文件引用候选 ───────────────────────────

_MENTION_LIMIT = 30


def _mention_usable(filename: str, size: int) -> bool:
    """`@` 候选必须真的能被本轮上下文消费：附件白名单类型 + 不超附件大小上限。

    不给假选项（与 /slash-options 同纪律）：清单里出现一个点了就报"不支持的文件类型"
    或"超过 20MB"的文件，等于让用户白选一次。
    """
    from backend.modules.mind.multimodal import MAX_FILE_SIZE, classify_extension
    ext = os.path.splitext(filename or "")[1].lower()
    if classify_extension(ext) is None:
        return False
    return int(size or 0) <= MAX_FILE_SIZE


@router.get("/mention-files")
def list_chat_mention_files(
    q: str = Query("", description="文件名检索词(空=按最近更新给前若干条)"),
    conversation_id: int = Query(0, description="当前会话(0=新会话，只列归档资产)"),
    workspace_id: int = Query(0, description="当前工作空间"),
    user: UserInfo = Depends(get_current_user),
):
    """返回输入框 `@` 可检索的文件候选：归档资产（账号级）+ 本会话工作区既有文件。

    口径（产品决策 2026-11-04）：**上云 = 备份，归档 = 资产**。
    * `@` 只列「已归档进我的资产清单的文件」与「当前会话工作区里已有的文件」；
    * 自动上云的会话产物副本属于备份层（换节点/清理时自动回落，不丢文件），**不摊进**
      `@` 清单——否则每个会话的中间产物都混进"可跨会话复用"的选择面，用户十次有八次
      选到自己没打算留的东西。
    要复用别的会话的产物：先在那条消息里归档（一次显式的资产动作），再在任何会话 `@` 它。

    属主 fail-closed：只查 `user_id` = 本人的资产行、只列本人会话的工作区文件；与
    /api/assets 同域（接口不经权限码拦，入口显隐由 asset:read ↔ workspace:assets 裁决）。
    """
    from backend.common.db.metadata_db import get_metadata_conn
    from backend.modules.mind.api.user_assets import list_session_files
    from backend.modules.mind.multimodal import classify_extension

    uid = int(user["user_id"])
    keyword = (q or "").strip().lower()

    assets = []
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            sql = ("SELECT a.id, a.name, a.filename, a.category, a.size, a.created_at, "
                   "       w.name AS origin_workspace_name "
                   "FROM adh_user_assets a LEFT JOIN adh_workspaces w ON w.id = a.origin_workspace_id "
                   "WHERE a.user_id = %s")
            params = [uid]
            if keyword:
                sql += " AND (a.filename LIKE %s OR a.name LIKE %s)"
                like = f"%{keyword}%"
                params += [like, like]
            sql += f" ORDER BY a.created_at DESC LIMIT {_MENTION_LIMIT * 3}"
            cur.execute(sql, tuple(params))
            for r in cur.fetchall():
                if not _mention_usable(r.get("filename") or "", int(r.get("size") or 0)):
                    continue
                assets.append({
                    "source": "asset", "ref": r["id"],
                    "filename": r["filename"], "name": r.get("name") or r["filename"],
                    "category": r.get("category") or "file", "size": int(r.get("size") or 0),
                    "origin_workspace_name": r.get("origin_workspace_name") or "",
                    "created_at": str(r.get("created_at") or ""),
                })
                if len(assets) >= _MENTION_LIMIT:
                    break
    finally:
        conn.close()

    session_files = []
    if conversation_id and workspace_id:
        try:
            rows = list_session_files(int(workspace_id), int(conversation_id), uid)
        except Exception as exc:  # noqa: BLE001 — 会话还没有产物(新会话)不该让 @ 面板报错
            logger.info("@ 候选跳过会话文件(conversation=%s): %s", conversation_id, exc)
            rows = []
        for f in rows:
            rel = f.get("path") or ""
            name = os.path.basename(rel)
            if not _mention_usable(name, int(f.get("size") or 0)):
                continue
            session_files.append({
                "source": "session", "ref": rel, "filename": name, "name": name,
                "category": classify_extension(os.path.splitext(name)[1]) or "document",
                "size": int(f.get("size") or 0),
            })
            if len(session_files) >= _MENTION_LIMIT:
                break

    return {"assets": assets, "session_files": session_files}


# ── Chat Feedback ────────────────────────────────────────

class FeedbackRequest(BaseModel):
    question: str = ""
    tables_used: str = ""
    datasource_id: int = 0
    satisfied: bool
    # expected_table 已冻结：它让业务用户手输**物理表名**，同时违反
    # ui-resource-display §2（不得依赖人填内部标识）与护栏 §7（物理表名不得
    # 面向客户端）。新口径走 expected_ref（业务对象/指标/维度名）。
    expected_table: str = ""
    expected_ref: str = ""
    dissatisfaction_reason: str = ""
    reason: str = ""
    trace_id: str = ""
    message_uuid: str = ""
    conversation_id: int = 0
    workspace_id: int = 0


@router.post("/feedback")
def save_message_feedback(req: FeedbackRequest, user: UserInfo = Depends(get_current_user)):
    """消息赞踩反馈 → adh_message_feedback（自进化闭环的采集口）。

    按 message_uuid 幂等 upsert(唯一键 uk_msg);携带 trace 关联键时
    可观测页(Trace 列表/满意率 KPI)能直接联表命中该回合。
    无 message_uuid 的旧消息前端不传,此处兼容生成,仅计入满意度不关联 trace。

    踩 + 原因/期望业务名会被每日归纳作业转成进化候选（待开发指标/维度/
    待收编别名/待核绑定）。`digest_status='new'` 是作业水位线，保证同一批
    负反馈不被反复归类。
    """
    import uuid as _uuid

    from backend.common.db import execute_write
    from backend.modules.catalog.services.evolution_service import FEEDBACK_REASONS

    reason_key = (req.dissatisfaction_reason or "").strip()
    if reason_key and reason_key not in FEEDBACK_REASONS:
        raise HTTPException(
            status_code=400,
            detail=f"不满意的归因取值不合法: {reason_key!r}，应为 {list(FEEDBACK_REASONS)}")
    if req.satisfied:
        # 点赞不该带"缺哪个指标"这类归因；默默收下会让后面的归纳读到脏信号
        reason_key, expected_ref = "", ""
    else:
        expected_ref = (req.expected_ref or "").strip()[:128]

    message_uuid = req.message_uuid or _uuid.uuid4().hex
    tags = {"tables_used": req.tables_used, "datasource_id": req.datasource_id} if req.tables_used else None
    execute_write(
        """INSERT INTO adh_message_feedback
           (trace_id, conversation_id, message_uuid, user_id, workspace_id,
            satisfied, expected_table, expected_ref, dissatisfaction_reason, reason,
            tags, digest_status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'new')
           ON DUPLICATE KEY UPDATE
             satisfied=VALUES(satisfied), expected_table=VALUES(expected_table),
             expected_ref=VALUES(expected_ref),
             dissatisfaction_reason=VALUES(dissatisfaction_reason),
             reason=VALUES(reason), tags=VALUES(tags),
             digest_status='new',
             trace_id=IF(VALUES(trace_id)='', trace_id, VALUES(trace_id))""",
        (
            req.trace_id or "", req.conversation_id or 0, message_uuid,
            user["user_id"], req.workspace_id or 0,
            1 if req.satisfied else 0, req.expected_table or "", expected_ref,
            reason_key, req.reason or "",
            json.dumps(tags, ensure_ascii=False) if tags else None,
        ),
    )
    return {"ok": True, "message_uuid": message_uuid,
            "will_be_digested": bool(not req.satisfied and (expected_ref or reason_key))}


# ── Conversation Management ──────────────────────────────────────




@router.get("/session-file")
def get_session_file(
    conversation_id: int = Query(..., description="会话 ID"),
    path: str = Query(..., description="工作区相对路径(可带 /workspace/ 前缀)"),
    download: int = Query(0, description="1=作为附件下载, 0=内联预览"),
    user: UserInfo = Depends(get_file_user),
):
    """下载/预览 Agent 写入本会话工作区的产物文件(仅属主, 防目录穿越).

    会话产物镜像在云空间（对象存储）：本地目录不在（换节点/卷被清理）时先按云端
    副本回落再伺服，而不是把“文件其实在云上”显示成 404。
    """
    from backend.modules.mind.execution import session_workspace as sw

    try:
        root = sw.conversation_workspace_root(conversation_id, int(user["user_id"]))
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "会话不存在或无权访问") from None
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    ws_dir = (root / "workspace").resolve()

    from backend.modules.mind.multimodal.loader import normalize_rel_path, resolve_workspace_file

    if not normalize_rel_path(path):
        raise HTTPException(status_code=400, detail="缺少文件路径")
    try:
        # 防目录穿越/符号链接逃逸(与附件引用共用同一守卫)
        target = resolve_workspace_file(ws_dir, path)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e) or "非法文件路径")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    disposition = "attachment" if download else "inline"
    quoted = quote(target.name)
    headers = {"Content-Disposition": f"{disposition}; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"}
    return FileResponse(str(target), media_type=media_type, headers=headers)


@router.get("/render-document/{doc_id}")
def get_render_document(doc_id: str, user: UserInfo = Depends(get_current_user)):
    """按引用取渲染机交付对象（整份报告/图表数据）。

    为什么需要它：交付形态渲染机把正文存成受管文档对象、消息里只留 `{"doc_id": ...}`
    引用（正文不进消息 content：会话存储的容量闸门不覆盖 content，整份 HTML 落消息会
    把会话表体量改坏）。前端渲染时按引用取回。

    归属只校验属主本人（与会话/设计草稿同口径）：不存在与无权访问**分开返**，
    前端才能给“已被清理”和“无权访问”两种不同文案（而不是一律“加载失败”）。
    """
    from backend.modules.viz.services import report_document_service as docs

    try:
        return docs.load_document(doc_id, user_id=int(user["user_id"] or 0))
    except docs.RenderDocumentError as exc:
        status = {"not_found": 404, "forbidden": 403, "no_identity": 401}.get(exc.code, 400)
        raise HTTPException(status_code=status, detail=exc.message) from None


@router.get("/conversations")
def list_conversations(
    workspace_id: int = Query(0, description="Filter by workspace"),
    as_bot_key: str = Query("", description="按归属 AS-BOT 过滤"),
    user: UserInfo = Depends(get_current_user),
):
    """List user's conversations, optionally filtered by workspace and AS-BOT.

    会话历史合并共用（AS-BOT 面板与智能问数同一载体、不同入口），不再按入口区分。
    """
    cache_key = _conv_list_key(user["user_id"], workspace_id, as_bot_key)
    cached = conversation_cache.get(cache_key)
    if cached is not None:
        return cached

    from backend.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cols = "id, title, datasource_id, workspace_id, as_bot_key, site_code, created_at, updated_at"
            where = ["user_id = %s"]
            params: list = [user["user_id"]]
            if workspace_id:
                where.append("workspace_id IN (%s, 0)")
                params.append(workspace_id)
            if as_bot_key:
                where.append("as_bot_key = %s")
                params.append(as_bot_key)
            cur.execute(
                f"SELECT {cols} FROM adh_conversations "
                f"WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT 50",
                tuple(params),
            )
            rows = cur.fetchall()
            for r in rows:
                for k in ("created_at", "updated_at"):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()
            conversation_cache.set(cache_key, rows)
            return rows
    finally:
        conn.close()


@router.get("/conversations/{conv_id}")
def get_conversation(
    conv_id: int,
    msg_limit: int = Query(0, ge=0, description="只取最近 N 条消息（0=整份，旧客户端行为零变化）"),
    before_seq: int = Query(-1, ge=-1, description="往前翻页游标：只取 seq < before_seq 的消息"),
    user: UserInfo = Depends(get_current_user),
):
    """Get conversation with messages.

    长会话不再一次拉全部：传 ``msg_limit`` 只读最近几条，响应里的
    ``has_more_earlier``/``oldest_seq`` 就是往前翻的游标（读拆行表，不再整段 LONGTEXT）。
    """
    cache_key = _conv_detail_key(conv_id, msg_limit, before_seq)
    cached = conversation_cache.get(cache_key)
    if cached is not None:
        return cached

    from backend.common.db.metadata_db import get_metadata_conn
    from backend.core import conversation_store

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, datasource_id, workspace_id, as_bot_key, site_code, "
                "created_at, updated_at "
                "FROM adh_conversations WHERE id = %s AND user_id = %s",
                (conv_id, user["user_id"]),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Conversation not found")
            for k in ("created_at", "updated_at"):
                if hasattr(row.get(k), "isoformat"):
                    row[k] = row[k].isoformat()
            # 历史正文走拆行表（messages 列已退役为迁移证据列，不再整段 LONGTEXT 载入）
            page = conversation_store.load_page(
                conv_id, limit=int(msg_limit or 0),
                before_seq=(int(before_seq) if int(before_seq) >= 0 else None))
            from backend.modules.viz.services.report_service import with_reportability
            row["messages"] = [
                with_reportability(m) if isinstance(m, dict) and m.get("role") == "assistant" else m
                for m in page["messages"]
            ]
            # 翻页契约：总数 + 本页最小 seq + 是否还有更早（前端据此渲染“加载更早消息”）
            row["message_total"] = page["total"]
            row["oldest_seq"] = page["oldest_seq"]
            row["has_more_earlier"] = page["has_more_earlier"]
            conversation_cache.set(cache_key, row)
            return row
    finally:
        conn.close()


class CreateConversationRequest(BaseModel):
    datasource_id: Optional[int] = 0
    workspace_id: Optional[int] = 0
    as_bot_key: Optional[str] = ""
    site_code: Optional[str] = ""


@router.post("/conversations")
def create_conversation(
    req: CreateConversationRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Create a new conversation.

    workspace_id 未指定(0/缺省)时归属用户默认工作空间(个人空间, 随用户自动创建)。
    AS-BOT 与 Waker 统一后不再有全局/系统域会话(历史 ws=0 会话仅兼容保留)。
    """
    # 未指定工作空间 → 解析用户默认工作空间(唯一口径, 见 resolve_user_default_workspace_id)
    workspace_id = req.workspace_id or 0
    if not workspace_id:
        workspace_id = resolve_user_default_workspace_id(user["user_id"])
        if not workspace_id:
            raise HTTPException(status_code=422, detail="未找到您的默认工作空间，请先创建工作空间后再试")
    authorize_workspace(user, workspace_id)
    # AS-BOT 与角色强绑定(一对一)：会话归属直接取当前用户角色的 AS-BOT，
    # 不再依赖页面传标识；无授权 AS-BOT 则 fail-closed。
    as_bot_key = ""
    from backend.modules.mind.execution.models import ExecutionContext
    from backend.modules.mind.execution.tool_policy import resolve_policy
    ctx = ExecutionContext(user_id=user["user_id"], user_role=user.get("role", ""),
                           workspace_id=workspace_id, extra={"as_bot_key": req.as_bot_key or ""})
    try:
        as_bot_key = resolve_policy(ctx).as_bot["as_bot_key"]
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    from backend.common.db.metadata_db import get_metadata_conn

    # 会话站点校验：只接受启用且存在的站点（包含聚合态「全部站点」）；非法值显式报错不静默丢弃
    site_code = str(req.site_code or "").strip()
    if site_code:
        from backend.core.site_service import get_site
        site = get_site(site_code)
        if not site or not int(site.get("is_active") or 0):
            raise HTTPException(status_code=422, detail=f"站点「{site_code}」不可用，请重新选择查询站点")

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_conversations (user_id, title, datasource_id, workspace_id, as_bot_key, site_code, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())",
                (user["user_id"], "新对话", req.datasource_id or 0, workspace_id,
                 as_bot_key, site_code),
            )
            conn.commit()
            conv_id = cur.lastrowid
            _invalidate_conv_cache(user["user_id"])
            return {
                "id": conv_id,
                "title": "新对话",
                "datasource_id": req.datasource_id or 0,
                "workspace_id": workspace_id,
                "as_bot_key": as_bot_key,
                "site_code": site_code,
                "created_at": datetime.now().isoformat(),
            }
    finally:
        conn.close()


class UpdateConversationRequest(BaseModel):
    title: Optional[str] = None
    messages: Optional[list] = None
    executor_session_id: Optional[str] = None
    site_code: Optional[str] = None
    # 分页窗口起点：这份 messages 数组从哪个 seq 开始（0=整份历史，旧客户端不传行为不变）。
    # 只加载了最近 N 条时必须传 oldest_seq，否则 diff 会把没加载的更早消息当“数组变短”删掉。
    message_base_seq: Optional[int] = 0


@router.put("/conversations/{conv_id}")
def update_conversation(
    conv_id: int,
    req: UpdateConversationRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Update conversation title and/or messages.

    消息不再整列覆盖：写入走 conversation_store.save_messages 的 **增量 diff**
    （新增追加 / 变更改写 / 截断删除），旧形态每轮重写整份 LONGTEXT 在 ROW binlog 下
    是 O(n²) 写放大，且会把工具原始返回体一并明文常驻。
    """
    if req.executor_session_id is not None:
        raise HTTPException(422, "SDK 会话标识只能由服务端维护")
    from backend.common.db.metadata_db import get_metadata_conn
    from backend.core import conversation_store

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Verify ownership
            cur.execute(
                "SELECT id, workspace_id FROM adh_conversations WHERE id = %s AND user_id = %s",
                (conv_id, user["user_id"]),
            )
            owner = cur.fetchone()
            if not owner:
                raise HTTPException(status_code=404, detail="Conversation not found")

            if req.messages == []:
                # 清空 = 清理历史消息并重置模型上下文，不关闭会话：置空 sdk_session_id 后
                # 同一会话下一轮以零上下文继续；旧数据里被误判关闭的 closed 行一并重新开放。
                # backend_sessions 必须一起作废：只清单槽位会留下各后端的旧指针，换一次
                # Harness 就把"已清空"的转录接回来，等于清空失效。
                cur.execute("UPDATE adh_agent_sessions SET status='idle', sdk_session_id=NULL, "
                            "backend_sessions=NULL, execution_token=NULL, updated_at=UTC_TIMESTAMP(6) "
                            "WHERE conversation_id=%s AND user_id=%s AND status IN ('idle','interrupted','closed')",
                            (conv_id, user["user_id"]))
                cur.execute("SELECT status FROM adh_agent_sessions WHERE conversation_id=%s", (conv_id,))
                session = cur.fetchone()
                if session and session["status"] == "running":
                    raise HTTPException(409, "会话尚未停止，暂不能清空记录")
                if session and session["status"] == "deleting":
                    raise HTTPException(409, "会话正在删除中，暂不能清空")
            # Build dynamic update（只覆盖轻列：title / site_code；正文在拆行表）
            updates = ["updated_at = NOW()"]
            params = []
            if req.title is not None:
                updates.append("title = %s")
                params.append(req.title)
            if req.site_code is not None:
                site_code = str(req.site_code or "").strip()
                if site_code:
                    from backend.core.site_service import get_site
                    site = get_site(site_code)
                    if not site or not int(site.get("is_active") or 0):
                        raise HTTPException(status_code=422,
                                            detail=f"站点「{site_code}」不可用，请重新选择查询站点")
                updates.append("site_code = %s")
                params.append(site_code)

            stats: dict = {}
            if req.messages is not None:
                try:
                    stats = conversation_store.save_messages(
                        cur, conv_id, req.messages,
                        user_id=int(user["user_id"]),
                        workspace_id=int((owner or {}).get("workspace_id") or 0),
                        base_seq=int(req.message_base_seq or 0))
                except ValueError as exc:
                    # 窗口起点越界 = 本地与服务端对历史认知不一致（例如别处刚清空）：
                    # 不猜不静默对齐，回可诊断原因让客户端重新加载
                    conn.rollback()
                    raise HTTPException(status_code=422, detail=str(exc))

            params.append(conv_id)
            cur.execute(
                f"UPDATE adh_conversations SET {', '.join(updates)} WHERE id = %s",
                params,
            )
            conn.commit()
            _invalidate_conv_cache(user["user_id"], conv_id)
            return {"success": True, **({"messages": stats} if stats else {})}
    finally:
        conn.close()


class ReportDraftRequest(BaseModel):
    """报表草稿初稿的定位键。正文与标题一律由服务端从已落库会话读，
    不接受请求体传正文（护栏 §2：身份与内容都不信 body）。"""
    message_index: Optional[int] = None
    message_uuid: str = ""


@router.post("/conversations/{conv_id}/report-draft")
def create_report_draft(conv_id: int, req: ReportDraftRequest,
                        user: UserInfo = Depends(get_current_user)):
    """生成报表草稿初稿（留在会话里可反复调整，点「归档」才进报表中心）。

    与归档（``POST /api/reports`` 的 conversation 通道）共用同一套定位与治理判定：
    不可存的回合（纯问答/系统操作）给可解释原因，含基础设施信息的回答在这里就拦，
    不等用户改完稿才发现存不出去。
    """
    from backend.modules.viz.services import report_service
    try:
        return report_service.build_report_draft(
            user=user, conversation_id=conv_id,
            message_index=req.message_index, message_uuid=req.message_uuid)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def persist_turn_fallback(*, conversation_id: int, user_id: int, message: dict,
                          message_uuid: str = "", question: str = "") -> dict:
    """服务端兜底落库：把本回合已产出的 assistant 内容写进会话，不依赖前端那次 PUT。

    缺陷根因（会话 1783069459150）：消息持久化完全由前端在收到 done 后 PUT 整份消息数组。
    用户合上标签页/断网后，SSE 断开使服务端回合被取消，但模型这一轮跑满 126s、烧掉
    12 次模型请求的产出**一个字都没落库**（`messages=[]`），管理端 trace 还记成 success。
    服务端手里明明有正文与工具清单，就必须自己兜住这份用户资产。

    口径：
    - 身份只信服务端入参（由执行上下文传入），绝不从请求体取 user_id（护栏 §2）；
    - 幂等：按 `message_uuid` 判定该回合是否已被前端落库，已存在则**跳过**，
      不覆盖用户后续编辑/清空的结果（前端 PUT 的仍是整份数组，以其为准）；
    - 空回合（无正文、无工具调用、无错误）不写入，避免制造空气泡；
    - 失败不静默：返回带 reason 的结果并打 ERROR 日志，由调用方（收尾任务）标注到 trace。

    存储形态：走 conversation_store 拆行追加（不再读改写整份 LONGTEXT 列），
    工具原始返回体等大字段在写入前统一过入库投影。
    """
    from backend.common.db import DBConnection
    from backend.core import conversation_store

    cid = int(conversation_id or 0)
    uid = int(user_id or 0)
    if cid <= 0 or uid <= 0:
        return {"persisted": False, "reason": "no_conversation_or_identity"}
    content = str(message.get("content") or "").strip()
    if not content and not message.get("tool_calls") and not message.get("error"):
        return {"persisted": False, "reason": "empty_turn"}
    with DBConnection() as conn:
        conn.begin()
        try:
            with conn.cursor() as cur:
                out = conversation_store.append_message(
                    cur, cid, message, user_id=uid, message_uuid=message_uuid,
                    question=question, extra_fields={"fallback_persisted": True})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    if not out.get("persisted"):
        logger.info("[persist_turn_fallback] 会话 %s 本回合无需兜底写入: %s", cid, out.get("reason"))
        return out
    _invalidate_conv_cache(uid, cid)
    return out


# ── MCP Tools ─────────────────────────────────────────────────────

@router.get("/mcp-tools")
def list_mcp_tools(
    workspace_id: int = Query(0, description="Workspace ID"),
    user: UserInfo = Depends(get_current_user),
):
    """List available MCP servers and their tools for the given workspace."""
    from backend.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Get workspace MCP servers
            cur.execute(
                "SELECT id, name, url, discovered_tools, tools_config, is_active "
                "FROM adh_mcp_servers WHERE is_active = 1 AND workspace_id = %s",
                (workspace_id,),
            )
            servers = []
            for row in cur.fetchall():
                # Parse discovered tools
                all_tools = []
                discovered = row.get("discovered_tools", "")
                if isinstance(discovered, str) and discovered:
                    try:
                        parsed = json.loads(discovered)
                        if isinstance(parsed, list):
                            all_tools = parsed
                    except json.JSONDecodeError:
                        pass

                # Apply whitelist filter
                tools_config = row.get("tools_config", "")
                whitelist = None
                if isinstance(tools_config, str) and tools_config:
                    try:
                        parsed = json.loads(tools_config)
                        if isinstance(parsed, list) and parsed:
                            whitelist = {t.get("name") for t in parsed if isinstance(t, dict)}
                    except json.JSONDecodeError:
                        pass

                if whitelist:
                    filtered = [t for t in all_tools if t.get("name") in whitelist]
                else:
                    filtered = all_tools

                servers.append({
                    "id": row["id"],
                    "server_name": row["name"],
                    "server_url": row["url"],
                    "tools": filtered,
                })

            return {"servers": servers}
    finally:
        conn.close()


#: 一次批量删除的上限：防误选把整个会话历史清空，也让一次请求里的逐条清理可控。
_MAX_BATCH_DELETE = 50


def _delete_one_conversation(conv_id: int, user_id: int) -> None:
    """删除会话的唯一实现（单条与批量共用，不新建第二套语义）。失败抛 HTTPException。

    对外文案不携带目录/会话内部标识，只说“记录仍保留 + 下一步”（护栏 §7）。
    """
    from backend.modules.mind.execution.session_workspace import delete_conversation_workspace

    try:
        delete_conversation_workspace(conv_id, user_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("会话及工作区删除未完成: conversation_id=%s", conv_id)
        raise HTTPException(
            status_code=503,
            detail="会话工作区清理未完成，记录仍保留；可能已清理部分文件，请重试删除或联系管理员",
        ) from exc
    _invalidate_conv_cache(user_id, conv_id)


@router.delete("/conversations/{conv_id}")
def delete_conversation(
    conv_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """删除会话及其独立工作区；只有目录清理成功才移除数据库记录。"""
    _delete_one_conversation(int(conv_id), int(user["user_id"]))
    return {"success": True}


class BatchDeleteConversationsRequest(BaseModel):
    conversation_ids: list[int]


@router.post("/conversations/batch-delete")
def batch_delete_conversations(
    req: BatchDeleteConversationsRequest,
    user: UserInfo = Depends(get_current_user),
):
    """批量删除会话：逐条走与单条完全相同的删除路径，失败逐条给原因、不静默丢。

    归属预检是必要的：delete_conversation_workspace 对“不存在/不属于本人”的行执行
    DELETE ... AND user_id=%s，rowcount=0 也返回 success ——幂等对单条没问题，但批量里
    会把“根本没删掉”报成“已删除”，用户看到的清单与库里就不一致了。
    """
    from backend.common.db import execute_query

    uid = int(user["user_id"])
    ids = list(dict.fromkeys(int(i) for i in (req.conversation_ids or []) if int(i) > 0))
    if not ids:
        raise HTTPException(status_code=400, detail="请先勾选要删除的会话")
    if len(ids) > _MAX_BATCH_DELETE:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多删除 {_MAX_BATCH_DELETE} 个会话（本次 {len(ids)} 个），请分批操作")

    placeholders = ",".join(["%s"] * len(ids))
    rows = execute_query(
        f"SELECT id, title FROM adh_conversations WHERE id IN ({placeholders}) AND user_id=%s",
        tuple(ids) + (uid,)) or []
    titles = {int(r["id"]): (r.get("title") or "未命名会话") for r in rows}

    deleted: list[int] = []
    failed: list[dict] = []
    for cid in ids:
        if cid not in titles:
            failed.append({"id": cid, "title": "", "reason": "会话不存在或不属于当前用户"})
            continue
        try:
            _delete_one_conversation(cid, uid)
            deleted.append(cid)
        except HTTPException as exc:
            # 原因文案来自删除路径本身（如“会话仍在执行”），原样转述不编造
            failed.append({"id": cid, "title": titles[cid], "reason": str(exc.detail)})
    return {"deleted": deleted, "failed": failed,
            "deleted_count": len(deleted), "failed_count": len(failed)}
