"""P3 受控能力流程：场景 auto 模式的执行引擎（单步单 worker、人工停等、事件可审计）。

执行边界（ontology-modeling §10.6，三条缺一即拒）
------------------------------------------------
1. 场景显式声明 ``execution: {mode: "auto", enabled: true, input_schema, allowlist}``；
2. 场景 key 命中服务端白名单 ``CAPABILITY_FLOW_SCENARIOS``（默认仅 ``ops_content_publish``）；
3. 特性开关 ``CAPABILITY_FLOW_AUTO_ENABLED=true``（默认 false，P5 灰度）。

状态机（P3-3）
--------------
* run：``pending -> running -> waiting_human -> succeeded/failed/cancelled``
  （``compensating/compensated`` 为补偿预留；当前动作无 compensator 声明，
   取消/失败留可诊断半态并在事件里写明，不做假回滚）；
* step：``pending -> running -> waiting_human -> succeeded/failed/skipped/cancelled``；
* 并发：步骤抢占走条件 UPDATE + 租约（``status='pending'`` 且租约空/过期），
  单步只执行一次；重试仅限显式瞬态错误（``TIMEOUT``/``EXECUTION_FAILED``），
  权限、Schema、人工等待不重试。

人工步骤
--------
* 人工步骤（channel=human）绝不自动执行：停在 ``waiting_human``，AI 不得代做；
* 完成复核**读内容服务状态**（``content_service.get_article`` 的 live 发布事实），
  不信任客户端"已完成"；发布服务无需感知流程，流程侧主动复核事实源。

机器绑定（P3-1）
----------------
步骤 ``input_bindings``/``output_bindings`` 是机器执行的唯一依据：
``input.x`` 取运行输入、``ctx.x`` 取上下文、常量原样；``idempotency_key`` 支持
``{input.x}``/``{ctx.x}`` 模板。``consumes``/``produces`` 只是可读说明，不参与执行。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("platform.capability_flow")

# ── 运行/步骤状态（与迁移 ENUM 一一对应）───────────────────────────────────
RUN_PENDING = "pending"
RUN_RUNNING = "running"
RUN_WAITING_HUMAN = "waiting_human"
RUN_SUCCEEDED = "succeeded"
RUN_FAILED = "failed"
RUN_CANCELLED = "cancelled"
TERMINAL_RUN_STATUSES = (RUN_SUCCEEDED, RUN_FAILED, RUN_CANCELLED)

STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_WAITING_HUMAN = "waiting_human"
STEP_SUCCEEDED = "succeeded"
STEP_FAILED = "failed"

#: 允许重试的显式瞬态错误；权限/Schema/人工等待不重试
TRANSIENT_ERROR_CODES = ("TIMEOUT", "EXECUTION_FAILED")

DEFAULT_SCENARIOS = "ops_content_publish"
LEASE_SECONDS = 60


class FlowError(RuntimeError):
    """流程层失败：安全错误码 + 人话原因（细节只进服务端日志）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def as_dict(self) -> dict:
        return {"error": str(self), "code": self.code}


