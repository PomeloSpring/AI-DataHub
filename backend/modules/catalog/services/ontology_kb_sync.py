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
import re
import tempfile
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# 同步文档标题前缀(按此前缀+模型名匹配旧 source 并替换)
TITLE_PREFIX = "本体模型-"

# 对外错误文本脱敏: token 形态串(pt-/jt-/qt-开头)不得出站(护栏§7)
_TOKEN_RE = re.compile(r"\b(?:pt|jt|qt)-[A-Za-z0-9_\-]{4,}")


def _safe_err(msg: str) -> str:
    """错误文本脱敏截断后随响应回传(可诊断但不泄凭据)."""
    return _TOKEN_RE.sub("***", (msg or "")).strip()[:300]


# 凭据类失败的可操作提示(提醒用户而非只丢原始 stderr; qodercn 口径)
_CRED_HINT = ("qmind 凭据未通过 Qoder 平台认证：请在 qoder.cn 账号中心"
              "(qoder.cn/account/integrations)生成有效的 Personal Access Token(pt- 开头),"
              "更新 services/.env 的 QODERCN_PERSONAL_ACCESS_TOKEN 后重启服务；"
              "或配置 QMIND_TOKEN=jt- 开头的 job token 直连")


def _cred_hint(err: str) -> str | None:
    """识别凭据无效类错误(认证失败/令牌交换被拒), 返回可操作提示."""
    e = (err or "").lower()
    if "unauthorized" in e or "not authenticated" in e or "exchange" in e:
        return _CRED_HINT
    return None


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
            from backend.common.db import execute_query
            from backend.semantics.planner import _parse_variables

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
        from backend.common.db import execute_query

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
    from backend.modules.catalog.services.ontology_service import get_model

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
            from backend.common.rdf.sparql_client import get_sparql_client
            from backend.common.rdf.namespaces import ADH_NS

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
        from backend.common.db import execute_query

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
    from backend.common.db import execute_query

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
    """按模型 kind 解析同步目标知识库（RAG 归属裁决）。"""
    if not model:
        return []
    # 归属裁决（按 kind，不用 datasource_id=0 旧口径——业务本体跨源 ds_id 也是 0）：
    #   business / system → 全局 sync_ontology 库（共用知识库，RAG 检索是两者共同用途）
    #   source            → **不做 RAG**（源本体只服务语义检索/取数，按数据源权限分配）
    kind = str(model.get("kind") or "").strip()
    if not kind:
        # 兼容无 kind 的存量模型行：旧口径 ds<=0 视为系统域
        kind = "system" if int(model.get("datasource_id") or 0) <= 0 else "source"
    if kind in ("business", "system"):
        return sync_targets()
    if kind == "source":
        return []
    return []


def list_bindable_kbs() -> list[dict]:
    """业务本体可选目标知识库清单: active qmind 且 notebook 真实。

    排除全局 sync_ontology=true 的系统知识库(只服务系统本体), 避免业务本体重误绑进系统库。
    """
    from backend.common.db import execute_query
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
    data, _err = _run_cli_ex(args)
    return data


def _run_cli_ex(args: list[str]) -> tuple[dict | None, str]:
    """qmind CLI 执行并透出 stderr 错因(供失败显式回传, 不吞成返回空)."""
    try:
        from backend.modules.mind.rag.qmind_retriever import _run_cli_ex as cli
        return cli(args)
    except Exception as e:  # noqa: BLE001
        logger.warning("[OntoSync] qmind CLI unavailable: %s", e)
        return None, f"qmind CLI unavailable: {e}"


def _delete_old_sources(notebook_id: str, title: str) -> int:
    """删除 notebook 下同名旧 source(整篇替换), 返回删除数."""
    data = _run_cli(["source", "list", "--nb", notebook_id, "--format", "json"])
    if not data:
        return 0
    removed = 0
    for s in data.get("sources") or []:
        if (s.get("title") or "") == title and s.get("id"):
            res = _run_cli(["source", "delete", "--nb", notebook_id,
                           "--force", s["id"]])
            if res is not None:
                removed += 1
    return removed


