"""跨源拓扑唯一投影口 — 承载（carrier）事实源的读写与裁剪。

事实源：`adh_ontology_carriers`（迁移 docker/mysql/ontology_carrier_migration.sql）。
本模块是它的**唯一读取口**：LLM 目录（get_metrics）、绑定选路（binding_resolver）、
图谱投影（graph_builder）、云端文档（to_cloud_md）、前端页签全部经此处取数，
禁止任何消费方自己 SELECT 该表（防双口径漂移，同 cross_site.py 的"派生不落第二登记处"）。

分层纪律：
- carrier 只描述**路由**（该概念在哪个源、是哪个源本体对象的哪种粒度/新鲜度副本），
  不描述物理形状；表/列映射唯一在 `adh_ontology_bindings`（部署绑定层）。
- 与 `deploy_sites` 的分工：deploy_sites 管"同构多站点复制"的展开；carrier 管
  "跨源/跨层承载"（数仓汇总、SLS 日志、镜像）。detail 承载未显式登记时回落站点展开。

脱敏纪律（护栏 §7 / waker-datasource-domain §2）：
- 对外一律业务名（数据源名、站点业务名、承载业务名），**不得**出现 datasource_id/
  物理表列/连接信息；`CarrierError` 文案只含业务名与候选清单。
- 权限裁剪是**作用域**而非降级：未授权承载对 LLM 不可见（与未授权站点静默裁剪同口径），
  但被裁剪计数 `carriers_filtered` 必须留在返回值里供审计/评测区分（不得连计数一起吞掉）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Optional

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)

CARRIER_TYPES = ("detail", "global", "log", "mirror")
FRESHNESS_LEVELS = ("realtime", "t_plus_1", "lagged")
CARRIER_STATUS = ("active", "deprecated")

# 需要显式指向源本体对象（物理形状有独立来源）的承载类型
_NEEDS_SOURCE = ("global", "log", "mirror")


class CarrierError(ValueError):
    """可直接提示用户的承载配置错误；只含业务名，不含物理标识。"""


def routing_enabled() -> bool:
    """承载择路是否生效（灰度开关 `CARRIER_ROUTING_ENABLED`，缺省开）。

    关闭态必须同时作用于**目录暴露**与**执行选路**两个面（见 config 注释）：
    只关选路会让模型看得见 carriers、传了 `carrier=` 却被静默忽略。
    开关状态在 web 启动日志显式声明，不在这里反复刷日志。
    """
    from backend.common import config
    return bool(getattr(config, "CARRIER_ROUTING_ENABLED", True))


# ── 读取（全量，管理/派生面向）──────────────────────────────────────────

_SELECT_SQL = (
    "SELECT id, model_id, object_key, carrier_key, display_name, carrier_type, "
    "datasource, sites, source_model, source_object, grain, freshness, as_of_note, "
    "is_authority, priority, size_class, status, created_by, updated_at "
    "FROM adh_ontology_carriers"
)


def _row_to_dict(row: dict) -> dict:
    """DB 行 → 对外结构（sites JSON 归一为 list；不含任何 id 之外的物理信息）。"""
    raw = row.get("sites")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except (ValueError, TypeError):
            logger.warning("[topology] carrier %s sites 非法 JSON，按未限定处理", row.get("id"))
            raw = []
    return {
        "id": int(row.get("id") or 0),
        "model_id": int(row.get("model_id") or 0),
        "object_key": str(row.get("object_key") or ""),
        "carrier_key": str(row.get("carrier_key") or ""),
        "display_name": str(row.get("display_name") or ""),
        "carrier_type": str(row.get("carrier_type") or "detail"),
        "datasource": str(row.get("datasource") or ""),
        "sites": [str(s) for s in (raw or []) if str(s or "").strip()],
        "source_model": str(row.get("source_model") or ""),
        "source_object": str(row.get("source_object") or ""),
        "grain": str(row.get("grain") or ""),
        "freshness": str(row.get("freshness") or "realtime"),
        "as_of_note": str(row.get("as_of_note") or ""),
        "is_authority": int(row.get("is_authority") or 0),
        "priority": int(row.get("priority") or 100),
        "size_class": str(row.get("size_class") or ""),
        "status": str(row.get("status") or "active"),
        "created_by": str(row.get("created_by") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


def list_carriers(model_id: int = 0, object_keys: Optional[Iterable[str]] = None,
                  include_deprecated: bool = False) -> list[dict]:
    """承载清单（按 priority, id 稳定排序）。

    model_id>0 限定单模型；object_keys 限定对象集合（跨模型，按 key 匹配——
    业务对象 key 是跨源寻址的入口，与 binding_resolver 同口径）。
    """
    sql = _SELECT_SQL
    conds: list[str] = []
    params: list[Any] = []
    if not include_deprecated:
        conds.append("status = 'active'")
    if int(model_id or 0):
        conds.append("model_id = %s")
        params.append(int(model_id))
    keys = [str(k).strip() for k in (object_keys or []) if str(k or "").strip()]
    if keys:
        conds.append(f"object_key IN ({', '.join(['%s'] * len(keys))})")
        params.extend(keys)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY priority ASC, id ASC"
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or None)
            rows = cur.fetchall() or []
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def carriers_by_object(object_keys: Iterable[str]) -> dict[str, list[dict]]:
    """{object_key: [carrier…]}（走快照缓存 + 内存分组，避免逐对象多次远程往返）。"""
    wanted = {str(k).strip() for k in (object_keys or []) if str(k or "").strip()}
    grouped: dict[str, list[dict]] = {}
    if not wanted:
        return grouped
    for c in list_carriers_cached():
        if c["object_key"] in wanted:
            grouped.setdefault(c["object_key"], []).append(c)
    for items in grouped.values():
        items.sort(key=lambda c: (c["priority"], c["id"]))
    return grouped


def list_carriers_cached() -> list[dict]:
    """全量 active 承载快照（缓存共享载荷，权限裁决仍在调用时做）。

    口径与看板载荷缓存一致：只缓存**用户无关**的共享部分，可见性/授权保持实时，
    避免把某个角色的裁剪结果缓存成全局事实。
    """
    return cached_carriers("active:all", list_carriers) or []


def topology_digest(model_ids: Optional[Iterable[int]] = None) -> str:
    """拓扑水位线：与 canonical doc 版本戳**分离**（改承载不触发云文档重推）。"""
    sql = "SELECT COALESCE(MAX(updated_at), 'none') AS m, COUNT(*) AS n FROM adh_ontology_carriers"
    params: list[Any] = []
    ids = [int(i) for i in (model_ids or []) if int(i or 0)]
    if ids:
        sql += f" WHERE model_id IN ({', '.join(['%s'] * len(ids))})"
        params.extend(ids)
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or None)
            row = cur.fetchone() or {}
    finally:
        conn.close()
    return f"{row.get('m')}:{row.get('n')}"


# ── 授权裁剪（唯一裁决：角色数据源授权 + 其派生站点）────────────────────

def _datasource_id_by_name() -> dict[str, int]:
    """{数据源业务名: id}（业务名是稳定键，删除重建 id 会变、name 不变）。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM adh_datasources")
            return {str(r["name"] or "").strip(): int(r["id"])
                    for r in (cur.fetchall() or []) if str(r.get("name") or "").strip()}
    finally:
        conn.close()


