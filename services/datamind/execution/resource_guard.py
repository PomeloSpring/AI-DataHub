"""工具资源的服务端身份边界；工具授权不等于数据源或工作空间授权。"""
import json

from services.shared.common.db import execute_query

IDENTITY_FIELDS = {"user_id", "username", "workspace_id", "decided_by", "proposed_by", "user_role"}


def reject_identity(value):
    if isinstance(value, dict):
        if IDENTITY_FIELDS & value.keys():
            raise PermissionError("工具不能指定执行身份或工作空间")
        for v in value.values():
            reject_identity(v)
    elif isinstance(value, list):
        for v in value:
            reject_identity(v)


class ResourceScopeError(PermissionError):
    """可直接提示用户的资源选择错误；不得包含物理标识或原始异常。"""


def bind_resources(ctx, policy):
    """每次从共享库解析范围：空 Waker 源配置继承工作空间，非空只收窄。

    用户权限仍取交集；无唯一候选不猜源，已选源失效不静默切换。
    系统助手的默认域独立，不继承业务数据源。
    """
    if policy.waker["waker_key"] == "__system_bot__":
        ctx.datasource_id = 0
        allowed = set()
    else:
        configured = set(policy.waker.get("datasource_ids") or [])
        rows = execute_query(
            "SELECT b.datasource_id FROM adh_workspace_datasources b "
            "JOIN adh_datasources d ON d.id=b.datasource_id WHERE b.workspace_id=%s",
            (ctx.workspace_id,),
        )
        workspace_sources = {r["datasource_id"] for r in rows}
        if ctx.datasource_id:
            if configured and ctx.datasource_id not in configured:
                raise ResourceScopeError("当前数据源不在 Waker 指定范围内，请选择已授权数据源")
            if ctx.datasource_id not in workspace_sources:
                raise ResourceScopeError("当前数据源未绑定到工作空间或绑定已撤回，请重新选择")
        allowed = workspace_sources & configured if configured else workspace_sources
        if ctx.user_role != "admin":
            from services.authservice.services.role_service import role_service
            # fail-closed(护城河 RBAC): 空角色权限不解释为全量, 与普通用户取交集即收窄。
            allowed &= set(role_service.get_user_allowed_datasources(ctx.user_id, ctx.workspace_id))
            if ctx.datasource_id and ctx.datasource_id not in allowed:
                raise ResourceScopeError("当前用户无权使用该数据源，请选择已授权数据源")
        if not ctx.datasource_id and len(allowed) == 1:
            ctx.datasource_id = next(iter(allowed))
    ctx.extra["bound_knowledge_base_ids"] = list(policy.waker.get("knowledge_base_ids") or [])
    return allowed


def validate_knowledge_binding(ids):
    """知识库权限只来自 Waker；状态仍实时校验，空绑定明确拒绝。"""
    if not ids:
        raise ResourceScopeError("当前 Waker 未绑定知识库")
    marks = ','.join(['%s'] * len(ids))
    rows = execute_query(f"SELECT id FROM adh_knowledge_bases WHERE id IN ({marks}) "
                         "AND status='active'", tuple(ids))
    if {r['id'] for r in rows} != set(ids):
        raise ResourceScopeError("知识库不存在或未启用，请检查 Waker 知识库绑定")


