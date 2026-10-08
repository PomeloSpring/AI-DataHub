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
    EXPECTED_KEYS, SUITES, SUITE_LABELS, CaseContractError,
)

router = APIRouter()


# ── Request models ──────────────────────────────────────────────────────────

class CasePayload(BaseModel):
    case_key: str
    suite: str
    question: str
    tags: list[str] = []
    payload: dict = {}
    expected: dict = {}
    note: str = ""
    is_active: int = 1
    sort: int = 0


class CaseUpdate(BaseModel):
    case_key: Optional[str] = None
    suite: Optional[str] = None
    question: Optional[str] = None
    tags: Optional[list[str]] = None
    payload: Optional[dict] = None
    expected: Optional[dict] = None
    note: Optional[str] = None
    is_active: Optional[int] = None
    sort: Optional[int] = None


class RunRequest(BaseModel):
    suite: str = "all"
    mode: str = "sync"          # sync | async（async 走 Celery）
    trigger_type: str = "manual"


def _store_error(e: Exception) -> HTTPException:
    return HTTPException(status_code=500, detail=f"评测数据读写失败：{e}")


# ── 评测集元信息（供页面渲染表单）──────────────────────────────────────────────

@router.get("/suites")
def list_suites(user: dict = Depends(get_current_user)):
    """评测集清单 + 各集支持的期望键（页面据此渲染用例表单）。"""
    return {
        "suites": [
            {"suite": s, "label": SUITE_LABELS.get(s, s),
             "expected_keys": sorted(EXPECTED_KEYS[s])}
            for s in SUITES
        ]
    }


# ── 用例 CRUD ───────────────────────────────────────────────────────────────

@router.get("/cases")
def list_cases(suite: str = Query(""), tags: str = Query(""),
               include_inactive: bool = Query(False), user: dict = Depends(get_current_user)):
    if suite and suite not in SUITES:
        raise HTTPException(status_code=400, detail=f"suite 无效，应为 {list(SUITES)}")
    try:
        rows = store.list_cases(suite=suite, tags=tags, active_only=not include_inactive)
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
            from backend.modules.flow.tasks.eval_tasks import run_eval
            run_eval.delay(body.suite, body.trigger_type, owner)
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
    """发布前检查：各评测集最近一次运行是否低于基线。

    任一 ``baseline_ok=False`` 即整体不通过 —— 供发布流程做门禁。
    """
    try:
        out = {}
        all_ok = True
        for s in SUITES:
            run = store.latest_done_run(s)
            if not run:
                out[s] = {"has_run": False, "baseline_ok": True,
                          "note": "尚无已完成运行，无法判断"}
                continue
            ok = bool(run.get("baseline_ok"))
            all_ok = all_ok and ok
            out[s] = {
                "has_run": True,
                "run_id": int(run["id"]),
                "baseline_ok": ok,
                "accuracy": float(run.get("accuracy") or 0),
                "total": int(run.get("total") or 0),
                "passed": int(run.get("passed") or 0),
                "finished_at": run.get("finished_at"),
                "note": run.get("error") or "",
            }
    except store.EvalStoreError as e:
        raise _store_error(e) from e
    return {
        "suites": out,
        "release_ok": all_ok,
        "verdict": "各评测集均未低于基线，可以发布" if all_ok else "存在回退，发布前检查不通过",
    }