def _flag(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def auto_enabled() -> bool:
    return _flag("CAPABILITY_FLOW_AUTO_ENABLED").lower() in ("1", "true", "yes", "on")


def allowed_scenarios() -> tuple[str, ...]:
    raw = _flag("CAPABILITY_FLOW_SCENARIOS", DEFAULT_SCENARIOS)
    return tuple(s.strip() for s in raw.split(",") if s.strip())


# ── 数据访问（连接自理；调用方不带连接）────────────────────────────────────

def _conn():
    from backend.common.db.metadata_db import get_metadata_conn
    return get_metadata_conn()


def _q(sql: str, params: tuple = ()) -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def _w(sql: str, params: tuple = ()) -> int:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            affected = int(cur.rowcount or 0)
        conn.commit()
        return affected
    finally:
        conn.close()


def _iso(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "strftime") else (
        str(value) if value else "")


def _event(run_id: int, step_no: int, event_type: str, detail: str, actor: str) -> None:
    """追加式事件账：只留安全摘要（不复制正文/凭据/SQL）。"""
    _w("INSERT INTO adh_capability_flow_events "
       "(run_id, step_no, event_type, detail, actor) VALUES (%s,%s,%s,%s,%s)",
       (int(run_id), int(step_no), event_type, str(detail or "")[:500], str(actor or "")[:64]))


# ── 场景解析（active 系统本体，指纹随 run 固定）───────────────────────────

def _load_scenario(scenario_key: str) -> tuple[dict, dict]:
    """返回 (scenario, contract)；场景缺失/非 auto 模式显式拒。"""
    from backend.modules.mind.execution import capability_runtime as caprt

    contract = caprt.load_active_contract()
    key = str(scenario_key or "").strip()
    matches = [s for s in (contract.doc.get("scenarios") or [])
               if str(s.get("key") or "") == key]
    if not matches:
        titles = [str(s.get("title") or s.get("key") or "")
                  for s in (contract.doc.get("scenarios") or [])][:12]
        raise FlowError("SCENARIO_NOT_FOUND",
                        f"场景 {key!r} 不在系统能力本体中（不模糊匹配）。可选：{titles or '（空）'}")
    if len(matches) > 1:
        raise FlowError("SCENARIO_AMBIGUOUS", f"场景 {key!r} 在本体中多处声明，无法唯一解析")
    return matches[0], contract


def _assert_auto_executable(scenario: dict) -> dict:
    """auto 执行三条件（§10.6）：显式声明 + 白名单 + 开关，缺一即拒。"""
    execution = scenario.get("execution") or {}
    if str(execution.get("mode") or "") != "auto" or not bool(execution.get("enabled")):
        raise FlowError(
            "SCENARIO_NOT_AUTO",
            f"场景「{scenario.get('title') or scenario.get('key')}」未显式声明自动执行"
            f"（execution.mode=auto & enabled=true）；请按计划逐步执行，或先在场景声明里开启")
    if str(scenario.get("key") or "") not in allowed_scenarios():
        raise FlowError(
            "SCENARIO_NOT_ALLOWLISTED",
            f"场景 {scenario.get('key')!r} 不在服务端白名单 "
            f"{list(allowed_scenarios())} 内（CAPABILITY_FLOW_SCENARIOS），拒绝自动执行")
    if not auto_enabled():
        raise FlowError(
            "FLOW_DISABLED",
            "能力流程自动执行未开启（CAPABILITY_FLOW_AUTO_ENABLED=false）；"
            "灰度由管理员开启后再试，或按计划逐步人工执行")
    return execution


# ── 绑定表达式（input.x / ctx.x / 常量；{input.x} 模板）───────────────────

_REF_RE = re.compile(r"^(input|ctx)\.(.+)$")
_TPL_RE = re.compile(r"\{(input|ctx)\.([^{}]+)\}")


def _eval_ref(expr: Any, run_input: dict, ctx_values: dict):
    text = str(expr or "").strip()
    m = _REF_RE.match(text)
    if not m:
        return text
    source, name = m.group(1), m.group(2)
    values = run_input if source == "input" else ctx_values
    if name not in values:
        raise FlowError("BINDING_UNRESOLVED",
                        f"绑定表达式 {text!r} 在{'运行输入' if source == 'input' else '上下文'}中"
                        f"没有取值（{name} 缺失），拒绝执行该步骤")
    return values[name]


def _eval_template(text: str, run_input: dict, ctx_values: dict) -> str:
    def _sub(m: re.Match) -> str:
        source, name = m.group(1), m.group(2)
        values = run_input if source == "input" else ctx_values
        return str(values.get(name, ""))

    return _TPL_RE.sub(_sub, str(text or ""))


def _resolve_step_args(bindings: dict, run_input: dict, ctx_values: dict) -> dict:
    return {str(k): _eval_ref(v, run_input, ctx_values)
            for k, v in (bindings or {}).items()}


def _apply_output_bindings(outputs: dict, result: Any, ctx_values: dict) -> dict:
    """步骤输出（MCP 形态 content[0].text 的 JSON payload）按 output_bindings 写入上下文。"""
    payload: dict = {}
    try:
        content = (result or {}).get("content") or []
        text = content[0].get("text", "") if content else ""
        payload = json.loads(text) if text else {}
        if not isinstance(payload, dict):
            payload = {"value": payload}
    except (TypeError, ValueError):
        payload = {}
    written = {}
    for ctx_key, expr in (outputs or {}).items():
        name = str(expr or "").strip()
        if name.startswith("output."):
            value = payload.get(name[len("output."):])
            if value is not None:
                ctx_values[str(ctx_key)] = value
                written[str(ctx_key)] = value
    return written


# ── 创建运行（预检全部自动步骤权限，缺任一即拒，不建半截运行）─────────────

def _preflight_step_perms(scenario: dict, user_id: int, workspace_id: int) -> None:
    """创建前预检：每个自动步骤的权限码都必须满足（缺任一 → 拒，不创建半截运行）。"""
    from backend.core import perm_link
    from backend.core.role_service import role_service
    from backend.modules.mind.execution import capability_runtime as caprt

    contract = caprt.load_active_contract()
    role_perms = role_service.get_user_role_ai_perms(int(user_id or 0), int(workspace_id or 0)) or {}
    missing: list[str] = []
    for st in scenario.get("flow") or []:
        action = contract.actions.get(str(st.get("action") or ""))
        if action is None:
            raise FlowError("ACTION_NOT_FOUND",
                            f"场景步骤引用的动作 {st.get('action')!r} 不在契约中，拒绝创建")
        if str(action.get("channel") or "") == "human":
            continue  # 人工步骤由人在界面完成，不预检 AI 执行权限
        if not bool(action.get("auto_executable")):
            raise FlowError("ACTION_NOT_EXECUTABLE",
                            f"动作 {st.get('action')!r} 不可由流程自动执行"
                            f"（人工/数据工程域动作不得进入流程）")
        codes = [str(action.get("perm_code") or "")] + \
            [p for p in (action.get("required_perm_codes") or [])
             if p != action.get("perm_code")]
        for code in [c for c in codes if c]:
            if bool(action.get("read_only")):
                ok = code in role_perms
            else:
                ok, _why = perm_link.check_write_perm(role_perms, code,
                                                      str(action.get("label") or ""))
            if not ok:
                missing.append(f"{st.get('action')}:{code}")
    if missing:
        raise FlowError(
            "PERMISSION_DENIED",
            f"发起人缺少流程所需权限（{', '.join(missing[:6])}）；"
            f"创建前预检全部自动步骤权限，缺任一不创建半截运行")


def _require_run_perm(user_id: int, workspace_id: int, label: str) -> None:
    """编排入口双闸的第二道：capability:run（第一道是 HTTP 中间件/工具授权）。"""
    from backend.core.perm_link import require_write_perm

    try:
        require_write_perm(int(user_id or 0), int(workspace_id or 0), "capability:run", label)
    except PermissionError as exc:
        raise FlowError("PERMISSION_DENIED", str(exc)) from exc


def create_run(*, scenario_key: str, input_payload: dict, idempotency_key: str,
               user_id: int, username: str = "", workspace_id: int = 0,
               channel: str = "human", as_bot: dict | None = None) -> dict:
    """创建并启动一次流程运行（幂等键唯一；重复请求返回首次运行）。

    编排层域边界（AS-BOT 场景白名单）：``channel="ai"`` 时场景 key 必须命中
    该 AS-BOT 声明的 ``allowed_scenarios``（`tools.scenarios`，空清单 = 未声明
    = 不得启动任何流程，fail-closed）；人工通道不受此限（只受 capability:run
    把关）。动作层权限仍由 _preflight_step_perms 逐步再裁，白名单不是权限替代。
    """
    _require_run_perm(user_id, workspace_id, "运行能力流程")
    key = str(idempotency_key or "").strip()
    if not key:
        raise FlowError("IDEMPOTENCY_KEY_REQUIRED", "创建流程必须带幂等键（Idempotency-Key）")
    if str(channel or "human") == "ai":
        from backend.modules.mind.execution import as_bots
        allowed = as_bots.collect_allowed_scenarios([as_bot or {}])
        want = str(scenario_key or "").strip()
        if want not in allowed:
            raise FlowError(
                "SCENARIO_NOT_ALLOWED",
                f"场景 {want!r} 不在当前助手的可编排场景白名单内"
                f"（已声明：{allowed or '无'}）。流程编排需在「AS-BOT 配置」"
                "的 tools.scenarios 显式声明后才对 AI 开放")
    scenario, contract = _load_scenario(scenario_key)
    execution = _assert_auto_executable(scenario)
    payload = dict(input_payload or {})

    schema = execution.get("input_schema") or {}
    if isinstance(schema, dict) and schema:
        try:
            from jsonschema import Draft7Validator
            Draft7Validator(schema).validate(payload)
        except Exception as exc:  # noqa: BLE001
            raise FlowError("SCHEMA_INVALID", f"运行输入不符合场景 input_schema：{exc}") from exc

    steps = [s for s in (scenario.get("flow") or []) if isinstance(s, dict)]
    if not steps:
        raise FlowError("SCENARIO_EMPTY", "场景没有可执行步骤")
    allowlist = {str(a) for a in (execution.get("allowlist") or [])}
    for st in steps:
        if allowlist and str(st.get("action") or "") not in allowlist:
            raise FlowError("ACTION_NOT_ALLOWLISTED",
                            f"步骤动作 {st.get('action')!r} 不在场景 allowlist 内，拒绝创建")

    _preflight_step_perms(scenario, user_id, workspace_id)

    conn = _conn()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status FROM adh_capability_flow_runs "
                "WHERE scenario_key=%s AND initiated_by=%s AND idempotency_key=%s",
                (str(scenario.get("key") or ""), int(user_id), key))
            existing = cur.fetchone()
            if existing:
                conn.rollback()
                run = get_run(int(existing["id"]))
                if run is not None:
                    run["replayed"] = True
                    return run
            cur.execute(
                "INSERT INTO adh_capability_flow_runs "
                "(scenario_key, scenario_title, ontology_fingerprint, initiated_by, "
                " initiated_by_name, workspace_id, status, current_step, input_json, "
                " idempotency_key) VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s,%s)",
                (str(scenario.get("key") or ""), str(scenario.get("title") or "")[:191],
                 contract.fingerprint, int(user_id), str(username or "")[:64],
                 int(workspace_id), RUN_PENDING,
                 json.dumps(payload, ensure_ascii=False, default=str), key))
            run_id = int(cur.lastrowid or 0)
            for st in steps:
                action = contract.actions.get(str(st.get("action") or "")) or {}
                # 机器执行快照：输入/输出绑定 + 幂等键模板 + 重试策略随步骤落库，
                # 推进时只认这份快照（场景 doc 后续变更不影响已建运行）
                spec = {"bindings": st.get("input_bindings") or {},
                        "outputs": st.get("output_bindings") or {},
                        "idem": st.get("idempotency_key") or "",
                        "retry": st.get("retry") or {}}
                cur.execute(
                    "INSERT INTO adh_capability_flow_steps "
                    "(run_id, step_no, action_key, action_label, channel, status, "
                    " input_json, perm_code, executor_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (run_id, int(st.get("order") or 0), str(st.get("action") or ""),
                     str(action.get("label") or st.get("action") or "")[:128],
                     str(action.get("channel") or "ai"), STEP_PENDING,
                     json.dumps(spec, ensure_ascii=False, default=str),
                     str(action.get("perm_code") or "")[:64],
                     str(action.get("executor_ref") or "")[:128]))
        _event_in(conn, run_id, 0, "created",
                  f"scenario={scenario.get('key')} steps={len(steps)} "
                  f"fingerprint={contract.fingerprint[:12]}", str(username or ""))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        if "uk_cap_flow_run_idem" in str(exc):
            raise FlowError("IDEMPOTENCY_CONFLICT",
                            "同一幂等键的流程已在创建中/已存在，请查询既有运行") from exc
        logger.exception("[cap-flow] 创建运行失败 scenario=%s", scenario_key)
        raise FlowError("EXECUTION_FAILED", f"流程创建失败：{type(exc).__name__}") from exc
    finally:
        conn.close()

    return advance_run(run_id, worker=f"user:{user_id}") or get_run(run_id)


