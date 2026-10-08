"""Metrics service - CRUD logic for metrics and dimensions."""

import json
import logging
import time
from datetime import datetime
from typing import Optional

from backend.common.db import DBConnection

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 前端表单字段 ↔ adh_metrics 真实列的映射(页面用 name/display_name/metric_type/…,
# 语义层 mdl_compiler/run_semantic_query 读的是 name(中文名)/name_en/formula/agg_type)
_FRONTEND_TO_DB = {
    "display_name": "name",
    "metric_type": "category",
    "calculation_type": "agg_type",
    "expression": "formula",
    "unit": "unit",
    "description": "description",
    "target_table": "target_table",
    "target_column": "target_column",
    "is_active": "is_active",
    "category": "category",
    "bound_object_key": "bound_object_key",
    "formula_dsl": "formula_dsl",
}

_ALIAS_SELECT = (
    "id, name, name AS display_name, name_en, category AS metric_type, "
    "agg_type AS calculation_type, agg_type, formula AS expression, formula, "
    "unit, target_table, target_column, description, category, "
    "COALESCE(aliases, '[]') AS aliases, "
    "bound_object_key, is_active, workspace_id, created_at, updated_at"
)


def _norm_aliases(val):
    """别名入参归一: list/逗号串 → JSON 字符串; 空 → '[]'。列型为 JSON。"""
    if val is None:
        return "[]"
    if isinstance(val, str):
        items = [x.strip() for x in val.split(",") if x.strip()]
    elif isinstance(val, (list, tuple)):
        items = [str(x).strip() for x in val if str(x).strip()]
    else:
        items = []
    # 去重保序
    seen: list[str] = []
    for x in items:
        if x not in seen:
            seen.append(x)
    return json.dumps(seen, ensure_ascii=False)


def _isoformat(rows: list) -> list:
    for r in rows:
        for k in ("created_at", "updated_at"):
            if hasattr(r.get(k), "isoformat"):
                r[k] = r[k].isoformat()
    return rows


def _model_object_keys(model_id: int) -> list[str]:
    """取某本体模型当前生效的对象 key 集合(模型工作区作用域的锚点)。

    源自 adh_ontology_objects(激活/保存时由 _expand_objects 展开写入, 幂等)。
    """
    if not model_id:
        return []
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT object_key FROM adh_ontology_objects "
                "WHERE model_id = %s AND is_active = 1", (model_id,))
            return [r["object_key"] for r in cur.fetchall() if r.get("object_key")]


def _apply_model_scope(conditions: list, params: list,
                       model_id: Optional[int], scope: Optional[str]) -> bool:
    """把模型作用域追加到 WHERE 条件; 返回 False 表示必然空集(调用方可短路)。

    scope: 'model'=只看本模型对象挂接的资产(需 model_id); 'unbound'=只看待归属(未绑定);
    其他/空 = 不加过滤(全局字典视图旧行为不变)。MySQL ci 排序规则使 IN 天然大小写不敏感。
    """
    if scope == "unbound":
        conditions.append("(bound_object_key IS NULL OR bound_object_key = '')")
        return True
    if scope == "model" or model_id:
        keys = _model_object_keys(int(model_id or 0))
        if not keys:
            return False
        conditions.append(
            f"bound_object_key IN ({', '.join(['%s'] * len(keys))})")
        params.extend(keys)
        return True
    return True


