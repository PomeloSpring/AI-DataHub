"""评测工程能力 API — 用例 CRUD / 运行 / 结果 / 版本对比 / 发布前检查。

放在 authservice 是因为它与 observability 同属"运营与治理"读面，
管理页也在一起（既定决策：结果存 MySQL 并在现有管理页做版本对比）。

安全口径：
    * 用例增删改与运行触发是**写操作**，一律 ``require_admin``；
    * 读接口走 ``get_current_user``，由 api_permission 中间件按权限码门控；
    * 落库失败抛 ``EvalStoreError`` → 这里转 500 并把原因回给用户，
      **不静默返回空结果**（结果丢了却报"通过"是降级掩盖）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from backend.common.auth import get_current_user, require_admin
from backend.eval import store
from backend.eval.compare import compare_runs, verdict_text
from backend.eval.contract import (
    EXPECTED_KEYS, SUITES, SUITE_LABELS, SUITE_SCOPE_NOTES, CaseContractError,
)
from backend.eval.gate import (
    CAPABILITIES, CAPABILITY_LABELS, CASE_STATUSES, GateError, evaluate_buckets, gate_text,
)

router = APIRouter()


# ── Request models ──────────────────────────────────────────────────────────

class CasePayload(BaseModel):
    case_key: str
    suite: str
    question: str
    capability: str
    tags: list[str] = []
    payload: dict = {}
    expected: dict = {}
    note: str = ""
    is_active: int = 1
    case_status: str = "active"
    status_note: str = ""
    sort: int = 0


class CaseUpdate(BaseModel):
    case_key: Optional[str] = None
    suite: Optional[str] = None
    question: Optional[str] = None
    capability: Optional[str] = None
    tags: Optional[list[str]] = None
    payload: Optional[dict] = None
    expected: Optional[dict] = None
    note: Optional[str] = None
    is_active: Optional[int] = None
    case_status: Optional[str] = None
    status_note: Optional[str] = None
    sort: Optional[int] = None


class RunRequest(BaseModel):
    suite: str = "all"
    mode: str = "sync"          # sync | async（async 走 Celery）
    trigger_type: str = "manual"


class GatePolicyPayload(BaseModel):
    suite: str
    capability: str = ""          # 空串=该层整体下限
    floor_accuracy: float = 1.0
    min_cases: int = 0
    max_live_cases_per_run: Optional[int] = None
    enabled: int = 1


def _store_error(e: Exception) -> HTTPException:
    return HTTPException(status_code=500, detail=f"评测数据读写失败：{e}")


# ── 评测集元信息（供页面渲染表单）──────────────────────────────────────────────

@router.get("/suites")
def list_suites(user: dict = Depends(get_current_user)):
    """评测层清单 + 契约组 + 各层可用期望键（页面据此渲染用例表单）。

    ``scope_note`` 是故意给前端的：不告诉用户“L1 跑的是 mock 目录、L4 的 trace
    是自录轨迹回放”，一个总通过率就会被误读成平台能力。
    """
    return {
        "suites": [
            {"suite": s, "label": SUITE_LABELS.get(s, s),
             "scope_note": SUITE_SCOPE_NOTES.get(s, ""),
             "expected_keys": sorted(EXPECTED_KEYS[s])}
            for s in SUITES
        ],
        "capabilities": [{"key": c, "label": CAPABILITY_LABELS.get(c, c)} for c in CAPABILITIES],
        "case_statuses": list(CASE_STATUSES),
    }


@router.get("/coverage")
def coverage(user: dict = Depends(get_current_user)):
    """capability × layer 覆盖矩阵：“内容是否符合要求”据此判定，不数条数。

    空格就是缺口。没用例的格 ``scored=0``；有用例但未跑过的格 ``accuracy=None``
    （未知不等于 0%，也不等于通过）。
    """
    results = {s: store.latest_result_matrix(s) for s in SUITES}
    cells = []
    for m in store.coverage_matrix():
        cap = m.get("capability") or ""
        got = (results.get(m["suite"]) or {}).get(cap) or {}
        cells.append({
            **m,
            "capability_label": CAPABILITY_LABELS.get(cap, cap or "未登记"),
            "suite_label": SUITE_LABELS.get(m["suite"], m["suite"]),
            "accuracy": got.get("accuracy"),
            "run_id": got.get("run_id"),
        })
    # 把"库里根本没这个格"的组合也补出来，否则矩阵只显现有项、缺口看不全
    seen = {(c["suite"], c["capability"]) for c in cells}
    for s in SUITES:
        for cap in CAPABILITIES:
            if (s, cap) not in seen:
                cells.append({"suite": s, "capability": cap, "total": 0, "scored": 0,
                              "retired": 0, "known_defect": 0, "accuracy": None, "run_id": None,
                              "capability_label": CAPABILITY_LABELS[cap],
                              "suite_label": SUITE_LABELS.get(s, s)})
    return {"cells": cells, "suites": list(SUITES), "capabilities": list(CAPABILITIES)}


@router.get("/gate")
def list_gate(user: dict = Depends(get_current_user)):
    """门禁阈值清单（绝对下限，与相对回退判定正交）。"""
    try:
        rows = store.list_gate_policies()
    except store.EvalStoreError as e:
        raise _store_error(e) from e
    return {"items": rows, "total": len(rows),
            "capabilities": [{"key": c, "label": CAPABILITY_LABELS.get(c, c)}
                             for c in CAPABILITIES]}


@router.post("/gate")
def upsert_gate(body: GatePolicyPayload, user: dict = Depends(require_admin)):
    """新增/修改一条门禁阈值。不合法直接 400，**不默认成“通过”**。"""
    try:
        row = store.upsert_gate_policy(body.model_dump(),
                                       updated_by=str(user.get("username") or ""))
    except GateError as exc:
        raise HTTPException(status_code=400, detail=f"门禁策略不合法：{exc}") from exc
    except store.EvalStoreError as e:
        raise _store_error(e) from e
    return row


@router.delete("/gate/{policy_id}")
def delete_gate(policy_id: int, user: dict = Depends(require_admin)):
    """删策略会使该层“无启用门禁”；precheck 据此报门禁失效而不是静默放行。"""
    try:
        ok = store.delete_gate_policy(policy_id)
    except store.EvalStoreError as e:
        raise _store_error(e) from e
    if not ok:
        raise HTTPException(status_code=404, detail="门禁策略不存在")
    return {"id": policy_id, "deleted": True}


# ── 用例 CRUD ───────────────────────────────────────────────────────────────

@router.get("/cases")
def list_cases(suite: str = Query(""), tags: str = Query(""),
               case_status: str = Query(""),
               include_inactive: bool = Query(False), user: dict = Depends(get_current_user)):
    if suite and suite not in SUITES:
        raise HTTPException(status_code=400, detail=f"suite 无效，应为 {list(SUITES)}")
    if case_status and case_status not in CASE_STATUSES:
        raise HTTPException(status_code=400, detail=f"case_status 无效，应为 {list(CASE_STATUSES)}")
    try:
        rows = store.list_cases(suite=suite, tags=tags, active_only=not include_inactive,
                                case_status=case_status)
    except store.EvalStoreError as e:
        raise _store_error(e) from e
    return {"items": rows, "total": len(rows)}


@router.get("/cases/{case_id}")
def get_case(case_id: int, user: dict = Depends(get_current_user)):
    try:
        row = store.get_case(case_id)
    except store.EvalStoreError as e:
        raise _store_error(e) from e
    if not row:
        raise HTTPException(status_code=404, detail="用例不存在")
    return row


@router.post("/cases")
def create_case(body: CasePayload, user: dict = Depends(require_admin)):
    try:
        cid = store.create_case(body.model_dump(), created_by=str(user.get("username") or ""))
    except CaseContractError as exc:
        raise HTTPException(status_code=400, detail=f"用例不合法：{exc}") from exc
    except store.EvalStoreError as e:
        raise _store_error(e) from e
    return {"id": cid, "case_key": body.case_key}


@router.put("/cases/{case_id}")
def update_case(case_id: int, body: CaseUpdate, user: dict = Depends(require_admin)):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        ok = store.update_case(case_id, data)
    except CaseContractError as exc:
        raise HTTPException(status_code=400, detail=f"用例不合法：{exc}") from exc
    except store.EvalStoreError as e:
        raise _store_error(e) from e
    return {"id": case_id, "updated": bool(ok)}


@router.post("/cases/{case_id}/active")
def set_case_active(case_id: int, active: bool = Query(...), user: dict = Depends(require_admin)):
    try:
        ok = store.set_case_active(case_id, active)
    except store.EvalStoreError as e:
        raise _store_error(e) from e
    return {"id": case_id, "is_active": bool(active), "updated": bool(ok)}


@router.delete("/cases/{case_id}")
def delete_case(case_id: int, user: dict = Depends(require_admin)):
    """硬删除用例。历史运行结果保留（按 case_key 可诊断为"已删"）。"""
    try:
        ok = store.delete_case(case_id)
    except store.EvalStoreError as e:
        raise _store_error(e) from e
    return {"id": case_id, "deleted": bool(ok)}


# ── 运行与结果 ──────────────────────────────────────────────────────────────

@router.post("/runs")
def create_run(body: RunRequest, user: dict = Depends(require_admin)):
    """触发一次评测。

    ``mode=sync``：同步跑完返回报告（适合 compile 这类快集）。
    ``mode=async``：丢给 Celery，返回 run_id 供轮询（retrieval/llm 可能较慢）。
    """
    if body.suite != "all" and body.suite not in SUITES:
        raise HTTPException(status_code=400, detail=f"suite 无效，应为 all 或 {list(SUITES)}")
    if body.mode not in ("sync", "async"):
        raise HTTPException(status_code=400, detail="mode 只能是 sync 或 async")

    owner = str(user.get("username") or "")
    if body.mode == "async":
        try:
            from backend.core.task_runtime import send_task
            send_task("backend.modules.flow.tasks.eval_tasks.run_eval",
                      args=(body.suite, body.trigger_type, owner))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"评测任务投递失败：{e}") from e
        return {"accepted": True, "mode": "async", "suite": body.suite,
                "note": "已投递评测任务，请在运行列表中刷新查看结果"}

    from backend.eval.runner import run_all, run_suite
    try:
        if body.suite == "all":
            out = run_all(trigger_type=body.trigger_type, created_by=owner, persist=True)
        else:
            out = run_suite(body.suite, trigger_type=body.trigger_type,
                            created_by=owner, persist=True)
    except store.EvalStoreError as e:
        raise _store_error(e) from e
    except CaseContractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"accepted": True, "mode": "sync", "report": out}


@router.get("/runs")
def list_runs(suite: str = Query(""), limit: int = Query(50, ge=1, le=200),
              user: dict = Depends(get_current_user)):
    try:
        rows = store.list_runs(suite=suite, limit=limit)
    except store.EvalStoreError as e:
        raise _store_error(e) from e
    return {"items": rows, "total": len(rows)}


@router.get("/runs/{run_id}")
def get_run(run_id: int, user: dict = Depends(get_current_user)):
    try:
        row = store.get_run(run_id)
    except store.EvalStoreError as e:
        raise _store_error(e) from e
    if not row:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return row


@router.get("/runs/{run_id}/results")
def get_run_results(run_id: int, only_failed: bool = Query(False),
                    user: dict = Depends(get_current_user)):
    try:
        rows = store.get_results(run_id, only_failed=only_failed)
    except store.EvalStoreError as e:
        raise _store_error(e) from e
    return {"items": rows, "total": len(rows)}


# ── 版本对比与发布前检查 ────────────────────────────────────────────────────

@router.get("/runs/{run_id}/compare")
def compare_run(run_id: int, baseline_id: int = Query(None),
                user: dict = Depends(get_current_user)):
    """把指定运行与基线运行对比（不传 baseline_id 则与最近一条已完成运行比）。

    注意：只比确定性 ``passed``，不看 LLM 评分（既定决策）。
    """
    try:
        cur = store.get_run(run_id)
        if not cur:
            raise HTTPException(status_code=404, detail="运行记录不存在")
        cur_rows = store.get_results(run_id)
        if baseline_id:
            base = store.get_run(baseline_id)
            if not base:
                raise HTTPException(status_code=404, detail="基线运行不存在")
            base_rows = store.get_results(baseline_id)
        else:
            base = store.latest_done_run(cur["suite"], exclude_run_id=run_id)
            base_rows = store.get_results(int(base["id"])) if base else []
    except store.EvalStoreError as e:
        raise _store_error(e) from e

    cmp = compare_runs(cur_rows, base_rows)
    return {
        "run_id": run_id,
        "baseline_run_id": int(base["id"]) if base else None,
        "verdict": verdict_text(cmp),
        **cmp,
    }


@router.get("/precheck")
def precheck(user: dict = Depends(get_current_user)):
    """发布前检查：**双口径** —— 相对未回退 AND 达绝对下限。

    三个必须说清的口径，每一个都对应一个已发生或可预见的掩盖：

    1. **不信历史行里的 ``floor_ok`` 快照**。阈值会调、用例会改、而且迁移前的
       旧运行行该列默认是 1。结论按当前用例表 + 最近一次运行的实跑结果**重算**。
    2. **“尚无运行”不等于通过**（旧口径把无运行当 ok，整体仍报绿）。未知就是不过。
    3. ``baseline_ok`` 保留原义（只表相对未回退），对外结论看 ``release_ok``。
    """
    try:
        policies = store.list_gate_policies()
        counts = {}
        for m in store.coverage_matrix():
            b = counts.setdefault(m["suite"], {"scored": 0, "retired": 0, "known_defect": 0})
            b["scored"] += int(m.get("scored") or 0)
            b["retired"] += int(m.get("retired") or 0)
            b["known_defect"] += int(m.get("known_defect") or 0)

        out, release_ok = {}, True
        for s in SUITES:
            run = store.latest_done_run(s)
            c = counts.get(s) or {"scored": 0, "retired": 0, "known_defect": 0}
            if not run:
                # 无运行 = 无法背书 = 不过；旧口径在这里写 baseline_ok=True 是假绿灯的一半根因
                release_ok = False
                out[s] = {"has_run": False, "baseline_ok": False, "floor_ok": False,
                          "release_ok": False, "gate_verdict": "no_run",
                          "retired_count": c["retired"],
                          "known_defect_count": c["known_defect"],
                          "note": "尚无已完成运行，无法背书（未知不等于通过）"}
                continue

            matrix = store.latest_result_matrix(s)
            buckets = {"": {"passed": 0, "total": 0}}
            for cap, b in matrix.items():
                buckets[cap] = {"passed": int(b.get("passed") or 0),
                                "total": int(b.get("total") or 0)}
                buckets[""]["passed"] += int(b.get("passed") or 0)
                buckets[""]["total"] += int(b.get("total") or 0)
            gate_res = evaluate_buckets(s, buckets, policies)
            floor_ok = gate_res["floor_ok"]
            relative_ok = bool(run.get("baseline_ok"))
            verdict = ("ok" if floor_ok and relative_ok else
                       "below_floor" if not floor_ok and relative_ok else
                       "regression" if floor_ok and not relative_ok else "both")
            release_ok = release_ok and floor_ok and relative_ok
            ov = buckets[""]
            scored_accuracy = round(ov["passed"] / ov["total"], 4) if ov["total"] else 0.0
            out[s] = {
                "has_run": True,
                "run_id": int(run["id"]),
                "baseline_ok": relative_ok,
                "floor_ok": floor_ok,
                "release_ok": bool(floor_ok and relative_ok),
                "gate_verdict": verdict,
                "floor_verdict": gate_text(gate_res),
                "breaches": gate_res["breaches"],
                # 门禁判的就是这个分母（仅 active+known_defect）；另一个数字是当时快照
                "accuracy": scored_accuracy,
                "passed": ov["passed"],
                "total": ov["total"],
                # 历史运行行里的原值保留展示，并说清两者分母为何不同：
                # 退役用例后同一条运行重算会变，拿旧分母的百分比当判定口径会误导
                "run_accuracy_snapshot": float(run.get("accuracy") or 0),
                "run_total_snapshot": int(run.get("total") or 0),
                "finished_at": run.get("finished_at"),
                "scored_cases": c["scored"],
                "retired_count": c["retired"],
                "known_defect_count": c["known_defect"],
                "note": run.get("error") or "",
            }
    except store.EvalStoreError as e:
        raise _store_error(e) from e

    if release_ok:
        verdict_text_out = "各层均未回退且达绝对下限，可以发布"
    elif not all(v.get("floor_ok") for v in out.values()):
        verdict_text_out = "发布前检查不通过：有层低于绝对下限、用例数不足或未配门禁"
    elif not all(v.get("baseline_ok") for v in out.values()):
        verdict_text_out = "发布前检查不通过：出现回退"
    else:
        verdict_text_out = "发布前检查不通过：有层尚无已完成运行"
    return {
        "suites": out,
        "release_ok": release_ok,
        # 旧字段保留但不再等于发布结论，避免历史消费方拿到错语义还以为对
        "baseline_ok": bool(all(v.get("baseline_ok") for v in out.values())),
        "verdict": verdict_text_out,
    }
