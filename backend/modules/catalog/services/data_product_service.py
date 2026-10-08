"""数据产品层（L1）：治理身份 + 数据契约 + 血缘 + 版本。

## 职责边界（不得混淆）
- `adh_table_info`     物理元数据事实源（表/列/类型/注释），由元数据同步维护；
- `adh_data_products`  治理身份（谁产出 / 契约 / 版本 / 能否被本体引用），由加工层登记；
- `adh_datasets`       BI 消费封装（语义/SQL 双来源），在数据产品之上。

## 为什么需要它
本体对象现在绑在「数据源连接 + 裸物理表名」上，回答不了四个问题：
①这张表谁产出 ②什么时候产出、跑成功没 ③列变了谁负责 ④能不能被本体引用。
数据产品给"可被本体引用的表"一个**平台级稳定身份**：`product_name` 全局唯一且
不含内部 id，数据源删除重建后 id 变了 name 不变。

## 纪律
- **幂等**：登记一律按 `uk_product_name` upsert，重复投递不产生重复行（distributed-first）；
- **fail-loud**：schema 变更必须显式产出版本记录与兼容性判定，破坏性变更标 `is_breaking=1`，
  不得静默覆盖旧契约；
- **只读契约**：本模块不直接执行查询，也不碰 `adh_table_info`（那是元数据事实源）。
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from typing import Optional

from backend.common.db import execute_query, execute_write

# producer_kind / status 取值（与迁移文件注释保持一致）
PRODUCER_KINDS = ("sync_task", "dag_workflow", "manual", "external")
STATUSES = ("draft", "certified", "deprecated", "retired")
COMPAT = ("backward", "forward", "full", "none")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def schema_fingerprint(columns: list) -> str:
    """列集指纹：按 (列名, 类型) 排序后哈希。

    排序后再哈希，保证列顺序变化不算 schema 变更；只认 name+type，
    注释变化不触发版本升级（注释不是契约）。
    """
    norm = sorted(
        f"{str(c.get('name') or '').strip().lower()}:{str(c.get('type') or '').strip().lower()}"
        for c in (columns or [])
    )
    return hashlib.sha256("|".join(norm).encode("utf-8")).hexdigest()


def _diff_columns(old: list, new: list) -> dict:
    """列级差异：新增 / 删除 / 类型变更。用于兼容性判定与影响分析。"""
    o = {str(c.get("name") or "").lower(): str(c.get("type") or "").lower() for c in (old or [])}
    n = {str(c.get("name") or "").lower(): str(c.get("type") or "").lower() for c in (new or [])}
    added = sorted(set(n) - set(o))
    dropped = sorted(set(o) - set(n))
    changed = sorted(k for k in (set(o) & set(n)) if o[k] != n[k])
    return {
        "added_columns": added,
        "dropped_columns": dropped,
        "changed_columns": [{"name": k, "from": o[k], "to": n[k]} for k in changed],
    }


def _judge_compatibility(diff: dict) -> tuple[str, bool]:
    """兼容性判定（保守）：删列/类型变更视为破坏性，新增列向后兼容。"""
    if diff["dropped_columns"] or diff["changed_columns"]:
        return "none", True
    if diff["added_columns"]:
        return "backward", False
    return "full", False


def list_class_instances(product_class: str) -> list:
    """产品类的全部物理实例（每个站点一个）。
    
        同构多站点的展开入口：本体对象绑 product_class，执行时按会话站点从这里
        取到具体实例的 (数据源, 物理表)。类名为空返回空（fail-closed，不猜）。
    """
    cls = str(product_class or "").strip()
    if not cls:
        return []
    return execute_query(
        "SELECT product_name, product_class, site, datasource_name, db_name, "
        "       physical_table, catalog_ref, status, owner, sla "
        "FROM adh_data_products WHERE product_class = %s AND status <> 'retired' "
        "ORDER BY site, product_name", (cls,)) or []


def check_class_contract(product_class: str, exclude_product: str = "",
                         schema_hash: str = "") -> list:
    """同构约束校验：产品类下的各站点实例必须共享同一 schema 契约。
    
        为什么要校验：6 个站点表结构一样，本应是「一个产品类 × 6 个物理部署」。
        若无同构约束，schema 变更会退化为逐站点独立演进，出现「改了北京忘了上海」，
        而下游的本体/口径/看板都以为它们同构——错数会静默发生。
    
        返回 warnings（空 = 同构）；**只报告不阻断**（阻断会妨碍先把站点登记进来），
        但调用方必须把 warnings 显式带回，不得吞掉（no-silent-degradation）。
    """
    cls = str(product_class or "").strip()
    if not cls:
        return []
    rows = execute_query(
        "SELECT product_name, site, schema_hash, schema_version, physical_table "
        "FROM adh_data_products WHERE product_class = %s AND status <> 'retired'",
        (cls,)) or []
    warnings: list = []
    seen: dict = {}
    for r in rows:
        pname = str(r.get("product_name") or "")
        if exclude_product and pname == exclude_product:
            continue
        h = str(r.get("schema_hash") or "")
        if not h:
            continue
        seen.setdefault(h, []).append(f"{pname}({r.get('site') or '-'} v{r.get('schema_version')})")

    if len(seen) > 1:
        groups = " | ".join(f"{h[:8]}:{v}" for h, v in seen.items())
        warnings.append(
            f"产品类 {cls} 下站点实例 schema 不一致（同构约束破坏）：{groups}。"
            f"同构站点应共享一份契约；请确认是站点真实差异还是某站点漏改。")
    if schema_hash:
        # 与同类其余实例比：传入的指纹若不在已知集合里，说明本次登记将引入分歧
        others = {h for h in seen}
        if others and schema_hash not in others:
            warnings.append(
                f"本次登记的 schema 与产品类 {cls} 的既有实例均不同"
                f"（现有指纹 {sorted(x[:8] for x in others)}，本次 {schema_hash[:8]}）。")
    return warnings


def find_by_table(datasource_name: str, physical_table: str) -> Optional[dict]:
    """按 (数据源名, 物理表) 定位数据产品；数据源名为空时回落只按表名。

    **唯一查询口径**：binding_resolver 与 ontology_service 都必须走这里，
    不得另造第二套判断（否则两处口径漂移，会出现“解析说未登记、门禁说已登记”）。
    注意 `product_name` 形如 `数据源.表`，但匹配用 datasource_name + physical_table，
    因为 AS-BOT 系统本体的 datasource_name 可能为空（datasource_id=0 无对应数据源行）。
    """
    table = str(physical_table or "").strip()
    if not table:
        return None
    ds = str(datasource_name or "").strip()
    if ds:
        row = execute_query(
            "SELECT * FROM adh_data_products "
            "WHERE datasource_name = %s AND physical_table = %s ORDER BY id DESC LIMIT 1",
            (ds, table), fetchone=True)
    else:
        row = execute_query(
            "SELECT * FROM adh_data_products "
            "WHERE physical_table = %s ORDER BY id DESC LIMIT 1",
            (table,), fetchone=True)
    return dict(row) if row else None


def analyze_impact(product_name: str) -> dict:
    """契约变更影响分析：从 product_ref 反查所有受影响资产。

    数据产品是本体的来源，本体是指标/维度的来源，指标被 BI 数据集/看板消费。
    所以产品契约变更的影响面是链式的：产品 → 本体对象 → 指标/维度 → 数据集/图表。
    这是 P3「破坏性变更需人工确认」的依据——没影响面就不知道该通知谁。
    """
    product = get_product(product_name)
    if not product:
        raise ValueError(f"数据产品不存在: {product_name}")

    objects = execute_query(
        "SELECT object_key, model_id FROM adh_ontology_bindings "
        "WHERE product_ref = %s AND status = 'active'", (product_name,)) or []
    obj_keys = [str(r["object_key"]) for r in objects if r.get("object_key")]

    metrics = dims = datasets = []
    if obj_keys:
        ph = ",".join(["%s"] * len(obj_keys))
        metrics = execute_query(
            f"SELECT name FROM adh_metrics WHERE is_active = 1 AND bound_object_key IN ({ph})",
            tuple(obj_keys)) or []
        dims = execute_query(
            f"SELECT name FROM adh_dimensions WHERE is_active = 1 AND bound_object_key IN ({ph})",
            tuple(obj_keys)) or []
        datasets = execute_query(
            f"SELECT name FROM adh_datasets WHERE status = 'active' AND object_key IN ({ph})",
            tuple(obj_keys)) or []

    impact = {
        "product_name": product_name,
        "product_status": product.get("status"),
        "ontology_objects": [{"object_key": r["object_key"], "model_id": r.get("model_id")}
                             for r in objects],
        "metrics": [m["name"] for m in metrics],
        "dimensions": [d["name"] for d in dims],
        "datasets": [d["name"] for d in datasets],
    }
    impact["total"] = (len(impact["ontology_objects"]) + len(impact["metrics"])
                       + len(impact["dimensions"]) + len(impact["datasets"]))
    if impact["total"] == 0:
        impact["note"] = "无下游消费，契约变更风险低"
    elif not impact["ontology_objects"]:
        impact["note"] = "未被本体引用，但可能有其他消费方，请人工确认"
    else:
        impact["note"] = f"影响 {len(impact['ontology_objects'])} 个本体对象及下游指标/维度/数据集"
    return impact


def propose_contract_change(product_name: str, schema_version: int,
                            compatibility: str, changed_summary: str,
                            note: str, requested_by: str = "") -> dict:
    """契约变更影响分析（破坏性变更的 UI 确认前置）。

    只做影响分析、**不改契约**：破坏性变更须人工确认影响面后，由调用方直调
    :func:`apply_contract_change` 生效（菜单与功能权限码 dataset:manage 把关，
    不再走提议→审批→执行回路）。

    返回 {"impact": {...}, "payload": {...}}；失败显式抛错，不静默。
    """
    if compatibility not in COMPAT:
        raise ValueError(f"compatibility 必须是 {COMPAT}")
    impact = analyze_impact(product_name)
    payload = {
        "product_name": product_name,
        "schema_version": int(schema_version),
        "compatibility": compatibility,
        "changed_summary": changed_summary or "",
        "note": note or "",
        "is_breaking": 1 if compatibility == "none" else 0,
    }
    return {"impact": impact, "payload": payload}


def _safe_user_id(requested_by) -> int:
    """从 `user:123` 这类标记取数字 id；取不到返回 0（系统提议）。"""
    s = str(requested_by or "")
    digits = "".join(ch for ch in s.split(":")[-1] if ch.isdigit())
    return int(digits or 0)


def apply_contract_change(product_name: str, schema_version: int,
                          approved_by: str = "") -> dict:
    """应用候选契约版本（把 pending 候选置为 applied 并更新产品契约）。

    直执行：由持有 dataset:manage（菜单与功能权限码）的人确认影响面后调用；
    调用方必须提供 approved_by（审计溯源），不得匿名应用。
    """
    product = get_product(product_name)
    if not product:
        raise ValueError(f"数据产品不存在: {product_name}")
    row = execute_query(
        "SELECT * FROM adh_data_product_versions "
        "WHERE product_name = %s AND schema_version = %s",
        (product_name, int(schema_version)), fetchone=True)
    if not row:
        raise ValueError(f"候选契约版本不存在: {product_name} v{schema_version}")
    if not approved_by:
        raise ValueError("应用契约变更必须提供 approved_by（审计溯源）")
    # 写动作直执行把关：dataset:manage（菜单与功能权限码）fail-closed
    from backend.modules.mind.execution.perm_link import require_write_perm
    require_write_perm(_safe_user_id(approved_by), 0, "dataset:manage", "数据产品契约变更")

    cols = row.get("columns_snapshot") or []
    if isinstance(cols, (str, bytes)):
        try:
            cols = json.loads(cols)
        except (ValueError, TypeError):
            cols = []
    execute_write(
        "UPDATE adh_data_product_versions SET status = 'applied' "
        "WHERE product_name = %s AND schema_version = %s",
        (product_name, int(schema_version)))
    execute_write(
        "UPDATE adh_data_products SET schema_hash = %s, schema_version = %s, "
        "compatibility = %s, updated_at = %s WHERE product_name = %s",
        (row.get("schema_hash") or "", int(schema_version),
         row.get("compatibility") or "backward", _now(), product_name))
    return {"product": get_product(product_name), "applied_version": int(schema_version),
            "approved_by": approved_by, "columns": cols}


def list_pending_changes() -> list:
    """待人工确认的破坏性契约变更清单（status='pending'）。"""
    rows = execute_query(
        "SELECT product_name, schema_version, compatibility, dropped_columns, "
        "       changed_columns, added_columns, status, note, created_by, created_at "
        "FROM adh_data_product_versions WHERE status = 'pending' "
        "ORDER BY created_at DESC") or []
    out = []
    for r in rows:
        d = dict(r)
        for k in ("dropped_columns", "changed_columns", "added_columns"):
            if isinstance(d.get(k), (str, bytes)):
                try:
                    d[k] = json.loads(d[k])
                except (ValueError, TypeError):
                    d[k] = []
        if hasattr(d.get("created_at"), "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


def get_product(product_name: str) -> Optional[dict]:
    row = execute_query(
        "SELECT * FROM adh_data_products WHERE product_name = %s",
        (product_name,), fetchone=True)
    return dict(row) if row else None


def list_products(datasource_name: str = "", status: str = "",
                  keyword: str = "", limit: int = 200) -> list:
    """数据产品清单。返回体带 name 字段，供 UI 显示用 name 而非裸 id。"""
    sql = ("SELECT id, product_name, display_name, description, domain, datasource_name, "
           "       catalog_ref, physical_table, producer_kind, producer_ref, "
           "       schema_version, compatibility, classification, tier, owner, status, "
           "       created_at, updated_at "
           "FROM adh_data_products WHERE 1=1")
    params: list = []
    if datasource_name:
        sql += " AND datasource_name = %s"
        params.append(datasource_name)
    if status:
        sql += " AND status = %s"
        params.append(status)
    if keyword:
        sql += " AND (product_name LIKE %s OR display_name LIKE %s OR physical_table LIKE %s)"
        params += [f"%{keyword}%"] * 3
    sql += " ORDER BY updated_at DESC LIMIT %s"
    params.append(int(limit))
    rows = execute_query(sql, tuple(params)) or []
    out = []
    for r in rows:
        d = dict(r)
        for k in ("created_at", "updated_at"):
            if hasattr(d.get(k), "isoformat"):
                d[k] = d[k].isoformat()
        out.append(d)
    return out


def register_product(payload: dict, columns: list = None,
                     created_by: str = "system", on_breaking: str = "defer") -> dict:
    """登记/更新一个数据产品（幂等，按 product_name upsert）。

    Args:
        payload: product_name(必填) + display_name/description/domain/
                 datasource_name/catalog_name/db_name/physical_table/catalog_ref/
                 producer_kind/producer_ref/upstream_refs/compatibility/
                 classification/tier/owner/status
                 + product_class(产品类，同构站点共享契约；缺省取 physical_table)
                 + site(站点业务名，非站点化部署留空)
        columns: 当前列集 [{"name","type","comment"}]，用于算 schema 指纹与版本比对
        created_by: 登记来源（system = 加工层自动登记）
        on_breaking: 破坏性变更处置 ——
            "defer"(默认) 记 pending 候选版本、**不改产品契约**，待审批后生效；
            "apply" 直接生效（仅限人工明确要求的场景）。

    Returns:
        {"product":..., "schema_changed":bool, "version_record":...,
         "warnings":[...], "pending_approval":bool}
    """
    name = str(payload.get("product_name") or "").strip()
    if not name:
        raise ValueError("product_name 必填（数据产品的稳定身份）")
    producer_kind = str(payload.get("producer_kind") or "manual")
    if producer_kind not in PRODUCER_KINDS:
        raise ValueError(f"producer_kind 必须是 {PRODUCER_KINDS}")
    status = str(payload.get("status") or "draft")
    if status not in STATUSES:
        raise ValueError(f"status 必须是 {STATUSES}")

    new_fp = schema_fingerprint(columns or [])
    prev = get_product(name)
    warnings: list = []
    version_record = None
    schema_changed = False
    pending_approval = False

    # 产品类：同构站点共享的 schema 契约名。缺省取物理表名（非站点化部署的类名即表名）。
    product_class = str(payload.get("product_class") or payload.get("physical_table") or "").strip()
    site = str(payload.get("site") or "").strip()
    if product_class:
        # 同构约束：同类站点实例必须同 schema。漂移不阻断登记（登记后才能比对），
        # 但必须显式带出（no-silent-degradation）——否则「改了北京忘了上海」会静默发生。
        warnings.extend(check_class_contract(product_class, name, new_fp))

    now = _now()
    if prev is None:
        execute_write(
            "INSERT INTO adh_data_products "
            "(product_name, product_class, site, display_name, description, domain, datasource_name, catalog_name, "
            " db_name, physical_table, catalog_ref, producer_kind, producer_ref, upstream_refs, "
            " data_version, schema_hash, schema_version, compatibility, classification, tier, "
            " owner, status, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s)",
            (name, product_class, site, payload.get("display_name") or name, payload.get("description") or "",
             payload.get("domain") or "", payload.get("datasource_name") or "",
             payload.get("catalog_name") or "", payload.get("db_name") or "",
             payload.get("physical_table") or "", payload.get("catalog_ref") or "",
             producer_kind, payload.get("producer_ref") or "",
             json.dumps(payload.get("upstream_refs") or [], ensure_ascii=False),
             payload.get("data_version") or "", new_fp,
             payload.get("compatibility") or "backward",
             payload.get("classification") or "internal", payload.get("tier") or "",
             payload.get("owner") or "", status, now, now))
        if columns:
            version_record = _write_version(
                get_product(name), 1, new_fp, columns,
                {"added_columns": sorted(str(c.get("name")) for c in columns),
                 "dropped_columns": [], "changed_columns": []},
                ("backward", False), "初始登记", created_by, status="applied")
        return {"product": get_product(name), "schema_changed": False,
                "version_record": version_record, "warnings": warnings,
                "pending_approval": False}

    # 已存在：比对 schema 指纹
    old_fp = str(prev.get("schema_hash") or "")
    if old_fp and old_fp != new_fp:
        schema_changed = True
        old_cols = _load_columns_snapshot(prev["id"], int(prev.get("schema_version") or 1))
        diff = _diff_columns(old_cols, columns or [])
        compat, breaking = _judge_compatibility(diff)
        # 版本号取 **max+1** 而非 prev+1：挂起的候选可能已占了下一个版本号，
        # 若后续变更复用同号会被唯一键 ON DUPLICATE KEY 覆盖（历史 bug：
        # pending 候选被 backward 变更冲掉，待审批清单凭空消失）。
        new_version = _next_schema_version(name)

        if breaking and on_breaking != "apply":
            # 破坏性变更挂起：记 pending 候选版本，**不改产品契约**
            # （宁阻断勿破坏：下游消费者需人工确认影响面后直调 apply_contract_change）
            version_record = _write_version(
                prev, new_version, new_fp, columns or [], diff, (compat, breaking),
                "破坏性变更(挂起待确认)", created_by, status="pending")
            pending_approval = True
            warnings.append(
                f"数据产品 {name} 的破坏性 schema 变更已**挂起**（候选 v{new_version}），"
                f"未生效。删除列={diff['dropped_columns']} 变更列="
                f"{[c['name'] for c in diff['changed_columns']]}。"
                f"需人工确认影响面后调用 apply_contract_change 生效（dataset:manage）")
        else:
            execute_write(
                "UPDATE adh_data_products SET schema_hash=%s, schema_version=%s, compatibility=%s, "
                " updated_at=%s WHERE product_name=%s",
                (new_fp, new_version, compat, now, name))
            version_record = _write_version(
                get_product(name), new_version, new_fp, columns or [], diff,
                (compat, breaking), "schema 变更", created_by, status="applied")
            if breaking:
                warnings.append(
                    f"数据产品 {name} 发生破坏性 schema 变更(v{new_version}): "
                    f"删除列={diff['dropped_columns']} 变更列={[c['name'] for c in diff['changed_columns']]} "
                    f"— 下游消费者需人工确认")
    elif not old_fp and new_fp:
        # 首次有指纹：只回填，不算变更
        execute_write(
            "UPDATE adh_data_products SET schema_hash=%s, updated_at=%s WHERE product_name=%s",
            (new_fp, now, name))

    # 位置/血缘/责任人等元信息一并刷新（schema 之外的变化不产生新版本）
    execute_write(
        "UPDATE adh_data_products SET display_name=%s, description=%s, domain=%s, "
        " datasource_name=%s, catalog_name=%s, db_name=%s, physical_table=%s, catalog_ref=%s, "
        " producer_kind=%s, producer_ref=%s, upstream_refs=%s, data_version=%s, "
        " classification=%s, tier=%s, owner=%s, status=%s, updated_at=%s WHERE product_name=%s",
        (payload.get("display_name") or name, payload.get("description") or "",
         payload.get("domain") or "", payload.get("datasource_name") or "",
         payload.get("catalog_name") or "", payload.get("db_name") or "",
         payload.get("physical_table") or "", payload.get("catalog_ref") or "",
         producer_kind, payload.get("producer_ref") or "",
         json.dumps(payload.get("upstream_refs") or [], ensure_ascii=False),
         payload.get("data_version") or "",
         payload.get("classification") or prev.get("classification") or "internal",
         payload.get("tier") or "", payload.get("owner") or "", status, now, name))

    return {"product": get_product(name), "schema_changed": schema_changed,
            "version_record": version_record, "warnings": warnings,
            "pending_approval": pending_approval}


def _next_schema_version(product_name: str) -> int:
    """下一个可用 schema 版本号（取历史 max+1，避让已占用号，含 pending 候选）。"""
    row = execute_query(
        "SELECT COALESCE(MAX(schema_version), 0) AS m FROM adh_data_product_versions "
        "WHERE product_name = %s", (product_name,), fetchone=True)
    return int((row or {}).get("m") or 0) + 1


def _write_version(product: dict, version: int, fp: str, columns: list,
                   diff: dict, compat: tuple, note: str, created_by: str,
                   status: str = "applied") -> dict:
    """落一条 schema 版本记录（幂等：按 product_id+schema_version 唯一键）。

    status: applied=已生效；pending=破坏性变更挂起，待人工确认后 apply_contract_change。
    """
    if not product:
        return None
    compat_str, breaking = compat
    execute_write(
        "INSERT INTO adh_data_product_versions "
        "(product_id, product_name, schema_version, schema_hash, columns_snapshot, "
        " added_columns, dropped_columns, changed_columns, compatibility, is_breaking, "
        " status, note, created_by, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE schema_hash=VALUES(schema_hash), "
        " columns_snapshot=VALUES(columns_snapshot), added_columns=VALUES(added_columns), "
        " dropped_columns=VALUES(dropped_columns), changed_columns=VALUES(changed_columns), "
        " compatibility=VALUES(compatibility), is_breaking=VALUES(is_breaking), "
        " note=VALUES(note), status=VALUES(status)",
        (product["id"], product["product_name"], version, fp,
         json.dumps(columns or [], ensure_ascii=False),
         json.dumps(diff.get("added_columns") or [], ensure_ascii=False),
         json.dumps(diff.get("dropped_columns") or [], ensure_ascii=False),
         json.dumps(diff.get("changed_columns") or [], ensure_ascii=False),
         compat_str, 1 if breaking else 0, status, note, created_by, _now()))
    return {"product_id": product["id"], "product_name": product["product_name"],
            "schema_version": version, "schema_hash": fp,
            "compatibility": compat_str, "is_breaking": bool(breaking), "status": status,
            "added_columns": diff.get("added_columns"), "dropped_columns": diff.get("dropped_columns"),
            "changed_columns": diff.get("changed_columns"), "note": note}


def _load_columns_snapshot(product_id: int, version: int) -> list:
    """读某版本的列快照；读不到时返回空（此时差异会把所有列当新增，保守但可诊断）。"""
    row = execute_query(
        "SELECT columns_snapshot FROM adh_data_product_versions "
        "WHERE product_id = %s AND schema_version = %s",
        (product_id, version), fetchone=True)
    if not row or not row.get("columns_snapshot"):
        return []
    v = row["columns_snapshot"]
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return []
    return list(v or [])


def list_versions(product_name: str) -> list:
    rows = execute_query(
        "SELECT schema_version, schema_hash, added_columns, dropped_columns, changed_columns, "
        "       compatibility, is_breaking, status, note, created_by, created_at "
        "FROM adh_data_product_versions WHERE product_name = %s ORDER BY schema_version DESC",
        (product_name,)) or []
    out = []
    for r in rows:
        d = dict(r)
        for k in ("added_columns", "dropped_columns", "changed_columns"):
            if isinstance(d.get(k), (str, bytes)):
                try:
                    d[k] = json.loads(d[k])
                except (ValueError, TypeError):
                    d[k] = []
        if hasattr(d.get("created_at"), "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        d["is_breaking"] = bool(d.get("is_breaking"))
        out.append(d)
    return out
