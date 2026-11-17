"""批量认领本体对象来源表为数据产品（activate 硬门禁 P2.3 的配套脚本）。

`ontology_service.activate` 拒绝激活「对象绑在未登记为数据产品的裸表」上的模型
（本体只绑数据产品：有契约与责任归属）。错误提示引用的就是本脚本：

    venv/bin/python -m backend.scripts.claim_ontology_tables_as_products --model-id <id>
    venv/bin/python -m backend.scripts.claim_ontology_tables_as_products --model-id <id> --dry-run
    venv/bin/python -m backend.scripts.claim_ontology_tables_as_products --all [--dry-run]

行为（幂等，可重复跑）：
1. 读模型 doc，按对象 primary_table 逐表经 `data_product_service.find_by_table`
   （唯一查询口径）判定未登记；
2. 未登记表经 `register_product` 登记：product_name = <数据源名>.<表>，
   producer_kind=manual、producer_ref 溯源到模型 id，附 adh_column_metadata
   列快照算 schema 指纹（有列快照才写 v1 版本记录）；
3. 源本体 doc 缺 datasource_name 时（生成链路历史缺陷：门禁/解析会退化为
   只按表名的宽松匹配），经 **save_draft 级联**回填——不绕派生表直接 UPDATE；
4. 收尾重跑 `find_unregistered_tables` 报门禁状态（空 = 激活门禁该项通过）。

--dry-run 只打印将认领的清单，不落库。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.common.db import execute_query
from backend.modules.catalog.services import data_product_service as dps
from backend.modules.catalog.services import ontology_service


def _datasource_name(datasource_id) -> str:
    if not datasource_id:
        return ""
    row = execute_query(
        "SELECT name FROM adh_datasources WHERE id = %s",
        (int(datasource_id),), fetchone=True)
    return str(row["name"]) if row else ""


def _column_snapshot(datasource_id, table: str) -> list:
    """列快照（schema 指纹用）；无元数据时回空列表（登记不阻断，指纹为空待回填）。"""
    if not datasource_id:
        return []
    rows = execute_query(
        "SELECT column_name, data_type, column_comment FROM adh_column_metadata "
        "WHERE datasource_id = %s AND table_name = %s AND is_active = 1 "
        "ORDER BY id",
        (int(datasource_id), table))
    return [{"name": r["column_name"], "type": r.get("data_type") or "",
             "comment": r.get("column_comment") or ""} for r in rows]


def _missing_pairs(doc: dict, ds_name: str) -> list:
    """doc 对象来源表中未登记为数据产品的 (ds_name, table) 去重清单。"""
    pairs = sorted({(ds_name, str(o.get("primary_table") or "").strip())
                    for o in (doc.get("objects") or [])
                    if str(o.get("primary_table") or "").strip()})
    return [(ds, t) for ds, t in pairs if not dps.find_by_table(ds, t)]


def claim_model(model_id: int, dry_run: bool = False, owner: str = "") -> dict:
    """认领单个模型的全部未登记来源表；返回 {model, claimed, warnings, gate_missing}。"""
    model = ontology_service.get_model(model_id)
    if not model:
        raise ValueError(f"模型不存在: {model_id}")
    doc = json.loads(model["json_content"])
    ds_name = str(doc.get("datasource_name") or "").strip() \
        or _datasource_name(model.get("datasource_id"))
    missing = _missing_pairs(doc, ds_name)

    claimed, warnings = [], []
    for ds, table in missing:
        product_name = f"{ds}.{table}" if ds else table
        if dry_run:
            claimed.append(product_name)
            continue
        res = dps.register_product(
            {
                "product_name": product_name,
                "display_name": table,
                "datasource_name": ds,
                "physical_table": table,
                "producer_kind": "manual",
                "producer_ref": f"claim:ontology_model:{model_id}",
                "status": "draft",
                "owner": owner,
                "description": f"脚本认领：本体模型「{model['name']}」对象来源表",
            },
            columns=_column_snapshot(model.get("datasource_id"), table),
            created_by="script:claim_ontology_tables_as_products")
        claimed.append(product_name)
        warnings.extend(res.get("warnings") or [])

    # 非 system 本体（business，含原 source 迁移而来）doc 缺 datasource_name → 经 save_draft 级联回填
    # （门禁/解析恢复严格匹配口径）。
    backfilled = False
    if (not dry_run and missing
            and str(model.get("kind") or "") != "system"
            and ds_name and not str(doc.get("datasource_name") or "").strip()):
        doc["datasource_name"] = ds_name
        model = ontology_service.save_draft(model_id, json.dumps(doc, ensure_ascii=False))
        backfilled = True

    gate_missing = ontology_service.find_unregistered_tables(
        json.loads(model["json_content"]))
    return {"model": model["name"], "datasource_name": ds_name,
            "claimed": claimed, "warnings": warnings,
            "backfilled_datasource_name": backfilled,
            "gate_missing": gate_missing, "dry_run": dry_run}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="认领本体对象来源表为数据产品（激活门禁配套）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--model-id", type=int, help="目标本体模型 id")
    g.add_argument("--all", action="store_true", help="扫描全部 draft/active 模型")
    ap.add_argument("--dry-run", action="store_true", help="只打印将认领的清单，不落库")
    ap.add_argument("--owner", default="", help="数据产品责任人（可选）")
    args = ap.parse_args(argv)

    if args.all:
        rows = execute_query(
            "SELECT id FROM adh_ontology_models WHERE status IN ('draft', 'active') "
            "ORDER BY id")
        ids = [r["id"] for r in rows]
    else:
        ids = [args.model_id]

    failed = 0
    for mid in ids:
        try:
            r = claim_model(mid, dry_run=args.dry_run, owner=args.owner)
        except ValueError as e:
            print(f"[skip] model={mid}: {e}")
            failed += 1
            continue
        if not r["claimed"] and not r["gate_missing"]:
            continue
        tag = "[dry-run] " if r["dry_run"] else ""
        print(f"{tag}model={r['model']} (id={mid}) ds={r['datasource_name'] or '-'}")
        for p in r["claimed"]:
            print(f"  {'将认领' if r['dry_run'] else '已认领'}: {p}")
        for w in r["warnings"]:
            print(f"  [warning] {w}")
        if r["backfilled_datasource_name"]:
            print(f"  已回填 doc.datasource_name={r['datasource_name']}（save_draft 级联）")
        if r["gate_missing"]:
            print(f"  [gate] 仍未登记: {', '.join(r['gate_missing'])}")
            failed += 1
        else:
            print("  [gate] 数据产品登记项已通过（可激活）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
