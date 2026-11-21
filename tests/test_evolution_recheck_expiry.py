"""进化候选「自我清算」必须真的能关单（spec `Chat语义缺口驱动补全` P1.5）。

缺陷（F24/F25，2026-11-20 双库实测暴露）：
1. `recheck_candidates` 的过期判据用 `updated_at`，而 `still_open` 分支自己会
   `SET last_checked_at=NOW()`；该表 `updated_at ... ON UPDATE CURRENT_TIMESTAMP`
   被连带刷新 ⇒ 「探针天天跑 = 过期计时天天归零」，带 `object_hint` 的条目永远
   到不了 45 天。活证：`rum_event`/事件时间 created 23 天、updated 3 天。
2. `evolution_candidates_migration.sql` 的 `status` ENUM 少了 `expired`，
   而两库实际都有 —— 迁移真源与库漂移，新环境建库后过期分支写入会失败。

为什么必须与 P1 同批：P1 会让 `chat` 缺口也进这张表；漏斗不修，
「边聊边建模」的提议会在一个只进不出的队列里越积越假。
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _norm(s) -> str:
    return " ".join(str(s).split())


def _install_fakes(monkeypatch, evo, rows_by_sql: dict, still_fails: bool):
    """把库访问换成可断言的假实现；返回 (q_calls, w_calls)。"""
    q_calls: list[tuple[str, tuple]] = []
    w_calls: list[tuple[str, tuple]] = []

    def fake_q(sql, params=None):
        s = _norm(sql)
        q_calls.append((s, params))
        for marker, rows in rows_by_sql.items():
            if marker in s:
                return rows
        return []

    def fake_w(sql, params=None):
        w_calls.append((_norm(sql), params))

    monkeypatch.setattr(evo, "_q", fake_q)
    monkeypatch.setattr(evo, "_w", fake_w)
    monkeypatch.setattr(evo, "_term_still_unresolved", lambda *a, **k: still_fails)
    return q_calls, w_calls


PENDING_ROW = {
    "id": 22, "term": "事件时间", "kind": "new_dimension",
    "datasource_name": "arms-prd-cn", "object_hint": "rum_event",
    "evidence": "{}", "hit_count": 1, "updated_at": None,
}


def test_expiry_judges_on_created_at_not_updated_at(monkeypatch):
    """过期必须按 `created_at` 判：`updated_at` 会被本函数自己的回写刷新（F24 根因）。"""
    from backend.modules.catalog.services import evolution_service as evo

    q_calls, w_calls = _install_fakes(
        monkeypatch, evo,
        {"ORDER BY hit_count DESC": [PENDING_ROW],
         "created_at < DATE_SUB": [{"id": 22}]},
        still_fails=True)          # 探针判定“仍解不开” → 走 still_open 回写

    out = evo.recheck_candidates()

    expiry_sql = [s for s, _ in q_calls if "DATE_SUB" in s]
    assert expiry_sql, q_calls
    assert "created_at <" in expiry_sql[0], expiry_sql[0]
    # 反向断言：回归到 updated_at 的写法会让“永不过期”的死锁复活
    assert "updated_at <" not in expiry_sql[0], expiry_sql[0]

    assert out["expired"] == 1, out
    assert any("status='expired'" in s for s, _ in w_calls), w_calls


def test_probe_writeback_does_not_block_expiry(monkeypatch):
    """被探针反复碰过的条目依然能过期：回写只动 last_checked_at，不参与过期计时。"""
    from backend.modules.catalog.services import evolution_service as evo

    _q_calls, w_calls = _install_fakes(
        monkeypatch, evo,
        {"ORDER BY hit_count DESC": [PENDING_ROW],
         "created_at < DATE_SUB": [{"id": 22}]},
        still_fails=True)

    out = evo.recheck_candidates()

    still_open_writes = [s for s, _ in w_calls if "last_checked_at=NOW()" in s
                         and "status=" not in s]
    expired_writes = [s for s, _ in w_calls if "status='expired'" in s]
    assert still_open_writes, "探针回写分支应当执行（这是 F24 的触发条件）"
    assert expired_writes, "回写过之后仍必须能关单"
    assert out["still_open"] == 1 and out["expired"] == 1, out


def test_resolved_candidate_is_verified_not_expired(monkeypatch):
    """已能解析的词走 verified，不得被过期分支抢走（两个状态语义不同）。"""
    from backend.modules.catalog.services import evolution_service as evo

    _q_calls, w_calls = _install_fakes(
        monkeypatch, evo,
        {"ORDER BY hit_count DESC": [PENDING_ROW],
         "created_at < DATE_SUB": [{"id": 22}]},
        still_fails=False)

    out = evo.recheck_candidates()

    assert out["resolved"] == 1 and out["expired"] == 1, out
    assert any("status='verified'" in s for s, _ in w_calls), w_calls


def test_term_kind_is_unverifiable_not_expired_immediately(monkeypatch):
    """`term`（纯黑话）没有字典探针，只计入 unverifiable，不因此被提前关单。"""
    from backend.modules.catalog.services import evolution_service as evo

    row = {**PENDING_ROW, "kind": "term"}
    _q_calls, _w_calls = _install_fakes(
        monkeypatch, evo, {"ORDER BY hit_count DESC": [row]}, still_fails=True)

    out = evo.recheck_candidates()
    assert out["unverifiable"] == 1 and out["still_open"] == 0, out


# ── 迁移真源不得再漂移（F25） ─────────────────────────────────────


def test_migration_create_table_declares_expired_status():
    """CREATE TABLE 的 ENUM 必须含 `expired`，否则新环境建库后过期分支写入失败。"""
    sql = (ROOT / "docker/mysql/evolution_candidates_migration.sql").read_text(
        encoding="utf-8")
    m = re.search(r"status\s+ENUM\(([^)]*)\)", sql)
    assert m, "找不到 status ENUM 定义"
    assert "'expired'" in m.group(1), m.group(1)


def test_expired_status_migration_is_idempotent_guarded():
    """存量库对齐迁移必须自带存在性判断（表存在 且 未含 expired 才改），不写库名。"""
    path = ROOT / "docker/mysql/evolution_candidates_expired_status_migration.sql"
    sql = path.read_text(encoding="utf-8")
    assert "information_schema" in sql
    assert "COLUMN_TYPE LIKE '%expired%'" in sql
    assert "@evo_has_table = 0 OR @evo_has_expired > 0" in sql
    # 库名由连接配置决定（script-hygiene：迁移文件不得写死 USE <db>）
    assert not re.search(r"^\s*USE\s+\w+\s*;", sql, re.M), "迁移文件不得写 USE <db>"