"""术语收编（规则 §5「收编即退役」）：adh_business_terms 黑话缓冲区收敛。

判定口径：
- 术语的 term_cn/term_en 命中字典名（adh_metrics/adh_dimensions 的 name/name_en）
  或对象名（canonical 对象 key/display_name/aliases）→ 视为"已收编"；
- 已收编术语的同义词（term_cn/term_en/term_aliases）并入归属方 aliases（去重后），
  术语行 is_active=0 退役；
- 命中多个不同归属方 = 歧义，只进人工确认清单，不自动合并；
- 无命中 = 尚未收编的纯黑话，保留在缓冲区（这是合法状态），列入待收编清单。

用法（repo 根目录）:
  venv/bin/python backend/scripts/absorb_business_terms.py            # dry-run 报告
  venv/bin/python backend/scripts/absorb_business_terms.py --apply    # 合并别名+术语退役

幂等：第二次执行应报 0 处合并。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

_SPLIT = re.compile(r"[,，、;；/|]+")


def _tokens(*vals) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in vals:
        for t in _SPLIT.split(str(v or "")):
            t = t.strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
    return out


def _norm(s) -> str:
    return str(s or "").strip().lower()


def _load_targets() -> tuple[list[dict], list[dict]]:
    """返回 (字典目标, 对象目标)。目标 = {kind, ref, names, aliases}。"""
    from backend.common.db.metadata_db import get_metadata_conn

    dicts: list[dict] = []
    objs: list[dict] = []
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            for tbl in ("adh_metrics", "adh_dimensions"):
                cur.execute(
                    f"SELECT id, name, name_en, aliases, bound_object_key "
                    f"FROM {tbl} WHERE is_active = 1")
                for r in cur.fetchall() or []:
                    dicts.append({
                        "kind": tbl,
                        "ref": r["id"],
                        "names": _tokens(r.get("name"), r.get("name_en")),
                        "aliases": _tokens(r.get("aliases")),
                    })
            cur.execute(
                "SELECT id, json_content FROM adh_ontology_models "
                "WHERE status = 'active' AND json_content IS NOT NULL")
            for m in cur.fetchall() or []:
                try:
                    doc = json.loads(m.get("json_content") or "{}")
                except json.JSONDecodeError:
                    continue
                for o in doc.get("objects") or []:
                    objs.append({
                        "kind": "object",
                        "ref": (m["id"], o.get("key") or ""),
                        "names": _tokens(o.get("key"), o.get("display_name")),
                        "aliases": _tokens(o.get("aliases")),
                    })
    return dicts, objs


def _resolve(term_tokens: list[str], targets: list[dict]) -> tuple[dict | None, list[dict]]:
    """命中唯一归属方 → (target, [])；歧义 → (None, 候选)；无命中 → (None, [])。"""
    hits: list[dict] = []
    for t in targets:
        known = {_norm(x) for x in t["names"] + t["aliases"]}
        if any(tok.lower() in known for tok in term_tokens):
            hits.append(t)
    # 按归属身份去重（同名/别名命中同一方只算一次）
    uniq: dict = {}
    for h in hits:
        uniq[(h["kind"], str(h["ref"]))] = h
    hits = list(uniq.values())
    if len(hits) == 1:
        return hits[0], []
    return None, hits


def run(apply: bool) -> int:
    from backend.common.db.metadata_db import get_metadata_conn

    dicts, objs = _load_targets()
    targets = dicts + objs

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM adh_business_terms WHERE is_active = 1")
            terms = cur.fetchall() or []

    merged = 0
    ambiguous: list[tuple] = []
    pending: list[dict] = []
    dict_updates: dict = {}          # (kind, ref) -> merged alias list
    object_updates: dict = {}        # model_id -> {key: merged alias list}
    retire_ids: list[int] = []

    for term in terms:
        toks = _tokens(term.get("term_cn"), term.get("term_en"), term.get("term_aliases"))
        target, amb = _resolve(toks, targets)
        if amb:
            ambiguous.append((term, amb))
            continue
        if not target:
            pending.append(term)
            continue
        known = {_norm(x) for x in target["names"] + target["aliases"]}
        add = [t for t in toks if t.lower() not in known]
        if target["kind"] == "object":
            model_id, key = target["ref"]
            bucket = object_updates.setdefault(model_id, {}).setdefault(key, [])
        else:
            bucket = dict_updates.setdefault((target["kind"], target["ref"]), [])
        for a in add:
            if a.lower() not in {_norm(x) for x in bucket}:
                bucket.append(a)
        retire_ids.append(term["id"])
        merged += 1

    print(f"术语总数 {len(terms)}：可收编 {merged}，歧义 {len(ambiguous)}，"
          f"未收编黑话 {len(pending)}")

    if ambiguous:
        print("\n== 人工确认清单（命中多个归属方，未自动合并） ==")
        for term, amb in ambiguous:
            who = " / ".join(f"{h['kind']}:{h['ref']}" for h in amb)
            print(f"  {term['term_cn']} → {who}")

    if pending:
        print("\n== 待收编清单（纯黑话，保留在缓冲区） ==")
        for term in pending:
            print(f"  {term['term_cn']} ({term.get('term_aliases') or '-'})")

    if not apply:
        if dict_updates or object_updates:
            print(f"\n[dry-run] 将更新字典行 {len(dict_updates)} 条、对象 {sum(len(v) for v in object_updates.values())} 个，"
                  f"退役术语 {len(retire_ids)} 条；加 --apply 落库")
        return 0

    # ── 落库 ──────────────────────────────────────────────────────────
    from backend.modules.catalog.services import ontology_service as osvc

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            for (kind, ref), adds in dict_updates.items():
                if not adds:
                    continue
                cur.execute(f"SELECT aliases FROM {kind} WHERE id = %s", (ref,))
                row = cur.fetchone()
                cur.execute(f"UPDATE {kind} SET aliases = %s WHERE id = %s",
                            (json.dumps(_tokens(row.get("aliases"), adds), ensure_ascii=False), ref))
            if retire_ids:
                fmt = ",".join(["%s"] * len(retire_ids))
                cur.execute(f"UPDATE adh_business_terms SET is_active = 0 WHERE id IN ({fmt})",
                            retire_ids)
        conn.commit()

    for model_id, by_key in object_updates.items():
        model = osvc.get_model(model_id)
        if not model:
            continue
        doc = json.loads(model.get("json_content") or "{}")
        touched = False
        for o in doc.get("objects") or []:
            adds = by_key.get(str(o.get("key") or ""))
            if not adds:
                continue
            o["aliases"] = _tokens(o.get("aliases"), adds)
            touched = True
        if touched:
            osvc.save_draft(model_id, json.dumps(doc, ensure_ascii=False))
            print(f"[apply] model #{model_id} 对象别名收编并级联重建")

    print(f"\n完成: 收编并退役术语 {len(retire_ids)} 条（字典 {len(dict_updates)} 行、"
          f"对象 {sum(len(v) for v in object_updates.values())} 个）")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true", help="落库（默认 dry-run）")
    return run(apply=p.parse_args().apply)


if __name__ == "__main__":
    raise SystemExit(main())
