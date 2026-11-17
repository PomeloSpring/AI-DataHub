"""属性业务名辅助生成（Q11：工具全辅助生成 + 人工审业务层）。

背景：契约对象的 properties[].name 照搬物理列名（如 id/email），触发脱敏规则
"无业务名的属性不渲染"（ontology-modeling §2），同步文档的属性/枚举段被整段剔除
——云端检索看不到属性语义。列注释（business_desc/column_comment）是业务名的
权威候选来源（人工在库里维护的中文注释）。

本脚本（幂等，dry-run 默认）：
1. 对 name==column（无业务名）的属性，从列注释提炼业务名候选；
   注释清洗（枚举值说明/过长/含标点 → 不接受，进人工清单）；
2. 改名联动：properties[].name 与部署绑定行 column_map 键同步（业务名→物理列）；
3. save_draft 级联落库（展开/图谱/字典自动跟随）。

用法（repo 根目录）:
  venv/bin/python backend/scripts/name_business_properties.py            # dry-run
  venv/bin/python backend/scripts/name_business_properties.py --apply
  ... --model-id 1789467214865307
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

DEFAULT_MODEL_ID = 1789467214865307

_BAD_CHARS = re.compile(r"[：:，,。;；/\\()（）\[\]【】\"'""]")


def _acceptable(comment: str) -> str:
    """注释 → 可用业务名（空串=不可用，进人工清单）。"""
    c = (comment or "").strip()
    if not c:
        return ""
    if len(c) < 2 or len(c) > 12:
        return ""
    if _BAD_CHARS.search(c) or c[0].isdigit():
        return ""
    if not re.search(r"[一-鿿]", c):   # 要中文业务名
        return ""
    return c


def _load_comments(datasource_id: int) -> dict[tuple[str, str], str]:
    from backend.common.db.metadata_db import get_metadata_conn
    out: dict[tuple[str, str], str] = {}
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name, business_desc, column_comment "
                "FROM adh_column_metadata WHERE datasource_id = %s", (datasource_id,))
            for r in cur.fetchall() or []:
                c = _acceptable(r.get("business_desc") or "") or _acceptable(r.get("column_comment") or "")
                if c:
                    out[(str(r["table_name"]), str(r["column_name"]))] = c
    return out


def _load_dict_names() -> dict[tuple[str, str], tuple[str, dict]]:
    """维度字典候选：(bound_object_key, 列名) → (维度业务名, value_labels)。
    字典是人工维护过的业务名（枚举列的最佳候选源），value_labels 回填属性枚举。
    按 bound_object_key 关联（字典行 datasource_id 历史落 0，不可按源筛）。"""
    from backend.common.db.metadata_db import get_metadata_conn
    out: dict[tuple[str, str], tuple[str, dict]] = {}
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT bound_object_key, name, target_column, value_labels FROM adh_dimensions "
                "WHERE is_active = 1 AND COALESCE(target_column,'') <> '' "
                "AND COALESCE(bound_object_key,'') <> ''")
            for r in cur.fetchall() or []:
                col = str(r["target_column"]).rsplit(".", 1)[-1]
                key = (str(r["bound_object_key"]), col)
                nm = (r.get("name") or "").strip()
                if _acceptable(nm) and key not in out:
                    vl = r.get("value_labels")
                    if isinstance(vl, str):
                        try:
                            vl = json.loads(vl or "{}")
                        except ValueError:
                            vl = {}
                    out[key] = (nm, vl or {})
    return out


def run(apply: bool, model_id: int, names_json: str = "") -> int:
    from backend.common.db.metadata_db import get_metadata_conn
    from backend.modules.catalog.services import ontology_service as osvc

    override: dict[tuple[str, str], str] = {}
    if names_json:
        with open(names_json, encoding="utf-8") as f:
            for k, v in (json.load(f) or {}).items():
                ok, _, col = str(k).rpartition(".")
                if _acceptable(str(v)):
                    override[(ok, col)] = str(v)
        print(f"人工/wiki 命名覆盖表: {len(override)} 条（最高优先）")

    model = osvc.get_model(model_id)
    if not model:
        print("ERR: 模型不存在", file=sys.stderr)
        return 2
    doc = json.loads(model["json_content"])
    ds_id = int(model.get("datasource_id") or 0)
    comments = _load_comments(ds_id)
    dict_names = _load_dict_names()

    renames: list[tuple[str, str, str, str]] = []   # (object_key, column, old, new)
    manual: list[tuple[str, str]] = []              # (object_key, column)
    enums_filled = 0
    for o in doc.get("objects") or []:
        used = set()
        table = str(o.get("primary_table") or "")
        for p in o.get("properties") or []:
            col_full = str(p.get("column") or "")
            col = col_full.rsplit(".", 1)[-1]
            biz = (p.get("name") or "").strip()
            if biz and biz != col:
                used.add(biz)
                continue
            cand = (override.get((str(o.get("key")), col))
                    or comments.get((table, col)) or comments.get(("", col))
                    or dict_names.get((str(o.get("key")), col), ("", {}))[0])
            # 字典值标签回填枚举（业务语义，安全上云）
            vl = dict_names.get((str(o.get("key")), col), ("", {}))[1]
            if vl and not (p.get("enum") or p.get("value_labels")):
                p["enum"] = [f"{k}:{v}" for k, v in sorted(vl.items(), key=lambda kv: str(kv[0]))]
                enums_filled += 1
            if not cand:
                manual.append((str(o.get("key")), col))
                continue
            name = cand
            i = 2
            while name in used:
                name = f"{cand}{i}"
                i += 1
            used.add(name)
            renames.append((str(o.get("key")), col, biz or col, name))
            p["name"] = name

    print(f"业务名生成: 改名 {len(renames)} 个 | 待人工命名 {len(manual)} 个 | 枚举回填 {enums_filled} 个")
    for ok, col, old, new in renames[:12]:
        print(f"  {ok}.{col}: {old} → {new}")
    if len(renames) > 12:
        print(f"  … 共 {len(renames)} 条")
    if manual:
        print("待人工命名（注释缺失/不合规范，属性暂不渲染）:")
        for ok, col in manual[:10]:
            print(f"  {ok}.{col}")
        if len(manual) > 10:
            print(f"  … 共 {len(manual)} 条")

    if not apply:
        print("[dry-run] 加 --apply 落库")
        return 0

    # 联动：部署绑定行 column_map 键同步（业务名→物理列）
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, object_key, column_map FROM adh_ontology_bindings "
                "WHERE model_id = %s AND status = 'active'", (model_id,))
            rows = cur.fetchall() or []
            by_obj: dict[str, list[tuple[str, str, str]]] = {}
            for ok, col, old, new in renames:
                by_obj.setdefault(ok, []).append((col, old, new))
            updated = 0
            for r in rows:
                ok = str(r["object_key"])
                if ok not in by_obj:
                    continue
                cm = r.get("column_map")
                if isinstance(cm, str):
                    try:
                        cm = json.loads(cm or "{}")
                    except ValueError:
                        cm = {}
                cm = dict(cm or {})
                changed = False
                for col, old, new in by_obj[ok]:
                    if old in cm and new not in cm:
                        cm[new] = cm.pop(old)
                        changed = True
                    elif col in cm and new not in cm:
                        cm[new] = cm.pop(col)
                        changed = True
                if changed:
                    cur.execute(
                        "UPDATE adh_ontology_bindings SET column_map = %s, updated_at = UTC_TIMESTAMP(6) "
                        "WHERE id = %s", (json.dumps(cm, ensure_ascii=False), r["id"]))
                    updated += 1
        conn.commit()
    print(f"column_map 键同步: {updated} 行")

    osvc.save_draft(model_id, json.dumps(doc, ensure_ascii=False))
    print("[apply] 已落库（save_draft 级联：展开/图谱/字典/内嵌投影已跟随）")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true", help="落库（默认 dry-run）")
    p.add_argument("--model-id", type=int, default=DEFAULT_MODEL_ID)
    p.add_argument("--names-json", default="", help="人工/wiki 命名覆盖表 {'object.column': '业务名'}")
    args = p.parse_args()
    return run(apply=args.apply, model_id=args.model_id, names_json=args.names_json)


if __name__ == "__main__":
    raise SystemExit(main())
