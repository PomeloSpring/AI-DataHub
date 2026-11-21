"""知识更新闭环（提议表 + 对象标量合并）的后端门禁。

进化中心此前只能处置「语义缺口」（缺别名/指标/维度/绑定），但业务真实纠正往往是
"这句话说错了 / 这篇文档要补一段 / 这类分析该这么做" —— 落点是本体对象说明、
知识库文档正文、分析场景。本文件锁住这条路的三条纪律：

1. **提议只是备忘录**：`create_proposal` 只写 `adh_knowledge_update_proposals`，
   不碰任何资产；正文/标题/枚举非法一律 ValueError（不静默归一）。
2. **状态机只前进一步**：`pending → applied|rejected` 用条件 UPDATE 仲裁，
   第二次点击不重复落库、不覆盖已处置结论（宁阻断勿错写）。
3. **标量合并只动三个字段**：`display_name` / `description` / `aliases`（并集、不删既有），
   `properties` / `links` / 其它对象原样保留；落 `save_draft_only`（不级联、不生效），
   对象不存在与 `kind='system'` 直接拒 —— 给入口就是骗人。

另锁住 Chat 工具侧的护栏：入参 schema 不含任何内部 id，禁传键表与 schema 同源。
"""
import json

import pytest

from backend.modules.catalog.services import knowledge_update_service as kus
from backend.modules.catalog.services import ontology_service
from backend.modules.mind.execution import function_tools


# ── 桩：把库访问换成可断言的假实现 ────────────────────────────────────────────

def _install(monkeypatch, rows_by_marker: dict | None = None):
    """返回 (reads, writes, state)：reads/writes 记 (sql, params)，state 可调 affected。"""
    reads: list[tuple[str, tuple]] = []
    writes: list[tuple[str, tuple]] = []
    rows = rows_by_marker or {}
    state = {"insert_id": 7, "affected": 1}

    def fake_q(sql, params=None):
        s = " ".join(str(sql).split())
        reads.append((s, params))
        for marker, val in rows.items():
            if marker in s:
                return val(params) if callable(val) else val
        return []

    def fake_w(sql, params=None):
        s = " ".join(str(sql).split())
        writes.append((s, params))
        return state["affected"]

    def fake_insert(sql, params=None):
        # 新行 id 必须来自 lastrowid（rowcount 恒为 1，会打到别人那条上）
        s = " ".join(str(sql).split())
        writes.append((s, params))
        return state["insert_id"]

    monkeypatch.setattr(kus, "_q", fake_q)
    monkeypatch.setattr(kus, "_w", fake_w)
    monkeypatch.setattr(kus, "_insert", fake_insert)
    return reads, writes, state


# ═══════════════════════════════════════════════════════════════════════════
# 1. 提议写入：只记"应该改什么"，枚举与必填不静默
# ═══════════════════════════════════════════════════════════════════════════

def test_create_proposal_writes_pending_only(monkeypatch):
    """只 INSERT 一条 pending：不 UPDATE 任何资产表，也不回内部指针以外的信息。"""
    reads, writes, _ = _install(monkeypatch)

    out = kus.create_proposal(target_type="kb_doc", title="返利口径说明",
                              content="返利按确认收货月份计提", rationale="用户纠正",
                              target_hint="业务知识库", datasource_name="ds_a",
                              source="chat", origin_conversation_id=12,
                              origin_message_uuid="u-1")

    assert out == {"id": 7, "status": "pending"}
    assert len(writes) == 1, f"只该有 1 条写，实际 {writes}"
    sql, params = writes[0]
    assert sql.startswith("INSERT INTO adh_knowledge_update_proposals")
    assert "'pending'" in sql
    # 来源落值：会话与消息 uuid 是回链锚点，丢了就点不回原对话
    assert params[0] == "chat" and params[1] == "kb_doc"
    assert 12 in params and "u-1" in params
    assert not reads, "写入路径不该顺带读别的表"


@pytest.mark.parametrize("kwargs,needle", [
    ({"target_type": "kb-doc", "title": "t"}, "target_type"),
    ({"target_type": "", "title": "t"}, "target_type"),
    ({"target_type": "kb_doc", "title": "   "}, "标题"),
    ({"target_type": "kb_doc", "title": "t", "source": "llm"}, "source"),
])
def test_create_proposal_rejects_illegal_input(monkeypatch, kwargs, needle):
    """枚举/必填非法一律 ValueError 并点名原因（不静默归一成合法值）。"""
    _, writes, _ = _install(monkeypatch)
    with pytest.raises(ValueError) as exc:
        kus.create_proposal(**kwargs)
    assert needle in str(exc.value)
    assert not writes, "校验失败不得落库"


