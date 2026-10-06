"""AS-BOT 设计草稿、显式业务授权、治理预览与事务发布。

设计状态只存在 MySQL；SQL 仅能经鉴权 REST 交给人类编辑器，不能进入工具返回。
发布走直执行（菜单与功能权限码 dashboard:manage 把关），不再经审批回路。
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import logging
from types import SimpleNamespace
import uuid

from services.shared.common.db import execute_query, execute_insert
from services.shared.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)
TERMINAL = {"published", "cancelled"}
SQL_KEYS = {"sql", "raw_sql", "statement", "query_sql", "base_sql", "sql_query", "secured_sql", "manual_sql"}
PRIVATE_KEYS = SQL_KEYS | {"datasource_id", "source_id", "workspace_id", "user_id", "username",
                           "user_role", "decided_by", "proposed_by", "catalog_ref", "physical_table", "provenance"}


class DesignError(ValueError):
    """可以安全向用户展示的业务错误。"""
    def __init__(self, message, code="invalid_design", status=422):
        super().__init__(message)
        self.code, self.status = code, status


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def decoded(value, default=None):
    return json.loads(value) if isinstance(value, str) else (value if value is not None else default)


def reject_private(value):
    if isinstance(value, dict):
        tokens = {str(k).replace('_', '').lower() for k in value}
        if PRIVATE_KEYS.intersection(value) or tokens.intersection({'countsql', 'rawsql', 'sqlquery', 'basesql', 'securedsql', 'datasourceid'}):
            raise DesignError("设计工具只接受业务意图和视觉配置，不能指定 SQL、身份或物理资源")
        for item in value.values():
            reject_private(item)
    elif isinstance(value, list):
        for item in value:
            reject_private(item)


@contextmanager
def transaction():
    conn = get_metadata_conn()
    try:
        conn.begin()
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def identity(user):
    from services.shared.common.auth import resolve_execution_owner
    live = resolve_execution_owner(user.get("user_id"), 0)
    return live


def check_conversation(conversation_id, user):
    """设计会话绑定只校验归属（user_id）；会话历史已合并共用，不再按入口区分。"""
    row = execute_query("SELECT user_id FROM adh_conversations WHERE id=%s",
                        (conversation_id,), fetchone=True)
    if not row or row["user_id"] != user["user_id"]:
        raise DesignError("设计会话不存在或无权访问", "not_found", 404)


def load(design_id, user, conversation_id=None, cur=None):
    user = identity(user)
    sql = "SELECT *,preview_expires_at > UTC_TIMESTAMP(6) AS preview_valid FROM adh_as_bot_dashboard_designs WHERE id=%s AND user_id=%s"
    if cur is not None:
        cur.execute(sql + " FOR UPDATE", (design_id, user["user_id"]))
        row = cur.fetchone()
    else:
        row = execute_query(sql, (design_id, user["user_id"]), fetchone=True)
    if not row or (conversation_id is not None and row["conversation_id"] != conversation_id):
        raise DesignError("设计不存在或不属于当前会话", "not_found", 404)
    check_conversation(row["conversation_id"], user)
    for key in ("content", "preview", "result"):
        row[key] = decoded(row.get(key), {})
    return row, user


def public_design(row):
    """严格投影：人工 SQL 和预览数据永不进入聊天/Agent。"""
    doc = row["content"]
    widgets = []
    for w in doc.get("widgets", []):
        widgets.append({k: w[k] for k in ("key", "title", "chart_type", "query", "config", "position", "query_source") if k in w})
    return {"design_id": row["id"], "version": row["version"], "status": row["status"],
            "name": doc.get("name", ""), "request": doc.get("request", ""),
            "operation": doc.get("operation", "append"), "selection": doc.get("selection"),
            "widgets": widgets, "steps": doc.get("steps", []), "questions": doc.get("questions", []),
            "answers": doc.get("answers", {}),
            "preview_valid": bool(row.get("preview_valid")), "result": row.get("result") or None,
            "notice": "业务范围和存疑口径由用户选择；SQL 编辑与预览请打开设计面板。"}


def get_design(design_id, user, conversation_id=None):
    row, user = load(design_id, user, conversation_id)
    if row["content"].get("selection") and row["status"] not in TERMINAL:
        resolve_scope(row["content"], user)
    return public_design(row)


def list_designs(conversation_id, user):
    user = identity(user)
    check_conversation(conversation_id, user)
    rows = execute_query("SELECT *,preview_expires_at > UTC_TIMESTAMP(6) AS preview_valid FROM adh_as_bot_dashboard_designs "
                         "WHERE user_id=%s AND conversation_id=%s ORDER BY created_at", (user["user_id"], conversation_id))
    for row in rows:
        for key in ("content", "result"):
            row[key] = decoded(row.get(key), {})
    return [public_design(row) for row in rows]


def request_design(user, conversation_id, request, name="", operation="append"):
    user = identity(user)
    check_conversation(conversation_id, user)
    if operation not in ("create", "append", "update") or not str(request).strip():
        raise DesignError("请说明设计需求和操作类型")
    design_id = uuid.uuid4().hex
    doc = {"name": str(name)[:256], "request": str(request)[:4000], "operation": operation,
           "widgets": [], "steps": [], "questions": [], "answers": {}}
    execute_insert("INSERT INTO adh_as_bot_dashboard_designs (id,user_id,conversation_id,content,created_at,updated_at) "
                   "VALUES (%s,%s,%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
                   (design_id, user["user_id"], conversation_id, dump(doc)))
    return get_design(design_id, user, conversation_id)


def _accessible_kbs(user, workspace_id):
    """设计知识库来自当前角色 AS-BOT 显式绑定；业务访问仍须确认设计范围。"""
    from services.shared.common.auth import authorize_workspace
    from services.datamind.execution.as_bots import resolve_as_bots, default_as_bot
    authorize_workspace(user, workspace_id)
    as_bot = default_as_bot(resolve_as_bots(workspace_id, user.get("role") or "",
                                             user_id=user.get("user_id") or 0))
    if not as_bot:
        raise DesignError("当前角色未配置 AS-BOT", "scope_changed", 409)
    ids = as_bot.get("knowledge_base_ids") or []
    if not ids:
        return []
    marks = ','.join(['%s'] * len(ids))
    rows = execute_query(f"SELECT id,name,kb_type,source_config FROM adh_knowledge_bases "
                         f"WHERE id IN ({marks}) AND status='active' ORDER BY id", tuple(ids))
    if {r['id'] for r in rows} != set(ids):
        raise DesignError("AS-BOT 绑定的知识库不存在或已停用", "scope_changed", 409)
    return rows


def _accessible_datasource_ids(user, workspace_id):
    """None=不限制(admin)；否则返回授权集合（空集合=无权，不回退全量）。"""
    if user["role"] == "admin":
        return None
    from services.authservice.services.role_service import role_service
    return set(role_service.get_user_allowed_datasources(user["user_id"], workspace_id))


def _discover_domains(user, workspace_id):
    """业务域 = 有生效业务本体模型且用户可访问的数据源；AS-BOT 不持有数据源配置。"""
    rows = execute_query(
        "SELECT d.id,d.name,d.db_type,m.name AS model_name FROM adh_datasources d "
        "JOIN adh_ontology_models m ON m.datasource_id=d.id AND m.status='active' AND m.datasource_id>0 "
        "ORDER BY d.id,m.id")
    allowed = _accessible_datasource_ids(user, workspace_id)
    kbs = _accessible_kbs(user, workspace_id)
    domains, seen = [], set()
    for r in rows:
        if allowed is not None and r["id"] not in allowed:
            continue
        if r["name"] in seen:
            continue
        seen.add(r["name"])
        domains.append({"datasource_name": r["name"], "model_name": r["model_name"],
                        "knowledge_bases": [{"id": k["id"], "name": k["name"], "kb_type": k["kb_type"]}
                                            for k in kbs if k["kb_type"] == "qmind"]})
    return domains


def options(design_id, user, workspace_id=None):
    """工作空间不是必选项：缺省按系统会话域(0)发现候选，仪表盘/业务域直接可选。"""
    row, user = load(design_id, user)
    from services.shared.common.auth import authorize_workspace
    ws = authorize_workspace(user, workspace_id if workspace_id is not None else 0)
    spaces = [{"id": 0, "name": "系统工作空间"}] + execute_query("SELECT id,name FROM adh_workspaces ORDER BY id")
    out = {"workspaces": spaces, "dashboards": [], "domains": _discover_domains(user, ws)}
    sql = ("SELECT id,name,workspace_id AS ws FROM adh_dashboards WHERE owner_id=%s "
           "AND (workspace_id=%s OR is_public=1) ORDER BY id")
    dashboards = execute_query(sql, (user["user_id"], ws))
    for dashboard in dashboards:
        dashboard["charts"] = execute_query("SELECT id,name,chart_type FROM adh_charts WHERE dashboard_id=%s ORDER BY id",
                                             (dashboard["id"],))
    out["dashboards"] = dashboards
    return out


def _derive_workspace(s, ds_id, user):
    """工作空间自动派生：已有仪表盘取其归属；否则显式 workspace；均不要求用户先选。

    工作空间不再绑定数据源（adh_workspace_datasources 退役为冻结表），
    workspace_id 仅作管理归属；无仪表盘/显式空间时回落 0（个人域锚点）。
    """
    if s.get("dashboard_id"):
        row = execute_query("SELECT workspace_id FROM adh_dashboards WHERE id=%s", (s["dashboard_id"],), fetchone=True)
        if row:
            return int(row["workspace_id"] or 0)
    if s.get("workspace") is not None:
        return int(s["workspace"])
    return 0


def resolve_scope(doc, user, cur=None):
    s = doc.get("selection")
    if not s or not s.get("datasource_name"):
        raise DesignError("请先在设计面板确认目标仪表盘与业务域", "selection_required", 409)
    ds = execute_query("SELECT id,name,db_type FROM adh_datasources WHERE name=%s", (s["datasource_name"],), fetchone=True)
    if not ds:
        raise DesignError("业务域不存在或已删除", "scope_changed", 409)
    model = execute_query("SELECT id FROM adh_ontology_models WHERE datasource_id=%s AND status='active' AND datasource_id>0",
                          (ds["id"],), fetchone=True)
    if not model:
        raise DesignError("该业务域没有生效的业务本体模型，请先完成建模或选择其他业务域", "no_ontology", 409)
    ws = _derive_workspace(s, ds["id"], user)
    from services.shared.common.auth import authorize_workspace
    authorize_workspace(user, ws)
    allowed = _accessible_datasource_ids(user, ws)
    if allowed is not None and ds["id"] not in allowed:
        raise DesignError("业务域未授权给当前用户", "forbidden", 403)
    selected_ids = s.get("knowledge_base_ids") or []
    accessible_kbs = _accessible_kbs(user, ws)
    if not set(selected_ids) <= {kb["id"] for kb in accessible_kbs}:
        raise DesignError("知识库不可用或 AS-BOT 授权已变化，请重新选择", "scope_changed", 409)
    kbs = [kb for kb in accessible_kbs if kb["id"] in selected_ids]
    target = target_snapshot(doc, user, ws, cur=cur)
    return {"workspace": ws, "datasource_id": ds["id"], "datasource_name": ds["name"],
            "dialect": "postgres" if ds["db_type"] in ("postgres", "postgresql", "pg", "sls") else "mysql",
            "knowledge_bases": kbs, "target": target}


def target_snapshot(doc, user, workspace_id, cur=None):
    s = doc["selection"]
    if doc["operation"] == "create":
        if not doc.get("name", "").strip():
            raise DesignError("新仪表盘名称不能为空")
        return {}
    sql = "SELECT id,name,owner_id,workspace_id,layout,filters,params FROM adh_dashboards WHERE id=%s"
    if cur:
        cur.execute(sql + " FOR UPDATE", (s.get("dashboard_id"),))
        target = cur.fetchone()
    else:
        target = execute_query(sql, (s.get("dashboard_id"),), fetchone=True)
    if not target or target["owner_id"] != user["user_id"] or int(target["workspace_id"] or 0) != workspace_id:
        raise DesignError("目标仪表盘不存在或无编辑权限", "forbidden", 403)
    sql = "SELECT id,name,chart_type,sql_query,semantic_query,query_source,source_id,config,position FROM adh_charts WHERE dashboard_id=%s ORDER BY id"
    if cur:
        cur.execute(sql + " FOR UPDATE", (target["id"],))
        charts = cur.fetchall()
    else:
        charts = execute_query(sql, (target["id"],))
    if doc["operation"] == "update" and not any(c["id"] == s.get("chart_id") for c in charts):
        raise DesignError("待修改图表不属于目标仪表盘")
    return {"dashboard": target, "charts": charts}


def _version(row, expected_version):
    if row["version"] != expected_version:
        raise DesignError("设计已变化，请刷新后重试", "version_conflict", 409)
    if row["status"] in TERMINAL:
        raise DesignError("设计已结束，不能继续修改", "design_closed", 409)


def _supersede(cur, row):
    """设计/预览变更使旧预览失效（无审批单，直接清预览状态）。"""
    return None


def change(design_id, user, expected_version, mutate):
    with transaction() as cur:
        row, live = load(design_id, user, cur=cur)
        _version(row, expected_version)
        status = mutate(row["content"], live) or "designing"
        cur.execute("UPDATE adh_as_bot_dashboard_designs SET content=%s,status=%s,version=version+1,preview=NULL,"
                    "preview_expires_at=NULL,updated_at=UTC_TIMESTAMP(6) WHERE id=%s AND version=%s",
                    (dump(row["content"]), status, design_id, expected_version))
    return get_design(design_id, user)


def select_scope(design_id, user, expected_version, selection, name=None, operation=None):
    allowed_keys = {"datasource_name", "knowledge_base_ids", "dashboard_id", "chart_id", "workspace"}
    if not isinstance(selection, dict) or not selection.get("datasource_name") \
            or set(selection) - allowed_keys \
            or not isinstance(selection.get("knowledge_base_ids", []), list):
        raise DesignError("请选择业务域；知识库可选（不选则仅用实时语义目录）")
    if operation is not None and operation not in ("create", "append", "update"):
        raise DesignError("操作类型无效")
    def mutate(doc, live):
        doc["selection"] = selection
        if operation is not None:
            doc["operation"] = operation
        if doc.get("operation") == "create":
            doc["selection"].pop("dashboard_id", None)
            doc["selection"].pop("chart_id", None)
        elif not selection.get("dashboard_id"):
            raise DesignError("请选择目标仪表盘，或选择新建")
        if name is not None:
            doc["name"] = str(name)[:256]
        resolve_scope(doc, live)
        # 换域不能沿用旧对象和人工 SQL。
        doc.update(widgets=[], steps=[], questions=[], answers={})
    return change(design_id, user, expected_version, mutate)


def semantics(design_id, user, keyword="", conversation_id=None):
    row, live = load(design_id, user, conversation_id)
    scope = resolve_scope(row["content"], live)
    if row["status"] in TERMINAL:
        raise DesignError("设计已结束，请新建设计")
    from services.datamind.execution.sdk_tools.scoped_metadata import execute
    ctx = SimpleNamespace(datasource_id=scope["datasource_id"], extra={})
    catalog = execute("get_metrics", {"keyword": keyword}, ctx, resource_scope=scope)
    objects = execute("search_ontology", {"keyword": keyword}, ctx, resource_scope=scope)
    return {**catalog, "ontology": objects["objects"], "scope": "dashboard_design", "design_id": design_id,
            "datasource_name": scope["datasource_name"], "usage": "复制实时目录名称；口径或时间字段存疑时提出选项，等待用户确认。"}


def business_knowledge(design_id, user, questions, conversation_id=None):
    from services.datamind.rag import qmind_retriever as qr
    from services.shared.observability import record_span
    row, live = load(design_id, user, conversation_id)
    scope = resolve_scope(row["content"], live)
    if row["status"] in TERMINAL:
        raise DesignError("设计已结束，请新建设计")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 8 or any(not isinstance(q, str) or not q.strip() for q in questions):
        raise DesignError("请提供 1–8 个业务问题")
    if not scope["knowledge_bases"]:
        raise DesignError("尚未选择业务知识库；请选择，或明确仅依据实时语义目录继续", "knowledge_required", 409)
    results = []
    for kb in scope["knowledge_bases"]:
        cfg = decoded(kb["source_config"], {})
        if kb["kb_type"] != "qmind" or not cfg.get("notebook_id"):
            raise DesignError("所选知识库没有可用的 QMind 检索配置", "knowledge_unavailable", 503)
        for question in questions:
            try:
                chunks = qr.retrieve_notebook_strict(cfg["notebook_id"], question)
            except Exception as exc:
                logger.exception("业务知识检索失败，design=%s", design_id)
                raise DesignError("业务知识库当前不可用；未切换检索来源，请重试或选择仅使用实时语义目录", "knowledge_unavailable", 503) from exc
            verified = []
            for chunk in chunks:
                stamp = qr._extract_doc_stamp([chunk])
                if stamp and str(stamp["model_id"]).isdigit():
                    models = execute_query("SELECT id,name,datasource_id,updated_at FROM adh_ontology_models WHERE id=%s", (int(stamp["model_id"]),))
                else:
                    title = str(chunk.get("title") or "").rsplit("/", 1)[-1]
                    if not title.startswith("本体模型-") or not title.endswith(".md"):
                        raise DesignError("知识文档缺少可核验的业务模型来源，未向助手提供该内容", "unverified_source", 409)
                    models = execute_query("SELECT id,name,datasource_id,updated_at FROM adh_ontology_models WHERE name=%s AND status='active'", (title[5:-3],))
                if len(models) != 1 or models[0]["datasource_id"] != scope["datasource_id"]:
                    raise DesignError("知识库结果包含其他业务域或来源不明确，请重新选择知识库", "scope_mismatch", 409)
                verified.append({**chunk, "model_name": models[0]["name"],
                                 "doc_stale": str(stamp["model_version"]) != str(models[0]["updated_at"]) if stamp else None})
            results.append({"question": question, "knowledge_base": kb["name"], "chunks": verified,
                            "count": len(verified), "retrieval_source": "qmind_hit" if verified else "none"})
    # 检索期间撤权不得通过旧结果继续访问。
    resolve_scope(row["content"], identity(live))
    record_span(kind="retrieval", name="search_business_knowledge", status="success",
                output_text=f"scope=dashboard_design count={sum(r['count'] for r in results)}")
    return {"design_id": design_id, "scope": "dashboard_design", "datasource_name": scope["datasource_name"], "results": results}


def validate_questions(questions):
    if not isinstance(questions, list) or len(questions) > 12:
        raise DesignError("待确认问题格式无效")
    seen = set()
    for q in questions:
        if not isinstance(q, dict) or set(q) != {"key", "label", "options"} or not q["key"] or q["key"] in seen:
            raise DesignError("每个待确认问题须有唯一 key、label 和 options")
        seen.add(q["key"])
        if not isinstance(q["options"], list) or len(q["options"]) < 2 or any(not isinstance(o, str) or not o for o in q["options"]):
            raise DesignError("存疑问题至少提供两个明确选项")


def validate_widgets(widgets):
    from services.datamind.execution.sdk_tools.screen_tools import _CHART_TYPES
    if not isinstance(widgets, list) or not 1 <= len(widgets) <= 12:
        raise DesignError("设计需包含 1–12 个图表")
    result = []
    for i, raw in enumerate(widgets):
        if not isinstance(raw, dict):
            raise DesignError("图表设计必须为对象")
        reject_private(raw)
        if set(raw) - {"key", "title", "chart_type", "query", "config", "position"}:
            raise DesignError("图表包含未支持的设计字段")
        if raw.get("chart_type") not in _CHART_TYPES - {"iframe"} or not isinstance(raw.get("query"), dict):
            raise DesignError("请选择支持的图表类型，并提供声明式查询意图")
        if not raw.get("title"):
            raise DesignError("图表标题不能为空")
        cfg = raw.get("config") or {}
        if not isinstance(cfg, dict):
            raise DesignError("视觉配置必须为对象")
        pos = raw.get("position") or {"x": 0, "y": i * 372, "w": 960, "h": 360}
        if not isinstance(pos, dict) or set(pos) != {"x", "y", "w", "h"} or any(not isinstance(v, (int, float)) for v in pos.values()):
            raise DesignError("布局必须包含数值 x/y/w/h")
        if min(pos["x"], pos["y"]) < 0 or min(pos["w"], pos["h"]) <= 0:
            raise DesignError("布局尺寸或位置无效")
        result.append({"key": str(i), "title": str(raw["title"])[:256], "chart_type": raw["chart_type"],
                       "query": raw["query"], "query_source": "semantic", "config": cfg, "position": pos})
    return result


def prepare(design_id, user, expected_version, widgets, steps, questions=None, conversation_id=None):
    row, live = load(design_id, user, conversation_id)
    questions = questions or []
    validate_questions(questions)
    reject_private(questions)
    if not isinstance(steps, list) or any(not isinstance(s, str) for s in steps) or not steps:
        raise DesignError("请提供具体设计步骤")
    def mutate(doc, actor):
        resolve_scope(doc, actor)
        if any(w.get("query_source") == "raw_sql" for w in doc.get("widgets", [])):
            raise DesignError("当前含人工 SQL，请用户在设计面板重置后再生成，不能覆盖人工修改")
        doc["widgets"] = validate_widgets(widgets) if widgets else []
        if doc["operation"] == "update" and len(doc["widgets"]) > 1:
            raise DesignError("修改单图只允许一个图表")
        if not doc["widgets"] and not questions:
            raise DesignError("请提供图表方案或待确认问题")
        doc["steps"] = steps[:30]
        old_questions = doc.get("questions", [])
        old_answers = doc.get("answers", {})
        doc["questions"] = questions
        doc["answers"] = {q["key"]: old_answers[q["key"]] for q in questions
                          if q in old_questions and q["key"] in old_answers}
    return change(design_id, live, expected_version, mutate)


def edit_design(design_id, user, expected_version, patch):
    if set(patch) - {"answers", "widgets", "visuals", "name", "reset_sql"}:
        raise DesignError("不支持的设计修改")
    reject_private(patch)
    def mutate(doc, live):
        resolve_scope(doc, live)
        if "answers" in patch:
            answers = patch["answers"]
            valid = {q["key"]: q["options"] for q in doc.get("questions", [])}
            if not isinstance(answers, dict) or any(k not in valid or v not in valid[k] for k, v in answers.items()):
                raise DesignError("请选择列出的口径选项")
            doc["answers"] = answers
            # 回答待确认问题后必须由 Agent 据此更新方案，不能批准旧意图。
            doc["widgets"] = []
        if "name" in patch:
            doc["name"] = str(patch["name"])[:256]
        if "widgets" in patch:
            if any(w.get("query_source") == "raw_sql" for w in doc.get("widgets", [])):
                raise DesignError("人工 SQL 模式请仅编辑视觉配置或先重置 SQL")
            doc["widgets"] = validate_widgets(patch["widgets"])
        if "visuals" in patch:
            for visual in patch["visuals"]:
                if not isinstance(visual, dict) or set(visual) - {"key", "title", "chart_type", "config", "position"}:
                    raise DesignError("视觉修改不能改变查询")
                widget = next((w for w in doc.get("widgets", []) if w["key"] == visual.get("key")), None)
                if not widget:
                    raise DesignError("待修改图表不存在")
                candidate = {k: v for k, v in widget.items() if k in {"key", "title", "chart_type", "config", "position", "query"}}
                candidate.update(visual)
                validate_widgets([candidate])
                widget.update(visual)
        if patch.get("reset_sql"):
            for widget in doc.get("widgets", []):
                widget.pop("manual_sql", None)
                widget["query_source"] = "semantic"
    return change(design_id, user, expected_version, mutate)


def require_sql_permission(user):
    from services.shared.common.api_permission import check_api_permission
    if not check_api_permission(user["role"], "POST", "/api/playground/execute"):
        raise DesignError("没有 SQL 查看/编辑权限", "forbidden", 403)


def compile_widget(widget, scope):
    from services.shared.semantics.intent import parse_intent
    from services.shared.semantics.binding_resolver import resolve_binding
    from services.shared.semantics.planner import plan
    from services.shared.semantics.sql_guard import bounded_query
    if widget.get("query_source") == "raw_sql":
        return bounded_query(widget["manual_sql"], 500, scope["dialect"]), None
    query = widget.get("query") or {}
    reject_private(query)
    q, error, _ = parse_intent({**query, "datasource_id": scope["datasource_id"], "workspace_id": scope["workspace"]})
    if error:
        raise DesignError("语义意图无效，请依据实时指标目录修改")
    # 时间维度必须显式选择，避免 planner 默认采用首个时间字段。
    if q.time_window and not q.time_column:
        raise DesignError("请明确选择近 N 天所依据的时间字段", "time_column_required")
    binding, _ = resolve_binding(q.object, datasource_id=scope["datasource_id"])
    if not binding or binding.datasource_id != scope["datasource_id"]:
        raise DesignError("对象未绑定到已确认业务域", "unbound")
    model = execute_query("SELECT id,json_content,updated_at FROM adh_ontology_models WHERE id=%s AND datasource_id=%s AND status='active'",
                          (binding.model_id, scope["datasource_id"]), fetchone=True)
    if not model:
        raise DesignError("对象不属于已确认业务域的生效本体", "unbound")
    q.limit = min(q.limit or 500, 500)
    p = plan(q, binding)
    failures = [w for w in p.warnings if not str(w).startswith(("guardrail: force_limit applied", "guardrail: limit clamped to max_rows"))]
    if not p.sql or failures:
        from services.datamind.execution.sdk_tools.semantic_query import _safe_warnings
        warnings = _safe_warnings(failures)
        raise DesignError("语义编译需调整：" + ("；".join(warnings) if warnings else "请检查对象、指标、维度及限制"))
    return bounded_query(p.sql, 500, p.dialect), {"binding": binding.model_dump(), "model": model}


def validate_query_sources(sql, scope):
    from services.shared.semantics.sql_guard import extract_tables
    refs = extract_tables(sql, scope["dialect"])
    rows = execute_query("SELECT t.table_name,d.database_name,c.catalog_name FROM adh_table_info t "
                         "JOIN adh_datasources d ON d.id=t.datasource_id LEFT JOIN adh_catalogs c ON c.id=t.catalog_id "
                         "WHERE t.datasource_id=%s AND t.is_active=1", (scope["datasource_id"],))
    allowed = set()
    for r in rows:
        name = r["table_name"]
        allowed.add(name.lower())
        if r.get("database_name"):
            allowed.add(f"{r['database_name']}.{name}".lower())
            if r.get("catalog_name"):
                allowed.add(f"{r['catalog_name']}.{r['database_name']}.{name}".lower())
    if any(ref.lower() not in allowed for ref in refs):
        raise DesignError("查询引用了已确认业务域以外或未登记的数据对象", "source_mismatch", 403)
    return refs


def compile_design(row, user):
    doc = row["content"]
    scope = resolve_scope(doc, user)
    if not doc.get("widgets"):
        raise DesignError("尚未完成图表方案，请继续与 AS-BOT 确认")
    if any(q["key"] not in doc.get("answers", {}) for q in doc.get("questions", [])):
        raise DesignError("还有待确认的口径选项", "answers_required")
    queries, bindings, policies = [], [], []
    from services.datamind.permission.enforcer import permission_enforcer
    for widget in doc["widgets"]:
        sql, binding = compile_widget(widget, scope)
        for table in validate_query_sources(sql, scope):
            policy = permission_enforcer.check_access(user["user_id"], scope["workspace"], scope["datasource_id"], table)
            if not policy.allowed:
                raise DesignError("查询引用了未授权的数据", "forbidden", 403)
            policies.append({"table": table, **asdict(policy)})
        queries.append(sql)
        bindings.append(binding)
    dictionaries = {table: execute_query(f"SELECT * FROM {table} WHERE datasource_id=%s AND is_active=1 ORDER BY id",
                                          (scope["datasource_id"],)) for table in ("adh_metrics", "adh_dimensions")}
    fingerprint = digest({"content": doc, "queries": queries, "bindings": bindings, "policies": policies,
                          "user": user, "knowledge_bases": scope["knowledge_bases"],
                          "dictionaries": dictionaries, "target": scope["target"]})
    return scope, queries, fingerprint


def sql_view(design_id, user):
    row, live = load(design_id, user)
    require_sql_permission(live)
    scope, queries, _ = compile_design(row, live)
    return {"version": row["version"], "dialect": scope["dialect"],
            "queries": [{"key": w["key"], "sql": sql, "query_source": w["query_source"]}
                        for w, sql in zip(row["content"]["widgets"], queries)]}


def edit_sql(design_id, user, expected_version, widget_key, sql):
    from services.shared.semantics.sql_guard import bounded_query
    live = identity(user)
    require_sql_permission(live)
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 50000:
        raise DesignError("SQL 不能为空或超过长度限制")
    def mutate(doc, actor):
        scope = resolve_scope(doc, actor)
        validate_query_sources(bounded_query(sql, 500, scope["dialect"]), scope)
        widget = next((w for w in doc.get("widgets", []) if w["key"] == widget_key), None)
        if not widget:
            raise DesignError("图表不存在")
        widget.update(manual_sql=sql, query_source="raw_sql")
    return change(design_id, live, expected_version, mutate)


def preview(design_id, user, expected_version):
    # 先持久化废弃旧预览，即使后续查询失败也不能继续发布旧预览。
    changed = change(design_id, user, expected_version, lambda doc, actor: resolve_scope(doc, actor) and "designing")
    version = changed["version"]
    row, live = load(design_id, user)
    scope, queries, fingerprint = compile_design(row, live)
    from services.dataviz.services.governed_query import governed_execute
    results = []
    for widget, sql in zip(row["content"]["widgets"], queries):
        result = governed_execute(sql, scope["datasource_id"], live["user_id"], scope["workspace"], live["username"])
        results.append({"key": widget["key"], **result, "limit": 500,
                        "possibly_truncated": result.get("row_count", 0) >= 500})
    # 查询期间的权限、模型、目标变化也必须重新预览。
    if compile_design(row, live)[2] != fingerprint:
        raise DesignError("查询期间业务绑定、权限或目标发生变化，请重新预览", "preview_changed", 409)
    with transaction() as cur:
        locked, live = load(design_id, user, cur=cur)
        _version(locked, version)
        cur.execute("UPDATE adh_as_bot_dashboard_designs SET status='pending_confirmation',preview=%s,"
                    "preview_expires_at=DATE_ADD(UTC_TIMESTAMP(6), INTERVAL 10 MINUTE),updated_at=UTC_TIMESTAMP(6) WHERE id=%s",
                    (dump({"digest": fingerprint, "user_id": live["user_id"], "version": version}), design_id))
        cur.execute("SELECT UTC_TIMESTAMP(6) AS generated_at")
        generated_at = str(cur.fetchone()["generated_at"])
    return {"design": get_design(design_id, live), "charts": results, "generated_at": generated_at}


def cancel(design_id, user, expected_version):
    return change(design_id, user, expected_version, lambda doc, actor: "cancelled")


def publish(design_id, user, expected_version):
    """直执行发布：预览有效 + 摘要一致 + 目标未变才落库；菜单与功能权限码把关。

    并发/重复发布：事务内以行锁 + 状态条件更新仲裁（重复调用返回已发布结果，不重复落图）。
    """
    live = identity(user)
    from services.datamind.execution.perm_link import require_write_perm
    require_write_perm(live["user_id"], 0, "dashboard:manage", "发布仪表盘")
    row, live = load(design_id, live)
    if row["status"] == "published":
        return {"design_id": design_id, "status": "published", "result": row["result"]}
    _version(row, expected_version)
    scope, queries, fingerprint = compile_design(row, live)
    if row["status"] != "pending_confirmation" or not row["preview_valid"]:
        raise DesignError("预览过期或尚未预览，请重新预览确认", "stale_preview", 409)
    if not row["preview"] or row["preview"].get("digest") != fingerprint:
        raise DesignError("权限、口径、绑定或目标已变化，请重新预览确认", "stale_preview", 409)
    with transaction() as cur:
        locked, live = load(design_id, live, cur=cur)
        if locked["status"] == "published":
            return {"design_id": design_id, "status": "published", "result": locked["result"]}
        _version(locked, expected_version)
        if locked["status"] != "pending_confirmation" or not locked["preview_valid"]:
            raise DesignError("预览过期或审批已失效，请重新预览", "stale_preview", 409)
        if locked["preview"].get("digest") != fingerprint:
            raise DesignError("预览版本不一致", "stale_preview", 409)
        if digest(target_snapshot(locked["content"], live, scope["workspace"], cur=cur)) != digest(scope["target"]):
            raise DesignError("目标仪表盘已变化，请重新预览", "target_conflict", 409)
        # 持有目标锁后再次校验，避免等待事务锁期间沿用已撤销授权。
        if compile_design(locked, live)[2] != fingerprint:
            raise DesignError("发布前权限或业务绑定已变化", "stale_preview", 409)
        from services.dataviz.services.dashboard_service import publish_design_in_transaction
        result = publish_design_in_transaction(cur, locked["content"], queries, scope, live)
        # 状态条件更新仲裁：只有把 pending_confirmation 置为 published 的一方执行落图。
        cur.execute("UPDATE adh_as_bot_dashboard_designs SET status='published',result=%s,updated_at=UTC_TIMESTAMP(6) "
                    "WHERE id=%s AND status='pending_confirmation'", (dump(result), design_id))
        if cur.rowcount != 1:
            raise DesignError("设计状态已变化", "conflict", 409)
    from services.dataviz.services.dashboard_service import _invalidate_dashboard_cache
    _invalidate_dashboard_cache(live["user_id"])
    return {"design_id": design_id, "status": "published", "result": result}
