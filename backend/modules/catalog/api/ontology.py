"""Ontology Modeling API — 本体模型生成/编辑/激活/检索。

挂载在 /api/catalog/ontology 前缀下:
    POST   /generate              SSE 流式生成本体草案(自动建 AS-BOT 会话派发任务)
    GET    /models                模型列表
    GET    /models/{id}           模型详情（含三格式内容）
    PUT    /models/{id}           保存草案编辑（JSON 事实源）
    POST   /models/{id}/activate  激活并向量化
    POST   /models/{id}/archive   归档并下线对象向量
    DELETE /models/{id}           删除模型
    GET    /search                对象向量检索（调试/预览）
    POST   /import-yaml           导入 Palantir Ontology YAML 为 active 模型并入图
"""

import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..services import ontology_service
from backend.common.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


def _allowed_source_ids(user: dict) -> set:
    """用户可见数据源集（角色×数据源授权，唯一裁决点，fail-closed）。

    admin 同样纯角色裁决不 bypass（waker-datasource-domain §1）；
    空集=无任何源本体可见，不得当全量。
    get_current_user 返回 dict（{user_id, username, role}），不得按对象属性取值。
    """
    from backend.core.role_service import role_service
    return set(role_service.get_user_allowed_datasources(int(user.get("user_id") or 0), 0))


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ═══════════════════════════════════════════════════════════════════
# 生成本体草案：自动创建 AS-BOT 会话 + 派发『生成本体模型』任务
# ═══════════════════════════════════════════════════════════════════
# 归纳由 AS-BOT 会话内的 qoder agent 完成(generate_ontology_draft 取素材 →
# agent 归纳 → save_ontology_draft 提交)，端点与工具均**不调用任何 LLM**；
# 确定性校验/合并/落库留在 ontology_service。目标数据源业务名进 prompt，
# 内部 id 对 LLM 黑盒；身份一律服务端注入(不接受请求体 created_by)。


def _datasource_brief(datasource_id: int) -> tuple:
    """(数据源业务名, 表规模) — 任务消息用；数据源不存在显式 404。"""
    from backend.common.db.metadata_db import get_metadata_conn

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM adh_datasources WHERE id = %s", (datasource_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="数据源不存在")
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM adh_table_info "
                "WHERE datasource_id = %s AND is_active = 1",
                (datasource_id,))
            count = int((cur.fetchone() or {}).get("cnt") or 0)
    return str(row.get("name") or ""), count


