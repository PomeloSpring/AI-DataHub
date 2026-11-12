"""``adh_conversation_messages``（拆行消息表）的内存替身 —— 回归用例共用。

为什么要有这个文件：会话消息从「单列 LONGTEXT 装整份 JSON 数组」改成「一条消息一行」
之后，各测试里手写的 fake cursor 若还按退役列建模，就会出现最坏的一种绿：既不报错、
也不落东西，断言全部退化成"什么都没发生"。替身必须跟着真存储形态走，并且

- **只认 conversation_store 真发出的语句**：不认识的 SQL 交给宿主 fake（返回 False），
  但一旦属于拆行表却不支持，显式失败提醒，不静默跳过；
- **读写与真实现同源**：建行走 ``split_message``（含入库投影）、读回归走 ``merge_message``，
  避免"测试里的形态和生产不一样"；
- **撞 seq 就报错**：真库是 ``uk_conv_seq`` 冲突，替身也必须炸，否则 seq 计算类缺陷
  （历史缺陷：``MAX(seq)`` 为 0 时被 ``or -1`` 当假值，算出重复 seq）测不出来。

用法（宿主 fake 里）::

    from split_table_fake import SplitTable

    def execute(self, sql, params=None):
        state["log"].append((sql, params))
        if self.table.handle(sql, params):     # 拆行表语句由替身接管
            return
        ...                                     # 其余语句宿主自己处理
"""

from __future__ import annotations

from backend.core import conversation_store as store

_TABLE = store.TABLE


def new_state(conversation_id: int = 150, user_id: int = 7, workspace_id: int = 301,
              owner_ok: bool = True) -> dict:
    """替身状态：会话父行 + 拆行清单 + 读回来的消息（前端契约形态）。"""
    return {
        "conv": {"id": conversation_id, "user_id": user_id, "workspace_id": workspace_id},
        "owner_ok": owner_ok,
        "conversation_id": conversation_id,
        "rows": [],
        "messages": [],
        "log": [],
    }


def seed(state: dict, messages) -> None:
    """按投影后形态灌入既有消息（等价于真库回填/历史写入后的状态）。"""
    rows = []
    for seq, msg in enumerate(messages or []):
        role, content, meta_json, uuid_ = store.split_message(msg)
        rows.append({"conversation_id": state["conversation_id"], "seq": seq, "role": role,
                     "content": content, "meta": meta_json, "message_uuid": uuid_,
                     "digest": store.message_digest(store.project_message(msg)),
                     "storage_state": store.STORAGE_STATE_FULL})
    state["rows"] = rows
    _refresh(state)


def read(state: dict, seq: int | None = None):
    """读回消息（前端契约）；给 seq 时只取那一条。"""
    if seq is None:
        return list(state["messages"])
    row = next((r for r in state["rows"] if r["seq"] == int(seq)), None)
    return store.merge_message(row) if row else None


def _refresh(state: dict) -> None:
    state["messages"][:] = [store.merge_message(r)
                            for r in sorted(state["rows"], key=lambda r: r["seq"])]


