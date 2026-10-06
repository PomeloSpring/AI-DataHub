"""评测运行编排（工程能力的统一出口）。

用法::

    python -m services.shared.eval.runner                    # 跑全部评测集并落库
    python -m services.shared.eval.runner --suite compile    # 只跑语义层编译
    python -m services.shared.eval.runner --json             # 机器可读（供 CI）
    python -m services.shared.eval.runner --no-persist       # 只跑不落库（本地调试）

设计要点
--------
* **用例来自 DB**（``adh_eval_cases``），页面可增删改查；不再读 YAML。
* 三个评测集共用一套契约/评分/对比/落库；各层只提供 ``run_case`` 适配器。
* ``passed`` 由确定性断言给出；``score`` 是 LLM 辅助评分，**不影响** ``passed``。
* 每次运行都与"最近一条已完成运行"做基线对比，回退即 ``baseline_ok=False``
  （发布前检查的判定依据）。
* 落库失败抛 ``EvalStoreError``：结果丢了却报"通过"是降级掩盖，不许发生。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter

from services.shared.eval import compare as compare_mod
from services.shared.eval import store
from services.shared.eval.contract import (
    Case, CaseContractError, SUITES, SUITE_LABELS, check, normalize_case,
)

ADAPTERS = {
    "compile": "services.shared.eval.adapters.compile",
    "retrieval": "services.shared.eval.adapters.retrieval",
    "llm": "services.shared.eval.adapters.llm",
}

SOURCES_LABELS = {
    "compile": "解析命中档位（planner 名字解析，非检索来源）",
    "retrieval": "检索来源（retrieval strategy / rag_source，非解析档位）",
    "llm": "LLM 功能（工具调用轨迹 / 出站结果）",
}


def _adapter(suite: str):
    import importlib
    mod_name = ADAPTERS.get(suite)
    if not mod_name:
        raise CaseContractError(f"未知评测集: {suite!r}")
    return importlib.import_module(mod_name)


def _run_one(suite: str, case: Case) -> dict:
    """跑一条用例，返回 {passed, reason, score, sources, duration_ms}。"""
    started = time.monotonic()
    try:
        mod = _adapter(suite)
        observed = mod.run_case(case)
        if observed.get("error"):
            return {"passed": False, "reason": f"适配器报错: {observed['error']}",
                    "score": None, "sources": observed.get("sources") or {},
                    "duration_ms": int((time.monotonic() - started) * 1000)}
        ok, reason = check(suite, observed, case.expected)
        score = None
        if suite == "llm" and hasattr(mod, "score_answer"):
            score = mod.score_answer(case, observed)
        return {"passed": ok, "reason": reason, "score": score,
                "sources": observed.get("sources") or {},
                "duration_ms": int((time.monotonic() - started) * 1000)}
    except CaseContractError as exc:
        # 用例本身写得不对 —— 必须显式报出来，不能算"通过"也不能静默跳过
        return {"passed": False, "reason": f"用例不合法: {exc}",
                "score": None, "sources": {}, "duration_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001 —— 适配器异常也要落成失败，不可吞
        return {"passed": False, "reason": f"{type(exc).__name__}: {exc}",
                "score": None, "sources": {}, "duration_ms": int((time.monotonic() - started) * 1000)}


def run_suite(suite: str, *, trigger_type: str = "manual", created_by: str = "",
              persist: bool = True, run_key: str = "") -> dict:
    """跑一个评测集，返回报告 dict（可直接 JSON 化）。"""
    if suite not in SUITES:
        raise CaseContractError(f"未知评测集: {suite!r}")

    rows = store.list_cases(suite=suite, active_only=True)
    cases: list[Case] = []
    invalid: list[dict] = []
    for row in rows:
        try:
            cases.append(normalize_case(row))
        except CaseContractError as exc:
            invalid.append({"case_key": row.get("case_key"), "error": str(exc)})

    run_id = store.create_run(suite, trigger_type=trigger_type,
                              created_by=created_by, run_key=run_key) if persist else 0
    if persist and run_id:
        store.mark_run_running(run_id)

    results = []
    tag_total, tag_pass = Counter(), Counter()
    src_counter = Counter()
    for case in cases:
        r = _run_one(suite, case)
        results.append({
            "case_key": case.case_key, "suite": suite,
            "passed": r["passed"], "reason": r["reason"], "score": r["score"],
            "sources": r["sources"], "duration_ms": r["duration_ms"],
        })
        for t in case.tags:
            tag_total[t] += 1
            if r["passed"]:
                tag_pass[t] += 1
        for _name, src in (r["sources"] or {}).items():
            if isinstance(src, str) and src:
                src_counter[src] += 1

    total = len(cases)
    passed = sum(1 for r in results if r["passed"])
    accuracy = round(passed / total, 4) if total else 0.0
    by_tag = {t: {"passed": tag_pass[t], "total": tag_total[t],
                  "accuracy": round(tag_pass[t] / tag_total[t], 4) if tag_total[t] else 0.0}
              for t in sorted(tag_total)}

    # 基线对比（发布前检查）
    baseline_run = store.latest_done_run(suite, exclude_run_id=run_id or None) if persist else None
    baseline_rows = store.get_results(int(baseline_run["id"])) if baseline_run else []
    cmp = compare_mod.compare_runs(results, baseline_rows)

    report = {
        "run_id": run_id,
        "suite": suite,
        "suite_label": SUITE_LABELS.get(suite, suite),
        "total": total,
        "passed": passed,
        "accuracy": accuracy,
        "by_tag": by_tag,
        "by_source": dict(src_counter),
        "sources_label": SOURCES_LABELS.get(suite, ""),
        "failures": [r for r in results if not r["passed"]],
        "invalid_cases": invalid,
        "baseline": {
            "run_id": int(baseline_run["id"]) if baseline_run else None,
            **cmp,
        },
        "baseline_ok": cmp["baseline_ok"],
        "verdict": compare_mod.verdict_text(cmp),
    }

    if persist and run_id:
        store.save_results(run_id, [
            _to_result(r) for r in results
        ])
        store.finish_run(
            run_id, total=total, passed=passed, accuracy=accuracy,
            by_tag=by_tag, by_source=dict(src_counter),
            baseline_run_id=report["baseline"]["run_id"],
            baseline_ok=cmp["baseline_ok"], regression=cmp["regression"],
            error="; ".join(f"{i['case_key']}: {i['error']}" for i in invalid)[:1024])
    return report


def _to_result(r: dict):
    from services.shared.eval.contract import CaseResult
    return CaseResult(case_key=r["case_key"], suite=r["suite"], passed=r["passed"],
                      reason=r["reason"], score=r["score"], sources=r["sources"],
                      duration_ms=r["duration_ms"])


def run_all(*, trigger_type: str = "manual", created_by: str = "",
            persist: bool = True) -> dict:
    """跑全部评测集，返回 {suite: report} 与总体结论。"""
    reports = {}
    for suite in SUITES:
        reports[suite] = run_suite(suite, trigger_type=trigger_type,
                                   created_by=created_by, persist=persist)
    all_ok = all(r["baseline_ok"] for r in reports.values())
    return {
        "suites": reports,
        "baseline_ok": all_ok,
        "verdict": "全部未低于基线" if all_ok else "存在回退，发布前检查不通过",
    }


def _print_report(rep: dict):
    print("=" * 64)
    print(f"{rep['suite_label']}（{rep['suite']}）: {rep['passed']}/{rep['total']} "
          f"accuracy={rep['accuracy']:.1%}")
    print("=" * 64)
    if rep.get("sources_label"):
        print(f"分桶口径: {rep['sources_label']}")
    if rep["by_tag"]:
        print("By tag:")
        for t, v in rep["by_tag"].items():
            print(f"  - {t:<22} {v['passed']}/{v['total']}  ({v['accuracy']:.0%})")
    if rep["by_source"]:
        print("By source:")
        for s, n in sorted(rep["by_source"].items(), key=lambda x: -x[1]):
            print(f"  - {s:<24} {n}")
    if rep.get("invalid_cases"):
        print("Invalid cases (用例本身不合法):")
        for i in rep["invalid_cases"]:
            print(f"  ! {i['case_key']}: {i['error']}")
    if rep["failures"]:
        print("Failures:")
        for f in rep["failures"]:
            print(f"  ✗ {f['case_key']}: {f['reason']}")
    else:
        print("All cases passed.")
    print(f"发布前检查: {rep['verdict']} (baseline_ok={rep['baseline_ok']})")


def main():
    ap = argparse.ArgumentParser(description="AI-DataHub 评测运行器")
    ap.add_argument("--suite", choices=list(SUITES), default="", help="只跑指定评测集")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--no-persist", action="store_true", help="不落库（本地调试）")
    ap.add_argument("--trigger", default="ci", help="触发方式标记 (manual/celery/ci)")
    ap.add_argument("--by", default="", help="触发人")
    args = ap.parse_args()

    if args.suite:
        out = run_suite(args.suite, trigger_type=args.trigger,
                        created_by=args.by, persist=not args.no_persist)
        failed = out["failures"] or out["invalid_cases"]
        code = 0 if not failed and out["baseline_ok"] else 1
        if args.json:
            print(json.dumps(out, ensure_ascii=False, default=str))
        else:
            _print_report(out)
        sys.exit(code)

    out = run_all(trigger_type=args.trigger, created_by=args.by,
                  persist=not args.no_persist)
    code = 0 if out["baseline_ok"] and all(
        not r["failures"] and not r["invalid_cases"] for r in out["suites"].values()) else 1
    if args.json:
        print(json.dumps(out, ensure_ascii=False, default=str))
    else:
        for rep in out["suites"].values():
            _print_report(rep)
            print()
        print(out["verdict"])
    sys.exit(code)


if __name__ == "__main__":
    main()
