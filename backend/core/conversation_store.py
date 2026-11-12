"""会话消息持久层 —— 拆行存储（adh_conversation_messages）+ 入库投影（单条消息一行）。

改造动因（2026-11 存储审计实测：30 会话 / 172 条消息 / 2.31MB，其中
``tool_calls[].result`` 22.2% + ``process`` 14.5% + ``thinking`` 13.5%，
正文只占 3.6%）：

1. **写放大**：旧形态是 ``adh_conversations.messages`` 单列 LONGTEXT 装整份 JSON 数组，
   每轮 PUT 全列覆盖 —— 会话越长每轮重写越多字节（O(n²)），且元库
   ``binlog_format=ROW`` 会把 before/after 两份镜像都写进 binlog；
2. **敏感面**：同一列里除用户可见正文外还存 ``tool_calls[].result``（工具**原始返回体**：
   目录转储、知识库 chunk、引擎明细）、``thinking``、``ai_raw_response`` —— 未经展示治理
   的中间产物明文常驻，并随 ``GET /chat/conversations/{id}`` 历史回放再次下发；
3. **读放大**：详情页整段 LONGTEXT 载入后 ``json.loads``。

口径（本文件是唯一实现处，新增会话读写路径不得绕过）：

- **拆行**：``(conversation_id, seq)`` 一行消息；``role``/``content`` 独立列可查，
  其余展示字段收进 ``meta`` JSON；写入是 **diff**（追加新增、改写变更、截断删除），
  不再整片覆盖。``digest`` 存投影后消息的 md5，用于判等；迁移回填行 ``digest=''``
  表示"未算"，首次改写自然补上（自愈，不需要双口径）。
- **入库投影**：任何写入都先过 :func:`project_message` —— 大字段按上限截断并留
  ``*_truncated`` 事实标记，重复正文（``ai_raw_response`` 与 content/reply 同值）不落两份。
  前端 ``chatStore.saveMessages`` 的 slim 只是同口径的**带宽裁剪**，服务端投影才是权威。
- **遗留列**：``adh_conversations.messages`` 退役为迁移证据列，读写路径一律不得消费
  （``tests/test_conversation_store.py`` 有源码级护栏防漂移）。存量历史由
  ``docker/mysql/conversation_messages_split_migration.sql`` 回填到拆行表，
  原列内容保持不动以便回滚核对。
- **身份**：``user_id``/``workspace_id`` 只由调用方从服务端上下文传入（护栏 §2），
  归属校验在调用方完成，本层不做第二套权限判断。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

TABLE = "adh_conversation_messages"

# 遗留列（退役中）：仅迁移脚本与"禁止消费"护栏认识它，业务读写不得出现
LEGACY_COLUMN = "adh_conversations.messages"

#: meta 里除 role/content 之外一律照原样落，投影只做大字段截断；
#: 保留期裁剪后写回的 storage_state=trimmed 会同步在 meta 打标记供前端提示。
STORAGE_STATE_FULL = "full"
STORAGE_STATE_TRIMMED = "trimmed"


# ── 投影上限（唯一登记处 backend/common/config.py，本层只读不重复默认值）──

def tool_result_max_chars() -> int:
    """``tool_calls[].result`` 落库上限：0 = 完全不落（只留 result_preview）。"""
    return _limit("CONV_TOOL_RESULT_MAX_CHARS")


def thinking_max_chars() -> int:
    return _limit("CONV_THINKING_MAX_CHARS")


def process_step_max_chars() -> int:
    return _limit("CONV_PROCESS_STEP_MAX_CHARS")


def process_max_steps() -> int:
    return _limit("CONV_PROCESS_MAX_STEPS")


def raw_response_max_chars() -> int:
    return _limit("CONV_RAW_RESPONSE_MAX_CHARS")


def progress_stage_max() -> int:
    return _limit("CONV_PROGRESS_STAGE_MAX")


def _limit(name: str) -> int:
    """每次调用现取 config（负值按 0 处理；测试可 monkeypatch config 同名项）。"""
    from backend.common import config
    return max(0, int(getattr(config, name, 0) or 0))


# ── 入库投影（P0-1 唯一规则处）────────────────────────────────────────

def _clip(text: Any, limit: int) -> tuple[Any, bool]:
    """按上限裁剪字符串，返回 (值, 是否被裁)。limit<=0 → 丢弃（返回 None）。"""
    if limit <= 0:
        return None, True
    if not isinstance(text, str):
        return text, False
    if len(text) <= limit:
        return text, False
    return text[:limit] + "…", True


def project_message(msg: Any) -> dict:
    """把一条会话消息投影成"可落库形态"（不改动调用方内存对象）。

    只裁剪**未经展示治理的中间产物**，用户可见正文（content/reply）与已治理结果集
    （message 级 result.rows，取数时已按敏感/RLS 策略改写）保持原样——它们是用户
    资产与"存为报表"的事实源，不在这里动刀。
    """
    if not isinstance(msg, dict):
        return {"role": "", "content": "" if msg is None else str(msg)}

    out: dict = dict(msg)
    content = out.get("content")
    reply = out.get("reply")

    # 1) 模型思考链：可读头寸保留，超出截断并标注
    if "thinking" in out:
        clipped, hit = _clip(out.get("thinking"), thinking_max_chars())
        if hit:
            out["thinking_truncated"] = True
        if clipped is None:
            out.pop("thinking", None)
        else:
            out["thinking"] = clipped

    # 2) 原始模型输出：与 content/reply 同值就是同一份文本的第三次拷贝，不落三份
    if "ai_raw_response" in out:
        raw = out.get("ai_raw_response")
        if isinstance(raw, str) and raw in (content, reply):
            out.pop("ai_raw_response", None)
        else:
            clipped, hit = _clip(raw, raw_response_max_chars())
            if hit:
                out["ai_raw_response_truncated"] = True
            if clipped is None:
                out.pop("ai_raw_response", None)
            else:
                out["ai_raw_response"] = clipped

    # 3) 工具调用：原始返回体截断（保留参数/耗时/错误/摘要，明细已在工作区产物里）
    tool_calls = out.get("tool_calls")
    if isinstance(tool_calls, list):
        limit = tool_result_max_chars()
        slim: list = []
        truncated_any = False
        for tc in tool_calls:
            if not isinstance(tc, dict):
                slim.append(tc)
                continue
            item = dict(tc)
            if "result" in item:
                clipped, hit = _clip(item.get("result") if isinstance(item.get("result"), str)
                                     else _dumps(item.get("result")), limit)
                if hit:
                    item["result_truncated"] = True
                    truncated_any = True
                if clipped is None:
                    item.pop("result", None)
                    item["result_omitted"] = True
                else:
                    item["result"] = clipped
            slim.append(item)
        out["tool_calls"] = slim
        if truncated_any:
            out["tool_results_truncated"] = True

    # 4) 执行过程步骤：步数与单步文本都设上限
    process = out.get("process")
    if isinstance(process, list):
        max_steps = process_max_steps()
        slim_p: list = []
        hit = len(process) > max_steps
        for step in process[:max_steps] if max_steps > 0 else []:
            if not isinstance(step, dict):
                slim_p.append(step)
                continue
            item = dict(step)
            if isinstance(item.get("text"), str):
                clipped, step_hit = _clip(item["text"], process_step_max_chars())
                if step_hit:
                    item["text_truncated"] = True
                    hit = True
                if clipped is None:
                    item.pop("text", None)
                else:
                    item["text"] = clipped
            slim_p.append(item)
        out["process"] = slim_p
        if hit:
            out["process_truncated"] = True

    # 5) 进度阶段：只保留最近若干条（与任务回写的 40 条口径一致，防长任务无限膨胀）
    #    上限 0 = 完全不落，与其它 *_MAX 口径一致（`stages[-0:]` 会返回整片，是陷阱）
    stages = out.get("progressStages")
    if isinstance(stages, list):
        keep = progress_stage_max()
        if len(stages) > keep:
            out["progressStages"] = stages[-keep:] if keep > 0 else []
            out["progressStages_truncated"] = True

    return out


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _canonical(msg: dict) -> str:
    # 键排序 + 紧凑分隔：同一份内容在任何写入路径都算出同一个 digest
    return json.dumps(msg, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def message_digest(msg: dict) -> str:
    return hashlib.md5(_canonical(msg).encode("utf-8")).hexdigest()


# ── 行 ↔ 消息 双向转换 ────────────────────────────────────────────────

_CONTENT_KEYS = ("role", "content")


def split_message(msg: Any) -> tuple[str, str, str, str]:
    """消息 → (role, content, meta_json, message_uuid)。除 role/content 外的字段进 meta。"""
    projected = project_message(msg)
    role = str(projected.get("role") or "")
    content = projected.get("content")
    meta = {k: v for k, v in projected.items() if k not in _CONTENT_KEYS}
    return role, ("" if content is None else str(content)), _dumps(meta), str(meta.get("message_uuid") or "")


def merge_message(row: dict) -> dict:
    """拆行 → 前端契约里的消息对象（meta 字段 + role/content），与旧 JSON 数组同形。"""
    raw_meta = row.get("meta")
    meta: dict = {}
    if isinstance(raw_meta, dict):
        meta = dict(raw_meta)
    elif isinstance(raw_meta, (bytes, bytearray)):
        raw_meta = raw_meta.decode("utf-8", "ignore")
    if isinstance(raw_meta, str) and raw_meta:
        try:
            parsed = json.loads(raw_meta)
            if isinstance(parsed, dict):
                meta = parsed
        except ValueError:
            # 不静默丢内容：损坏时把原文放进 meta_raw，页面至少能显示"这条记录异常"
            logger.error("会话消息 meta JSON 损坏: conversation_id=%s seq=%s",
                         row.get("conversation_id"), row.get("seq"))
            meta = {"meta_raw": raw_meta}
    out = dict(meta)
    out["role"] = str(row.get("role") or "")
    content = row.get("content")
    if isinstance(content, (bytes, bytearray)):
        content = content.decode("utf-8", "ignore")
    out["content"] = "" if content is None else content
    if str(row.get("storage_state") or STORAGE_STATE_FULL) == STORAGE_STATE_TRIMMED:
        out["storage_trimmed"] = True
    return out


# ── 写入（diff，取代整片覆盖）─────────────────────────────────────────

def _lock_parent(cur, conversation_id: int, user_id: int) -> dict | None:
    """锁会话父行串行化同会话并发写（锁小行，不再重写大列）。"""
    if user_id:
        cur.execute("SELECT id, user_id, workspace_id FROM adh_conversations "
                    "WHERE id=%s AND user_id=%s FOR UPDATE", (conversation_id, user_id))
    else:
        cur.execute("SELECT id, user_id, workspace_id FROM adh_conversations "
                    "WHERE id=%s FOR UPDATE", (conversation_id,))
    row = cur.fetchone()
    return dict(row) if isinstance(row, dict) else (row or None)


def save_messages(cur, conversation_id: int, messages: Iterable[Any], *,
                  user_id: int = 0, workspace_id: int = 0, lock: bool = True,
                  base_seq: int = 0) -> dict:
    """把消息数组落库（**增量 diff**，语义等价于旧整列覆盖）。

    - 新增尾部 → INSERT；
    - 内容变更（用户在会话内改写/服务端回写同一条）→ UPDATE 该行；
    - 数组变短（清空记录/删除回合）→ DELETE 尾部多余行。

    ``cur`` 必须在事务内（调用方 commit）；``lock=True`` 时先锁父行，
    避免两个写者同时算出同一个 seq。

    ``base_seq`` = 这份数组从哪个 seq 开始（分页窗口写回用）：缺省 0 = 整份历史，
    与旧语义一致；>0 时 ``seq < base_seq`` 的行**一律不动** —— 那是客户端根本没
    加载进来的更早消息，当成“数组变短”删掉就是抹历史。
    窗口起点越过服务端现有条数 = 两边对历史的认知不一致（例如别处刚清空过），
    显式报错让调用方重新加载，不猜也不静默对齐。
    """
    cid = int(conversation_id)
    items = [m for m in (messages or [])]
    base = int(base_seq or 0)
    parent: dict = {}
    if lock:
        parent = _lock_parent(cur, cid, int(user_id or 0)) or {}
        if not parent:
            # 会话不存在/不属于当前身份（只能是同事务并发删除）：显式报错，不往孤儿会话上插行
            raise ValueError(f"会话不存在或无权访问: conversation_id={cid}")
    if not workspace_id:
        workspace_id = int((parent or {}).get("workspace_id") or 0)
    if not user_id:
        user_id = int((parent or {}).get("user_id") or 0)

    # 全量取 seq+digest（每行只一个 int + 32 字串 md5，不是大字段）：
    # 既要窗口内的对比基准，也要靠它判窗口起点是否越界
    cur.execute(f"SELECT seq, digest FROM {TABLE} WHERE conversation_id=%s FOR UPDATE", (cid,))
    all_rows = [(int(r["seq"]), str(r["digest"] or "")) for r in (cur.fetchall() or [])]
    total = len(all_rows)
    if base > total:
        raise ValueError(f"会话历史已变化（本地窗口起点 {base} 超出服务端 {total} 条），"
                         f"请重新打开会话后再保存")
    existing = {seq: digest for seq, digest in all_rows if seq >= base}

    inserted = updated = 0
    for offset, msg in enumerate(items):
        seq = base + offset
        role, content, meta_json, uuid_ = split_message(msg)
        digest = message_digest(project_message(msg))
        old = existing.get(seq)
        if old is None:
            cur.execute(
                f"INSERT INTO {TABLE} (conversation_id, user_id, workspace_id, seq, role, "
                f"content, meta, message_uuid, digest, storage_state) "
                f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'{STORAGE_STATE_FULL}')",
                (cid, int(user_id or 0), int(workspace_id or 0), seq, role, content,
                 meta_json, uuid_, digest))
            inserted += 1
        elif old != digest:
            cur.execute(
                f"UPDATE {TABLE} SET role=%s, content=%s, meta=%s, message_uuid=%s, digest=%s, "
                f"storage_state='{STORAGE_STATE_FULL}' WHERE conversation_id=%s AND seq=%s",
                (role, content, meta_json, uuid_, digest, cid, seq))
            updated += 1

    deleted = 0
    tail = base + len(items)
    if total > tail:
        cur.execute(f"DELETE FROM {TABLE} WHERE conversation_id=%s AND seq >= %s", (cid, tail))
        deleted = total - tail

    return {"count": len(items), "base_seq": base, "inserted": inserted,
            "updated": updated, "deleted": deleted}


def append_message(cur, conversation_id: int, message: dict, *, user_id: int = 0,
                   workspace_id: int = 0, message_uuid: str = "",
                   question: str = "", extra_fields: dict | None = None) -> dict:
    """追加一条回合（服务端兜底落库用），按 ``message_uuid`` 判重幂等。

    与旧实现的差异：不再 SELECT 大列 → 改数组 → 整列写回，而是父行锁 + 尾部 seq 追加；
    判重口径不变（uuid 优先，退回"尾部同正文 assistant"判重），已存在则**跳过**，
    不覆盖用户后续编辑/清空的结果。

    ``question`` 非空且尾部不是这条问题时先补问题再补助手正文（断连时前端那次 PUT
    可能连用户问题都没落库，只补答案会让回放变成没头没尾的一坨）。
    """
    cid = int(conversation_id)
    parent = _lock_parent(cur, cid, int(user_id or 0)) or {}
    if not parent:
        return {"persisted": False, "reason": "conversation_not_found_or_not_owner"}
    if not user_id:
        user_id = int(parent.get("user_id") or 0)
    if not workspace_id:
        workspace_id = int(parent.get("workspace_id") or 0)

    cur.execute(f"SELECT seq, message_uuid, role, content FROM {TABLE} "
                f"WHERE conversation_id=%s ORDER BY seq DESC LIMIT 2 FOR UPDATE", (cid,))
    tail = [dict(r) for r in (cur.fetchall() or [])][::-1]
    uuid_ = str(message_uuid or (message or {}).get("message_uuid") or "")
    if uuid_ and any(str(t.get("message_uuid") or "") == uuid_ for t in tail):
        return {"persisted": False, "reason": "already_persisted_by_client"}
    content = str((message or {}).get("content") or "")
    # 兜底第二道判重：有的入口（AS-BOT 面板）落库时不携带 message_uuid，只看尾部
    # 长一样的 assistant 正文 —— 判重范围限在末尾，避免把历史上真重复过的短答案误判。
    for t in tail:
        if str(t.get("role") or "") == "assistant" and content and str(t.get("content") or "") == content:
            return {"persisted": False, "reason": "already_persisted_by_client"}

    # 尾部 seq 取当前最大值（补问题后 seq 会 +1，助手正文用新算的值）
    def _next_seq() -> int:
        # 注意：MAX(seq) 合法取值包含 0（会话只有 1 条消息），不得用 `x or -1` 判空 ——
        # 那会把 0 当假值退回 -1，算出重复 seq 撞 uk_conv_seq（断连兜底/任务进度回写全挂）。
        cur.execute(f"SELECT COALESCE(MAX(seq), -1) AS m FROM {TABLE} WHERE conversation_id=%s", (cid,))
        raw = (cur.fetchone() or {}).get("m")
        return (int(raw) if raw is not None else -1) + 1

    qtext = str(question or "").strip()
    if qtext and not (tail and str(tail[-1].get("role") or "") == "user"
                      and str(tail[-1].get("content") or "").strip() == qtext):
        _append_row(cur, cid, {"role": "user", "content": qtext},
                    seq=_next_seq(), user_id=user_id, workspace_id=workspace_id)

    record = dict(message or {})
    if uuid_:
        record["message_uuid"] = uuid_
    record.update(extra_fields or {})
    seq = _next_seq()
    _append_row(cur, cid, record, seq=seq, user_id=user_id, workspace_id=workspace_id)
    return {"persisted": True, "seq": seq, "count": seq + 1}


def _append_row(cur, conversation_id: int, message: Any, *, seq: int, user_id: int,
                workspace_id: int) -> None:
    """单行追加（写入前必经投影）。"""
    projected = project_message(message)
    role, content, meta_json, uuid_ = split_message(projected)
    cur.execute(
        f"INSERT INTO {TABLE} (conversation_id, user_id, workspace_id, seq, role, content, meta, "
        f"message_uuid, digest, storage_state) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'{STORAGE_STATE_FULL}')",
        (int(conversation_id), int(user_id or 0), int(workspace_id or 0), int(seq), role,
         content, meta_json, uuid_, message_digest(projected)))


def replace_message_by_key(cur, conversation_id: int, *, match: dict, message: dict,
                           user_id: int = 0, workspace_id: int = 0) -> dict:
    """按条件替换会话里**唯一一条**消息（本体生成任务把进度写回同一条 assistant 消息）。

    ``match`` 是消息字段等值条件（如 ``{"role": "assistant", "task_kind": ...}``）；
    命中最后一条则改写它，一条都没有则追加。旧实现是"读整份数组→改→写回整列"，
    本函数把它收敛成单行改写。

    读也走传入的 ``cur``（同一事务）：不得用独立连接去看不到的未提交行。
    """
    cid = int(conversation_id)
    cur.execute(f"SELECT seq FROM {TABLE} WHERE conversation_id=%s ORDER BY seq DESC LIMIT 200 "
                f"FOR UPDATE", (cid,))
    candidate_seqs = [int(r["seq"]) for r in (cur.fetchall() or [])]
    target = None
    for seq in candidate_seqs:                      # 从尾往前找第一条匹配的
        cur.execute(f"SELECT role, meta FROM {TABLE} WHERE conversation_id=%s AND seq=%s",
                    (cid, seq))
        row = cur.fetchone() or {}
        merged = merge_message({**row, "conversation_id": cid, "seq": seq})
        if all(merged.get(k) == v for k, v in (match or {}).items()):
            target = seq
            break
    if target is None:
        return append_message(cur, cid, message, user_id=user_id, workspace_id=workspace_id)
    role, content, meta_json, uuid_ = split_message(message)
    cur.execute(
        f"UPDATE {TABLE} SET role=%s, content=%s, meta=%s, message_uuid=%s, digest=%s, "
        f"storage_state='{STORAGE_STATE_FULL}' WHERE conversation_id=%s AND seq=%s",
        (role, content, meta_json, uuid_, message_digest(project_message(message)), cid, int(target)))
    return {"updated": 1, "seq": int(target)}


# ── 读取 ─────────────────────────────────────────────────────────────

def _load_rows(conversation_id: int, *, limit: int = 0, since_seq: int = -1,
               before_seq: int | None = None) -> list[dict]:
    """拆行读取（按 seq 升序返回）：``limit`` 取最近 N 条，``before_seq`` 限定上界。"""
    cid = int(conversation_id)
    if cid <= 0:
        return []
    from backend.common.db import execute_query

    sql = (f"SELECT conversation_id, seq, role, content, meta, storage_state FROM {TABLE} "
           f"WHERE conversation_id=%s AND seq > %s")
    params: list = [cid, int(since_seq)]
    if before_seq is not None:
        sql += " AND seq < %s"
        params.append(int(before_seq))
    if int(limit or 0) > 0:
        # 取"最近 N 条"靠 DESC+LIMIT，返回前再排回升序（前端契约是时间正序）
        sql += " ORDER BY seq DESC LIMIT %s"
        params.append(int(limit))
        rows = execute_query(sql, tuple(params)) or []
        return sorted(rows, key=lambda r: int(r["seq"]))
    sql += " ORDER BY seq ASC"
    return list(execute_query(sql, tuple(params)) or [])


def load_messages(conversation_id: int, *, limit: int = 0, since_seq: int = -1,
                  before_seq: int | None = None) -> list[dict]:
    """历史消息（前端契约不变：与旧 JSON 数组同形的 dict 列表）。

    ``limit``>0 时只取最近 N 条（供长会话按需加载；缺省 0=全量，兼容现有调用方）。
    """
    return [merge_message(r) for r in
            _load_rows(conversation_id, limit=limit, since_seq=since_seq, before_seq=before_seq)]


def load_page(conversation_id: int, *, limit: int = 0, before_seq: int | None = None) -> dict:
    """详情页分页读：最近 ``limit`` 条（0=整份），``before_seq`` 再往前翻一页。

    翻页游标随消息一起给出，调用方不必自己数 seq：
    ``oldest_seq`` 本页最小 seq（也是下一次往前翻的 ``before_seq``），
    ``has_more_earlier`` 还有更早的消息没读（前端据此显示“加载更早消息”），
    ``total`` 会话消息总数。seq 从 0 连续编号（写入侧保证），所以 oldest>0 就等价于“还有更早”。
    """
    rows = _load_rows(conversation_id, limit=limit, before_seq=before_seq)
    oldest = int(rows[0]["seq"]) if rows else None
    return {"messages": [merge_message(r) for r in rows],
            "oldest_seq": oldest,
            "has_more_earlier": bool(oldest),
            "total": count_messages(conversation_id)}


def count_messages(conversation_id: int) -> int:
    from backend.common.db import execute_query
    row = execute_query(f"SELECT COUNT(*) AS c FROM {TABLE} WHERE conversation_id=%s",
                        (int(conversation_id),), fetchone=True)
    return int((row or {}).get("c") or 0)


# ── 级联删除（会话/工作空间删除必须带上，不得留孤儿行）────────────────

def delete_by_conversation(cur, conversation_id: int) -> int:
    cur.execute(f"DELETE FROM {TABLE} WHERE conversation_id=%s", (int(conversation_id),))
    return int(cur.rowcount or 0)


def delete_by_workspace(cur, workspace_id: int) -> int:
    cur.execute(f"DELETE FROM {TABLE} WHERE workspace_id=%s", (int(workspace_id),))
    return int(cur.rowcount or 0)


def cascade_delete_statement(workspace_id: int) -> tuple[str, tuple]:
    """供工作空间删除级联清单使用（纯函数，便于回归断言级联范围）。"""
    return (f"DELETE FROM {TABLE} WHERE workspace_id = %s", (int(workspace_id),))


def table_exists(cur) -> bool:
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() "
                "AND table_name=%s", (TABLE,))
    return cur.fetchone() is not None
