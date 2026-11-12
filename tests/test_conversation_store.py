"""会话消息存储改造回归（P0 入库投影 + P1 拆行 + P2 保留策略）。

覆盖三件事，都是这次改造的真实动因，不是随手断言：

1. **入库投影**：工具原始返回体/思考链/重复正文不得整份常驻（实测占会话体积 ~50%）；
2. **拆行增量写**：同一份数组重复保存必须 0 写入（旧形态每轮重写整份 LONGTEXT，
   ROW binlog 下是 O(n²) 写放大），且读写往返与旧 JSON 数组同形；
3. **退役列护栏**：任何代码不得再消费 ``adh_conversations.messages``（防双口径漂移）。

真实 MySQL 用例沿用仓库口径，需 ``ADH_TEST_AGENT_MYSQL=1`` 显式启用。
"""

import json
import os
import re
import uuid
from pathlib import Path

import pytest

from backend.core import conversation_store as store

BACKEND_ROOT = Path(__file__).resolve().parent.parent / "backend"


# ══════════════════════════════════════════════════════════════════════
# ① 入库投影（project_message）
# ══════════════════════════════════════════════════════════════════════

def test_tool_result_truncated_with_marker_not_silently_kept():
    """超上限的工具返回体截断并留可诊断标记（不静默少内容）。"""
    big = "x" * (store.tool_result_max_chars() + 500)
    out = store.project_message({
        "role": "assistant", "content": "答案",
        "tool_calls": [{"step": 1, "tool": "execute_sql", "result": big, "result_preview": "x" * 200}],
    })
    tc = out["tool_calls"][0]
    assert len(tc["result"]) <= store.tool_result_max_chars() + 1   # +1 是省略号
    assert tc["result_truncated"] is True
    assert out["tool_results_truncated"] is True


def test_zero_limit_drops_tool_result_entirely(monkeypatch):
    """上限配 0 = 该类明细完全不落（隐私最严口径可配置），并标 result_omitted。"""
    monkeypatch.setattr(store, "_limit", lambda name: 0)
    out = store.project_message({"role": "assistant", "content": "a",
                                 "tool_calls": [{"tool": "t", "result": "机密转储"}]})
    tc = out["tool_calls"][0]
    assert "result" not in tc and tc["result_omitted"] is True
    assert "thinking" not in out and "process" not in out


def test_dict_tool_result_is_serialized_before_clip():
    """非字符串返回体先序列化再判长（否则 dict 直接绕过上限）。"""
    payload = {"rows": [["v" * 50] * 20] * 60}
    out = store.project_message({"role": "assistant", "content": "a",
                                 "tool_calls": [{"tool": "t", "result": payload}]})
    assert isinstance(out["tool_calls"][0]["result"], str)
    assert len(out["tool_calls"][0]["result"]) <= store.tool_result_max_chars() + 1


def test_raw_response_deduped_against_content_but_kept_when_different():
    """content/reply/ai_raw_response 三份同文只留一份（实测各占 3.5%）。"""
    same = store.project_message({"role": "assistant", "content": "正文", "reply": "正文",
                                  "ai_raw_response": "正文"})
    assert "ai_raw_response" not in same
    diff = store.project_message({"role": "assistant", "content": "正文",
                                  "ai_raw_response": "含 ```chart 块的原始输出"})
    assert diff["ai_raw_response"] == "含 ```chart 块的原始输出"


def test_visible_answer_and_governed_rows_untouched():
    """用户可见正文与已治理结果集不被投影改动（那是用户资产，也是"存为报表"事实源）。"""
    rows = [["值", i] for i in range(1000)]
    msg = {"role": "assistant", "content": "结论文字很长" * 2000,
           "reply": "结论文字很长" * 2000,
           "result": {"columns": ["a", "b"], "rows": rows, "row_count": len(rows)}}
    out = store.project_message(msg)
    assert out["content"] == msg["content"]
    assert out["reply"] == msg["reply"]
    assert out["result"]["rows"] == rows


