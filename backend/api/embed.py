"""Embed API — third-party integration endpoints with API Key auth."""
from __future__ import annotations

import json
import logging
import math
import re
import time as _time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse

from backend.common.embed_auth import (
    create_embed_token, decode_embed_token, should_refresh_token,
    find_app_by_api_key, update_last_used,
    list_applications, create_application, update_application, rotate_api_key,
    delete_application, get_app_by_id, log_embed_action, list_embed_logs,
)
from backend.models.schemas import (
    EmbedVerifyRequest, EmbedVerifyResponse, EmbedRefreshRequest,
    ApplicationCreate, ApplicationUpdate, ApplicationResponse,
    ApplicationListResponse, ApplicationKeyResponse,
    EmbedLogItem, EmbedLogResponse,
    EmbedChatRequest, EmbedDashboardListResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()
security = HTTPBearer()


def _get_embed_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Validate embed token and return payload."""
    token = credentials.credentials
    payload = decode_embed_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired embed token")
    return payload


def _sanitize_for_json(obj):
    """Make object JSON-serializable."""
    from decimal import Decimal
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(i) for i in obj]
    return obj


def _sse_event(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(_sanitize_for_json(data), ensure_ascii=False)}\n\n"


def _require_admin(credentials: HTTPAuthorizationCredentials) -> dict:
    """Validate admin JWT token."""
    from jose import JWTError, jwt as jose_jwt
    from backend.common.config import ADH_SECRET_KEY
    token = credentials.credentials
    try:
        payload = jose_jwt.decode(token, ADH_SECRET_KEY, algorithms=["HS256"])
        if payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin only")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ── Auth ───────────────────────────────────────────────────────────

@router.post("/auth/verify", response_model=EmbedVerifyResponse)
async def verify_api_key(req: EmbedVerifyRequest, request: Request):
    app = find_app_by_api_key(req.api_key)
    if not app:
        raise HTTPException(status_code=401, detail="API Key无效或已禁用")

    token, expires_at = create_embed_token(req.user_id, app["id"])
    update_last_used(app["id"])

    ip = request.client.host if request.client else ""
    log_embed_action(
        app["id"], req.user_id, req.user_name or "", "verify",
        detail="嵌入认证成功", ip_address=ip,
    )

    return EmbedVerifyResponse(embed_token=token, expires_at=expires_at, app_id=app["id"])


@router.post("/auth/refresh", response_model=EmbedVerifyResponse)
async def refresh_token(req: EmbedRefreshRequest, request: Request):
    payload = decode_embed_token(req.embed_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token无效或已过期")

    app_id = payload.get("app_id")
    user_id = payload.get("sub", "")

    app = get_app_by_id(app_id)
    if not app or app["status"] != "active":
        raise HTTPException(status_code=401, detail="应用已禁用")

    new_token, expires_at = create_embed_token(user_id, app_id)
    return EmbedVerifyResponse(embed_token=new_token, expires_at=expires_at, app_id=app_id)


# ── Chat ───────────────────────────────────────────────────────────

@router.get("/chat/conversations")
async def list_conversations(user: dict = Depends(_get_embed_user)):
    import pymysql
    from backend.common.config import DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE

    user_id = user["sub"]
    try:
        conn = pymysql.connect(
            host=DORIS_HOST, port=DORIS_PORT, user=DORIS_USER,
            password=DORIS_PASSWORD, database=METADATA_DB_DATABASE,
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, datasource_id, created_at, updated_at "
                "FROM adh_conversations "
                "WHERE embed_user_id = %s AND user_id = 0 "
                "ORDER BY updated_at DESC",
                (user_id,),
            )
            items = cur.fetchall()
        conn.close()
        return {"items": items, "total": len(items)}
    except Exception as e:
        logger.error("Failed to list embed conversations: %s", e)
        raise HTTPException(status_code=500, detail="查询对话列表失败")


@router.post("/chat/conversations")
async def create_conversation(request: Request, user: dict = Depends(_get_embed_user)):
    import pymysql
    from backend.common.config import DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE

    body = await request.json()
    datasource_id = body.get("datasource_id", 0)
    user_id = user["sub"]
    conv_id = int(_time.time() * 1000)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn = pymysql.connect(
            host=DORIS_HOST, port=DORIS_PORT, user=DORIS_USER,
            password=DORIS_PASSWORD, database=METADATA_DB_DATABASE,
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_conversations "
                "(id, title, user_id, embed_user_id, datasource_id, messages, created_at, updated_at) "
                "VALUES (%s, '新对话', 0, %s, %s, '[]', %s, %s)",
                (conv_id, user_id, datasource_id, now, now),
            )
        conn.commit()
        conn.close()
        return {"id": conv_id, "title": "新对话", "datasource_id": datasource_id}
    except Exception as e:
        logger.error("Failed to create embed conversation: %s", e)
        raise HTTPException(status_code=500, detail="创建对话失败")


@router.get("/chat/conversations/{conv_id}")
async def get_conversation(conv_id: int, user: dict = Depends(_get_embed_user)):
    import pymysql
    from backend.common.config import DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE

    user_id = user["sub"]
    try:
        conn = pymysql.connect(
            host=DORIS_HOST, port=DORIS_PORT, user=DORIS_USER,
            password=DORIS_PASSWORD, database=METADATA_DB_DATABASE,
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, datasource_id, messages, created_at, updated_at "
                "FROM adh_conversations WHERE id = %s AND embed_user_id = %s",
                (conv_id, user_id),
            )
            conv = cur.fetchone()
        conn.close()

        if not conv:
            raise HTTPException(status_code=404, detail="对话不存在")
        if isinstance(conv.get("messages"), str):
            conv["messages"] = json.loads(conv["messages"])
        return conv
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get embed conversation: %s", e)
        raise HTTPException(status_code=500, detail="查询对话失败")


@router.delete("/chat/conversations/{conv_id}")
async def delete_conversation(conv_id: int, user: dict = Depends(_get_embed_user)):
    import pymysql
    from backend.common.config import DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE

    user_id = user["sub"]
    try:
        conn = pymysql.connect(
            host=DORIS_HOST, port=DORIS_PORT, user=DORIS_USER,
            password=DORIS_PASSWORD, database=METADATA_DB_DATABASE,
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        )
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM adh_conversations WHERE id = %s AND embed_user_id = %s",
                (conv_id, user_id),
            )
        conn.commit()
        conn.close()
        return {"success": True}
    except Exception as e:
        logger.error("Failed to delete embed conversation: %s", e)
        raise HTTPException(status_code=500, detail="删除对话失败")


@router.post("/chat/send")
async def send_message(request: Request, user: dict = Depends(_get_embed_user)):
    """Send a message and stream back the response (SSE)."""
    from backend.nl2sql.intent.intent_classifier import _quick_classify
    from backend.rag.table_selector import select_tables
    from backend.rag.rag_retriever import retrieve_all
    from backend.nl2sql.prompt.prompt_builder import build_nl2sql_prompt
    from backend.common.llm.llm_client import generate_sql_stream
    from backend.nl2sql.sql.sql_validator import validate_and_fix
    from backend.nl2sql.sql.query_executor import execute_query
    from backend.api.chat import _interpret_results_stream
    import pymysql
    from backend.common.config import DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE

    body = await request.json()
    question = body.get("question", "").strip()
    history = body.get("history", [])
    datasource_id = body.get("datasource_id", 0)
    conversation_id = body.get("conversation_id")

    app_id = user["app_id"]
    user_id = user["sub"]

    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    # Check enable_chat permission
    app = get_app_by_id(app_id)
    if not app or not app.get("enable_chat"):
        raise HTTPException(status_code=403, detail="该应用未启用Chat分析功能")

    # Check table permissions
    allowed_tables = None
    if app and app.get("allowed_tables"):
        try:
            allowed_tables = json.loads(app["allowed_tables"])
        except Exception:
            pass

    # Log action
    ip = request.client.host if request.client else ""
    log_embed_action(app_id, user_id, "", "chat_send", detail=question[:200], ip_address=ip)

    def event_generator():
        try:
            # Step 1: Classify intent
            quick = _quick_classify(question)
            intent = quick.get("intent", "query") if quick else "query"
            yield _sse_event("progress", {"stage": "intent", "message": "正在分析意图..."})

            if intent == "greeting":
                yield _sse_event("done", {
                    "reply": "你好！我是ChatBI数据分析助手，请问有什么可以帮您的？",
                    "intent": "greeting",
                })
                return

            # Step 2: Table selection
            yield _sse_event("progress", {"stage": "rag", "message": "正在检索元数据..."})
            selected_tables = select_tables(question, top_k=5, datasource_id=datasource_id)

            # Step 3: RAG retrieval
            rag_results = retrieve_all(question, selected_tables=selected_tables, datasource_id=datasource_id)
            rag_source = rag_results.get("rag_source", "keyword_selected")

            # Step 4: Build prompt and generate SQL
            yield _sse_event("progress", {"stage": "llm", "message": "正在生成 SQL..."})
            messages = build_nl2sql_prompt(
                question=question,
                table_info=rag_results.get("table_info", []),
                column_metadata=rag_results.get("column_metadata", []),
                sql_templates=rag_results.get("sql_templates", []),
                business_terms=rag_results.get("business_terms", []),
                table_relations=rag_results.get("table_relations", []),
                conversation_history=history,
            )

            # Stream LLM generation — generate_sql_stream yields (event_type, data) tuples
            full_text = ""
            thinking_text = ""
            for event_type, data in generate_sql_stream(messages):
                if event_type == "thinking":
                    thinking_text += data
                    yield _sse_event("thinking", {"text": data})
                elif event_type == "token":
                    full_text += data
                    yield _sse_event("token", {"text": data})
                elif event_type == "done":
                    pass  # tokens metadata, not needed here

            # Parse SQL from LLM response
            from backend.api.chat import _parse_llm_json
            parsed = _parse_llm_json(full_text)
            sql = parsed.get("sql", "")
            chart_type = parsed.get("chart-type", "table")
            brief = parsed.get("brief", "")

            if not sql:
                yield _sse_event("done", {
                    "reply": parsed.get("message", full_text),
                    "intent": intent,
                })
                return

            # Step 5: Validate SQL
            yield _sse_event("progress", {"stage": "validate", "message": "正在校验 SQL..."})
            is_valid, fixed_sql, warnings = validate_and_fix(sql, datasource_id)
            if not is_valid:
                yield _sse_event("done", {
                    "reply": f"SQL验证失败: {'; '.join(warnings)}",
                    "sql": sql, "intent": intent, "warnings": warnings,
                })
                return

            # Check table permissions
            if allowed_tables:
                tables_in_sql = re.findall(r'FROM\s+(\w+)', fixed_sql, re.IGNORECASE)
                tables_in_sql += re.findall(r'JOIN\s+(\w+)', fixed_sql, re.IGNORECASE)
                for t in tables_in_sql:
                    if t.lower() not in [at.lower() for at in allowed_tables]:
                        yield _sse_event("done", {
                            "reply": f"无权查询表: {t}",
                            "sql": fixed_sql, "intent": intent,
                        })
                        return

            # Step 6: Execute query
            yield _sse_event("progress", {"stage": "execute", "message": "正在执行查询..."})
            result = execute_query(fixed_sql, datasource_id=datasource_id)

            # ── Interpretation Loop ──────────────────────────
            from backend.api.model_config import get_system_config
            needs_interp = parsed.get("needs_interpretation", False)
            interp_prompt = parsed.get("interpretation_prompt", "")
            interp_round = 0
            MAX_INTERP_ROUNDS = int(get_system_config("max_interpretation_rounds", "3"))

            while needs_interp and interp_round < MAX_INTERP_ROUNDS:
                interp_round += 1
                logger.info("[Embed] Interpretation round %d/%d", interp_round, MAX_INTERP_ROUNDS)

                yield _sse_event("progress", {
                    "stage": "interpret",
                    "message": f"正在分析结果（第{interp_round}轮）...",
                })

                interp_result = None
                for evt_type, evt_data in _interpret_results_stream(
                    question=question, sql=fixed_sql, result=result,
                    interpretation_prompt=interp_prompt,
                    column_metadata=rag_results.get("column_metadata", []),
                    current_round=interp_round, max_rounds=MAX_INTERP_ROUNDS,
                ):
                    if evt_type == "token":
                        yield _sse_event("token", evt_data)
                    elif evt_type == "thinking":
                        yield _sse_event("thinking", evt_data)
                    elif evt_type == "interpretation_done":
                        interp_result = evt_data

                if interp_result:
                    interp_reply = interp_result.get("reply", "")
                    if interp_reply:
                        brief = interp_reply[:200]
                        result["interpretation"] = interp_reply
                    new_chart_type = interp_result.get("chart_type")
                    if new_chart_type and new_chart_type != chart_type:
                        chart_type = new_chart_type
                    needs_interp = interp_result.get("needs_interpretation", False)
                    interp_prompt = interp_result.get("interpretation_prompt", "")
                else:
                    break

            # Save to conversation
            if conversation_id and result:
                try:
                    conn = pymysql.connect(
                        host=DORIS_HOST, port=DORIS_PORT, user=DORIS_USER,
                        password=DORIS_PASSWORD, database=METADATA_DB_DATABASE,
                        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
                    )
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE adh_conversations SET messages = %s, updated_at = NOW() "
                            "WHERE id = %s AND embed_user_id = %s",
                            (json.dumps(history + [
                                {"role": "user", "content": question},
                                {"role": "assistant", "content": full_text, "sql": fixed_sql},
                            ]), conversation_id, user_id),
                        )
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.warning("Failed to save embed conversation: %s", e)

            yield _sse_event("done", {
                "reply": brief or full_text,
                "sql": fixed_sql,
                "chart_type": chart_type,
                "result": _sanitize_for_json(result),
                "intent": intent,
                "warnings": warnings if warnings else [],
                "rag": {
                    "rag_source": rag_source,
                    "table_info_count": len(rag_results.get("table_info", [])),
                    "column_metadata_count": len(rag_results.get("column_metadata", [])),
                },
            })

        except Exception as e:
            logger.error("Embed chat error: %s", e, exc_info=True)
            yield _sse_event("error", {"message": str(e)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Dashboard ──────────────────────────────────────────────────────

@router.get("/dashboards")
async def list_dashboards(user: dict = Depends(_get_embed_user)):
    import pymysql
    from backend.common.config import DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE

    app_id = user["app_id"]
    app = get_app_by_id(app_id)

    allowed_ids = None
    if app and app.get("allowed_dashboards"):
        try:
            allowed_ids = json.loads(app["allowed_dashboards"])
        except Exception:
            pass

    try:
        conn = pymysql.connect(
            host=DORIS_HOST, port=DORIS_PORT, user=DORIS_USER,
            password=DORIS_PASSWORD, database=METADATA_DB_DATABASE,
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        )
        with conn.cursor() as cur:
            if allowed_ids:
                placeholders = ",".join(["%s"] * len(allowed_ids))
                cur.execute(
                    f"SELECT id, name, description, status, created_at, updated_at "
                    f"FROM adh_dashboards WHERE id IN ({placeholders})",
                    allowed_ids,
                )
            else:
                cur.execute(
                    "SELECT id, name, description, status, created_at, updated_at "
                    "FROM adh_dashboards"
                )
            items = cur.fetchall()
        conn.close()
        return {"items": items, "total": len(items)}
    except Exception as e:
        logger.error("Failed to list embed dashboards: %s", e)
        raise HTTPException(status_code=500, detail="查询仪表盘列表失败")


@router.get("/dashboards/{dashboard_id}")
async def get_dashboard(dashboard_id: int, user: dict = Depends(_get_embed_user)):
    import pymysql
    from backend.common.config import DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE

    app_id = user["app_id"]
    app = get_app_by_id(app_id)

    # Check permission
    if app and app.get("allowed_dashboards"):
        try:
            allowed_ids = json.loads(app["allowed_dashboards"])
            if dashboard_id not in allowed_ids:
                raise HTTPException(status_code=403, detail="无权访问该仪表盘")
        except HTTPException:
            raise
        except Exception:
            pass

    try:
        conn = pymysql.connect(
            host=DORIS_HOST, port=DORIS_PORT, user=DORIS_USER,
            password=DORIS_PASSWORD, database=METADATA_DB_DATABASE,
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, description, layout, filters, params, status, "
                "is_public, carousel_interval, created_at, updated_at "
                "FROM adh_dashboards WHERE id = %s",
                (dashboard_id,),
            )
            dashboard = cur.fetchone()

            if not dashboard:
                raise HTTPException(status_code=404, detail="仪表盘不存在")

            cur.execute(
                "SELECT id, name, chart_type, sql_query, config, position, "
                "source_type, source_id, data_cache, created_at, updated_at "
                "FROM adh_charts WHERE dashboard_id = %s",
                (dashboard_id,),
            )
            charts = cur.fetchall()
        conn.close()

        for field in ("layout", "filters", "params"):
            if isinstance(dashboard.get(field), str):
                try:
                    dashboard[field] = json.loads(dashboard[field])
                except Exception:
                    dashboard[field] = {} if field != "params" else []

        for chart in charts:
            for field in ("config", "position"):
                if isinstance(chart.get(field), str):
                    try:
                        chart[field] = json.loads(chart[field])
                    except Exception:
                        chart[field] = {}

        dashboard["charts"] = charts
        return dashboard

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get embed dashboard: %s", e)
        raise HTTPException(status_code=500, detail="查询仪表盘失败")


# ── Admin: Application Management ──────────────────────────────────

@router.get("/admin/applications", response_model=ApplicationListResponse)
async def admin_list_applications(
    page: int = QueryParam(1, ge=1),
    size: int = QueryParam(50, ge=1, le=200),
    search: str = QueryParam(""),
    admin: dict = Depends(lambda credentials=Depends(security): _require_admin(credentials)),
):
    items, total = list_applications(page=page, size=size, search=search)
    return ApplicationListResponse(items=items, total=total)


@router.post("/admin/applications", response_model=ApplicationKeyResponse)
async def admin_create_application(
    req: ApplicationCreate,
    admin: dict = Depends(lambda credentials=Depends(security): _require_admin(credentials)),
):
    ok, msg, app_id, api_key = create_application(
        name=req.name,
        description=req.description or "",
        enable_chat=req.enable_chat,
        allowed_dashboards=req.allowed_dashboards,
        allowed_tables=req.allowed_tables,
        rate_limit=req.rate_limit,
        created_by=admin.get("id", 0),
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return ApplicationKeyResponse(id=app_id, name=req.name, api_key=api_key, message=msg)


@router.put("/admin/applications/{app_id}")
async def admin_update_application(
    app_id: int, req: ApplicationUpdate,
    admin: dict = Depends(lambda credentials=Depends(security): _require_admin(credentials)),
):
    ok, msg = update_application(
        app_id,
        name=req.name, description=req.description,
        enable_chat=req.enable_chat,
        allowed_dashboards=req.allowed_dashboards, allowed_tables=req.allowed_tables,
        rate_limit=req.rate_limit, status=req.status,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}


@router.post("/admin/applications/{app_id}/rotate-key", response_model=ApplicationKeyResponse)
async def admin_rotate_key(
    app_id: int,
    admin: dict = Depends(lambda credentials=Depends(security): _require_admin(credentials)),
):
    app = get_app_by_id(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    ok, msg, new_key = rotate_api_key(app_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return ApplicationKeyResponse(id=app_id, name=app["name"], api_key=new_key, message=msg)


@router.delete("/admin/applications/{app_id}")
async def admin_delete_application(
    app_id: int,
    admin: dict = Depends(lambda credentials=Depends(security): _require_admin(credentials)),
):
    """Delete an application (admin only)."""
    ok, msg = delete_application(app_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}


@router.get("/admin/dashboards-for-select")
async def admin_dashboards_for_select(
    admin: dict = Depends(lambda credentials=Depends(security): _require_admin(credentials)),
):
    """List all dashboards for selector UI (admin only)."""
    import pymysql
    from backend.common.config import DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE
    try:
        conn = pymysql.connect(
            host=DORIS_HOST, port=DORIS_PORT, user=DORIS_USER,
            password=DORIS_PASSWORD, database=METADATA_DB_DATABASE,
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM adh_dashboards ORDER BY id DESC")
            items = cur.fetchall()
        conn.close()
        return {"items": items}
    except Exception as e:
        logger.error("Failed to list dashboards for select: %s", e)
        return {"items": []}


@router.get("/admin/tables-for-select")
async def admin_tables_for_select(
    datasource_id: int = QueryParam(0),
    admin: dict = Depends(lambda credentials=Depends(security): _require_admin(credentials)),
):
    """List all tables for selector UI (admin only)."""
    import pymysql
    from backend.common.config import DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE
    try:
        conn = pymysql.connect(
            host=DORIS_HOST, port=DORIS_PORT, user=DORIS_USER,
            password=DORIS_PASSWORD, database=METADATA_DB_DATABASE,
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        )
        with conn.cursor() as cur:
            if datasource_id:
                cur.execute(
                    "SELECT DISTINCT table_name FROM adh_table_info WHERE datasource_id = %s ORDER BY table_name",
                    (datasource_id,),
                )
            else:
                cur.execute("SELECT DISTINCT table_name FROM adh_table_info ORDER BY table_name")
            items = cur.fetchall()
        conn.close()
        return {"items": [r["table_name"] for r in items]}
    except Exception as e:
        logger.error("Failed to list tables for select: %s", e)
        return {"items": []}


@router.get("/admin/embed-logs", response_model=EmbedLogResponse)
async def admin_list_embed_logs(
    page: int = QueryParam(1, ge=1),
    size: int = QueryParam(50, ge=1, le=200),
    app_id: int = QueryParam(0),
    user_id: str = QueryParam(""),
    status: str = QueryParam(""),
    admin: dict = Depends(lambda credentials=Depends(security): _require_admin(credentials)),
):
    items, total = list_embed_logs(page=page, size=size, app_id=app_id, user_id=user_id, status=status)
    return EmbedLogResponse(items=[EmbedLogItem(**item) for item in items], total=total)
