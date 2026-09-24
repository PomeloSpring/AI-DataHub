"""本体模型 → qmind 知识库自动同步.

语义层(本体模型)保存/激活/YAML导入后, 把服务端派生的 md_content 作为知识库文档
上传到目标 qmind notebook(标题固定「本体模型-<模型名>.md」, 先删旧再传新),
使 knowledge_search 能"优先检索知识库拿到最新本体定义、信息不全再补查语义工具"。

目标知识库: adh_knowledge_bases 中 kb_type='qmind'、status='active' 且
source_config.sync_ontology=true 的条目(source_config.notebook_id 须为真实 notebook)。

失败策略: 全异步后台执行、异常只记日志, 绝不影响本体保存主链路。
"""

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# 同步文档标题前缀(按此前缀+模型名匹配旧 source 并替换)
TITLE_PREFIX = "本体模型-"


def _parse_cfg(value) -> dict:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def _make_template_vars_resolver():
    """返回 template_ref -> {var_name: decl} 的解析器(仅取声明的变量名/类型, 不取 SQL 正文)。

    失败静默返回 None(to_cloud_md 会省略参数行), 不影响同步主链路。
    """
    cache: dict[str, dict | None] = {}

    def resolve(template_ref: str):
        if not template_ref:
            return None
        if template_ref in cache:
            return cache[template_ref]
        decl: dict | None = None
        try:
            from services.shared.common.db import execute_query
            from services.shared.semantics.planner import _parse_variables

            rows = execute_query(
                "SELECT variables FROM adh_sql_templates "
                "WHERE template_id = %s AND is_active = 1 LIMIT 1",
                (template_ref,),
            )
            if rows:
                parsed = _parse_variables(rows[0].get("variables"))
                decl = parsed or None
        except Exception as e:  # noqa: BLE001
            logger.debug("[OntoSync] template vars load failed for %s: %s", template_ref, e)
            decl = None
        cache[template_ref] = decl
        return decl

    return resolve


def current_active_version(datasource_id: int) -> dict | None:
    """返回指定数据源当前生效本体的版本标识 {model_id, model_version}, 供新鲜度比对。

    model_version 与 _version_header 写入文档头部的值同源(均为 active 模型 updated_at isoformat)。
    无 active 模型/异常时返回 None(调用方据此不下 stale 结论)。
    """
    if not datasource_id:
        return None
    try:
        from services.shared.common.db import execute_query

        rows = execute_query(
            "SELECT id, updated_at FROM adh_ontology_models "
            "WHERE datasource_id = %s AND status = 'active' "
            "ORDER BY updated_at DESC LIMIT 1",
            (datasource_id,),
        )
        if not rows:
            return None
        r = rows[0]
        ua = r.get("updated_at")
        ver = ua.isoformat() if hasattr(ua, "isoformat") else str(ua or "")
        return {"model_id": r.get("id"), "model_version": ver}
    except Exception as e:  # noqa: BLE001
        logger.debug("[OntoSync] current_active_version(ds=%s) failed: %s", datasource_id, e)
        return None


