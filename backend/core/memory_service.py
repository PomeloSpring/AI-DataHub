"""用户记忆存储（adh_user_memory）— 工具组与 REST 的单一事实源。

记忆模型（两类行，同表共存）:
- **固定槽位** ``FIXED_SLOTS``: 每人每槽一行(``entry_no`` 恒为 1), 同名覆盖;
- **浮动条目** ``FLOATING_CATEGORIES``: 每人每类多行(``entry_no`` 递增),
  两类**共享** ``FLOATING_LIMIT`` 条上限, 超限 fail-loud(不静默淘汰旧条目)。

唯一键 ``uk_user_slot_entry(user_id, slot_key, entry_no)`` 承担两件事:
固定槽位的单值语义由 DB 保证(upsert), 浮动条目的多行由 entry_no 放开。

内容边界(本模块的硬约束, 见 ``validate_content``):
只允许**跨会话稳定事实**(偏好/口径/习惯)。会话状态与临时上下文("正在做 X"、
"上次问了 Y"、"本次的结果")一律拒绝 —— 记忆按 user_id 归属、跨会话注入 prompt,
存进一次会话的上下文会把该会话的措辞带到别的会话里造成串味与错误推断。
数据行/查询结果也不得入记忆(取数必须每次走治理入口, 见 security-guardrails)。
"""

import logging
import re
from typing import Optional

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)

# 固定槽位定义（slot_key → 显示名）；数量恒定 → prompt 注入长度可预算
FIXED_SLOTS = {
    "business_domain": "业务域",
    "preferred_chart": "默认图表",
    "default_time_grain": "时间粒度",
    "output_format": "输出格式",
    "number_precision": "数字精度",
    "role_context": "角色背景",
}

# 浮动条目类别（slot_key → 显示名），共享 FLOATING_LIMIT 上限
FLOATING_CATEGORIES = {
    "correction": "口径纠错",
    "workflow": "工作习惯",
}

# 浮动条目硬上限（跨类别合计）
FLOATING_LIMIT = 5

# 单条 content 字符上限（与表列宽一致）
CONTENT_MAX = 300

# 会话状态/临时上下文词 —— 命中即拒（记忆跨会话生效，这类内容必然串味）
_SESSION_STATE_PATTERN = re.compile(
    r"(正在|上次|刚才|刚刚|前面|之前那个|当前会话|这个对话|本次|这轮|本轮|上一轮|上面问|稍后|待会|接下来)",
    re.IGNORECASE,
)

# 查询结果/数据行泄露词 —— 命中即拒（取数结果不得沉淀成记忆）
_RESULT_LEAK_PATTERN = re.compile(
    r"(查询结果|返回了?\s*\d|共\s*\d+\s*(行|条|个|笔)|结果是\s*\d|数据如下)",
)

_SOURCE_VALUES = {"auto", "manual"}


def validate_content(content: str) -> Optional[str]:
    """校验记忆内容：非空、长度、禁会话上下文、禁查询结果。返回错误信息或 None。"""
    content = (content or "").strip()
    if not content:
        return "记忆内容不能为空"
    if len(content) > CONTENT_MAX:
        return f"记忆内容超过 {CONTENT_MAX} 字符上限，请精简"
    if _SESSION_STATE_PATTERN.search(content):
        return ("禁止记录会话状态/临时上下文（如\"正在做 X\"、\"上次问了 Y\"）——"
                "记忆会跨会话注入提示词，只存稳定事实（偏好/口径/习惯）")
    if _RESULT_LEAK_PATTERN.search(content):
        return "禁止把查询结果/数据行写入记忆（每次取数都须走治理入口，不得沉淀数值）"
    return None