def _upload_markdown(notebook_id: str, title: str, md: str) -> tuple[bool, str]:
    """写临时 md 文件并上传为 source; 返回 (ok, err), err 为 CLI 错因透传."""
    path = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", encoding="utf-8", delete=False
        ) as f:
            f.write(md)
            path = f.name
        data, err = _run_cli_ex(["source", "upload", "--nb", notebook_id,
                                "--file", path, "--title", title, "--format", "json"])
        return (data is not None), ("" if data is not None else (err or "返回空"))
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def ensure_notebook(kb: dict) -> tuple[str | None, str]:
    """确保 kb 绑定的 notebook 对当前凭据可用, 返回 (notebook_id, err)。

    账号切换等场景下旧 notebook 对当前凭据不可用(403/不存在)时, 在当前账号
    自动重建同名 notebook 并回写绑定 —— 知识库内容全部由本项目同步生成(派生视图),
    重建后由本次/下次推送全量补齐, 不存在内容丢失。

    边界(防误建):
    - 仅「无权/不存在」类错误触发重建; 网络抖动/服务端 5xx 维持原绑定交由调用方报错;
    - 重建用 MySQL GET_LOCK 分布式互斥 + 锁内复查(并发同步只建一个),
      不用进程内防重(distributed-first);
    - 重建事件显式落日志与 source_config(previous_notebook_id/rebuilt_at),
      审计可区分正常同步与重建后同步。
    """
    from backend.modules.mind.rag.qmind_retriever import (
        probe_notebook, create_notebook, is_unavailable_error,
    )

    cfg = kb.get("cfg") or {}
    nb = cfg.get("notebook_id") or kb.get("notebook_id") or ""
    if not nb:
        return None, "知识库未绑定 notebook_id"
    ok, err = probe_notebook(nb)
    if ok:
        return nb, ""
    if not is_unavailable_error(err):
        return nb, err  # 网络/服务端异常: 不重建, 保持现状由调用方报错

    from backend.common.db.metadata_db import get_metadata_conn
    kb_id = int(kb.get("id") or 0)
    lock_name = f"adh_kb_nb_rebuild_{kb_id}"
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 30)", (lock_name,))
            if int(list((cur.fetchone() or {}).values())[0] or 0) != 1:
                return None, "知识库重建互斥锁未获得, 稍后重试"
            try:
                # 锁内复查: 并发同步可能已重建/修复, 避免重复建库
                cur.execute("SELECT name, source_config FROM adh_knowledge_bases WHERE id=%s", (kb_id,))
                row = cur.fetchone() or {}
                nb2 = _parse_cfg(row.get("source_config")).get("notebook_id") or ""
                if nb2 and nb2 != nb:
                    ok2, _ = probe_notebook(nb2)
                    if ok2:
                        return nb2, ""
                    nb = nb2
                new_nb = create_notebook(
                    row.get("name") or kb.get("name") or f"AI-DataHub-KB-{kb_id}",
                    "AI-DataHub 语义文档知识库（由本项目自动同步，重建于凭据不可用场景）",
                )
                if not new_nb:
                    return None, "自动重建知识库失败(qmind notebook create 未返回 id)"
                cfg2 = _parse_cfg(row.get("source_config"))
                cfg2["previous_notebook_id"] = nb
                cfg2["rebuilt_at"] = datetime.now(timezone.utc).isoformat()
                cfg2["notebook_id"] = new_nb
                cur.execute("UPDATE adh_knowledge_bases SET source_config=%s WHERE id=%s",
                            (json.dumps(cfg2, ensure_ascii=False), kb_id))
                conn.commit()
                # 显式声明降级/重建事实(双向声明: 服务端日志 + 落库可查)
                logger.warning("[OntoSync] 知识库 %s(%s) 的 notebook 对当前凭据不可用，"
                               "已在当前账号自动重建: %s -> %s（内容由本次同步全量重建）",
                               kb_id, row.get("name"), nb, new_nb)
                return new_nb, ""
            finally:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
    finally:
        conn.close()


