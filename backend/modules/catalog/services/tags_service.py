"""Tags service - CRUD and query logic for tags, categories, and tag values."""

import logging
import time
from datetime import datetime
from typing import Optional

from backend.common.db import DBConnection

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def list_categories(workspace_id: int = 0) -> list:
    """List tag categories in tree structure.

    Args:
        workspace_id: Workspace isolation

    Returns:
        list of category dicts with children
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            ws_cond = "WHERE workspace_id = %s" if workspace_id else ""
            ws_params = [workspace_id] if workspace_id else []

            cur.execute(
                f"SELECT id, name, description, parent_id, sort_order, is_active "
                f"FROM adh_tag_categories {ws_cond} "
                f"ORDER BY sort_order, name",
                ws_params,
            )
            rows = cur.fetchall()

    # Build tree
    category_map = {}
    roots = []
    for row in rows:
        row["children"] = []
        category_map[row["id"]] = row

    for row in rows:
        parent_id = row.get("parent_id")
        if parent_id and parent_id in category_map:
            category_map[parent_id]["children"].append(row)
        else:
            roots.append(row)

    return roots


def create_category(data: dict) -> dict:
    """Create a tag category.

    Args:
        data: Category data

    Returns:
        dict with id and success
    """
    now = _now()
    row_id = int(time.time() * 1000000)

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_tag_categories "
                "(id, name, description, parent_id, sort_order, is_active, workspace_id, "
                "created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    data["name"],
                    data.get("description", ""),
                    data.get("parent_id"),
                    data.get("sort_order", 0),
                    data.get("is_active", 1),
                    data.get("workspace_id", 0),
                    now,
                ),
            )

    return {"id": row_id, "success": True}


def list_tags(
    page: int = 1,
    size: int = 50,
    category_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    search: str = "",
    workspace_id: int = 0,
) -> dict:
    """List tags with pagination and filters.

    Args:
        page: Page number (1-based)
        size: Page size
        category_id: Filter by category
        entity_type: Filter by entity type (table, column, metric)
        search: Search keyword
        workspace_id: Workspace isolation

    Returns:
        dict with total and items
    """
    conditions = []
    params = []

    if workspace_id:
        conditions.append("t.workspace_id = %s")
        params.append(workspace_id)
    if category_id:
        conditions.append("t.category_id = %s")
        params.append(category_id)
    if entity_type:
        conditions.append("t.entity_type = %s")
        params.append(entity_type)
    if search:
        conditions.append("(t.name LIKE %s OR t.description LIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS total FROM adh_tags t {where}",
                params,
            )
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT t.id, t.name, t.description, t.category_id, t.entity_type, "
                f"t.is_active, t.created_at, t.updated_at, "
                f"c.name AS category_name "
                f"FROM adh_tags t "
                f"LEFT JOIN adh_tag_categories c ON t.category_id = c.id "
                f"{where} "
                f"ORDER BY t.name LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                for k in ("created_at", "updated_at"):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()

    return {"total": total, "items": rows}


def create_tag(data: dict) -> dict:
    """Create a tag.

    Args:
        data: Tag data

    Returns:
        dict with id and success
    """
    now = _now()
    row_id = int(time.time() * 1000000)

    with DBConnection() as conn:
        with conn.cursor() as cur:
            # Check duplicate tag name in same category
            cur.execute(
                "SELECT id FROM adh_tags WHERE name = %s AND category_id = %s AND workspace_id = %s",
                (data["name"], data.get("category_id", 0), data.get("workspace_id", 0)),
            )
            if cur.fetchone():
                raise ValueError(f"Tag '{data['name']}' already exists in this category")

            # entity_type 列是 ENUM('user','table','column','metric','custom')，空串会写入失败
            cur.execute(
                "INSERT INTO adh_tags "
                "(id, name, description, category_id, entity_type, tag_type, data_type, color, "
                "is_active, workspace_id, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    data["name"],
                    data.get("description", ""),
                    data.get("category_id"),
                    data.get("entity_type") or "table",
                    data.get("tag_type") or "manual",
                    data.get("data_type") or "string",
                    data.get("color") or "",
                    data.get("is_active", 1),
                    data.get("workspace_id", 0),
                    now,
                    now,
                ),
            )

    return {"id": row_id, "success": True}


def update_tag(tag_id: int, data: dict) -> bool:
    """Update a tag.

    Args:
        tag_id: Tag ID
        data: Fields to update

    Returns:
        True if updated, False if not found
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_tags WHERE id = %s", (tag_id,))
            if not cur.fetchone():
                return False

            fields = []
            params = []
            for key in ("name", "description", "category_id", "entity_type", "is_active"):
                if key in data:
                    fields.append(f"{key} = %s")
                    params.append(data[key])

            if not fields:
                return True

            fields.append("updated_at = %s")
            params.append(_now())
            params.append(tag_id)

            cur.execute(f"UPDATE adh_tags SET {', '.join(fields)} WHERE id = %s", params)

    return True