def _resolve_task_as_bot(user: dict, workspace_id: int) -> str:
    """AS-BOT 解析口径与 chat 创建会话一致(角色强绑定，fail-closed)。"""
    from backend.modules.mind.execution.models import ExecutionContext
    from backend.modules.mind.execution.tool_policy import resolve_policy

    ctx = ExecutionContext(user_id=int(user.get("user_id") or 0),
                           user_role=user.get("role") or "",
                           workspace_id=workspace_id,
                           extra={"as_bot_key": ""})
    try:
        return resolve_policy(ctx).as_bot["as_bot_key"]
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _assert_dispatchable(workspace_id: int) -> None:
    """执行层可用性 fail-loud 预检(口径与 ChatService._try_dispatch_via_execution_layer 一致)。

    无可用外部执行层(qoder 层缺失/未启用)时显式报错，不做任何静默回退
    (no-silent-degradation)；PAT 未配等运行期不可用由派发流转译为 error 事件。
    """
    from backend.modules.mind.execution import service as exec_service

    try:
        layers = exec_service.get_workspace_layers(workspace_id)
    except Exception as e:
        logger.error("execution layer resolve failed: %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail="执行层权限配置暂不可用，生成任务未派发") from e
    row, fallback = None, None
    for layer in layers or []:
        if layer.get("status") != "active" or not exec_service.is_healthy_layer(layer):
            continue
        if layer.get("is_default"):
            row = layer
            break
        fallback = fallback or layer
    row = row or fallback
    if row is None:
        row = exec_service.get_default_external_layer()
    if row is None or row.get("status") != "active":
        raise HTTPException(
            status_code=503,
            detail="生成任务无法派发：未找到可用的外部执行层(qoder 层缺失或未启用)")


def _build_task_message(ds_name: str, table_count: int) -> str:
    """服务端组装『生成本体模型』任务消息(数据源业务名，内部 id 对 LLM 黑盒)。"""
    from ..services.ontology_service import ONTOLOGY_SCHEMA_SPEC

    return f"""请执行『生成本体模型』任务：为数据源「{ds_name}」(业务名，共 {table_count} 张业务表)归纳源本体草案。

归纳规范(输出结构 domain / description / objects 必须严格遵守)：
{ONTOLOGY_SCHEMA_SPEC}

执行流程(严格遵守)：
1. 调用 generate_ontology_draft 取第 1 批素材(batch=0，返回里含总批数)；
2. 仅依据素材归纳出本批业务对象(不要臆造表/列/口径)；
3. 调用 save_ontology_draft 提交本批对象：**第一批必须 append=false**(覆盖旧草案)，
   第 2 批起 append=true(并入既有草案，同名对象由服务端确定性合并)；
4. 若总批数 > 1，逐批重复 1-3(batch 递增)；
5. 全部批次完成后汇报：对象总数、业务域、合并告警(warnings)。

注意：工具不接受任何数据源标识参数，目标数据源由服务端绑定；本任务只做归纳与提交，
是否激活由用户在建模页决定。
"""


def _create_task_conversation(user: dict, workspace_id: int, datasource_id: int,
                              as_bot_key: str, ds_name: str, task_message: str) -> int:
    """自动创建任务会话(写 adh_conversations，口径与 chat 创建会话一致)。"""
    from backend.common.db.metadata_db import get_metadata_conn

    messages = [{"role": "user", "content": task_message, "question": task_message}]
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_conversations "
                "(user_id, title, datasource_id, workspace_id, as_bot_key, messages, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())",
                (int(user.get("user_id") or 0), f"生成本体模型：{ds_name}",
                 datasource_id, workspace_id, as_bot_key,
                 json.dumps(messages, ensure_ascii=False)),
            )
            conn.commit()
            return int(cur.lastrowid)


def _persist_task_reply(conversation_id: int, task_message: str, reply: str,
                        tool_calls: list, error_message: str) -> None:
    """执行回复回写会话历史(聊天侧可回看/追问)；旁路持久化失败不回滚主流程，但显式记日志。"""
    from backend.common.db.metadata_db import get_metadata_conn

    assistant = {
        "role": "assistant",
        "content": reply or error_message or "",
        "question": task_message,
        "intent": "agent",
        "reply": reply or error_message or "",
        "tool_calls": list(tool_calls or []),
    }
    if error_message:
        assistant["error"] = error_message
    try:
        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT messages FROM adh_conversations WHERE id = %s", (conversation_id,))
                row = cur.fetchone()
                messages = []
                if row and row.get("messages"):
                    try:
                        messages = json.loads(row["messages"])
                    except ValueError:
                        logger.error("会话 %s 历史消息损坏，已重置后回写", conversation_id)
                        messages = []
                messages.append(assistant)
                cur.execute("UPDATE adh_conversations SET messages = %s, updated_at = NOW() WHERE id = %s",
                            (json.dumps(messages, ensure_ascii=False), conversation_id))
                conn.commit()
    except Exception as e:
        logger.error("任务回复回写会话 %s 失败: %s", conversation_id, e, exc_info=True)


def _parse_sse_frame(frame: str) -> tuple:
    """执行层 SSE 帧(event: X / data: {...}) → (事件名, 数据)。"""
    event, payload = "", {}
    for line in frame.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            try:
                payload = json.loads(line[6:].strip())
            except ValueError:
                payload = {"raw": line[6:].strip()}
    return event, payload


def _tool_name(tool: str) -> str:
    """mcp__server__tool 形态归一为尾段工具名。"""
    return str(tool or "").rsplit("__", 1)[-1]


def _capture_draft_result(captured: dict, output) -> None:
    """从 save_ontology_draft 工具输出提取落库结果(model_id/对象数/合并告警)。"""
    try:
        data = json.loads(output) if isinstance(output, str) else (output or {})
    except ValueError:
        # 展示限长截断时退化为正则提取 model_id(宁可少带字段，不误报未完成)
        match = re.search(r'"model_id"\s*:\s*(\d+)', str(output or ""))
        if match:
            captured["model_id"] = int(match.group(1))
        return
    if not isinstance(data, dict) or not data.get("success"):
        return
    captured["model_id"] = data.get("model_id")
    captured["object_count"] = int(data.get("object_count") or 0)
    captured["merged"] = int(data.get("merged") or 0)
    captured["warnings"] = list(data.get("warnings") or [])