def test_create_proposal_rejects_oversize_content(monkeypatch):
    """正文超 200KB 拒绝：MEDIUMTEXT 存得下，但没人会审 1MB 提议。"""
    _, writes, _ = _install(monkeypatch)
    with pytest.raises(ValueError) as exc:
        kus.create_proposal(target_type="kb_doc", title="t",
                            content="x" * (kus.MAX_CONTENT_BYTES + 1))
    assert "KB" in str(exc.value)
    assert not writes


# ═══════════════════════════════════════════════════════════════════════════
# 2. 清单与状态流转：条件 UPDATE 仲裁，重复点击不落双份
# ═══════════════════════════════════════════════════════════════════════════

def test_list_proposals_orders_and_counts(monkeypatch):
    """稳定序 + 各状态计数：前端页签 Badge 直接读 counts，不允许本地猜。"""
    rows = [{"id": 3, "status": "pending", "created_at": None, "decided_at": None,
             "updated_at": None}]
    reads, _, _ = _install(monkeypatch, {
        "FROM adh_knowledge_update_proposals ORDER BY": rows,
        "GROUP BY status": [{"status": "pending", "n": 3}, {"status": "applied", "n": 2}],
    })

    out = kus.list_proposals(limit=9999)

    sql = reads[0][0]
    assert "ORDER BY created_at DESC, id DESC" in sql
    # limit 上限 500：让人误以为"拉到全部"比显式截断更危险
    assert reads[0][1][-1] == 500
    assert out["counts"]["pending"] == 3 and out["counts"]["applied"] == 2
    assert out["counts"]["rejected"] == 0 and out["counts"]["superseded"] == 0
    assert out["items"][0]["created_at"] == ""  # 时间统一成字符串，前端不解析 YYYY


@pytest.mark.parametrize("field,value", [("status", "done"), ("target_type", "doc"),
                                        ("source", "agent")])
def test_list_proposals_rejects_illegal_filter(monkeypatch, field, value):
    """筛选项非法要报错：静默返回空清单会被读成"没有待办"。"""
    _install(monkeypatch)
    with pytest.raises(ValueError):
        kus.list_proposals(**{field: value})


def test_mark_applied_is_conditional_and_idempotent(monkeypatch):
    """第一次 applied 成功；重复调用回 changed=False 且不再写库。"""
    reads, writes, state = _install(monkeypatch, {
        "SELECT * FROM adh_knowledge_update_proposals WHERE id": [
            {"id": 9, "status": "applied", "created_at": None, "decided_at": None,
             "updated_at": None}],
    })

    first = kus.mark_applied(9, "ds_a / 返利口径说明", "admin")
    assert first == {"changed": True, "status": "applied"}
    sql, params = writes[0]
    # 条件 UPDATE 是唯一的并发仲裁点：少了 status='pending' 就会覆盖别人已下的结论
    assert "status='applied'" in sql and "status='pending'" in sql
    assert params[-1] == 9

    state["affected"] = 0
    second = kus.mark_applied(9, "再来一次", "admin")
    assert second["changed"] is False and second["status"] == "applied"
    assert "未重复标记" in second["message"]
    # 第二次仍会发一条 UPDATE，但它必须还带 pending 闸 —— 否则重复点击就能
    # 覆盖别人已经下过的结论（把 rejected 改回 applied 是最坏的一种）
    assert len(writes) == 2
    assert "status='pending'" in writes[1][0]
    assert "AND status='pending'" in writes[1][0]


def test_reject_keeps_row_and_states_reason(monkeypatch):
    """驳回留痕不删行：反复被驳的信号本身是数据。"""
    _, writes, _ = _install(monkeypatch)

    out = kus.reject_proposal(11, "admin", "口径未确认")

    assert out == {"changed": True, "status": "rejected"}
    sql, params = writes[0]
    assert "status='rejected'" in sql and "status='pending'" in sql
    assert "decided_note" in sql and "口径未确认" in params
    assert "DELETE" not in sql.upper()