class SplitTable:
    """拆行表语句处理器：``handle()`` 返回是否已接管该语句。"""

    def __init__(self, state: dict):
        self.state = state
        self._result: list = []
        self.rowcount = 0

    # ── 语句分派 ──────────────────────────────────────────────────
    def handle(self, sql: str, params=()) -> bool:
        p = tuple(params or ())
        s = " ".join(sql.split())

        # 退役列被重新消费 = 有人绕开拆行表另起口径，必须显式失败
        if "adh_conversations" in s and ("SET messages" in s or "SELECT messages" in s):
            raise AssertionError(f"退役列 adh_conversations.messages 又被读写了: {sql}")

        if _TABLE in s:
            return self._handle_table(s, p)
        if "FROM adh_conversations" in s and "FOR UPDATE" in s:      # _lock_parent
            self._result = [self.state["conv"]] if self.state.get("owner_ok", True) else []
            return True
        return False

    def _handle_table(self, s: str, p: tuple) -> bool:
        rows = self.state["rows"]
        if s.startswith("SELECT conversation_id, seq, role, content, meta, storage_state"):
            # load_messages/load_page：整份、最近 N 条、或 before_seq 之前的最近 N 条
            # （参数个数随分支变，按语句里的子句逐项吃参数，不按固定下标取）
            cid, since = int(p[0]), int(p[1])
            rest = list(p[2:])
            before = int(rest.pop(0)) if "seq < %s" in s else None
            limit = int(rest[0]) if "LIMIT" in s else 0
            hit = [dict(r) for r in rows
                   if r["conversation_id"] == cid and r["seq"] > since
                   and (before is None or r["seq"] < before)]
            if limit:
                hit = sorted(hit, key=lambda r: -r["seq"])[:limit]
            self._result = sorted(hit, key=lambda r: r["seq"])
            return True
        if "COUNT(*)" in s:
            self._result = [{"c": sum(1 for r in rows if r["conversation_id"] == int(p[0]))}]
            return True
        if s.startswith("SELECT seq, digest"):
            self._result = [{"seq": r["seq"], "digest": r["digest"]}
                            for r in sorted(rows, key=lambda r: r["seq"])]
            return True
        if s.startswith("SELECT seq, message_uuid, role, content"):
            self._result = sorted(rows, key=lambda r: -r["seq"])[:2]
            return True
        if s.startswith("SELECT seq FROM"):
            self._result = [{"seq": r["seq"]} for r in sorted(rows, key=lambda r: -r["seq"])][:200]
            return True
        if s.startswith("SELECT role, meta FROM"):
            cid, seq = int(p[0]), int(p[1])
            self._result = [dict(r) for r in rows if r["seq"] == seq and r["conversation_id"] == cid]
            return True
        if "COALESCE(MAX(seq)" in s:
            self._result = [{"m": max((r["seq"] for r in rows), default=-1)}]
            return True
        if s.startswith("INSERT INTO"):
            self._insert(p)
            return True
        if s.startswith("UPDATE "):
            self._update(p, s)
            return True
        if s.startswith("DELETE FROM"):
            self._delete(p, s)
            return True
        raise AssertionError(f"拆行表替身不支持这条语句: {s}")

    # ── 写路径 ────────────────────────────────────────────────────
    def _insert(self, p: tuple) -> None:
        cid, _uid, _ws, seq, role, content, meta_json, uuid_, digest = p[:9]
        seq = int(seq)
        if any(r["seq"] == seq for r in self.state["rows"]):
            raise AssertionError(f"重复 seq={seq}（真库会 IntegrityError: uk_conv_seq）")
        self.state["rows"].append({
            "conversation_id": int(cid), "seq": seq, "role": role, "content": content,
            "meta": meta_json, "message_uuid": uuid_, "digest": digest,
            "storage_state": store.STORAGE_STATE_FULL})
        self.rowcount = 1
        _refresh(self.state)

    def _update(self, p: tuple, s: str) -> None:
        if "WHERE conversation_id=%s AND seq=%s" not in s:
            raise AssertionError(f"拆行改写缺少定位条件: {s}")
        # 两种改写：整行（role/content/meta/uuid/digest + cid/seq，7 参）与
        # 保留策略只改 meta+状态（meta/cid/seq，3 参）
        state_only = "storage_state='trimmed'" in s
        if state_only:
            meta_json, cid, seq = p[0], int(p[1]), int(p[2])
        else:
            role, content, meta_json, uuid_, digest, cid, seq = p[:7]
            cid, seq = int(cid), int(seq)
        hit = next((r for r in self.state["rows"] if r["seq"] == seq
                    and r["conversation_id"] == cid), None)
        if hit is None:
            raise AssertionError(f"改写不存在的行 conversation_id={cid} seq={seq}")
        if state_only:
            hit.update({"meta": meta_json, "storage_state": store.STORAGE_STATE_TRIMMED})
        else:
            hit.update({"role": role, "content": content, "meta": meta_json,
                        "message_uuid": uuid_, "digest": digest,
                        "storage_state": store.STORAGE_STATE_FULL})
        self.rowcount = 1
        _refresh(self.state)

    def _delete(self, p: tuple, s: str) -> None:
        cid = int(p[0])
        if "seq >=" in s:
            keep = [r for r in self.state["rows"]
                    if not (r["conversation_id"] == cid and r["seq"] >= int(p[1]))]
        else:
            keep = [r for r in self.state["rows"] if r["conversation_id"] != cid]
        self.rowcount = len(self.state["rows"]) - len(keep)
        self.state["rows"][:] = keep
        _refresh(self.state)

    # ── 读结果（cursor 协议）───────────────────────────────────────
    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)