def validate_tool_resources(ctx, qualified_name, args):
    if not isinstance(args, dict):
        raise ValueError("工具参数必须是对象")
    runtime = ctx.extra["secure_runtime"]
    # 文件与自定义工具参数是内容数据，不能把文件正文解析成权限参数。
    name = qualified_name.rsplit("__", 1)[-1]
    if qualified_name.startswith("mcp__datahub_workspace__"):
        return dict(args)
    reject_identity(args)
    if qualified_name.startswith("mcp__external_"):
        return dict(args)
    available_sources = bind_resources(ctx, runtime.policy)
    is_system_bot = ctx.extra.get("waker_key") == "__system_bot__"
    # 供 list_datasources 业务投影返回授权集内全部候选（仅名称，不含 id，守 §7）。
    if not is_system_bot:
        ctx.extra["available_datasource_ids"] = sorted(available_sources or [])
    # 数据源作用域：以三方授权集 available_sources（工作空间∩Waker角色）为准，系统助手域只允许系统源(0)。
    if is_system_bot:
        source_scope = {0}
    else:
        source_scope = set(available_sources or [])
        if ctx.datasource_id:
            source_scope.add(int(ctx.datasource_id))
    # 查询工具：LLM 用业务名在授权集内选源（不持有 id）。解析 name→id 作为生效源；
    # 丢弃任何模型传入/历史残留的 datasource_id，一律以服务端解析/会话源为准注入。
    if qualified_name.startswith("mcp__datahub_query__") and name in ("execute_sql", "check_sql"):
        chosen_name = args.get("datasource")
        args = {k: v for k, v in args.items() if k not in ("datasource", "datasource_id")}
        if isinstance(chosen_name, str) and chosen_name.strip():
            ctx.datasource_id = _resolve_authorized_source(chosen_name.strip(), available_sources)
            source_scope.add(int(ctx.datasource_id))
        if ctx.datasource_id:
            args = {**args, "datasource_id": ctx.datasource_id}
    for value in args.values():
        if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except ValueError:
                continue
            reject_identity(parsed)
            _check_source(parsed, source_scope)
    _check_source(args, source_scope)
    if is_system_bot:
        if ctx.user_role != "admin":
            raise PermissionError("系统工具仅允许管理员")
    elif name in ("get_metrics", "knowledge_search", "run_semantic_query", "create_data_screen", "update_data_screen_chart",
                  "execute_sql", "check_sql"):
        if not ctx.datasource_id:
            if not available_sources:
                raise ResourceScopeError("当前工作空间与 Waker 限定范围内没有已授权数据源，请检查绑定和用户权限")
            hint = "，或在工具中用 datasource 指定其一" if qualified_name.startswith("mcp__datahub_query__") else ""
            raise ResourceScopeError(f"当前范围内有多个已授权数据源，请先在会话中选择{hint}")
    design_tools = {"request_dashboard_design", "get_dashboard_design", "prepare_dashboard_design",
                    "get_business_semantics", "search_business_knowledge"}
    if name in design_tools:
        from services.dataviz.services import dashboard_design_service as designs
        if ctx.extra.get("waker_key") != "__system_bot__":
            raise PermissionError("设计工具仅允许系统助手使用")
        designs.reject_private(args)
        if name != "request_dashboard_design":
            designs.load(args.get("design_id"), {"user_id": ctx.user_id}, int(ctx.extra.get("conversation_id") or 0))
    if name in ("create_data_screen", "update_data_screen_chart") and ctx.extra.get("waker_key") == "__system_bot__":
        from services.dataviz.services.dashboard_design_service import reject_private
        reject_private(args)
        # 旧入口仅提出设计，不读取业务图表或写正式对象。
        return dict(args)
    if name in ("get_data_screen", "update_data_screen_chart"):
        dashboard = execute_query("SELECT owner_id,workspace_id,is_public FROM adh_dashboards WHERE id=%s",
                                  (int(args.get("dashboard_id") or 0),), fetchone=True)
        if not dashboard or int(dashboard.get("workspace_id") or 0) != ctx.workspace_id:
            raise PermissionError("大屏不存在或不属于当前工作空间")
        if dashboard["owner_id"] != ctx.user_id and (name == "update_data_screen_chart" or not dashboard.get("is_public")):
            raise PermissionError("未授权访问或修改该大屏")
        charts = execute_query("SELECT source_id,semantic_query FROM adh_charts WHERE dashboard_id=%s",
                               (int(args["dashboard_id"]),))
        for chart in charts:
            if not chart.get("source_id"):
                continue
            if ctx.extra.get("waker_key") == "__system_bot__":
                intent = chart.get("semantic_query")
                if isinstance(intent, str):
                    intent = json.loads(intent)
                if not isinstance(intent, dict) or not intent.get("object"):
                    raise PermissionError("系统助手仅能访问系统本体大屏")
                from services.shared.semantics.binding_resolver import resolve_binding
                binding, _ = resolve_binding(intent["object"], datasource_id=0)
                validate_binding(ctx, binding)
            elif int(chart["source_id"]) != ctx.datasource_id:
                raise PermissionError("大屏包含当前 Waker 未授权的数据源")
    if name == "query_by_tags":
        # 注入授权数据源域(workspace∩waker∩role 交集)供 handler 做资源域隔离; 不进 LLM 视野。
        ctx.extra["tag_authorized_datasource_ids"] = sorted(int(s) for s in (available_sources or set()))
    if name == "knowledge_search":
        validate_knowledge_binding(ctx.extra["bound_knowledge_base_ids"])
    if name in ("generate_ontology_draft", "save_ontology_model", "activate_ontology_model", "import_ontology_yaml"):
        if ctx.extra.get("waker_key") != "__system_bot__":
            raise PermissionError("本体写操作必须经系统助手审批")
        if args.get("model_id"):
            model = execute_query("SELECT datasource_id FROM adh_ontology_models WHERE id=%s", (args["model_id"],), fetchone=True)
            if model is None or int(model.get("datasource_id") or 0) > 0:
                raise PermissionError("系统助手不能操作业务本体")
    return dict(args)


