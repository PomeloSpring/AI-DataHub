"""候选会话回链（spec `Chat语义缺口驱动补全` P1）。

为什么要这条链：候选表原本只记「信号管道」（`source`），不记「在哪一轮对话问出来的」。
审核面看到某个词点不回原会话，语义层补完之后也没有锚点回去验证。
本用例锁住四件事：

1. 口径：**不扩 `source` 枚举加 `chat`**（`source` 是管道，`chat` 是场景），
   "来自真实对话"只由 `origin_conversation_id <> 0` 表达；
2. 回链**只填不改**：首次出现的会话是稳定事实，后续在别的会话被撞不得覆盖；
3. evidence 里另记 `last_origin`（最近一次），与列（首次）分工明确；
4. 迁移真源与代码同步（CREATE TABLE 与存量 ALTER 都带两列，幂等、不写 `USE`）。
"""
import json
import re
from pathlib import Path

from backend.modules.catalog.services import evolution_service as evo

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "docker" / "mysql"


def _capture(monkeypatch, existing_rows):
    writes: list[tuple[str, tuple]] = []

    def fake_q(sql, params=()):
        if "SELECT id, status, hit_count" in sql:
            return list(existing_rows)
        if sql.strip().startswith("SELECT id FROM adh_evolution_candidates"):
            return [{"id": 77}]
        return []

    def fake_w(sql, params=()):
        writes.append((" ".join(str(sql).split()), params))
        return 1

    monkeypatch.setattr(evo, "_q", fake_q)
    monkeypatch.setattr(evo, "_w", fake_w)
    return writes


def _upsert(**kw):
    base = dict(term="状态", kind="alias", source="parse_fail",
                datasource_name="prd-cn", classify_reason="测试分类",
                evidence={"reason": "unresolved"})
    base.update(kw)
    return evo.upsert_candidate(**base)


def test_source_enum_not_extended_with_chat():
    """`chat` 是场景不是管道：扩进 source 会把同一事实压成两个枚举值。"""
    assert "chat" not in evo.SOURCES, evo.SOURCES


def test_update_fills_origin_only_when_empty(monkeypatch):
    """回链列只填空、不覆盖：首个会话是稳定事实。"""
    writes = _capture(monkeypatch, [{"id": 22, "status": "pending", "hit_count": 1}])
    _upsert(origin_conversation_id=123, origin_message_uuid="u-abc")
    sql, params = writes[0]

    assert "origin_conversation_id = IF(origin_conversation_id=0, %s, origin_conversation_id)" in sql
    assert "origin_message_uuid = IF(origin_message_uuid='', %s, origin_message_uuid)" in sql
    assert sql.count("%s") == len(params), (sql.count("%s"), len(params), sql)
    assert 123 in params and "u-abc" in params


def test_evidence_carries_last_origin(monkeypatch):
    """列记首次，evidence 记最近一次（JSON_MERGE_PATCH 顶层键覆盖即刷新）。"""
    writes = _capture(monkeypatch, [{"id": 22, "status": "pending", "hit_count": 1}])
    _upsert(origin_conversation_id=456, origin_message_uuid="u-xyz")
    _sql, params = writes[0]

    payload = json.loads(params[1])
    assert payload["last_origin"] == {"conversation_id": 456, "message_uuid": "u-xyz"}
    # 原信号不被挤掉
    assert payload["reason"] == "unresolved"


def test_no_origin_when_not_from_chat(monkeypatch):
    """脚本/评测批次（不传回链）不得伪造来源：写 0/空串且 evidence 不带 last_origin。"""
    writes = _capture(monkeypatch, [{"id": 22, "status": "pending", "hit_count": 1}])
    _upsert()
    _sql, params = writes[0]

    assert 0 in params
    assert "" in params
    assert "last_origin" not in json.loads(params[1])


def test_insert_path_writes_origin_columns(monkeypatch):
    """首次出现走 INSERT：两列必须入列，且占位符与实参一对一。"""
    writes = _capture(monkeypatch, [])
    assert _upsert(origin_conversation_id=9, origin_message_uuid="u-9") == 77
    sql, params = writes[0]

    assert "origin_conversation_id, origin_message_uuid" in sql
    assert sql.count("%s") == len(params), (sql.count("%s"), len(params), sql)
    assert 9 in params and "u-9" in params


def test_record_parse_fail_terms_passes_origin(monkeypatch):
    """采集入口必须把会话回链透传下去（否则列永远是 0，等于没加）。"""
    captured: list[dict] = []

    def fake_upsert(**kw):
        captured.append(kw)
        return 1

    monkeypatch.setattr(evo, "upsert_candidate", fake_upsert)
    monkeypatch.setattr(evo, "datasource_name_by_id", lambda _id: "prd-cn")
    monkeypatch.setattr(evo, "classify_term", lambda term, **kw: {
        "kind": "alias", "target_ref": "订单状态", "target_type": "dimension",
        "classify_reason": "有近似候选"})

    n = evo.record_parse_fail_terms(
        5, [{"term": "状态", "type": "dimension", "reason": "fuzzy_rejected"}],
        "订单", conversation_id=321, message_uuid="u-conv")

    assert n == 1
    assert captured[0]["origin_conversation_id"] == 321
    assert captured[0]["origin_message_uuid"] == "u-conv"


def test_semantic_query_call_site_passes_conversation():
    """工具调用点必须传会话上下文（源码级：回链不得只存在于服务层签名里）。"""
    src = (ROOT / "backend/modules/mind/execution/sdk_tools/semantic_query.py").read_text(encoding="utf-8")
    assert 'get("conversation_id")' in src
    assert "_origin_message_uuid()" in src
    idx = src.index("record_parse_fail_terms")
    tail = src[idx: idx + 800]
    assert "conversation_id" in tail and "_origin_message_uuid()" in tail


def test_migration_is_idempotent_and_carries_both_columns():
    """存量迁移：两列 + 索引、no-op 兜底、可重跑、不写库名。"""
    sql = (SQL_DIR / "evolution_candidates_origin_migration.sql").read_text(encoding="utf-8")
    assert "origin_conversation_id" in sql
    assert "origin_message_uuid" in sql
    assert "idx_evo_origin" in sql
    assert "'DO 0'" in sql, "必须先判存在性，第二次执行零变更"
    # 库名由连接配置决定（script-hygiene：不得写死 USE <db>；注释里提到它不算违规）
    assert not re.search(r"^\s*USE\s+\w+\s*;", sql, re.M), "迁移文件不得写 USE <db>"


def test_create_table_source_carries_origin_columns():
    """建库真源同步（防 F25 类漂移：库比迁移文件新 → 新环境建不起来）。"""
    sql = (SQL_DIR / "evolution_candidates_migration.sql").read_text(encoding="utf-8")
    ddl = sql[sql.index("CREATE TABLE IF NOT EXISTS adh_evolution_candidates"):]
    assert "origin_conversation_id" in ddl
    assert "origin_message_uuid" in ddl
    assert "idx_evo_origin" in ddl
    # 口径保护：source 枚举不得偷偷加 chat
    assert "'chat'" not in ddl