def carriers_for_llm(object_keys: Iterable[str], user_id: int = 0,
                     workspace_id: int = 0) -> dict:
    """LLM 可见的承载视图（业务名级）：{object_key: [carrier…]}。

    不携带 source_model/source_object 等结构字段（那些只在服务端择路时用），
    避免模型把内部建模结构当可传参数回灌。
    """
    view = visible_carriers(object_keys, user_id=user_id, workspace_id=workspace_id)
    return {"carriers": {k: [_llm_view(c) for c in items]
                         for k, items in (view.get("carriers") or {}).items()},
            "carriers_filtered": view.get("carriers_filtered", 0)}


def visible_carriers(object_keys: Iterable[str], user_id: int = 0,
                     workspace_id: int = 0) -> dict:
    """服务端可见的**完整**承载行（含 source_model/source_object，仅内部使用，不得外返）。

    空角色授权 = fail-closed 返回空（**不得**解释为全量，waker-datasource-domain §1）。
    返回 `carriers_filtered` 计数供审计/评测区分"确实没有承载"与"有承载但无权"——
    两者对用户呈现相同（都是分站/无路径），但对排查与评测必须可分辨。
    """
    keys = [str(k).strip() for k in (object_keys or []) if str(k or "").strip()]
    if not keys:
        return {"carriers": {}, "carriers_filtered": 0}

    from backend.core.role_service import role_service

    allowed_ids = set(role_service.get_user_allowed_datasources(int(user_id or 0),
                                                               int(workspace_id or 0)))
    name_to_id = _datasource_id_by_name()
    authorized_sites: set[str] = set()
    if allowed_ids:
        try:
            from backend.core.site_service import authorized_sites as _auth_sites
            authorized_sites = {str(s.get("site_code") or "")
                                for s in _auth_sites(int(user_id or 0), int(workspace_id or 0))}
        except Exception as e:  # noqa: BLE001 — 站点派生不可用时只按数据源裁决，不阻断
            logger.warning("[topology] authorized_sites 派生失败，按数据源授权裁剪: %s", e)

    out: dict[str, list[dict]] = {}
    filtered = 0
    for key, items in carriers_by_object(keys).items():
        visible: list[dict] = []
        for c in items:
            ds = c["datasource"]
            if ds:
                ds_id = name_to_id.get(ds, 0)
                if not ds_id or ds_id not in allowed_ids:
                    filtered += 1
                    continue          # 源不存在或无权：对 LLM 不可见（作用域裁剪）
            elif c["sites"]:
                # 未指定源（按站点展开）→ 站点必须至少有一个可查，否则整条裁掉
                if not (set(c["sites"]) & authorized_sites):
                    filtered += 1
                    continue
            visible.append(c)
        if visible:
            out[key] = visible
    return {"carriers": out, "carriers_filtered": filtered}


