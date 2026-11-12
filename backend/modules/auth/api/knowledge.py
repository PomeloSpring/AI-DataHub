"""Knowledge API — 语义层语料管理（分析场景 / 推荐问题 / 追问案例）。

挂载: 管理面 ``/api/admin/knowledge``（auth 组）; 消费面 ``/api/knowledge``（public_router）。
数据表: adh_knowledge_items —— **一张表多种 knowledge_type**，按类型归不同权限码。

命名口径（用户 2026-11-04 拍板）: knowledge_type 的内部枚举值仍是 ``quick_path``，
用户可见名叫**分析场景** —— 它的实质是"用 markdown 描述的场景化查询步骤"（对已建模的
对象/指标/维度的编排），不是字面意义的"路径"。改枚举值要连带动 4 处消费方与存量数据，
收益只是命名统一，所以只改可见名、不改值。

类型级归属（为什么在路由层再校一次）: 中间件按 api_pattern × api_method 裁决"这条路径
能不能调"，而各类型共用同一个路径，pattern 分不出类型。因此这里只补**"是哪一类对象"**
的判断（quick_path→knowledge:*），用的仍是
``adh_user_roles ⋈ adh_role_perms ⋈ adh_perm_registry`` 这一唯一真值源，不另造权限体系。

消费面为什么单独一条路由: Chat 空白页与首页要展示场景示例，那是面向全员的内容读取
（与 ``/api/release-notes`` 同构）；走管理面就得让所有角色都勾 knowledge:read，等于把
语义层配置页的读权限塞给每个用户。故消费面只回标题 + 适用场景（不含正文、不含物理表列名）。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.common.auth import get_current_user
from backend.common.db import get_metadata_conn

logger = logging.getLogger(__name__)
router = APIRouter()
public_router = APIRouter()

# knowledge_type → (读码, 写码)。recommend_question / followup_case 沿用 knowledge:*:
# 同属语义层语料、影响面相同（都会进提示词或推荐位），不为其另开码。
TYPE_PERMS = {
    "quick_path": ("knowledge:read", "knowledge:manage"),
    "recommend_question": ("knowledge:read", "knowledge:manage"),
    "followup_case": ("knowledge:read", "knowledge:manage"),
}
TYPE_LABELS = {
    "quick_path": "分析场景",
    "recommend_question": "推荐问题", "followup_case": "追问案例",
}
# 已下线的类型（用户 2026-11-05 拍板：与**数据集**重合 —— 问答 SQL 的沉淀单位就是数据集，
# 且 sql_pair 行没有任何取数/提示词消费方，配了也不进 LLM，是个空承诺的配置面）。
# 单独登记而不是删字典了事: 旧调用方/书签仍会带这个 type 过来，必须给可读的“已下线”原因，
# 而不是当成“未知类型”让人以为是配置错。存量行保留在表里但不再有任何读路径。
RETIRED_TYPES = {
    "sql_pair": "SQL 示例对",
}


def _held_codes(user: dict) -> set:
    """用户经角色持有的权限码（唯一真值源，与中间件/AS-BOT 同一口径）。

    admin 恒放行 —— 与 ``check_api_permission`` 对 role='admin' 的处理一致，不是本模块
    新增特权；否则补了类型校验反而会把超级用户挡在门外。
    """
    if not isinstance(user, dict):
        return set()
    if str(user.get("role") or "") == "admin":
        return {"*"}
    uid = int(user.get("user_id") or 0)
    if not uid:
        return set()
    try:
        from backend.core.role_service import role_service
        return set(role_service.get_user_role_perm_codes(uid))
    except Exception as exc:  # noqa: BLE001 — 拿不到权限就当无权限（fail-closed），不得默认放开
        logger.warning("[knowledge] 读取角色权限码失败, 按无权限处理: %s", exc)
        return set()


def _assert_type_perm(user: dict, knowledge_type: str, *, write: bool = False) -> None:
    """按 knowledge_type 校该类型的读/写码；不过就 403 并说清缺哪个码。"""
    ktype = str(knowledge_type or "").strip()
    if ktype in RETIRED_TYPES:
        raise HTTPException(
            status_code=410,
            detail=f"「{RETIRED_TYPES[ktype]}」已下线（与数据集重合），请改用「数据集」沉淀问答 SQL")
    codes = _held_codes(user)
    if "*" in codes:
        return
    pair = TYPE_PERMS.get(ktype)
    if not pair:
        raise HTTPException(status_code=400, detail=f"未知的 knowledge_type: {ktype}")
    need = pair[1] if write else pair[0]
    if need in codes or f"{need.split(':')[0]}:*" in codes:
        return
    raise HTTPException(
        status_code=403,
        detail=f"要{'编辑' if write else '查看'}「{TYPE_LABELS.get(ktype, ktype)}」"
               f"需要「{need}」权限，当前角色未授予该权限")


def _readable_types(user: dict) -> list:
    """未显式传 knowledge_type 时，把结果收窄到调用方可读的类型。

    不收窄就会出现: 只勾了某一类语料的角色，用一个不带 type 的管理页请求把其他类型
    一起拉走 —— 路径能调不等于所有类型都能看。
    """
    codes = _held_codes(user)
    if "*" in codes:
        return list(TYPE_PERMS)
    return [t for t, (rc, _wc) in TYPE_PERMS.items()
            if rc in codes or f"{rc.split(':')[0]}:*" in codes]


# ── 分析场景的主体归属（配置面校验 + 可见性标注）─────────────────────────────

def _assert_scenario_subjects(ktype: str, metadata: dict | None) -> None:
    """分析场景（quick_path）必须显式声明主体范围：要么选至少一个主体，要么勾「不限主体」。

    为什么不默认兼容：消费侧按主体授权是 fail-closed 的（未绑主体且未勾不限主体 = 不可见、
    不命中），若这里不拦，管理员配完场景会在 `/` 面板和问答里都看不到它，只能归为
    “配了没生效”的缺陷。把错误拦在写入点比拦在运行时可诊断（no-silent-degradation）。
    """
    if str(ktype or "") != "quick_path":
        return
    from backend.core.scenario_access import scenario_subjects, subject_catalog

    keys, all_visible = scenario_subjects(metadata or {})
    if all_visible and not keys:
        return
    if not keys:
        raise HTTPException(status_code=422,
                            detail="分析场景需要至少绑定一个主体（本体对象），或勾选「不限主体」。"
                                   "未声明主体的场景对所有角色都不会命中，也不会出现在 / 候选。")
    known = {c.get("key") for c in subject_catalog()}
    unknown = [k for k in keys if k not in known]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="分析场景绑定的主体不存在或已停用：" + "、".join(unknown)
                   + "（请重新选择主体；主体清单来自启用中的本体模型）")


def _scenario_visibility(rows: list, user: dict) -> None:
    """给管理面清单补上「主体业务名」与「当前身份是否看得见/为什么看不见」。

    管理员需要知道：这条场景对所有人生效、只对有某主体权限的角色生效，还是因为
    没绑主体而谁也看不到 —— 这些都是配置事实，不是运行时才能发现的意外。
    """
    from backend.core import scenario_access

    try:
        allowed = scenario_access.visible_subject_keys(
            int(user.get("user_id") or 0), 0, role=str(user.get("role") or ""),
            system_scope=True)      # 管理面按“能力上限”展示可见性，系统能力面由 AS-BOT 配
    except Exception as e:  # noqa: BLE001 — 可见性补充失败不能拖死清单（降级为未标注）
        logger.warning("[knowledge] 场景主体可见性计算失败: %s", e)
        return
    for r in rows:
        if r.get("knowledge_type") != "quick_path":
            continue
        meta = r.get("metadata") or {}
        keys, all_visible = scenario_access.scenario_subjects(meta)
        r["subject_keys"] = keys
        r["subject_all"] = all_visible
        r["subject_names"] = scenario_access.subject_display_names(keys) if keys else (
            ["不限主体"] if all_visible else [])
        ok, reason = scenario_access.is_scenario_visible(meta, allowed)
        r["visible_to_me"] = ok
        r["visibility_note"] = "" if ok else reason


# ── Pydantic models ────────────────────────────────────────────────

class KnowledgeCreate(BaseModel):
    workspace_id: int = 0
    datasource_id: int = 0
    knowledge_type: str  # quick_path / recommend_question / followup_case
    title: str
    content: str
    metadata: Optional[dict] = None
    related_tables: str = ""
    priority: int = 0
    is_active: int = 1


class KnowledgeUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    metadata: Optional[dict] = None
    related_tables: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[int] = None


# ── Helpers ────────────────────────────────────────────────────────

def _serialize_row(row: dict) -> dict:
    """把 metadata JSON 字符串解析为 dict。"""
    out = dict(row)
    if isinstance(out.get("metadata"), str):
        try:
            out["metadata"] = json.loads(out["metadata"])
        except (json.JSONDecodeError, TypeError):
            out["metadata"] = None
    return out


def _invalidate_quick_paths() -> None:
    """分析场景写操作后失效命中缓存，使配置立即生效。

    best-effort: 缓存没清只是"下一次仍用旧场景"，不影响写入本身，因此不阻断；
    但必须留日志，否则配置改了没生效会无从查起。
    """
    try:
        from backend.modules.mind.execution.quick_paths import clear_cache
        clear_cache()
    except Exception as e:  # noqa: BLE001
        logger.warning("[Knowledge] 分析场景缓存未失效（配置将按旧缓存命中一次）: %s", e)


def _item_type(cur, item_id: int) -> str:
    """取条目所属类型（改/删前要用它判类型级归属，不能信请求体）。"""
    cur.execute("SELECT knowledge_type FROM adh_knowledge_items WHERE id = %s", (item_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="条目不存在")
    return str(row["knowledge_type"])


# ── 消费面（面向全员, 只读, 只回标题与适用场景）─────────────────────────────

@public_router.get("/scenarios")
def list_scenarios_for_chat(
    workspace_id: int = Query(0),
    datasource_id: int = Query(0),
    limit: int = Query(4, ge=1, le=10),
):
    """Chat 空白页 / 首页的「分析场景」示例。

    只回 ``title`` 与 ``metadata.scenario``（适用场景一句话）—— 正文里有对象与指标的
    编排细节，属配置面内容，不下发给无 knowledge:read 的用户。
    """
    from backend.common.ttl_cache import workspace_cache
    cache_key = f"scenarios:{workspace_id}:{datasource_id}:{limit}"
    cached = workspace_cache.get(cache_key)
    if cached is not None:
        return cached

    where = ["knowledge_type = 'quick_path'", "is_active = 1"]
    params: list = []
    if workspace_id:
        where.append("(workspace_id = %s OR workspace_id = 0)")
        params.append(workspace_id)
    if datasource_id:
        where.append("(datasource_id = %s OR datasource_id = 0)")
        params.append(datasource_id)

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT id, title, metadata FROM adh_knowledge_items
                    WHERE {' AND '.join(where)}
                    ORDER BY priority DESC, created_at DESC LIMIT %s""",
                params + [limit],
            )
            rows = cur.fetchall()

    items = []
    for r in rows:
        meta = _serialize_row(r).get("metadata") or {}
        items.append({"id": str(r["id"]), "title": r["title"],
                      "scenario": str(meta.get("scenario") or "")})
    result = {"items": items}
    workspace_cache.set(cache_key, result)
    return result


