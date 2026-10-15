"""L2 真实语义层适配器：intent → 真实 binding → plan → 闸门（纯判定）。

与 L1（``compile``）的根本差别
------------------------------
``compile`` 把 ``_ColumnResolver._load_*`` 整体打桩成内存 fixture，测的是
**planner 规则对 mock 目录的正确性**；它 72/72 绿，不能证明本平台真实本体、
真实字典、真实治理配置有任何一处是对的。

本适配器**不打桩**：走真实 ``adh_ontology_bindings`` / 字典 / 敏感基线 / RLS 配置，
因此它回答的是「用户这句话在本平台真实语义资产上会被编译成什么、权限怎么被施加」。
这正是自进化闭环需要的验证层——反馈说"这题答错了"，只有在这一层才能回归验证。

安全边界（三条，缺一即视为缺陷）
--------------------------------
1. **不下发数据源**：只跑到 ``gates.run_gates``（gate 1..6 的改写部分）为止。
   该函数自身注释即"纯判定, 不在此执行真实查询"，所以本适配器**不产生数据行**，
   不违反护栏 §1 的统一取数入口（压根没取数）。
2. **不落审计**：审计只应记录真实用户调用（护栏 §9）。评测用 user 身份跑的是
   判定段，若落审计会污染权限台账，让"谁查过什么"混进机器产生的假记录。
3. **物理标识只用于服务端断言，不外发**：本层断言治理效果时必须引用物理列名
   （不引用就无法验证 mask/block 是否真生效）。这些标识只存在于服务内存与本层
   失败原因里，读取通道是 ``/api/eval*``（JWT + ``eval:read`` 权限码 +
   ``ai_access='none'``），**永不**进 LLM 视野、SSE、聊天历史或普通客户端响应。
   ``sources`` 分桶一律只放业务可读的档位/闸门名，不放表列名。

身份与数据源按业务名声明
------------------------
``payload.datasource_name``（全局唯一 ``uk_datasource_name``）与
``payload.identity.username`` 由服务端解析成内部 id，与
``waker-datasource-domain`` §2 同口径：不要求用例作者记内部 id。
解析不到时**显式报错**，不得静默按 id=0 跑——那样 RLS 段会被跳过，
一条"看起来验证了行级权限"的用例其实什么都没验。
"""

from __future__ import annotations

from backend.eval.contract import Case, CaseContractError

SOURCES_LABEL = "真实语义层归因（解析档位 + 闸门落点 + 治理是否真被验证）"

#: 闸门落点 → 对外可读的分桶名（不含物理表列）
_GATE_BUCKET = {
    "allowed": "gate_allowed",
    "identity": "gate_blocked_identity",
    "permission": "gate_blocked_permission",
    "preflight": "gate_blocked_preflight",
    "proposal": "gate_proposal",
    "approval": "gate_needs_approval",
    "rls": "gate_blocked_rls_rewrite_failed",
    "execute": "gate_blocked_execute",
}


def _resolve_datasource_id(name: str) -> int:
    """datasource_name → id（name 是全局唯一标识，删重建后仍可对上）。"""
    from backend.common.db.datasource_db import get_datasource_by_name

    ds = get_datasource_by_name(name) or {}
    ds_id = int(ds.get("id") or 0)
    if not ds_id:
        raise CaseContractError(
            f"payload.datasource_name={name!r} 在本平台查不到数据源；"
            f"用例必须引用真实存在的数据源名，不得靠内部 id 猜值")
    return ds_id


def _resolve_identity(payload: dict) -> tuple[dict, bool]:
    """把 ``payload.identity`` 解析成 gates 需要的 user_context。

    Returns (user_context, identity_declared)：``identity_declared=False`` 表示用例
    没给身份，此时 RLS/列限制段不会被执行，治理类断言必须被契约层拒（见
    ``contract.check_semantic`` 的 ``governance_verified`` 约束），否则就是假验证。
    """
    ident = payload.get("identity") or {}
    user_id = int(ident.get("user_id") or 0)
    username = str(ident.get("username") or "").strip()

    if not user_id and username:
        from backend.modules.auth.services.auth_service import get_user_by_username

        row = get_user_by_username(username) or {}
        user_id = int(row.get("id") or 0)
        if not user_id:
            raise CaseContractError(
                f"payload.identity.username={username!r} 查不到用户；"
                f"身份解析不到不得回退成 user_id=0（那会把 RLS 段跳过去冒充已验证）")

    workspace_id = int(ident.get("workspace_id") or 0)
    ctx = {
        "user_id": user_id,
        "username": username or str((payload.get("identity") or {}).get("username") or ""),
        "workspace_id": workspace_id,
    }
    return ctx, bool(user_id)