def _event_in(conn, run_id: int, step_no: int, event_type: str, detail: str,
              actor: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO adh_capability_flow_events "
            "(run_id, step_no, event_type, detail, actor) VALUES (%s,%s,%s,%s,%s)",
            (int(run_id), int(step_no), event_type, str(detail or "")[:500],
             str(actor or "")[:64]))


# ── 推进引擎（租约 + 条件更新；单步单 worker）──────────────────────────────

def _claim_run(run_id: int, worker: str) -> bool:
    return _w(
        "UPDATE adh_capability_flow_runs SET lease_owner=%s, "
        "lease_until=NOW() + INTERVAL %s SECOND "
        "WHERE id=%s AND status IN ('pending','running') "
        "AND (lease_until IS NULL OR lease_until < NOW() OR lease_owner=%s)",
        (str(worker)[:64], LEASE_SECONDS, int(run_id), str(worker)[:64])) == 1


def _claim_step(step_id: int, worker: str) -> bool:
    """条件更新抢占：只有 status='pending' 且租约空/过期的 worker 拿到（并发只执行一次）。"""
    return _w(
        "UPDATE adh_capability_flow_steps SET status='running', lease_owner=%s, "
        "lease_until=NOW() + INTERVAL %s SECOND, attempts=attempts+1, started_at=NOW() "
        "WHERE id=%s AND status='pending' "
        "AND (lease_until IS NULL OR lease_until < NOW() OR lease_owner=%s)",
        (str(worker)[:64], LEASE_SECONDS, int(step_id), str(worker)[:64])) == 1