def list_metrics(
    page: int = 1,
    size: int = 20,
    metric_type: Optional[str] = None,
    tags: Optional[str] = None,
    search: str = "",
    workspace_id: int = 0,
    model_id: Optional[int] = None,
    scope: Optional[str] = None,
) -> dict:
    """List metrics with pagination and filters.

    Args:
        page: Page number (1-based)
        size: Page size
        metric_type: Filter by metric type (e.g., "basic", "derived", "compound")
        tags: Filter by tags (comma-separated)
        search: Search keyword
        workspace_id: Workspace isolation
        model_id: 本体模型作用域(配合 scope='model'): 只列 bound_object_key ∈ 该模型对象集的指标
        scope: 'model' | 'unbound' | 空(全局, 旧行为)

    Returns:
        dict with total and items
    """
    conditions = []
    params = []

    if workspace_id:
        conditions.append("workspace_id = %s")
        params.append(workspace_id)
    if metric_type:
        conditions.append("(category = %s OR agg_type = %s)")
        params.extend([metric_type, metric_type])
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        for tag in tag_list:
            conditions.append("FIND_IN_SET(%s, category)")
            params.append(tag)
    if search:
        conditions.append("(name LIKE %s OR name_en LIKE %s OR description LIKE %s)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    if not _apply_model_scope(conditions, params, model_id, scope):
        return {"total": 0, "items": []}

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM adh_metrics {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT {_ALIAS_SELECT} "
                f"FROM adh_metrics {where} "
                f"ORDER BY name LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            _isoformat(rows)

    return {"total": total, "items": rows}


def create_metric(data: dict) -> dict:
    """Create a new metric.

    Args:
        data: Metric data dict

    Returns:
        dict with id and success
    """
    now = _now()

    # 前端 name 是英文标识(如 gmv), display_name 是业务名(如 交易总额);
    # 语义层按 name 取指标, 因此 name 存中文业务名, name_en 存标识。
    name_en = (data.get("name") or "").strip()
    biz_name = (data.get("display_name") or data.get("name") or "").strip()
    if not biz_name:
        raise ValueError("metric name is required")
    agg_type = (data.get("calculation_type") or data.get("agg_type") or "count").upper()
    formula = data.get("expression") or data.get("formula") or ""

    with DBConnection() as conn:
        with conn.cursor() as cur:
            # Check duplicate (同名中文名或英文标识)
            cur.execute(
                "SELECT id FROM adh_metrics WHERE (name = %s OR name_en = %s) AND workspace_id = %s",
                (biz_name, name_en or biz_name, data.get("workspace_id", 0)),
            )
            if cur.fetchone():
                raise ValueError(f"Metric '{biz_name}' already exists")

            # 字典写入口的引用完整性: bound_object_key 必须指向当前生效对象(ontology-modeling §4),
            # 悬空创建即拒, 不等到激活阶段才暴露
            _require_object_keys(cur, [data.get("bound_object_key")])

            # adh_metrics.id 是 INT, 不能用时间戳主键
            cur.execute("SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM adh_metrics")
            row_id = cur.fetchone()["nid"]
            cur.execute(
                "INSERT INTO adh_metrics "
                "(id, workspace_id, name, name_en, formula, unit, agg_type, "
                "target_table, target_column, description, owner, category, datasource_id, "
                "is_active, created_at, updated_at, default_agg, bound_object_key, aliases) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    data.get("workspace_id", 0),
                    biz_name,
                    name_en,
                    formula,
                    data.get("unit", ""),
                    agg_type,
                    data.get("target_table", ""),
                    data.get("target_column", ""),
                    data.get("description", ""),
                    data.get("owner", ""),
                    data.get("metric_type") or data.get("category") or "业务",
                    data.get("datasource_id", 0),
                    data.get("is_active", 1),
                    now,
                    now,
                    agg_type,
                    data.get("bound_object_key") or None,
                    _norm_aliases(data.get("aliases")),
                ),
            )

    return {"id": row_id, "success": True}


def get_metric(metric_id: int) -> Optional[dict]:
    """Get metric detail with dimensions."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_ALIAS_SELECT} FROM adh_metrics WHERE id = %s",
                (metric_id,),
            )
            metric = cur.fetchone()
            if not metric:
                return None

            _isoformat([metric])

            # Get dimensions(关系表 JOIN 维度字典)
            metric["dimensions"] = _fetch_metric_dimensions(cur, metric_id)

    return metric


def _fetch_metric_dimensions(cur, metric_id: int) -> list:
    cur.execute(
        "SELECT md.id, md.metric_id, md.relation_type, md.description, "
        "d.id AS dimension_id, d.name, d.name AS display_name, d.name_en, "
        "d.target_table, d.target_column AS column_name, d.category AS data_type "
        "FROM adh_metric_dimensions md "
        "JOIN adh_dimensions d ON md.dimension_id = d.id "
        "WHERE md.metric_id = %s "
        "ORDER BY d.name",
        (metric_id,),
    )
    return cur.fetchall()


def _require_object_keys(cur, keys) -> None:
    """字典写入的 bound_object_key 引用校验: 非空 key 必须在当前生效对象集内。"""
    want = [str(k).strip() for k in keys if k and str(k).strip()]
    if not want:
        return
    ph = ", ".join(["%s"] * len(want))
    cur.execute(
        f"SELECT object_key FROM adh_ontology_objects "
        f"WHERE is_active = 1 AND object_key IN ({ph})", tuple(want))
    ok = {str(r["object_key"]).lower() for r in cur.fetchall()}
    bad = [k for k in want if k.lower() not in ok]
    if bad:
        raise ValueError(f"归属对象不存在或已失效: {', '.join(bad)}（请先在本体模型中创建并激活对象）")


def update_metric(metric_id: int, data: dict) -> bool:
    """Update a metric.

    Args:
        metric_id: Metric ID
        data: Fields to update

    Returns:
        True if updated, False if not found
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_metrics WHERE id = %s", (metric_id,))
            if not cur.fetchone():
                return False
            if data.get("bound_object_key"):
                _require_object_keys(cur, [data.get("bound_object_key")])

            fields = []
            params = []
            # 前端键 → 真实列映射
            mapped: dict = {}
            for fe_key, db_key in _FRONTEND_TO_DB.items():
                if fe_key in data:
                    mapped[db_key] = data[fe_key]
            if "name" in data and "display_name" not in data and "name" not in mapped:
                # 前端 name 是标识 → 写 name_en, 避免误覆盖中文业务名
                mapped["name_en"] = data["name"]
            if "calculation_type" in data:
                mapped["agg_type"] = (data["calculation_type"] or "").upper()
            if "expression" in data:
                mapped["formula"] = data["expression"]
            for key in ("name", "name_en", "formula", "agg_type", "unit", "description",
                        "category", "target_table", "target_column", "bound_object_key",
                        "formula_dsl", "is_active", "default_agg", "certified", "owner"):
                if key in mapped:
                    fields.append(f"{key} = %s")
                    params.append(mapped[key])
            if "aliases" in data:
                fields.append("aliases = %s")
                params.append(_norm_aliases(data["aliases"]))

            if not fields:
                return True

            fields.append("updated_at = %s")
            params.append(_now())
            params.append(metric_id)

            cur.execute(f"UPDATE adh_metrics SET {', '.join(fields)} WHERE id = %s", params)

    return True


def delete_metric(metric_id: int) -> bool:
    """Delete a metric and its dimensions.

    Args:
        metric_id: Metric ID

    Returns:
        True if deleted, False if not found
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_metrics WHERE id = %s", (metric_id,))
            if not cur.fetchone():
                return False

            cur.execute("DELETE FROM adh_metric_dimensions WHERE metric_id = %s", (metric_id,))
            cur.execute("DELETE FROM adh_metrics WHERE id = %s", (metric_id,))

    return True


def get_dimensions(metric_id: int) -> list:
    """Get dimensions for a metric (relation table JOIN adh_dimensions)."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            return _fetch_metric_dimensions(cur, metric_id)


def add_dimension(metric_id: int, data: dict) -> dict:
    """Link a dimension to a metric.

    维度字典在 adh_dimensions, 本表只是 metric↔dimension 关系;
    传入的维度名在字典中不存在时自动创建字典行。
    """
    now = _now()

    dim_name = (data.get("display_name") or data.get("name") or "").strip()
    dim_en = (data.get("name") or "").strip() if data.get("display_name") else ""
    if not dim_name:
        raise ValueError("dimension name is required")

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, target_table FROM adh_metrics WHERE id = %s", (metric_id,))
            metric_row = cur.fetchone()
            if not metric_row:
                raise ValueError(f"Metric {metric_id} not found")

            # 在维度字典中查找(中文名或英文标识命中即可)
            cur.execute(
                "SELECT id FROM adh_dimensions WHERE name = %s OR (name_en <> '' AND name_en = %s) "
                "ORDER BY id LIMIT 1",
                (dim_name, dim_en or dim_name),
            )
            hit = cur.fetchone()
            if hit:
                dimension_id = hit["id"]
            else:
                # adh_dimensions.id 是 INT, 不能用时间戳主键
                cur.execute("SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM adh_dimensions")
                dimension_id = cur.fetchone()["nid"]
                cur.execute(
                    "INSERT INTO adh_dimensions "
                    "(id, name, name_en, level, target_table, target_column, description, "
                    "category, datasource_id, is_active, created_at, updated_at) "
                    "VALUES (%s, %s, %s, 0, %s, %s, %s, %s, 0, 1, %s, %s)",
                    (
                        dimension_id,
                        dim_name,
                        dim_en,
                        data.get("target_table") or metric_row["target_table"] or "",
                        data.get("column_name") or data.get("target_column") or "",
                        data.get("description", ""),
                        data.get("category") or "属性",
                        now,
                        now,
                    ),
                )

            # Check duplicate link
            cur.execute(
                "SELECT id FROM adh_metric_dimensions WHERE metric_id = %s AND dimension_id = %s",
                (metric_id, dimension_id),
            )
            if cur.fetchone():
                raise ValueError(f"Dimension '{dim_name}' already linked to this metric")

            # adh_metric_dimensions.id 是 INT
            cur.execute("SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM adh_metric_dimensions")
            row_id = cur.fetchone()["nid"]
            cur.execute(
                "INSERT INTO adh_metric_dimensions "
                "(id, metric_id, dimension_id, relation_type, description, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    metric_id,
                    dimension_id,
                    data.get("relation_type") or "GROUP_BY",
                    data.get("description", ""),
                    now,
                ),
            )

    return {"id": row_id, "dimension_id": dimension_id, "success": True}


