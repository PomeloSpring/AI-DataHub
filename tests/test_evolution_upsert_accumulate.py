"""候选 upsert 必须真的累加（spec `Chat语义缺口驱动补全` P1.5 / F26）。

缺陷（2026-11-20 实测）：
`upsert_candidate` 的 UPDATE 分支 SQL 只有 10 个 `%s`，实参却给了 13 个
（多出 3 个 `payload`）。pymysql 的 `query % args` 因此每次都抛
`TypeError: not all arguments converted during string formatting`，
被 `record_parse_fail_terms` 的调用方 catch 成 warning —— **hit_count 从未累加过**。

这直接解释了现网分布：24 条里 20 条 hit_count=1（83%），
不是「用户被拒一次就换问法」，而是累加分支本身是坏的。
于是任何「hit_count 当热度门槛」的方案都是建在沙子上的。

同批修掉两个连带的采坑点：
* `IF(col='' OR %s='', %s, col)` 在新值为空时会把已有值**清空**（填空写成覆盖）；
* `evidence = %s` 直接覆盖会冲掉人工复核写入的 `triage`/`probe`（必须 JSON 合并）。
"""
import json

from backend.modules.catalog.services import evolution_service as evo


def _capture(monkeypatch, existing_rows):
    """把库访问换成捕获器：返回 (captured_writes, captured_queries)。"""
    writes: list[tuple[str, tuple]] = []
    queries: list[tuple[str, tuple]] = []

    def fake_q(sql, params=()):
        queries.append((" ".join(str(sql).split()), params))
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
    return writes, queries


def _upsert(**kw):
    base = dict(term="状态", kind="alias", source="parse_fail",
                datasource_name="prd-cn", classify_reason="测试分类",
                evidence={"reason": "unresolved"})
    base.update(kw)
    return evo.upsert_candidate(**base)


def test_update_placeholders_match_arguments(monkeypatch):
    """占位符与实参必须一对一：错位就是「静默永不累加」的根因（F26）。"""
    writes, _ = _capture(monkeypatch, [{"id": 22, "status": "pending", "hit_count": 1}])
    assert _upsert() == 22
    sql, params = writes[0]

    assert sql.count("%s") == len(params), (sql.count("%s"), len(params), sql)
    # 同一套格式化由 pymysql 执行；这里等价复现，错位会直接抛 TypeError
    rendered = sql % tuple("v" for _ in params)
    assert "UPDATE adh_evolution_candidates" in rendered


def test_update_accumulates_hit_count(monkeypatch):
    """命中数必须是 `hit_count + delta`，不是写入固定值。"""
    writes, _ = _capture(monkeypatch, [{"id": 22, "status": "pending", "hit_count": 1}])
    _upsert(delta_hits=2)
    sql, params = writes[0]
    assert "hit_count = hit_count + %s" in sql
    assert params[0] == 2


def test_update_merges_evidence_instead_of_overwriting(monkeypatch):
    """evidence 必须 JSON 合并：覆盖会冲掉人工复核写进去的 triage/probe。"""
    writes, _ = _capture(monkeypatch, [{"id": 22, "status": "pending", "hit_count": 1}])
    _upsert()
    sql, params = writes[0]
    assert "JSON_MERGE_PATCH" in sql
    assert "COALESCE(evidence, JSON_OBJECT())" in sql
    # 新证据仍是 JSON 文本，且内容就是本次信号
    assert json.loads(params[1])["reason"] == "unresolved"


def test_update_fills_blank_columns_without_clearing(monkeypatch):
    """旧写法 `IF(col='' OR %s='', %s, col)` 在新值为空时会清空已有值。"""
    writes, _ = _capture(monkeypatch, [{"id": 22, "status": "pending", "hit_count": 1}])
    _upsert(target_ref="", target_type="", object_hint="")
    sql, _params = writes[0]
    assert "OR %s=''" not in sql, "填空条件不得写成覆盖语义"
    assert "target_ref = IF(target_ref='', %s, target_ref)" in sql
    assert "object_hint = IF(object_hint='', %s, object_hint)" in sql


def test_insert_path_when_no_existing_row(monkeypatch):
    """首次出现走 INSERT：占位符同样要一对一，且状态为 pending。"""
    writes, _ = _capture(monkeypatch, [])
    assert _upsert() == 77
    sql, params = writes[0]
    assert "INSERT INTO adh_evolution_candidates" in sql
    assert sql.count("%s") == len(params), (sql.count("%s"), len(params))
    assert "'pending'" in sql


def test_manual_review_state_not_overwritten(monkeypatch):
    """已人工处置的候选：只累加证据与命中数，不写 status（防止作业把结论改回去）。"""
    writes, _ = _capture(monkeypatch, [{"id": 22, "status": "converted", "hit_count": 3}])
    _upsert()
    sql, _params = writes[0]
    assert "status" not in sql.replace("status=", ""), sql
    assert "hit_count = hit_count + %s" in sql