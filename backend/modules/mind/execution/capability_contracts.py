"""平台动作的单一契约组装与漂移校验。

功能动作以 ``function_tools.FUNCTION_ACTION_SPECS`` 为真源，本体工具以
``ontology_tools.TOOL_SPECS`` 为真源；系统本体目录只消费这里生成的契约。
``executor_ref`` 是稳定引用，canonical JSON 不保存 Handler、import 路径或连接信息。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any


_PERM_CODE_RE = re.compile(r"^[a-z][a-z0-9_-]*(:[a-z][a-z0-9_-]*)+$")
_DATA_ENGINEERING_PREFIXES = ("sync:", "dag:", "udf:")


MCP_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["type", "text"],
            },
        },
        "isError": {"type": "boolean"},
    },
    "required": ["content"],
}

HUMAN_RESULT_SCHEMA: dict[str, Any] = {"type": "object"}


# 人工动作没有 AI executor，但必须明确唯一人工入口；入口引用不承载凭据或内部对象。
HUMAN_ACTION_SPECS: dict[str, dict[str, Any]] = {
    "dashboard.publish": {
        "tool_name": "",
        "label": "发布看板",
        "description": "完成治理预览与摘要确认后，在看板设计页发布。",
        "perm_code": "dashboard:manage",
        "object_key": "dashboard",
        "read_only": False,
        "input_schema": {
            "type": "object",
            "properties": {
                "design_id": {"type": "string", "description": "看板设计业务标识"},
                "expected_version": {"type": "integer", "description": "预览时的版本号"},
            },
            "required": ["design_id", "expected_version"],
        },
        "human_entry_ref": "http:POST /api/as-bot/dashboard-designs/{design_id}/publish",
    },
    "product.contract_change": {
        "tool_name": "",
        "label": "变更数据产品契约",
        "description": "在数据产品页确认影响面后应用契约版本。",
        "perm_code": "dataset:manage",
        "object_key": "data_product",
        "read_only": False,
        "input_schema": {
            "type": "object",
            "properties": {
                "product_name": {"type": "string", "description": "数据产品业务名"},
                "schema_version": {"type": "integer", "description": "待应用的契约版本"},
            },
            "required": ["product_name", "schema_version"],
        },
        "human_entry_ref": "page:data_product_contract",
    },
    "content_review.publish": {
        "tool_name": "",
        "label": "发布内容",
        "description": "审核通过后，由发布人在内容中心发布上线。",
        "perm_code": "article:publish",
        "object_key": "content_article",
        "read_only": False,
        "input_schema": {
            "type": "object",
            "properties": {
                "ident": {"type": "string", "description": "内容条目 slug 或内部标识"},
                "action": {"type": "string", "enum": ["publish"]},
            },
            "required": ["ident"],
        },
        "human_entry_ref": "http:POST /api/content/publish/{ident}",
    },
    "alias.approve": {
        "tool_name": "",
        "label": "采纳别名建议",
        "description": "在进化中心确认候选后收编为既有对象或字典别名。",
        "perm_code": "ontology:save",
        "object_key": "alias_suggestion",
        "read_only": False,
        "input_schema": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "integer"},
                "target_type": {"type": "string", "enum": ["metric", "dimension", "object"]},
                "target_ref": {"type": "string", "description": "目标业务名"},
            },
            "required": ["candidate_id", "target_type", "target_ref"],
        },
        "human_entry_ref": "http:POST /api/as-bot/alias-suggestions/{candidate_id}/approve",
    },
    "alias.reject": {
        "tool_name": "",
        "label": "驳回别名建议",
        "description": "在进化中心驳回候选并留下人工理由。",
        "perm_code": "ontology:save",
        "object_key": "alias_suggestion",
        "read_only": False,
        "input_schema": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "integer"},
                "note": {"type": "string", "description": "驳回理由"},
            },
            "required": ["candidate_id"],
        },
        "human_entry_ref": "http:POST /api/as-bot/alias-suggestions/{candidate_id}/reject",
    },
}


# 进化动作的第一道权限来自功能注册表；领域写权限仍由服务/Handler 再裁决一次。
_EVOLUTION_DOMAIN_PERMS: dict[str, tuple[str, ...]] = {
    "evolution_candidate.adopt": ("ontology:save",),
    "evolution_candidate.reject": ("ontology:save",),
    "evolution_candidate.to_term": ("ontology:save",),
    "evolution_metric.create": ("metrics:manage",),
    "evolution_dimension.create": ("metrics:manage",),
    "evolution_proposal.applied": ("knowledge:manage",),
    "evolution_proposal.reject": ("knowledge:manage",),
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def contract_digest(value: Any) -> str:
    """确定性 SHA-256；注册表或 Schema 任一变化都会得到不同指纹。"""
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _json_schema(raw: Any) -> dict[str, Any]:
    from backend.modules.mind.execution.sdk_tools.compat import to_json_schema

    return copy.deepcopy(to_json_schema(raw or {}))


def _timeout(action_key: str, read_only: bool) -> int:
    if action_key == "evolution_digest.run":
        return 300_000
    if action_key in {"ontology.publish", "ontology.import_yaml", "ontology.generate_material"}:
        return 180_000
    return 30_000 if read_only else 120_000


def _base_contract(action_key: str, spec: dict[str, Any], *, source: str,
                   channel: str, executor_ref: str, qualified_name: str = "") -> dict[str, Any]:
    perm_code = str(spec.get("perm_code") or "").strip()
    read_only = bool(spec.get("read_only", spec.get("readonly", False)))
    additional = [p for p in _EVOLUTION_DOMAIN_PERMS.get(action_key, ()) if p != perm_code]
    required_perms = [perm_code] + additional
    error_codes = ["SCHEMA_INVALID", "PERMISSION_DENIED", "TIMEOUT", "EXECUTION_FAILED"]
    if not read_only:
        error_codes.append("IDEMPOTENCY_CONFLICT")
    if channel == "human":
        error_codes.append("HUMAN_ACTION_REQUIRED")
    out: dict[str, Any] = {
        "action_key": action_key,
        "key": action_key,
        "label": str(spec.get("label") or action_key),
        "description": str(spec.get("description") or ""),
        "object_key": str(spec.get("object_key") or ""),
        "source_registry": source,
        "executor_ref": executor_ref,
        "qualified_name": qualified_name,
        "tool_name": str(spec.get("tool_name") or spec.get("name") or ""),
        "input_schema": _json_schema(spec.get("schema") or spec.get("input_schema")),
        "output_schema": copy.deepcopy(MCP_RESULT_SCHEMA if channel == "ai" else HUMAN_RESULT_SCHEMA),
        "perm_code": perm_code,
        "code": perm_code,
        "required_perm_codes": required_perms,
        "channel": channel,
        "read_only": read_only,
        "error_codes": error_codes,
        "idempotency": str(spec.get("idempotency") or "none"),
        "timeout_ms": int(spec.get("timeout_ms") or _timeout(action_key, read_only)),
        "auto_executable": channel == "ai" and not perm_code.startswith(_DATA_ENGINEERING_PREFIXES),
    }
    if channel == "human":
        out["human_entry_ref"] = str(spec.get("human_entry_ref") or "")
    return out


def _validate_contracts(contracts: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    executors: dict[str, str] = {}
    tool_names: dict[str, str] = {}
    try:
        from jsonschema import Draft7Validator
    except ImportError as exc:  # pragma: no cover - 依赖缺失必须显式失败
        return [f"jsonschema 不可用: {exc}"]

    for key, contract in contracts.items():
        if not key or key != contract.get("action_key"):
            errors.append(f"动作 key 不稳定: {key!r}")
        channel = contract.get("channel")
        if channel not in ("ai", "human"):
            errors.append(f"{key} 的执行通道非法: {channel!r}")
        perm_code = str(contract.get("perm_code") or "")
        if not _PERM_CODE_RE.fullmatch(perm_code):
            errors.append(f"{key} 的权限码非法: {perm_code!r}")
        for extra in contract.get("required_perm_codes") or []:
            if not _PERM_CODE_RE.fullmatch(str(extra)):
                errors.append(f"{key} 的附加权限码非法: {extra!r}")
        if not isinstance(contract.get("read_only"), bool):
            errors.append(f"{key} 的 read_only 必须是布尔值")
        if int(contract.get("timeout_ms") or 0) <= 0:
            errors.append(f"{key} 的 timeout_ms 必须为正整数")
        if contract.get("idempotency") not in ("none", "required"):
            errors.append(f"{key} 的 idempotency 必须是 none/required")
        if not contract.get("error_codes"):
            errors.append(f"{key} 未声明 error_codes")
        for field in ("input_schema", "output_schema"):
            schema = contract.get(field)
            if not isinstance(schema, dict):
                errors.append(f"{key} 的 {field} 不是对象 Schema")
                continue
            try:
                Draft7Validator.check_schema(schema)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{key} 的 {field} 非法: {exc}")
        props = (contract.get("input_schema") or {}).get("properties") or {}
        required = (contract.get("input_schema") or {}).get("required") or []
        if not isinstance(props, dict) or any(name not in props for name in required):
            errors.append(f"{key} 的 input_schema.required 引用了不存在的属性")
        executor_ref = str(contract.get("executor_ref") or "")
        if channel == "ai":
            if not executor_ref:
                errors.append(f"{key} 是 AI 动作但缺 executor_ref")
            if executor_ref in executors:
                errors.append(f"executor_ref 不唯一: {executor_ref} 同时属于 {executors[executor_ref]} 与 {key}")
            executors[executor_ref] = key
            if contract.get("human_entry_ref"):
                errors.append(f"{key} 是 AI 动作却声明了人工入口")
        else:
            if executor_ref:
                errors.append(f"{key} 是人工动作却绑定了 AI executor")
            if not str(contract.get("human_entry_ref") or "").strip():
                errors.append(f"{key} 是人工动作但缺 human_entry_ref")
            if contract.get("auto_executable") is not False:
                errors.append(f"{key} 是人工动作但 auto_executable 不为 false")
        tool_name = str(contract.get("tool_name") or "")
        if channel == "ai":
            if not tool_name:
                errors.append(f"{key} 是 AI 动作但缺 tool_name")
            elif tool_name in tool_names:
                errors.append(f"工具名不唯一: {tool_name} 同时属于 {tool_names[tool_name]} 与 {key}")
            tool_names[tool_name] = key
        if perm_code.startswith(_DATA_ENGINEERING_PREFIXES) and contract.get("auto_executable"):
            errors.append(f"{key} 属数据工程域，不得进入 AI 可执行集合")
    return errors


def build_action_contracts() -> dict[str, dict[str, Any]]:
    """从真实注册表组装可序列化契约；不把 Handler 写进 canonical doc。"""
    from backend.modules.mind.execution.function_tools import FUNCTION_ACTION_SPECS
    from backend.modules.mind.execution.sdk_tools.ontology_tools import TOOL_SPECS

    contracts: dict[str, dict[str, Any]] = {}
    for action_key, spec in FUNCTION_ACTION_SPECS.items():
        if action_key in contracts:
            raise ValueError(f"动作 key 重复: {action_key}")
        contracts[action_key] = _base_contract(
            action_key, spec, source="function_tools", channel="ai",
            executor_ref=f"function:{spec['tool_name']}",
            qualified_name=f"mcp__datahub_functions__{spec['tool_name']}",
        )

    for spec in TOOL_SPECS:
        action_key = str(spec.get("action_key") or "").strip()
        if not action_key:
            raise ValueError(f"本体工具未声明 action_key: {spec.get('name')}")
        if action_key in contracts:
            raise ValueError(f"动作 key 重复: {action_key}")
        name = str(spec.get("name") or "")
        contracts[action_key] = _base_contract(
            action_key, spec, source="ontology_tools", channel="ai",
            executor_ref=f"ontology:{name}",
            qualified_name=f"mcp__datahub_ontology__{name}",
        )

    for action_key, spec in HUMAN_ACTION_SPECS.items():
        if action_key in contracts:
            raise ValueError(f"动作 key 重复: {action_key}")
        contracts[action_key] = _base_contract(
            action_key, spec, source="human", channel="human", executor_ref="",
        )

    errors = _validate_contracts(contracts)
    if errors:
        raise ValueError("动作契约自检失败: " + "; ".join(errors))
    for contract in contracts.values():
        contract["contract_digest"] = contract_digest(contract)
    return contracts


def get_executor_registry() -> dict[str, dict[str, Any]]:
    """返回仅供服务端使用的真实 executor；按 action key 精确索引。"""
    from backend.modules.mind.execution.function_tools import FUNCTION_ACTION_SPECS
    from backend.modules.mind.execution.sdk_tools.ontology_tools import TOOL_SPECS

    out: dict[str, dict[str, Any]] = {}
    for action_key, spec in FUNCTION_ACTION_SPECS.items():
        if action_key in out:
            raise ValueError(f"动作 executor 重复: {action_key}")
        out[action_key] = {
            "handler": spec["handler"],
            "qualified_name": f"mcp__datahub_functions__{spec['tool_name']}",
            "async_handler": False,
        }
    for spec in TOOL_SPECS:
        action_key = str(spec.get("action_key") or "")
        if not action_key or action_key in out:
            raise ValueError(f"本体工具 action_key 缺失或重复: {action_key!r}")
        out[action_key] = {
            "handler": spec["handler"],
            "qualified_name": f"mcp__datahub_ontology__{spec['name']}",
            "async_handler": True,
        }
    return out


def resolve_executor(action_key: str) -> dict[str, Any]:
    """精确解析，禁止模糊匹配或静默回退。"""
    key = str(action_key or "")
    executor = get_executor_registry().get(key)
    if executor is None:
        raise KeyError(f"动作未注册 AI executor: {key}")
    return executor


#: 动作 → 能力域映射（发现清单的语义分组，只引导选择、不做权限判断）。
#: 域标签回答“这是系统能力还是数据分析”，缩小 LLM 第一跳的选择面；
#: 权限裁决仍由 check_tool/require_write_perm 按权限码完成，与域无关。
_ACTION_DOMAINS: tuple[tuple[str, str], ...] = (
    ("ontology.", "modeling"), ("graph.", "modeling"),
    ("metrics.", "modeling"), ("dimension.", "modeling"), ("term.", "modeling"),
    ("dataset.", "data"), ("report.", "data"), ("dashboard.", "data"),
    ("playground.", "data"), ("datasource.", "data"),
    ("content_", "content"), ("content.", "content"),
    ("knowledge_", "content"), ("article.", "content"),
    ("kb.", "content"), ("review.", "content"),
    ("evolution_", "governance"), ("quality.", "governance"), ("eval.", "governance"),
    ("scheduled.", "operations"), ("task.", "operations"), ("capability.", "operations"),
    ("system.", "system"),
)


def action_domain(action_key: str) -> str:
    """按 action key 前缀确定能力域（确定性映射，无匹配回 \"general\"）。"""
    key = str(action_key or "")
    for prefix, domain in _ACTION_DOMAINS:
        if key.startswith(prefix):
            return domain
    return "general"


