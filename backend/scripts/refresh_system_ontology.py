"""刷新 AS-BOT 系统能力本体（kind='system'）到当前代码事实。

事实源：`backend/modules/catalog/services/system_ontology_catalog.py`（声明式目录）。
本脚本只做「合并落库 + 级联 + 退役陈旧绑定行 + 同步知识库 + 核验」，不在这里写建模内容。

用法：
    ./venv/bin/python -m backend.scripts.refresh_system_ontology            # dry-run：只打印差异
    ./venv/bin/python -m backend.scripts.refresh_system_ontology --apply    # 落库并级联
    ./venv/bin/python -m backend.scripts.refresh_system_ontology --apply --skip-kb

为什么用 `allow_full_regenerate`：
    系统本体是**整份 doc 由目录全量重算**的派生对象——已退役概念（adh_wakers /
    adh_as_bot_approvals / metadata.sync 等）本就应该消失。`_reject_destructive_overwrite`
    保护的是人工/LLM 凭摘要重建的编辑，不适用于本受信任维护通道；该关键字参数不对外暴露，
    AS-BOT 工具通道无权设置（AST 用例锁定调用点，见 tests/test_dataset_semantic_binding_p0.py）。

失败口径（no-silent-degradation）：
    - 列不存在、M2/M3/M4 校验有 error、知识库同步失败、对象表行数与 doc 不一致，
      一律打印原因并以非零退出码结束，不"看起来成功"。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from backend.common.db import get_metadata_conn
from backend.modules.catalog.services import ontology_service
from backend.modules.catalog.services import system_ontology_catalog as catalog


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    raise SystemExit(1)


def _load_system_model() -> dict:
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, kind, status, datasource_id, json_content "
                "FROM adh_ontology_models WHERE kind='system' AND status='active'"
            )
            rows = cur.fetchall()
    if not rows:
        _fail("未找到 active 的系统本体（kind='system'），请先确认模型是否存在")
    if len(rows) > 1:
        _fail(f"存在多个 active 系统本体: {[r['id'] for r in rows]}；系统本体应全局唯一")
    return rows[0]


def _verify_physical_columns(doc: dict) -> None:
    """列存在性校验（fail-loud）：本体登记的每个物理列必须在库里真实存在。"""
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE() AS db")
            db = (cur.fetchone() or {}).get("db")
            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=%s", (db,))
            cols: dict[str, set] = {}
            for r in cur.fetchall():
                cols.setdefault(r["TABLE_NAME"], set()).add(r["COLUMN_NAME"])
    bad: list[str] = []
    for obj in doc.get("objects") or []:
        table = str(obj.get("primary_table") or "")
        if not table:
            continue
        if table not in cols:
            bad.append(f"{obj.get('key')}: 表不存在 {table}")
            continue
        for prop in obj.get("properties") or []:
            col = str(prop.get("column") or "")
            if "." not in col:
                continue
            bare = col.split(".", 1)[1]
            if bare not in cols[table]:
                bad.append(f"{obj.get('key')}: {table}.{bare} 不存在")
    if bad:
        _fail("本体登记了不存在的表/列（先修目录再落库）: " + "; ".join(bad[:20]))


def _verify_m234(doc: dict) -> None:
    errors = [it for it in ontology_service.find_m234_issues(doc) if it.get("severity") == "error"]
    if errors:
        _fail("本体结构校验未通过: " + "; ".join(it.get("message", "") for it in errors[:10]))


def _diff(old_doc: dict, new_doc: dict) -> dict:
    old_objs = {str(o.get("key") or ""): o for o in (old_doc.get("objects") or [])}
    new_objs = {str(o.get("key") or ""): o for o in (new_doc.get("objects") or [])}
    added = sorted(set(new_objs) - set(old_objs))
    removed = sorted(set(old_objs) - set(new_objs))
    changed = sorted(
        k for k in (set(old_objs) & set(new_objs))
        if json.dumps(old_objs[k], ensure_ascii=False, sort_keys=True)
        != json.dumps(new_objs[k], ensure_ascii=False, sort_keys=True)
    )
    return {"added": added, "removed": removed, "changed": changed,
            "old_count": len(old_objs), "new_count": len(new_objs)}


def _print_diff(diff: dict) -> None:
    print(f"对象数: {diff['old_count']} → {diff['new_count']}")
    print(f"新增 {len(diff['added'])}: {', '.join(diff['added']) or '-'}")
    print(f"移除 {len(diff['removed'])}: {', '.join(diff['removed']) or '-'}")
    print(f"变更 {len(diff['changed'])}: {', '.join(diff['changed']) or '-'}")


def _object_rows(model_id: int) -> int:
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM adh_ontology_objects WHERE model_id=%s",
                        (model_id,))
            return int((cur.fetchone() or {}).get("n") or 0)


def _retire_bindings(model_id: int) -> None:
    """退役系统本体的部署绑定行（幂等）。

    系统本体描述平台自身能力，对象随项目版本发布、不参与业务取数；`adh_ontology_bindings`
    里的历史行既不随 doc 维护（已退役的 waker / as_bot_approval 仍留 active 行），也会被
    血缘巡检当成「对象未绑定数据产品」报假缺口（`lineage_service` 按
    `status='active' AND product_ref=''` 判定）。故统一置 deprecated，并删掉同一
    `(object_key, physical_table)` 的重复废弃行（历史 import-yaml 反复跑留下的冗余）。

    页面侧对 `kind='system'` 改由 doc 合成契约清单、按 information_schema 核验列
    （见 `backend/modules/catalog/api/ontology.py: _platform_contract_objects`），不再读本表。
    """
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE adh_ontology_bindings SET status='deprecated', updated_at=NOW() "
                "WHERE model_id=%s AND status<>'deprecated'", (model_id,))
            retired = int(cur.rowcount or 0)
            cur.execute(
                "DELETE b FROM adh_ontology_bindings b JOIN ("
                "  SELECT MIN(id) AS keep_id, object_key, physical_table "
                "  FROM adh_ontology_bindings WHERE model_id=%s AND status='deprecated' "
                "  GROUP BY object_key, physical_table HAVING COUNT(*) > 1"
                ") d ON b.object_key = d.object_key AND b.physical_table = d.physical_table "
                "AND b.model_id=%s AND b.status='deprecated' AND b.id <> d.keep_id",
                (model_id, model_id))
            deduped = int(cur.rowcount or 0)
        conn.commit()
    print(f"部署绑定行：退役 {retired} 行 active，清理重复废弃行 {deduped} 行")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="刷新 AS-BOT 系统能力本体")
    parser.add_argument("--apply", action="store_true", help="落库并级联（默认只 dry-run）")
    parser.add_argument("--skip-kb", action="store_true", help="跳过知识库同步")
    args = parser.parse_args(argv)

    model = _load_system_model()
    model_id = int(model["id"])
    print(f"目标模型: {model_id} 「{model['name']}」 status={model['status']}")

    try:
        old_doc = json.loads(model.get("json_content") or "{}")
    except ValueError as e:
        _fail(f"现有 json_content 不是合法 JSON: {e}")

    try:
        new_doc = catalog.build_system_ontology_doc()
    except ValueError as e:
        _fail(str(e))

    _verify_physical_columns(new_doc)
    _verify_m234(new_doc)

    diff = _diff(old_doc, new_doc)
    _print_diff(diff)
    print(f"规则 {len(new_doc.get('rules') or [])} 条 / 场景 {len(new_doc.get('scenarios') or [])} 条 / "
          f"动作 {sum(len(o.get('actions') or []) for o in new_doc['objects'])} 个")

    if not args.apply:
        print("dry-run 结束（加 --apply 才会落库）")
        return 0

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = f"/tmp/system_ontology_backup_{stamp}.json"
    with open(backup, "w", encoding="utf-8") as fh:
        json.dump(old_doc, fh, ensure_ascii=False, indent=2)
    print(f"已备份旧内容: {backup}")

    result = ontology_service.save_draft(
        model_id,
        json.dumps(new_doc, ensure_ascii=False),
        allow_full_regenerate=True,
    )
    print(f"保存完成: object_count={result.get('object_count')} updated_at={result.get('updated_at')}")
    for w in (result.get("validation_warnings") or []):
        print(f"  告警: {w}")
    graph = result.get("graph_rebuild") or {}
    if graph:
        print(f"  图谱重建: {json.dumps(graph, ensure_ascii=False)[:300]}")

    rows = _object_rows(model_id)
    expected = len(new_doc["objects"])
    if rows != expected:
        _fail(f"对象表行数与 doc 不一致: adh_ontology_objects={rows} doc={expected}")
    print(f"对象表已重展开: {rows} 行")

    # 部署绑定行退役：系统本体不该在业务绑定层留 active 行（假血缘缺口 + 页面错口径）
    _retire_bindings(model_id)

    if args.skip_kb:
        print("已跳过知识库同步（--skip-kb）")
        return 0

    from backend.modules.catalog.services import ontology_kb_sync
    kb = ontology_kb_sync.sync_model_to_qmind(model_id)
    print(f"知识库同步: {json.dumps(kb, ensure_ascii=False)}")
    if (kb or {}).get("skipped") or not (kb or {}).get("synced"):
        _fail("知识库同步未成功（见上方 skipped/错误），请检查知识库配置或连通性")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())