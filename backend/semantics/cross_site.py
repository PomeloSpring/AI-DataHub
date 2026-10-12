"""部署清单（v0.5）：对象 × 站点部署事实的只读派生 —— LLM 逐站编排的知识来源。

事实源 = adh_ontology_bindings 部署绑定（object_key × datasource_name, status='active'）
+ adh_data_products.site（站点业务名）。派生不落第二登记处（防漂移，见
GLOBAL_SEMANTIC_LAYER_DESIGN.md §6/§9）。

职责三分（§9）：
- 本模块：对象在哪几站可查（get_metrics.deployments 顺风车字段）；
- skill（data_analysis/system.md）：站点↔数据源映射、编排方法、口径红线、示例；
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
    """加载派生事实：(部署绑定行, datasource_name→站点名, object_key→{name,aliases})。"""
    from backend.common.db.metadata_db import get_metadata_conn

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT object_key, datasource_name FROM adh_ontology_bindings "
                "WHERE status = 'active' AND datasource_name <> '' "
                "GROUP BY object_key, datasource_name")
            bind_rows = cur.fetchall() or []
            cur.execute(
                "SELECT datasource_name, site FROM adh_data_products "
                "WHERE site IS NOT NULL AND site <> '' GROUP BY datasource_name, site")
            site_map = {
                str(r["datasource_name"]): str(r["site"])
                for r in (cur.fetchall() or [])
            }
            keys = sorted({str(r["object_key"]) for r in bind_rows})
            meta: dict[str, dict] = {}
            if keys:
                fmt = ",".join(["%s"] * len(keys))
                cur.execute(
                    f"SELECT object_key, display_name, aliases FROM adh_ontology_objects "
                    f"WHERE is_active = 1 AND object_key IN ({fmt})", keys)
                for r in cur.fetchall() or []:
                    try:
                        aliases = json.loads(r.get("aliases") or "[]")
                    except (ValueError, TypeError):
                        aliases = []
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