def _identity(s) -> str:
    """对象身份键（忽大小写与分隔符）：与 object_identity_key/binding_resolver 同口径。

    LLM 常写 'Case' 而 doc 里是 'case'，承载查找不能因写法差异退成“无承载”。
    """
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _norm_row(c: dict) -> dict:
    """完整承载行 → 择路用的结构（key/type/authority 等短名，仅服务端内部）。"""
    return {"key": c["carrier_key"], "name": c["display_name"] or c["carrier_key"],
            "type": c["carrier_type"], "datasource": c["datasource"], "sites": c["sites"],
            "source_model": c["source_model"], "source_object": c["source_object"],
            "grain": c["grain"], "freshness": c["freshness"], "as_of_note": c["as_of_note"],
            "authority": bool(c["is_authority"]), "priority": c["priority"]}


def _llm_view(carrier: dict) -> dict:
    """LLM 侧视图：业务名级字段 + 站点业务名（不出现 id/物理表列）。"""
    sites = carrier["sites"]
    return {
        "key": carrier["carrier_key"],
        "name": carrier["display_name"] or carrier["carrier_key"],
        "type": carrier["carrier_type"],
        "datasource": carrier["datasource"],
        "sites": [_site_label(s) for s in sites] if sites else [],
        "grain": carrier["grain"],
        "freshness": carrier["freshness"],
        "as_of_note": carrier["as_of_note"],
        "authority": bool(carrier["is_authority"]),
        "priority": carrier["priority"],
    }


def _site_label(site_code: str) -> str:
    try:
        from backend.core.site_service import get_site
        return str((get_site(site_code) or {}).get("display_name") or site_code)
    except Exception:  # noqa: BLE001 — 站点名解析失败回退代码，不影响主链路
        return site_code


