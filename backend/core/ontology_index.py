"""本体模型索引（L1，common ← core ← semantics/modules）— 跨层共用的只读解析口。

为什么在 core 而不是 catalog service：这两份事实解析同时被
- 语义层（`backend/semantics/topology.py` 承载校验、绑定选路）与
- 目录层（`backend/modules/catalog` 的本体服务、生成器、前端接口）
消费。放在 modules 会构成 `semantics → modules` 的反向依赖（分层契约 R3 硬禁，
无豁免），所以把**解析**下沉到 core，modules 侧保留同名薄委托函数不破坏既有调用点。

口径纪律：
- 名字是稳定键（数据源/模型删除重建后 id 会变、name 不变，见 waker-datasource-domain §2），
  因此对外一律用 name 关联，id 只在服务端内部使用；
- 源本体的对象清单与 `object_keys` **同源**（都读 canonical doc）：不得一边读 doc、
  一边读展开行 `adh_ontology_objects`——存量展开行可能因旧字段名（object_key/name）而为空；
- 兼容存量空 `kind` 行：`datasource_id > 0` 视为源本体（旧口径），不静默丢弃。
"""
from __future__ import annotations

import json
import logging

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)


def datasource_name_set() -> set:
    """系统注册数据源业务名集合（承载/路由里 datasource 的解析口径）。"""
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM adh_datasources")
            return {str(r["name"] or "").strip() for r in (cur.fetchall() or [])} - {""}


def active_source_ontology_index() -> dict:
    """active 源本体索引：业务本体承载/路由引用的唯一解析口径。

    Returns: {model_name: {"id", "datasource_name", "object_keys": set,
                           "objects": [{"key", "name"}]}}
    """
    index: dict = {}
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT m.id, m.name, m.json_content, COALESCE(ds.name, '') AS ds_name "
                "FROM adh_ontology_models m "
                "LEFT JOIN adh_datasources ds ON ds.id = m.datasource_id "
                "WHERE m.status = 'active' "
                "  AND (m.kind = 'source' "
                "       OR (COALESCE(m.kind, '') = '' AND m.datasource_id > 0))")
            rows = cur.fetchall() or []
    for r in rows:
        try:
            src_doc = json.loads(r.get("json_content") or "{}")
        except (ValueError, TypeError):
            logger.warning("[ontology_index] 源本体 %s 的 json_content 解析失败，跳过",
                           r.get("name"))
            continue
        name = str(r.get("name") or "").strip()
        if not name or name in index:
            continue    # 同名多行（版本家族）保留首条，与 list_models 归组口径一致
        # 字段名归一（存量 doc 用 object_key/name 旧名）：只影响本索引输出，不回写 doc
        objs = [{"key": str(o.get("key") or o.get("object_key") or "").strip(),
                 "name": str(o.get("display_name") or o.get("name") or "").strip()}
                for o in (src_doc.get("objects") or []) if isinstance(o, dict)]
        objs = [o for o in objs if o["key"]]
        for o in objs:
            if not o["name"]:
                o["name"] = o["key"]
        index[name] = {
            "id": r["id"],
            "datasource_name": str(r.get("ds_name") or ""),
            "object_keys": {o["key"] for o in objs},
            "objects": sorted(objs, key=lambda o: (o["name"], o["key"])),
        }
    return index