def test_act_on_missing_proposal_raises(monkeypatch):
    """不存在的提议要报错，不能回"已处理"（那是假回执）。"""
    _, _, state = _install(monkeypatch)  # SELECT * 回空
    state["affected"] = 0
    with pytest.raises(ValueError):
        kus.mark_applied(404, "x", "admin")
    with pytest.raises(ValueError):
        kus.reject_proposal(404, "admin")


# ═══════════════════════════════════════════════════════════════════════════
# 3. 对象标量合并：只动三个字段，落草案不级联
# ═══════════════════════════════════════════════════════════════════════════

def _doc(objects: list) -> str:
    return json.dumps({"objects": objects}, ensure_ascii=False)


def _install_ontology(monkeypatch, *, kind="business", objects=None, draft=None,
                      draft_has=False):
    """把本体读写换成桩；返回 (saved, merged) —— merged 是最终落盘的正文。"""
    saved: dict = {}
    calls = {"draft_only": 0, "draft_full": 0}

    monkeypatch.setattr(ontology_service, "get_model",
                        lambda model_id: {"id": model_id, "name": "订单模型", "kind": kind,
                                          "status": "active",
                                          "json_content": _doc(objects or [])})
    monkeypatch.setattr(ontology_service, "get_draft",
                        lambda model_id: {"has_draft": draft_has, "draft": draft})

    def fake_save_draft_only(model_id, content, created_by=""):
        calls["draft_only"] += 1
        saved[model_id] = content

    def fake_save_draft(*a, **k):  # 生效保存会级联，本通道绝不许走到这里
        calls["draft_full"] += 1
        return {}

    monkeypatch.setattr(ontology_service, "save_draft_only", fake_save_draft_only)
    monkeypatch.setattr(ontology_service, "save_draft", fake_save_draft)
    return saved, calls


def test_merge_object_scalars_touches_only_three_fields(monkeypatch):
    """改一个对象的一句话：properties / links / 其它对象必须原样保留。"""
    target = {"key": "rebate", "display_name": "返利", "description": "旧说明",
              "aliases": ["Rebate"],
              "properties": [{"key": "amount", "display_name": "金额"}],
              "links": [{"to": "order", "type": "belongs_to"}]}
    other = {"key": "order", "display_name": "订单", "description": "订单",
             "properties": [{"key": "no"}]}
    draft = _doc([target, other])

    saved, calls = _install_ontology(monkeypatch, draft=None)
    monkeypatch.setattr(ontology_service, "get_draft", lambda model_id: {
        "has_draft": True, "draft": draft})

    out = ontology_service.merge_object_scalars(
        5, "REBATE", {"display_name": "返利金额", "description": "按确认收货月份计提",
                      "aliases": ["rebate", "返点"]}, created_by="admin")

    assert out["mode"] == "updated" and out["draft_pending"] is True
    assert set(out["changed_fields"]) == {"display_name", "description", "aliases"}
    assert calls["draft_only"] == 1 and calls["draft_full"] == 0, "必须落草案，不得走生效保存"

    doc = json.loads(saved[5])
    obj = next(o for o in doc["objects"] if o["key"] == "rebate")
    assert obj["display_name"] == "返利金额"
    assert obj["description"] == "按确认收货月份计提"
    # 别名取并集（大小写不敏感去重），既有别名不删 —— 删别名属建模决策
    assert obj["aliases"] == ["Rebate", "返点"]
    assert obj["properties"] == target["properties"]
    assert obj["links"] == target["links"]
    assert next(o for o in doc["objects"] if o["key"] == "order") == other


def test_merge_object_scalars_noop_reports_unchanged(monkeypatch):
    """提交值与当前一致：回 unchanged 且不写草案（不制造无意义的待发布版本）。"""
    obj = {"key": "rebate", "display_name": "返利", "description": "说明"}
    saved, calls = _install_ontology(monkeypatch, objects=[obj])

    out = ontology_service.merge_object_scalars(
        5, "rebate", {"display_name": "返利", "description": "说明"})

    assert out["mode"] == "unchanged" and out["changed_fields"] == []
    assert not saved and calls["draft_only"] == 0


