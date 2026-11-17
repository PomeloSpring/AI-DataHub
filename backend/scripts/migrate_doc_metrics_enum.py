"""一次性迁移: canonical doc 旧格式条目收敛（口径单一登记 + enum 同构）。

- objects[].metrics: {name, formula, description} dict 条目 → 归并进指标字典
  （adh_metrics，口径唯一登记）后降为指标名字符串；
- properties[].enum: ["0=否"] 字符串列表 → {"0": "否"} 对象（与 value_labels 同构）。

迁移后 save_draft/activate 写闸（validate_doc_formats）fail-loud 拒收旧格式。

用法（repo 根目录）:
  venv/bin/python backend/scripts/migrate_doc_metrics_enum.py          # dry-run 报告
  venv/bin/python backend/scripts/migrate_doc_metrics_enum.py --apply  # 落库(级联重建)

幂等: 第二次执行应报 0 处变更。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)


def _normalize_enum(enum):
    """旧格式字符串列表 → {code: label}；新格式/空原样返回。返回 (new, changed)。"""
    if not enum or isinstance(enum, dict):
        return enum, False
    from backend.modules.catalog.services.ontology_service import enum_pairs
    pairs = enum_pairs(enum)
    return (dict(pairs) if pairs else {}), True


def run(apply: bool) -> int:
    from backend.common.db.metadata_db import get_metadata_conn
    from backend.modules.catalog.services import ontology_service as osvc

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, datasource_id, json_content FROM adh_ontology_models "
                "WHERE status != 'archived'")
            models = cur.fetchall() or []

    total_models = total_enum = total_metric_dicts = 0
    for m in models:
        try:
            doc = json.loads(m.get("json_content") or "{}")
        except json.JSONDecodeError:
            print(f"[跳过] model #{m.get('id')} json_content 解析失败")
            continue

        enum_fixed = 0
        for o in doc.get("objects") or []:
            for p in o.get("properties") or []:
                new_enum, changed = _normalize_enum(p.get("enum"))
                if changed:
                    p["enum"] = new_enum
                    enum_fixed += 1

        metric_dicts = sum(
            1 for o in doc.get("objects") or []
            for x in (o.get("metrics") or []) if isinstance(x, dict))

        before = json.dumps(doc, ensure_ascii=False, sort_keys=True)
        doc = osvc.absorb_object_metric_specs(
            doc, int(m.get("datasource_id") or 0), persist=apply)
        after = json.dumps(doc, ensure_ascii=False, sort_keys=True)
        changed = (before != after) or enum_fixed

        if not changed:
            continue
        total_models += 1
        total_enum += enum_fixed
        total_metric_dicts += metric_dicts
        if apply:
            osvc.save_draft(m["id"], json.dumps(doc, ensure_ascii=False))
            print(f"[apply]   model #{m['id']} ({m.get('name') or ''}): "
                  f"enum 归一 {enum_fixed} 处, 指标条目降引用 {metric_dicts} 条（口径已并入字典）")
        else:
            print(f"[dry-run] model #{m['id']} ({m.get('name') or ''}): "
                  f"将归一 enum {enum_fixed} 处, 指标条目降引用 {metric_dicts} 条")

    print(f"\n完成: 变更模型 {total_models} 个（enum {total_enum} 处、"
          f"指标条目 {total_metric_dicts} 条）"
          f"（{'已落库' if apply else 'dry-run，加 --apply 落库'}）")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true", help="落库（默认 dry-run）")
    return run(apply=p.parse_args().apply)


if __name__ == "__main__":
    raise SystemExit(main())
