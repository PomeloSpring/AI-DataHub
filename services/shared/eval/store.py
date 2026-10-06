"""评测用例与运行结果的持久化（MySQL 元库）。

与 ``observability.store`` 的**关键差别**：那边是可观测旁路，写失败只记日志；
这里是**质量门禁的真值源**，写失败必须抛 ``EvalStoreError`` —— 结果丢了却说
"评测通过"就是典型的降级掩盖（no-silent-degradation §1）。调用方（API / Celery）
拿到异常后要显式报给用户，不得吞掉继续跑。

用例是 DB 管理的（页面可增删改查），因此这里的读写都是唯一入口；
``case_key`` 是跨版本对比的稳定标识，同 suite 内唯一。
"""

from __future__ import annotations

import json
import logging
import uuid

logger = logging.getLogger("eval.store")


class EvalStoreError(RuntimeError):
    """评测数据读写失败。必须显式暴露给用户，不得静默降级。"""


def _conn():
    from services.shared.common.db.metadata_db import get_metadata_conn
    return get_metadata_conn()


def _loads(val, default):
    if val is None or val == "":
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (TypeError, ValueError):
        return default


def _row(row) -> dict:
    """DB 行 → dict，并把 JSON 列解析出来。"""
    d = dict(row)
    for k in ("payload", "expected", "by_tag", "by_source", "regression", "sources"):
        if k in d:
            d[k] = _loads(d[k], {} if k != "regression" else {})
    return d


def _exec(sql: str, params: tuple = (), *, fetchone=False, fetchall=False):
    """执行 SQL；任何异常都包成 EvalStoreError 抛出（fail-loud）。

    返回值：读语句回行/行列表；**写语句回受影响行数**。
    写语句不能回 None —— 否则调用方 `bool(_exec(...))` 恒为 False，
    接口会把"删成功"报成"deleted:false"，是个骗人的假失败。
    """
    try:
        conn = _conn()
    except Exception as e:  # noqa: BLE001
        raise EvalStoreError(f"评测库连接失败: {e}") from e
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if fetchone:
                r = cur.fetchone()
                out = _row(r) if r else None
            elif fetchall:
                out = [_row(r) for r in cur.fetchall()]
            else:
                out = int(cur.rowcount or 0)
        conn.commit()
        return out
    except EvalStoreError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("[eval.store] SQL 执行失败")
        raise EvalStoreError(f"评测数据读写失败: {e}") from e
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# 用例 CRUD（页面调用）
# ═══════════════════════════════════════════════════════════════════════════

_CASE_COLS = "id, case_key, suite, question, tags, payload, expected, note, is_active, sort, created_by, created_at, updated_at"


def list_cases(suite: str = "", tags: str = "", active_only: bool = True) -> list[dict]:
    cond, params = [], []
    if suite:
        cond.append("suite = %s")
        params.append(suite)
    if active_only:
        cond.append("is_active = 1")
    if tags:
        for t in [x.strip() for x in tags.split(",") if x.strip()]:
            cond.append("FIND_IN_SET(%s, tags)")
            params.append(t)
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    return _exec(f"SELECT {_CASE_COLS} FROM adh_eval_cases {where} ORDER BY suite, sort, id",
                 tuple(params), fetchall=True) or []


def get_case(case_id: int) -> dict | None:
    return _exec(f"SELECT {_CASE_COLS} FROM adh_eval_cases WHERE id=%s", (int(case_id),), fetchone=True)


def get_case_by_key(suite: str, case_key: str) -> dict | None:
    return _exec(f"SELECT {_CASE_COLS} FROM adh_eval_cases WHERE suite=%s AND case_key=%s",
                 (suite, case_key), fetchone=True)


def create_case(data: dict, created_by: str = "") -> int:
    """新建用例。case_key 冲突时抛 EvalStoreError（不静默覆盖别人的用例）。"""
    from services.shared.eval.contract import CaseContractError, normalize_case
    try:
        case = normalize_case({**data, "is_active": data.get("is_active", 1)})
    except CaseContractError:
        raise
    if get_case_by_key(case.suite, case.case_key):
        raise EvalStoreError(f"用例标识「{case.case_key}」在 {case.suite} 集内已存在，请换一个")
    _exec(
        "INSERT INTO adh_eval_cases (case_key, suite, question, tags, payload, expected, note, is_active, sort, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (case.case_key, case.suite, case.question, ",".join(case.tags),
         json.dumps(case.payload, ensure_ascii=False, default=str),
         json.dumps(case.expected, ensure_ascii=False, default=str),
         case.note, case.is_active, int(data.get("sort") or 0), created_by))
    row = get_case_by_key(case.suite, case.case_key)
    return int(row["id"]) if row else 0


def update_case(case_id: int, data: dict) -> bool:
    """更新用例，返回是否真的改动了行。"""
    from services.shared.eval.contract import CaseContractError, normalize_case
    old = get_case(case_id)
    if not old:
        raise EvalStoreError(f"用例 {case_id} 不存在")
    merged = {**old, **data, "id": old["id"], "case_key": data.get("case_key", old["case_key"])}
    try:
        case = normalize_case(merged)
    except CaseContractError:
        raise
    dup = get_case_by_key(case.suite, case.case_key)
    if dup and int(dup["id"]) != int(case_id):
        raise EvalStoreError(f"用例标识「{case.case_key}」在 {case.suite} 集内已被 {dup['id']} 占用")
    _exec(
        "UPDATE adh_eval_cases SET case_key=%s, suite=%s, question=%s, tags=%s, payload=%s, "
        "expected=%s, note=%s, is_active=%s, sort=%s WHERE id=%s",
        (case.case_key, case.suite, case.question, ",".join(case.tags),
         json.dumps(case.payload, ensure_ascii=False, default=str),
         json.dumps(case.expected, ensure_ascii=False, default=str),
         case.note, case.is_active, int(merged.get("sort") or 0), int(case_id)))
    return True  # 上面查到旧行且无重复冲突，UPDATE 必然作用到该行


