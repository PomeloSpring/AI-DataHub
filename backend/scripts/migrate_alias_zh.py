"""一次性迁移: 把硬编码兜底表 _ALIAS_ZH 的中文别名收编进 canonical doc 对象 aliases。

背景: `ontology_yaml_import._ALIAS_ZH` 是绕过事实源的暗别名登记处（违背"同一概念的
别名只登记在字典/对象层"），已从导入代码删除。存量模型中依赖该表生成的别名需要
收编进 canonical JSON doc 的 `objects[].aliases`（唯一可编辑登记），再经 save_draft
级联重建展开行/图谱投影。

用法（repo 根目录执行）:
  venv/bin/python backend/scripts/migrate_alias_zh.py              # dry-run 只报告
  venv/bin/python backend/scripts/migrate_alias_zh.py --apply      # 落库（级联重建）
  venv/bin/python backend/scripts/migrate_alias_zh.py --model-id 3 # 只处理指定模型

幂等: 第二次执行应报 0 处变更。未匹配的 legacy key 只出人工确认清单，不硬塞归属。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

# ── 冻结的 legacy 数据（迁移专用，无代码消费方；勿在此表新增） ──────────
LEGACY_ALIAS_ZH: dict[str, list[str]] = {
    "user":                  ["用户", "客户", "账号"],
    "company":               ["企业", "机构", "公司"],
    "role":                  ["角色", "权限角色"],
    "department":            ["部门", "科室"],
    "patient":               ["患者", "病人"],
    "partnership":           ["合作伙伴", "合作关系"],
    "dentist":               ["牙医", "医师"],
    "case":                  ["案例", "口扫案例", "病例"],
    "case_file":             ["案例文件", "病例附件"],
    "casefile":              ["案例文件", "病例附件"],
    "case_treatment":        ["治疗", "治疗方案"],
    "casetreatment":         ["治疗", "治疗方案"],
    "case_recycle_bin":      ["案例回收站", "回收站"],
    "caserecyclebin":        ["案例回收站", "回收站"],
    "order":                 ["订单"],
    "order_item":            ["订单明细", "订单行"],
    "orderitem":             ["订单明细", "订单行"],
    "equipment":             ["设备", "扫描仪"],
    "product":               ["产品", "商品"],
    "work_order":            ["工单"],
    "workorder":             ["工单"],
    "warranty":              ["保修", "质保"],
    "isv":                   ["独立软件厂商", "服务商"],
    "software_version":      ["软件版本", "版本"],
    "softwareversion":       ["软件版本", "版本"],
    "ai_conversation":       ["AI 会话", "智能助手会话"],
    "aiconversation":        ["AI 会话", "智能助手会话"],
    "disk_file":             ["网盘文件", "素材文件"],
    "diskfile":              ["网盘文件", "素材文件"],
    "dc_case_record":        ["数采案例记录"],
    "dccaserecord":          ["数采案例记录"],
    "observability_event":   ["可观测事件", "前端埋点"],
    "observabilityevent":    ["可观测事件", "前端埋点"],
    "api_request":           ["API 请求", "接口请求"],
    "apirequest":            ["API 请求", "接口请求"],
    "system_log":            ["系统日志"],
    "systemlog":             ["系统日志"],
}


def _match_keys() -> dict[str, list[str]]:
    """legacy key 归一成对象身份键（case_file/casefile 同身份）→ 别名并集。"""
    from backend.modules.catalog.services.ontology_service import object_identity_key

    merged: dict[str, list[str]] = {}
    for key, aliases in LEGACY_ALIAS_ZH.items():
        ik = object_identity_key(key)
        if not ik:
            continue
        bucket = merged.setdefault(ik, [])
        for a in aliases:
            if a not in bucket:
                bucket.append(a)
    return merged


def _fetch_models(model_id: int | None) -> list[dict]:
    from backend.common.db.metadata_db import get_metadata_conn

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            if model_id:
                cur.execute(
                    "SELECT id, name, status, json_content FROM adh_ontology_models "
                    "WHERE id = %s", (model_id,))
            else:
                cur.execute(
                    "SELECT id, name, status, json_content FROM adh_ontology_models "
                    "WHERE status != 'archived'")
            return list(cur.fetchall() or [])


def run(apply: bool, model_id: int | None) -> int:
    from backend.modules.catalog.services import ontology_service as osvc

    legacy = _match_keys()
    matched: set[str] = set()
    changed_models = 0

    for m in _fetch_models(model_id):
        if not m or m.get("status") == "archived":
            continue
        try:
            doc = json.loads(m.get("json_content") or "{}")
        except json.JSONDecodeError:
            print(f"[跳过] model #{m.get('id')} json_content 解析失败")
            continue
        touched = False
        for obj in doc.get("objects") or []:
            ik = osvc.object_identity_key(
                obj.get("key") or obj.get("primary_table") or "")
            adds = legacy.get(ik)
            if not adds:
                continue
            matched.add(ik)
            existing = [str(a) for a in (obj.get("aliases") or [])]
            seen = {a.lower() for a in existing}
            merged = list(existing)
            for a in adds:
                if a.lower() not in seen:
                    seen.add(a.lower())
                    merged.append(a)
                    touched = True
            obj["aliases"] = merged
        if not touched:
            continue
        changed_models += 1
        if apply:
            osvc.save_draft(m["id"], json.dumps(doc, ensure_ascii=False))
            print(f"[apply]   model #{m['id']} ({m.get('name') or ''}) 别名已收编并级联重建")
        else:
            print(f"[dry-run] model #{m['id']} ({m.get('name') or ''}) 将收编别名")

    unmatched = sorted(set(legacy) - matched)
    if unmatched:
        print("\n== 人工确认清单（无同身份对象可归属，未硬塞） ==")
        for k in unmatched:
            print(f"  {k}: {'/'.join(legacy[k])}")
    print(f"\n完成: 变更模型 {changed_models} 个"
          f"（{'已落库' if apply else 'dry-run，加 --apply 落库'}），"
          f"未匹配 key {len(unmatched)} 个")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="落库（默认 dry-run 只报告）")
    parser.add_argument("--model-id", type=int, default=None,
                        help="只处理指定模型 id")
    args = parser.parse_args()
    return run(apply=args.apply, model_id=args.model_id)


if __name__ == "__main__":
    raise SystemExit(main())