def _exec_ctx_for(run: dict):
    from types import SimpleNamespace

    return SimpleNamespace(user_id=int(run.get("initiated_by") or 0),
                           workspace_id=int(run.get("workspace_id") or 0),
                           username=str(run.get("initiated_by_name") or ""))


def _step_spec(row: dict) -> dict:
    raw = row.get("input_json")
    try:
        spec = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        spec = {}
    if not isinstance(spec, dict):
        spec = {}
    # 兼容旧形（裸 bindings）
    if "bindings" not in spec:
        spec = {"bindings": spec, "outputs": {}, "idem": "", "retry": {}}
    return spec


def _step_retry_limit(spec: dict) -> int:
    raw = spec.get("retry") if isinstance(spec.get("retry"), dict) else {}
    try:
        return max(0, int(raw.get("max_attempts") or 1))
    except (TypeError, ValueError):
        return 1


def advance_run(run_id: int, *, worker: str = "flow-worker") -> dict | None:
    """把运行推进到下一阻塞点（waiting_human）或终态；并发安全（租约+条件更新）。"""
    from backend.modules.mind.execution import capability_runtime as caprt
    from backend.modules.mind.execution.capability_runtime import CapabilityRuntimeError

    run_rows = _q("SELECT * FROM adh_capability_flow_runs WHERE id=%s", (int(run_id),))
    if not run_rows:
        raise FlowError("RUN_NOT_FOUND", f"流程运行 {run_id} 不存在")
    run = run_rows[0]
    if str(run.get("status")) in TERMINAL_RUN_STATUSES:
        return _public_run(run)
    if not _claim_run(int(run_id), worker):
        return None  # 另一个 worker 正在推进（并发只执行一次）

    run_input = {}
    try:
        raw = run.get("input_json")
        run_input = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        run_input = {}
    ctx_values: dict = {}
    exec_ctx = _exec_ctx_for(run)

    while True:
        steps = _q("SELECT * FROM adh_capability_flow_steps WHERE run_id=%s "
                   "ORDER BY step_no", (int(run_id),))
        nxt = next((s for s in steps if str(s.get("status")) == STEP_PENDING), None)
        if nxt is None:
            break
        # 人工步骤：绝不自动执行，停 waiting_human（AI 不得代做）
        if str(nxt.get("channel")) == "human":
            _w("UPDATE adh_capability_flow_steps SET status=%s WHERE id=%s AND status=%s",
               (STEP_WAITING_HUMAN, int(nxt["id"]), STEP_PENDING))
            _w("UPDATE adh_capability_flow_runs SET status=%s, current_step=%s, "
               "lease_owner='', lease_until=NULL WHERE id=%s",
               (RUN_WAITING_HUMAN, int(nxt.get("step_no") or 0), int(run_id)))
            _event(int(run_id), int(nxt.get("step_no") or 0), "waiting_human",
                   f"action={nxt.get('action_key')} 人工步骤待完成（入口：内容中心发布）",
                   worker)
            return get_run(int(run_id))

        if not _claim_step(int(nxt["id"]), worker):
            return None  # 并发抢占失败：让拿租约的 worker 继续

        step_no = int(nxt.get("step_no") or 0)
        spec = _step_spec(nxt)
        try:
            args = _resolve_step_args(spec.get("bindings") or {}, run_input, ctx_values)
        except FlowError as exc:
            _fail_step(int(nxt["id"]), int(run_id), step_no, exc.code, str(exc), worker)
            return get_run(int(run_id))

        idem = _eval_template(spec.get("idem") or "", run_input, ctx_values)
        idem = idem or f"capflow:{run_id}:{step_no}"
        try:
            result = caprt.execute_action(str(nxt.get("action_key") or ""), args,
                                          ctx=exec_ctx, idempotency_key=idem)
        except CapabilityRuntimeError as exc:
            attempts = int(nxt.get("attempts") or 0)
            if exc.code in TRANSIENT_ERROR_CODES and attempts < _step_retry_limit(spec):
                _w("UPDATE adh_capability_flow_steps SET status=%s, lease_owner='', "
                   "lease_until=NULL, error_code=%s, error_message=%s WHERE id=%s",
                   (STEP_PENDING, exc.code, str(exc)[:500], int(nxt["id"])))
                _event(int(run_id), step_no, "step_retry",
                       f"action={nxt.get('action_key')} transient={exc.code} attempts={attempts}",
                       worker)
                continue
            _fail_step(int(nxt["id"]), int(run_id), step_no, exc.code, str(exc), worker)
            return get_run(int(run_id))

        written = _apply_output_bindings(spec.get("outputs") or {}, result, ctx_values)
        _w("UPDATE adh_capability_flow_steps SET status=%s, output_ref=%s, "
           "error_code='', error_message='', lease_owner='', lease_until=NULL, "
           "finished_at=NOW() WHERE id=%s",
           (STEP_SUCCEEDED, json.dumps(written, ensure_ascii=False, default=str)[:191],
            int(nxt["id"])))
        # 上下文持久化到 run.output_json：人工复核与后续步骤都从这里取（不靠内存）
        _w("UPDATE adh_capability_flow_runs SET current_step=%s, output_json=%s WHERE id=%s",
           (step_no, json.dumps(ctx_values, ensure_ascii=False, default=str), int(run_id)))
        _event(int(run_id), step_no, "step_succeeded",
               f"action={nxt.get('action_key')} output_ref={list(written)}", worker)

    # 循环结束：全部步骤非 pending → 按步骤状态判运行态
    steps = _q("SELECT status FROM adh_capability_flow_steps WHERE run_id=%s", (int(run_id),))
    statuses = [str(s.get("status")) for s in steps]
    if any(s == STEP_FAILED for s in statuses):
        _w("UPDATE adh_capability_flow_runs SET status=%s, lease_owner='', "
           "lease_until=NULL, finished_at=NOW() WHERE id=%s AND status IN ('pending','running')",
           (RUN_FAILED, int(run_id)))
    elif all(s == STEP_SUCCEEDED for s in statuses):
        _w("UPDATE adh_capability_flow_runs SET status=%s, lease_owner='', "
           "lease_until=NULL, finished_at=NOW(), output_json=%s WHERE id=%s",
           (RUN_SUCCEEDED,
            json.dumps({k: v for k, v in ctx_values.items()}, ensure_ascii=False,
                       default=str), int(run_id)))
        _event(int(run_id), 0, "succeeded", "全部步骤完成", worker)
    return get_run(int(run_id))


