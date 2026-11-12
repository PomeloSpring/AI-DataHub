"""分析场景的「主体授权」派生 —— 场景绑定业务主体（本体对象），可见性由角色权限派生。

用户 2026-11-06 拍板：场景不按"角色 × 场景"逐个勾，而是**按场景对应的主体授权**——
角色对该主体有权限，就能看到这个场景、也才会在 AI 侧命中它。

为什么这样切（而不是新建一张 adh_role_scenario_access）：
  * 主体（本体对象）的物理落点就是"哪个数据源的哪张表"，而**数据源可用性在本仓只有一个
    裁决点**：角色授权（`role_service.get_user_allowed_datasources`，见 waker-datasource-domain §1）。
    再建一张场景授权表等于给同一件事配第二个真值源，两边迟早不一致。
  * 场景正文编排的就是对象/指标/维度。角色查不到这个主体，那份正文对它没有意义
    （注入了也取不到数），把它藏起来才是正确语义。

口径（三条硬要求）：
  1. **fail-closed**：场景没登记主体、也没显式勾「不限主体」→ 不可见、不命中、不进 `/` 候选。
     空授权不得被解释为全量（与 §1 同口径）。
  2. **不限主体是显式动作**：`metadata.subject_all=true` 才全员可见 —— 让"扩权"由管理员主动
     勾选，而不是漏填主体时的副作用。
  3. **三处消费同源**：`/` 面板候选、`match_quick_paths` 提示词注入、管理面可见性说明，
     必须走同一个判定函数。只过滤展示层是假的 —— 那样别人的场景正文仍会被触发词带进
     提示词（越权信息面）。

系统本体（kind='system'，如 dataset/dashboard/role 这些平台自身对象）不在数据源授权里，
按 AS-BOT 是否持有 `system` 工具组裁决（`system_scope` 参数）；调用方拿不到策略时按 False
处理（宁缺勿滥）。
"""
from __future__ import annotations

import json
import logging
import time

from backend.common.db import execute_query

logger = logging.getLogger(__name__)

_TTL_SECONDS = 60.0          # 配置类事实短 TTL：改本体/改角色授权后最多 1 分钟生效
_catalog_cache: tuple[float, list[dict]] | None = None
_models_cache: tuple[float, dict] | None = None
_ds_name_cache: tuple[float, dict] | None = None


def clear_cache() -> None:
    """主体清单 / 模型映射 / 数据源名缓存失效（本体发布或角色授权变更后调用）。"""
    global _catalog_cache, _models_cache, _ds_name_cache
    _catalog_cache = _models_cache = _ds_name_cache = None


def _cached(cache: tuple | None, builder):
    """TTL 缓存读取（命中返回旧值，否则重建并回填）。"""
    if cache is not None and time.time() - cache[0] <= _TTL_SECONDS:
        return cache[1]
    return builder()


# ── 主体清单（管理页选择框的唯一数据源）────────────────────────────────────

def subject_catalog() -> list[dict]:
    """全部启用本体对象：``[{key, display_name, kind, model_id, model_name, datasource_id}]``。

    显示一律用业务名（ui-resource-display：选择框给 name，value 才用内部 key）。
    """
    def build():
        rows: list[dict] = []
        try:
            # `key` 是 MySQL 保留字，必须加反引号（不加会 1064 语法错，而异常被下面
            # 吞成空清单，表现为“所有场景对所有角色都不可见”——难查且危险）
            rows = [dict(r) for r in (execute_query(
                """SELECT o.object_key AS `key`, o.display_name, m.kind, m.id AS model_id,
                          m.name AS model_name, m.datasource_id
                   FROM adh_ontology_objects o
                   JOIN adh_ontology_models m ON m.id = o.model_id
                   WHERE o.is_active = 1 AND m.status = 'active'
                   ORDER BY CASE m.kind WHEN 'business' THEN 0 WHEN 'system' THEN 1 ELSE 2 END,
                            m.name, o.display_name, o.object_key""") or [])]
        except Exception as e:  # noqa: BLE001 — 加载失败要可诊断，不能静默当"没有主体"
            logger.warning("[ScenarioAccess] 主体清单加载失败: %s", e)
        global _catalog_cache
        _catalog_cache = (time.time(), rows)
        return rows

    return _cached(_catalog_cache, build)


def _models_by_id() -> dict:
    """``{model_id: {kind, datasource_id, datasource_names}}`` —— 判模型落在哪些数据源。

    业务本体的多站点归属在模型级 `deploy_sites`（[{site_code, datasource(业务名)}]，
    ontology-modeling §1），所以"模型落在哪些源" = 主源 id ∪ deploy_sites 里的业务名。
    """
    def build():
        out: dict = {}
        try:
            for r in (execute_query(
                    "SELECT id, kind, datasource_id, deploy_sites FROM adh_ontology_models "
                    "WHERE status='active'") or []):
                raw = r.get("deploy_sites")
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw or "[]")
                    except (json.JSONDecodeError, TypeError):
                        raw = []
                names = {str(d["datasource"]).strip() for d in (raw or [])
                         if isinstance(d, dict) and d.get("datasource")}
                out[int(r["id"])] = {"kind": r.get("kind") or "",
                                     "datasource_id": int(r.get("datasource_id") or 0),
                                     "datasource_names": names}
        except Exception as e:  # noqa: BLE001
            logger.warning("[ScenarioAccess] 本体模型映射加载失败: %s", e)
        global _models_cache
        _models_cache = (time.time(), out)
        return out

    return _cached(_models_cache, build)