_ACTION_CONTEXT_TTL = 300.0
_ACTION_CONTEXT_CACHE: tuple[float, dict[str, dict[str, list[str]]]] | None = None


def action_context_index(refresh: bool = False) -> dict[str, dict[str, list[str]]]:
    """动作 → {scenarios, rules} 反查索引（从系统本体 doc 投影，TTL 缓存）。

    只是发现清单的语义引导（告诉 LLM 每个功能在哪些场景用、受哪条规则约束），
    不是第二套契约。构建失败必须显式抛错（fail-loud），由调用方标注缺失原因，
    不得静默回空索引装作“没有约束”。
    """
    global _ACTION_CONTEXT_CACHE
    import time as _time

    now = _time.time()
    if (not refresh and _ACTION_CONTEXT_CACHE is not None
            and now - _ACTION_CONTEXT_CACHE[0] < _ACTION_CONTEXT_TTL):
        return _ACTION_CONTEXT_CACHE[1]
    from backend.modules.catalog.services import system_ontology_catalog as catalog

    doc = catalog.build_system_ontology_doc()
    scenarios: dict[str, list[str]] = {}
    for scn in doc.get("scenarios") or []:
        title = str(scn.get("title") or scn.get("key") or "")
        for a in scn.get("uses_actions") or []:
            scenarios.setdefault(str(a), []).append(title)
    # 规则 = 触发条件（2026-11-28 语义）：动作专属触发条件 + 所属对象不变式
    # 聚合进动作的 rules_hint；权限原则（permission_principles）不是触发条件，不进
    # 逐动作提示；绑到场景的规则也不逐动作下发（噪声）。
    action_object: dict[str, str] = {}
    for obj in doc.get("objects") or []:
        for a in obj.get("actions") or []:
            action_object[str(a.get("action_key") or a.get("key") or "")] = str(obj.get("key") or "")
    by_action: dict[str, list[str]] = {}
    by_object: dict[str, list[str]] = {}
    for rule in doc.get("rules") or []:
        trig = rule.get("trigger") or {}
        statement = str(rule.get("statement") or "").strip()[:120]
        if not statement:
            continue
        on = trig.get("on")
        ons = [str(x) for x in on] if isinstance(on, (list, tuple)) else [str(on)] if on else []
        if trig.get("on_kind") == "action":
            for t in ons:
                by_action.setdefault(t, []).append(statement)
        elif trig.get("on_kind") == "object":
            for t in ons:
                by_object.setdefault(t, []).append(statement)

    def _rules_for(action_key: str) -> list[str]:
        return [*by_action.get(action_key, []),
                *by_object.get(action_object.get(action_key, ""), [])]

    out = {k: {"scenarios": scenarios.get(k, []), "rules": _rules_for(k)}
           for k in set(scenarios) | set(by_action) | set(action_object)
           if scenarios.get(k) or _rules_for(k)}
    _ACTION_CONTEXT_CACHE = (now, out)
    return out