def _fail_step(step_id: int, run_id: int, step_no: int, code: str, message: str,
               worker: str) -> None:
    _w("UPDATE adh_capability_flow_steps SET status=%s, error_code=%s, error_message=%s, "
       "lease_owner='', lease_until=NULL, finished_at=NOW() WHERE id=%s",
       (STEP_FAILED, str(code)[:64], str(message)[:500], int(step_id)))
    _w("UPDATE adh_capability_flow_runs SET status=%s, error_code=%s, error_message=%s, "
       "lease_owner='', lease_until=NULL, finished_at=NOW() WHERE id=%s "
       "AND status IN ('pending','running')",
       (RUN_FAILED, str(code)[:64], str(message)[:500], int(run_id)))
    _event(int(run_id), step_no, "step_failed",
           f"code={code} detail={str(message)[:200]}", worker)


# ── 人工步骤复核（读内容服务事实源，不信任客户端"已完成"）─────────────────

def resume_human_step(run_id: int, *, actor: str = "") -> dict:
    """人工步骤完成复核：读内容服务状态确认发布事实，再继续/收尾。

    不接受客户端直接宣告"已完成"：发布与否只看内容服务的 live 发布事实。
    """
    rows = _q("SELECT * FROM adh_capability_flow_runs WHERE id=%s", (int(run_id),))
    if not rows:
        raise FlowError("RUN_NOT_FOUND", f"流程运行 {run_id} 不存在")
    run = rows[0]
    if str(run.get("status")) != RUN_WAITING_HUMAN:
        raise FlowError("RUN_NOT_WAITING",
                        f"运行当前状态是 {run.get('status')!r}，只有 waiting_human 可复核")
    steps = _q("SELECT * FROM adh_capability_flow_steps WHERE run_id=%s ORDER BY step_no",
               (int(run_id),))
    waiting = next((s for s in steps if str(s.get("status")) == STEP_WAITING_HUMAN), None)
    if waiting is None:
        raise FlowError("STEP_NOT_WAITING", "没有等待人工的步骤")

    ctx_values: dict = {}
    try:
        raw = run.get("output_json")
        parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if isinstance(parsed, dict):
            ctx_values.update(parsed)
    except (TypeError, ValueError):
        pass
    action_key = str(waiting.get("action_key") or "")
    slug = str(ctx_values.get("article.slug") or "")

    if action_key == "content_review.publish":
        if not slug:
            raise FlowError("HUMAN_VERIFY_FAILED",
                            "上下文缺少 article.slug，无法复核发布事实（不信任客户端宣告）")
        from backend.modules.platform.services import content_service

        try:
            article = content_service.get_article(slug=slug, can_edit=True)
        except Exception as exc:  # noqa: BLE001
            raise FlowError("HUMAN_VERIFY_FAILED",
                            f"复核发布事实失败：{type(exc).__name__}") from exc
        published = int(article.get("live_revision_id") or 0) > 0 and \
            str(article.get("status") or "") != content_service.STATUS_ARCHIVED
        if not published:
            raise FlowError("HUMAN_PENDING",
                            f"内容「{slug}」尚未发布（内容服务 live 版本为空），"
                            f"人工步骤仍待完成；发布后重新复核")
    # 其它人工动作：显式拒（不开"默认通过"的口子）
    else:
        raise FlowError("HUMAN_VERIFY_UNSUPPORTED",
                        f"动作 {action_key} 的人工复核器未实现（不默认通过）")

    _w("UPDATE adh_capability_flow_steps SET status=%s, finished_at=NOW() "
       "WHERE id=%s AND status=%s",
       (STEP_SUCCEEDED, int(waiting["id"]), STEP_WAITING_HUMAN))
    _event(int(run_id), int(waiting.get("step_no") or 0), "human_verified",
           f"action={action_key} ref={slug}", str(actor or ""))
    _w("UPDATE adh_capability_flow_runs SET status=%s WHERE id=%s",
       (RUN_RUNNING, int(run_id)))
    return advance_run(int(run_id), worker=f"human:{actor or 'operator'}") or get_run(int(run_id))