def list_entries(user_id: int) -> list:
    """列出该用户全部记忆行（按用户隔离，无跨用户读取路径）。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, slot_key, entry_no, content, source, created_at, updated_at "
                "FROM adh_user_memory WHERE user_id=%s "
                "ORDER BY slot_key, entry_no", (int(user_id),))
            return list(cur.fetchall())
    finally:
        conn.close()


def split_entries(rows: list) -> tuple:
    """行清单 → (固定槽位行, 浮动条目行)；未知 slot_key 的行两侧都不出现。"""
    fixed = [r for r in rows if r["slot_key"] in FIXED_SLOTS]
    floating = [r for r in rows if r["slot_key"] in FLOATING_CATEGORIES]
    return fixed, floating


def save_entry(user_id: int, slot_key: str, content: str,
               source: str = "auto", entry_id: Optional[int] = None) -> dict:
    """新增/覆盖一条记忆。校验不通过或超限 → 抛 ValueError（消息可直接给用户看）。

    - 固定槽位: 同 slot_key 覆盖旧值（DB 唯一键 upsert）。
    - 浮动条目: 传 ``entry_id`` 则更新该行, 否则新增一行(entry_no 递增);
      新增时两类合计已达 ``FLOATING_LIMIT`` → 拒绝。
    """
    uid = int(user_id or 0)
    if uid <= 0:
        raise ValueError("缺少用户上下文，无法保存记忆")
    slot_key = (slot_key or "").strip()
    if slot_key not in FIXED_SLOTS and slot_key not in FLOATING_CATEGORIES:
        raise ValueError(
            f"未知 slot_key: {slot_key}"
            f"（固定槽位 {sorted(FIXED_SLOTS)} / 浮动条目 {sorted(FLOATING_CATEGORIES)}）")
    err = validate_content(content)
    if err:
        raise ValueError(err)
    content = content.strip()
    source = (source or "auto").strip()
    if source not in _SOURCE_VALUES:
        raise ValueError(f"未知 source: {source}（只允许 auto/manual）")

    if slot_key in FLOATING_CATEGORIES:
        if entry_id:
            return update_entry(uid, int(entry_id), content, source=source)
        return _insert_floating(uid, slot_key, content, source)
    return _upsert_fixed(uid, slot_key, content, source)


def _upsert_fixed(uid: int, slot_key: str, content: str, source: str) -> dict:
    """固定槽位写入：entry_no 恒为 1，同槽位由唯一键 upsert 覆盖。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_user_memory (user_id, slot_key, entry_no, content, source) "
                "VALUES (%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE content=VALUES(content), source=VALUES(source), "
                "updated_at=NOW()",
                (uid, slot_key, 1, content, source))
        conn.commit()
    finally:
        conn.close()
    return {"saved": True, "slot_key": slot_key, "content": content, "mode": "overwrite"}


def _insert_floating(uid: int, slot_key: str, content: str, source: str,
                     _attempts: int = 2) -> dict:
    """浮动条目新增：先查共享上限，再按 (slot_key) 内最大 entry_no +1 落行。

    entry_no 由 SELECT MAX 算出，并发下两个请求可能算到同一序号并撞唯一键；
    此时不静默丢弃，重算一次仍失败则抛 ValueError 让调用方重试（fail-loud）。
    """
    from pymysql.err import IntegrityError

    last_exc: Optional[Exception] = None
    for _ in range(_attempts):
        try:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) AS c FROM adh_user_memory "
                        "WHERE user_id=%s AND slot_key IN ({})".format(
                            ",".join(["%s"] * len(FLOATING_CATEGORIES))),
                        (uid, *FLOATING_CATEGORIES.keys()))
                    row = cur.fetchone()
                    count = int((row or {}).get("c") or 0)
                    if count >= FLOATING_LIMIT:
                        raise ValueError(
                            f"浮动条目已达上限 {FLOATING_LIMIT} 条，"
                            "请先用 delete_memory 清理后再保存")
                    cur.execute(
                        "SELECT COALESCE(MAX(entry_no), 0) + 1 AS n FROM adh_user_memory "
                        "WHERE user_id=%s AND slot_key=%s", (uid, slot_key))
                    entry_no = int((cur.fetchone() or {}).get("n") or 1)
                    cur.execute(
                        "INSERT INTO adh_user_memory (user_id, slot_key, entry_no, content, source) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        (uid, slot_key, entry_no, content, source))
                conn.commit()
            finally:
                conn.close()
            return {"saved": True, "slot_key": slot_key, "content": content,
                    "entry_no": entry_no, "mode": "append"}
        except IntegrityError as exc:
            # 并发撞 entry_no：回滚后重算一次
            last_exc = exc
            logger.warning("[memory] entry_no 并发冲突, 重算重试: user=%s slot=%s", uid, slot_key)
            continue
    raise ValueError("记忆写入冲突（同一类别正在被并发保存），请重试") from last_exc


def update_entry(user_id: int, memory_id: int, content: str,
                 source: str = "manual") -> dict:
    """按 id 更新记忆内容（只能改自己的，fail-closed）。"""
    uid = int(user_id or 0)
    err = validate_content(content)
    if err:
        raise ValueError(err)
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # 先锁行判存在：MySQL 默认 rowcount=“实际变更行”，内容未改时 UPDATE
            # 影响 0 行，拿 rowcount 当“不存在”会把幂等重写误报成删除失败。
            cur.execute("SELECT id FROM adh_user_memory WHERE id=%s AND user_id=%s FOR UPDATE",
                        (int(memory_id), uid))
            if not cur.fetchone():
                raise ValueError("记忆不存在或无权修改")
            cur.execute(
                "UPDATE adh_user_memory SET content=%s, source=%s, updated_at=NOW() "
                "WHERE id=%s AND user_id=%s",
                (content.strip(), (source or "manual").strip(), int(memory_id), uid))
        conn.commit()
    finally:
        conn.close()
    return {"saved": True, "id": int(memory_id), "content": content.strip(), "mode": "update"}


def delete_entry(user_id: int, memory_id: int) -> int:
    """删除指定记忆（只能删自己的）。返回影响行数（0 = 不存在或无权）。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_user_memory WHERE id=%s AND user_id=%s",
                        (int(memory_id), int(user_id)))
            deleted = int(cur.rowcount or 0)
        conn.commit()
    finally:
        conn.close()
    return deleted