# ── 管理面（按类型归属裁决）────────────────────────────────────────────────

@router.get("/knowledge")
def list_knowledge(
    workspace_id: int = Query(0),
    knowledge_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    """知识条目列表（分页）。传了 type 就校该类型的读码，没传则收窄到可读类型。"""
    where = ["1=1"]
    params: list = []

    if knowledge_type:
        _assert_type_perm(user, knowledge_type)
        where.append("knowledge_type = %s")
        params.append(knowledge_type)
    else:
        readable = _readable_types(user)
        if not readable:
            raise HTTPException(status_code=403,
                                detail="查看语义层语料需要「查看分析场景」权限")
        where.append(f"knowledge_type IN ({','.join(['%s'] * len(readable))})")
        params.extend(readable)

    if workspace_id:
        where.append("workspace_id = %s")
        params.append(workspace_id)
    if search:
        where.append("(title LIKE %s OR content LIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])

    where_sql = " AND ".join(where)

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) as total FROM adh_knowledge_items WHERE {where_sql}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"""SELECT * FROM adh_knowledge_items WHERE {where_sql}
                    ORDER BY priority DESC, created_at DESC
                    LIMIT %s OFFSET %s""",
                params + [size, offset],
            )
            rows = [_serialize_row(r) for r in cur.fetchall()]

    _scenario_visibility(rows, user)
    return {"items": rows, "total": total, "page": page, "size": size}