# ── 取消（无 compensator 声明：留可诊断半态，不做假回滚）──────────────────

def cancel_run(run_id: int, *, actor: str = "", user_id: int = 0,
               workspace_id: int = 0) -> dict:
    _require_run_perm(user_id, workspace_id, "取消能力流程")
    rows = _q("SELECT * FROM adh_capability_flow_runs WHERE id=%s", (int(run_id),))
    if not rows:
        raise FlowError("RUN_NOT_FOUND", f"流程运行 {run_id} 不存在")
    run = rows[0]
    if str(run.get("status")) in TERMINAL_RUN_STATUSES:
        raise FlowError("RUN_TERMINAL", f"运行已是终态 {run.get('status')!r}，不可取消")
    affected = _w(
        "UPDATE adh_capability_flow_runs SET status=%s, lease_owner='', lease_until=NULL, "
        "finished_at=NOW(), error_code='CANCELLED', "
        "error_message='运行被取消；已创建的业务数据不自动删除（动作无补偿器声明），半态可诊断' "
        "WHERE id=%s AND status NOT IN ('succeeded','failed','cancelled')",
        (RUN_CANCELLED, int(run_id)))
    if affected:
        _w("UPDATE adh_capability_flow_steps SET status='cancelled' "
           "WHERE run_id=%s AND status IN ('pending','running','waiting_human')",
           (int(run_id),))
        _event(int(run_id), 0, "cancelled",
               "取消不自动删除已创建业务数据（无 compensator），半态留可诊断",
               str(actor or ""))
    return get_run(int(run_id))