def model_sync_status(model_id: int) -> dict:
    """模型的图谱/知识库同步状态(工作区头部徽章)。全程容错, 绝不抛异常。

    graph: active 模型比对 Oxigraph 命名图 owl:Class 数与 object_count
           (synced/lagging/absent); 草案=draft; 图不可达=unreachable。
    kb:    adh_ontology_kb_sync_state 水位线行(success/failed/removed);
           无行且存在开启同步的库=pending, 无库=no_kb; 表未迁移=unknown。
    """
    from services.datacatalog.services.ontology_service import get_model

    try:
        model = get_model(model_id)
    except Exception:
        model = None
    if not model:
        return {"graph": {"state": "absent"}, "kb": {"state": "absent"}}

    ds = int(model.get("datasource_id") or 0)
    obj_n = int(model.get("object_count") or 0)
    graph = {"state": "draft" if model.get("status") == "draft" else "unknown",
             "objects": obj_n}
    if model.get("status") == "active":
        try:
            from services.shared.common.rdf.sparql_client import get_sparql_client
            from services.shared.common.rdf.namespaces import ADH_NS

            rows = get_sparql_client().query(
                f"SELECT ?s WHERE {{ GRAPH <{ADH_NS}ds:{ds}> "
                "{{ ?s a <http://www.w3.org/2002/07/owl#Class> } } } LIMIT 1000")
            n = len(rows or [])
            graph = {"state": "absent" if n == 0 else ("synced" if n == obj_n else "lagging"),
                     "classes": n, "objects": obj_n}
        except Exception as e:  # noqa: BLE001 — 图服务不可达不影响徽章展示
            logger.debug("[OntoSync] graph status check failed: %s", e)
            graph = {"state": "unreachable", "objects": obj_n}

    kb: dict = {"state": "unknown"}
    kb_id = model.get("kb_id")
    targets_list: list[dict] = []
    try:
        targets_list = sync_targets_for_model(model)
    except Exception:  # noqa: BLE001
        pass
    targets = len(targets_list)
    is_business = int(model.get("datasource_id") or 0) > 0
    if is_business and not kb_id:
        # 业务本体未绑定目标库: 无论旧水位线如何都显式提示 no_kb_bound(不拿旧 success 充数)。
        kb = {"state": "no_kb_bound", "targets": 0, "kb_id": None, "target_names": []}
        return {"graph": graph, "kb": kb}
    try:
        from services.shared.common.db import execute_query

        rows = execute_query(
            "SELECT status, synced_version, synced_at, error "
            "FROM adh_ontology_kb_sync_state WHERE model_id = %s", (model_id,))
        if rows:
            r = rows[0]
            kb = {"state": str(r.get("status") or "unknown"),
                  "synced_at": str(r.get("synced_at") or ""),
                  "error": str(r.get("error") or "")[:200]}
        elif is_business and not kb_id:
            kb = {"state": "no_kb_bound"}
        else:
            kb = {"state": "no_kb" if not targets else "pending"}
    except Exception as e:  # noqa: BLE001 — 水位线表未迁移属正常
        logger.debug("[OntoSync] kb state read failed: %s", e)
        kb = {"state": "no_kb" if not targets else "unknown"}
    kb["targets"] = targets
    kb["kb_id"] = kb_id
    kb["target_names"] = [t.get("name") or str(t.get("id")) for t in targets_list]
    return {"graph": graph, "kb": kb}