@router.get("/knowledge/subjects")
def list_scenario_subjects(user: dict = Depends(get_current_user)):
    """分析场景可绑定的主体清单（本体对象）：管理页「主体」选择框的唯一数据源。

    只给业务名与内部 key（选择框显示 name、value 用 key，不让人手输 object_key）；
    配场景需要这个码才能拿到清单，所以按 quick_path 的读码裁决。
    """
    _assert_type_perm(user, "quick_path")
    from backend.core.scenario_access import subject_catalog

    return {"items": [{"key": c.get("key"), "name": c.get("display_name") or c.get("key"),
                       "model_name": c.get("model_name") or "", "kind": c.get("kind") or ""}
                      for c in subject_catalog() if c.get("key")]}


@router.get("/knowledge/random")
def random_knowledge(
    knowledge_type: str = Query("recommend_question"),
    limit: int = Query(3, ge=1, le=10),
    workspace_id: int = Query(0),
    datasource_id: int = Query(0),
    user: dict = Depends(get_current_user),
):
    """随机获取 N 条知识条目（管理面）。Chat/首页请走 ``/api/knowledge/scenarios``。"""
    _assert_type_perm(user, knowledge_type)
    from backend.common.ttl_cache import workspace_cache
    cache_key = f"rand:{knowledge_type}:{workspace_id}:{datasource_id}:{limit}"
    cached = workspace_cache.get(cache_key)
    if cached is not None:
        return cached

    where = ["knowledge_type = %s", "is_active = 1"]
    params: list = [knowledge_type]

    if workspace_id:
        where.append("workspace_id = %s")
        params.append(workspace_id)
    if datasource_id:
        where.append("(datasource_id = %s OR datasource_id = 0)")
        params.append(datasource_id)

    where_sql = " AND ".join(where)

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT * FROM adh_knowledge_items WHERE {where_sql}
                    ORDER BY RAND() LIMIT %s""",
                params + [limit],
            )
            rows = [_serialize_row(r) for r in cur.fetchall()]

    result = {"items": rows}
    workspace_cache.set(cache_key, result)
    return result


@router.get("/knowledge/stats")
def knowledge_stats(
    workspace_id: int = Query(0),
    user: dict = Depends(get_current_user),
):
    """知识条目统计（按类型分组，只统计调用方可读的类型）。"""
    readable = _readable_types(user)
    if not readable:
        raise HTTPException(status_code=403, detail="查看语义层语料统计需要对应的查看权限")
    where = [f"knowledge_type IN ({','.join(['%s'] * len(readable))})"]
    params: list = list(readable)
    if workspace_id:
        where.append("workspace_id = %s")
        params.append(workspace_id)

    where_sql = " AND ".join(where)

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) as total FROM adh_knowledge_items WHERE {where_sql}", params)
            total = cur.fetchone()["total"]

            cur.execute(
                f"""SELECT knowledge_type, COUNT(*) as cnt,
                           SUM(usage_count) as total_usage,
                           SUM(positive_count) as total_positive,
                           SUM(negative_count) as total_negative
                    FROM adh_knowledge_items WHERE {where_sql}
                    GROUP BY knowledge_type""",
                params,
            )
            by_type = cur.fetchall()

    return {"total": total, "by_type": by_type}


@router.post("/knowledge")
def create_knowledge(body: KnowledgeCreate, user: dict = Depends(get_current_user)):
    """创建知识条目（按 knowledge_type 归到对应权限码）。"""
    _assert_type_perm(user, body.knowledge_type, write=True)
    _assert_scenario_subjects(body.knowledge_type, body.metadata)
    item_id = uuid.uuid4().int & ((1 << 63) - 1)  # 正 BIGINT
    metadata_json = json.dumps(body.metadata, ensure_ascii=False) if body.metadata else None

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO adh_knowledge_items
                   (id, workspace_id, datasource_id, knowledge_type, title, content,
                    metadata, related_tables, priority, is_active)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (item_id, body.workspace_id, body.datasource_id, body.knowledge_type,
                 body.title, body.content, metadata_json, body.related_tables,
                 body.priority, body.is_active),
            )

    _invalidate_quick_paths()
    return {"id": item_id, "message": "created"}


@router.put("/knowledge/{item_id}")
def update_knowledge(item_id: int, body: KnowledgeUpdate, user: dict = Depends(get_current_user)):
    """更新知识条目。类型由库里的行决定，不接受请求体改类型（防止把场景改成 SQL 对绕过归属）。"""
    updates = []
    params: list = []

    for field in ("title", "content", "related_tables", "priority", "is_active"):
        val = getattr(body, field, None)
        if val is not None:
            updates.append(f"{field} = %s")
            params.append(val)

    if body.metadata is not None:
        updates.append("metadata = %s")
        params.append(json.dumps(body.metadata, ensure_ascii=False))

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(item_id)
    set_sql = ", ".join(updates)

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            ktype = _item_type(cur, item_id)
            _assert_type_perm(user, ktype, write=True)
            if body.metadata is not None:
                # 主体声明只在写 metadata 时校（没改 metadata 就不拿旧数据为新的错负责）
                _assert_scenario_subjects(ktype, body.metadata)
            cur.execute(f"UPDATE adh_knowledge_items SET {set_sql} WHERE id = %s", params)
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="条目不存在")

    _invalidate_quick_paths()
    return {"message": "updated"}


@router.patch("/knowledge/{item_id}/toggle")
def toggle_knowledge(item_id: int, user: dict = Depends(get_current_user)):
    """切换条目的启用/禁用状态（停用即不再注入提示词，属写动作）。"""
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            ktype = _item_type(cur, item_id)
            _assert_type_perm(user, ktype, write=True)
            cur.execute("SELECT is_active FROM adh_knowledge_items WHERE id = %s", (item_id,))
            row = cur.fetchone()
            new_val = 0 if row["is_active"] else 1
            cur.execute("UPDATE adh_knowledge_items SET is_active = %s WHERE id = %s", (new_val, item_id))

    _invalidate_quick_paths()
    return {"is_active": new_val}


@router.delete("/knowledge/{item_id}")
def delete_knowledge(item_id: int, user: dict = Depends(get_current_user)):
    """删除知识条目。"""
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            ktype = _item_type(cur, item_id)
            _assert_type_perm(user, ktype, write=True)
            cur.execute("DELETE FROM adh_knowledge_items WHERE id = %s", (item_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="条目不存在")

    _invalidate_quick_paths()
    return {"message": "deleted"}