def test_projection_is_idempotent_and_does_not_mutate_caller():
    msg = {"role": "assistant", "content": "a", "thinking": "y" * 9000,
           "tool_calls": [{"tool": "t", "result": "z" * 9000}]}
    snapshot = json.dumps(msg, ensure_ascii=False)
    once = store.project_message(msg)
    twice = store.project_message(once)
    assert json.dumps(msg, ensure_ascii=False) == snapshot, "投影不得改调用方内存对象"
    assert once == twice, "投影必须幂等（同一内容算出同一 digest）"


# ══════════════════════════════════════════════════════════════════════
# ② 行 ↔ 消息 双向转换（前端读契约）
# ══════════════════════════════════════════════════════════════════════

def test_split_merge_roundtrip_keeps_contract_shape():
    original = {"role": "assistant", "content": "答案", "intent": "agent", "sql": None,
                "tool_calls": [{"tool": "t", "result_preview": "p"}], "message_uuid": "u1",
                "executionStats": {"num_turns": 3}}
    role, content, meta_json, uuid_ = store.split_message(original)
    assert (role, content, uuid_) == ("assistant", "答案", "u1")
    merged = store.merge_message({"role": role, "content": content, "meta": meta_json, "seq": 0})
    assert merged["intent"] == "agent" and merged["sql"] is None
    assert merged["executionStats"] == {"num_turns": 3}
    assert merged["tool_calls"][0]["result_preview"] == "p"
    assert "role" in merged and merged["content"] == "答案"


def test_role_content_not_duplicated_into_meta():
    """role/content 是独立列，再进 meta 就是同一概念两处落地。"""
    _role, _content, meta_json, _u = store.split_message({"role": "user", "content": "问题"})
    meta = json.loads(meta_json)
    assert "role" not in meta and "content" not in meta


def test_corrupt_meta_surfaces_instead_of_vanishing(caplog):
    merged = store.merge_message({"role": "assistant", "content": "a", "meta": "{坏 JSON",
                                  "conversation_id": 1, "seq": 2})
    assert merged["meta_raw"] == "{坏 JSON"     # 原文留着，不静默丢
    assert "meta JSON 损坏" in caplog.text


def test_trimmed_row_visible_to_reader():
    merged = store.merge_message({"role": "assistant", "content": "a", "meta": "{}",
                                  "storage_state": store.STORAGE_STATE_TRIMMED})
    assert merged["storage_trimmed"] is True


# ══════════════════════════════════════════════════════════════════════
# ③ 退役列护栏：不得再有第二套读写口径
# ══════════════════════════════════════════════════════════════════════

_LEGACY_ACCESS = re.compile(
    r"(SELECT[^\"']*[\s(]messages[\s,]|UPDATE\s+adh_conversations\s+SET[^\"']*messages)")


def test_no_code_path_reads_or_writes_legacy_messages_column():
    """adh_conversations.messages 退役为迁移证据列：源码级禁止再消费。

    出现新引用=有人绕开拆行表另起一套口径（读写双口径比旧实现更糟），直接失败提醒。
    """
    offenders = []
    for path in BACKEND_ROOT.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "adh_conversations.messages" in stripped and "#" in stripped:
                continue
            if _LEGACY_ACCESS.search(line):
                offenders.append(f"{path.relative_to(BACKEND_ROOT.parent)}:{lineno}: {stripped[:110]}")
    assert not offenders, "仍在使用退役列 messages：\n" + "\n".join(offenders)


# ══════════════════════════════════════════════════════════════════════
# ④ 保留策略（P2）
# ══════════════════════════════════════════════════════════════════════