# ── 择路（确定性、单点实现）─────────────────────────────────────────

def metric_carrier_constraints(object_key: str, metric_names: Iterable[str]) -> dict[str, str]:
    """指标名 → 允许的承载类型（`adh_metrics.allowed_carrier`）。

    只读不写：字典是口径唯一权威，这里只消费它的约束，不另定义一套口径。
    列不存在（未跑迁移）时按“无约束”处理并记日志 —— 不静默当查询失败。
    """
    wanted = {str(m or "").strip().lower().rsplit(".", 1)[-1] for m in (metric_names or [])}
    wanted.discard("")
    if not wanted:
        return {}
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT name, name_en, COALESCE(aliases, '[]') AS aliases, allowed_carrier "
                    "FROM adh_metrics WHERE is_active = 1 AND bound_object_key = %s",
                    (object_key,))
                rows = cur.fetchall() or []
            except Exception as e:  # noqa: BLE001 — 旧库未跑迁移：降级为无约束，但显式可诊断
                logger.warning("[topology] allowed_carrier 不可用（跑 ontology_carrier_routing_migration.sql），"
                               "本次按无承载约束处理: %s", e)
                return {}
    finally:
        conn.close()
    out: dict[str, str] = {}
    for r in rows:
        names = {str(r.get("name") or "").strip().lower(),
                 str(r.get("name_en") or "").strip().lower()}
        try:
            names |= {str(a).strip().lower() for a in json.loads(r.get("aliases") or "[]")}
        except (ValueError, TypeError):
            pass
        hit = names & wanted
        # 命中的每个写法都要记下约束（LLM 常写别名而非权威名）：只记一个会让别名逃过约束
        for h in hit:
            out[h] = str(r.get("allowed_carrier") or "any").strip().lower() or "any"
    return out


