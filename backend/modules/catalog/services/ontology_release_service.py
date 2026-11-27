"""P4 系统本体版本化发布服务：统一编排快照、草案、激活、对象展开、图谱与 KB 同步。

为什么需要它（口径由来）
----------------------
系统能力本体是整份 doc 由声明目录全量重算的派生对象，但它的**发布**必须像业务
本体一样可审计、可回看、可排障：

* active system model id 永远稳定（引用方（工具契约/流程/前端）不感知换版）；
* 发布前把旧 active doc 快照为 **archived 版本行**（版本内容复用模型行，不建
  第二版本表），「版本管理」视图可查可回滚激活；
* 每次发布落一行 ``adh_ontology_release_runs``：指纹前后、对象 diff、契约漂移、
  已发生的副作用、失败原因——失败**不得假回滚成"什么都没发生"**（副作用
  显式记录，正文回滚与否按 release 状态如实标注）。

用法
----
    from backend.modules.catalog.services import ontology_release_service as release
    report = release.release_system_ontology(actor="ops", dry_run=True)   # 只出 diff/漂移
    report = release.release_system_ontology(actor="ops")                 # 快照+落库+级联

边界
----
* ``allow_full_regenerate`` 只在本服务与 refresh 脚本这一受信任维护通道内使用，
  普通 API / AS-BOT 工具通道无权传入（ontology_service.save_draft 的调用点由
  源码级用例锁定）；
* ``expected_active_fingerprint`` 乐观戳：调用方钉住"我基于哪个生效版发布"，
  期间被他人改动即拒（宁阻断勿覆盖）；
* 任一步失败抛错（脚本侧非零退出），release 行留 failed + error + side_effects。
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


class ReleaseError(RuntimeError):
    """发布失败（含已完成的副作用说明，不得被调用方吞掉）。"""

    def __init__(self, message: str, *, side_effects: list[str] | None = None):
        super().__init__(message)
        self.side_effects = list(side_effects or [])


# ── 发布运行日志（adh_ontology_release_runs）────────────────────────────────────


def _conn():
    from backend.common.db.metadata_db import get_metadata_conn
    return get_metadata_conn()


def _run_start(model_id: int, actor: str, channel: str, fp_before: str,
               diff: dict, contract_drift: bool) -> int:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_ontology_release_runs "
                "(model_id, actor, channel, status, fingerprint_before, diff_json, "
                " contract_drift) VALUES (%s, %s, %s, 'running', %s, %s, %s)",
                (int(model_id), str(actor or "")[:128], str(channel or "script")[:32],
                 fp_before, json.dumps(diff, ensure_ascii=False), bool(contract_drift)))
            return int(cur.lastrowid or 0)


def _run_finish(run_id: int, *, status: str, fp_after: str = "",
                snapshot_model_id: int = 0, side_effects: list[str] | None = None,
                error: str = "") -> None:
    if not run_id:
        return
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE adh_ontology_release_runs SET status=%s, fingerprint_after=%s, "
                "snapshot_model_id=%s, side_effects=%s, error=%s, finished_at=NOW() "
                "WHERE id=%s",
                (status, fp_after, int(snapshot_model_id or 0),
                 "\n".join(side_effects or []) or None, str(error or "") or None,
                 int(run_id)))
        conn.commit()


# ── 校验与 diff（fail-loud，失败即拒）───────────────────────────────────────────


def verify_physical_columns(doc: dict) -> None:
    """列存在性校验：本体登记的每个物理列必须在库里真实存在。"""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE() AS db")
            db = (cur.fetchone() or {}).get("db")
            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=%s", (db,))
            cols: dict[str, set] = {}
            for r in cur.fetchall():
                cols.setdefault(r["TABLE_NAME"], set()).add(r["COLUMN_NAME"])
    bad: list[str] = []
    for obj in doc.get("objects") or []:
        table = str(obj.get("primary_table") or "")
        if not table:
            continue
        if table not in cols:
            bad.append(f"{obj.get('key')}: 表不存在 {table}")
            continue
        for prop in obj.get("properties") or []:
            col = str(prop.get("column") or "")
            if "." not in col:
                continue
            bare = col.split(".", 1)[1]
            if bare not in cols[table]:
                bad.append(f"{obj.get('key')}: {table}.{bare} 不存在")
    if bad:
        raise ReleaseError("本体登记了不存在的表/列（先修目录再发布）: " + "; ".join(bad[:20]))


def verify_m234(doc: dict) -> None:
    from backend.modules.catalog.services import ontology_service

    errors = [it for it in ontology_service.find_m234_issues(doc)
              if it.get("severity") == "error"]
    if errors:
        raise ReleaseError(
            "本体结构校验未通过: " + "; ".join(it.get("message", "") for it in errors[:10]))


def diff_objects(old_doc: dict, new_doc: dict) -> dict:
    """对象级 diff（key 清单 + 计数），发布报告与 dry-run 共用同一构造。"""
    old_objs = {str(o.get("key") or ""): o for o in (old_doc.get("objects") or [])}
    new_objs = {str(o.get("key") or ""): o for o in (new_doc.get("objects") or [])}
    added = sorted(set(new_objs) - set(old_objs))
    removed = sorted(set(old_objs) - set(new_objs))
    changed = sorted(
        k for k in (set(old_objs) & set(new_objs))
        if json.dumps(old_objs[k], ensure_ascii=False, sort_keys=True)
        != json.dumps(new_objs[k], ensure_ascii=False, sort_keys=True))
    return {"added": added, "removed": removed, "changed": changed,
            "old_count": len(old_objs), "new_count": len(new_objs)}


# ── 发布编排 ───────────────────────────────────────────────────────────────────


def load_active_system_model() -> dict:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, kind, status, datasource_id, json_content "
                "FROM adh_ontology_models WHERE kind='system' AND status='active'")
            rows = cur.fetchall() or []
    if not rows:
        raise ReleaseError("未找到 active 的系统本体（kind='system'）")
    if len(rows) > 1:
        raise ReleaseError(f"存在多个 active 系统本体: {[r['id'] for r in rows]}；应全局唯一")
    return rows[0]


def contract_drift_of(old_doc: dict) -> list[str]:
    """旧 active doc 与当前代码注册表的契约漂移清单（dry-run 报告用）。"""
    from backend.modules.mind.execution import capability_contracts
    try:
        return capability_contracts.validate_document_contracts(old_doc)
    except Exception as exc:  # noqa: BLE001 — 校验器自身故障也要显式带回
        return [f"契约校验器不可用: {exc}"]


def _snapshot_as_archived(model: dict, actor: str) -> int:
    """把旧 active doc 快照为 archived 版本行（版本管理视图可查可回滚激活）。

    版本内容复用模型行（同 datasource_id + name 归组），不新建第二版本表。
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_ontology_models "
                "(datasource_id, name, kind, domain, status, json_content, yaml_content, "
                " md_content, object_count, created_by, created_at, updated_at) "
                "SELECT datasource_id, name, kind, domain, 'archived', json_content, "
                " yaml_content, md_content, object_count, %s, NOW(), NOW() "
                "FROM adh_ontology_models WHERE id=%s",
                (str(actor or "")[:64], int(model["id"])))
            snapshot_id = int(cur.lastrowid or 0)
        conn.commit()
    return snapshot_id