async def _generate_task_events(request: Request, *, task_message: str, conversation_id: int,
                                datasource_id: int, workspace_id: int, user: dict,
                                as_bot_key: str, task_binding: dict):
    """派发『生成本体模型』任务并把 agent 事件流转译为前端契约(progress/done/error)。"""
    from backend.modules.mind.services.chat_service import ChatService

    stream = ChatService().stream_query(
        question=task_message,
        history=[],
        datasource_id=datasource_id,
        model_id=None,
        pipeline_mode="agent",
        retrieval_strategy=None,
        workspace_id=workspace_id,
        user_id=int(user.get("user_id") or 0),
        username=str(user.get("username") or ""),
        request=request,
        conversation_id=conversation_id,
        user_role=str(user.get("role") or ""),
        as_bot_key=as_bot_key,
        task_binding=task_binding,
    )

    token_buf = ""
    captured: dict = {}
    terminal: dict = {}
    error_message = ""

    def _flush_tokens(force: bool = False):
        nonlocal token_buf
        events = []
        lines = token_buf.split("\n")
        token_buf = "" if force else lines.pop()
        for line in lines:
            if line.strip():
                events.append(("progress", {"stage": "agent", "detail": line.strip()}))
        if force and token_buf.strip():
            events.append(("progress", {"stage": "agent", "detail": token_buf.strip()}))
            token_buf = ""
        return events

    try:
        buf = ""
        async for chunk in stream:
            buf += chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
            frames = buf.split("\n\n")
            buf = frames.pop()  # 末段可能是跨 chunk 的半帧，压回 buf 等下一块
            for frame in frames:
                event, data = _parse_sse_frame(frame)
                if event == "token":
                    token_buf += str(data.get("text") or "")
                    for ev in _flush_tokens():
                        yield ev
                elif event == "progress":
                    yield ("progress", {
                        "stage": str(data.get("stage") or "agent"),
                        "detail": str(data.get("message") or data.get("detail") or ""),
                    })
                elif event == "tool_start":
                    yield ("progress", {"stage": "tool",
                                        "detail": f"调用工具 {_tool_name(data.get('tool'))}"})
                elif event == "tool_result":
                    name = _tool_name(data.get("tool"))
                    err = str(data.get("error") or "")
                    if name == "save_ontology_draft" and not err:
                        _capture_draft_result(captured, data.get("output"))
                    yield ("progress", {"stage": "tool",
                                        "detail": f"工具 {name} {'失败: ' + err if err else '完成'}"})
                elif event == "error":
                    error_message = str(data.get("message") or "生成任务执行失败")
                elif event == "done":
                    terminal = dict(data or {})
    except Exception as e:  # noqa: BLE001 — 转译层兜底，不得吞掉失败静默结成“成功”
        logger.error("ontology generate task dispatch failed: %s", e, exc_info=True)
        error_message = error_message or f"生成任务派发失败: {e}"
    finally:
        await stream.aclose()

    for ev in _flush_tokens(force=True):
        yield ev
    reply = str(terminal.get("reply") or "")
    tool_calls = list(terminal.get("tool_calls") or [])
    if terminal.get("error"):
        error_message = error_message or str(terminal.get("error"))
    if not terminal and not error_message:
        error_message = "生成任务未返回结果，请在会话中查看执行过程"

    # 回写会话消息(任务消息在建会话时已落，这里补执行回复)
    _persist_task_reply(conversation_id, task_message, reply, tool_calls, error_message)

    if error_message:
        yield ("error", {"message": error_message, "conversation_id": conversation_id,
                        "workspace_id": workspace_id})
        return
    yield ("done", {
        "model_id": captured.get("model_id"),
        "object_count": int(captured.get("object_count") or 0),
        "merged": int(captured.get("merged") or 0),
        "warnings": list(captured.get("warnings") or []),
        "conversation_id": conversation_id,
        "workspace_id": workspace_id,
        "completed": bool(captured.get("model_id")),
    })


