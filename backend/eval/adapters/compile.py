"""语义层编译适配器：intent → binding → plan 的确定性编译评测。

**覆盖范围（引用结论前必读）**：本适配器只走 ``shared/semantics/{intent,planner}``
的离线确定性编译，用内存语义目录（``fixtures/demo_semantics``）打桩字典加载。
它**不经过** ``datamind/rag/strategies/graphrag.py``，
也不度量自然语言→intent 的生成准确率。

因此 ``sources`` 分桶是 **planner 的名字解析档位**（dict_exact / dict_alias /
phys_col / physical_desc / fuzzy_rejected），不是检索来源；两者语义不同，
报告里必须分开呈现，不得拿本适配器的通过率给检索层背书。
"""

from __future__ import annotations

import contextlib

from backend.eval.contract import Case
from backend.eval.fixtures import demo_semantics as demo


@contextlib.contextmanager
def _patched_resolver_catalog():
    """把 resolver 的字典/模板加载临时打桩到内存 demo 目录，退出时恢复。

    必须是上下文管理器而非永久替换：否则同进程后续 planner 测试会被污染。
    """
    from backend.semantics import planner
    from backend.semantics.planner import _ColumnResolver

    orig_phys = _ColumnResolver._load_phys
    orig_dims = _ColumnResolver._load_dims
    orig_metrics = _ColumnResolver._load_metrics
    orig_tpl = planner._load_template_row

    def _load_phys(self):
        return {c: dict(m) for c, m in demo.PHYS.get(self.binding.physical_table, {}).items()}

    # 注意：demo.DIMS / demo.METRICS 是**按物理表分组的 dict**（表名 → {列名: 元数据}），
    # 且 resolver 期望返回按列名索引的 dict，不是 list。
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


def run_case(case: Case) -> dict:
    """跑一条编译用例，返回 ``observed`` 供 contract.check_compile 比对。"""
    from backend.semantics.intent import parse_intent
    from backend.semantics.planner import plan

    payload = case.payload or {}
    observed: dict = {"parsed": None, "bound": False, "planned": False,
                     "sql": "", "warnings": [], "sources": {}}

    with _patched_resolver_catalog():
        q, err, _notes = parse_intent(payload.get("intent") or {})
        if err:
            observed["intent_rejected"] = True
            observed["parse_error"] = err
            return observed

        observed["parsed"] = q
        binding = demo.binding_for(int(payload.get("datasource_id") or 0), q.object)
        # bound 是**是否绑定到对象**（unbound 断言看它），与 parsed 不是一回事：
        # intent 能解析不代表对象在本数据源里存在。
        observed["bound"] = binding is not None
        if binding is None:
            return observed

        p = plan(q, binding)
        if p is None:
            return observed
        observed["planned"] = True
        observed["sql"] = p.sql or ""
        observed["warnings"] = list(p.warnings or [])
        observed["sources"] = dict((p.provenance or {}).get("resolution_sources") or {})
    return observed


SOURCES_LABEL = "解析命中档位（planner 名字解析，非检索来源）"
