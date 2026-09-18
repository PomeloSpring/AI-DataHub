"""MDL Compiler (语义层版) — 扩展 manifest_builder 的 Cube-MDL 生成。

对比 datamind 版 `manifest_builder.build_manifest`, 本模块新增:
1. **真实 catalog**:通过 `adh_table_info.catalog_id` -> `adh_catalogs.catalog_name`,
   支持 Doris 三段式联邦 (`catalog.db.table`), 根治 catalog="adh" 硬编码。
2. **measures/dimensions**:`adh_metrics`/`adh_dimensions` 编译进 model.measures /
   model.dimensions, 优先取 `formula_dsl` + `default_agg` + `certified`(口径 SSoT)。
3. **binding-aware 表引用**:如果传入 `bindings`, 每个 model 的 tableReference
   按 ResolvedBinding 的 catalog_ref 输出, 与 binding 表保持单一事实源。
4. **保留 RLS/列级脱敏注入**:直接复用 manifest_builder 的 helpers, 不改语义。

Phase 3/4 时, `datamind/nl2sql/sql/manifest_builder.py` 会改为本模块的薄代理。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from services.shared.common.db.metadata_db import get_metadata_conn
from services.shared.semantics.models import ResolvedBinding

logger = logging.getLogger(__name__)


def _map_column_type(mysql_type: str) -> str:
    """类型映射与 datamind 保持一致;这里独立一份避免跨服务耦合。"""
    from services.datamind.nl2sql.sql.manifest_builder import _map_column_type as _impl
    return _impl(mysql_type)


def _map_join_type(rel_type: str) -> str:
    from services.datamind.nl2sql.sql.manifest_builder import _map_join_type as _impl
    return _impl(rel_type)


def _find_primary_key(cols: list[dict]) -> Optional[str]:
    from services.datamind.nl2sql.sql.manifest_builder import _find_primary_key as _impl
    return _impl(cols)


def _build_session_properties_for_rls(policies: list[dict]) -> list[dict]:
    from services.datamind.nl2sql.sql.manifest_builder import (
        _build_session_properties_for_rls as _impl,
    )
    return _impl(policies)


# ── metadata loaders (语义层新增/扩展) ─────────────────────────

def _get_tables_with_catalog(datasource_id: int) -> list[dict]:
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.id, t.table_name, t.table_comment, d.database_name AS db_name, "
                "       t.catalog_id, t.size_class, t.query_mode, t.allow_full_scan, "
                "       c.catalog_name "
                "FROM adh_table_info t "
                "LEFT JOIN adh_catalogs c ON c.id = t.catalog_id "
                "LEFT JOIN adh_datasources d ON d.id = t.datasource_id "
                "WHERE t.datasource_id = %s AND t.is_active = 1",
                (datasource_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _get_metrics(datasource_id: int) -> list[dict]:
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT id, name, name_en, formula, agg_type, unit, "
                    "       target_table, target_column, category, "
                    "       certified, owner_role, default_agg, bound_object_key, formula_dsl, aliases "
                    "FROM adh_metrics "
                    "WHERE is_active = 1 AND (datasource_id = %s OR datasource_id = 0)",
                    (datasource_id,),
                )
            except Exception:
                # aliases 为字典增强列; 迁移未执行时回落旧查询
                cur.execute(
                    "SELECT id, name, name_en, formula, agg_type, unit, "
                    "       target_table, target_column, category, "
                    "       certified, owner_role, default_agg, bound_object_key, formula_dsl "
                    "FROM adh_metrics "
                    "WHERE is_active = 1 AND (datasource_id = %s OR datasource_id = 0)",
                    (datasource_id,),
                )
                return cur.fetchall()
            rows = cur.fetchall()
            for r in rows:
                r["aliases"] = _parse_json_field(r.get("aliases")) or []
            return rows
    finally:
        conn.close()


def _get_dimensions(datasource_id: int) -> list[dict]:
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT id, name, name_en, hierarchy, level, "
                    "       target_table, target_column, category, "
                    "       certified, owner_role, bound_object_key, aliases, value_labels "
                    "FROM adh_dimensions "
                    "WHERE is_active = 1 AND (datasource_id = %s OR datasource_id = 0)",
                    (datasource_id,),
                )
            except Exception:
                # aliases/value_labels 为字典增强列; 迁移未执行时回落旧查询
                cur.execute(
                    "SELECT id, name, name_en, hierarchy, level, "
                    "       target_table, target_column, category, "
                    "       certified, owner_role, bound_object_key "
                    "FROM adh_dimensions "
                    "WHERE is_active = 1 AND (datasource_id = %s OR datasource_id = 0)",
                    (datasource_id,),
                )
                return cur.fetchall()
            rows = cur.fetchall()
            for r in rows:
                r["aliases"] = _parse_json_field(r.get("aliases")) or []
                r["value_labels"] = _parse_json_field(r.get("value_labels")) or {}
            return rows
    finally:
        conn.close()


def _parse_json_field(v):
    """JSON 列可能以字符串返回(pymysql), 统一解析; 解析失败给 None。"""
    import json as _json
    if isinstance(v, (str, bytes)):
        try:
            return _json.loads(v)
        except (ValueError, TypeError):
            return None
    return v


# ── catalog 三段式 ──────────────────────────────────────────────

def _table_reference(table_row: dict, binding: Optional[ResolvedBinding] = None) -> str:
    """按 (catalog, db, table) 拼三段式;binding 优先。"""
    if binding and binding.catalog_ref:
        return binding.catalog_ref
    catalog = (table_row.get("catalog_name") or "adh").strip()
    db = (table_row.get("db_name") or "").strip()
    tbl = (table_row.get("table_name") or "").strip()
    parts = [p for p in (catalog, db, tbl) if p]
    return ".".join(parts)


# ── 主入口 ──────────────────────────────────────────────────────

def build_semantic_manifest(
    datasource_id: int,
    workspace_id: int = 0,
    table_names: Optional[list[str]] = None,
    user_id: int = 0,
    bindings: Optional[dict[str, ResolvedBinding]] = None,
    default_catalog: str = "adh",
) -> dict:
    """构建语义层版 MDL manifest。

    Args:
        datasource_id: 目标数据源
        workspace_id: 工作空间(RLS 过滤)
        table_names: 可选:仅编译这些表
        user_id: 用户属性注入 RLS
        bindings: {object_key or physical_table: ResolvedBinding}, 覆盖 tableReference
        default_catalog: 未指定 catalog 时的兜底(默认 'adh', 保持向后兼容)
    """
    # 复用旧 builder 的 RLS/列策略/关系/user attrs, 避免漂移
    from services.datamind.nl2sql.sql.manifest_builder import (
        _get_columns, _get_relations, _get_rls_policies, _get_column_policies,
        _get_user_attributes,
    )

    tables = _get_tables_with_catalog(datasource_id)
    columns = _get_columns(datasource_id)
    relations = _get_relations(datasource_id)
    rls_policies = _get_rls_policies(datasource_id, workspace_id)
    column_policies = _get_column_policies(datasource_id, workspace_id)
    metrics = _get_metrics(datasource_id)
    dimensions = _get_dimensions(datasource_id)
    user_attrs = _get_user_attributes(user_id, workspace_id) if user_id else {}

    if table_names:
        keep = set(table_names)
        tables = [t for t in tables if t["table_name"] in keep]
    active_tables = {t["table_name"] for t in tables}

    columns_by_table: dict[str, list[dict]] = {}
    for col in columns:
        if col["table_name"] in active_tables:
            columns_by_table.setdefault(col["table_name"], []).append(col)

    rls_by_table: dict[str, list[dict]] = {}
    for p in rls_policies:
        rls_by_table.setdefault(p["table_name"], []).append(p)

    col_pol_by_table: dict[str, dict] = {}
    for cp in column_policies:
        col_pol_by_table.setdefault(cp["table_name"], {})[cp["column_name"]] = {
            "access_type": cp["access_type"],
            "mask_pattern": cp.get("mask_pattern", ""),
        }

    measures_by_table: dict[str, list[dict]] = {}
    for m in metrics:
        tbl = m.get("target_table")
        if not tbl or tbl not in active_tables:
            continue
        formula = (m.get("formula_dsl") or m.get("formula") or "").strip()
        if not formula:
            continue
        agg = (m.get("default_agg") or m.get("agg_type") or "").strip().upper()
        measures_by_table.setdefault(tbl, []).append({
            "name": m["name"],
            "expression": formula,
            "aggType": agg,
            "unit": m.get("unit") or "",
            "certified": int(m.get("certified") or 0) == 1,
            "boundObject": m.get("bound_object_key") or "",
            "description": m.get("description") or "",
            "aliases": m.get("aliases") or [],
        })

    dims_by_table: dict[str, list[dict]] = {}
    for d in dimensions:
        tbl = d.get("target_table")
        if not tbl or tbl not in active_tables:
            continue
        dims_by_table.setdefault(tbl, []).append({
            "name": d["name"],
            "columnName": d.get("target_column") or "",
            "hierarchy": d.get("hierarchy") or "",
            "certified": int(d.get("certified") or 0) == 1,
            "boundObject": d.get("bound_object_key") or "",
            "description": d.get("description") or "",
            "aliases": d.get("aliases") or [],
            "valueLabels": d.get("value_labels") or {},
        })

    # relationships (与旧 builder 语义一致, 保留)
    relationships = []
    for rel in relations:
        s, t = rel["source_table"], rel["target_table"]
        if s not in active_tables or t not in active_tables:
            continue
        relationships.append({
            "name": rel.get("relation_name") or f"{s}_{t}",
            "models": [s, t],
            "joinType": _map_join_type(rel.get("relation_type", "many_to_one")),
            "condition": f"{s}.{rel['source_column']} = {t}.{rel['target_column']}",
        })

    models: list[dict[str, Any]] = []
    bindings = bindings or {}
    for table in tables:
        tbl = table["table_name"]
        binding = bindings.get(tbl) or next(
            (v for k, v in bindings.items() if v.physical_table == tbl), None
        )
        tbl_cols = columns_by_table.get(tbl, [])
        tbl_col_pol = col_pol_by_table.get(tbl, {})

        mdl_columns = []
        for col in tbl_cols:
            name = col["column_name"]
            pol = tbl_col_pol.get(name, {})
            hidden = pol.get("access_type") == "hidden"
            mdl_col = {
                "name": name,
                "type": _map_column_type(col.get("column_type", "")),
                "isCalculated": False,
                "notNull": col.get("is_primary_key", 0) == 1 or col.get("is_nullable", "YES") == "NO",
                "isHidden": hidden,
            }
            if pol.get("access_type") == "masked":
                mdl_col["masking"] = {"type": pol.get("mask_pattern", "partial")}
            mdl_columns.append(mdl_col)

        for rel in relationships:
            if tbl in rel["models"]:
                other = [m for m in rel["models"] if m != tbl][0]
                mdl_columns.append({
                    "name": other, "type": "object",
                    "relationship": rel["name"],
                    "isCalculated": False, "notNull": False, "isHidden": False,
                })

        rls_controls = []
        for policy in rls_by_table.get(tbl, []):
            session_props = _build_session_properties_for_rls([policy])
            cond = policy.get("row_filter", "") or ""
            if policy.get("filter_type") == "user_attribute" and policy.get("user_attribute"):
                key = policy["user_attribute"]
                cond = cond.replace(f":user_{key}", f"'{user_attrs.get(key, '')}'")
            if cond:
                rls_controls.append({
                    "name": policy.get("policy_name") or f"rls_{policy['id']}",
                    "requiredProperties": session_props,
                    "condition": cond,
                })

        model = {
            "name": tbl,
            "tableReference": _table_reference(table, binding),
            "columns": mdl_columns,
            "primaryKey": _find_primary_key(tbl_cols),
            "cached": False,
            "measures": measures_by_table.get(tbl, []),
            "dimensions": dims_by_table.get(tbl, []),
        }
        # 成本分级透传, 供 DataFusion 侧或审计参考
        if table.get("size_class") or table.get("query_mode"):
            model["costProfile"] = {
                "sizeClass": table.get("size_class"),
                "queryMode": table.get("query_mode"),
                "allowFullScan": int(table.get("allow_full_scan") if table.get("allow_full_scan") is not None else 1),
            }
        if rls_controls:
            model["rowLevelAccessControls"] = rls_controls
        models.append(model)

    manifest = {
        "catalog": default_catalog,
        "schema": "public",
        "models": models,
        "relationships": relationships,
        "views": [],
        "dataSource": _get_data_source_type(datasource_id),
    }
    logger.info(
        "[mdl_compiler] ds=%d models=%d rels=%d measures=%d dims=%d (catalog=%s)",
        datasource_id, len(models), len(relationships),
        sum(len(m.get("measures", [])) for m in models),
        sum(len(m.get("dimensions", [])) for m in models),
        default_catalog,
    )
    return manifest


def _get_data_source_type(datasource_id: int) -> str:
    from services.datamind.nl2sql.sql.manifest_builder import _get_data_source_type as _impl
    return _impl(datasource_id)