def test_retention_strip_keeps_answer_and_trims_details():
    from backend.modules.mind.services import conversation_retention as ret
    meta = {"tool_calls": [{"tool": "t", "result": "原始转储" * 100, "result_preview": "p"}],
            "thinking": "很长的思考" * 500, "ai_raw_response": "原文", "process": [{"kind": "thinking"}],
            "result": {"rows": [[i] for i in range(500)], "columns": ["a"], "row_count": 500},
            "message_uuid": "u"}
    new, changed = ret._strip_details(meta)
    assert changed is True and new["storage_trimmed"] is True
    assert "result" not in new["tool_calls"][0] and new["tool_calls"][0]["result_omitted"] is True
    assert new["tool_calls"][0]["result_preview"] == "p", "摘要要留着，不能一起剥光"
    assert "thinking" not in new and "process" not in new
    from backend.common import config
    assert len(new["result"]["rows"]) == config.RETENTION_RESULT_ROWS_KEEP
    assert new["result"]["rows_total"] == 500 and new["result"]["rows_trimmed"] is True


def test_retention_strip_noop_is_detected():
    """已剥过的行不再算"变更"（幂等，重复执行不重复计数）。"""
    from backend.modules.mind.services import conversation_retention as ret
    meta = {"message_uuid": "u", "tool_calls": [{"tool": "t", "result_omitted": True}]}
    _new, changed = ret._strip_details(meta)
    assert changed is False


def test_retention_job_is_registered_in_all_four_places():
    """内置任务必须四处同批登记（注册表/回调表/任务壳/Beat），缺一处就是"配了却没跑"。"""
    from backend.common import system_jobs
    from backend.core import task_runtime

    assert "conversation_history_retention" in system_jobs.BUILTIN_JOBS
    assert "conversation_retention.run" in task_runtime._CALLBACKS
    assert callable(task_runtime.get_callback("conversation_retention.run"))

    from backend.modules.flow.tasks import executor, beat_schedule
    assert hasattr(executor, "conversation_history_retention")
    static = beat_schedule.build_beat_schedule.__code__.co_consts
    assert any("conversation_history_retention" in str(c) for c in static), "Beat 未登记该周期任务"


def test_retention_missing_config_fails_loud(monkeypatch):
    """保留期没配 = 配置缺陷，显式报错而不是"那就都别删"（把只增不减掩成稳态）。"""
    from backend.common import config
    from backend.modules.mind.services import conversation_retention as ret
    monkeypatch.setattr(config, "RETENTION_LLM_SPAN_DAYS", 0, raising=False)
    with pytest.raises(ValueError, match="RETENTION_LLM_SPAN_DAYS"):
        ret.purge_observability()


# ══════════════════════════════════════════════════════════════════════
# ⑤ 密钥门禁（P0-2）
# ══════════════════════════════════════════════════════════════════════

def test_placeholder_secret_key_is_reported(monkeypatch):
    from backend.common import crypto

    monkeypatch.setattr(crypto, "ADH_SECRET_KEY", "change-me-in-production")
    assert crypto.key_is_placeholder() is True
    assert crypto.security_advisories(), "占位密钥必须给出可见告警，不得静默假装已加密"
    monkeypatch.delenv("ADH_SECRET_KEY_STRICT", raising=False)
    crypto.assert_key_operational()          # 非严格模式：告警不阻断
    monkeypatch.setenv("ADH_SECRET_KEY_STRICT", "1")
    with pytest.raises(RuntimeError):
        crypto.assert_key_operational()      # 严格模式：拒绝启动


def test_real_secret_key_has_no_advisory(monkeypatch):
    from backend.common import crypto
    monkeypatch.setattr(crypto, "ADH_SECRET_KEY", uuid.uuid4().hex + uuid.uuid4().hex)
    assert crypto.key_is_placeholder() is False
    assert crypto.security_advisories() == []
    crypto.assert_key_operational()