def decide_carrier(object_key: str, *, carrier: str = "", metrics: Iterable[str] = (),
                   user_id: int = 0, workspace_id: int = 0,
                   need_realtime: bool = False) -> dict:
    """择路唯一实现：本次取数该走哪条承载。

    status（调用方不得自己再写一套判断）：
      `none`        无承载声明 → 沿用现有路径，行为与改造前一致（渐进增强不阻塞）
      `use`         命中一条非 detail 承载 → 按 carrier 路由到目标源 + 源本体对象
      `detail`      只有各站明细承载 → 调用方逐站取数、分开呈现（不合计）
      `ambiguous`   多条候选且结果会不同 → 回抛业务名候选，让 LLM 用 ask_user 确认
      `unavailable` 显式指定的承载不存在/无权 → fail-loud，**不回退到别的承载静默出数**

    `path_reason` 与 status 同时给出（仅**已执行**的路径：use/detail/none），由调用方落
    `ResolvedBinding.path_used_reason`（审计/评测归因“本次到底走了哪条路”）。这个字段必须在
    **每个**分支都有值：只给 `use` 落戳会让“退成逐站”与“根本未声明承载”两种情形
    在评测里无从分辨（与 carriers_filtered 同理）。
    取值：declared | metric-constraint | authority | priority | site-detail
          | detail-fallback | legacy-binding
    （`reason` 则是面向模型/用户的文字说明，与分桶码分开，两套不混用）

    权限与降级的边界（拍板）：未授权承载对模型不可见（作用域裁剪，不向用户解释原因）；
    但“全局口径不可用”不得变成“拿分站数字冒充全局值”——只能退成逐站分开呈现。
    """
    if not routing_enabled():
        # 灰度关闭 = 行为等于改造前（不择路），但不得静默：原因入返回值供 provenance 区分
        return {"status": "none", "carrier": None, "candidates": [],
                "path_reason": "legacy-binding",
                "note": "CARRIER_ROUTING_ENABLED=false，本次未按取数路径选路（行为等同改造前）",
                "carriers_filtered": 0}
    key = str(object_key or "").strip()
    # 同一对象的承载可能挂在不同写法上（Case / case），按身份键收集后再裁剪
    ident = _identity(key)
    sibling_keys = [c["object_key"] for c in list_carriers_cached()
                    if _identity(c["object_key"]) == ident]
    view = visible_carriers(sibling_keys or [key], user_id=user_id, workspace_id=workspace_id)
    filtered = int(view.get("carriers_filtered") or 0)
    items = [_norm_row(c)
             for k, lst in (view.get("carriers") or {}).items() if _identity(k) == ident
             for c in lst]
    if not items:
        return {"status": "none", "carrier": None, "candidates": [],
                "path_reason": "legacy-binding", "carriers_filtered": filtered}

    want = str(carrier or "").strip()
    if want:
        hit = next((c for c in items if c["key"] == want), None)
        if hit is None:
            return {"status": "unavailable", "carrier": None,
                    "candidates": [c["key"] for c in items],
                    "reason": f"未找到取数路径「{want}」（不存在或当前不可用）；"
                              f"可用：{'、'.join(c['key'] for c in items) or '无'}",
                    "carriers_filtered": filtered}
        return _pick(hit, filtered)

    cons = metric_carrier_constraints(key, metrics)
    allow = {t for t in cons.values() if t and t != "any"}
    pool = items
    if allow:
        pool = [c for c in items if c["type"] in allow]
        if not pool:
            detail = [c for c in items if c["type"] == "detail"]
            if detail:
                return {"status": "detail", "carrier": None,
                        "candidates": [c["key"] for c in detail],
                        "path_reason": "detail-fallback",
                        "note": "所需全局口径的取数路径当前不可用，本次按站点分开取数（不合计）",
                        "carriers_filtered": filtered}
            return {"status": "unavailable", "carrier": None,
                    "candidates": [c["key"] for c in items],
                    "reason": "该口径需要的取数路径当前不可用，且无可分站查询的明细路径；"
                              "请如实告知用户该口径暂不可得，不得拿别的口径充数",
                    "carriers_filtered": filtered}
    if need_realtime:
        fresh = [c for c in pool if c["freshness"] == "realtime"]
        if fresh:
            pool = fresh
    if all(c["type"] == "detail" for c in pool):
        return {"status": "detail", "carrier": None,
                "candidates": [c["key"] for c in pool], "path_reason": "site-detail",
                "carriers_filtered": filtered}
    chosen = [c for c in pool if c["authority"]] or [c for c in pool if c["type"] != "detail"]
    chosen = sorted(chosen, key=lambda c: (c["priority"], c["key"]))
    if len(chosen) > 1:
        return {"status": "ambiguous", "carrier": None,
                "candidates": [{"key": c["key"], "name": c["name"], "type": c["type"],
                                "grain": c["grain"], "freshness": c["freshness"]} for c in chosen],
                "note": "同一问题有多条取数路径且结果可能不同，请让用户确认要哪个口径",
                "carriers_filtered": filtered}
    reason = ("metric-constraint" if allow else
              ("authority" if chosen[0]["authority"] else "priority"))
    return _pick(chosen[0], filtered, reason=reason)


def _pick(carrier: dict, filtered: int, *, reason: str = "declared") -> dict:
    status = "detail" if carrier["type"] == "detail" else "use"
    return {"status": status, "carrier": carrier, "candidates": [carrier["key"]],
            "path_reason": reason, "carriers_filtered": filtered}


