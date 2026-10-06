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
    """每次从共享库解析范围：AS-BOT 不持有数据源边界，范围=用户角色授权（唯一裁决层）。

    AS-BOT 必须依托用户或工作空间使用，数据源权限始终跟随用户角色（RLS/列级按真实用户施加）。
    工作空间不再绑定数据源（adh_workspace_datasources 已退役为冻结表，不被消费）。
    无唯一候选不猜源，已选源失效不静默切换；空角色授权 fail-closed（admin 同样纯角色）。
    system 工具组授权是**能力叠加**（admin 专属的系统能力面，域规则更新）：
    不屏蔽业务数据源继承，只额外提供系统资源（系统本体/系统工具/系统源 0）的访问权。
    """
    from services.authservice.services.role_service import role_service
    # fail-closed(护城河 RBAC): 空角色授权不解释为全量; admin 不 bypass, 同样纯角色裁决。
    allowed = set(role_service.get_user_allowed_datasources(ctx.user_id, ctx.workspace_id))
    if ctx.datasource_id and ctx.datasource_id not in allowed:
        raise ResourceScopeError("当前用户无权使用该数据源，请选择已授权数据源")
    if not ctx.datasource_id and len(allowed) == 1:
        ctx.datasource_id = next(iter(allowed))
    ctx.extra["bound_knowledge_base_ids"] = list(policy.as_bot.get("knowledge_base_ids") or [])
    return allowed


def validate_knowledge_binding(ids):
    """知识库权限只来自 AS-BOT；状态仍实时校验，空绑定明确拒绝。"""
    if not ids:
        raise ResourceScopeError("当前 AS-BOT 未绑定知识库")
    marks = ','.join(['%s'] * len(ids))
    rows = execute_query(f"SELECT id FROM adh_knowledge_bases WHERE id IN ({marks}) "
                         "AND status='active'", tuple(ids))
    found = {r['id'] for r in rows}
    missing, extra = set(ids) - found, found - set(ids)
    # 严格相等：缺失=绑定漂移；越界返回=IN 过滤失效(越权读)，均 fail-loud 不静默
    if missing or extra:
        raise ResourceScopeError(
            f"AS-BOT 绑定的知识库校验失败: 缺失/未启用 {sorted(missing)}，越界返回 {sorted(extra)}，请检查 AS-BOT 知识库绑定")


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
    from services.datamind.execution.tool_policy import is_system_scope
    system_scope = is_system_scope(runtime.policy)  # 能力叠加：附加系统资源面，不屏蔽业务域
    # 供 list_datasources 业务投影返回授权集内全部候选（仅名称，不含 id，守 §7）。
    ctx.extra["available_datasource_ids"] = sorted(available_sources or [])
    # 数据源作用域：角色授权集 ∪ 系统源(0，仅 system 能力时)。
    source_scope = set(available_sources or [])
    if system_scope:
        source_scope.add(0)
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
    if system_scope:
        if name in ("system_usage", "system_overview") and ctx.user_role != "admin":
            raise PermissionError("系统工具仅允许管理员")
    if not system_scope and name in ("get_metrics", "knowledge_search", "run_semantic_query", "create_data_screen", "update_data_screen_chart",
                  "execute_sql", "check_sql"):
        if not ctx.datasource_id:
            if not available_sources:
                raise ResourceScopeError("当前用户角色没有已授权数据源，请检查角色数据源授权")
            hint = "，或在工具中用 datasource 指定其一" if qualified_name.startswith("mcp__datahub_query__") else ""
            raise ResourceScopeError(f"当前范围内有多个已授权数据源，请先在会话中选择{hint}")
    design_tools = {"request_dashboard_design", "get_dashboard_design", "prepare_dashboard_design",
                    "get_business_semantics", "search_business_knowledge"}
    if name in design_tools:
        from services.dataviz.services import dashboard_design_service as designs
        designs.reject_private(args)
        if name != "request_dashboard_design":
            designs.load(args.get("design_id"), {"user_id": ctx.user_id}, int(ctx.extra.get("conversation_id") or 0))
    if name in ("create_data_screen", "update_data_screen_chart") and system_scope:
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
            if int(chart["source_id"]) == int(ctx.datasource_id or 0):
                continue  # 会话业务源内，放行
            # 系统能力附加面：系统域图表(源 0)要求对象为系统本体（kind='system'）
            if system_scope and int(chart["source_id"]) == 0:
                intent = chart.get("semantic_query")
                if isinstance(intent, str):
                    intent = json.loads(intent)
                if not isinstance(intent, dict) or not intent.get("object"):
                    raise PermissionError("系统大屏图表缺少本体对象意图")
                from services.shared.semantics.binding_resolver import resolve_binding
                binding, _ = resolve_binding(intent["object"], datasource_id=0)
                validate_binding(ctx, binding)
                continue
            raise PermissionError("大屏包含当前 AS-BOT 未授权的数据源")
    if name == "query_by_tags":
        # 注入授权数据源域(用户角色授权集)供 handler 做资源域隔离; 不进 LLM 视野。
        ctx.extra["tag_authorized_datasource_ids"] = sorted(int(s) for s in (available_sources or set()))
    if name == "knowledge_search":
        validate_knowledge_binding(ctx.extra["bound_knowledge_base_ids"])
    if name in ("generate_ontology_draft", "save_ontology_model", "activate_ontology_model", "import_ontology_yaml"):
        # 本体写操作限**系统本体域**（as-bot-system-waker §4）；动作权限由菜单与功能
        # 权限码在工具 handler 内把关（ontology:generate/save/activate/import）。
        # 域判定显式覆盖两种寻址方式（model_id / datasource_id）——
        # 不依赖 _check_source 或 handler 的副作用碰巧挡住，那是意外正确，
        # schema 一变就会漏（历史就漏了 datasource_id 寻址的两个工具）。
        if args.get("model_id"):
            model = execute_query("SELECT kind FROM adh_ontology_models WHERE id=%s", (args["model_id"],), fetchone=True)
            if model is None or str(model.get("kind") or "") != "system":
                raise PermissionError("AS-BOT 不能操作业务本体")
        if args.get("datasource_id") not in (None, "", 0, "0"):
            raise PermissionError("AS-BOT 只能写系统本体，不接受业务数据源标识")
    return dict(args)


