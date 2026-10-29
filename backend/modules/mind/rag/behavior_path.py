"""行为路径检索：命中"关键路径 / 漏斗 / 用户指定路径"的口径声明（canonical 确定性读取）。

职责边界（与 as-bot / ontology-modeling 规范一致）：
- 路径是**口径声明 + 检索对象**，不是关联关系：planner 的 JOIN 链只认 `obj:` 前缀的
  adh:Link，`adh:Path` / `adh:PathStep` 节点天然不参与 SQL 编译；
- 本模块只回答"这条路径叫什么、有哪些有序步骤、按什么实体与窗口判定、该查哪个对象、
  能传哪些参数"，把 LLM 引到正确的 `run_semantic_query(object=..., params=...)` 上；
- 数据从 `adh_ontology_models.json_content`（canonical 事实源）读取，**不从知识库文本
  回传**（KB 可能 stale，脱敏文档不得当数据通道，护栏 §7）。

失败语义：canonical 读取异常一律抛出（调用方必须显式标注降级，不得当作"无路径"）。
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


def _norm(s) -> str:
    """名称归一：全角→半角、去空白与分隔符、casefold（与 planner._norm_name 同口径）。"""
    t = str(s or "")
    t = "".join(chr(ord(c) - 0xFEE0) if "\uFF01" <= c <= "\uFF5E" else c for c in t)
    return re.sub(r"[\s_\-/、,，>→]+", "", t).casefold()


def _dataset_hint(dataset_ref: str) -> dict:
    """数据集可用参数与可下钻字段清单（名称+类型，不含 SQL 正文）。

    只回声明层信息：数据集正文/物理表不进 LLM 视野（护栏 §7）。
    供两条通道使用：`query_dataset(name=..., args=...)` 与绑定该数据集的
    本体对象 `run_semantic_query(object=..., params=...)`。
    """
    ref = str(dataset_ref or "").strip()
    if not ref:
        return {}
    try:
        from backend.modules.viz.services import dataset_service

        row = (dataset_service.get_dataset_by_name(ref)
               if not ref.isdigit() else dataset_service.get_dataset(int(ref)))
    except Exception as e:  # noqa: BLE001 — 提示不可用不毁掉路径检索，但必须可诊断
        logger.info("[behavior_path] 数据集提示不可用 %s: %s", ref, e)
        return {}
    if not row:
        return {}
    from backend.semantics.sql_params import parse_json_obj, parse_variables

    out: dict = {"dataset": str(row.get("name") or ref)}
    decls = parse_variables(row.get("variables"))
    if decls:
        out["params"] = {k: str((v or {}).get("type") or "") for k, v in decls.items()}
    cols = parse_json_obj(row.get("field_config"), []) or []
    if isinstance(cols, list) and cols:
        out["dimensions"] = [str(c.get("label") or c.get("field")) for c in cols
                             if isinstance(c, dict) and c.get("role") != "measure"]
        out["metrics"] = [str(c.get("label") or c.get("field")) for c in cols
                          if isinstance(c, dict) and c.get("role") == "measure"]
    out["certified"] = bool(row.get("certified") or 0)
    # 可用数据源范围 > 1 时才附站点提示（单源不加噪声，站点映射只在服务端）：
    # 否则 LLM 看见多源数据集却不知道怎么定源，只能靠报错回抛候选。
    if parse_json_obj(row.get("available_datasources"), []) or []:
        src_names = dataset_service.available_source_names(row)
        if len(src_names) > 1:
            out["available_datasources"] = src_names
    return out


def _path_entry(scn: dict, path: dict, model_name: str) -> dict:
    steps = []
    for i, st in enumerate(path.get("steps") or [], start=1):
        if not isinstance(st, dict):
            continue
        try:
            order = int(st.get("order") or i)
        except (TypeError, ValueError):
            order = i
        steps.append({
            "order": order,
            "name": str(st.get("name") or st.get("key") or ""),
            "key": str(st.get("key") or ""),
            "event": str(st.get("event") or ""),
            "object_key": str(st.get("object_key") or ""),
        })
    steps.sort(key=lambda s: s["order"])
    entry = {
        "path_key": str(path.get("key") or scn.get("key") or ""),
        "title": str(path.get("title") or scn.get("title") or ""),
        "scenario_key": str(scn.get("key") or ""),
        "ontology": model_name,
        "steps": [s["name"] for s in steps],
        "step_details": steps,
        "related_objects": sorted({str(o).strip() for o in (scn.get("uses_objects") or [])
                                   if str(o or "").strip()}),
        "entity": str(path.get("entity") or ""),
        "event_time": str(path.get("event_time") or ""),
        "aliases": [str(a) for a in (path.get("aliases") or [])],
    }
    win = str(path.get("window_sec") or "").strip()
    if win.isdigit():
        entry["window_sec"] = int(win)
    # 查询落点：绑定预制 SQL 的数据集（名称供 query_dataset / run_semantic_query 使用）
    ds_ref = str(path.get("dataset") or path.get("dataset_ref") or "").strip()
    if ds_ref:
        hint = _dataset_hint(ds_ref)
        if hint:
            entry["dataset_hint"] = hint
    obj_ref = str(path.get("query_object") or "").strip()
    if obj_ref:
        entry["query_object"] = obj_ref
    return entry


def collect_behavior_paths() -> list:
    """全量行为路径清单（active 本体，排除系统本体域）。

    系统运营问题不得拿业务路径充数（as-bot 域规则）：kind='system' 一律不读。
    源本体已退役（WS2 合并为 business）→ 统一读非 system（kind <> 'system'）。
    """
    from backend.common.db.metadata_db import get_metadata_conn

    out: list = []
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, json_content FROM adh_ontology_models "
                "WHERE status = 'active' AND kind <> 'system'")
            rows = cur.fetchall()
    finally:
        conn.close()
    for row in rows:
        try:
            doc = json.loads(row.get("json_content") or "{}")
        except (ValueError, TypeError):
            logger.warning("[behavior_path] 模型 %s json_content 解析失败，跳过", row.get("name"))
            continue
        for scn in (doc.get("scenarios") or []):
            path = scn.get("path")
            if isinstance(path, dict) and (path.get("steps") or []):
                out.append(_path_entry(scn, path, str(row.get("name") or "")))
    return out


def resolve_behavior_paths(object_keys: list = None, keywords: list = None) -> list:
    """按命中对象 key / 业务关键词解析行为路径（宁缺勿错，不猜路径）。

    Returns:
        [{path_key, title, steps[], step_details[], entity, event_time, window_sec,
          query_object?, template_hint?, ontology}]

    命中规则：① 路径相关对象（场景 uses_objects / 步骤 object_key / 查询落点对象）
    与传入对象 key 相交；② 关键词与 path_key/title/aliases/步骤业务名 归一后互为子串。
    返回 [] = 确实无匹配路径；canonical 读取失败**抛异常**（调用方须显式标注降级）。
    """
    keys = {_norm(k) for k in (object_keys or []) if str(k).strip()}
    words = [w for w in (_norm(k) for k in (keywords or []) if str(k).strip()) if w]
    paths = collect_behavior_paths()
    if not keys and not words:
        return []
    matched = []
    for p in paths:
        hit = False
        obj_refs = ({_norm(o) for o in p.get("related_objects") or []}
                    | {_norm(s.get("object_key")) for s in p.get("step_details") or []
                       if s.get("object_key")}
                    | ({_norm(p.get("query_object"))} if p.get("query_object") else set()))
        obj_refs.discard("")
        if keys and (obj_refs & keys):
            hit = True
        if not hit and words:
            hayl = [_norm(x) for x in
                    [p.get("path_key"), p.get("title"), p.get("scenario_key")]
                    + list(p.get("aliases") or []) + list(p.get("steps") or [])
                    if str(x or "").strip()]
            for w in words:
                if any(w in h or h in w for h in hayl if h):
                    hit = True
                    break
        if hit:
            matched.append(p)
    return matched
