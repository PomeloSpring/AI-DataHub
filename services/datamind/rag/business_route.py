"""业务本体路由解析：业务对象命中 → 目标源本体路由（canonical 确定性读取）。

业务本体是"概念 + 路由指针"的索引层：RAG 命中业务概念后，路由告诉 LLM
该去哪个源本体做语义检索与执行（如"日本站点"→ 目标源本体/数据源 + 源内过滤提示）。

权威性纪律：路由数据从 adh_ontology_models.json_content（canonical 事实源）读取，
**不从知识库文本回传**——KB 文档可能 stale（doc_stale），脱敏文档不得当数据通道
（护栏 §7）。返回 name 级（源本体名/数据源名/对象 key/维度名），
不含 datasource_id/物理表列/连接信息。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def resolve_business_routes(object_keys: list) -> list:
    """业务对象 key → 路由清单（含其关联的跨源场景路由）。

    Returns:
        [{
          "object_key": 业务对象 key,
          "mode": "source" | "object_filter",
          "source_ontology": 目标源本体名,
          "datasource_name": 目标数据源业务名,
          "target_object_key": 源本体对象 key（mode=object_filter 时非空）,
          "filter_hints": [{"dimension": ..., "examples": [...]}],
          "scenarios": [{"key", "title", "sources": [...], "join_hint"}],  # 关联跨源场景
        }]

    未命中或无 route 的 key 不返回（宁缺勿错，不猜路由）。
    返回 [] = 确实无路由；canonical 读取失败**抛异常**（不返 []），由调用方
    显式标注降级（routes=[] 与 failed 必须可区分，no-silent-degradation）。
    """
    keys = {str(k).strip() for k in (object_keys or []) if str(k).strip()}
    if not keys:
        return []
    from services.shared.common.db.metadata_db import get_metadata_conn
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, json_content FROM adh_ontology_models "
                "WHERE status = 'active' AND kind = 'business'")
            rows = cur.fetchall()

    routes: list = []
    for row in rows:
        try:
            doc = json.loads(row.get("json_content") or "{}")
        except (ValueError, TypeError):
            logger.warning("[business_route] 模型 %s 的 json_content 解析失败，跳过", row.get("name"))
            continue

        doc_routes: list = []
        for o in (doc.get("objects") or []):
            ok = str(o.get("key") or "")
            if ok not in keys:
                continue
            route = o.get("route")
            if not isinstance(route, dict) or not route.get("source_ontology"):
                continue
            doc_routes.append({
                "object_key": ok,
                "mode": str(route.get("mode") or "source"),
                "source_ontology": str(route.get("source_ontology") or ""),
                "datasource_name": str(route.get("datasource_name") or ""),
                "target_object_key": str(route.get("object_key") or ""),
                "filter_hints": [h for h in (route.get("filter_hints") or [])
                                 if (h or {}).get("dimension")],
            })

        # 关联场景路由（跨源）：命中对象出现在场景 route.sources 的 object_keys 中
        for s in (doc.get("scenarios") or []):
            sroute = s.get("route") or {}
            entries = sroute.get("sources") or []
            if not entries or not doc_routes:
                continue
            scenario_keys = {str(k) for e in entries for k in (e.get("object_keys") or [])}
            if not (scenario_keys & keys):
                continue
            scenario = {
                "key": str(s.get("key") or ""),
                "title": str(s.get("title") or ""),
                "sources": [{
                    "source_ontology": str(e.get("source_ontology") or ""),
                    "datasource_name": str(e.get("datasource_name") or ""),
                    "object_keys": [str(k) for k in (e.get("object_keys") or [])],
                } for e in entries],
                "join_hint": str(sroute.get("join_hint") or ""),
            }
            for r in doc_routes:
                r.setdefault("scenarios", []).append(scenario)

        routes.extend(doc_routes)
    return routes
