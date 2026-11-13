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
  metadata.report_template= 绑定的**报告模板业务名**（可选；命中该场景时平台把模板版式
  写成契约注入提示词。存名字不存字模 id —— 模型与管理页都不该拿 id 当口径）
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
    """DB 行 → 命中用结构：解析 metadata.triggers / scenario / subjects。"""
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
    # 主体绑定（场景可见性的唯一判据）：与 scenario_access 同一份解析，不在此另写一套
    from backend.core.scenario_access import scenario_subjects
    subjects, subject_all = scenario_subjects(meta)
    return {
        "id": row.get("id"),
        "title": row.get("title") or "",
        "content": row.get("content") or "",
        "scenario": (meta.get("scenario") or "").strip(),
        # 场景绑定的报告模板（业务名）：由 prompt_composer 在命中时解析成版式契约
        "report_template": (meta.get("report_template") or "").strip(),
        "triggers": clean,
        "subjects": subjects,
        "subject_all": subject_all,
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


def _subject_ok(path: dict, allowed_subjects: set[str] | None) -> bool:
    """主体门控：`allowed_subjects=None` = 调用方显式声明不做主体裁决。

    传集合时按「不限主体」或「与可见主体有交集」放行；**空集 = 只有不限主体的场景可用**
    （fail-closed，不把空授权当全量）。没登记主体又没勾不限主体的场景一律不命中。
    """
    if allowed_subjects is None:
        return True
    if path.get("subject_all"):
        return True
    keys = path.get("subjects") or []
    return bool(keys) and bool(set(keys) & set(allowed_subjects))


def match_quick_paths(
    question: str,
    workspace_id: int = 0,
    datasource_id: int = 0,
    limit: int = 3,
    allowed_subjects: set[str] | None = None,
) -> list[dict]:
    """按用户问题命中快速路径（分析场景）。

    Args:
        allowed_subjects: 当前身份可见的主体（object_key）集合；None = 不做主体裁决。
            **生产调用点必须显式传**（secure_sdk / scheduled_analysis / chat 候选三处同源），
            否则就是“靠触发词把别人主体的场景正文带进提示词”的越权信息面。
            这条由 tests/test_scenario_subject_access.py 源码级护栏锁定。

    Returns: 命中路径列表（按得分/优先级降序），每项
      ``{id, title, content, scenario, related_tables, subjects, matched_triggers}``；无命中返回 ``[]``。
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
        if not _subject_ok(path, allowed_subjects):
            continue
        score, hits = _score(path, q_norm)
        if score <= 0:
            continue
        scored.append((score, path["priority"], path, hits))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out: list[dict] = []
    for score, _prio, path, hits in scored[:limit]:
        out.append(_hit_payload(path, hits))
    return out


def _hit_payload(path: dict, hits: list[str], pinned: bool = False) -> dict:
    """命中项载荷。

    `pinned=True` 表示**用户在输入框用 `/` 显式点名**了这个场景（不是触发词猜中的）。
    这个区分必须一路带到提示词：合成一份列表后，显式指定会被模型当成“服务端顺带提个
    建议”，问题一含糊就回落到通用反问 —— 用户点了场景却没人按它做（历史缺陷）。
    """
    return {
        "id": path.get("id"),
        "title": path["title"],
        "content": path["content"],
        "scenario": path["scenario"],
        "report_template": path.get("report_template") or "",
        "related_tables": path["related_tables"],
        "subjects": path.get("subjects") or [],
        "matched_triggers": hits,
        "pinned": bool(pinned),
    }


def paths_by_ids(
    ids: list,
    allowed_subjects: set[str] | None = None,
    limit: int = 3,
    max_requested: int = 5,
) -> tuple[list[dict], list[dict]]:
    """按用户显式选中的场景 id 取方案（`/` 面板选场景 → 不看触发词也强制注入）。

    不静默丢弃：被拒的 id 连同原因一并回传（护栏 no-silent-degradation）。
    `limit` 只封顶**注入份数**，不封顶待评定的 id 数 —— 旧写法先截断再判定，
    第 4 个选中项会连原因一起消失（用户以为选了，实际没人处理它）。超出
    `max_requested` 的部分归并为一条“一次最多用 N 个场景”的拒因，仍然可见。
    """
    wanted = [str(i).strip() for i in (ids or []) if str(i).strip()]
    if not wanted:
        return [], []
    overflow = [{"id": wid, "title": "", "reason": f"一轮最多使用 {limit} 个分析场景，本项未生效"
                 } for wid in wanted[max_requested:]]
    wanted = wanted[:max_requested]
    by_id = {str(p["id"]): p for p in _load_rows()}
    accepted: list[dict] = []
    rejected: list[dict] = []
    for wid in wanted:
        path = by_id.get(wid)
        if not path:
            rejected.append({"id": wid, "title": "", "reason": "场景不存在或已停用"})
            continue
        if not path["content"]:
            rejected.append({"id": wid, "title": path["title"], "reason": "场景正文为空，无可注入的方案"})
            continue
        if not _subject_ok(path, allowed_subjects):
            from backend.core.scenario_access import is_scenario_visible
            _ok, reason = is_scenario_visible({"subjects": path.get("subjects") or [],
                                               "subject_all": bool(path.get("subject_all"))},
                                              allowed_subjects or set())
            rejected.append({"id": wid, "title": path["title"],
                             "reason": reason or "当前身份无权使用该场景"})
            continue
        if len(accepted) >= limit:
            rejected.append({"id": wid, "title": path["title"],
                             "reason": f"一轮最多注入 {limit} 个场景方案，本项未生效"})
            continue
        # pinned 标记随载荷走：提示词要按“用户指定”与“触发词命中”分档措辞
        accepted.append(_hit_payload(path, [], pinned=True))
    return accepted, rejected + overflow


def list_visible_paths(allowed_subjects: set[str] | None = None,
                       workspace_id: int = 0, datasource_id: int = 0,
                       limit: int = 50) -> tuple[list[dict], int]:
    """当前身份可见的启用场景清单（`/` 面板候选）。

    Returns ``(items, hidden_count)``：hidden_count = 因主体无权/未绑主体而被隐去的条数，
    调用方得把它告诉用户（“不是没配场景，是那些场景的主体你没权限”），不能静默少返。
    """
    rows = [p for p in _load_rows() if p["content"] and _in_scope(p, workspace_id, datasource_id)]
    visible = [p for p in rows if _subject_ok(p, allowed_subjects)]
    visible.sort(key=lambda p: (-int(p.get("priority") or 0), (p.get("title") or "")))
    # 字段名跟前端契约一致：候选标识叫 `key`（叫 id 的话前端读 s.key=undefined，
    # 同一组内所有项的 React key 撞成一个，来回切类别就渲染出重复行与 Esc 关不掉的残影）。
    # subjects 是内部 object_key，对候选列表没意义且属实现细节，不下发。
    return ([{"key": str(p["id"]), "name": p["title"],
              "description": p["scenario"] or _short(p["content"])} for p in visible[:limit]],
            max(0, len(rows) - len(visible)))


def _short(text: str) -> str:
    """没填「适用场景」时用正文首行做候选描述（不拿整段正文当副标题刷屏）。"""
    first = next((ln.strip().lstrip("#").strip() for ln in (text or "").splitlines()
                  if ln.strip()), "")
    return first[:60]
