"""评测运行编排（工程能力的统一出口）。

用法::

    python -m backend.eval.runner                    # 跑全部评测集并落库
    python -m backend.eval.runner --suite compile    # 只跑语义层编译
    python -m backend.eval.runner --json             # 机器可读（供 CI）
    python -m backend.eval.runner --no-persist       # 只跑不落库（本地调试）

设计要点
--------
* **用例来自 DB**（``adh_eval_cases``），页面可增删改查；不再读 YAML。
* 三个评测集共用一套契约/评分/对比/落库；各层只提供 ``run_case`` 适配器。
* ``passed`` 由确定性断言给出；``score`` 是 LLM 辅助评分，**不影响** ``passed``。
* **双口径门禁**（二者正交，不得顶替）：
  ``baseline_ok`` = 相对上一次完成的运行未回退；
  ``floor_ok``   = 达绝对下限且活跃用例数够覆盖度下界（``gate.py``）。
  只卡前者会产生“长期坏掉却报绿”的真实事故，发布结论一律看 ``release_ok``。
* ``case_status='retired'`` 的用例不跑不入分母，但条数与原因必须回报并落库。
* 落库失败抛 ``EvalStoreError``：结果丢了却报"通过"是降级掩盖，不许发生。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter

from backend.eval import compare as compare_mod
from backend.eval import gate as gate_mod
from backend.eval import store
from backend.eval.contract import (
    Case, CaseContractError, SUITES, SUITE_LABELS, check, normalize_case,
)

ADAPTERS = {
    "compile": "backend.eval.adapters.compile",
    "semantic": "backend.eval.adapters.semantic",
    "retrieval": "backend.eval.adapters.retrieval",
    "llm": "backend.eval.adapters.llm",
}

SOURCES_LABELS = {
    "compile": "解析命中档位（planner 名字解析，非检索来源）",
    "semantic": "真实语义层归因（解析档位 + 闸门落点 + 治理是否真被验证）",
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
    """跑一条用例，返回 {passed, reason, score, sources, item_count, duration_ms}。

    ``item_count`` 走独立字段而不是混进 ``sources``：sources 是"检索来源/解析档位"
    这类**归因分桶**，把条目数塞进去会让 By source 里出现 0/69 这种数字项，
    把分桶语义弄脏（retrieval 适配器曾这样做过，已改）。
    """
    started = time.monotonic()
    try:
        mod = _adapter(suite)
        observed = mod.run_case(case)
        item_count = observed.get("item_count")
        if observed.get("error"):
            return {"passed": False, "reason": f"适配器报错: {observed['error']}",
                    "score": None, "sources": observed.get("sources") or {},
                    "item_count": item_count,
                    "duration_ms": int((time.monotonic() - started) * 1000)}
        ok, reason = check(suite, observed, case.expected)
        score = None
        if suite == "llm" and hasattr(mod, "score_answer"):
            score = mod.score_answer(case, observed)
        return {"passed": ok, "reason": reason, "score": score,
                "sources": observed.get("sources") or {}, "item_count": item_count,
                "duration_ms": int((time.monotonic() - started) * 1000)}
    except CaseContractError as exc:
        # 用例本身写得不对 —— 必须显式报出来，不能算"通过"也不能静默跳过
        return {"passed": False, "reason": f"用例不合法: {exc}",
                "score": None, "sources": {}, "item_count": None,
                "duration_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001 —— 适配器异常也要落成失败，不可吞
        return {"passed": False, "reason": f"{type(exc).__name__}: {exc}",
                "score": None, "sources": {}, "item_count": None,
                "duration_ms": int((time.monotonic() - started) * 1000)}


def run_suite(suite: str, *, trigger_type: str = "manual", created_by: str = "",
              persist: bool = True, run_key: str = "") -> dict:
    """跑一个评测集（层），返回报告 dict（可直接 JSON 化）。

    分母口径：**只算 ``case_status`` 为 active/known_defect 的用例**。retired 不跑、
    不入分母，但条数与原因作为 ``retired`` 列回传并落库 —— “退役了多少”必须看得见，
    否则“把难用例标退役”就能静默刷高通过率（no-silent-degradation）。
    """
    if suite not in SUITES:
        raise CaseContractError(f"未知评测集: {suite!r}")

    rows = store.list_cases(suite=suite, active_only=True)
    cases: list[Case] = []
    invalid: list[dict] = []
    retired: list[dict] = []
    for row in rows:
        try:
            case = normalize_case(row)
        except CaseContractError as exc:
            invalid.append({"case_key": row.get("case_key"), "error": str(exc)})
            continue
        if not case.scored:
            retired.append({"case_key": case.case_key, "note": case.status_note})
            continue
        cases.append(case)

    run_id = store.create_run(suite, trigger_type=trigger_type,
                              created_by=created_by, run_key=run_key) if persist else 0
    if persist and run_id:
        store.mark_run_running(run_id)

    results = []
    tag_total, tag_pass = Counter(), Counter()
    cap_total, cap_pass = Counter(), Counter()
    src_counter = Counter()
    for case in cases:
        r = _run_one(suite, case)
        results.append({
            "case_key": case.case_key, "suite": suite, "capability": case.capability,
            "case_status": case.case_status, "expected": case.expected,
            "passed": r["passed"], "reason": r["reason"], "score": r["score"],
            "sources": r["sources"], "item_count": r.get("item_count"),
            "duration_ms": r["duration_ms"],
        })
        for t in case.tags:
            tag_total[t] += 1
            if r["passed"]:
                tag_pass[t] += 1
        cap_total[case.capability] += 1
        if r["passed"]:
            cap_pass[case.capability] += 1
        for _name, src in (r["sources"] or {}).items():
            if isinstance(src, str) and src:
                src_counter[src] += 1

    total = len(cases)
    passed = sum(1 for r in results if r["passed"])
    accuracy = round(passed / total, 4) if total else 0.0
    by_tag = {t: {"passed": tag_pass[t], "total": tag_total[t],
                  "accuracy": round(tag_pass[t] / tag_total[t], 4) if tag_total[t] else 0.0}
              for t in sorted(tag_total)}
    by_capability = {c: {"passed": cap_pass[c], "total": cap_total[c],
                         "label": gate_mod.CAPABILITY_LABELS.get(c, c),
                         "accuracy": round(cap_pass[c] / cap_total[c], 4) if cap_total[c] else 0.0}
                     for c in sorted(cap_total)}

    # 基线对比（相对回退口径）
    baseline_run = store.latest_done_run(suite, exclude_run_id=run_id or None) if persist else None
    baseline_rows = store.get_results(int(baseline_run["id"])) if baseline_run else []
    cmp = compare_mod.compare_runs(results, baseline_rows)

    # 绝对下限口径（与相对回退正交）。策略不可读时报错而不是“默认通过”——
    # 门禁读失败却报绿，等于门禁没在。
    policies = store.list_gate_policies()
    gate_res = gate_mod.evaluate_gate(suite, results, policies)
    floor_ok = gate_res["floor_ok"]
    verdict_code = gate_mod.gate_verdict(floor_ok, cmp["baseline_ok"])
    # 真空通过侦察：passed 但实测一条也没观察到。不计进分母、不判失败，但必须列出来，
    # 否则"能力已死 + 正确率 100%"这种组合可以一直绿着（本轮 L3 就是这样）。
    weak = gate_mod.weak_passes(suite, results)

    report = {
        "run_id": run_id,
        "suite": suite,
        "suite_label": SUITE_LABELS.get(suite, suite),
        "total": total,
        "passed": passed,
        "accuracy": accuracy,
        "by_tag": by_tag,
        "by_capability": by_capability,
        "by_source": dict(src_counter),
        "sources_label": SOURCES_LABELS.get(suite, ""),
        "failures": [r for r in results if not r["passed"]],
        "invalid_cases": invalid,
        "retired": retired,
        "weak_passes": weak,
        "weak_pass_count": len(weak),
        "known_defect_count": sum(1 for c in cases if c.case_status == "known_defect"),
        "active_count": total,
        "baseline": {
            "run_id": int(baseline_run["id"]) if baseline_run else None,
            **cmp,
        },
        # baseline_ok 仍只表“相对未回退”（历史语义不变，已有对比依赖它）
        "baseline_ok": cmp["baseline_ok"],
        "verdict": compare_mod.verdict_text(cmp),
        # 绝对下限与合成结论：发布门禁看 release_ok，不得只看 baseline_ok
        "floor_ok": floor_ok,
        "floor_verdict": gate_mod.gate_text(gate_res),
        "gate": gate_res,
        "gate_verdict": verdict_code,
        "release_ok": bool(floor_ok and cmp["baseline_ok"]),
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
            floor_ok=floor_ok, gate_verdict=verdict_code, active_count=total,
            known_defect_count=report["known_defect_count"], retired_count=len(retired),
            error="; ".join(f"{i['case_key']}: {i['error']}" for i in invalid)[:1024])
    return report


def _to_result(r: dict):
    from backend.eval.contract import CaseResult
    return CaseResult(case_key=r["case_key"], suite=r["suite"], passed=r["passed"],
                      reason=r["reason"], score=r["score"], sources=r["sources"],
                      duration_ms=r["duration_ms"])


def run_all(*, trigger_type: str = "manual", created_by: str = "",
            persist: bool = True) -> dict:
    """跑全部评测集，返回 {suite: report} 与总体结论。

    总闸看 ``release_ok``（相对未回退 AND 达绝对下限）。
    ``baseline_ok`` 保留但只代“相对未回退”——拿它当发布结论就是那个已发生的假绿灯。
    """
    reports = {}
    for suite in SUITES:
        reports[suite] = run_suite(suite, trigger_type=trigger_type,
                                   created_by=created_by, persist=persist)
    relative_ok = all(r["baseline_ok"] for r in reports.values())
    floor_ok = all(r["floor_ok"] for r in reports.values())
    release_ok = bool(relative_ok and floor_ok)
    if release_ok:
        verdict = "各层均未回退且达绝对下限，可以发布"
    elif not floor_ok and not relative_ok:
        verdict = "发布前检查不通过：既有回退，又低于绝对下限"
    elif not floor_ok:
        # 专门盖住"长期坏着却报绿"那一类：没回退不等于能用
        verdict = "发布前检查不通过：未低于基线，但正确率低于绝对下限或用例数不足"
    else:
        verdict = "发布前检查不通过：出现回退"
    return {
        "suites": reports,
        "baseline_ok": relative_ok,
        "floor_ok": floor_ok,
        "release_ok": release_ok,
        "verdict": verdict,
    }


def _print_report(rep: dict):
    print("=" * 64)
    print(f"{rep['suite_label']}（{rep['suite']}）: {rep['passed']}/{rep['total']} "
          f"accuracy={rep['accuracy']:.1%}")
    print("=" * 64)
    if rep.get("sources_label"):
        print(f"分桶口径: {rep['sources_label']}")
    if rep.get("by_capability"):
        print("By capability（能力契约覆盖度）:")
        for c, v in rep["by_capability"].items():
            print(f"  - {c:<12} {v['label']:<8} {v['passed']}/{v['total']}  ({v['accuracy']:.0%})")
    if rep["by_tag"]:
        print("By tag:")
        for t, v in rep["by_tag"].items():
            print(f"  - {t:<22} {v['passed']}/{v['total']}  ({v['accuracy']:.0%})")
    if rep["by_source"]:
        print("By source:")
        for s, n in sorted(rep["by_source"].items(), key=lambda x: -x[1]):
            print(f"  - {s:<24} {n}")
    if rep.get("known_defect_count") or rep.get("retired"):
        print(f"分母构成: 活跃 {rep['active_count']} 条，其中已知缺陷 "
              f"{rep.get('known_defect_count', 0)} 条；退役未跑 {len(rep.get('retired') or [])} 条")
    if rep.get("weak_passes"):
        print("Vacuous passes（通过但实测空结果，不能证明能力在用）:")
        for w in rep["weak_passes"]:
            print(f"  ~ {w['case_key']}: {w['reason']}（期望键 {w['expected_keys']}）")
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
    print(f"相对基线: {rep['verdict']} (baseline_ok={rep['baseline_ok']})")
    print(f"绝对下限: {rep['floor_verdict']} (floor_ok={rep['floor_ok']})")
    print(f"发布前检查: {'通过' if rep['release_ok'] else '不通过'} "
          f"(gate_verdict={rep['gate_verdict']})")


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
        # 退出码看 release_ok：只卡 baseline_ok 会让长期坏掉的集永远 CI 绿
        code = 0 if not failed and out["release_ok"] else 1
        if args.json:
            print(json.dumps(out, ensure_ascii=False, default=str))
        else:
            _print_report(out)
        sys.exit(code)

    out = run_all(trigger_type=args.trigger, created_by=args.by,
                  persist=not args.no_persist)
    code = 0 if out["release_ok"] and all(
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
