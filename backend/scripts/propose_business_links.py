"""业务本体关系（links）候选推断：从列元数据确定性出候选，只报告不自动改。

为什么要这个脚本：现网业务本体 17 个对象 `links[] = 0`（全库 0 条关系登记），
而语义层的跨对象引用（如「案例的所属公司名称」）完全依赖 canonical `links[].join`
做 ≤2 跳 BFS 解 JOIN 路径（`semantics/planner.py::_load_object_links` +
`_link_chain`）。没有 links = 跨对象问题一律回抛 `no_join_path`，评测里
`join_three_hop_chain` 那类用例必然失败——关系网是业务本体「跨站点统一描述 + 关系网」
定位里缺的那一半。

补关系**不能靠猜**（宁缺勿错：错数比无答案危害大）。本脚本用三个确定性信号出候选：
  ① 目标表把该列当身份列（`is_key=1`，或表名主干 + `_code/_id/_no` 同名自证）；
  ② 源表存在**同名**列（外键式，值域天然对齐）；
  ③ 列注释/业务名（`column_comment`/`business_desc`）指向目标对象的显示名或别名（佐证）。
命中 ①+② → 置信「高」；再加 ③ → 更强；只有 ①+③ → 「中」；只有 ②（列名巧合） → 「低」，
低置信默认不推给用户，只计数提示。

分层与纪律：
- 默认 **dry-run 只出报告**（与 `check_alias_projection.py` 同族：只报告不自动改）。
  候选必须经人工审业务名/基数后才进 canonical doc。
- `--apply --yes` 才落库，且**只走草案**（`save_draft_only`，两段式第一段：发布是独立动作
  + 独立权限码），并且**只追加不覆盖**既有 links（防 `_reject_destructive_overwrite` 那类损毁）。
- `links[].join` 是物理信息：只存在于 canonical doc/内部 md/图谱，上云一律剔除
  （`ontology_service.to_cloud_md` 白名单已保证，本脚本不新增泄露面）。

用法（repo 根目录）:
  venv/bin/python backend/scripts/propose_business_links.py                 # 报告全部候选
  venv/bin/python backend/scripts/propose_business_links.py --min-confidence 中
  venv/bin/python backend/scripts/propose_business_links.py --json         # 机器可读
  venv/bin/python backend/scripts/propose_business_links.py --apply --yes  # 落草案（人工确认后）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

from backend.common.db.metadata_db import get_metadata_conn  # noqa: E402

_KEY_SUFFIXES = ("_code", "_id", "_no")
_CONF_ORDER = {"高": 3, "中": 2, "低": 1}
# 自关联常见的层级列（公司父子、部门上级）——保留但关系名要能读
_TABLE_PREFIX_RE = re.compile(r"^(?:t|tb|sys|open|dwd|ads)[_.]", re.I)


def _biz_model_id(conn, model_id: int = 0) -> int:
    """缺省取 active 业务本体模型（业务关系网只能挂在业务本体上）。"""
    if int(model_id or 0):
        return int(model_id)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM adh_ontology_models "
                    "WHERE kind = 'business' AND status = 'active' ORDER BY id LIMIT 1")
        row = cur.fetchone()
    if not row:
        raise ValueError("未找到生效的业务本体模型（kind='business' AND status='active'）；"
                         "请先确认本体已发布，或用 --model-id 指定")
    return int(row["id"])


def _load_model(conn, model_id: int) -> tuple[dict, dict]:
    """返回 (模型行, canonical doc)。"""
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, kind, datasource_id, json_content "
                    "FROM adh_ontology_models WHERE id = %s", (model_id,))
        row = cur.fetchone()
    if not row:
        raise ValueError(f"本体模型不存在: {model_id}")
    if str(row.get("kind") or "") == "system":
        raise ValueError(f"系统本体「{row.get('name')}」随项目版本发布，不在本脚本维护范围内")
    try:
        doc = json.loads(row.get("json_content") or "{}")
    except (ValueError, TypeError) as e:
        raise ValueError(f"模型 {model_id} 的 canonical doc 无法解析: {e}") from e
    return row, doc


def _object_tables(doc: dict) -> dict[str, dict]:
    """{object_key: {table, display_name, aliases}}（主表为空的对象不参与推断）。"""
    out: dict[str, dict] = {}
    for o in doc.get("objects") or []:
        key = str(o.get("key") or o.get("object_key") or "").strip()
        table = str(o.get("primary_table") or "").strip().split(".")[-1].strip()
        if not key or not table:
            continue
        out[key] = {"table": table,
                    "display_name": str(o.get("display_name") or o.get("name") or key),
                    "aliases": [str(a) for a in (o.get("aliases") or []) if str(a or "").strip()]}
    return out


def _as_flag(val) -> int:
    """`is_key` 在现网存的是字符串（'true'/'false'）而不是 0/1，直转 int 会炸。"""
    if isinstance(val, bool):
        return 1 if val else 0
    s = str(val or "").strip().lower()
    if s in ("1", "true", "t", "y", "yes"):
        return 1
    return 0


def _load_columns(conn, datasource_id: int, tables: list[str]) -> dict[str, dict[str, dict]]:
    """{table: {column: {comment, business_desc, is_key}}}（一次性取齐，避免逐表往返）。"""
    if not tables:
        return {}
    ph = ", ".join(["%s"] * len(tables))
    sql = ("SELECT table_name, column_name, COALESCE(column_comment,'') AS column_comment, "
           "COALESCE(business_desc,'') AS business_desc, is_key "
           f"FROM adh_column_metadata WHERE datasource_id = %s AND table_name IN ({ph})")
    with conn.cursor() as cur:
        cur.execute(sql, [int(datasource_id)] + list(tables))
        rows = cur.fetchall() or []
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        out.setdefault(str(r["table_name"]), {})[str(r["column_name"])] = {
            "comment": str(r.get("column_comment") or ""),
            "business_desc": str(r.get("business_desc") or ""),
            "is_key": _as_flag(r.get("is_key")),
        }
    return out


def _table_stem(table: str) -> str:
    """表名主干（去前缀、去复数尾 s）：`t_user_company` → `user_company`→`user_compan`? 保守只剥前缀。"""
    s = _TABLE_PREFIX_RE.sub("", str(table or ""))
    return s.strip("_").lower()


def _identity_columns(table: str, cols: dict[str, dict]) -> set[str]:
    """目标表的「身份列」：is_key=1，或表名主干 + 键后缀同名（自证式业务主键）。

    现网业务库几乎不建物理外键（`adh_table_relations` 只有 2 行），所以只能靠
    元数据里的身份列标记 + 同名列对齐这两个信号。
    """
    ident = {c for c, m in cols.items() if int(m.get("is_key") or 0)}
    stem = _table_stem(table)
    for suffix in _KEY_SUFFIXES:
        for cand in (f"{stem}{suffix}", f"{stem.rstrip('s')}{suffix}"):
            if cand in cols:
                ident.add(cand)
    # 单段主干也试（t_case_records.case_code 这类：stem=case_records → 主干 case）
    head = stem.split("_")[0]
    for suffix in _KEY_SUFFIXES:
        for cand in (f"{head}{suffix}",):
            if cand in cols:
                ident.add(cand)
    return ident


def _mentions_target(text: str, target: dict) -> bool:
    """注释/业务名是否指向目标对象（业务名或别名出现）。"""
    s = str(text or "")
    if not s:
        return False
    names = [target.get("display_name") or ""] + list(target.get("aliases") or [])
    for n in names:
        n = str(n or "").strip()
        if len(n) >= 2 and n in s:
            return True
    return False


def build_candidates(objs: dict[str, dict], meta: dict[str, dict[str, dict]], *,
                     include_low: bool = False) -> list[dict]:
    """确定性关系候选（按 置信度 desc, from, to, col 稳定排序）。

    每个候选：{from, to, column, target_column, join, cardinality, type, confidence, evidence}。
    `join` 用物理表名限定（planner 的 `_rewrite_join` 按 identity 归一改写成查询内别名）。
    低置信（只有列名巧合、无注释佐证也无目标表同名列）默认不产出——宁缺勿错。
    """
    ident_by_table = {k: _identity_columns(v["table"], meta.get(v["table"]) or {})
                      for k, v in objs.items()}
    # 身份列 → 归属对象（同名身份列可能属多个对象，如 company_code 既是 company 的身份列）
    ident_owner: dict[str, list[str]] = {}
    for key, idents in ident_by_table.items():
        for col in idents:
            ident_owner.setdefault(col, []).append(key)

    cands: list[dict] = []
    for src_key, src in sorted(objs.items()):
        src_cols = meta.get(src["table"]) or {}
        for col in sorted(src_cols):
            if not col.endswith(_KEY_SUFFIXES):
                continue
            owners = [t for t in ident_owner.get(col, []) if t != src_key]
            if not owners:
                continue          # 只有身份列是它自己时才可能是"自身主键"，不是关系
            for tgt_key in sorted(owners):
                tgt = objs[tgt_key]
                if col in (ident_by_table.get(src_key) or set()) and len(owners) > 1:
                    continue      # 该列同时是本对象身份列且有多个同名归属：歧义，不猜
                note = src_cols[col].get("comment") or src_cols[col].get("business_desc") or ""
                mention = _mentions_target(note, tgt)
                same_table_key = col in (meta.get(tgt["table"]) or {})
                evidence = ["目标身份列"]
                if same_table_key:
                    evidence.append("目标表同名列")
                if mention:
                    evidence.append("注释指向")
                if same_table_key:
                    conf = "高"
                elif mention:
                    conf = "中"
                else:
                    conf = "低"
                if conf == "低" and not include_low:
                    continue
                cands.append({
                    "from": src_key,
                    "to": tgt_key,
                    "column": col,
                    "target_column": col,
                    "join": f"{src['table']}.{col} = {tgt['table']}.{col}",
                    "cardinality": "N:1",
                    "type": f"所属{tgt['display_name']}",
                    "confidence": conf,
                    "evidence": evidence,
                    "comment": note,
                })
    dup: set[tuple] = set()
    kept: list[dict] = []
    for cd in cands:
        pair = (cd["from"], cd["to"], cd["column"])
        if pair in dup:
            continue
        dup.add(pair)
        kept.append(cd)
    kept.sort(key=lambda x: (-_CONF_ORDER[x["confidence"]], x["from"], x["to"], x["column"]))
    return kept


def _identity(s) -> str:
    """对象/列名比较用的归一键（与 planner._identity 同口径：忽大小写与分隔符）。"""
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def audit_existing_links(objs: dict[str, dict], meta: dict[str, dict[str, dict]],
                         doc_objects: list[dict]) -> list[dict]:
    """已登记 links 的可执行性审计（与 planner 的判定口径对齐，不是另造一套规则）。

    planner 会把下面三类关联直接跳过（`planner.py::_rewrite_join` 及其调用点）：
      1. join 里引用了未建模的对象名（如桥接对象 `Membership`/`UserRole` 没登记）；
      2. join 含跨跳占位符 `→` / `—`（M:N 手写两跳，语义层不解析）；
      3. 列名在目标对象主表上不存在（生成 SQL 会 Unknown column）。
    另有“无列元数据可比对”：不是已知的错，而是**无法验证**（元数据未同步），
    必须单列一类报出来——当成“通过”会把缺口一直藏到跑数时才暴露。
    """
    names = set()
    for key, v in objs.items():
        names.add(_identity(key))
        names.add(_identity(v.get("display_name")))
        for a in v.get("aliases") or []:
            names.add(_identity(a))
    key_by_ref = {_identity(k): k for k in objs}
    key_by_ref.update({_identity(v["display_name"]): k for k, v in objs.items()
                       if str(v.get("display_name") or "").strip()})

    issues: list[dict] = []
    for o in doc_objects:
        src = str(o.get("key") or o.get("object_key") or "")
        for l in (o.get("links") or []):
            join = str((l or {}).get("join") or "")
            target = str((l or {}).get("target") or "")
            kinds: list[str] = []
            detail: list[str] = []
            if not join:
                kinds.append("语义关系无 join")     # 不构成 SQL 链（检索可用，取数不可用）
            if "→" in join or "—" in join:
                kinds.append("跨跳占位符")
            for ref, col in re.findall(r"([A-Za-z_]\w*)\.(\w+)", join):
                ok_key = key_by_ref.get(_identity(ref))
                if ok_key is None and _identity(ref) not in names:
                    kinds.append("引用未建模对象")
                    detail.append(f"{ref}.{col}")
                    continue
                table = (objs.get(ok_key or "") or {}).get("table") or ""
                cols = meta.get(table) or {}
                if not cols:
                    if "目标无列元数据" not in kinds:
                        kinds.append("目标无列元数据")
                        detail.append(ref)
                elif _identity(col) not in {_identity(x) for x in cols}:
                    kinds.append("列不存在")
                    detail.append(f"{ref}.{col}")
            if target and _identity(target) not in key_by_ref and _identity(target) not in names:
                kinds.append("target 悬空")
                detail.append(target)
            if kinds:
                issues.append({"from": src, "to": target, "join": join,
                               "kinds": sorted(set(kinds)), "detail": detail[:6],
                               "cardinality": str((l or {}).get("cardinality") or ""),
                               "type": str((l or {}).get("type") or "")})
    return issues


def _doc_has_link(doc: dict, cand: dict) -> bool:
    """doc 里是否已登记该关系（按 from+join 判重，幂等追加不覆盖）。"""
    for o in doc.get("objects") or []:
        if str(o.get("key") or o.get("object_key") or "") != cand["from"]:
            continue
        for l in (o.get("links") or []):
            if str((l or {}).get("join") or "").replace(" ", "") == cand["join"].replace(" ", ""):
                return True
            if str((l or {}).get("target") or "") == cand["to"]:
                return True           # 同对象已有关系：不重复追加（人工确认后再补第二条）
    return False


def apply_to_draft(conn, model_id: int, cands: list[dict], *, created_by: str = "") -> dict:
    """把候选写进 canonical doc 的草案（只追加，objects 其余字段原样保留）。

    两段式：本函数只落草案，**不发布**（发布是独立动作 + 独立权限码，会级联重展开
    objects → 重建图谱 → 同步知识库）。走 `ontology_service.save_draft_only` 与 AS-BOT
    写通道同一落库路径，不自建第二条 UPDATE 派生表的旁路。
    """
    row, doc = _load_model(conn, model_id)
    objs = doc.get("objects") or []
    by_key = {str(o.get("key") or o.get("object_key") or ""): o for o in objs}
    added: list[str] = []
    for cd in cands:
        o = by_key.get(cd["from"])
        if o is None:
            continue
        if _doc_has_link(doc, cd):
            continue
        links = o.setdefault("links", [])
        links.append({"target": cd["to"], "type": cd["type"], "join": cd["join"],
                      "cardinality": cd["cardinality"],
                      "description": cd["comment"] or cd["type"]})
        added.append(f"{cd['from']}→{cd['to']}({cd['column']})")
    if not added:
        return {"model_id": model_id, "added": [], "draft_saved": False,
                "note": "候选关系已全部登记（或无可写入项），草案未变更"}

    from backend.modules.catalog.services import ontology_service
    ontology_service.save_draft_only(
        model_id, json.dumps(doc, ensure_ascii=False), name=row.get("name"),
        created_by=created_by)
    return {"model_id": model_id, "added": added, "draft_saved": True,
            "next": "草案已保存：请在「本体建模」页核对关系名/基数后发布（publish 才级联生效）"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="业务本体关系候选推断（默认只报告）")
    ap.add_argument("--model-id", type=int, default=0, help="业务本体模型 id（缺省自动取 active 业务本体）")
    ap.add_argument("--min-confidence", choices=list(_CONF_ORDER), default="中",
                    help="最低置信度（默认 中；低置信只计数不列出）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    ap.add_argument("--apply", action="store_true", help="写入草案（默认 dry-run 只报告）")
    ap.add_argument("--yes", action="store_true", help="--apply 的二次确认（防误写共享元库）")
    ap.add_argument("--created-by", default="propose_business_links")
    args = ap.parse_args(argv)

    conn = get_metadata_conn()
    try:
        model_id = _biz_model_id(conn, args.model_id)
        row, doc = _load_model(conn, model_id)
        objs = _object_tables(doc)
        meta = _load_columns(conn, int(row.get("datasource_id") or 0),
                             [v["table"] for v in objs.values()])
        missing = [k for k, v in objs.items() if not meta.get(v["table"])]
        all_cands = build_candidates(objs, meta, include_low=True)
        low = [c for c in all_cands if c["confidence"] == "低"]
        shown = [c for c in all_cands
                 if _CONF_ORDER[c["confidence"]] >= _CONF_ORDER[args.min_confidence]]
        already = [c for c in shown if _doc_has_link(doc, c)]
        todo = [c for c in shown if c not in already]
        registered = sum(len(o.get("links") or []) for o in (doc.get("objects") or []))
        broken = audit_existing_links(objs, meta, doc.get("objects") or [])

        if args.json:
            print(json.dumps({"model_id": model_id, "model": row.get("name"),
                              "objects": len(objs), "existing_links": registered,
                              "links_audit": {"unusable": broken,
                                              "usable": registered - len(broken)},
                              "candidates": todo, "already_registered": already,
                              "low_confidence_skipped": len(low),
                              "tables_without_metadata": missing},
                             ensure_ascii=False, indent=2))
        else:
            print(f"[模型] {row.get('name')}（id={model_id}） objects={len(objs)} 已登记 links={registered}")
            kinds_count: dict[str, int] = {}
            for b in broken:
                for k in b["kinds"]:
                    kinds_count[k] = kinds_count.get(k, 0) + 1
            print(f"[审计] 已登记关系 {registered} 条：可走通 {registered - len(broken)} 条，"
                  f"有问题 {len(broken)} 条（按类型重叠计数: "
                  f"{('、'.join(f'{k}×{v}' for k, v in sorted(kinds_count.items())) or '无')}）")
            for b in broken[:12]:
                print(f"  [{'/'.join(b['kinds'])}] {b['from']} → {b['to']} [{b['type']}] {b['join'][:56]}")
                if b["detail"]:
                    print(f"        涉: {'、'.join(b['detail'])}")
            if len(broken) > 12:
                print(f"  … 共 {len(broken)} 条，跑 --json 看全量")
            if missing:
                print(f"[警告] 以下对象主表在列元数据里没有对应行（元数据未同步或表名漂移）: "
                      f"{'、'.join(missing)}")
            print(f"[候选] 置信≥{args.min_confidence} 共 {len(shown)} 条（已登记 {len(already)} 条，"
                  f"待确认 {len(todo)} 条；低置信跳过 {len(low)} 条）")
            for cd in todo:
                print(f"  [{cd['confidence']}] {cd['from']} → {cd['to']}"
                      f"  键: {cd['column']}  基数: {cd['cardinality']}  关系名: {cd['type']}"
                      f"  据: {'/'.join(cd['evidence'])}")
                if cd["comment"]:
                    print(f"        注释: {cd['comment'][:40]}")
                print(f"        join: {cd['join']}")

        if args.apply:
            if not args.yes:
                print("\n[拒绝] --apply 需要同时给 --yes：候选关系必须人工审过业务名/基数才落草案")
                return 2
            res = apply_to_draft(conn, model_id, todo, created_by=args.created_by)
            print("\n[草案] " + json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print("\n[dry-run] 未写库。确认无误后：--apply --yes（只落草案，发布仍需人工点「发布」）")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
