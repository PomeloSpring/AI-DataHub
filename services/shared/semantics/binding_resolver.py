"""Binding resolver — 把语义对象解析到物理表(含 catalog / 护栏 / 权限)。

优先级:
  1. `adh_ontology_bindings` 结构化行 (最近 Phase 2 目标态)
  2. `adh_ontology_objects.execution_binding` 内联 JSON 快照 (便利视图)
  3. canonical model JSON 里的 `primary_table` (Phase 2 之前的过渡来源)
  4. 同名物理表兜底(object_key == table_name)

护栏/权限信息无论来自哪一级, 都会与 `adh_table_info` 的 size_class/query_mode/
allow_full_scan/est_rows + `adh_catalogs` 的 catalog_name 合成, 得到统一视图。

DRIFT 处理:binding.sync_state='drifted'/'orphaned' 不静默, 由调用方决定
是否阻断;本模块只在 warnings 里透传。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from services.shared.common.db.metadata_db import get_metadata_conn
from services.shared.semantics.models import (
    AccessControl, Guardrail, ResolvedBinding, normalize_object_ref,
)

logger = logging.getLogger(__name__)


# ── helpers ─────────────────────────────────────────────────────

def _as_dict(v: Any) -> dict[str, Any]:
    if isinstance(v, dict):
        return v
    if isinstance(v, (str, bytes)) and v:
        try:
            obj = json.loads(v)
            return obj if isinstance(obj, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _guardrail_from_row(row: dict[str, Any], fallback_mode: str = "materialized") -> Guardrail:
    size = row.get("size_class") or None
    mode = row.get("query_mode") or fallback_mode
    allow = bool(row.get("allow_full_scan") if row.get("allow_full_scan") is not None else True)
    max_rows = row.get("max_rows") or None
    timeout = row.get("timeout_sec") or None
    force_limit = (mode == "raw_source") or (size == "huge") or not allow
    return Guardrail(
        query_mode=mode, size_class=size, allow_full_scan=allow,
        max_rows=max_rows, timeout_sec=timeout, force_limit=force_limit,
    )


def _access_from_row(row: dict[str, Any]) -> AccessControl:
    toks = row.get("permission_tokens")
    if isinstance(toks, str):
        try:
            toks = json.loads(toks)
        except (ValueError, TypeError):
            toks = []
    masked = row.get("masked_columns")
    if isinstance(masked, str):
        try:
            masked = json.loads(masked)
        except (ValueError, TypeError):
            masked = []
    return AccessControl(
        permission_tokens=[str(x) for x in (toks or [])],
        rls_policy_refs=[row["rls_policy_ref"]] if row.get("rls_policy_ref") else [],
        masked_columns=[str(x) for x in (masked or [])],
    )


def _lookup_table_row(conn, datasource_id: int, table_name: str) -> Optional[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.id, t.table_name, d.database_name AS db_name, "
            "       t.catalog_id, t.size_class, t.est_rows, "
            "       t.query_mode, t.allow_full_scan, t.datasource_id, "
            "       c.catalog_name "
            "FROM adh_table_info t "
            "LEFT JOIN adh_catalogs c ON c.id = t.catalog_id "
            "LEFT JOIN adh_datasources d ON d.id = t.datasource_id "
            "WHERE t.datasource_id = %s AND t.table_name = %s AND t.is_active = 1",
            (datasource_id, table_name),
        )
        return cur.fetchone()


# ── 数据源身份(name 为全局唯一标识, id 可能因删除重建而变) ────────────

def _ds_name_by_id(conn, datasource_id: int) -> str:
    """当前 datasource_id 对应的数据源名(查不到返回 '')。"""
    if not datasource_id:
        return ""
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM adh_datasources WHERE id = %s LIMIT 1", (datasource_id,))
        row = cur.fetchone()
    return (row or {}).get("name") or ""


def _ds_id_by_name(conn, name: str) -> Optional[int]:
    """按全局唯一名映射到"当前有效"的 datasource id(同名多条时取最新=删除重建后新增的)。

    这是"删除重建后仍可关联"的关键: binding 存的是 name, 解析时换成实时 id。
    """
    if not name:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM adh_datasources WHERE name = %s ORDER BY id DESC LIMIT 1",
                (name,),
            )
            row = cur.fetchone()
        return (row or {}).get("id") if row else None
    except Exception as e:  # noqa: BLE001 — 名查不到不阻断, 回落存内 id
        logger.debug("[binding] datasource name->id lookup failed for '%s': %s", name, e)
        return None


def _ds_db_type(conn, datasource_id: int) -> str:
    """数据源的 db_type(方言/协议族); 查不到时保守回落 'mysql'(旧行为)。"""
    if not datasource_id:
        return "mysql"
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT db_type FROM adh_datasources WHERE id = %s LIMIT 1",
                (datasource_id,),
            )
            row = cur.fetchone()
        return ((row or {}).get("db_type") or "mysql").lower()
    except Exception as e:  # noqa: BLE001 — 不阻断解析, 方言回落由 planner 再校验
        logger.debug("[binding] datasource db_type lookup failed for id=%s: %s", datasource_id, e)
        return "mysql"


# ── 主入口 ──────────────────────────────────────────────────────

def resolve_binding(
    object_ref: str,
    datasource_id: int = 0,
    model_id: Optional[int] = None,
    datasource_name: str = "",
) -> tuple[ResolvedBinding | None, list[str]]:
    """把对象 (IRI 或 object_key) 解析到 ResolvedBinding。

    datasource 以 **name 为全局唯一标识** 参与匹配: 传入 datasource_id 时会先反查其 name,
    再用 name 去命中 binding(从而数据源删除重建、id 变更后仍可关联)。

    返回 (binding, warnings)。找不到对象时 binding=None, warnings 说明原因。
    """
    object_key = normalize_object_ref(object_ref)
    if not object_key:
        return None, ["empty object reference"]

    conn = get_metadata_conn()
    warnings: list[str] = []
    try:
        # name 优先: 未显式传入则由当前(可能为新) datasource_id 反查, 作为稳定关联键
        if not datasource_name and datasource_id:
            datasource_name = _ds_name_by_id(conn, datasource_id)

        binding = _resolve_from_bindings(
            conn, object_key, datasource_id, datasource_name, model_id, warnings)
        if binding is None:
            binding = _resolve_from_objects_inline(conn, object_key, datasource_id, datasource_name, warnings)
        if binding is None:
            binding = _resolve_from_canonical(conn, object_key, datasource_id, warnings)
        if binding is None:
            return None, warnings or [f"no binding found for object '{object_key}'"]

        # 统一用 adh_table_info 补齐 catalog / guardrail (若未显式设置)
        _enrich_from_table_info(conn, binding, warnings)
        if binding.sync_state in ("drifted", "orphaned"):
            warnings.append(f"binding sync_state={binding.sync_state}")
        return binding, warnings
    finally:
        conn.close()


# ── Level 1: adh_ontology_bindings ─────────────────────────────

def _resolve_from_bindings(conn, object_key, datasource_id, datasource_name,
                           model_id, warnings) -> ResolvedBinding | None:
    sql = (
        "SELECT * FROM adh_ontology_bindings "
        "WHERE object_key = %s AND status = 'active'"
    )
    params: list[Any] = [object_key]
    # 作用域: 按 id 命中 + 按 name 命中(支持删除重建后按名重关联) + 通配 datasource_id=0
    #   注意: 占位符顺序必须与 params 追加顺序严格一致。
    scope_parts: list[str] = []
    if datasource_id:
        scope_parts.append("datasource_id = %s")
        params.append(datasource_id)
    if datasource_name:
        scope_parts.append("datasource_name = %s")
        params.append(datasource_name)
    scope_parts.append("datasource_id = 0")
    sql += " AND (" + " OR ".join(scope_parts) + ")"
    if model_id is not None:
        sql += " AND model_id = %s"
        params.append(model_id)
    # 排序: name 命中优先, 其次 id 命中, 最后通配
    sql += " ORDER BY (datasource_name = %s) DESC, (datasource_id = %s) DESC, id ASC LIMIT 1"
    params.append(datasource_name or "")
    params.append(datasource_id or 0)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if not row:
        return None
    column_map = _as_dict(row.get("column_map"))
    stored_name = row.get("datasource_name") or datasource_name or ""
    # name 为关联键: 映射到当前有效 id(删除重建后 id 已变), 查不到再回落存内 id
    live_id = _ds_id_by_name(conn, stored_name) if stored_name else None
    ds_id_out = int(live_id or row["datasource_id"])
    return ResolvedBinding(
        object_key=row["object_key"],
        model_id=row.get("model_id"),
        datasource_id=ds_id_out,
        datasource_name=stored_name,
        catalog_name="",  # 由 _enrich 补
        db_name="",
        physical_table=row.get("physical_table") or "",
        catalog_ref=row.get("catalog_ref") or "",
        bind_kind=row.get("bind_kind") or "primary",
        template_ref=row.get("template_ref") or "",
        db_type=_ds_db_type(conn, ds_id_out),
        join_expr=row.get("join_expr") or "",
        column_map={str(k): str(v) for k, v in column_map.items()},
        guardrail=_guardrail_from_row(row),
        access=_access_from_row(row),
        sync_state=row.get("sync_state") or "bound",
        source="adh_ontology_bindings",
    )


# ── Level 2: adh_ontology_objects.execution_binding ────────────

def _resolve_from_objects_inline(conn, object_key, datasource_id, datasource_name, warnings) -> ResolvedBinding | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT model_id, datasource_id, display_name, execution_binding "
            "FROM adh_ontology_objects "
            "WHERE object_key = %s AND is_active = 1 "
            "  AND (%s = 0 OR datasource_id = %s) "
            "ORDER BY (datasource_id = %s) DESC, id ASC LIMIT 1",
            (object_key, datasource_id, datasource_id, datasource_id),
        )
        row = cur.fetchone()
    if not row or not row.get("execution_binding"):
        return None
    eb = _as_dict(row["execution_binding"])
    bind_kind = eb.get("bind_kind") or "primary"
    table = eb.get("physical_table") or eb.get("table") or ""
    if not table and bind_kind != "sql_template":
        warnings.append("execution_binding missing physical_table")
        return None
    # 内联快照也按 name 重关联: eb 新格式含 datasource_name, 旧行回落传入 name
    eb_name = eb.get("datasource_name") or datasource_name or ""
    live_id = _ds_id_by_name(conn, eb_name) if eb_name else None
    ds_id_out = int(live_id or eb.get("datasource_id") or row["datasource_id"])
    return ResolvedBinding(
        object_key=object_key,
        model_id=row.get("model_id"),
        datasource_id=ds_id_out,
        datasource_name=eb_name,
        catalog_name=eb.get("catalog", ""),
        db_name=eb.get("db", ""),
        physical_table=table,
        catalog_ref=eb.get("catalog_ref", ""),
        bind_kind=bind_kind,
        template_ref=eb.get("template_ref") or "",
        db_type=_ds_db_type(conn, ds_id_out),
        join_expr=eb.get("join_expr", ""),
        column_map={str(k): str(v) for k, v in (eb.get("column_map") or {}).items()},
        guardrail=_guardrail_from_row(eb),
        access=_access_from_row(eb),
        sync_state="bound",
        source="adh_ontology_objects.execution_binding",
    )


# ── Level 3: canonical model JSON (objects[i].primary_table) ───

def _resolve_from_canonical(conn, object_key, datasource_id, warnings) -> ResolvedBinding | None:
    """Phase 2 之前的过渡:Palantir 导入器已把 objects[].primary_table 写进
    `adh_ontology_models.json_content`; 从 canonical JSON 回捞一次即能让 traversal
    与语义层查询今天就能跑通。

    不依赖 `adh_ontology_objects`(导入器当前不展开到行级, Phase 2 会补上)。
    """
    sql = (
        "SELECT id, datasource_id, json_content FROM adh_ontology_models "
        "WHERE status = 'active'"
    )
    params: list[Any] = []
    if datasource_id:
        sql += " AND (datasource_id = %s OR datasource_id = 0)"
        params.append(datasource_id)
    sql += " ORDER BY (datasource_id = %s) DESC, id DESC"
    params.append(datasource_id)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    for m in rows:
        payload = _as_dict(m.get("json_content"))
        objs = payload.get("objects") or []
        match = next(
            (o for o in objs if (o.get("key") or o.get("name")) == object_key), None,
        )
        if not match:
            continue
        table = match.get("primary_table") or ""
        if not table:
            return None
        warnings.append("resolved via canonical model JSON (no explicit binding row)")
        m_name = _ds_name_by_id(conn, m["datasource_id"])
        live_id = _ds_id_by_name(conn, m_name) if m_name else None
        ds_id_out = int(live_id or m["datasource_id"])
        return ResolvedBinding(
            object_key=object_key,
            model_id=m["id"],
            datasource_id=ds_id_out,
            datasource_name=m_name,
            physical_table=table,
            bind_kind="primary",
            db_type=_ds_db_type(conn, ds_id_out),
            source="adh_ontology_objects.execution_binding",
            sync_state="unbound",
        )
    return None


# ── 补齐 catalog / guardrail from adh_table_info ────────────────

def _enrich_from_table_info(conn, b: ResolvedBinding, warnings: list[str]) -> None:
    if not b.physical_table:
        return
    row = _lookup_table_row(conn, b.datasource_id, b.physical_table)
    if not row:
        warnings.append(f"physical_table '{b.physical_table}' not in adh_table_info")
        return
    b.db_name = b.db_name or (row.get("db_name") or "")
    b.catalog_name = b.catalog_name or (row.get("catalog_name") or "adh")
    if not b.catalog_ref:
        parts = [p for p in (b.catalog_name, b.db_name, b.physical_table) if p]
        b.catalog_ref = ".".join(parts)
    # guardrail: 只有 binding 里没显式指定时, 才回落到表级默认
    tbl_size = row.get("size_class")
    tbl_mode = row.get("query_mode")
    tbl_allow = bool(row.get("allow_full_scan") if row.get("allow_full_scan") is not None else 1)
    g = b.guardrail
    if g.size_class is None and tbl_size:
        g.size_class = tbl_size
    if g.query_mode == "materialized" and tbl_mode:
        g.query_mode = tbl_mode
    if g.allow_full_scan and not tbl_allow:
        g.allow_full_scan = False
    g.force_limit = (g.query_mode == "raw_source") or (g.size_class == "huge") or not g.allow_full_scan