def validate_document_contracts(doc: dict[str, Any]) -> list[str]:
    """校验 active canonical doc 是否仍与当前代码注册表一致。"""
    expected = build_action_contracts()
    errors: list[str] = []
    if doc.get("contract_digest") != contract_digest(expected):
        errors.append("系统本体 contract_digest 与代码注册表漂移")
    actions: dict[str, dict[str, Any]] = {}
    for obj in doc.get("objects") or []:
        for action in obj.get("actions") or []:
            key = str(action.get("action_key") or action.get("key") or "")
            if key in actions:
                errors.append(f"系统本体动作 key 重复: {key}")
            actions[key] = action
    missing = sorted(set(expected) - set(actions))
    unknown = sorted(set(actions) - set(expected))
    if missing:
        errors.append("系统本体缺动作: " + ", ".join(missing))
    if unknown:
        errors.append("系统本体含未知动作: " + ", ".join(unknown))
    fields = (
        "executor_ref", "qualified_name", "tool_name", "input_schema", "output_schema",
        "perm_code", "required_perm_codes", "channel", "read_only", "error_codes",
        "idempotency", "timeout_ms", "auto_executable", "human_entry_ref", "contract_digest",
    )
    for key in sorted(set(actions) & set(expected)):
        actual = actions[key]
        for field in fields:
            want = expected[key].get(field)
            got = actual.get(field)
            if field == "human_entry_ref" and not want:
                continue
            if got != want:
                errors.append(f"动作 {key} 的 {field} 与代码注册表漂移")
        if actual.get("params_schema") != expected[key]["input_schema"]:
            errors.append(f"动作 {key} 的 params_schema 与 input_schema 不一致")
    return errors


__all__ = [
    "HUMAN_ACTION_SPECS", "MCP_RESULT_SCHEMA", "action_context_index", "action_domain",
    "build_action_contracts", "contract_digest", "get_executor_registry",
    "resolve_executor", "validate_document_contracts",
]