def test_health_body_exposes_security_advisory(monkeypatch):
    """密钥缺陷必须挂到健康检查口（只写日志等于没人看得见）。"""
    from backend.app import main as web_main
    from backend.common import crypto

    class _Req:
        scope = {"server": ("127.0.0.1", 8000)}

    monkeypatch.setattr(crypto, "ADH_SECRET_KEY", "change-me-in-production")
    monkeypatch.delenv("ADH_SECRET_KEY_STRICT", raising=False)
    body = web_main._health_body(_Req())
    assert body["status"] == "ok"                       # 旧探测口径不变
    assert body.get("security_advisories"), "健康口未暴露密钥告警"


def test_encrypt_field_never_falls_back_to_plaintext(monkeypatch):
    """加密失败必须上抛：历史缺陷是 except 后 return value，明文落库而没人知道。"""
    from backend.common import crypto

    def _boom(_value):
        raise RuntimeError("KMS 不可用")

    monkeypatch.setattr(crypto, "encrypt_password", _boom)
    with pytest.raises(ValueError, match="加密失败"):
        crypto.encrypt_field("user@example.com")
    assert crypto.encrypt_field("") == ""


def test_live_user_path_surfaces_encrypt_failure_not_generic_error(monkeypatch):
    """挂在 /api/users 上的实现同样不得回退明文，也不得把错因洗成"创建失败"。

    历史缺陷：修好的那份在无人调用的 backend/common/auth.py 里，测试打在其上是假绿；
    真正生效的 auth_service 仍 ``return value`` 把明文落库。
    """
    from backend.common import crypto
    from backend.modules.auth.services import auth_service

    def _boom(_value):
        raise RuntimeError("KMS 不可用")

    monkeypatch.setattr(crypto, "encrypt_password", _boom)
    ok, msg, uid = auth_service.create_user("u_ok", "Aa123456!", email="a@b.com")
    assert ok is False and uid == 0
    assert "加密失败" in msg, f"错因被通用文案吞掉了：{msg}"
    assert "创建失败" != msg

    ok2, msg2 = auth_service.update_user(1, email="a@b.com")
    assert ok2 is False and "加密失败" in msg2


def test_no_plaintext_fallback_branch_anywhere():
    """源码级护栏：加密助手不得再有"失败→返回原值"的分支（双口径漂移的入口）。"""
    offenders = []
    for path in (BACKEND_ROOT / "common" / "crypto.py",
                 BACKEND_ROOT / "common" / "auth.py",
                 BACKEND_ROOT / "modules" / "auth" / "services" / "auth_service.py"):
        text = path.read_text(encoding="utf-8")
        if "Fallback to plaintext" in text or re.search(
                r"except[^\n]*:\n[^\n]*logger[^\n]*\n[^\n]*return value", text):
            offenders.append(str(path.relative_to(BACKEND_ROOT.parent)))
    assert not offenders, "仍存在明文回退分支：\n" + "\n".join(offenders)


def test_decrypt_failure_is_diagnosable(monkeypatch):
    """密文解不开要回可诊断文案（旧实现回 "***"，会被当成"已脱敏"）。"""
    from backend.common import crypto

    def _boom(_value):
        raise ValueError("Decryption failed")

    monkeypatch.setattr(crypto, "decrypt_password", _boom)
    encrypted_like = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    assert crypto.is_encrypted(encrypted_like)
    out = crypto.decrypt_field(encrypted_like)
    assert "无法解密" in out and out != "*" * 3
    assert out == crypto.DECRYPT_FAILED_HINT