# ── 全局维度字典(adh_dimensions) — 指标中心唯一人工编辑入口 ──────────────
# run_semantic_query 解析维度名/别名/枚举标签的权威来源。与本体回写链路
# (sync_enums_to_dimensions)协同: aliases/value_labels 人工值优先, 本体激活只补空白不覆盖。
# name / datasource_id / is_active 不开放——是字典键与血缘锚点。

_DIM_SELECT = (
    "id, COALESCE(name, '') AS name, COALESCE(name_en, '') AS name_en, "
    "COALESCE(category, '') AS category, COALESCE(level, 0) AS level, "
    "COALESCE(hierarchy, '') AS hierarchy, COALESCE(target_table, '') AS target_table, "
    "COALESCE(target_column, '') AS target_column, "
    "COALESCE(bound_object_key, '') AS bound_object_key, "
    "aliases, value_labels, COALESCE(certified, 0) AS certified, "
    "COALESCE(datasource_id, 0) AS datasource_id, COALESCE(description, '') AS description"
)

# 白名单可编辑列(字典键/血缘锚点不在此列表, 一律拒绝写入; bound_object_key 为归属引用, 开放)
_DIM_EDITABLE = [
    "name_en", "category", "level", "hierarchy",
    "aliases", "value_labels", "description", "certified", "bound_object_key",
]


