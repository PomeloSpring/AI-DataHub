"""Golden-question 评测运行器: 离线跑 intent -> binding -> plan 主路, 断言解析/编译结构。

用法:
    python -m tests.eval.runner            # 打印整体 + 按 tag + 按解析来源分桶报告
    python -m tests.eval.runner --json     # 机器可读结果(供 CI 落库/看板)

设计: 不依赖元数据库与 LLM。用例给出结构化 intent(LLM 应产出的ground-truth),
运行器用内存语义目录(demo_semantics)打桩 _ColumnResolver 的三张字典加载与模板加载,
只评测**确定性编译层的解析质量**(种子解析三因子中最易回归的一档)。
"""
import argparse
import contextlib
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import yaml  # noqa: E402

from services.shared.semantics import planner  # noqa: E402
from services.shared.semantics.planner import _ColumnResolver, plan  # noqa: E402
from services.shared.semantics.intent import parse_intent  # noqa: E402
from tests.eval import demo_semantics as demo  # noqa: E402

_CASES_FILE = os.path.join(os.path.dirname(__file__), "cases.yaml")

# 物理标识(用于断言失败提示文案不外泄物理细节)
_PHYS_TOKENS = ("t_case_records", "t_ticket_records", "patient_phone", "del_flag",
                "company_name", "case_status", "create_time", "ticket_id")


@contextlib.contextmanager
def _patched_resolver_catalog():
    """把 resolver 的字典加载与模板加载临时打桩到内存 demo, 退出时恢复原方法。

    必须是上下文管理器而非永久替换: 否则同进程后续 planner 测试会被污染的 demo 目录影响。
    """
    orig_phys = _ColumnResolver._load_phys
    orig_dims = _ColumnResolver._load_dims
    orig_metrics = _ColumnResolver._load_metrics
    orig_tpl = planner._load_template_row

    def _load_phys(self):
        return {c: dict(m) for c, m in demo.PHYS.get(self.binding.physical_table, {}).items()}

    def _load_dims(self):
        src = demo.DIMS.get(self.binding.physical_table, {})
        return {k: dict(v) for k, v in src.items()}

    def _load_metrics(self):
        src = demo.METRICS.get(self.binding.physical_table, {})
        return {k: dict(v) for k, v in src.items()}

    def _load_template_row(template_ref, warnings):
        row = demo.TEMPLATES.get(template_ref)
        if not row:
            warnings.append(f"SQL 模板 '{template_ref}' 不存在或未启用")
        return row

    _ColumnResolver._load_phys = _load_phys
    _ColumnResolver._load_dims = _load_dims
    _ColumnResolver._load_metrics = _load_metrics
    planner._load_template_row = _load_template_row
    try:
        yield
    finally:
        _ColumnResolver._load_phys = orig_phys
        _ColumnResolver._load_dims = orig_dims
        _ColumnResolver._load_metrics = orig_metrics
        planner._load_template_row = orig_tpl


def load_cases() -> list[dict]:
    with open(_CASES_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def _check(case: dict, p, sources: dict) -> tuple[bool, str]:
    """返回 (是否通过, 失败原因)。p 为 PlannedExecution 或 None(未绑定)。"""
    exp = case.get("expect") or {}
    warns = " ; ".join(p.warnings) if p else ""

    if exp.get("unbound"):
        return (p is None), "expected unbound object (binding None)"

    if p is None:
        return False, "unexpectedly unbound"

    if exp.get("ok"):
        if "无法解析" in warns:
            return False, f"ok-case has unresolved warning: {warns}"
        if not p.sql:
            return False, "ok-case produced empty sql"

    if exp.get("error_contains"):
        needle = exp["error_contains"]
        if needle not in warns:
            return False, f"expected warning containing {needle!r}, got: {warns!r}"

    # 未解析提示不得泄露物理细节(护栏 §7 的一致性延伸)
    if exp.get("hint_no_physical"):
        if any(tok in warns for tok in _PHYS_TOKENS):
            return False, f"unresolved hint leaked physical token: {warns}"

    for frag in exp.get("sql_contains") or []:
        if frag not in p.sql:
            return False, f"expected sql fragment {frag!r} not in: {p.sql!r}"

    for name, want in (exp.get("resolution_source") or {}).items():
        got = sources.get(name)
        if got != want:
            return False, f"resolution_source[{name}] expected {want!r}, got {got!r}"

    return True, ""


def run() -> dict:
    cases = load_cases()
    results = []
    tag_total, tag_pass = Counter(), Counter()
    src_counter = Counter()

    with _patched_resolver_catalog():
        for case in cases:
            tags = case.get("tags") or []
            exp = case.get("expect") or {}
            q, err, _notes = parse_intent(case.get("intent") or {})
            if err:
                # 期望 intent 在解析层被拒收(如夹带 SQL): 拒收即通过
                if exp.get("intent_rejected"):
                    ok, reason = True, ""
                else:
                    ok, reason = False, f"intent parse failed: {err}"
                for t in tags:
                    tag_total[t] += 1
                    if ok:
                        tag_pass[t] += 1
                results.append({"id": case.get("id"), "pass": ok, "reason": reason, "tags": tags})
                continue

            binding = demo.binding_for(case.get("datasource_id") or 0, q.object)
            if binding is None:
                p, sources = None, {}
            else:
                p = plan(q, binding)
                sources = (p.provenance or {}).get("resolution_sources", {}) if p else {}

            ok, reason = _check(case, p, sources)
            for _name, src in sources.items():
                src_counter[src] += 1
            for t in tags:
                tag_total[t] += 1
                if ok:
                    tag_pass[t] += 1
            results.append({"id": case.get("id"), "pass": ok, "reason": reason, "tags": tags})

    total = len(cases)
    passed = sum(1 for r in results if r["pass"])
    by_tag = {t: {"passed": tag_pass[t], "total": tag_total[t],
                  "accuracy": round(tag_pass[t] / tag_total[t], 4) if tag_total[t] else 0.0}
              for t in sorted(tag_total)}
    return {
        "total": total,
        "passed": passed,
        "accuracy": round(passed / total, 4) if total else 0.0,
        "by_tag": by_tag,
        "by_resolution_source": dict(src_counter),
        "failures": [r for r in results if not r["pass"]],
    }


def _print_report(rep: dict):
    print("=" * 60)
    print(f"Golden-question eval: {rep['passed']}/{rep['total']} "
          f"accuracy={rep['accuracy']:.1%}")
    print("=" * 60)
    print("By tag:")
    for t, v in rep["by_tag"].items():
        print(f"  - {t:<22} {v['passed']}/{v['total']}  ({v['accuracy']:.0%})")
    print("By resolution_source (解析命中档位分布):")
    for s, n in sorted(rep["by_resolution_source"].items(), key=lambda x: -x[1]):
        print(f"  - {s:<16} {n}")
    if rep["failures"]:
        print("Failures:")
        for f in rep["failures"]:
            print(f"  ✗ {f['id']}: {f['reason']}")
    else:
        print("All cases passed.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args()
    rep = run()
    if args.json:
        print(json.dumps(rep, ensure_ascii=False))
    else:
        _print_report(rep)
    # 退出码: 有失败即非零, 便于 CI 门禁
    sys.exit(0 if not rep["failures"] else 1)


if __name__ == "__main__":
    main()