@router.post("/generate")
async def generate_ontology(request: Request, req: dict, user: dict = Depends(get_current_user)):
    """生成本体草案（SSE 流式进度，最终事件携带 model_id 与 conversation_id）。

    自动创建 AS-BOT 会话并派发『生成本体模型』任务，归纳由会话内 qoder agent
    完成(工具不调 LLM)；SSE 事件契约保持 progress/done/error。
    身份一律服务端注入(不接受请求体 created_by)。
    """
    from backend.core.role_service import role_service
    from backend.modules.mind.execution.perm_link import require_write_perm
    from backend.common.auth import authorize_workspace, resolve_user_default_workspace_id

    datasource_id = int(req.get("datasource_id") or 0)
    if not datasource_id:
        raise HTTPException(status_code=400, detail="datasource_id 必填")

    user_id = int(user.get("user_id") or 0)
    workspace_id = resolve_user_default_workspace_id(user_id) or 0
    if not workspace_id:
        raise HTTPException(status_code=422, detail="未找到您的默认工作空间，请先创建工作空间后再试")
    authorize_workspace(user, workspace_id)

    # 权限把关(fail-closed，拒绝可解释)
    try:
        require_write_perm(user_id, workspace_id, "ontology:generate", "生成本体草案")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    # 目标数据源必须 ∈ 用户角色授权集(fail-closed：空授权集不放行)
    allowed = set(role_service.get_user_allowed_datasources(user_id, workspace_id))
    if datasource_id not in allowed:
        raise HTTPException(status_code=403, detail="当前用户无权使用该数据源，请选择已授权数据源")

    ds_name, table_count = _datasource_brief(datasource_id)
    _assert_dispatchable(workspace_id)
    as_bot_key = _resolve_task_as_bot(user, workspace_id)

    task_message = _build_task_message(ds_name, table_count)
    conversation_id = _create_task_conversation(
        user, workspace_id, datasource_id, as_bot_key, ds_name, task_message)
    # 任务绑定标记：业务源草案写入仅限本任务会话(resource_guard.assert_ontology_draft_scope)
    task_binding = {"kind": "ontology_generate", "datasource_id": datasource_id}

    async def event_stream():
        async for event, data in _generate_task_events(
                request, task_message=task_message, conversation_id=conversation_id,
                datasource_id=datasource_id, workspace_id=workspace_id, user=user,
                as_bot_key=as_bot_key, task_binding=task_binding):
            yield _sse_event(event, data)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/generate-business-assets")