def _datasource_names(ids: set[int]) -> set[str]:
    if not ids:
        return set()
    def build():
        out: dict = {}
        try:
            placeholders = ",".join(["%s"] * len(ids))
            for r in (execute_query(
                    f"SELECT id, name FROM adh_datasources WHERE id IN ({placeholders})",
                    tuple(ids)) or []):
                out[int(r["id"])] = str(r.get("name") or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("[ScenarioAccess] 数据源名解析失败: %s", e)
        global _ds_name_cache
        _ds_name_cache = (time.time(), out)
        return out

    mapping = _cached(_ds_name_cache, build)
    return {mapping[int(i)] for i in ids if mapping.get(int(i))}


# ── 可见主体派生 ────────────────────────────────────────────────────────────

def visible_subject_keys(user_id: int, workspace_id: int = 0, *, role: str = "",
                         system_scope: bool = False,
                         allowed_datasource_ids: list[int] | None = None) -> set[str]:
    """当前身份可见的主体（object_key）集合。

    业务主体：角色授权数据源命中模型 `datasource_id` 或 `deploy_sites.datasource` 业务名。
    系统主体：仅当 `system_scope=True`（AS-BOT 持有 system 工具组）才纳入。
    admin：不受主体限制（与 dashboard/dataset 的 admin 代码层旁路同口径）。
    """
    catalog = subject_catalog()
    if not catalog:
        return set()
    if (role or "").strip().lower() == "admin":
        return {c["key"] for c in catalog if c.get("key")}

    ids = allowed_datasource_ids
    if ids is None:
        from backend.core.role_service import role_service
        ids = role_service.get_user_allowed_datasources(user_id, workspace_id)
    allowed_ids = {int(i) for i in (ids or [])}
    if not allowed_ids:
        return set()          # 空授权 = 无任何主体可见（fail-closed，不是"不限制"）

    names = _datasource_names(allowed_ids)
    models = _models_by_id()
    out: set[str] = set()
    for c in catalog:
        key = c.get("key")
        if not key:
            continue
        kind = (c.get("kind") or "").lower()
        if kind == "system":
            if system_scope:
                out.add(key)
            continue
        if kind == "source":
            continue          # 源本体已退役（kind 二分拦截），不参与场景主体
        m = models.get(int(c.get("model_id") or 0))
        if m is None:         # 映射缺失时不猜可见：退回对象行自己的数据源
            ds_id = int(c.get("datasource_id") or 0)
            if ds_id and ds_id in allowed_ids:
                out.add(key)
            continue
        if (m["datasource_id"] and m["datasource_id"] in allowed_ids) or \
           (m["datasource_names"] & names):
            out.add(key)
    return out


# ── 场景与主体的绑定判定 ────────────────────────────────────────────────────

def scenario_subjects(metadata: dict | str | None) -> tuple[list[str], bool]:
    """解析场景登记的主体：返回 ``(object_keys, 不限主体)``。

    历史 `related_tables` 存的是物理表名，**不算主体**；所以老数据没登记 subjects 时返回
    空列表，由调用方按 fail-closed 处理（不可见 + 给出可诊断原因）。
    """
    meta = metadata
    if isinstance(meta, str):
        try:
            meta = json.loads(meta or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
    if not isinstance(meta, dict):
        return [], False
    subjects = meta.get("subjects") or []
    if isinstance(subjects, str):
        subjects = subjects.replace("，", ",").split(",")
    return [str(s).strip() for s in subjects if str(s).strip()], bool(meta.get("subject_all"))


def is_scenario_visible(metadata: dict | str | None, allowed_keys: set[str]) -> tuple[bool, str]:
    """场景对当前身份是否可见，同时给出不可见原因（可诊断，不静默丢）。"""
    keys, all_visible = scenario_subjects(metadata)
    if all_visible:
        return True, ""
    if not keys:
        return False, "未绑定主体（既未选主体也未勾「不限主体」），不会命中、也不进 / 候选"
    if not (set(keys) & set(allowed_keys or set())):
        return False, "所绑主体当前身份无权限（主体：" + "、".join(keys) + "）"
    return True, ""


def subject_display_names(keys: list[str]) -> list[str]:
    """object_key → 业务名（列表页/提示词显示用；查不到就原样回 key，不编造）。"""
    if not keys:
        return []
    by_key = {c.get("key"): (c.get("display_name") or c.get("key")) for c in subject_catalog()}
    return [by_key.get(k) or k for k in keys]