# ── 读面（安全 DTO：不含正文/凭据）────────────────────────────────────────

def _public_run(row: dict) -> dict:
    input_json = row.get("input_json")
    try:
        input_json = json.loads(input_json) if isinstance(input_json, str) else (input_json or {})
    except (TypeError, ValueError):
        input_json = {}
    output_json = row.get("output_json")
    try:
        output_json = json.loads(output_json) if isinstance(output_json, str) else (output_json or {})
    except (TypeError, ValueError):
        output_json = {}
    return {
        "run_id": int(row.get("id") or 0),
        "scenario": str(row.get("scenario_key") or ""),
        "scenario_title": str(row.get("scenario_title") or ""),
        "status": str(row.get("status") or ""),
        "current_step": int(row.get("current_step") or 0),
        "initiated_by_name": str(row.get("initiated_by_name") or ""),
        "input": {k: v for k, v in (input_json or {}).items()},
        "output": output_json or {},
        "idempotency_key": str(row.get("idempotency_key") or ""),
        "error_code": str(row.get("error_code") or ""),
        "error_message": str(row.get("error_message") or ""),
        "created_at": _iso(row.get("created_at")),
        "finished_at": _iso(row.get("finished_at")),
    }


def _public_step(row: dict) -> dict:
    return {
        "step_no": int(row.get("step_no") or 0),
        "action": str(row.get("action_key") or ""),
        "label": str(row.get("action_label") or ""),
        "channel": str(row.get("channel") or ""),
        "status": str(row.get("status") or ""),
        "attempts": int(row.get("attempts") or 0),
        "output_ref": str(row.get("output_ref") or ""),
        "error_code": str(row.get("error_code") or ""),
        "error_message": str(row.get("error_message") or ""),
        "finished_at": _iso(row.get("finished_at")),
    }