def generate_business_assets(user: dict = Depends(get_current_user)):
    """自动生成/刷新业务本体（数据资产路由图）。

    从数据源、数据集、同步任务、数据安全配置、数据质量、用户权限策略与血缘
    自动构建路由索引层（确定性生成，无 LLM）；落库走 save_draft/activate 级联。
    返回生成摘要与显式告警（治理数据未对齐项，不静默吞）。"""
    from ..services import business_asset_ontology
    try:
        return business_asset_ontology.generate_business_asset_ontology(
            created_by=str((user or {}).get("username") or ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("business asset ontology generation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="业务本体自动生成未完成，请查看服务端日志") from e


@router.get("/models")
def list_models(
    datasource_id: int = Query(None, description="按数据源筛选"),
    include_archived: bool = Query(False, description="是否包含归档版本（默认排除，归档版本走版本管理）"),
    user: dict = Depends(get_current_user),
):
    # 可见性：源本体按用户数据源权限分配；业务/系统本体全员可见
    allowed = _allowed_source_ids(user)
    return {"items": ontology_service.list_models(
        datasource_id, include_archived=include_archived,
        allowed_datasource_ids=allowed)}


@router.get("/models/{model_id}")
def get_model(model_id: int, user: dict = Depends(get_current_user)):
    model = ontology_service.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")
    if not ontology_service.can_view_model(model, _allowed_source_ids(user)):
        # 不泄露存在性：不可见即 404（不回 403 提示“存在但无权”）
        raise HTTPException(status_code=404, detail="模型不存在")
    return model


@router.get("/models/{model_id}/versions")
def list_versions(model_id: int):
    """同一模型（datasource_id + name 归组）的全部版本，含归档；用于「版本管理」视图。"""
    try:
        return {"items": ontology_service.list_versions(model_id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/models/{model_id}")
def save_model(model_id: int, req: dict):
    """保存草案编辑：提交 JSON 事实源，服务端重派生 YAML/MD。"""
    json_content = req.get("json_content")
    if not json_content:
        raise HTTPException(status_code=400, detail="json_content 必填")
    try:
        return ontology_service.save_draft(model_id, json_content, name=req.get("name"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/models/{model_id}/activate")
def activate_model(model_id: int):
    """激活模型：旧 active 归档，对象 MD 段向量化入向量库。"""
    try:
        return ontology_service.activate(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/models/{model_id}/archive")
def archive_model(model_id: int):
    try:
        return ontology_service.archive(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/models/{model_id}/restore")
def restore_model(model_id: int):
    """还原已归档模型为草案（归档数据可见可逆）。"""
    try:
        return ontology_service.restore(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/models/{model_id}/kb-sync")
def sync_model_kb(model_id: int):
    """手动把生效模型的脱敏语义文档同步到目标知识库（结果落水位线）。

    同步失败显式报错并写服务端日志，不静默吞成成功；成功/失败均落
    adh_ontology_kb_sync_state 供任务监控/徽章展示。
    """
    from ..services import ontology_kb_sync
    try:
        result = ontology_kb_sync.sync_model_to_qmind(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("manual kb sync failed for model %s: %s", model_id, e, exc_info=True)
        raise HTTPException(status_code=502, detail="知识库同步未完成，请查看服务端日志") from e
    if result.get("error") and not (result.get("synced") or result.get("removed")):
        # 全部目标失败: 显式报错(no-silent-degradation), 不吞成 synced=0 成功响应
        detail = f"知识库同步失败：{result['error']}"
        if result.get("error_hint"):
            detail += f"。{result['error_hint']}"
        raise HTTPException(status_code=502, detail=detail)
    return result


@router.delete("/models/{model_id}")
def delete_model(model_id: int):
    return {"success": ontology_service.delete_model(model_id)}


@router.post("/import-yaml")
def import_yaml(req: dict):
    """导入 Palantir Ontology YAML（ontology/*.yaml）为 active 本体模型并重建图谱。

    body: {datasource_id: int, dir?: str, created_by?: str, rebuild_graph?: bool}
    """
    from ..services import ontology_yaml_import

    datasource_id = int(req.get("datasource_id") or 0)
    if not datasource_id:
        raise HTTPException(status_code=400, detail="datasource_id 必填")
    try:
        return ontology_yaml_import.import_palantir_yaml(
            dir_path=req.get("dir"),
            datasource_id=datasource_id,
            created_by=str(req.get("created_by") or ""),
            rebuild_graph=bool(req.get("rebuild_graph", True)),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("ontology import-yaml failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models/{model_id}/sync-status")
def model_sync_status(model_id: int):
    """模型的图谱/知识库同步状态(工作区头部徽章, 只读容错)。"""
    from ..services import ontology_kb_sync

    return ontology_kb_sync.model_sync_status(model_id)


@router.get("/kb-options")
def kb_options():
    """业务本体可选目标知识库清单(active qmind)。"""
    from ..services import ontology_kb_sync

    return {"items": ontology_kb_sync.list_bindable_kbs()}


@router.put("/models/{model_id}/kb")
def set_model_kb(model_id: int, req: dict):
    """为模型选定目标知识库(业务本体同步去向); 改绑后自动重推/下线旧库。"""
    try:
        return ontology_service.set_model_kb(model_id, req.get("kb_id"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/search")
def search_objects(
    q: str = Query(..., min_length=1, description="检索关键词"),
    datasource_id: int = Query(0),
    limit: int = Query(5, ge=1, le=20),
):
    """对象向量检索（调试与前端预览）。"""
    hits = ontology_service.search_objects(q, datasource_id=datasource_id, limit=limit)
    return {
        "items": [
            {
                "object_key": h["object_key"],
                "display_name": h["display_name"],
                "aliases": h.get("aliases", ""),
                "description": h.get("description", ""),
                "distance": h.get("distance"),
                "object": h.get("object"),
            }
            for h in hits
        ]
    }
