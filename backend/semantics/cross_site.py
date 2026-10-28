"""部署清单（v0.6）：对象 × 站点部署事实的只读派生 —— LLM 逐站编排的知识来源。

事实源 = `adh_ontology_models.deploy_sites`（模型级适用站点，一份契约 × N 站）
× 该模型的 active 对象绑定行 + `adh_sites.display_name`（站点业务名）。
未配置 deploy_sites 的模型（源本体/存量）回落按绑定行的 distinct datasource_name。
派生不落第二登记处（防漂移，见 GLOBAL_SEMANTIC_LAYER_DESIGN.md §6/§9）。

旧口径（逐对象逐站登记 `adh_ontology_bindings` 的 datasource_name）已收敛：
27 对象 × 5 站 = 135 行机械复制，改一次要改 135 处。

职责三分（§9）：
- 本模块：对象在哪几站可查（get_metrics.deployments 顺风车字段）；
- skill（data_analysis/system.md）：编排方法、口径红线、示例（站点↔数据源映射
  不再硬编码，由服务端「数据源拓扑」段按会话用户注入）；
- list_datasources：用户可查数据源（授权集，运行时逐用户）。

跨站执行 = LLM 逐站编排多次 run_semantic_query（各带 datasource 名）。
服务端跨站展开/合并（maybe_execute_cross_site/resolve_deployments/_reverse_deployments）
已随 v0.5 退役——勿再引入（tests/test_cross_site.py 锁定退役状态）。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def _load_facts() -> tuple[list[dict], dict[str, str], dict[str, dict]]:
    """加载派生事实：(对象→站点部署行, datasource_name→站点名, object_key→{name,aliases})。"""
    from backend.common.db.metadata_db import get_metadata_conn

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT site_code, display_name FROM adh_sites")
            code_names = {str(r["site_code"]): str(r["display_name"]) for r in (cur.fetchall() or [])}
            cur.execute("SELECT id, deploy_sites FROM adh_ontology_models")
            model_rows = cur.fetchall() or []
            cur.execute(
                "SELECT model_id, object_key, datasource_name FROM adh_ontology_bindings "
                "WHERE status = 'active' AND object_key <> '' "
                "GROUP BY model_id, object_key, datasource_name")
            bind_rows_raw = cur.fetchall() or []

        by_model: dict[int, list[dict]] = {}
        for r in bind_rows_raw:
            by_model.setdefault(int(r["model_id"] or 0), []).append(r)

        declared_models: set[int] = set()
        site_map: dict[str, str] = {}
        bind_rows: list[dict] = []
        for m in model_rows:
            mid = int(m["id"])
            raw = m.get("deploy_sites")
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="replace")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw or "[]")
                except (ValueError, TypeError):
                    raw = None
            if not isinstance(raw, list) or not raw:
                continue
            routes = [
                {"site_code": str(s.get("site_code") or ""), "datasource": str(s.get("datasource") or "")}
                for s in raw if isinstance(s, dict) and s.get("site_code")
            ]
            routes = [r for r in routes if r["datasource"]]
            if not routes:
                continue
            declared_models.add(mid)
            for r in routes:
                site_map[r["datasource"]] = code_names.get(r["site_code"], r["datasource"])
            # 一份契约 × N 站：该模型的每个对象都适用于声明的全部站点
            for row in by_model.get(mid, []):
                for r in routes:
                    bind_rows.append({"object_key": str(row["object_key"]),
                                      "datasource_name": r["datasource"]})
        # 未声明站点的模型（源本体/存量）：回落按绑定行自身的数据源
        for mid, rows in by_model.items():
            if mid in declared_models:
                continue
            for row in rows:
                ds = str(row.get("datasource_name") or "")
                if ds:
                    bind_rows.append({"object_key": str(row["object_key"]),
                                      "datasource_name": ds})

        keys = sorted({str(r["object_key"]) for r in bind_rows})
        meta: dict[str, dict] = {}
        if keys:
            fmt = ",".join(["%s"] * len(keys))
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT object_key, display_name, aliases FROM adh_ontology_objects "
                    f"WHERE is_active = 1 AND object_key IN ({fmt})", keys)
                for r in cur.fetchall() or []:
                    try:
                        aliases = json.loads(r.get("aliases") or "[]")
                    except (ValueError, TypeError):
                        aliases = []
                    existing = meta.get(str(r["object_key"]))
                    if existing is not None:
                        continue  # 同名对象多模型时保留首条（已按 object_key 去重）
                    meta[str(r["object_key"])] = {
                        "name": str(r["display_name"] or r["object_key"]),
                        "aliases": [str(a) for a in aliases],
                    }
    return bind_rows, site_map, meta


def list_deployments() -> list[dict]:
    """对象部署清单（LLM 编排知识）。

    Returns: [{object, name, aliases, deployments: [{site, datasource}], cross_site}]
    """
    bind_rows, site_map, meta = _load_facts()
    grouped: dict[str, list[dict]] = {}
    for r in bind_rows:
        key = str(r["object_key"])
        ds = str(r["datasource_name"])
        grouped.setdefault(key, []).append({
            "site": site_map.get(ds, ds),
            "datasource": ds,
        })
    out: list[dict] = []
    for key in sorted(grouped):
        deps = sorted(grouped[key], key=lambda d: (d["site"], d["datasource"]))
        out.append({
            "object": key,
            "name": meta.get(key, {}).get("name", key),
            "aliases": meta.get(key, {}).get("aliases", []),
            "deployments": deps,
            "cross_site": len(deps) >= 2,
        })
    logger.debug("[deployments] %d objects, %d cross-site",
                 len(out), sum(1 for e in out if e["cross_site"]))
    return out