# ══════════════════════════════════════════════════════════════════════
# ⑥ 真实 MySQL：拆行增量写与读写往返
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture
def mysql_conv():
    """真实元库上的隔离会话（需 ADH_TEST_AGENT_MYSQL=1）。"""
    if os.getenv("ADH_TEST_AGENT_MYSQL") != "1":
        pytest.skip("需显式启用隔离测试记录的真实 MySQL 回归")
    from backend.common.db import DBConnection, execute_insert, execute_query, execute_write

    uid = 2 ** 60 + uuid.uuid4().int % 10 ** 10
    cid = execute_insert(
        "INSERT INTO adh_conversations (user_id,title,workspace_id,as_bot_key) "
        "VALUES (%s,'__split_test__',0,'')", (uid,))
    tables = {(r.get("TABLE_NAME") or r.get("table_name")) for r in execute_query(
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()")}
    assert store.TABLE in tables, (
        f"缺表 {store.TABLE}：请先应用 docker/mysql/conversation_messages_split_migration.sql")
    try:
        yield {"user_id": uid, "id": cid, "conn": DBConnection, "query": execute_query}
    finally:
        execute_write(f"DELETE FROM {store.TABLE} WHERE conversation_id=%s", (cid,))
        execute_write("DELETE FROM adh_conversations WHERE id=%s AND user_id=%s", (cid, uid))


def _save(cid, messages, uid):
    from backend.common.db import DBConnection
    with DBConnection() as conn:
        conn.begin()
        with conn.cursor() as cur:
            out = store.save_messages(cur, cid, messages, user_id=uid)
        conn.commit()
    return out


def test_first_save_inserts_and_reread_matches_contract(mysql_conv):
    cid, uid = mysql_conv["id"], mysql_conv["user_id"]
    msgs = [{"role": "user", "content": "问题一"},
            {"role": "assistant", "content": "回答一", "intent": "agent",
             "tool_calls": [{"tool": "t", "result": "r" * 20000}]},
            {"role": "user", "content": "问题二"}]
    stats = _save(cid, msgs, uid)
    assert (stats["inserted"], stats["updated"], stats["deleted"]) == (3, 0, 0)

    loaded = store.load_messages(cid)
    assert [m["role"] for m in loaded] == ["user", "assistant", "user"]
    assert loaded[1]["intent"] == "agent"
    # 落库的是投影后的形态：正文原样，工具明细截断
    assert loaded[1]["content"] == "回答一"
    assert len(loaded[1]["tool_calls"][0]["result"]) <= store.tool_result_max_chars() + 1


def test_reshaping_same_array_writes_nothing(mysql_conv):
    """写放大消除的直接断言：同一份数组再 PUT 一次 = 0 行写入。"""
    cid, uid = mysql_conv["id"], mysql_conv["user_id"]
    msgs = [{"role": "user", "content": "a" * 5000},
            {"role": "assistant", "content": "b" * 5000, "thinking": "c" * 30000}]
    _save(cid, msgs, uid)
    again = _save(cid, msgs, uid)
    assert again == {"count": 2, "base_seq": 0, "inserted": 0, "updated": 0, "deleted": 0}


def test_append_turn_writes_one_row_and_truncation_deletes_tail(mysql_conv):
    cid, uid = mysql_conv["id"], mysql_conv["user_id"]
    base = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
    _save(cid, base, uid)
    grown = base + [{"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"}]
    assert _save(cid, grown, uid)["inserted"] == 2

    edited = [dict(grown[0], content="q1 改"), *grown[1:]]
    st = _save(cid, edited, uid)
    assert (st["inserted"], st["updated"], st["deleted"]) == (0, 1, 0)

    assert _save(cid, edited[:1], uid)["deleted"] == 3
    assert store.count_messages(cid) == 1
    assert _save(cid, [], uid)  # 清空记录
    assert store.count_messages(cid) == 0


def test_append_message_is_idempotent_by_uuid(mysql_conv):
    from backend.common.db import DBConnection
    cid, uid = mysql_conv["id"], mysql_conv["user_id"]

    def _append(turn):
        with DBConnection() as conn:
            conn.begin()
            with conn.cursor() as cur:
                out = store.append_message(cur, cid, turn, user_id=uid,
                                           message_uuid=turn["message_uuid"],
                                           question="问题", extra_fields={"fallback_persisted": True})
            conn.commit()
        return out

    turn = {"role": "assistant", "content": "兜底正文", "message_uuid": uuid.uuid4().hex,
            "tool_calls": [{"tool": "t", "result": "x" * 30000}]}
    first = _append(turn)
    assert first["persisted"] is True
    again = _append(turn)
    assert again["persisted"] is False and again["reason"] == "already_persisted_by_client"

    msgs = store.load_messages(cid)
    assert [m["role"] for m in msgs] == ["user", "assistant"], "断连兜底要成对补问题与答案"
    assert msgs[0]["content"] == "问题"
    assert msgs[1]["fallback_persisted"] is True
    assert len(msgs[1]["tool_calls"][0]["result"]) <= store.tool_result_max_chars() + 1


def test_persist_turn_fallback_skips_missing_conversation():
    """会话不存在/不属该身份时不得写出孤儿消息行。"""
    from backend.modules.mind.api.chat import persist_turn_fallback
    out = persist_turn_fallback(conversation_id=2 ** 62, user_id=2 ** 62,
                                message={"role": "assistant", "content": "x"})
    assert out == {"persisted": False, "reason": "no_conversation_or_identity"} or \
        out.get("reason") in {"conversation_not_found_or_not_owner", "empty_turn"}


def test_replace_message_by_key_updates_single_row(mysql_conv):
    from backend.common.db import DBConnection
    from backend.modules.catalog.api.ontology import _TASK_KIND
    cid, uid = mysql_conv["id"], mysql_conv["user_id"]
    _save(cid, [{"role": "user", "content": "生成本体模型"}], uid)

    def _write(payload):
        with DBConnection() as conn:
            conn.begin()
            with conn.cursor() as cur:
                out = store.replace_message_by_key(
                    cur, cid, match={"role": "assistant", "task_kind": _TASK_KIND},
                    message=payload, user_id=uid)
            conn.commit()
        return out

    _write({"role": "assistant", "content": "批次 1/3", "task_kind": _TASK_KIND,
            "progressStages": [{"stage": "s", "message": "m", "timestamp": "t"} for _ in range(300)]})
    # 投影在写入时生效：进度阶段超限被截到上限（截断标记可诊断）——必须在被后续
    # 写入覆盖前核验，否则断的是不存在的键。
    stage_row = store.load_messages(cid)[1]
    assert len(stage_row["progressStages"]) == store.progress_stage_max()
    assert stage_row["progressStages_truncated"] is True

    _write({"role": "assistant", "content": "批次 2/3", "task_kind": _TASK_KIND})
    _write({"role": "assistant", "content": "完成", "task_kind": _TASK_KIND})

    msgs = store.load_messages(cid)
    assert len(msgs) == 2, "同一任务只保留一条进度消息（不越写越多）"
    assert msgs[1]["content"] == "完成"


def test_retention_trim_runs_against_real_rows(mysql_conv):
    from backend.common.db import DBConnection, execute_write
    from backend.modules.mind.services import conversation_retention as ret
    cid, uid = mysql_conv["id"], mysql_conv["user_id"]
    _save(cid, [{"role": "user", "content": "q"},
                {"role": "assistant", "content": "a", "thinking": "t" * 9000,
                 "tool_calls": [{"tool": "x", "result": "r" * 9000}]}], uid)
    # 把会话时间推到保留期之外（不改代码里的默认天数，避免测试依赖配置）
    execute_write("UPDATE adh_conversations SET updated_at = NOW() - INTERVAL 900 DAY WHERE id=%s", (cid,))

    plan = ret.trim_stale_conversations(days=30, dry_run=True, conversation_ids=[cid])
    assert plan["conversations"] == 1 and plan["dry_run"] is True
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) c FROM {store.TABLE} WHERE conversation_id=%s "
                        "AND storage_state='full'", (cid,))
            assert cur.fetchone()["c"] == 2, "dry-run 不得落库"

    done = ret.trim_stale_conversations(days=30, conversation_ids=[cid])
    assert done["messages_trimmed"] >= 1 and done["bytes_saved"] > 0
    msgs = store.load_messages(cid)
    assert msgs[1]["content"] == "a", "正文必须留着"
    assert msgs[1]["storage_trimmed"] is True and "thinking" not in msgs[1]
    assert ret.trim_stale_conversations(
        days=30, conversation_ids=[cid])["messages_trimmed"] == 0, "重复执行不重复计数"