def delete_tag(tag_id: int) -> bool:
    """Delete a tag and its values.

    Args:
        tag_id: Tag ID

    Returns:
        True if deleted, False if not found
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_tags WHERE id = %s", (tag_id,))
            if not cur.fetchone():
                return False

            cur.execute("DELETE FROM adh_tag_values WHERE tag_id = %s", (tag_id,))
            cur.execute("DELETE FROM adh_tags WHERE id = %s", (tag_id,))

    return True


def get_tag_values(tag_id: int, entity_type: Optional[str] = None) -> list:
    """Get tag values (entities tagged with this tag).

    Args:
        tag_id: Tag ID
        entity_type: Optional filter by entity type

    Returns:
        list of tag value dicts
    """
    # adh_tag_values 无 entity_type/entity_name 列：实体类型从标签定义 JOIN 得出
    with DBConnection() as conn:
        with conn.cursor() as cur:
            conditions = ["tv.tag_id = %s"]
            params = [tag_id]

            if entity_type:
                conditions.append("t.entity_type = %s")
                params.append(entity_type)

            where = f"WHERE {' AND '.join(conditions)}"

            cur.execute(
                f"SELECT tv.id, t.entity_type, tv.entity_id, tv.value, tv.source, tv.created_at "
                f"FROM adh_tag_values tv "
                f"JOIN adh_tags t ON tv.tag_id = t.id "
                f"{where} "
                f"ORDER BY tv.created_at DESC",
                params,
            )
            rows = cur.fetchall()
            for r in rows:
                if hasattr(r.get("created_at"), "isoformat"):
                    r["created_at"] = r["created_at"].isoformat()

    return rows


def set_tag_value(tag_id: int, data: dict) -> dict:
    """Set tag value for an entity.

    Args:
        tag_id: Tag ID
        data: Tag value data (entity_type, entity_id, entity_name)

    Returns:
        dict with id and success
    """
    now = _now()
    row_id = int(time.time() * 1000000)

    with DBConnection() as conn:
        with conn.cursor() as cur:
            # Check if tag exists (workspace_id 跟随标签定义)
            cur.execute("SELECT id, workspace_id FROM adh_tags WHERE id = %s", (tag_id,))
            tag_row = cur.fetchone()
            if not tag_row:
                raise ValueError(f"Tag {tag_id} not found")

            cur.execute(
                "SELECT id FROM adh_tag_values "
                "WHERE tag_id = %s AND entity_id = %s",
                (tag_id, data["entity_id"]),
            )
            if cur.fetchone():
                return {"success": True, "message": "Tag already applied to this entity"}

            cur.execute(
                "INSERT INTO adh_tag_values "
                "(id, workspace_id, tag_id, entity_id, value, confidence, source, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, 1.00, %s, %s, %s)",
                (
                    row_id,
                    data.get("workspace_id", tag_row["workspace_id"]),
                    tag_id,
                    data["entity_id"],
                    data.get("value") or data.get("entity_name") or "",
                    data.get("source") or "manual",
                    now,
                    now,
                ),
            )

    result = {"id": row_id, "success": True}
    # 打标后立即把「业务域」标注派生回 adh_table_info.domain_tag。
    # 派生失败不阻断打标主链路，但必须把失败显式带回给调用方（不得静默）。
    try:
        result["tag_derivation"] = derive_table_domain_tags()
    except Exception as e:  # noqa: BLE001 — 见上
        logger.warning("[Tags] derive_table_domain_tags failed: %s", e)
        result["tag_derivation"] = {"error": str(e)}
    return result


# ── 域标记事实源归一（adh_tag_values → adh_table_info.domain_tag）──

_DOMAIN_CATEGORY = "业务域"


def derive_table_domain_tags(datasource_id: Optional[int] = None) -> dict:
    """把 `adh_tag_values` 的「业务域」人工标注派生回写 `adh_table_info.domain_tag`。

    事实源划分（ontology-modeling §3 同一概念只登记一处）：
    - **人工标注源** = `adh_tag_values`（标签管理页录入，三个分类：业务域/数据分层/敏感级别）；
    - **派生缓存** = `adh_table_info.domain_tag`，供检索选表 / LLM 生成分批消费，
      不得独立编辑，也不得被元数据同步的表名推断覆盖。

    为什么需要派生：`metadata_sync.extract_domain_tag` 是表名**前缀**启发式
    （dim_/dwd_/ods_/adh_），对 `t_*` 前缀的业务表一条都不命中，全部回落 'other'——
    后端吃到的域信号实际是失效的，而真正在用的人工标注却只有前端在读。

    唯一匹配才写：`adh_tag_values.entity_id` 是裸表名（无 datasource 维度），
    同名表跨数据源会串味。命中多个数据源的表**跳过并记入 warnings**，不猜。
    """
    updated: list = []
    ambiguous: list = []
    uncovered: list = []
    warnings: list = []

    with DBConnection() as conn:
        with conn.cursor() as cur:
            # 1) 人工标注：只要「业务域」分类、table 类实体
            cur.execute(
                "SELECT tv.entity_id AS table_name, tv.value AS domain_name "
                "FROM adh_tag_values tv "
                "JOIN adh_tags t ON tv.tag_id = t.id "
                "JOIN adh_tag_categories c ON t.category_id = c.id "
                "WHERE c.name = %s AND t.entity_type = 'table' AND t.is_active = 1",
                (_DOMAIN_CATEGORY,),
            )
            labels: dict = {}
            for r in cur.fetchall() or []:
                name = str(r.get("table_name") or "").strip()
                val = str(r.get("domain_name") or "").strip()
                if not name or not val:
                    continue
                if name in labels and labels[name] != val:
                    # 同一张表被打了两个业务域 → 不猜，显式记录
                    warnings.append(f"表 {name} 同时标注了多个业务域: "
                                    f"{labels[name]} / {val}，未派生")
                    labels[name] = "__conflict__"
                elif name not in labels:
                    labels[name] = val

            # 2) 现有表（可按数据源限定）
            sql = ("SELECT id, datasource_id, table_name, domain_tag FROM adh_table_info "
                   "WHERE is_active = 1")
            params: list = []
            if datasource_id:
                sql += " AND datasource_id = %s"
                params.append(datasource_id)
            cur.execute(sql, params)
            rows = cur.fetchall() or []

            # 同名表出现在多个数据源下 → 歧义，不派生
            by_name: dict = {}
            for r in rows:
                by_name.setdefault(str(r.get("table_name") or ""), []).append(r)

            for name, rs in by_name.items():
                if not name:
                    continue
                label = labels.get(name)
                if not label:
                    uncovered.append(name)
                    continue
                if label == "__conflict__":
                    continue
                if len(rs) > 1:
                    ambiguous.append(name)
                    continue
                row = rs[0]
                if (row.get("domain_tag") or "") == label:
                    continue
                cur.execute("UPDATE adh_table_info SET domain_tag = %s WHERE id = %s",
                            (label, row["id"]))
                updated.append(name)

    if ambiguous:
        warnings.append(
            f"{len(ambiguous)} 张同名表存在于多个数据源, 未派生业务域(避免串味): "
            + ", ".join(sorted(ambiguous)[:10]))
    logger.info("[Tags] derive_table_domain_tags: updated=%d uncovered=%d ambiguous=%d",
                len(updated), len(uncovered), len(ambiguous))
    return {
        "updated": len(updated),
        "updated_tables": sorted(updated),
        "uncovered_count": len(uncovered),
        "uncovered_sample": sorted(uncovered)[:20],
        "ambiguous": sorted(ambiguous),
        "warnings": warnings,
    }


def query_entities_by_tags(conditions: list, operator: str = "AND", workspace_id: int = 0,
                           datasource_ids: Optional[list] = None) -> list:
    """Query entities by tag conditions (intersection/union).

    Args:
        conditions: list of dicts with tag_id and optionally tag_name, value
        operator: "AND" for intersection, "OR" for union
        workspace_id: Workspace isolation
        datasource_ids: 本体资源域隔离——仅返回归属于这些数据源的实体。
            传入空列表视为无任何授权(fail-closed, 返回空); 不传(None)则不限数据源(仅 REST 旧调用兼容)。

    Returns:
        list of entity dicts with matched tag info
    """
    if not conditions:
        return []

    with DBConnection() as conn:
        with conn.cursor() as cur:
            # 查询主体条件仅按 tag_id 匹配（value 过滤在前端结果展示层处理）
            tag_ids = [c["tag_id"] for c in conditions]
            placeholders = ", ".join(["%s"] * len(tag_ids))
            # entity_type 来自标签定义（adh_tag_values 无该列）
            select_sql = (
                f"SELECT t.entity_type, tv.entity_id, "
                f"COUNT(DISTINCT tv.tag_id) AS match_count, "
                f"GROUP_CONCAT(DISTINCT t.name) AS matched_tags "
                f"FROM adh_tag_values tv "
                f"JOIN adh_tags t ON tv.tag_id = t.id "
            )
            ws_cond = "AND tv.workspace_id = %s" if workspace_id else ""

            if operator.upper() == "AND":
                # Intersection: entities must have ALL specified tags
                cur.execute(
                    select_sql + f"WHERE tv.tag_id IN ({placeholders}) {ws_cond} "
                    f"GROUP BY t.entity_type, tv.entity_id "
                    f"HAVING match_count = %s "
                    f"ORDER BY t.entity_type, tv.entity_id",
                    tag_ids + ([workspace_id] if workspace_id else []) + [len(tag_ids)],
                )
            else:
                # Union: entities with ANY specified tag
                cur.execute(
                    select_sql + f"WHERE tv.tag_id IN ({placeholders}) {ws_cond} "
                    f"GROUP BY t.entity_type, tv.entity_id "
                    f"ORDER BY match_count DESC, t.entity_type, tv.entity_id",
                    tag_ids + ([workspace_id] if workspace_id else []),
                )

            rows = cur.fetchall()
            for r in rows:
                # 前端按数组渲染 matched_tags，GROUP_CONCAT 字符串转列表
                mt = r.get("matched_tags") or ""
                r["matched_tags"] = [x for x in mt.split(",") if x]

    return _filter_by_datasource(rows, datasource_ids)


def _filter_by_datasource(rows: list, datasource_ids: Optional[list]) -> list:
    """按数据源资源域过滤 tag 命中实体(entity_type='table' 的 entity_id 为物理表名)。

    datasource_ids=None → 不过滤(REST 旧行为); 空集 → fail-closed 不返回。
    仅对可映射到数据源的实体类型(table/column/metric)保留, custom/user 无数据源属主不纳入。
    """
    if datasource_ids is None or not rows:
        return rows
    allowed = {int(d) for d in datasource_ids if d}
    if not allowed:
        return []  # fail-closed: 无授权数据源不返回任何实体
    marks = ", ".join(["%s"] * len(allowed))
    ds_params = list(allowed)
    # 每种实体类型 → 该授权数据源集下的实体标识集合
    type_queries = {
        "table": f"SELECT DISTINCT table_name AS e FROM adh_table_info WHERE datasource_id IN ({marks})",
        "column": f"SELECT DISTINCT CONCAT(table_name, '.', column_name) AS e FROM adh_column_metadata WHERE datasource_id IN ({marks})",
        "metric": f"SELECT DISTINCT name AS e FROM adh_metrics WHERE is_active = 1 AND datasource_id IN ({marks})",
    }
    allowed_entities: dict[str, set] = {}
    with DBConnection() as conn:
        with conn.cursor() as cur:
            for et, q in type_queries.items():
                try:
                    cur.execute(q, ds_params)
                    allowed_entities[et] = {str(r["e"]) for r in cur.fetchall()}
                except Exception:  # noqa: BLE001 — 某类映射表不可用时该类实体不命中(保守)
                    allowed_entities[et] = set()
    kept = []
    for r in rows:
        et = r.get("entity_type")
        eid = str(r.get("entity_id") or "")
        allow = allowed_entities.get(et)
        if allow is None:
            continue  # custom/user 等无法归属数据源 → 不纳入受治理结果
        if eid in allow or (et == "column" and any(e.endswith("." + eid) for e in allow)):
            kept.append(r)
    return kept