def validate_binding(ctx, binding):
    runtime = (getattr(ctx, "extra", None) or {}).get("secure_runtime") if ctx else None
    if runtime is None:
        return
    if binding is None:
        raise PermissionError("对象未绑定到当前 AS-BOT 资源域")
    from services.datamind.execution.tool_policy import is_system_scope
    # 能力叠加：system 能力额外放行系统本体(kind='system')；业务绑定按会话源裁决。
    if is_system_scope(runtime.policy):
        row = execute_query("SELECT kind FROM adh_ontology_models WHERE id=%s",
                            (binding.model_id,), fetchone=True)
        if row is not None and str(row.get("kind") or "") == "system":
            return
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
    """在角色授权集内按业务名解析 datasource_id。

    数据源 name 全局唯一(uk_datasource_name)，至多一命中；未命中抛可操作错误(附候选名)供 LLM 问用户。
    """
    ids = [int(i) for i in (available_sources or [])]
    if not ids:
        raise ResourceScopeError("当前会话没有已授权数据源，请检查用户角色授权")
    marks = ",".join(["%s"] * len(ids))
    rows = execute_query(f"SELECT id, name FROM adh_datasources WHERE id IN ({marks})", tuple(ids))
    for r in rows:
        if (r.get("name") or "") == name:
            return int(r["id"])
    available_names = "、".join(sorted({(r.get("name") or "") for r in rows if r.get("name")}))
    raise ResourceScopeError(
        f"未找到已授权数据源「{name}」；可用数据源：{available_names or '无'}。若不确定用哪个，请询问用户。")