def list_all_dimensions(model_id: Optional[int] = None,
                        scope: Optional[str] = None) -> dict:
    """全局维度字典只读列表(供指标中心「维度字典」Tab 与模型工作区复用)。

    model_id/scope 语义同 list_metrics: scope='model' 只看本模型对象挂接维度,
    'unbound' 看待归属, 缺省保持旧行为(全量)。
    """
    conditions = ["is_active = 1"]
    params: list = []
    if not _apply_model_scope(conditions, params, model_id, scope):
        return {"total": 0, "items": []}
    where = "WHERE " + " AND ".join(conditions)
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_DIM_SELECT} FROM adh_dimensions {where} "
                f"ORDER BY category, name", params)
            rows = cur.fetchall()
    return {"total": len(rows), "items": rows}


def update_dimension(dim_id: int, data: dict) -> bool:
    """编辑全局维度字典白名单列。

    aliases: list[str]; value_labels: dict[str,str] — 以 JSON 列存储。
    仅更新传入的白名单字段; 非白名单字段(name/datasource_id/is_active)忽略。
    bound_object_key 开放: 模型工作区"归属到对象/资产池归属动作"的写入口(对象名是引用非锚点)。
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_dimensions WHERE id = %s", (dim_id,))
            if not cur.fetchone():
                return False

            fields = []
            params = []
            for key in _DIM_EDITABLE:
                if key not in data:
                    continue
                val = data[key]
                if key in ("aliases", "value_labels"):
                    if val in (None, "", [], {}):
                        val = None
                    else:
                        val = json.dumps(val, ensure_ascii=False)
                elif key == "certified":
                    val = 1 if val else 0
                elif key == "level":
                    val = int(val or 0)
                fields.append(f"{key} = %s")
                params.append(val)

            if not fields:
                return True

            params.append(dim_id)
            cur.execute(f"UPDATE adh_dimensions SET {', '.join(fields)} WHERE id = %s", params)

    return True


# ── 未归属资产池 (模型工作区缓冲区视图) ────────────────────────

def delete_dimension(dim_id: int) -> bool:
    """删除维度字典行及其指标关系行(与 delete_metric 同口径的物理删除)。

    字典行是引用方(bound_object_key 指向对象), 删除不产生悬空引用;
    图谱/知识库里的残留由各自下次重建对账。
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_dimensions WHERE id = %s", (dim_id,))
            if not cur.fetchone():
                return False

            cur.execute("DELETE FROM adh_metric_dimensions WHERE dimension_id = %s", (dim_id,))
            cur.execute("DELETE FROM adh_dimensions WHERE id = %s", (dim_id,))

    return True