def sync_targets() -> list[dict]:
    """列出开启了本体自动同步的 qmind 知识库: [{id, name, notebook_id}]."""
    from services.shared.common.db import execute_query

    try:
        rows = execute_query(
            "SELECT id, name, source_config FROM adh_knowledge_bases "
            "WHERE kb_type='qmind' AND status='active'"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[OntoSync] load knowledge bases failed: %s", e)
        return []
    out = []
    for r in rows or []:
        cfg = _parse_cfg(r.get("source_config"))
        nb = cfg.get("notebook_id")
        if cfg.get("sync_ontology") and nb and nb != "system":
            out.append({"id": r["id"], "name": r.get("name"), "notebook_id": nb})
    return out


def sync_targets_for_model(model: dict | None) -> list[dict]:
    """按模型解析同步目标知识库(业务本体不再默认落系统库)。

    - 系统本体(datasource_id 0/null): 沿用全局 sync_ontology=true 的 qmind 库(行为不变)。
    - 业务本体(datasource_id>0): 仅返回 model.kb_id 指向的 active qmind 库; 未绑定→空(不静默推系统库)。
    """
    if not model:
        return []
    if int(model.get("datasource_id") or 0) <= 0:
        return sync_targets()
    kb_id = model.get("kb_id")
    if not kb_id:
        return []
    from services.shared.common.db import execute_query
    try:
        rows = execute_query(
            "SELECT id, name, source_config FROM adh_knowledge_bases "
            "WHERE id = %s AND kb_type='qmind' AND status='active'", (int(kb_id),))
    except Exception as e:  # noqa: BLE001 — 查询失败不阻断, 视为无目标
        logger.warning("[OntoSync] per-model kb lookup failed for kb_id=%s: %s", kb_id, e)
        return []
    out = []
    for r in rows or []:
        nb = _parse_cfg(r.get("source_config")).get("notebook_id")
        if nb and nb != "system":
            out.append({"id": r["id"], "name": r.get("name"), "notebook_id": nb})
    return out


def list_bindable_kbs() -> list[dict]:
    """业务本体可选目标知识库清单: active qmind 且 notebook 真实。

    排除全局 sync_ontology=true 的系统知识库(只服务系统本体), 避免业务本体重误绑进系统库。
    """
    from services.shared.common.db import execute_query
    try:
        rows = execute_query(
            "SELECT id, name, source_config FROM adh_knowledge_bases "
            "WHERE kb_type='qmind' AND status='active' ORDER BY name")
    except Exception as e:  # noqa: BLE001
        logger.warning("[OntoSync] list bindable kbs failed: %s", e)
        return []
    out = []
    for r in rows or []:
        cfg = _parse_cfg(r.get("source_config"))
        nb = cfg.get("notebook_id")
        if nb and nb != "system" and not cfg.get("sync_ontology"):
            out.append({"id": r["id"], "name": r.get("name"), "notebook_id": nb})
    return out


def _run_cli(args: list[str]) -> dict | None:
    """qmind CLI 执行(复用 datamind 的轻量封装, 顶层仅标准库, 失败返回 None)."""
    try:
        from services.datamind.rag.qmind_retriever import _run_cli as cli
        return cli(args)
    except Exception as e:  # noqa: BLE001
        logger.warning("[OntoSync] qmind CLI unavailable: %s", e)
        return None


def _delete_old_sources(notebook_id: str, title: str) -> int:
    """删除 notebook 下同名旧 source(整篇替换), 返回删除数."""
    data = _run_cli(["source", "list", "-nb", notebook_id, "-all", "-format", "json"])
    if not data:
        return 0
    removed = 0
    for s in data.get("sources") or []:
        if (s.get("title") or "") == title and s.get("id"):
            res = _run_cli(["source", "delete", "-nb", notebook_id, s["id"]])
            if res is not None:
                removed += 1
    return removed


def _upload_markdown(notebook_id: str, title: str, md: str) -> bool:
    """写临时 md 文件并上传为 source."""
    path = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", encoding="utf-8", delete=False
        ) as f:
            f.write(md)
            path = f.name
        data = _run_cli(["source", "upload", "-nb", notebook_id,
                         "-file", path, "-title", title, "-format", "json"])
        return data is not None
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _has_active_version_with_name(name: str, datasource_id, exclude_id: int = 0) -> bool:
    """同名(同数据源)是否还存在其它 active 版本."""
    try:
        from services.datacatalog.services.ontology_service import list_models

        for m in list_models(datasource_id=datasource_id) or []:
            if m.get("status") == "active" and (m.get("name") or "") == name \
                    and int(m.get("id") or 0) != int(exclude_id or 0):
                return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[OntoSync] check active versions failed: %s", e)
    return False


def _model_version(model: dict) -> str:
    return str(model.get("updated_at") or model.get("created_at") or "")


def _version_header(model: dict) -> str:
    """在文档头部写入版本戳, 供 knowledge_search 判断新鲜度(T4 消费)。

    model_version 取模型 updated_at(每次保存/激活变化), 附 model_id/status。
    """
    ver = _model_version(model)
    synced_at = datetime.now(timezone.utc).isoformat()
    return (
        "<!-- model-version-stamp "
        f"model_id={model.get('id')} "
        f"model_version={ver} "
        f"synced_at={synced_at} "
        "-->\n\n"
    )


def _render_cloud_doc(model: dict) -> str:
    """从 canonical json_content 渲染白名单脱敏文档 + 版本戳头。

    解析失败则回落空串(调用方据此跳过, 宁可不上传也不能泄露物理细节)。
    """
    from services.datacatalog.services.ontology_service import to_cloud_md

    raw = model.get("json_content") or ""
    if not raw.strip():
        return ""
    try:
        doc = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:  # noqa: BLE001
        logger.warning("[OntoSync] parse json_content failed for model=%s: %s",
                       model.get("id"), e)
        return ""
    body = to_cloud_md(doc, template_vars_fn=_make_template_vars_resolver())
    if not body.strip():
        return ""
    return _version_header(model) + body


