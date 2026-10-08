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
import re
from typing import Any, Optional

from backend.common.db.metadata_db import get_metadata_conn
from backend.semantics.models import (
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

        # 别名归一: 历史写法/别名 → canonical key（唯一命中才改写）
        object_key = _resolve_key_by_alias(conn, object_key, datasource_id, warnings)

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
        # 标注解析档位 + 同名兜底可观测（P2.5）
        binding.resolution_source = _resolution_source_of(binding)
        if _norm_ref(object_key) == _norm_ref(binding.physical_table or ""):
            binding.resolution_source = "table_fallback"
            warnings.append(
                f"对象 '{object_key}' 经同名兜底解析到表 '{binding.physical_table}'"
                f"（未登记为数据产品前属高风险绑定）")
        # 补数据产品身份（L1）：不改变解析结果，只带上治理身份
        _enrich_from_data_product(conn, binding, warnings)
        if binding.sync_state in ("drifted", "orphaned"):
            warnings.append(f"binding sync_state={binding.sync_state}")
        return binding, warnings
    finally:
        conn.close()


# ── 别名归一（历史写法 → canonical key）─────────────────────

def _norm_ref(s) -> str:
    """判定"是否同一个引用"时的归一：忽略大小写与分隔符。"""
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _resolution_source_of(binding: ResolvedBinding) -> str:
    """把内部 source 映射为对外解析档位（供 eval 分桶归因）。"""
    return {
        "adh_ontology_bindings": "bindings",
        "adh_ontology_objects.execution_binding": "execution_binding",
        "adh_table_info": "canonical",
    }.get(str(binding.source or ""), "canonical")


def _enrich_from_data_product(conn, binding: ResolvedBinding, warnings: list) -> None:
    """补数据产品身份（L1）：product_ref / product_status。

    数据产品是「可被本体引用的表」的治理身份（谁产出 / 契约 / 版本 / 责任）。
    这里**不改变解析结果**（仍按 physical_table 执行），只把身份带上。

    查不到产品 = 该表未登记为托管资产；这是 P2.3 activate 门禁的依据，
    此处只记 warning 不阻断（阻断在 activate 侧，避免影响存量取数）。
    """
    if not binding.physical_table:
        return
    # 不再因 product_ref 已持久化而短路：product_class/site 不在绑定行里，
    # 必须从产品表补（产品表是身份唯一事实源）。有索引，开销可接受。
    try:
        with conn.cursor() as cur:
            ds = str(binding.datasource_name or "").strip()
            if ds:
                cur.execute(
                    "SELECT product_name, status, product_class, site FROM adh_data_products "
                    "WHERE datasource_name = %s AND physical_table = %s "
                    "ORDER BY id DESC LIMIT 1",
                    (ds, binding.physical_table))
            else:
                # 数据源名为空（如 AS-BOT 系统本体 datasource_id=0）时回落只按表名。
                # 口径必须与 data_product_service.find_by_table 一致（见
                # tests/test_ontology_product_binding.py 的一致性断言），不得另造判断。
                cur.execute(
                    "SELECT product_name, status, product_class, site FROM adh_data_products "
                    "WHERE physical_table = %s ORDER BY id DESC LIMIT 1",
                    (binding.physical_table,))
            row = cur.fetchone()
    except Exception as e:  # noqa: BLE001 — 查不到不阻断解析主链路
        logger.debug("[binding] data product lookup skipped: %s", e)
        return
    if row:
        # 已持久化的 product_ref 不被覆盖（绑定行是已认领的稳定身份）；
        # 但 status 是动态的，总是刷新
        if not getattr(binding, "product_ref", ""):
            binding.product_ref = str(row.get("product_name") or "")
        binding.product_status = str(row.get("status") or "")
        # 产品类与站点：同构多站点下，本体绑产品类、按站点路由到物理实例
        if not getattr(binding, "product_class", ""):
            binding.product_class = str(row.get("product_class") or "")
        if not getattr(binding, "site", ""):
            binding.site = str(row.get("site") or "")
    else:
        # 只有本来就没产品身份时才报“未登记”；已持久化 product_ref 的（登记过、
        # 可能产品行暂不可达）不误报，否则会让已认领的绑定平白多一条告警。
        if not getattr(binding, "product_ref", ""):
            warnings.append(
                f"表 '{binding.datasource_name}.{binding.physical_table}' 未登记为数据产品"
                f"（本体只绑数据产品，激活时会被门禁阻断）")


def _resolve_key_by_alias(conn, object_key: str, datasource_id: int,
                          warnings: list) -> str:
    """把 object_ref 里的别名/历史写法归一到 canonical 对象 key。

    背景：合并重复对象后，历史写法（`casefile` / `CaseFile`）不再是 object_key，
    但仍作为别名留在 `case_file.aliases` 里。若不在解析前归一，检索/LLM 传旧写法
    会直接 "no binding found"，看起来像能力倒退。

    口径（宁缺勿错，ontology-modeling §7）：
    - **唯一命中**才改写；
    - 同一别名命中多个对象 → 不归一，把候选写进 warnings 由调用方确认；
    - 无命中 → 原样返回，交给后续三级解析。
    对象 key 本身也会参与比对，所以传 canonical key 时归一为无操作。
    """
    want = str(object_key or "").strip().lower()
    if not want:
        return object_key
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT object_key, aliases FROM adh_ontology_objects "
                "WHERE is_active = 1 AND (datasource_id = %s OR datasource_id = 0)",
                (datasource_id or 0,),
            )
            rows = cur.fetchall() or []
    except Exception as e:  # noqa: BLE001 — 归一失败不阻断解析主链路
        logger.debug("[binding] alias resolve skipped: %s", e)
        return object_key

    hits: list[str] = []
    for r in rows:
        candidates = [str(r.get("object_key") or "")]
        candidates += [a for a in str(r.get("aliases") or "").split(",")]
        if want in {c.strip().lower() for c in candidates if c.strip()}:
            hits.append(str(r.get("object_key") or ""))

    uniq = sorted(set(h for h in hits if h))
    if len(uniq) == 1:
        if uniq[0] != object_key:
            logger.debug("[binding] object ref %r 归一到 %r（唯一别名命中）", object_key, uniq[0])
        return uniq[0]
    if len(uniq) > 1:
        # 歧义不猜：回抛候选，让 LLM/调用方确认后重试
        warnings.append(
            f"'{object_key}' 命中多个对象的别名 {uniq}，未自动归一，请改传对象 key")
    return object_key