def asset_pool() -> dict:
    """聚合所有"尚未归属/悬空"的建模资产, 供工作区左栏资产池与各页计数徽章。

    - metrics/dimensions: bound_object_key 为空(unbound) 或指向已不再生效对象的孤儿(orphan);
    - terms: 未绑定任何表的黑话(target_table 为空);
    - suggestions: 解析失败回流的候选词(adh_alias_suggestions pending, 表未迁移时容错为空)。
    只读聚合, 治理动作(归属/收编/退回)由各自既有写入口完成。
    """
    out: dict = {"metrics": [], "dimensions": [], "terms": [], "suggestions": [], "counts": {}}
    orphan_cond = (
        "(COALESCE(bound_object_key, '') = '' OR bound_object_key NOT IN "
        "(SELECT object_key FROM adh_ontology_objects WHERE is_active = 1))")
    reason_case = ("CASE WHEN COALESCE(bound_object_key, '') = '' "
                   "THEN 'unbound' ELSE 'orphan' END AS pool_reason")
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_ALIAS_SELECT}, {reason_case} FROM adh_metrics "
                f"WHERE is_active = 1 AND {orphan_cond} ORDER BY name LIMIT 200")
            out["metrics"] = _isoformat(cur.fetchall())
            cur.execute(
                f"SELECT {_DIM_SELECT}, {reason_case} FROM adh_dimensions "
                f"WHERE is_active = 1 AND {orphan_cond} ORDER BY name LIMIT 200")
            out["dimensions"] = cur.fetchall()
            cur.execute(
                "SELECT id, datasource_id, term_cn, term_en, term_aliases, term_type, "
                "       target_table, target_column, description "
                "FROM adh_business_terms WHERE is_active = 1 "
                "  AND COALESCE(target_table, '') = '' ORDER BY term_cn LIMIT 200")
            out["terms"] = cur.fetchall()
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, term, target_type, target_ref, datasource_id, source, "
                    "       candidates, hit_count, updated_at "
                    "FROM adh_alias_suggestions WHERE status = 'pending' "
                    "ORDER BY hit_count DESC, updated_at DESC LIMIT 100")
                out["suggestions"] = cur.fetchall()
    except Exception as e:  # noqa: BLE001 — 队列表未迁移属正常, 不报错
        logger.debug("[asset_pool] suggestions skipped: %s", e)
    out["counts"] = {k: len(out[k]) for k in ("metrics", "dimensions", "terms", "suggestions")}
    out["counts"]["total"] = sum(out["counts"].values())
    return out


# ── 口径认证（P4）：区分「可信口径」与「草稿口径」 ─────────────────
# 为什么要认证：指标/维度是给业务看的权威口径，未经人工确认的 formula 可能是
# LLM 归纳或历史遗留，直接当权威答案会误导决策。认证 = 有人对其口径负责。
# 纪律：认证只是**标记权威性**，不改变解析/查询行为（否则 0 认证时知识库会空，
# 反而破坏现有功能）；未认证口径仍然可用，但必须在展示时标注为草稿。