def sync_model_to_qmind(model_id: int) -> dict:
    """把指定本体模型的**脱敏语义文档**同步到该模型选定的目标知识库.

    上云文档经白名单渲染(剔除物理表/列名/JOIN/formula), 不再直接上传含物理信息的 md_content。
    仅同步 active 模型(草案/归档不污染知识库); 非 active 时若存在旧文档则清理。
    目标库按模型解析: 系统本体→全局 sync_ontology 库; 业务本体→model.kb_id 选定库(未绑定不同步)。
    Returns: {"synced": n, "targets": [...], "skipped": reason|None}
    """
    from services.datacatalog.services.ontology_service import get_model

    model = get_model(model_id)
    if not model:
        return {"synced": 0, "targets": [], "skipped": "model_not_found"}
    targets = sync_targets_for_model(model)
    if not targets:
        if int(model.get("datasource_id") or 0) > 0 and not model.get("kb_id"):
            return {"synced": 0, "targets": [], "skipped": "no_kb_bound"}
        return {"synced": 0, "targets": [], "skipped": "no_kb_with_sync_ontology"}
    name = model.get("name") or f"model-{model_id}"
    if (model.get("status") or "") != "active":
        # 非生效版本: 若同名模型已无其它 active 版本则下线知识库文档, 否则不动
        # (同名多版本共用一篇文档, 文档归属 active 版本; 误删会让知识库缺定义)
        if not _has_active_version_with_name(name, model.get("datasource_id"), model_id):
            return {"removed": _remove_from_targets(name, targets)["removed"],
                    "targets": [], "skipped": f"status_{model.get('status')}"}
        return {"synced": 0, "targets": [], "skipped": "superseded_by_active_version"}
    md = _render_cloud_doc(model).strip()
    if not md:
        logger.info("[OntoSync] model %s has no renderable content, skip", model_id)
        return {"synced": 0, "targets": [], "skipped": "no_md_content"}

    title = f"{TITLE_PREFIX}{name}.md"
    synced: list[str] = []
    last_err = ""
    for kb in targets:
        nb = kb["notebook_id"]
        try:
            _delete_old_sources(nb, title)
            if _upload_markdown(nb, title, md):
                synced.append(kb["name"] or str(kb["id"]))
            else:
                last_err = f"upload to kb={kb['id']} failed(返回空)"
                logger.error("[OntoSync] %s", last_err)
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:400]
            logger.error("[OntoSync] sync to kb=%s error: %s", kb["id"], e)
    logger.info("[OntoSync] model=%s title=%s synced=%s", model_id, title, synced)
    # 水位线: 成功/失败都落状态表(静默失败改为可告警记录), 表未迁移时降级为日志。
    _write_state(model_id, model.get("datasource_id") or 0,
                 (targets[0]["notebook_id"] if targets else ""),
                 _model_version(model),
                 "success" if synced else "failed",
                 "" if synced else (last_err or "no target synced"))
    return {"synced": len(synced), "targets": synced}


def _remove_from_targets(model_name: str, targets: list[dict]) -> dict:
    """从给定目标库集合删除同名文档。"""
    title = f"{TITLE_PREFIX}{(model_name or '').strip()}.md"
    removed = 0
    for kb in targets:
        try:
            removed += _delete_old_sources(kb["notebook_id"], title)
        except Exception as e:  # noqa: BLE001
            logger.warning("[OntoSync] remove from kb=%s error: %s", kb.get("id"), e)
    return {"removed": removed}


def remove_model_from_qmind(model_name: str, model: dict | None = None) -> dict:
    """模型删除/归档后, 清理目标知识库中的对应文档。

    传入 model 时按模型解析目标(业务本体只清自己绑定的库); 未传时沿用全局 sync_targets(系统库/旧调用兼容)。
    """
    targets = sync_targets_for_model(model) if model is not None else sync_targets()
    return _remove_from_targets(model_name, targets)