# ── Level 1: adh_ontology_bindings ─────────────────────────────

def _resolve_from_bindings(conn, object_key, datasource_id, datasource_name,
                           model_id, warnings) -> ResolvedBinding | None:
    sql = (
        "SELECT b.* FROM adh_ontology_bindings b "
        "JOIN adh_ontology_models m ON m.id = b.model_id "
        "WHERE b.object_key = %s AND b.status = 'active'"
    )
    params: list[Any] = [object_key]
    # ── 归属裁决（语义取数真源）：取数绑定只认与查询域同类的模型 ──
    #   datasource_id>0（业务源取数）→ kind='source'（源本体，语义检索真源）
    #   datasource_id<=0（系统域）  → kind='system'
    #   kind='business' 的绑定**永不参与取数**：业务本体是 RAG 知识层（与系统本体
    #   共用知识库），其绑定行只承载对象→产品治理血缘（analyze_impact 反查用）。
    #   历史缺陷：业务本体绑定与源本体绑定重复占用同一 object_key，解析靠 id ASC
    #   意外让源本体赢——真源归属必须显式声明，不靠先来后到。
    #   空 kind 的存量模型行按旧口径（ds>0=源域）兼容。
    sql += (" AND ((%s > 0 AND (m.kind = 'source' OR COALESCE(m.kind,'') = '')) "
            "OR (%s <= 0 AND (m.kind = 'system' OR COALESCE(m.kind,'') = '')))")
    params.append(datasource_id or 0)
    params.append(datasource_id or 0)
    # 作用域: 按 id 命中 + 按 name 命中(支持删除重建后按名重关联) + 通配 datasource_id=0
    #   注意: 占位符顺序必须与 params 追加顺序严格一致。
    scope_parts: list[str] = []
    if datasource_id:
        scope_parts.append("b.datasource_id = %s")
        params.append(datasource_id)
    if datasource_name:
        scope_parts.append("b.datasource_name = %s")
        params.append(datasource_name)
    scope_parts.append("b.datasource_id = 0")
    sql += " AND (" + " OR ".join(scope_parts) + ")"
    if model_id is not None:
        sql += " AND b.model_id = %s"
        params.append(model_id)
    # 排序: name 命中优先, 其次 id 命中, 最后通配（不 LIMIT 1：多站点需先看全量再裁决）
    sql += " ORDER BY (b.datasource_name = %s) DESC, (b.datasource_id = %s) DESC, b.id ASC"
    params.append(datasource_name or "")
    params.append(datasource_id or 0)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    if not rows:
        return None

    # ── 多站点路由：同构多站点下，一个对象有 N 条绑定（每站点一条）──
    # 站点选择在 **Python 端显式做**，不依赖 SQL ORDER BY 排序（隐式依赖排序很脆弱，
    # fake 一测就露馅）。
    # 指定站点 → 精确选 name 命中；未指定且多站点 → 歧义回抛候选（宁缺勿错）：
    # 静默取任意一条会让“查上海站数据却返回北京站”这种错数发生。
    def _pick(cands):
        if datasource_name:
            for r in cands:
                if str(r.get("datasource_name") or "") == datasource_name:
                    return r
        if datasource_id:
            for r in cands:
                if int(r.get("datasource_id") or 0) == int(datasource_id):
                    return r
        return cands[0]

    if datasource_name or datasource_id:
        row = _pick(rows)
    else:
        sites: list[str] = []
        for r in rows:
            s = str(r.get("datasource_name") or "").strip()
            if s and s not in sites:
                sites.append(s)
        if len(sites) > 1:
            warnings.append(
                f"对象 '{object_key}' 在多个站点有绑定 {sites}，未指定站点无法确定取数范围；"
                f"请指定站点后重试")
            return None
        row = rows[0]
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
        # 产品身份优先取绑定行（已持久化，见 data_product_binding_migration）；
        # 空时由 _enrich_from_data_product 回落到 adh_data_products 查询
        product_ref=str(row.get("product_ref") or ""),
        sync_state=row.get("sync_state") or "bound",
        source="adh_ontology_bindings",
    )


# ── Level 2: adh_ontology_objects.execution_binding ────────────

def _resolve_from_objects_inline(conn, object_key, datasource_id, datasource_name, warnings) -> ResolvedBinding | None:
    with conn.cursor() as cur:
        # JOIN models 按 kind 裁决（与 Level 1 同口径）：取数只认 source/system 模型的
        # 展开行，business 本体行只承载治理血缘不参与取数（否则多模型重复行会靠 id 先后意外命中）
        cur.execute(
            "SELECT o.model_id, o.datasource_id, o.display_name, o.execution_binding "
            "FROM adh_ontology_objects o "
            "JOIN adh_ontology_models m ON m.id = o.model_id "
            "WHERE o.object_key = %s AND o.is_active = 1 "
            "  AND (%s = 0 OR o.datasource_id = %s) "
            "  AND ((%s > 0 AND (m.kind = 'source' OR COALESCE(m.kind,'') = '')) "
            "       OR (%s <= 0 AND (m.kind = 'system' OR COALESCE(m.kind,'') = ''))) "
            "ORDER BY (o.datasource_id = %s) DESC, o.id ASC LIMIT 1",
            (object_key, datasource_id, datasource_id, datasource_id, datasource_id, datasource_id),
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