def test_retention_scope_does_not_touch_other_conversations(mysql_conv):
    """定点范围必须真生效：不带 scope 的全表裁剪在测试里会动别人的会话（事故）。"""
    from backend.common.db import execute_query, execute_write
    from backend.modules.mind.services import conversation_retention as ret
    cid, uid = mysql_conv["id"], mysql_conv["user_id"]
    _save(cid, [{"role": "assistant", "content": "a", "thinking": "t" * 9000}], uid)
    execute_write("UPDATE adh_conversations SET updated_at = NOW() - INTERVAL 900 DAY WHERE id=%s", (cid,))

    stale_all = execute_query(
        "SELECT c.id AS conversation_id FROM adh_conversations c "
        "WHERE c.updated_at < (NOW() - INTERVAL 30 DAY) "
        f"  AND EXISTS (SELECT 1 FROM {store.TABLE} m "
        "              WHERE m.conversation_id = c.id AND m.storage_state='full')") or []
    others = [int(r["conversation_id"]) for r in stale_all if int(r["conversation_id"]) != cid]
    if not others:
        pytest.skip("库里暂无其它陈旧会话，无法验证范围隔离")

    def _full_count() -> int:
        return sum(1 for _ in execute_query(
            f"SELECT m.seq FROM {store.TABLE} m WHERE m.conversation_id IN "
            f"({','.join(['%s'] * len(others))}) AND m.storage_state='full'", tuple(others)) or [])

    before = _full_count()
    ret.trim_stale_conversations(days=30, conversation_ids=[cid])
    assert _full_count() == before, "scope 外的会话被裁了"
    assert store.load_messages(cid)[0].get("storage_trimmed") is True, "scope 内要裁到"