def validate_binding(ctx, binding):
    runtime = (getattr(ctx, "extra", None) or {}).get("secure_runtime") if ctx else None
    if runtime is None:
        return
    if binding is None:
        raise PermissionError("对象未绑定到当前 Waker 资源域")
    if runtime.policy.waker["waker_key"] == "__system_bot__":
        row = execute_query("SELECT datasource_id FROM adh_ontology_models WHERE id=%s",
                            (binding.model_id,), fetchone=True)
        if row is None or int(row.get("datasource_id") or 0) > 0:
            raise PermissionError("系统助手仅允许查询系统本体")
    else:
        if not ctx.datasource_id or binding.datasource_id != ctx.datasource_id:
            raise ResourceScopeError("对象不属于当前会话选择的数据源")
        bind_resources(ctx, runtime.policy)


def _check_source(value, allowed_sources):
    if isinstance(value, dict):
        if "datasource_id" in value and value["datasource_id"] is not None:
            try:
                source = value["datasource_id"]
                if isinstance(source, bool) or not isinstance(source, (int, str)):
                    raise ValueError("无效标识")
                source = int(source)
            except (TypeError, ValueError) as exc:
                raise ResourceScopeError("工具数据源参数无效，请使用当前会话的数据源") from exc
            if source not in allowed_sources:
                raise ResourceScopeError("工具指定的数据源不在当前会话已授权范围内，请选择已授权数据源")
        for v in value.values():
            _check_source(v, allowed_sources)
    elif isinstance(value, list):
        for v in value:
            _check_source(v, allowed_sources)


def _resolve_authorized_source(name, available_sources):
    """在授权集(工作空间∩Waker∩角色)内按业务名解析 datasource_id。

    数据源 name 全局唯一(uk_datasource_name)，至多一命中；未命中抛可操作错误(附候选名)供 LLM 问用户。
    """
    ids = [int(i) for i in (available_sources or [])]
    if not ids:
        raise ResourceScopeError("当前会话没有已授权数据源，请检查工作空间绑定与用户权限")
    marks = ",".join(["%s"] * len(ids))
    rows = execute_query(f"SELECT id, name FROM adh_datasources WHERE id IN ({marks})", tuple(ids))
    for r in rows:
        if (r.get("name") or "") == name:
            return int(r["id"])
    available_names = "、".join(sorted({(r.get("name") or "") for r in rows if r.get("name")}))
    raise ResourceScopeError(
        f"未找到已授权数据源「{name}」；可用数据源：{available_names or '无'}。若不确定用哪个，请询问用户。")