def retire_model_doc(name: str, datasource_id) -> dict:
    """模型物理删除后: 若同名已无 active 版本, 下线知识库文档."""
    if _has_active_version_with_name((name or "").strip(), datasource_id):
        return {"removed": 0, "skipped": "other_active_version_exists"}
    return remove_model_from_qmind(name)


def trigger_sync_async(model_id: int):
    """后台线程触发同步(不阻塞 API 响应, 异常记日志不影响主链路)."""
    import threading

    def _run():
        try:
            sync_model_to_qmind(model_id)
        except Exception as e:  # noqa: BLE001
            logger.error("[OntoSync] async sync model=%s failed: %s", model_id, e)

    threading.Thread(target=_run, daemon=True, name=f"onto-kb-sync-{model_id}").start()


# ── 同步水位线与定时对账 (T9) ─────────────────────────────────────

def _write_state(model_id, datasource_id, notebook_id, version, status, error=""):
    """写同步水位线(upsert)。状态表未迁移时仅记 debug, 不影响同步主链路。"""
    from services.shared.common.db import execute_query

    try:
        execute_query(
            "INSERT INTO adh_ontology_kb_sync_state "
            "(model_id, datasource_id, notebook_id, synced_version, status, error) "
            "VALUES (%s,%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE datasource_id=VALUES(datasource_id), "
            "notebook_id=VALUES(notebook_id), synced_version=VALUES(synced_version), "
            "status=VALUES(status), error=VALUES(error)",
            (int(model_id or 0), int(datasource_id or 0), str(notebook_id or ""),
             str(version or ""), status, str(error or "")[:512]),
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("[OntoSync] write sync state failed (未迁移?): %s", e)


def reconcile() -> dict:
    """对账: 本地 active 模型版本与同步水位不一致(含上次失败) → 重推。

    返回 {checked, resynced, skipped|error}。目标库按模型解析(sync_model_to_qmind 内部判断):
    系统本体→全局 sync_ontology 库; 业务本体→model.kb_id 选定库; 无目标时直接 skipped(不打 CLI)。
    对账本身失败不抛(不影响调用方)。
    """
    from services.shared.common.db import execute_query

    try:
        rows = execute_query(
            "SELECT m.id, m.updated_at, s.synced_version, s.status "
            "FROM adh_ontology_models m "
            "LEFT JOIN adh_ontology_kb_sync_state s ON s.model_id = m.id "
            "WHERE m.status = 'active'")
    except Exception as e:  # noqa: BLE001 — 状态表缺失等: 降级不阻断
        logger.warning("[OntoSync] reconcile query failed: %s", e)
        return {"checked": 0, "resynced": 0, "error": str(e)[:200]}

    resynced = 0
    for r in rows or []:
        ver = r["updated_at"].isoformat() if hasattr(r.get("updated_at"), "isoformat") \
            else str(r.get("updated_at") or "")
        if r.get("status") == "success" and r.get("synced_version") == ver:
            continue
        try:
            result = sync_model_to_qmind(int(r["id"]))
            if result.get("synced") or result.get("removed"):
                resynced += 1
        except Exception as e:  # noqa: BLE001
            logger.error("[OntoSync] reconcile resync model=%s failed: %s", r.get("id"), e)
    if resynced:
        logger.warning("[OntoSync] reconcile 发现漂移并重推 %d 个模型", resynced)
    return {"checked": len(rows or []), "resynced": resynced}


def start_reconciler(interval_sec: int = 600):
    """启动后台对账循环(daemon 线程, 首轮先等待 interval 再跑, 避免启动即打云)。"""
    import threading

    def _loop():
        while True:
            try:
                time.sleep(max(60, int(interval_sec)))
                reconcile()
            except Exception as e:  # noqa: BLE001 — 循环永不因单次失败退出
                logger.warning("[OntoSync] reconcile loop error: %s", e)

    threading.Thread(target=_loop, daemon=True, name="onto-kb-reconcile").start()