def test_cascade_deletes_remove_split_rows(mysql_conv):
    from backend.common.db import DBConnection, execute_query
    cid, uid = mysql_conv["id"], mysql_conv["user_id"]
    _save(cid, [{"role": "user", "content": "q"}], uid)
    with DBConnection() as conn:
        with conn.cursor() as cur:
            removed = store.delete_by_conversation(cur, cid)
        conn.commit()
    assert removed == 1
    assert not execute_query(f"SELECT id FROM {store.TABLE} WHERE conversation_id=%s", (cid,))

    # 工作空间级联清单必须包含拆行表（只删会话行会留孤儿正文）
    from backend.modules.auth.services.workspace_deletion import build_cascade_statements
    stmts = build_cascade_statements(7, uid, {}, {store.TABLE})
    assert any(store.TABLE in sql for sql, _ in stmts)


# ══════════════════════════════════════════════════════════════════════
# ⑧ 详情页分页读（P1）与窗口写回护栏
# ══════════════════════════════════════════════════════════════════════

def test_detail_cache_key_carries_pagination():
    """详情缓存键必须带分页参数：不然“最近 60 条”会被当整份缓存住，翻页永远读不到旧页。"""
    from backend.modules.mind.api import chat
    from backend.common.ttl_cache import conversation_cache

    assert chat._conv_detail_key(150) != chat._conv_detail_key(150, 60)
    assert chat._conv_detail_key(150, 60) != chat._conv_detail_key(150, 60, 40)
    for key in (chat._conv_detail_key(150), chat._conv_detail_key(150, 60),
                chat._conv_detail_key(150, 60, 40), chat._conv_detail_key(1500, 60)):
        conversation_cache.set(key, {"marker": key})
    chat._invalidate_conv_cache(7, 150)
    assert conversation_cache.get(chat._conv_detail_key(150)) is None
    assert conversation_cache.get(chat._conv_detail_key(150, 60)) is None, "分页键没清掉"
    assert conversation_cache.get(chat._conv_detail_key(1500, 60)) is not None, "误清了别的会话（前缀碰撞）"


