"""站点作用域服务 — 多站点部署拓扑的唯一读取口（L1，common ← core ← modules）。

三层事实源（迁移 docker/mysql/site_scope_migration.sql）：
  adh_sites                        站点清单（含聚合态「全球」）
  adh_datasources.site_code        数据源归属站点
  adh_ontology_models.deploy_sites 业务本体整体适用站点 + 每站点路由到哪个数据源

设计边界：
- **可选站点是派生视图**，不是第二套授权裁决：可查站点 = 角色已授权数据源
  （role_service.get_user_allowed_datasources）携带的 site_code 集合
  （见 waker-datasource-domain.md §1，空授权 fail-closed 返回空）。
- 站点标识对 LLM 是**业务名/代码**（"中国"/"cn"），数据源 id 仍是黑盒
  （护栏 §7）：本模块的解析只在调用方传入的授权集内做 name↔id 映射。
- 错误消息只含业务名（站点名 + 数据源名），不得出现 id/host/账号（SiteError 是
  ResourceScopeError 的同级可外显错误）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Optional

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)

# 聚合态站点：不对应单一数据源（全球 = 逐站各查一次后汇总）
AGGREGATE_SITE = "global"


class SiteError(ValueError):
    """可直接提示用户的站点解析错误；只含业务名，不含物理标识。"""


# ── 站点清单 ──────────────────────────────────────────────────────────

def list_sites(only_active: bool = True, include_aggregate: bool = True) -> list[dict]:
    """站点清单（按 sort_order）。include_aggregate=False 时剔除聚合态。"""
    sql = ("SELECT site_code, display_name, is_aggregate, sort_order, is_active "
           "FROM adh_sites")
    conds = []
    if only_active:
        conds.append("is_active = 1")
    if not include_aggregate:
        conds.append("is_aggregate = 0")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY sort_order ASC, site_code ASC"
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall() or []
    finally:
        conn.close()
    return [
        {"site_code": str(r["site_code"]), "display_name": str(r["display_name"]),
         "is_aggregate": int(r.get("is_aggregate") or 0),
         "sort_order": int(r.get("sort_order") or 0),
         "is_active": int(r.get("is_active") or 0)}
        for r in rows
    ]


def get_site(site_code: str) -> Optional[dict]:
    """按 code 取站点（含停用行，供管理页回显）。"""
    code = str(site_code or "").strip()
    if not code:
        return None
    for s in list_sites(only_active=False):
        if s["site_code"] == code:
            return s
    return None


def _alias_index() -> dict[str, str]:
    """站点别名 → site_code：code、业务名、去/补「站点|站」后缀、括号内别名（新加坡）。"""
    idx: dict[str, str] = {}
    for s in list_sites(only_active=False):
        code, dn = s["site_code"], s["display_name"]
        names = {code, dn}
        names.update(re.findall(r"[(（]([^()（）]+)[)）]", dn))
        base = re.sub(r"[(（][^()（）]*[)）]", "", dn).strip()
        if base:
            names.add(base)
            # 业务名与口语写法双向对齐：中国 / 中国站 / 中国站点 都合法
            core = re.sub(r"(站点|站)$", "", base)
            for variant in (core, core + "站", core + "站点"):
                if variant:
                    names.add(variant)
        for n in names:
            key = str(n or "").strip().lower()
            if key:
                idx.setdefault(key, code)
    return idx


def normalize_site_ref(ref: Any) -> str:
    """站点写法归一为 site_code（不识别返回空串，由调用方决定如何报错）。"""
    key = str(ref or "").strip().lower()
    if not key:
        return ""
    return _alias_index().get(key, "")


def validate_site_code(ref: Any, allow_aggregate: bool = False) -> str:
    """校验并归一站点标识（数据源归属用：不允许聚合态）。不合法抛 SiteError。"""
    code = normalize_site_ref(ref)
    if not code:
        raise SiteError(f"未知站点「{ref}」；可选站点："
                        f"{'、'.join(s['display_name'] for s in list_sites(include_aggregate=allow_aggregate)) or '（站点清单为空，请先在系统管理-站点维护）'}")
    site = get_site(code) or {}
    if not int(site.get("is_active") or 0):
        raise SiteError(f"站点「{site.get('display_name') or code}」已停用，不可选用")
    if int(site.get("is_aggregate") or 0) and not allow_aggregate:
        raise SiteError(f"「{site.get('display_name') or code}」是聚合态站点，"
                        f"数据源只能归属具体站点")
    return code


# ── 站点 CRUD（管理页）────────────────────────────────────────────────

def upsert_site(site_code: str, display_name: str, *, is_aggregate: int = 0,
                sort_order: int = 0, is_active: int = 1) -> dict:
    """新增/更新站点（幂等 upsert；code 一经使用不得改名，避免拓扑漂移）。"""
    code = str(site_code or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", code):
        raise ValueError("站点代码只允许小写字母/数字/-/_，长度 1~32")
    name = str(display_name or "").strip()
    if not name:
        raise ValueError("站点名称不能为空")
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_sites (site_code, display_name, is_aggregate, sort_order, is_active) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE display_name = VALUES(display_name), "
                "is_aggregate = VALUES(is_aggregate), sort_order = VALUES(sort_order), "
                "is_active = VALUES(is_active)",
                (code, name, int(bool(is_aggregate)), int(sort_order or 0),
                 int(bool(is_active))))
        conn.commit()
    finally:
        conn.close()
    invalidate_site_cache()
    return get_site(code) or {}


def delete_site(site_code: str) -> dict:
    """删除站点：被数据源或本体引用时拒绝（fail-loud，不级联删拓扑）。"""
    code = str(site_code or "").strip()
    if not code or not get_site(code):
        raise ValueError("站点不存在")
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM adh_datasources WHERE site_code = %s ORDER BY name",
                        (code,))
            used_by = [str(r["name"]) for r in (cur.fetchall() or [])]
            cur.execute(
                "SELECT id, name FROM adh_ontology_models "
                "WHERE deploy_sites IS NOT NULL AND JSON_SEARCH("
                "deploy_sites, 'one', %s, NULL, '$[*].site_code') IS NOT NULL")
            models = [str(r["name"]) for r in (cur.fetchall() or [])]
    finally:
        conn.close()
    if used_by or models:
        parts = []
        if used_by:
            parts.append(f"数据源 {', '.join(used_by)}")
        if models:
            parts.append(f"业务本体 {', '.join(models)}")
        raise ValueError("该站点仍被引用，无法删除：" + "；".join(parts))
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_sites WHERE site_code = %s", (code,))
        conn.commit()
    finally:
        conn.close()
    invalidate_site_cache()
    return {"success": True}


def invalidate_site_cache() -> None:
    """站点/数据源拓扑变更后失效派生缓存（缓存不可用不影响正确性）。"""
    try:
        from backend.common.ttl_cache import datasource_cache
        datasource_cache.invalidate_prefix("sites:")
    except Exception as e:  # noqa: BLE001 — 缓存层故障不阻断写路径
        logger.debug("[site] cache invalidate skipped: %s", e)


# ── 数据源 ↔ 站点 ─────────────────────────────────────────────────────

def datasource_sites(datasource_ids: Iterable[int] | None = None) -> dict[int, str]:
    """datasource_id → site_code（只回有站点的源）。"""
    ids = [int(i) for i in (datasource_ids or []) if str(i).strip() != ""]
    sql = "SELECT id, site_code FROM adh_datasources WHERE COALESCE(site_code, '') <> ''"
    params: tuple = ()
    if ids:
        sql += f" AND id IN ({','.join(['%s'] * len(ids))})"
        params = tuple(ids)
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() or []
    finally:
        conn.close()
    return {int(r["id"]): str(r["site_code"]) for r in rows}


def datasources_of_site(site_code: str, allowed_ids: Iterable[int] | None = None) -> list[dict]:
    """站点内的数据源候选（业务名 + id）；限定在授权集内时只回交集。"""
    code = str(site_code or "").strip()
    if not code:
        return []
    ids = [int(i) for i in (allowed_ids or []) if str(i).strip() != ""]
    sql = "SELECT id, name, db_type FROM adh_datasources WHERE site_code = %s"
    params: list[Any] = [code]
    if ids:
        sql += f" AND id IN ({','.join(['%s'] * len(ids))})"
        params += ids
    sql += " ORDER BY name"
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall() or []
    finally:
        conn.close()
    return [{"id": int(r["id"]), "name": str(r["name"]), "db_type": str(r.get("db_type") or "")}
            for r in rows]


# ── 业务本体适用站点 ──────────────────────────────────────────────────

def model_deploy_sites(model_id: int) -> list[dict]:
    """模型级适用站点：[{site_code, datasource(业务名)}]；未配置返回空列表。"""
    mid = int(model_id or 0)
    if not mid:
        return []
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT deploy_sites FROM adh_ontology_models WHERE id = %s", (mid,))
            row = cur.fetchone()
    finally:
        conn.close()
    raw = (row or {}).get("deploy_sites") if row else None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except (ValueError, TypeError):
            logger.warning("[site] 模型 %s deploy_sites 非法 JSON，按未配置处理", mid)
            return []
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = str(item.get("site_code") or "").strip()
        ds = str(item.get("datasource") or "").strip()
        if code:
            out.append({"site_code": code, "datasource": ds})
    return out


def set_model_deploy_sites(model_id: int, sites: list[dict]) -> None:
    """写模型级适用站点（调用方负责校验；空列表 = 未配置，落 NULL 回落逐对象绑定行）。"""
    payload = json.dumps([{"site_code": str(s.get("site_code") or ""),
                           "datasource": str(s.get("datasource") or "")}
                          for s in sites or [] if (s or {}).get("site_code")],
                         ensure_ascii=False)
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE adh_ontology_models SET deploy_sites = %s WHERE id = %s",
                        (payload if payload != "[]" else None, int(model_id or 0)))
        conn.commit()
    finally:
        conn.close()


def site_of_model_target(model_id: int, site_code: str) -> str:
    """模型在该站点声明的路由目标数据源业务名（未声明返回空）。"""
    for entry in model_deploy_sites(model_id):
        if entry["site_code"] == site_code:
            return entry["datasource"]
    return ""


# ── 可查站点（角色授权派生）───────────────────────────────────────────

def authorized_sites(user_id: int, workspace_id: int = 0) -> list[dict]:
    """用户可查站点 = 角色已授权数据源携带的 site_code 集合（派生，非第二套裁决）。

    聚合态（全球）仅在可查站点数 ≥2 时提供——单站用户看到「全球」是空承诺。
    空角色授权 fail-closed 返回空列表（不解释为全量）。
    """
    from backend.core.role_service import role_service

    allowed = sorted(set(role_service.get_user_allowed_datasources(
        int(user_id or 0), int(workspace_id or 0))))
    if not allowed:
        return []
    marks = ",".join(["%s"] * len(allowed))
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, name, db_type, site_code FROM adh_datasources "
                f"WHERE id IN ({marks}) AND COALESCE(site_code, '') <> '' ORDER BY name",
                tuple(allowed))
            rows = cur.fetchall() or []
    finally:
        conn.close()
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(str(r["site_code"]), []).append(
            {"id": int(r["id"]), "name": str(r["name"]), "db_type": str(r.get("db_type") or "")})
    concrete = [
        {"site_code": s["site_code"], "display_name": s["display_name"],
         "is_aggregate": 0, "sort_order": s["sort_order"],
         "datasources": grouped.get(s["site_code"], [])}
        for s in list_sites() if s["site_code"] in grouped and not s["is_aggregate"]
    ]
    out = [
        {"site_code": s["site_code"], "display_name": s["display_name"],
         "is_aggregate": 1, "sort_order": s["sort_order"], "datasources": []}
        for s in list_sites()
        if s["is_aggregate"] and len(concrete) >= 2
    ]
    return sorted(out + concrete, key=lambda x: (x["sort_order"], x["site_code"]))


# ── 站点 → 生效数据源（服务端解析，LLM 只见业务名）────────────────────

def resolve_site_to_source(site_ref: Any, allowed_ids: Iterable[int],
                           model_id: int = 0) -> int:
    """站点业务名/代码 → 生效 datasource_id（只在角色授权集内解析）。

    唯一命中直接返回；站点内多源时用模型 deploy_sites 的路由目标消歧（业务本体
    取数的权威口径）；仍多义/零命中/聚合态 → 抛 SiteError（含业务名候选，不猜）。
    """
    ids = sorted({int(i) for i in (allowed_ids or []) if str(i).strip() != ""})
    code = normalize_site_ref(site_ref)
    site = get_site(code) if code else None
    if site is None:
        known = "、".join(s["display_name"] for s in list_sites(include_aggregate=False))
        raise SiteError(
            f"未识别站点「{site_ref}」；可选站点：{known or '（站点清单为空，请先在系统管理-站点维护）'}。"
            f"不确定就问用户，不要猜站点名。")
    if int(site.get("is_aggregate") or 0):
        raise SiteError(
            f"「{site['display_name']}」是聚合态站点，不对应单一数据源："
            f"请对每个具体站点各调一次查询（site=中国/国际/…）后汇总，"
            f"合计需标注「各站合计≠去重全球数」。")
    candidates = datasources_of_site(code, ids)
    if not candidates:
        available = "、".join(s["display_name"] for s in authorized_sites_from_ids(ids))
        raise SiteError(
            f"当前身份没有「{site['display_name']}」对应的已授权数据源；"
            f"当前可查站点：{available or '无'}。请如实告知用户，不要换站重试。")
    if len(candidates) == 1:
        return int(candidates[0]["id"])
    target = site_of_model_target(model_id, code) if model_id else ""
    if target:
        for c in candidates:
            if c["name"] == target:
                return int(c["id"])
        raise SiteError(
            f"业务本体在「{site['display_name']}」配置的数据源「{target}」不在当前授权范围内；"
            f"该站点已授权候选：{'、'.join(c['name'] for c in candidates)}。")
    names = "、".join(c["name"] for c in candidates)
    raise SiteError(
        f"「{site['display_name']}」有多个已授权数据源（{names}），无法确定取哪个；"
        f"请用 datasource 指定其一，或询问用户。")


def authorized_sites_from_ids(allowed_ids: Iterable[int]) -> list[dict]:
    """按授权源 id 集派生可查站点（不查角色，供已持有授权集的调用方复用）。"""
    ids = sorted({int(i) for i in (allowed_ids or []) if str(i).strip() != ""})
    if not ids:
        return []
    marks = ",".join(["%s"] * len(ids))
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT name, site_code FROM adh_datasources "
                f"WHERE id IN ({marks}) AND COALESCE(site_code, '') <> '' ORDER BY name",
                tuple(ids))
            rows = cur.fetchall() or []
    finally:
        conn.close()
    grouped: dict[str, list[str]] = {}
    for r in rows:
        grouped.setdefault(str(r["site_code"]), []).append(str(r["name"]))
    return [
        {"site_code": s["site_code"], "display_name": s["display_name"],
         "is_aggregate": int(s["is_aggregate"]), "sort_order": s["sort_order"],
         "datasources": grouped.get(s["site_code"], [])}
        for s in list_sites() if s["site_code"] in grouped and not s["is_aggregate"]
    ]


def session_source_for_site(site_ref: Any, allowed_ids: Iterable[int],
                            model_id: int = 0) -> int:
    """会话站点 → 生效 datasource_id；聚合态/多义/未站点化返回 0（不猜源）。

    返回 0 表示「会话不锁定单一源」，由工具侧 site/datasource 参数逐查询解析
    （resource_guard 的空 datasource 语义：多候选要求显式选择）。
    """
    code = normalize_site_ref(site_ref)
    if not code:
        return 0
    site = get_site(code) or {}
    if int(site.get("is_aggregate") or 0):
        return 0
    candidates = datasources_of_site(code, allowed_ids)
    if len(candidates) == 1:
        return int(candidates[0]["id"])
    if not candidates:
        return 0
    target = site_of_model_target(model_id, code) if model_id else ""
    if target:
        for c in candidates:
            if c["name"] == target:
                return int(c["id"])
    return 0


def site_display_name(site_ref: Any) -> str:
    """站点写法 → 业务名（不识别时原样返回，供文案兜底不编造）。"""
    code = normalize_site_ref(site_ref)
    site = get_site(code) if code else None
    return (site or {}).get("display_name") or str(site_ref or "")