def get_run(run_id: int) -> dict | None:
    rows = _q("SELECT * FROM adh_capability_flow_runs WHERE id=%s", (int(run_id),))
    if not rows:
        return None
    out = _public_run(rows[0])
    out["steps"] = [_public_step(s) for s in
                    _q("SELECT * FROM adh_capability_flow_steps WHERE run_id=%s ORDER BY step_no",
                       (int(run_id),))]
    out["events"] = [{"step_no": int(e.get("step_no") or 0),
                      "event": str(e.get("event_type") or ""),
                      "detail": str(e.get("detail") or ""),
                      "actor": str(e.get("actor") or ""),
                      "at": _iso(e.get("created_at"))}
                     for e in _q("SELECT * FROM adh_capability_flow_events "
                                 "WHERE run_id=%s ORDER BY id", (int(run_id),))]
    return out


def list_runs(*, page: int = 1, page_size: int = 20, status: str = "",
              q: str = "") -> dict:
    """服务端真分页（list-pagination §2）：COUNT 与列表共用筛选，稳定排序含 id tiebreaker。"""
    try:
        current = max(1, int(page or 1))
        size = min(max(1, int(page_size or 20)), 50)
    except (TypeError, ValueError) as exc:
        raise FlowError("QUERY_INVALID", "page/page_size 必须为正整数") from exc
    where, params = [], ()
    if status:
        if status not in (RUN_PENDING, RUN_RUNNING, RUN_WAITING_HUMAN,
                          RUN_SUCCEEDED, RUN_FAILED, RUN_CANCELLED):
            raise FlowError("QUERY_INVALID", f"status 无效：{status}")
        where.append("status=%s")
        params += (status,)
    keyword = str(q or "").strip()[:128]
    if keyword:
        where.append("(scenario_key LIKE %s OR scenario_title LIKE %s)")
        params += (f"%{keyword}%", f"%{keyword}%")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    total = int((_q(f"SELECT COUNT(*) AS n FROM adh_capability_flow_runs{where_sql}", params)
                 or [{}])[0].get("n") or 0)
    rows = _q(f"SELECT * FROM adh_capability_flow_runs{where_sql} "
              f"ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
              params + (size, (current - 1) * size))
    return {"items": [_public_run(r) for r in rows], "total": total,
            "page": current, "page_size": size}


__all__ = [
    "FlowError", "advance_run", "auto_enabled", "allowed_scenarios", "cancel_run",
    "create_run", "get_run", "list_runs", "resume_human_step",
]