def test_load_page_pages_backwards_without_losing_rows(mysql_conv):
    """分页游标必须连贯：翻完所有页得到的条数 = 总数，且不重不漏。"""
    cid, uid = mysql_conv["id"], mysql_conv["user_id"]
    _save(cid, [{"role": "user", "content": f"q{i}"} for i in range(10)], uid)

    first = store.load_page(cid, limit=4)
    assert first["total"] == 10 and first["oldest_seq"] == 6
    assert first["has_more_earlier"] is True

    got = [m["content"] for m in first["messages"]]
    page = first
    while page["has_more_earlier"]:
        page = store.load_page(cid, limit=4, before_seq=page["oldest_seq"])
        got = [m["content"] for m in page["messages"]] + got
    assert got == [f"q{i}" for i in range(10)], "翻页不重不漏"
    assert page["oldest_seq"] == 0 and page["has_more_earlier"] is False


def test_windowed_save_does_not_erase_older_rows(mysql_conv):
    """分页窗口写回不得抹历史（本项改造最大的风险）。

    旧语义是“数组变短 = 删尾部”；只加载了最近 4 条时拿这 4 条去 PUT，
    不声明窗口起点就会把更早的 6 条删掉 —— 用户看到的是记录凭空变短。
    """
    from backend.common.db import DBConnection
    cid, uid = mysql_conv["id"], mysql_conv["user_id"]
    full = [{"role": "user", "content": f"old{i}"} for i in range(6)] \
        + [{"role": "assistant", "content": f"new{i}"} for i in range(4)]
    _save(cid, full, uid)

    window = full[6:]                                    # 本地只有最近 4 条
    with DBConnection() as conn:
        conn.begin()
        with conn.cursor() as cur:
            stats = store.save_messages(cur, cid, window, user_id=uid, base_seq=6)
        conn.commit()
    assert (stats["inserted"], stats["updated"], stats["deleted"]) == (0, 0, 0), "窗口未变就不该有写入"
    assert store.count_messages(cid) == 10, "窗口外的更早消息被抹掉了"

    # 窗口里删一条：只删窗口尾部一条，窗口前 6 条仍然完好
    with DBConnection() as conn:
        conn.begin()
        with conn.cursor() as cur:
            stats2 = store.save_messages(cur, cid, window[:3], user_id=uid, base_seq=6)
        conn.commit()
    assert stats2["deleted"] == 1
    assert [m["content"] for m in store.load_messages(cid)] == \
        [f"old{i}" for i in range(6)] + ["new0", "new1", "new2"]


def test_stale_window_start_fails_loud(mysql_conv):
    """窗口起点越界（例如别处刚清空）：报错提醒重拉，不拿旧窗口继续写也不静默对齐。"""
    from backend.common.db import DBConnection
    cid, uid = mysql_conv["id"], mysql_conv["user_id"]
    _save(cid, [{"role": "user", "content": "only"}], uid)

    with DBConnection() as conn:
        conn.begin()
        with conn.cursor() as cur:
            with pytest.raises(ValueError, match="会话历史已变化"):
                store.save_messages(cur, cid, [{"role": "user", "content": "x"}],
                                    user_id=uid, base_seq=9)
            conn.rollback()
    assert [m["content"] for m in store.load_messages(cid)] == ["only"]


def test_full_save_keeps_legacy_semantics(mysql_conv):
    """不传 base_seq 时行为与旧口径完全一致（数组变短就截断）：旧客户端零变化。"""
    cid, uid = mysql_conv["id"], mysql_conv["user_id"]
    _save(cid, [{"role": "user", "content": f"q{i}"} for i in range(5)], uid)
    stats = _save(cid, [{"role": "user", "content": "q0"}], uid)
    assert stats["deleted"] == 4 and store.count_messages(cid) == 1