def set_case_active(case_id: int, active: bool) -> bool:
    """启/停用用例，返回是否真的改动了行。"""
    return _exec("UPDATE adh_eval_cases SET is_active=%s WHERE id=%s",
                 (1 if active else 0, int(case_id))) > 0


def delete_case(case_id: int) -> bool:
    """硬删除用例，返回是否真的删掉了行。历史运行结果保留（按 case_key 关联），
    对比时可诊断为"已删"。"""
    return _exec("DELETE FROM adh_eval_cases WHERE id=%s", (int(case_id),)) > 0


# ═══════════════════════════════════════════════════════════════════════════
# 运行记录与结果
# ═══════════════════════════════════════════════════════════════════════════

def create_run(suite: str, trigger_type: str = "manual", created_by: str = "",
               run_key: str = "") -> int:
    """创建一条 queued 运行记录，返回 run_id。run_key 幂等：重复投递不重复建。"""
    key = run_key or f"eval-{uuid.uuid4().hex[:16]}"
    existing = _exec("SELECT id, status FROM adh_eval_runs WHERE run_key=%s", (key,), fetchone=True)
    if existing:
        return int(existing["id"])
    _exec(
        "INSERT INTO adh_eval_runs (run_key, suite, trigger_type, status, created_by, started_at) "
        "VALUES (%s,%s,%s,'queued',%s, NOW())",
        (key, suite, trigger_type, created_by))
    row = _exec("SELECT id FROM adh_eval_runs WHERE run_key=%s", (key,), fetchone=True)
    return int(row["id"]) if row else 0


def mark_run_running(run_id: int) -> None:
    _exec("UPDATE adh_eval_runs SET status='running', started_at=NOW() WHERE id=%s", (int(run_id),))


def finish_run(run_id: int, *, total: int, passed: int, accuracy: float,
               by_tag: dict, by_source: dict, baseline_run_id=None,
               baseline_ok: bool = True, regression: dict | None = None,
               error: str = "") -> None:
    """落一条运行的最终结果。

    error 非空表示运行级失败（如适配器异常）——照样落库并置 status='failed'，
    绝不静默留下一条"看起来跑过了"的记录。
    """
    status = "failed" if error else "done"
    _exec(
        "UPDATE adh_eval_runs SET status=%s, total=%s, passed=%s, accuracy=%s, by_tag=%s, "
        "by_source=%s, baseline_run_id=%s, baseline_ok=%s, regression=%s, error=%s, finished_at=NOW() "
        "WHERE id=%s",
        (status, int(total), int(passed), float(accuracy),
         json.dumps(by_tag or {}, ensure_ascii=False, default=str),
         json.dumps(by_source or {}, ensure_ascii=False, default=str),
         int(baseline_run_id) if baseline_run_id else None,
         1 if baseline_ok else 0,
         json.dumps(regression or {}, ensure_ascii=False, default=str),
         (error or "")[:1024], int(run_id)))


def save_results(run_id: int, results: list) -> None:
    """批量落逐用例结果（同一事务；冲突按 run_id+case_key 幂等覆盖）。"""
    if not results:
        return
    rows = []
    for r in results:
        rows.append((
            int(run_id), r.case_key, r.suite, 1 if r.passed else 0,
            (r.reason or "")[:1024],
            None if r.score is None else float(r.score),
            json.dumps(r.sources or {}, ensure_ascii=False, default=str),
            int(r.duration_ms or 0)))
    try:
        conn = _conn()
    except Exception as e:  # noqa: BLE001
        raise EvalStoreError(f"评测库连接失败: {e}") from e
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO adh_eval_results "
                "(run_id, case_key, suite, passed, reason, score, sources, duration_ms) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE passed=VALUES(passed), reason=VALUES(reason), "
                "score=VALUES(score), sources=VALUES(sources), duration_ms=VALUES(duration_ms)",
                rows)
        conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.exception("[eval.store] 落逐用例结果失败")
        raise EvalStoreError(f"评测结果落库失败: {e}") from e
    finally:
        conn.close()


def get_run(run_id: int) -> dict | None:
    return _exec("SELECT * FROM adh_eval_runs WHERE id=%s", (int(run_id),), fetchone=True)


def list_runs(suite: str = "", limit: int = 50) -> list[dict]:
    cond, params = [], []
    if suite:
        cond.append("suite = %s")
        params.append(suite)
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    return _exec(
        f"SELECT * FROM adh_eval_runs {where} ORDER BY id DESC LIMIT %s",
        tuple(params) + (int(limit),), fetchall=True) or []


def get_results(run_id: int, only_failed: bool = False) -> list[dict]:
    cond = "run_id=%s" + (" AND passed=0" if only_failed else "")
    return _exec(
        f"SELECT run_id, case_key, suite, passed, reason, score, sources, duration_ms "
        f"FROM adh_eval_results WHERE {cond} ORDER BY passed, case_key",
        (int(run_id),), fetchall=True) or []


def latest_done_run(suite: str, exclude_run_id=None) -> dict | None:
    """最近一条已完成运行，用作默认基线（发布前检查）。"""
    if exclude_run_id:
        return _exec(
            "SELECT * FROM adh_eval_runs WHERE suite=%s AND status='done' AND id<>%s "
            "ORDER BY id DESC LIMIT 1", (suite, int(exclude_run_id)), fetchone=True)
    return _exec(
        "SELECT * FROM adh_eval_runs WHERE suite=%s AND status='done' ORDER BY id DESC LIMIT 1",
        (suite,), fetchone=True)