def test_merge_object_scalars_rejects_unknown_object(monkeypatch):
    """对象不存在：报错并指路本体建模，不在本通道新增对象。"""
    _install_ontology(monkeypatch, objects=[{"key": "order", "display_name": "订单"}])
    with pytest.raises(ValueError) as exc:
        ontology_service.merge_object_scalars(5, "ghost", {"description": "x"})
    assert "ghost" in str(exc.value) and "本体建模" in str(exc.value)


def test_merge_object_scalars_rejects_system_ontology(monkeypatch):
    """系统能力本体：声明目录维护，界面改完会被刷新覆盖 → 直接拒并说明做法。"""
    saved, calls = _install_ontology(monkeypatch, kind="system", objects=[
        {"key": "asbot", "display_name": "AS-BOT"}])
    with pytest.raises(ValueError) as exc:
        ontology_service.merge_object_scalars(5, "asbot", {"description": "x"})
    assert "refresh" in str(exc.value)
    assert not saved and calls["draft_only"] == 0


def test_merge_object_scalars_rejects_blank_display_name(monkeypatch):
    """显示名不能改空：对象必须有业务名，否则下游投影全成 key。"""
    _install_ontology(monkeypatch, objects=[{"key": "rebate", "display_name": "返利"}])
    with pytest.raises(ValueError) as exc:
        ontology_service.merge_object_scalars(5, "rebate", {"display_name": "  "})
    assert "显示名" in str(exc.value)


# ═══════════════════════════════════════════════════════════════════════════
# 4. 对象目录：只列业务本体（系统本体不给入口）
# ═══════════════════════════════════════════════════════════════════════════

def test_ontology_object_catalog_skips_system_kind(monkeypatch):
    """只列 kind='business'：系统本体的当前值来自声明目录，不是这里的 draft。"""
    monkeypatch.setattr("backend.core.scenario_access.subject_catalog", lambda: [
        {"key": "rebate", "display_name": "返利", "model_id": 5,
         "model_name": "订单模型", "kind": "business"},
        {"key": "asbot", "display_name": "AS-BOT", "model_id": 6,
         "model_name": "系统能力", "kind": "system"},
    ])
    _install(monkeypatch, {
        "FROM adh_ontology_models": [{"id": 5, "name": "订单模型", "kind": "business",
                                      "datasource_id": 0, "deploy_sites": "[]"}],
    })
    _install_ontology(monkeypatch, objects=[
        {"key": "rebate", "display_name": "返利金额", "description": "按收货月计提",
         "aliases": ["rebate"]}])

    out = kus.ontology_object_catalog()

    assert [o["key"] for o in out] == ["rebate"], "系统本体不得出现在可改清单里"
    assert out[0]["display_name"] == "返利金额"
    assert out[0]["description"] == "按收货月计提"
    assert out[0]["aliases"] == ["rebate"]
    assert out[0]["model_id"] == 5 and out[0]["model_name"] == "订单模型"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Chat 工具接线：入参无内部 id，写边界只有"记下来"
# ═══════════════════════════════════════════════════════════════════════════

def test_tool_schema_carries_no_internal_id():
    """LLM 拿不到内部 id，只能猜 —— schema 里给个 id 字段等于邀请它编造。"""
    spec = function_tools.FUNCTION_ACTION_SPECS["knowledge_proposal.create"]
    props = set(spec["schema"]["properties"])
    assert props == {"target_type", "title", "content", "rationale", "target_hint",
                     "datasource"}
    assert not props & function_tools._PROPOSAL_REJECTED_KEYS
    assert spec["perm_code"] == "knowledge:manage" and spec["read_only"] is False
    assert spec["tool_name"] == "create_knowledge_proposal"


def test_tool_description_says_proposal_not_write():
    """描述必须写明"只入待审队列"：模型据描述决定要不要向用户声称已改好。"""
    desc = function_tools.FUNCTION_ACTION_SPECS["knowledge_proposal.create"]["description"]
    assert "待审队列" in desc
    assert "不会立即修改" in desc
    assert "不要向用户声称已经改好了" in desc


def test_tool_rejects_id_like_args(monkeypatch):
    """运行时也要挡：模型没读 schema 硬塞 source_id 时显式拒绝，不静默丢弃。"""
    monkeypatch.setattr(function_tools, "_identity", lambda: {"user_id": 1})
    out = function_tools._create_knowledge_proposal(
        {"target_type": "kb_doc", "title": "t", "source_id": "abc"})
    assert out["isError"] is True
    assert "source_id" in out["content"][0]["text"]