CERTIFY_SCOPE = ("metric", "dimension")


def certify_metric(metric_id: int, certified: bool, owner: str = "",
                   certified_by: str = "") -> dict:
    """认证/解除认证指标口径。

    Args:
        metric_id: 指标 id
        certified: True=认证为可信口径；False=退回草稿
        owner: 口径责任人（认证时必填——谁认的、谁负责口径不漂）
        certified_by: 操作者标记（审计用，如 `user:1`）

    Returns:
        {"metric_id","name","certified","owner"}
    """
    return _certify("adh_metrics", metric_id, certified, owner, certified_by)


def certify_dimension(dim_id: int, certified: bool, owner: str = "",
                      certified_by: str = "") -> dict:
    """认证/解除认证维度口径（语义同 certify_metric）。"""
    return _certify("adh_dimensions", dim_id, certified, owner, certified_by)


def _certify(table: str, row_id: int, certified: bool, owner: str,
             certified_by: str) -> dict:
    if table not in ("adh_metrics", "adh_dimensions"):
        raise ValueError("不支持的口径类型")
    if certified and not str(owner or "").strip():
        # 认证必须有责任人：没有责任人的口径不叫认证，只是把 0 改成 1
        raise ValueError("认证口径必须指定 owner（口径责任人）")
    # 责任人列两表不同：adh_metrics.owner / adh_dimensions.owner_role
    owner_col = "owner" if table == "adh_metrics" else "owner_role"
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, name, certified, {owner_col} AS owner FROM {table} WHERE id = %s",
                (row_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"口径不存在: {row_id}")
            cur.execute(
                f"UPDATE {table} SET certified = %s, {owner_col} = %s, updated_at = %s WHERE id = %s",
                (1 if certified else 0, str(owner or "").strip() or (row.get("owner") or ""),
                 _now(), row_id))
    logger.info("[Metrics] certify %s id=%s certified=%s owner=%s by=%s",
                table, row_id, certified, owner, certified_by)
    return {"id": row_id, "name": row.get("name"), "certified": bool(certified),
            "owner": str(owner or "").strip() or (row.get("owner") or "")}


def certification_summary(datasource_id: int = 0) -> dict:
    """口径认证覆盖率（P4.3）：暴露「多少口径还没人负责」这个事实。

    认证率低不是缺陷，是**待办清单**——但必须可见，不能假装口径都是权威的。
    """
    scope = " AND datasource_id = %s" if datasource_id else ""
    params = (datasource_id,) if datasource_id else ()
    with DBConnection() as conn:
        with conn.cursor() as cur:
            # 责任人列两表不同：adh_metrics.owner / adh_dimensions.owner_role
            cur.execute(
                f"SELECT COUNT(*) total, SUM(COALESCE(certified,0)) cert, "
                f"       SUM(CASE WHEN COALESCE(owner,'')='' THEN 1 ELSE 0 END) no_owner "
                f"FROM adh_metrics WHERE is_active = 1{scope}", params)
            m = cur.fetchone() or {}
            cur.execute(
                f"SELECT COUNT(*) total, SUM(COALESCE(certified,0)) cert, "
                f"       SUM(CASE WHEN COALESCE(owner_role,'')='' THEN 1 ELSE 0 END) no_owner "
                f"FROM adh_dimensions WHERE is_active = 1{scope}", params)
            d = cur.fetchone() or {}

    def _pack(row):
        total = int(row.get("total") or 0)
        cert = int(row.get("cert") or 0)
        return {"total": total, "certified": cert, "draft": total - cert,
                "without_owner": int(row.get("no_owner") or 0),
                "certified_ratio": round(cert / total, 4) if total else 0.0}

    metrics, dims = _pack(m), _pack(d)
    total = metrics["total"] + dims["total"]
    cert = metrics["certified"] + dims["certified"]
    return {
        "metrics": metrics, "dimensions": dims,
        "overall": {"total": total, "certified": cert, "draft": total - cert,
                    "certified_ratio": round(cert / total, 4) if total else 0.0},
        "note": ("口径全部已认证" if total and cert == total else
                 f"仍有 {total - cert} 个口径未认证（草稿），展示时应标注为草稿而非权威答案"),
    }