def stamp_provenance(binding, decision: dict, *, explicit: bool = False) -> None:
    """把择路结果落到绑定溯源（内部/审计/eval 归因用，除 as_of_note 外不得外泄）。

    为何放在本模块而不是工具层：`semantic_query` 有一条静态护栏禁止 carrier 溯源字段
    出现在它的返源里（防进 LLM 视野）；落戳逻辑写在那里会把护栏磨钝。本模块不对外
    构返体，适合当写入点。

    `resolve_binding_via_carrier` 只会在命中非 detail 承载时落戳；detail/none 两条
    常规解析路径同样需要可诊断（否则“退成逐站”与“未声明承载”在评测里无从分辨）。
    已落过的戳不覆盖（择路在单一实现里算好，这里只搬运）。
    """
    if binding is None:
        return
    status = str(decision.get("status") or "none")
    if not getattr(binding, "carrier_bucket", ""):
        binding.carrier_bucket = status
    if not getattr(binding, "path_used_reason", ""):
        binding.path_used_reason = (str(decision.get("path_reason") or "")
                                    or ("declared" if explicit else "legacy-binding"))


# ── 校验（写侧，全部 fail-loud）────────────────────────────────────────

def validate_carriers(model_id: int, rows: list[dict]) -> list[str]:
    """返回错误清单（空 = 通过）。错误文案只含业务名。

    校验项（宁阻断勿悬空，与字典孤儿/路由校验同口径）：
    1. carrier_key 对象内唯一、非空；
    2. object_key 必须存在于该模型 canonical doc 的对象清单（防悬空）；
    3. carrier_type / freshness 值域合法；
    4. global/log/mirror 必须给出 datasource + source_model + source_object，
       且源本体对象真实存在于该源的 active 源本体（异构源的物理形状另有其人）；
    5. datasource 必须是注册数据源；sites 必须是启用站点且非聚合态；
    6. 每个对象最多一条 is_authority=1（两个权威口径 = 择路必错）。
    """
    issues: list[str] = []
    if not isinstance(rows, list):
        return ["carriers 必须是数组"]

    object_keys = _model_object_keys(int(model_id or 0))
    if object_keys is None:
        issues.append("模型不存在或 doc 无法解析，无法校验承载归属")

    ds_names: Optional[set] = None
    src_index: Optional[dict] = None
    seen_key: set[tuple] = set()
    authority_by_object: dict[str, str] = {}

    for i, raw in enumerate(rows):
        row = raw or {}
        okey = str(row.get("object_key") or "").strip()
        ckey = str(row.get("carrier_key") or "").strip()
        ctype = str(row.get("carrier_type") or "detail").strip()
        fresh = str(row.get("freshness") or "realtime").strip()
        ds = str(row.get("datasource") or "").strip()
        src_model = str(row.get("source_model") or "").strip()
        src_object = str(row.get("source_object") or "").strip()
        label = f"第 {i + 1} 条承载" + (f"（{okey}/{ckey}）" if okey or ckey else "")

        if not okey:
            issues.append(f"{label}：缺少归属对象")
        elif object_keys is not None and okey not in object_keys:
            issues.append(f"{label}：对象「{okey}」不在本模型的对象清单中"
                          f"（可用对象：{'、'.join(sorted(object_keys)) or '无'}）")
        if not ckey:
            issues.append(f"{label}：缺少承载标识（如 detail/global/sls_log）")
        elif object_keys is not None:
            pair = (okey, ckey)
            if pair in seen_key:
                issues.append(f"{label}：对象「{okey}」下承载标识「{ckey}」重复")
            seen_key.add(pair)

        if ctype not in CARRIER_TYPES:
            issues.append(f"{label}：承载类型「{ctype}」不合法（可选：{'/'.join(CARRIER_TYPES)}）")
        if fresh not in FRESHNESS_LEVELS:
            issues.append(f"{label}：新鲜度「{fresh}」不合法（可选：{'/'.join(FRESHNESS_LEVELS)}）")

        if ctype in _NEEDS_SOURCE:
            if not ds:
                issues.append(f"{label}：{ctype} 承载必须指定数据源（异构源不会随站点展开）")
            if not src_model or not src_object:
                issues.append(f"{label}：{ctype} 承载必须指明「源本体对象」"
                              f"（物理形状唯一在源本体，承载只负责指路）")
        if ds:
            if ds_names is None:
                from backend.core.ontology_index import datasource_name_set
                ds_names = datasource_name_set()
            if ds not in ds_names:
                issues.append(f"{label}：数据源「{ds}」不存在（或已改名/删除）")
        if src_model or src_object:
            if src_index is None:
                from backend.core.ontology_index import active_source_ontology_index
                src_index = active_source_ontology_index() or {}
            info = src_index.get(src_model)
            if info is None:
                issues.append(f"{label}：源本体「{src_model or '（未填）'}」没有生效版本"
                              f"（可用源本体：{'、'.join(sorted(src_index)) or '无，请先在数据源上生成本体模型'}）")
            elif src_object not in (info.get("object_keys") or set()):
                issues.append(f"{label}：源本体「{src_model}」中不存在对象「{src_object}」")
            elif ds and info.get("datasource_name") and info["datasource_name"] != ds:
                issues.append(f"{label}：数据源「{ds}」与源本体「{src_model}」实际归属的源"
                              f"「{info['datasource_name']}」不一致（防错配，请二选一）")

        for code in (row.get("sites") or []):
            site = _site_or_none(str(code))
            if site is None:
                issues.append(f"{label}：站点「{code}」不在启用站点清单")
            elif int(site.get("is_aggregate") or 0):
                issues.append(f"{label}：站点「{site.get('display_name') or code}」是聚合态，"
                              f"不能作为承载的适用站点（聚合态由逐站编排消费）")

        if int(row.get("is_authority") or 0) and okey:
            if okey in authority_by_object:
                issues.append(f"对象「{okey}」有多条权威承载"
                              f"（{authority_by_object[okey]} 与 {ckey}）——权威口径必须唯一")
            authority_by_object[okey] = ckey
    return issues