def run_case(case: Case) -> dict:
    """跑一条真实语义层用例，返回 ``observed`` 供 ``contract.check_semantic`` 比对。"""
    payload = case.payload or {}
    observed: dict = {
        "intent_rejected": False, "bound": False, "allowed": False,
        "sources": {}, "warnings": [], "applied_rls": [], "masked_columns": [],
        "gate_trail": [],
    }

    # 延迟导入：与本包其它适配器一致，被测链路的 import 成本只在实际跑评测时产生
    from backend.semantics.binding_resolver import resolve_binding
    from backend.semantics.gates import run_gates
    from backend.semantics.intent import parse_intent
    from backend.semantics.planner import plan

    intent = payload.get("intent")
    if not isinstance(intent, dict) or not intent:
        raise CaseContractError("payload.intent 必须是非空对象（本层从 intent 起步，不经 NL→intent）")

    q, err, notes = parse_intent(dict(intent))
    if err:
        observed["intent_rejected"] = True
        observed["parse_error"] = str(err)
        observed["intent_notes"] = list(notes or [])
        observed["sources"] = {"gate_bucket": "intent_rejected", "resolution_source": ""}
        return observed
    observed["intent_notes"] = list(notes or [])

    ds_name = str(payload.get("datasource_name") or "").strip()
    if ds_name:
        q = q.model_copy(update={"datasource_id": _resolve_datasource_id(ds_name)})
    user_context, identity_declared = _resolve_identity(payload)
    if user_context.get("user_id"):
        q = q.model_copy(update={"user_id": user_context["user_id"]})
    if user_context.get("workspace_id"):
        q = q.model_copy(update={"workspace_id": user_context["workspace_id"]})

    binding, bind_warnings = resolve_binding(
        q.object, datasource_id=int(q.datasource_id or 0))
    observed["bind_warnings"] = [str(w) for w in (bind_warnings or [])]
    observed["bound"] = binding is not None
    observed["bound_object_key"] = str(q.object or "")
    if binding is None:
        observed["sources"] = {"gate_bucket": "unbound", "resolution_source": "",
                               "governance_bucket": "not_reached"}
        observed["warnings"] = observed["bind_warnings"]
        return observed

    observed["resolution_source"] = str(binding.resolution_source or "")
    observed["bind_kind"] = str(binding.bind_kind or "")
    observed["sync_state"] = str(binding.sync_state or "")
    observed["datasource_name"] = str(binding.datasource_name or "")

    planned = plan(q, binding)
    if planned is None:
        observed["sources"] = {"gate_bucket": "plan_failed",
                               "resolution_source": observed["resolution_source"],
                               "governance_bucket": "not_reached"}
        observed["warnings"] = observed["bind_warnings"] + ["plan() 返回空"]
        return observed

    observed["sql"] = str(planned.sql or "")
    observed["warnings"] = observed["bind_warnings"] + [str(w) for w in (planned.warnings or [])]
    observed["resolved_tables_count"] = len(planned.resolved_tables or [])
    prov = dict(planned.provenance or {})
    observed["resolution_sources"] = dict(prov.get("resolution_sources") or {})
    observed["unresolved_terms"] = list(prov.get("unresolved_terms") or [])

    exec_res = run_gates(q, binding, planned, user_context)
    observed["allowed"] = bool(exec_res.allowed)
    observed["blocked_at"] = str(exec_res.blocked_at or "")
    observed["block_reason"] = str(exec_res.reason or "")
    observed["base_sql"] = str(exec_res.base_sql or "")
    observed["secured_sql"] = str(exec_res.secured_sql or "")
    observed["applied_rls"] = [str(x) for x in (exec_res.applied_rls or [])]
    observed["masked_columns"] = [str(x) for x in (exec_res.masked_columns or [])]
    observed["gate_trail"] = [str(g.get("gate") or "") for g in (exec_res.gate_trail or [])]
    observed["needs_approval"] = bool(exec_res.needs_approval)
    observed["blocked_by_policy"] = any(
        (g.get("detail") or {}).get("blocked_by_policy")
        for g in (exec_res.gate_trail or []) if isinstance(g, dict))
    # 提案/审批产物只做布尔呈现，不回了 edit 内容（可能含 SQL 片段）
    observed["has_proposed_edit"] = bool(exec_res.proposed_edit)

    gprov = dict(exec_res.provenance or {})
    observed["rls_policies"] = [str(x) for x in (gprov.get("rls_policies") or [])]

    gate_bucket = ("gate_allowed" if exec_res.allowed
                   else _GATE_BUCKET.get(observed["blocked_at"],
                                         f"gate_blocked_{observed['blocked_at'] or 'unknown'}"))
    # 治理是否**真的被验证**：user_id 为空时 gates 会跳过 RLS/列限制段，
    # 必须标 unverified 而不是 none —— 否则用例会把自己伪装成验证过行级权限。
    if not identity_declared:
        governance_bucket = "unverified_no_identity"
    elif not observed["resolved_tables_count"]:
        governance_bucket = "unverified_no_table"
    elif observed["applied_rls"] or observed["masked_columns"]:
        governance_bucket = "applied"
    else:
        governance_bucket = "checked_none_applied"

    observed["governance_bucket"] = governance_bucket
    observed["sources"] = {
        "gate_bucket": gate_bucket,
        "resolution_source": observed["resolution_source"],
        "governance_bucket": governance_bucket,
    }
    return observed