def _object_rows(model_id: int) -> int:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM adh_ontology_objects WHERE model_id=%s",
                        (int(model_id),))
            return int((cur.fetchone() or {}).get("n") or 0)


def _retire_bindings(model_id: int) -> tuple[int, int]:
    """退役系统本体的部署绑定行（幂等）：系统对象不参与业务取数绑定。

    旧行既不随 doc 维护，又会被血缘巡检当成「对象未绑定数据产品」报假缺口，
    故统一置 deprecated 并清理重复废弃行。
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE adh_ontology_bindings SET status='deprecated', updated_at=NOW() "
                "WHERE model_id=%s AND status<>'deprecated'", (int(model_id),))
            retired = int(cur.rowcount or 0)
            cur.execute(
                "DELETE b FROM adh_ontology_bindings b JOIN ("
                "  SELECT MIN(id) AS keep_id, object_key, physical_table "
                "  FROM adh_ontology_bindings WHERE model_id=%s AND status='deprecated' "
                "  GROUP BY object_key, physical_table HAVING COUNT(*) > 1"
                ") d ON b.object_key = d.object_key AND b.physical_table = d.physical_table "
                "AND b.model_id = %s AND b.status='deprecated' AND b.id <> d.keep_id",
                (int(model_id), int(model_id)))
            deduped = int(cur.rowcount or 0)
        conn.commit()
    return retired, deduped


def release_system_ontology(*, actor: str = "", dry_run: bool = False,
                            skip_kb: bool = False, channel: str = "script",
                            expected_active_fingerprint: str | None = None) -> dict:
    """系统本体版本化发布（dry-run 只出 diff 与契约漂移；apply 快照+落库+级联）。

    返回发布报告 dict；失败抛 ``ReleaseError``（含 side_effects，不得静默）。
    """
    from backend.modules.catalog.services import ontology_service
    from backend.modules.catalog.services import system_ontology_catalog as catalog

    model = load_active_system_model()
    model_id = int(model["id"])
    try:
        old_doc = json.loads(model.get("json_content") or "{}")
    except ValueError as exc:
        raise ReleaseError(f"现有 json_content 不是合法 JSON: {exc}") from exc

    fp_before = ontology_service._doc_fingerprint(model.get("json_content") or "")
    # 乐观戳：调用方钉住的生效版指纹 ≠ 当前生效版 → 拒（期间被他人改动，宁阻断勿覆盖）
    if expected_active_fingerprint and expected_active_fingerprint != fp_before:
        raise ReleaseError(
            "生效版在本次发布准备期间已被改动，已阻断（不覆盖他人工作）；"
            "请基于最新生效版重新生成后再发布")

    drift = contract_drift_of(old_doc)
    new_doc = catalog.build_system_ontology_doc()
    verify_physical_columns(new_doc)
    verify_m234(new_doc)
    diff = diff_objects(old_doc, new_doc)

    report: dict = {
        "model_id": model_id, "model_name": str(model.get("name") or ""),
        "dry_run": bool(dry_run), "diff": diff,
        "contract_drift": drift, "contract_drift_detected": bool(drift),
        "fingerprint_before": fp_before,
        "rules": len(new_doc.get("rules") or []),
        "scenarios": len(new_doc.get("scenarios") or []),
        "actions": sum(len(o.get("actions") or []) for o in new_doc.get("objects") or []),
    }
    if dry_run:
        report["applied"] = False
        return report

    run_id = _run_start(model_id, actor, channel, fp_before, diff, bool(drift))
    side_effects: list[str] = []
    try:
        # ① 快照旧 active doc 为 archived 版本行（版本管理可回看/回滚激活）
        snapshot_id = _snapshot_as_archived(model, actor)
        side_effects.append(f"旧生效版已快照为 archived 版本行 #{snapshot_id}（可回滚激活）")

        # ② 整份重算落库 + 级联（重展开 objects / 重建图谱 / 触发 KB 同步）
        result = ontology_service.save_draft(
            model_id, json.dumps(new_doc, ensure_ascii=False),
            allow_full_regenerate=True)
        side_effects.append(
            f"生效 doc 已落库并级联（object_count={result.get('object_count')}，"
            f"图谱重建={'是' if result.get('graph_rebuild') else '未返回'}）")

        # ③ 对象展开核对：展开表行数必须与 doc 对象数一致（防半截级联）
        rows = _object_rows(model_id)
        expected = len(new_doc.get("objects") or [])
        if rows != expected:
            raise ReleaseError(
                f"对象表行数与 doc 不一致: adh_ontology_objects={rows} doc={expected}")
        side_effects.append(f"对象展开核对通过（{rows} 行）")

        # ④ 部署绑定行退役（系统对象不参与业务绑定层）
        retired, deduped = _retire_bindings(model_id)
        side_effects.append(f"部署绑定行退役 {retired} 行、清理重复废弃行 {deduped} 行")

        # ⑤ 知识库同步（失败即发布失败——知识库是检索主路，不按旁路对待）
        if skip_kb:
            side_effects.append("知识库同步已跳过（skip_kb）")
        else:
            from backend.modules.catalog.services import ontology_kb_sync
            kb = ontology_kb_sync.sync_model_to_qmind(model_id)
            side_effects.append(f"知识库同步: {json.dumps(kb, ensure_ascii=False)[:300]}")
            if (kb or {}).get("skipped") or not (kb or {}).get("synced"):
                raise ReleaseError(
                    "知识库同步未成功（详情见服务端日志与 adh_ontology_kb_sync_state）")

        fp_after = ontology_service._doc_fingerprint(new_doc and json.dumps(
            new_doc, ensure_ascii=False, sort_keys=True) or "")
        _run_finish(run_id, status="succeeded", fp_after=fp_after,
                    snapshot_model_id=snapshot_id, side_effects=side_effects)
        report.update({"applied": True, "release_run_id": run_id,
                       "snapshot_model_id": snapshot_id,
                       "side_effects": side_effects,
                       "object_rows": rows, "result": result})
        return report
    except ReleaseError as exc:
        exc.side_effects = exc.side_effects or side_effects
        _run_finish(run_id, status="failed", side_effects=side_effects,
                    error=str(exc))
        raise
    except Exception as exc:  # noqa: BLE001 — 任何失败都要落 failed 运行行
        _run_finish(run_id, status="failed", side_effects=side_effects,
                    error=str(exc))
        raise ReleaseError(f"发布失败: {exc}", side_effects=side_effects) from exc