def _has_active_version_with_name(name: str, datasource_id, exclude_id: int = 0) -> bool:
    """同名(同数据源)是否还存在其它 active 版本."""
    try:
        from backend.modules.catalog.services.ontology_service import list_models

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
    from backend.modules.catalog.services.ontology_service import to_cloud_md
    dict_metrics = _load_dict_metrics(int(model.get("datasource_id") or 0))

    raw = model.get("json_content") or ""
    if not raw.strip():
        return ""
    try:
        doc = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:  # noqa: BLE001
        logger.warning("[OntoSync] parse json_content failed for model=%s: %s",
                       model.get("id"), e)
        return ""
    body = to_cloud_md(doc, template_vars_fn=_make_template_vars_resolver(),
                       dict_metrics=dict_metrics)
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
    from backend.modules.catalog.services.ontology_service import get_model

    model = get_model(model_id)
    if not model:
        return {"synced": 0, "targets": [], "skipped": "model_not_found"}
    targets = sync_targets_for_model(model)
    if not targets:
        if str(model.get("kind") or "") == "source":
            # 源本体不做 RAG 是归属裁决（只服务语义检索/取数），不是配置遗漏，
            # 显式标注原因避免被当成待办告警（no-silent-degradation 反向：不制造假告警）
            return {"synced": 0, "targets": [], "skipped": "source_model_semantic_only"}
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
        try:
            nb, nb_err = ensure_notebook(kb)
            if not nb:
                last_err = nb_err or f"kb={kb['id']} notebook 不可用"
                logger.error("[OntoSync] %s", last_err)
                continue
            kb["notebook_id"] = nb  # 重建后水位线与后续引用使用新 id
            _delete_old_sources(nb, title)
            ok, up_err = _upload_markdown(nb, title, md)
            if ok:
                synced.append(kb["name"] or str(kb["id"]))
            else:
                last_err = up_err or f"upload to kb={kb['id']} failed(返回空)"
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
    result = {"synced": len(synced), "targets": synced}
    if last_err:
        # 失败必须显式可诊断(no-silent-degradation): 脱敏错因随响应回传,
        # 全部失败由 API 层转 502, 部分成功随结果附 error 供前端警示。
        result["error"] = _safe_err(last_err)
        hint = _cred_hint(last_err)
        if hint:
            result["error_hint"] = hint
    return result


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
    from backend.common.db import execute_query

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
    from backend.common.db import execute_query

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
    """启动后台对账循环(daemon 线程, 首轮先等待 interval 再跑, 避免启动即打云)。

    对账属系统内置任务 kb_sync_reconcile：支持任务监控页人工暂停，运行结果落 adh_system_jobs。
    """
    import threading
    from backend.common import system_jobs

    def _loop():
        while True:
            try:
                time.sleep(max(60, int(interval_sec)))
                if not system_jobs.is_job_active("kb_sync_reconcile"):
                    logger.info("[OntoSync] 内置任务已人工暂停，本轮对账跳过")
                    continue
                result = reconcile()
                if result.get("error"):
                    system_jobs.record_run("kb_sync_reconcile", "failed", "对账未完成，详见服务端日志")
                else:
                    system_jobs.record_run("kb_sync_reconcile", "success",
                                           f"检查 {result.get('checked', 0)} 个模型，重推 {result.get('resynced', 0)} 个")
            except Exception as e:  # noqa: BLE001 — 循环永不因单次失败退出
                logger.warning("[OntoSync] reconcile loop error: %s", e)
                system_jobs.record_run("kb_sync_reconcile", "failed",
                                       f"对账失败（{type(e).__name__}），详见服务端日志")

    threading.Thread(target=_loop, daemon=True, name="onto-kb-reconcile").start()


def _load_dict_metrics(datasource_id: int = 0) -> dict:
    """把字典口径指标（adh_metrics）按 bound_object_key 分组，供 to_cloud_md 渲染。

    为什么必须传：口径的认证状态（certified/owner）只存在于字典层；
    canonical 的 objects[].metrics 是**派生计算字段**（is_active 这类），不是口径。
    不传的话 to_cloud_md 只能渲染派生字段，认证就没有出口。

    取数失败返回空 dict（渲染时只出派生字段段，不阻断同步主链路），
    但**不静默**：调用方日志可见。
    """
    try:
        from backend.common.db import execute_query
        scope = " AND datasource_id IN (%s, 0)" if datasource_id else ""
        params = (datasource_id,) if datasource_id else ()
        rows = execute_query(
            "SELECT name, description, certified, owner, bound_object_key "
            f"FROM adh_metrics WHERE is_active = 1 AND COALESCE(bound_object_key,'') <> ''{scope}",
            params) or []
    except Exception as e:  # noqa: BLE001 — 不阻断同步主链路
        logger.warning("[OntoSync] load dict metrics failed: %s", e)
        return {}
    out: dict = {}
    for r in rows:
        out.setdefault(str(r.get("bound_object_key") or ""), []).append({
            "name": r.get("name") or "",
            "description": r.get("description") or "",
            "certified": bool(r.get("certified")),
            "owner": r.get("owner") or "",
        })
    return out
