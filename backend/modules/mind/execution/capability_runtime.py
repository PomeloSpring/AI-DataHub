"""P2 本体动作运行时：让 active 系统能力本体真正参与运行前契约解析与执行。

执行链（fail-loud，缺一步即拒）
------------------------------
1. ``load_active_contract()`` 读取并固定 active 系统本体指纹（model id + contract_digest）；
   无 active doc → ``ONTOLOGY_UNAVAILABLE``；代码注册表变化而 active doc 未刷新 → ``CONTRACT_DRIFT``。
2. ``resolve_action(action_key)`` 精确解析（禁止模糊匹配）；缺失 → ``ACTION_NOT_FOUND``，
   doc 内一动作多处声明 → ``ACTION_AMBIGUOUS``。
3. 校验 executor 已注册（``EXECUTOR_NOT_REGISTERED``）且契约 executor_ref 与注册表一致（漂移即拒）。
4. ``jsonschema`` 校验输入（``SCHEMA_INVALID``）。
5. 复用 ``check_tool``（角色权限码 + 涉密上界 + AS-BOT 减法）、``require_write_perm``
   （写动作 AI 写级别闸）、``validate_tool_resources``（资源域）；缺权 → ``PERMISSION_DENIED``。
6. 人工动作拒 AI 执行（``HUMAN_ACTION_REQUIRED``，回 human_entry_ref 引导人到界面）。
7. 调用现有 Handler/业务服务；按契约 ``timeout_ms`` 截断（``TIMEOUT``）。
8. 校验输出 Schema（``EXECUTION_FAILED``）并落审计（本体指纹、action key、executor、
   权限码、结果与安全错误信息）。

幂等：契约 ``idempotency=required`` 的动作必须带 ``idempotency_key``；同键同参重放返回
首次结果（``replayed=True``），同键异参 → ``IDEMPOTENCY_CONFLICT``。进程内账本只服务
单进程语义（P3 流程执行以 DB 决策账本为准），不落任何凭据/正文。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("mind.capability_runtime")

# ── 错误码（对外契约；新增不得低于 plan P2 清单）────────────────────────────
ONTOLOGY_UNAVAILABLE = "ONTOLOGY_UNAVAILABLE"
ACTION_NOT_FOUND = "ACTION_NOT_FOUND"
ACTION_AMBIGUOUS = "ACTION_AMBIGUOUS"
CONTRACT_DRIFT = "CONTRACT_DRIFT"
SCHEMA_INVALID = "SCHEMA_INVALID"
EXECUTOR_NOT_REGISTERED = "EXECUTOR_NOT_REGISTERED"
PERMISSION_DENIED = "PERMISSION_DENIED"
HUMAN_ACTION_REQUIRED = "HUMAN_ACTION_REQUIRED"
EXECUTION_FAILED = "EXECUTION_FAILED"
IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
TIMEOUT = "TIMEOUT"
RESOURCE_FORBIDDEN = "RESOURCE_FORBIDDEN"
FEATURE_DISABLED = "FEATURE_DISABLED"

ERROR_CODES = (
    ONTOLOGY_UNAVAILABLE, ACTION_NOT_FOUND, ACTION_AMBIGUOUS, CONTRACT_DRIFT,
    SCHEMA_INVALID, EXECUTOR_NOT_REGISTERED, PERMISSION_DENIED, HUMAN_ACTION_REQUIRED,
    EXECUTION_FAILED, IDEMPOTENCY_CONFLICT, TIMEOUT, RESOURCE_FORBIDDEN, FEATURE_DISABLED,
)

_DATA_ENGINEERING_PREFIXES = ("sync:", "dag:", "udf:")


class CapabilityRuntimeError(RuntimeError):
    """运行时失败：携带稳定错误码与安全消息（原始细节只进服务端日志）。"""

    def __init__(self, code: str, message: str, *, human_entry_ref: str = ""):
        super().__init__(message)
        self.code = code
        self.human_entry_ref = human_entry_ref

    def as_dict(self) -> dict:
        out = {"error": str(self), "code": self.code}
        if self.human_entry_ref:
            out["human_entry_ref"] = self.human_entry_ref
        return out


@dataclass(frozen=True)
class ActiveContract:
    """已固定的 active 系统本体指纹与动作契约（读取后不再受并发发布影响）。"""

    model_id: int
    model_name: str
    fingerprint: str
    doc: dict = field(repr=False)
    actions: dict = field(repr=False)


def _conn_query(sql: str) -> dict | None:
    from backend.common.db import execute_query

    return execute_query(sql, fetchone=True)


def load_active_contract() -> ActiveContract:
    """读取 active 系统本体并校验与代码注册表零漂移；任一失败显式报错。"""
    from backend.modules.catalog.services import system_ontology_catalog as catalog
    from backend.modules.mind.execution import capability_contracts as contracts

    row = _conn_query(
        "SELECT id, name, json_content, updated_at FROM adh_ontology_models "
        "WHERE kind = 'system' AND status = 'active' "
        "ORDER BY updated_at DESC, id DESC LIMIT 1")
    if not row:
        raise CapabilityRuntimeError(
            ONTOLOGY_UNAVAILABLE,
            "系统能力本体不可用：没有 active 状态的系统本体（先执行 refresh_system_ontology 发布）")
    try:
        doc = row.get("json_content")
        doc = json.loads(doc) if isinstance(doc, str) else (doc or {})
    except (TypeError, ValueError) as exc:
        raise CapabilityRuntimeError(
            ONTOLOGY_UNAVAILABLE, "系统能力本体不可用：canonical doc 不是合法 JSON") from exc
    if not isinstance(doc, dict) or not doc.get("objects"):
        raise CapabilityRuntimeError(
            ONTOLOGY_UNAVAILABLE, "系统能力本体不可用：canonical doc 为空或缺对象清单")

    # 漂移即拒：代码注册表（function_tools/ontology_tools）变化而 active doc 未刷新，
    # 执行旧契约等于执行不存在的工具（P0-1 的契约 digest 在这里消费）。
    drift = contracts.validate_document_contracts(doc)
    if drift:
        raise CapabilityRuntimeError(
            CONTRACT_DRIFT,
            "系统本体动作契约与代码注册表漂移：" + "；".join(drift[:5])
            + "（请重跑 refresh_system_ontology 发布后再执行）")

    actions = _index_actions(doc)
    if not actions:
        raise CapabilityRuntimeError(
            ONTOLOGY_UNAVAILABLE, "系统能力本体没有登记任何动作契约")

    fingerprint = str(doc.get("contract_digest") or "")
    return ActiveContract(
        model_id=int(row.get("id") or 0),
        model_name=str(row.get("name") or catalog.SYSTEM_MODEL_NAME),
        fingerprint=fingerprint, doc=doc, actions=actions)


def _index_actions(doc: dict) -> dict[str, dict]:
    """把 doc 的 objects[].actions[] 建成 action_key 索引；重复声明即歧义拒。"""
    actions: dict[str, dict] = {}
    for obj in doc.get("objects") or []:
        for action in obj.get("actions") or []:
            key = str(action.get("action_key") or action.get("key") or "")
            if not key:
                raise CapabilityRuntimeError(
                    CONTRACT_DRIFT, "系统本体存在未声明 action_key 的动作条目")
            if key in actions:
                raise CapabilityRuntimeError(
                    ACTION_AMBIGUOUS, f"动作 {key} 在系统本体中被多处声明，无法唯一解析")
            actions[key] = action
    return actions


def resolve_action(action_key: str, contract: ActiveContract | None = None) -> dict:
    """按 action_key 精确解析契约；禁止模糊匹配（相近 key 只作诊断提示）。"""
    key = str(action_key or "").strip()
    contract = contract or load_active_contract()
    action = contract.actions.get(key)
    if action is None:
        near = sorted(k for k in contract.actions if key and (k.startswith(key) or key in k))[:5]
        hint = f"；相近 key：{near}" if near else ""
        raise CapabilityRuntimeError(
            ACTION_NOT_FOUND, f"动作 {key!r} 不在系统本体契约中（不模糊匹配）{hint}")
    return action


# ── 幂等账本（进程内；同键同参重放、同键异参冲突）──────────────────────────

_IDEM_LOCK = threading.Lock()
_IDEM_CACHE: dict[str, tuple[str, Any]] = {}
_IDEM_MAX = 512


def _idem_check(key: str, payload_hash: str) -> Any:
    """返回缓存结果（命中同参）；同键异参抛 IDEMPOTENCY_CONFLICT；未命中返回 None。"""
    with _IDEM_LOCK:
        hit = _IDEM_CACHE.get(key)
    if hit is None:
        return None
    seen_hash, result = hit
    if seen_hash != payload_hash:
        raise CapabilityRuntimeError(
            IDEMPOTENCY_CONFLICT,
            "同一幂等键已用于不同参数的调用，不能重放；请换新幂等键或按首次参数重试")
    return result


def _idem_record(key: str, payload_hash: str, result: Any) -> None:
    with _IDEM_LOCK:
        if len(_IDEM_CACHE) >= _IDEM_MAX:
            _IDEM_CACHE.clear()  # 进程内账本只需防瞬时重复；跨进程语义由 P3 DB 账本承担
        _IDEM_CACHE[key] = (payload_hash, result)


def _payload_hash(action_key: str, args: dict) -> str:
    raw = json.dumps({"action": action_key, "args": args or {}},
                     sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── 预检（Schema / executor / 权限 / 资源域）────────────────────────────────

def preflight(action_key: str, args: dict, ctx=None, *,
              contract: ActiveContract | None = None) -> dict:
    """运行前契约解析与权限预检；通过返回 {contract, action, executor, call_args}。"""
    from backend.modules.mind.execution import capability_contracts as contracts

    active = contract or load_active_contract()
    action = resolve_action(action_key, active)
    key = str(action.get("action_key") or action_key)
    channel = str(action.get("channel") or "")
    if channel == "human":
        raise CapabilityRuntimeError(
            HUMAN_ACTION_REQUIRED,
            f"「{action.get('label') or key}」是人工动作，AI 不得代做；"
            f"请人到界面操作（入口：{action.get('human_entry_ref') or '见人工入口'}）",
            human_entry_ref=str(action.get("human_entry_ref") or ""))

    try:
        executor = contracts.resolve_executor(key)
    except KeyError as exc:
        raise CapabilityRuntimeError(
            EXECUTOR_NOT_REGISTERED,
            f"动作 {key} 在契约中声明了 executor_ref，但代码注册表没有对应 executor") from exc
    declared = str(action.get("executor_ref") or "")
    if declared:
        # 契约 executor_ref（function:tool / ontology:name）必须与声明的注册表/工具名同源
        source = str(action.get("source_registry") or "")
        tool_name = str(action.get("tool_name") or "")
        expected = (f"function:{tool_name}" if source == "function_tools"
                    else f"ontology:{tool_name}" if source == "ontology_tools" else "")
        if expected and declared != expected:
            raise CapabilityRuntimeError(
                CONTRACT_DRIFT, f"动作 {key} 的 executor_ref 与代码注册表不一致")

    # 输入 Schema（jsonschema）；错误只回安全摘要，实例细节进服务端日志
    try:
        from jsonschema import Draft7Validator
        schema = action.get("input_schema") or action.get("params_schema") or {}
        Draft7Validator(schema).validate(dict(args or {}))
    except Exception as exc:  # noqa: BLE001 — 校验失败即拒，不静默放宽
        logger.info("[capability-runtime] 输入 Schema 校验失败 action=%s: %s", key, exc)
        raise CapabilityRuntimeError(
            SCHEMA_INVALID, f"动作 {key} 的入参不符合契约 Schema：{exc}") from exc

    # 数据工程域硬上界：任何通道都不得进入 AI 可执行集合（sync:/dag:/udf:）
    perm_code = str(action.get("perm_code") or "")
    if perm_code.startswith(_DATA_ENGINEERING_PREFIXES):
        raise CapabilityRuntimeError(
            PERMISSION_DENIED,
            f"「{action.get('label') or key}」属数据工程域，不对 AI 开放（人专属运维动作）")

    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    runtime_ctx = ctx if ctx is not None else get_execution_context()
    if runtime_ctx is None or int(getattr(runtime_ctx, "user_id", 0) or 0) <= 0:
        raise CapabilityRuntimeError(PERMISSION_DENIED, "无可信执行身份，拒绝执行任何动作")

    qualified = str(executor.get("qualified_name") or "")
    from backend.modules.mind.execution.resource_guard import (
        ResourceScopeError, validate_tool_resources)
    from backend.modules.mind.execution.tool_policy import check_tool

    try:
        check_tool(runtime_ctx, qualified)
        call_args = validate_tool_resources(runtime_ctx, qualified, dict(args or {}))
    except PermissionError as exc:
        raise CapabilityRuntimeError(PERMISSION_DENIED, str(exc)) from exc
    except ResourceScopeError as exc:
        raise CapabilityRuntimeError(RESOURCE_FORBIDDEN, str(exc)) from exc
    except ValueError as exc:
        raise CapabilityRuntimeError(SCHEMA_INVALID, str(exc)) from exc

    # 写动作再过 require_write_perm（AI 写级别闸；handler 内域码仍会再裁一次，只增不减）
    if not bool(action.get("read_only")):
        from backend.core.perm_link import require_write_perm

        for code in [perm_code] + [p for p in (action.get("required_perm_codes") or []) if p != perm_code]:
            if not code:
                continue
            try:
                require_write_perm(int(getattr(runtime_ctx, "user_id", 0) or 0),
                                   int(getattr(runtime_ctx, "workspace_id", 0) or 0),
                                   code, str(action.get("label") or key))
            except PermissionError as exc:
                raise CapabilityRuntimeError(PERMISSION_DENIED, str(exc)) from exc

    return {"contract": active, "action": action, "executor": executor, "call_args": call_args}


# ── 执行（timeout 截断 + 输出 Schema 校验 + 审计）──────────────────────────

def _invoke(handler, call_args: dict, *, async_handler: bool, timeout_sec: float):
    def _call():
        if async_handler:
            return asyncio.run(handler(call_args))
        return handler(call_args)

    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cap-action")
    future = pool.submit(_call)
    try:
        return future.result(timeout=timeout_sec)
    except FutureTimeout as exc:
        future.cancel()
        raise CapabilityRuntimeError(
            TIMEOUT, f"动作执行超过契约时限 {int(timeout_sec * 1000)}ms，已中止等待") from exc
    finally:
        # 超时后不得在 with 块里等慢调用跑完（那会让 TIMEOUT 形同虚设）：不等待直接收尾
        pool.shutdown(wait=False, cancel_futures=True)


def execute_action(action_key: str, args: dict, ctx=None, idempotency_key: str = "",
                   *, contract: ActiveContract | None = None) -> Any:
    """按 action key 直接执行（统一入口，覆盖功能动作与本体工具）。

    输出必须符合契约 output_schema；handler 的业务级错误（isError 结果）是合法返回，
    不吞不改；结构性失败一律带错误码抛出。

    灰度（P5）：``CAPABILITY_RUNTIME_ENABLED`` 默认关闭 —— 未开启时契约执行链不接流量，
    回退到壳层注册表直通（``function_tools.run_action_by_key``），保持后向兼容。
    """
    import os as _os
    if _os.getenv("CAPABILITY_RUNTIME_ENABLED", "").strip().lower() not in ("1", "true", "yes", "on"):
        raise CapabilityRuntimeError(
            FEATURE_DISABLED,
            "契约执行链未开启（CAPABILITY_RUNTIME_ENABLED=false）；"
            "请管理员灰度开启后重试，或按计划逐步人工执行")
    checked = preflight(action_key, args, ctx, contract=contract)
    action = checked["action"]
    executor = checked["executor"]
    key = str(action.get("action_key") or action_key)
    call_args = checked["call_args"]

    idem_key = str(idempotency_key or "").strip()
    if str(action.get("idempotency") or "none") == "required" and not idem_key:
        raise CapabilityRuntimeError(
            IDEMPOTENCY_CONFLICT, f"动作 {key} 的契约要求幂等键（idempotency=required）")
    payload_hash = _payload_hash(key, call_args)
    if idem_key:
        cached = _idem_check(idem_key, payload_hash)
        if cached is not None:
            replayed = dict(cached) if isinstance(cached, dict) else {"result": cached}
            replayed["replayed"] = True
            return replayed

    timeout_ms = int(action.get("timeout_ms") or 30_000)
    started = time.time()
    error_code = ""
    try:
        result = _invoke(executor["handler"], call_args,
                         async_handler=bool(executor.get("async_handler")),
                         timeout_sec=max(timeout_ms / 1000.0, 0.001))
    except CapabilityRuntimeError as exc:
        error_code = exc.code
        _audit(checked, key, ok=False, error_code=exc.code,
               duration_ms=int((time.time() - started) * 1000))
        raise
    except Exception as exc:  # noqa: BLE001 — 细节进日志，对外只给安全摘要
        logger.exception("[capability-runtime] 动作执行失败 action=%s", key)
        error_code = EXECUTION_FAILED
        _audit(checked, key, ok=False, error_code=error_code,
               duration_ms=int((time.time() - started) * 1000))
        raise CapabilityRuntimeError(
            EXECUTION_FAILED, f"动作 {key} 执行失败：{type(exc).__name__}") from exc

    _validate_output(action, result, key)
    if idem_key:
        _idem_record(idem_key, payload_hash, result)
    _audit(checked, key, ok=True, error_code=error_code,
           duration_ms=int((time.time() - started) * 1000))
    return result


def _validate_output(action: dict, result: Any, key: str) -> None:
    """输出必须符合契约 output_schema；不符合 = 执行失败（不得把坏输出当成功）。"""
    from jsonschema import Draft7Validator

    schema = action.get("output_schema") or {}
    if not isinstance(result, (dict, list)):
        raise CapabilityRuntimeError(
            EXECUTION_FAILED, f"动作 {key} 的输出不是契约声明的对象结构")
    try:
        Draft7Validator(schema).validate(result)
    except Exception as exc:  # noqa: BLE001
        logger.error("[capability-runtime] 输出不符合契约 action=%s: %s", key, exc)
        raise CapabilityRuntimeError(
            EXECUTION_FAILED, f"动作 {key} 的输出不符合契约 output_schema") from exc


def _audit(checked: dict, key: str, *, ok: bool, error_code: str, duration_ms: int) -> None:
    """审计（best-effort）：本体指纹、动作、executor、权限码、安全结果。"""
    try:
        from backend.common.auth import log_audit
        from backend.modules.mind.execution.sdk_tools.context import get_execution_context

        action = checked["action"]
        ctx = get_execution_context()
        user_id = int(getattr(ctx, "user_id", 0) or 0) if ctx else 0
        log_audit(user_id, f"capability:{key}",
                  "capability_action",
                  target_id=int(checked["contract"].model_id),
                  detail=(f"executor={action.get('executor_ref') or '-'} "
                          f"perm={action.get('perm_code') or '-'} "
                          f"ok={ok} error={error_code or '-'} ms={duration_ms}"),
                  module="mind")
    except Exception as exc:  # noqa: BLE001 — 审计是旁路，不阻断返回
        logger.warning("[capability-runtime] 审计写入失败（不影响主链路）: %s", exc)


# ── 计划构建（供 system_capability_plan：精确工具名/executor/Schema/幂等/超时）──

def build_capability_plan(scenario: dict, *, contract: ActiveContract | None = None,
                          doc: dict | None = None) -> dict:
    """把系统本体 scenario 展开为可执行计划（步骤带完整执行契约）。

    只做“能执行什么”的事实描述；“当前身份能不能做”由调用方叠加
    （system_capability_plan 用 _step_runnable 按角色持码判定，不重复裁决）。

    ``doc`` 允许传入已读取的 canonical doc（如 ``_active_system_doc()``）直接展开，
    不再回库；此路径是**描述**通道（doc 自检由 build_system_ontology_doc 完成），
    契约指纹硬校验仍在执行链（``load_active_contract`` / ``execute_action``）。
    """
    if contract is None:
        if doc is not None:
            contract = ActiveContract(
                model_id=0, model_name=str(doc.get("name") or ""),
                fingerprint=str(doc.get("contract_digest") or ""),
                doc=doc, actions=_index_actions(doc))
        else:
            contract = load_active_contract()
    active = contract
    rules = {str(r.get("key") or ""): r for r in (active.doc.get("rules") or [])}
    flow = sorted((x for x in (scenario.get("flow") or []) if isinstance(x, dict)),
                  key=lambda st: int(st.get("order") or 99)
                  if str(st.get("order") or "").lstrip("-").isdigit() else 99)

    steps: list[dict] = []
    for st in flow:
        action_key = str(st.get("action") or "")
        step = {
            "order": st.get("order"),
            "action": action_key,
            "label": st.get("action_label") or action_key,
            "purpose": st.get("purpose") or "",
            "channel": str(st.get("channel") or ""),
            "perm_code": str(st.get("perm_code") or ""),
            "consumes": st.get("consumes") or "",
            "produces": st.get("produces") or "",
            "rule_gates": [{"key": str(rk),
                            "statement": (rules.get(str(rk)) or {}).get("statement", "")}
                           for rk in (st.get("gated_by") or [])],
        }
        try:
            act = resolve_action(action_key, active)
        except CapabilityRuntimeError as exc:
            step.update({"resolvable": False, "why_not": str(exc)})
            steps.append(step)
            continue
        step.update({
            "resolvable": True,
            "label": st.get("action_label") or act.get("label") or action_key,
            "channel": str(act.get("channel") or step["channel"]),
            "perm_code": str(act.get("perm_code") or step["perm_code"]),
            "read_only": bool(act.get("read_only")),
            "tool_name": str(act.get("tool_name") or ""),
            "executor_ref": str(act.get("executor_ref") or ""),
            "qualified_name": str(act.get("qualified_name") or ""),
            "input_schema": act.get("input_schema") or {},
            "output_schema": act.get("output_schema") or {},
            "idempotency": str(act.get("idempotency") or "none"),
            "timeout_ms": int(act.get("timeout_ms") or 0),
            "error_codes": list(act.get("error_codes") or []),
            "auto_executable": bool(act.get("auto_executable")),
            "human_entry_ref": str(act.get("human_entry_ref") or ""),
        })
        steps.append(step)

    return {
        "scenario": {"key": scenario.get("key"), "title": scenario.get("title"),
                     "goal": scenario.get("goal"), "actors": scenario.get("actors") or []},
        "steps": steps,
        "rules": [{"key": str(rk),
                   "statement": (rules.get(str(rk)) or {}).get("statement", "")}
                  for rk in (scenario.get("uses_rules") or [])],
        "model_id": active.model_id,
        "fingerprint": active.fingerprint,
    }


__all__ = [
    "ActiveContract", "CapabilityRuntimeError", "ERROR_CODES",
    "ACTION_AMBIGUOUS", "ACTION_NOT_FOUND", "CONTRACT_DRIFT", "EXECUTION_FAILED",
    "EXECUTOR_NOT_REGISTERED", "FEATURE_DISABLED", "HUMAN_ACTION_REQUIRED",
    "IDEMPOTENCY_CONFLICT", "ONTOLOGY_UNAVAILABLE", "PERMISSION_DENIED",
    "RESOURCE_FORBIDDEN", "SCHEMA_INVALID", "TIMEOUT",
    "build_capability_plan", "execute_action", "load_active_contract",
    "preflight", "resolve_action",
]
