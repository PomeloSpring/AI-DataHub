"""快速路径（Quick Path）匹配 —— 知识管理的「查询方案指引」按用户问题命中并注入 LLM 上下文。

设计（对齐 skill_loader / prompt_composer 的技能注入范式）：
- 识别机制：**触发词 / 关键词子串匹配**（服务端、确定性、零额外 LLM 调用）。
  每条快速路径在 ``metadata.triggers`` 登记若干触发词，用户问题命中的词越多越优先。
- 注入方式：命中路径的「查询方案说明」（``content``）作为独立小节注入系统提示词
  （见 ``prompt_composer.compose_system_prompt``），让 LLM 知道该类问题应如何取数。

数据表复用 ``adh_knowledge_items``（``knowledge_type='quick_path'``）：
  title            = 路径名称
  content          = 查询方案说明（注入给 LLM 的指引正文）
  metadata.triggers= 触发词清单 [{..}] / ["词1","词2"]
  metadata.scenario= 适用场景（可选，仅管理页展示）
  related_tables   = 关联表（可选，附注）
  priority / datasource_id / workspace_id / is_active 沿用既有语义。

命中为「增强项」而非取数主链路：加载/匹配异常时返回空（不注入），仅告警，
不阻断正常问答（与 prompt_composer 中 RLS 摘要 / 屏蔽列等旁路补充一致）。
"""
from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

# ── TTL 缓存（默认 60s，热改管理页配置后无需重启即生效）────────────────
_CACHE_TTL = float(os.getenv("QUICK_PATH_CACHE_TTL", "60"))
_rows_cache: tuple[float, list[dict]] | None = None


def clear_cache() -> None:
    """失效缓存（管理页保存后调用可立即生效）。"""
    global _rows_cache
    _rows_cache = None


def _normalize(row: dict) -> dict:
    """DB 行 → 命中用结构：解析 metadata.triggers / scenario。"""
    meta = row.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    meta = meta if isinstance(meta, dict) else {}
    triggers = meta.get("triggers") or []
    if isinstance(triggers, str):
        triggers = [t.strip() for t in triggers.split(",")]
    # 触发词补充：路径名称本身也作为弱触发（整词命中）
    clean = [str(t).strip() for t in triggers if str(t).strip()]
    return {
        "id": row.get("id"),
        "title": row.get("title") or "",
        "content": row.get("content") or "",
        "scenario": (meta.get("scenario") or "").strip(),
        "triggers": clean,
        "related_tables": row.get("related_tables") or "",
        "priority": int(row.get("priority") or 0),
        "workspace_id": int(row.get("workspace_id") or 0),
        "datasource_id": int(row.get("datasource_id") or 0),
    }


def _load_rows() -> list[dict]:
    """加载全部启用中的快速路径行（TTL 缓存）。失败返回空并告警（不阻断）。"""
    global _rows_cache
    if _rows_cache is not None:
        ts, rows = _rows_cache
        if time.time() - ts <= _CACHE_TTL:
            return rows
    rows: list[dict] = []
    try:
        from backend.common.db import get_metadata_conn

        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, workspace_id, datasource_id, title, content,
                              metadata, related_tables, priority
                       FROM adh_knowledge_items
                       WHERE knowledge_type = 'quick_path' AND is_active = 1"""
                )
                rows = [_normalize(r) for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        logger.warning("[QuickPath] 加载快速路径失败（本次不注入）: %s", e)
        rows = []
    _rows_cache = (time.time(), rows)
    return rows


def _in_scope(path: dict, workspace_id: int, datasource_id: int) -> bool:
    """作用域过滤：workspace 0=全局；datasource 0=全局，命中源或全局。

    路径限定某工作空间（workspace_id>0）而当前为另一工作空间时不匹配；
    当前无可识别工作空间（workspace_id=0）时只放行全局路径。
    """
    pw = path["workspace_id"]
    if pw and pw != (workspace_id or 0):
        return False
    if path["datasource_id"] and path["datasource_id"] != (datasource_id or 0):
        return False
    return True


def _score(path: dict, q_norm: str) -> tuple[int, list[str]]:
    """触发词命中打分：命中的触发词个数 + 名称整词命中加权。"""
    if not q_norm:
        return 0, []
    hits: list[str] = []
    for t in path["triggers"]:
        if t and t.lower() in q_norm:
            hits.append(t)
    score = len(hits)
    title = (path["title"] or "").strip().lower()
    if title and title in q_norm:
        score += 2  # 路径名称整词命中权重最高
    return score, hits


def match_quick_paths(
    question: str,
    workspace_id: int = 0,
    datasource_id: int = 0,
    limit: int = 3,
) -> list[dict]:
    """按用户问题命中快速路径。

    Returns: 命中路径列表（按得分/优先级降序），每项
      ``{title, content, scenario, related_tables, matched_triggers}``；无命中返回 ``[]``。
    """
    q_norm = (question or "").strip().lower()
    if not q_norm:
        return []
    scored: list[tuple[int, int, dict, list[str]]] = []
    for path in _load_rows():
        if not path["content"]:
            continue
        if not _in_scope(path, workspace_id, datasource_id):
            continue
        score, hits = _score(path, q_norm)
        if score <= 0:
            continue
        scored.append((score, path["priority"], path, hits))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out: list[dict] = []
    for score, _prio, path, hits in scored[:limit]:
        out.append({
            "title": path["title"],
            "content": path["content"],
            "scenario": path["scenario"],
            "related_tables": path["related_tables"],
            "matched_triggers": hits,
        })
    return out