def _site_or_none(site_code: str) -> Optional[dict]:
    try:
        from backend.core.site_service import get_site
        site = get_site(site_code) or None
        if site and not int(site.get("is_active") or 0):
            return None
        return site
    except Exception:  # noqa: BLE001
        return None


def _model_object_keys(model_id: int) -> Optional[set]:
    """模型 doc 内的对象 key 集合（含存量旧字段名 object_key）；模型不存在返回 None。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT json_content FROM adh_ontology_models WHERE id = %s", (model_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        doc = json.loads(row.get("json_content") or "{}")
    except (ValueError, TypeError):
        return set()
    return {str(o.get("key") or o.get("object_key") or "").strip()
            for o in (doc.get("objects") or [])} - {""}


# ── 写入（整模型替换；调用方负责权限与审计字段注入）──────────────────────

def save_carriers(model_id: int, rows: list[dict], created_by: str = "") -> dict:
    """按模型整份替换承载清单（幂等、可重跑）。校验不通过抛 CarrierError。

    整份替换而非逐条 upsert 的理由：承载清单是"该模型的全部路径"这一整体配置，
    半截状态（漏删旧路径）会让择路在已废弃的承载上出数。删除单条走 status=deprecated
    由调用方在 rows 里显式表达（保留行、置状态），不物理删除，可回退。
    """
    mid = int(model_id or 0)
    if not mid:
        raise CarrierError("缺少模型 id")
    issues = validate_carriers(mid, rows or [])
    if issues:
        raise CarrierError("承载配置校验未通过：" + "；".join(issues[:10])
                           + (" 等" if len(issues) > 10 else ""))

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, object_key, carrier_key, status FROM adh_ontology_carriers "
                        "WHERE model_id = %s", (mid,))
            existing = {(str(r["object_key"]), str(r["carrier_key"])): r
                        for r in (cur.fetchall() or [])}
            wanted: dict[tuple, dict] = {}
            for raw in rows or []:
                row = raw or {}
                okey = str(row.get("object_key") or "").strip()
                ckey = str(row.get("carrier_key") or "").strip()
                status = str(row.get("status") or "active").strip()
                if status not in CARRIER_STATUS:
                    raise CarrierError(f"承载状态「{status}」不合法")
                wanted[(okey, ckey)] = row
            # 未出现在 rows 中的既有行 = 删除（配置整体替换语义）；已 deprecated 的行
            # 不重复写，避免无意义的 updated_at 抖动
            for pair, row in existing.items():
                if pair not in wanted and str(row.get("status")) != "deprecated":
                    # 显式写 UTC：列上的 ON UPDATE CURRENT_TIMESTAMP 用的是服务器本地时区，
                    # 会把 updated_at 抬成 +8 时刻（监控页按 UTC 窗口过滤就永远看不到）
                    cur.execute("UPDATE adh_ontology_carriers SET status = 'deprecated', "
                                "updated_at = UTC_TIMESTAMP(6) WHERE id = %s", (int(row["id"]),))
            for pair, row in wanted.items():
                payload = _carrier_params(mid, row, created_by,
                                          existing.get(pair) is not None)
                if existing.get(pair) is not None:
                    cur.execute(
                        "UPDATE adh_ontology_carriers SET "
                        "display_name=%s, carrier_type=%s, datasource=%s, sites=%s, "
                        "source_model=%s, source_object=%s, grain=%s, freshness=%s, "
                        "as_of_note=%s, is_authority=%s, priority=%s, size_class=%s, "
                        "status=%s, updated_at=UTC_TIMESTAMP(6) "
                        "WHERE model_id=%s AND object_key=%s AND carrier_key=%s",
                        payload[:-3] + (mid, pair[0], pair[1]))
                else:
                    cur.execute(
                        "INSERT INTO adh_ontology_carriers "
                        "(model_id, object_key, carrier_key, display_name, carrier_type, "
                        " datasource, sites, source_model, source_object, grain, freshness, "
                        " as_of_note, is_authority, priority, size_class, status, created_by) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        payload)
        conn.commit()
    finally:
        conn.close()
    invalidate_topology_cache()
    saved = list_carriers(model_id=mid, include_deprecated=False)
    return {"model_id": mid, "saved": len(saved),
            "carriers": [
                {"object_key": c["object_key"], "carrier_key": c["carrier_key"],
                 "display_name": c["display_name"], "carrier_type": c["carrier_type"]}
                for c in saved]}


def _carrier_params(model_id: int, row: dict, created_by: str, updating: bool) -> tuple:
    sites = [str(s).strip() for s in (row.get("sites") or []) if str(s or "").strip()]
    return (
        model_id,
        str(row.get("object_key") or "").strip(),
        str(row.get("carrier_key") or "").strip(),
        str(row.get("display_name") or "").strip(),
        str(row.get("carrier_type") or "detail").strip(),
        str(row.get("datasource") or "").strip(),
        json.dumps(sites, ensure_ascii=False) if sites else None,
        str(row.get("source_model") or "").strip(),
        str(row.get("source_object") or "").strip(),
        str(row.get("grain") or "").strip(),
        str(row.get("freshness") or "realtime").strip(),
        str(row.get("as_of_note") or "").strip(),
        1 if int(row.get("is_authority") or 0) else 0,
        int(row.get("priority") or 100),
        str(row.get("size_class") or "").strip(),
        str(row.get("status") or "active").strip(),
        "" if updating else str(created_by or "")[:64],
    )


# ── 缓存 ───────────────────────────────────────────────────────────────

def invalidate_topology_cache() -> None:
    """写时失效（不靠 TTL 收敛）：改承载后目录/选路必须立刻看到新拓扑。

    缓存键统一以 `carriers:` 前缀（快照与分片共用），整批前缀失效即可。
    """
    from backend.common.ttl_cache import topology_cache
    topology_cache.invalidate_prefix("carriers:")


def cached_carriers(scope: str, loader) -> Any:
    """读侧缓存壳（元库在公网，逐请求回源会把延迟累到用户可感知处）。

    loader 返回 None 时不缓存（避免把"查不到"固化成 5 分钟空结果）。
    """
    from backend.common.ttl_cache import topology_cache
    return topology_cache.get_or_set(f"carriers:{scope}", loader)
