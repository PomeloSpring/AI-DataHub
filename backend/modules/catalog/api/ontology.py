"""Ontology Modeling API — 本体模型生成/编辑/激活/检索。

挂载在 /api/catalog/ontology 前缀下:
    POST   /generate                        派发『生成本体模型』后台任务（立即返回 conversation_id）
    GET    /generate/tasks                   本用户后台生成任务清单（页面挂回/侧边栏徽标）
    GET    /generate/tasks/{id}              任务进度与终态（前端轮询）
    POST   /generate/tasks/{id}/cancel       取消正在后台执行的任务
    GET    /models                模型列表
    GET    /models/{id}           模型详情（含三格式内容）
    PUT    /models/{id}           保存草案编辑（JSON 事实源）
    POST   /models/{id}/activate  激活并向量化
    POST   /models/{id}/archive   归档并下线对象向量
    DELETE /models/{id}           删除模型
    GET    /search                对象向量检索（调试/预览）
    POST   /import-yaml           导入 Palantir Ontology YAML 为 active 模型并入图
"""

import asyncio
import json
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

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


def _build_task_message(ds_name: str, table_count: int, kb_count: int = 0) -> str:
    """服务端组装『生成本体模型』任务消息(数据源业务名，内部 id 对 LLM 黑盒)。"""
    from ..services.ontology_service import ONTOLOGY_SCHEMA_SPEC

    kb_note = (f"\n本次生成已选定 {kb_count} 个业务知识库，素材里会附带检索到的业务线索"
               "（实体关系/统计维度），请据此推断 links 与 dimensions（仅参考，不臆造）。"
               if kb_count else "")
    return f"""请执行『生成本体模型』任务：为数据源「{ds_name}」(业务名，共 {table_count} 张业务表)归纳源本体草案。{kb_note}

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


def _task_message_payload(run: dict) -> dict:
    """任务运行态 → 会话里的 assistant 消息（AS-BOT 侧边栏直接渲染 progressStages/tool_calls）。

    会话转录是任务进度的**事实源**：面板不在、页面已关都不影响执行与回看。
    """
    assistant = {
        "role": "assistant",
        "content": run.get("reply") or (run.get("error") if run["status"] != "running" else ""),
        "question": run.get("task_message") or "",
        "intent": "agent",
        "reply": run.get("reply") or "",
        "tool_calls": list(run.get("tool_calls") or []),
        "progressStages": [
            {"stage": p["stage"], "message": p["detail"], "timestamp": p["at"]}
            for p in (run.get("progress") or [])[-40:]
        ],
        "task_kind": _TASK_KIND,
        "task_state": run["status"],
    }
    if run.get("error"):
        assistant["error"] = run["error"]
    if run.get("model_id"):
        assistant["model_id"] = run["model_id"]
    return assistant


def _write_task_message(run: dict) -> None:
    """把任务进度/终态 upsert 进会话（只保留一条本任务 assistant 消息，不越写越多）。

    旁路持久化：写失败只记日志不回滚主流程（护栏 §9），但失败事实必须可查。
    """
    from backend.common.db.metadata_db import get_metadata_conn

    conv_id = int(run.get("conversation_id") or 0)
    if not conv_id:
        return
    payload = _task_message_payload(run)
    try:
        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT messages FROM adh_conversations WHERE id = %s", (conv_id,))
                row = cur.fetchone()
                messages: list = []
                if row and row.get("messages"):
                    try:
                        messages = json.loads(row["messages"])
                    except ValueError:
                        logger.error("会话 %s 历史消息损坏，已重置后回写", conv_id)
                        messages = []
                for i in range(len(messages) - 1, -1, -1):
                    m = messages[i]
                    if (isinstance(m, dict) and m.get("role") == "assistant"
                            and m.get("task_kind") == _TASK_KIND):
                        messages[i] = payload
                        break
                else:
                    messages.append(payload)
                cur.execute(
                    "UPDATE adh_conversations SET messages = %s, updated_at = NOW() WHERE id = %s",
                    (json.dumps(messages, ensure_ascii=False), conv_id))
                conn.commit()
    except Exception as e:  # noqa: BLE001 — 进度回写不得打断执行，但必须显式留痕
        logger.error("任务进度回写会话 %s 失败: %s", conv_id, e, exc_info=True)


def _load_task_message(conversation_id: int) -> dict:
    """读会话里本任务的持久态：{found, owner_user_id, workspace_id, datasource_id, message}。"""
    from backend.common.db.metadata_db import get_metadata_conn

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT c.user_id, c.workspace_id, c.datasource_id, c.messages, "
                "       d.name AS datasource_name "
                "FROM adh_conversations c "
                "LEFT JOIN adh_datasources d ON d.id = c.datasource_id "
                "WHERE c.id = %s", (int(conversation_id or 0),))
            row = cur.fetchone()
    if not row:
        return {"found": False}
    out = {"found": True, "owner_user_id": int(row.get("user_id") or 0),
           "workspace_id": int(row.get("workspace_id") or 0),
           "datasource_id": int(row.get("datasource_id") or 0),
           "datasource_name": str(row.get("datasource_name") or ""), "message": None}
    try:
        messages = json.loads(row.get("messages") or "[]")
    except ValueError:
        return out
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("task_kind") == _TASK_KIND:
            out["message"] = m
            break
    return out


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

    # 会话转录由后台执行器 `_drive_generate_task` 统一回写（单一写者，不重复落消息）
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
        # 正文与工具轨迹随终态带回，由后台执行器统一回写会话转录
        "reply": reply,
        "tool_calls": tool_calls,
    })


# ═══════════════════════════════════════════════════════════════════
# 后台静默执行：任务脱离 HTTP 请求生命周期，进度落 AS-BOT 会话
# ═══════════════════════════════════════════════════════════════════
# 旧行为：生成由 SSE 直连前端，客户端一断连 `request.is_disconnected()` 就让派发
# 提前 return——用户体感就是“离开页面任务就停了”。现在端点只做鉴权与派发，
# 执行由 web 进程内的后台 asyncio 任务持有，AS-BOT 侧边栏开关不影响它；
# 进度/终态写进会话转录（事实源），状态端点只读不拿进程内状态当唯一依据。
# 重启语义（拍板：不转 Celery）：注册表是进程内的，web 重启后 running 记录失去宿主
# → status() 显式判为 interrupted 并回写会话，不假报成功（no-silent-degradation）。

_TASK_KIND = "ontology_generate"
_PROGRESS_CAP = 200            # 单任务保留的进度行数上限（会话转录同步只带最后 40 行）
_PERSIST_EVERY_EVENTS = 4      # 进度落库节流：每 N 个事件写一次会话
_KEEP_FINISHED = 20            # 已结束任务在进程内保留条数（转录才是事实源）

_GENERATE_RUNS: dict = {}      # conversation_id -> run state


class _DetachedRequest:
    """后台执行的伪请求：ChatService 只用 `is_disconnected()` 做断连收尾，恒 False。

    只实现被消费的一个方法——不伪造 URL/headers/body，避免把“还有客户端”的错觉
    带进下游（护栏 §2：身份只由服务端已解析的 user dict 传递）。
    """

    async def is_disconnected(self) -> bool:
        return False


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def _public_run(run: dict) -> dict:
    """任务状态对外视图（不带 task_message 全文/内部对象，护栏 §7）。"""
    return {
        "conversation_id": run["conversation_id"],
        "workspace_id": run["workspace_id"],
        "datasource_id": run["datasource_id"],
        "datasource_name": run.get("datasource_name") or "",
        "status": run["status"],
        "progress": [{"stage": p["stage"], "detail": p["detail"]} for p in run["progress"]],
        "model_id": run.get("model_id"),
        "object_count": int(run.get("object_count") or 0),
        "warnings": list(run.get("warnings") or []),
        "error": run.get("error") or "",
        "started_at": run.get("started_at") or "",
        "finished_at": run.get("finished_at") or "",
    }


def _prune_runs() -> None:
    finished = [cid for cid, r in _GENERATE_RUNS.items() if r["status"] != "running"]
    for cid in sorted(finished)[:max(0, len(finished) - _KEEP_FINISHED)]:
        _GENERATE_RUNS.pop(cid, None)


def _record_event(run: dict, event: str, data: dict) -> None:
    """事件流 → 任务状态（进度行/工具结果里的草案落库结果/终态）。"""
    if event == "progress":
        detail = str(data.get("detail") or data.get("stage") or "").strip()
        if detail:
            run["progress"].append({"stage": str(data.get("stage") or ""), "detail": detail,
                                   "at": _now_ms()})
            if len(run["progress"]) > _PROGRESS_CAP:
                del run["progress"][:len(run["progress"]) - _PROGRESS_CAP]
    elif event == "done":
        run["reply"] = str(data.get("reply") or run.get("reply") or "")
        run["tool_calls"] = list(data.get("tool_calls") or run.get("tool_calls") or [])
        run["model_id"] = data.get("model_id") or None
        run["object_count"] = int(data.get("object_count") or 0)
        run["warnings"] = list(data.get("warnings") or [])
        if data.get("completed") and run["model_id"]:
            run["status"] = "done"
        else:
            # 任务结束但未提交草案：显式 error，不假报成功
            run["status"] = "error"
            run["error"] = str(data.get("error") or "任务已结束，但未提交草案")
    elif event == "error":
        run["status"] = "error"
        run["error"] = str(data.get("message") or "生成任务执行失败")


def _finish_run(run: dict, status: str, error: str = "") -> None:
    if run["status"] == "running":
        run["status"] = status
    if error and not run.get("error"):
        run["error"] = error
    run["finished_at"] = run["finished_at"] or _now_ms()


async def _drive_generate_task(run: dict) -> None:
    """后台执行主体：消费派发事件流 → 更新任务态 → 节流回写会话。"""
    events = _generate_task_events(
        _DetachedRequest(),  # type: ignore[arg-type]
        task_message=run["task_message"], conversation_id=run["conversation_id"],
        datasource_id=run["datasource_id"], workspace_id=run["workspace_id"],
        user=run["user"], as_bot_key=run["as_bot_key"], task_binding=run["task_binding"])
    since_persist = 0
    try:
        async for event, data in events:
            _record_event(run, event, data)
            since_persist += 1
            if since_persist >= _PERSIST_EVERY_EVENTS:
                since_persist = 0
                await asyncio.to_thread(_write_task_message, run)
    except asyncio.CancelledError:
        _finish_run(run, "cancelled", "任务已取消")
        _write_task_message(run)
        raise
    except Exception as e:  # noqa: BLE001 — 后台任务不得抛出无人接，必须落终态并显式记错
        logger.error("生成本体任务 %s 异常终止: %s", run["conversation_id"], e, exc_info=True)
        _finish_run(run, "error", f"生成任务异常终止: {e}")
    finally:
        if run["status"] == "running":
            run["status"] = "error"
            run["error"] = run.get("error") or "生成任务未返回结果即结束"
        run["finished_at"] = run["finished_at"] or _now_ms()
        _write_task_message(run)
        _prune_runs()


def _start_generate_task(run: dict) -> dict:
    run["started_at"] = _now_ms()
    run["task"] = asyncio.create_task(_drive_generate_task(run))
    _GENERATE_RUNS[int(run["conversation_id"])] = run
    return _public_run(run)


async def wait_generate_task(conversation_id: int, timeout: float | None = None) -> str:
    """等任务跑完并返回终态（供内部编排与回归测试；生产请求不阻塞在此）。

    被取消的任务不得把 CancelledError 泄给等待方——取消是任务状态，不是调用失败。
    """
    run = _GENERATE_RUNS.get(int(conversation_id or 0))
    if not run:
        return "none"
    task = run.get("task")
    if task is not None and not task.done():
        try:
            if timeout:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            else:
                await asyncio.shield(task)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    return run["status"]


def find_running_task(*, datasource_id: int = 0, user_id: int = 0) -> dict:
    """是否已有同一目标的生成任务在跑（并发会互盖草案，宁拒勿错）。"""
    for run in _GENERATE_RUNS.values():
        if run["status"] != "running":
            continue
        if datasource_id and int(run["datasource_id"]) != int(datasource_id):
            continue
        if user_id and int(run["user_id"]) != int(user_id):
            continue
        return run
    return {}


def generate_task_status(conversation_id: int, user_id: int) -> dict:
    """任务状态（只读）：注册表优先，无则读会话持久态。

    持久态仍写 running 但本进程已无该任务 → 进程重启导致中断，显式标为
    interrupted 并回写会话（不把“没跑完”说成“成功”）。
    """
    run = _GENERATE_RUNS.get(int(conversation_id or 0))
    if run:
        if int(run["user_id"]) != int(user_id):
            raise HTTPException(status_code=403, detail="无权查看该生成任务")
        return _public_run(run)

    persisted = _load_task_message(int(conversation_id or 0))
    if not persisted.get("found"):
        raise HTTPException(status_code=404, detail="生成任务不存在，请在 AS-BOT 会话历史中查看执行过程")
    if int(persisted.get("owner_user_id") or 0) != int(user_id):
        raise HTTPException(status_code=403, detail="无权查看该生成任务")
    msg = persisted.get("message") or {}
    state = str(msg.get("task_state") or "")
    if state == "running":
        orphan = {
            "conversation_id": int(conversation_id), "user_id": int(user_id),
            "workspace_id": int(persisted.get("workspace_id") or 0),
            "datasource_id": int(persisted.get("datasource_id") or 0),
            "datasource_name": persisted.get("datasource_name") or "",
            "status": "interrupted",
            "error": "服务已重启，本次生成任务中断（草案未保证完整），请重新发起生成",
            "reply": "", "tool_calls": [], "progress": [], "warnings": [],
            "model_id": msg.get("model_id"), "object_count": 0,
            "started_at": "", "finished_at": _now_ms(), "task_message": msg.get("question") or "",
        }
        _write_task_message(orphan)
        return _public_run(orphan)
    if not msg:
        raise HTTPException(status_code=404, detail="会话中未找到生成任务记录")
    return _public_run({
        "conversation_id": int(conversation_id), "user_id": int(user_id),
        "workspace_id": int(persisted.get("workspace_id") or 0),
        "datasource_id": int(persisted.get("datasource_id") or 0),
        "datasource_name": persisted.get("datasource_name") or "",
        "status": state or "error", "progress": msg.get("progressStages") or [],
        "model_id": msg.get("model_id"), "object_count": 0,
        "warnings": [], "error": msg.get("error") or "",
        "started_at": "", "finished_at": "",
    })


def list_generate_tasks(user_id: int, active_only: bool = True) -> list:
    """本用户进程内任务清单（页面重进时挂回进度 / AS-BOT 侧边栏“执行中”徽标）。"""
    out = [_public_run(r) for r in _GENERATE_RUNS.values() if int(r["user_id"]) == int(user_id)]
    if active_only:
        out = [r for r in out if r["status"] == "running"]
    return sorted(out, key=lambda r: r["started_at"] or 0, reverse=True)


@router.get("/generate/tasks")
def list_generate_tasks_endpoint(active_only: bool = Query(True), user: dict = Depends(get_current_user)):
    """『生成本体模型』后台任务清单（仅本用户；已结束任务在进程内留有限条数）。"""
    return {"items": list_generate_tasks(int(user.get("user_id") or 0), active_only)}


@router.get("/generate/tasks/{conversation_id}")
def get_generate_task(conversation_id: int, user: dict = Depends(get_current_user)):
    """任务进度/终态（前端轮询；AS-BOT 侧边栏与建模页同源）。"""
    return generate_task_status(conversation_id, int(user.get("user_id") or 0))


@router.post("/generate/tasks/{conversation_id}/cancel")
async def cancel_generate_task(conversation_id: int, user: dict = Depends(get_current_user)):
    """取消正在后台跑的生成任务（写动作，同样经 ontology:generate 权限码把关）。"""
    from backend.core.perm_link import require_write_perm

    user_id = int(user.get("user_id") or 0)
    run = _GENERATE_RUNS.get(int(conversation_id or 0))
    if not run:
        raise HTTPException(status_code=404,
                            detail="任务已结束或不在本实例执行中，无需取消")
    if int(run["user_id"]) != user_id:
        raise HTTPException(status_code=403, detail="无权取消该生成任务")
    try:
        require_write_perm(user_id, int(run["workspace_id"]), "ontology:generate", "取消本体生成任务")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    task = run.get("task")
    if task and not task.done():
        task.cancel()
    _finish_run(run, "cancelled", "任务已取消")
    await asyncio.to_thread(_write_task_message, run)
    return _public_run(run)


@router.post("/generate")
async def generate_ontology(request: Request, req: dict, user: dict = Depends(get_current_user)):
    """派发『生成本体模型』后台任务（立即返回，不随前端断连中止）。

    链路：服务端鉴权/建 AS-BOT 会话 → 进程序后台任务持有执行（归纳由会话内
    qoder agent 完成，工具不调 LLM）→ 进度/终态写会话转录。前端拿 conversation_id
    轮询 /generate/tasks/{id}，并直接打开 AS-BOT 侧边栏看实时步骤；
    关面板/离开页面都不影响执行。

    请求体：datasource_id 必填；kb_ids 可选（辅助生成的知识库，服务端校验后
    经 task_binding 注入工具侧检索通道，不进 LLM 视野，护栏 §7）。
    身份一律服务端注入（不接受请求体 created_by/user_id）。
    """
    from backend.core.role_service import role_service
    from backend.core.perm_link import require_write_perm
    from backend.common.auth import authorize_workspace, resolve_user_default_workspace_id

    datasource_id = int(req.get("datasource_id") or 0)
    if not datasource_id:
        raise HTTPException(status_code=400, detail="datasource_id 必填")

    user_id = int(user.get("user_id") or 0)
    workspace_id = resolve_user_default_workspace_id(user_id) or 0
    if not workspace_id:
        raise HTTPException(status_code=422, detail="未找到您的默认工作空间，请先创建工作空间后再试")
    authorize_workspace(user, workspace_id)

    # 权限把关(fail-closed，拒绝原因可解释)
    try:
        require_write_perm(user_id, workspace_id, "ontology:generate", "生成本体草案")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    # 目标数据源必须 ∈ 用户角色授权集(fail-closed：空授权集不放行)
    allowed = set(role_service.get_user_allowed_datasources(user_id, workspace_id))
    if datasource_id not in allowed:
        raise HTTPException(status_code=403, detail="当前用户无权使用该数据源，请选择已授权数据源")

    # 同一数据源已有生成任务在跑 → 显式拒绝（两批 append 互盖会把草案弄脏，宁阻断勿重复）
    clash = find_running_task(datasource_id=datasource_id)
    if clash:
        raise HTTPException(
            status_code=409,
            detail=(f"数据源「{clash.get('datasource_name') or datasource_id}」已有生成任务正在执行中"
                    f"（会话 {clash['conversation_id']}），请在 AS-BOT 侧边栏查看或先取消"))

    # 辅助生成知识库（可选）：服务端校验归属/启用（fail-closed，越界拒），
    # kb_ids 经任务绑定注入工具侧检索通道，不进 LLM 视野（护栏 §7）。
    raw_kb_ids = req.get("kb_ids") or []
    if not isinstance(raw_kb_ids, list):
        raise HTTPException(status_code=400, detail="kb_ids 需为数组")
    kb_ids = []
    for x in raw_kb_ids:
        try:
            kb_ids.append(int(x))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="kb_ids 需为知识库 id 数组")
    if kb_ids:
        from backend.modules.mind.execution.resource_guard import validate_knowledge_binding
        try:
            validate_knowledge_binding(kb_ids)
        except PermissionError as e:
            raise HTTPException(status_code=400, detail=f"知识库校验失败: {e}")

    ds_name, table_count = _datasource_brief(datasource_id)
    _assert_dispatchable(workspace_id)
    as_bot_key = _resolve_task_as_bot(user, workspace_id)

    task_message = _build_task_message(ds_name, table_count, len(kb_ids))
    conversation_id = _create_task_conversation(
        user, workspace_id, datasource_id, as_bot_key, ds_name, task_message)
    # 任务绑定标记：仅作任务派发溯源 + kb_ids 服务端传递（角色权限模型下不作工具把关条件，
    # 把关见 resource_guard：目标源 ∈ 角色授权集 + 权限码）
    task_binding = {"kind": "ontology_generate", "datasource_id": datasource_id, "kb_ids": kb_ids}

    run = {
        "conversation_id": int(conversation_id), "user_id": user_id,
        "workspace_id": int(workspace_id), "datasource_id": int(datasource_id),
        "datasource_name": ds_name, "status": "running", "progress": [],
        "task_message": task_message, "as_bot_key": as_bot_key,
        "task_binding": task_binding, "user": dict(user),
        "reply": "", "tool_calls": [], "model_id": None, "object_count": 0,
        "warnings": [], "error": "", "started_at": "", "finished_at": "", "task": None,
    }
    return _start_generate_task(run)


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
    items = ontology_service.list_models(
        datasource_id, include_archived=include_archived,
        allowed_datasource_ids=allowed)
    # 部署摘要（契约徽标依据，部署绑定派生）：≥2 站 → 前端展示「契约模型 · N 站部署」
    dep = _deployment_summary()
    for it in items or []:
        info = dep.get(int(it.get("id") or 0) if isinstance(it, dict) else 0, {})
        if isinstance(it, dict):
            it["deployment_count"] = info.get("deployment_count", 0)
            it["deployments"] = info.get("deployments", [])
    return {"items": items}


def _deployment_summary() -> dict:
    """model_id → {deployment_count, deployments:[{site,datasource}]}。

    事实源优先级：模型级 `deploy_sites`（新口径，一份契约 × N 站）> active 绑定行
    的 distinct 源（源本体/未迁移存量的兼容口径）。两者都不能缺失时回退裸 id
    （ui-resource-display §1：资源不可见时显示可读写法，不拿 id 充数）。
    """
    from backend.common.db.metadata_db import get_metadata_conn

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT site_code, display_name FROM adh_sites")
            code_names = {str(r["site_code"]): str(r["display_name"]) for r in (cur.fetchall() or [])}
            cur.execute("SELECT id, kind, deploy_sites FROM adh_ontology_models")
            model_rows = cur.fetchall() or []
            cur.execute(
                "SELECT model_id, datasource_name FROM adh_ontology_bindings "
                "WHERE status = 'active' AND datasource_name <> '' "
                "GROUP BY model_id, datasource_name")
            legacy_rows = cur.fetchall() or []

    out: dict = {}
    for m in model_rows:
        raw = m.get("deploy_sites")
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw or "[]")
            except (ValueError, TypeError):
                raw = []
        deps = [
            {"site": code_names.get(str(s.get("site_code")), str(s.get("site_code"))),
             "datasource": str(s.get("datasource") or "—")}
            for s in (raw or []) if isinstance(s, dict) and s.get("site_code")
        ]
        if deps:
            out[int(m["id"])] = {"deployment_count": len(deps), "deployments": deps}
    # 未配置 deploy_sites 的模型（源本体/存量）：仍按绑定行 distinct 源呈现
    for r in legacy_rows:
        mid = int(r["model_id"])
        if mid in out:
            continue
        ds = str(r["datasource_name"])
        entry = out.setdefault(mid, {"deployment_count": 0, "deployments": []})
        entry["deployments"].append({"site": ds, "datasource": ds})
    for entry in out.values():
        entry["deployments"].sort(key=lambda d: (d["site"], d["datasource"]))
        entry["deployment_count"] = len(entry["deployments"])
    return out


@router.get("/models/{model_id}/sites")
def get_model_sites(model_id: int, user: dict = Depends(get_current_user)):
    """业务本体适用站点 + 对象契约清单（前端「适用站点」页）。

    站点差异不再逐对象逐站登记（旧口径 27 对象 × 5 站 = 135 行机械复制），而是
    模型级声明一次、全对象共享；显示一律用站点/数据源业务名（ui-resource-display）。
    """
    model = ontology_service.get_model(model_id)
    if not model or not ontology_service.can_view_model(model, _allowed_source_ids(user)):
        raise HTTPException(status_code=404, detail="模型不存在")
    from backend.common.db.metadata_db import get_metadata_conn
    from backend.core.site_service import model_deploy_sites

    selected = {s["site_code"]: s["datasource"] for s in model_deploy_sites(model_id)}
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT site_code, display_name, is_aggregate FROM adh_sites "
                "WHERE is_active = 1 ORDER BY sort_order, site_code")
            site_rows = cur.fetchall() or []
            cur.execute(
                "SELECT name, site_code FROM adh_datasources "
                "WHERE site_code IS NOT NULL AND site_code <> '' ORDER BY name")
            by_site: dict = {}
            for r in cur.fetchall() or []:
                by_site.setdefault(str(r["site_code"]), []).append(str(r["name"]))
            cur.execute(
                "SELECT object_key, datasource_name, physical_table, bind_kind, column_map, "
                "sync_state, product_ref FROM adh_ontology_bindings "
                "WHERE model_id = %s AND status = 'active' ORDER BY object_key, id", (model_id,))
            bind_rows = cur.fetchall() or []
            cur.execute(
                "SELECT object_key, display_name FROM adh_ontology_objects "
                "WHERE model_id = %s AND is_active = 1", (model_id,))
            names = {str(r["object_key"]): str(r["display_name"] or r["object_key"])
                     for r in (cur.fetchall() or [])}

    sites = []
    for r in site_rows:
        code = str(r["site_code"])
        if int(r.get("is_aggregate") or 0):
            continue  # 聚合态不挂数据源，不作为部署站点候选
        options = by_site.get(code, [])
        sites.append({
            "site_code": code,
            "display_name": str(r["display_name"]),
            "datasource": selected.get(code, ""),
            "selected": code in selected,
            "datasource_options": options,
        })
    objects = []
    for r in bind_rows:
        key = str(r["object_key"])
        raw_cm = r.get("column_map")
        if isinstance(raw_cm, (bytes, bytearray)):
            raw_cm = raw_cm.decode("utf-8", errors="replace")
        if isinstance(raw_cm, str):
            try:
                raw_cm = json.loads(raw_cm or "{}")
            except (ValueError, TypeError):
                raw_cm = {}
        objects.append({
            "object_key": key,
            "object_name": names.get(key, key),
            "datasource": str(r.get("datasource_name") or ""),
            "physical_table": str(r.get("physical_table") or ""),
            "bind_kind": str(r.get("bind_kind") or "primary"),
            "column_count": len(raw_cm or {}),
            "sync_state": str(r.get("sync_state") or ""),
            "product_ref": str(r.get("product_ref") or ""),
        })
    return {"kind": str(model.get("kind") or ""), "sites": sites,
            "selected": [{"site_code": k, "datasource": v} for k, v in selected.items()],
            "contract_objects": objects}


@router.put("/models/{model_id}/sites")
def update_model_sites(model_id: int, req: dict, user: dict = Depends(get_current_user)):
    """保存业务本体适用站点（接口权限由 API 中间件按 ontology:save 码把关）。

    校验全部 fail-loud：站点必须存在于启用清单且非聚合态；数据源必须存在且其
    归属站点与声明站点一致（不一致直接 422，不静默纠正）。
    源本体不承载站点声明（它是单源物理投影，见 showsSiteScope 同口径）。
    """
    model = ontology_service.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")
    if str(model.get("kind") or "") == "source":
        raise HTTPException(status_code=400, detail="源本体不适用站点声明（它是单个数据源的物理投影）")
    from backend.core.site_service import get_site, list_sites, set_model_deploy_sites

    raw_sites = req.get("sites")
    if not isinstance(raw_sites, list):
        raise HTTPException(status_code=400, detail="sites 必须是数组")
    known_codes = {s["site_code"] for s in list_sites() if not s["is_aggregate"]}
    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in raw_sites:
        code = str((item or {}).get("site_code") or "").strip()
        ds = str((item or {}).get("datasource") or "").strip()
        if not code:
            continue
        if code not in known_codes:
            name = (get_site(code) or {}).get("display_name") or code
            raise HTTPException(
                status_code=422,
                detail=f"站点「{name}」不可用（不存在/已停用/是聚合态）；可选："
                       + "、".join(sorted(known_codes)))
        if code in seen:
            raise HTTPException(status_code=422, detail=f"站点 {code} 重复登记")
        seen.add(code)
        if not ds:
            raise HTTPException(status_code=422, detail=f"站点「{code}」未选择路由数据源")
        from backend.common.db import execute_query
        row = execute_query("SELECT site_code FROM adh_datasources WHERE name = %s", (ds,),
                            fetchone=True)
        if not row:
            raise HTTPException(status_code=422, detail=f"数据源「{ds}」不存在")
        if str(row.get("site_code") or "") != code:
            raise HTTPException(
                status_code=422,
                detail=f"数据源「{ds}」归属站点与声明站点不一致，请先在数据源管理里校正归属")
        cleaned.append({"site_code": code, "datasource": ds})
    set_model_deploy_sites(model_id, cleaned)
    return {"success": True, "sites": cleaned}


@router.get("/models/{model_id}")
def get_model(model_id: int, user: dict = Depends(get_current_user)):
    model = ontology_service.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")
    if not ontology_service.can_view_model(model, _allowed_source_ids(user)):
        # 不泄露存在性：不可见即 404（不回 403 提示“存在但无权”）
        raise HTTPException(status_code=404, detail="模型不存在")
    return model


@router.get("/models/{model_id}/view")
def view_model(
    model_id: int,
    fmt: str = Query("internal", alias="format", description="internal|palantir"),
    as_: str = Query("json", alias="as", description="json|yaml|md"),
    user: dict = Depends(get_current_user),
):
    """本体结构化查看器（只读投影）：format=internal|palantir × as=json|yaml|md。

    读当前模型 json_content → hydrate_doc（按 bound_object_key 注入字典口径/维度）
    → 按 format 选 canonical/to_palantir → 按 as 选 json/yaml/md。只读，
    不改 save_draft/activate 路径，存储的 canonical json_content 保持不变。
    """
    model = ontology_service.get_model(model_id)
    if not model or not ontology_service.can_view_model(model, _allowed_source_ids(user)):
        raise HTTPException(status_code=404, detail="模型不存在")
    if fmt not in ("internal", "palantir"):
        raise HTTPException(status_code=400, detail="format 仅支持 internal|palantir")
    if as_ not in ("json", "yaml", "md"):
        raise HTTPException(status_code=400, detail="as 仅支持 json|yaml|md")
    try:
        doc = json.loads(model.get("json_content") or "{}")
    except (json.JSONDecodeError, TypeError) as e:
        raise HTTPException(status_code=400, detail="模型内容损坏，无法生成视图") from e
    hydrated = ontology_service.hydrate_doc(doc, int(model.get("datasource_id") or 0))
    if fmt == "palantir":
        pal = ontology_service.to_palantir(hydrated)
        if as_ == "yaml":
            text = ontology_service.to_palantir_yaml(hydrated)
        elif as_ == "md":
            text = "# Palantir 视图\n\n```yaml\n" + ontology_service.to_palantir_yaml(hydrated) + "\n```\n"
        else:
            text = json.dumps(pal, ensure_ascii=False, indent=2)
    else:
        if as_ == "yaml":
            text = ontology_service.to_yaml(hydrated)
        elif as_ == "md":
            text = ontology_service.to_md(hydrated)
        else:
            text = json.dumps(hydrated, ensure_ascii=False, indent=2)
    return {"format": fmt, "as": as_, "text": text}


@router.get("/models/{model_id}/sync-doc")
def get_model_sync_doc(model_id: int, user: dict = Depends(get_current_user)):
    """同步文档（所见即所推）：「同步知识库」全量推送的那份 MD（脱敏白名单 + 版本戳头）。

    编辑器「MD 预览」渲染此文档，保证所见即所推；内部 md_content（含物理信息）
    不上云（脱敏红线，见 ontology-modeling §6）。
    """
    model = ontology_service.get_model(model_id)
    if not model or not ontology_service.can_view_model(model, _allowed_source_ids(user)):
        raise HTTPException(status_code=404, detail="模型不存在")
    from ..services import ontology_kb_sync
    return {"markdown": ontology_kb_sync._render_cloud_doc(model)}


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


@router.put("/models/{model_id}/draft")
def save_model_draft(model_id: int, req: dict, user: dict = Depends(get_current_user)):
    """只存草案（两段式第一段）：不碰生效内容、不级联，等 publish 才生效。

    与 PUT /models/{model_id} 的区别：后者对 active 模型会就地改内容并立即级联；
    本端点供 AS-BOT 通道与“先草稿后发布”流程使用。
    """
    json_content = req.get("json_content")
    if not json_content:
        raise HTTPException(status_code=400, detail="json_content 必填")
    try:
        return ontology_service.save_draft_only(
            model_id, json_content, name=req.get("name"),
            # 身份只信服务端解析（安全护栏 §2），不从请求体取
            created_by=str(user.get("username") or user.get("name") or ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/models/{model_id}/draft")
def get_model_draft(model_id: int):
    """草案状态（有无待发布、谁改的）；不回草案正文，避免随列表接口外泄。"""
    try:
        d = ontology_service.get_draft(model_id)
        d.pop("draft", None)
        return d
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/models/{model_id}/publish")
def publish_model_draft(model_id: int):
    """发布草案（两段式第二段）：生效 + 级联（重展开对象/重建图谱/同步知识库）。

    需 ontology:publish 权限（fail-closed）。AS-BOT 通道发布前必须先用 ask_user 向用户确认。
    """
    from backend.core.perm_link import require_write_perm
    require_write_perm("ontology:publish", "发布本体草案")
    try:
        result = ontology_service.publish_draft(model_id)
        return {"success": True, "object_count": result.get("object_count")}
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
def sync_model_kb(model_id: int, kb_id: Optional[int] = Query(None)):
    """手动把生效模型的脱敏语义文档同步到目标知识库（结果落水位线）。

    kb_id 为空时按模型 kind 默认路由；kb_id 非空时为一次性定向同步到指定库（含源本体，
    属用户主动的手动例外）。
    同步失败显式报错并写服务端日志，不静默吞成成功；成功/失败均落
    adh_ontology_kb_sync_state 供任务监控/徽章展示。
    """
    from ..services import ontology_kb_sync
    # 直连调用（非 FastAPI 注入）时 kb_id 可能是 Query 占位对象，归一为 None/正整数
    kb_id_val = int(kb_id) if str(kb_id or "").isdigit() and int(kb_id) > 0 else None
    try:
        result = ontology_kb_sync.sync_model_to_qmind(model_id, kb_id=kb_id_val)
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


@router.get("/kb-sync-options")
def kb_sync_options():
    """「同步知识库」可选目标库清单（全部 active qmind 库，供关系图谱页选库下拉）。"""
    from ..services import ontology_kb_sync

    return {"items": ontology_kb_sync.list_syncable_kbs()}


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
