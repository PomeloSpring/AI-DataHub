"""业务本体承载（carrier）跨源拓扑回归 — M1 承载事实源与投影口。

锁住的行为：
1. 写侧校验全部 fail-loud（只含业务名，不含 id/物理表列）：对象归属悬空、源本体对象
   不存在、数据源与源本体归属错配、global/log/mirror 缺数据源或源对象、类型/新鲜度
   非法、聚合态站点、同对象多权威口径、carrier_key 重复；
2. LLM 视图脱敏（护栏 §7）：返回体只有业务名级字段，不得出现 datasource_id/物理表；
3. 授权裁剪 fail-closed（waker-datasource-domain §1）：空角色授权 → 空视图，
   未授权源的承载不可见，且 `carriers_filtered` 计数可区分"真无承载"与"有权看不到"；
4. 整模型替换语义：未列出的既有承载置 deprecated（不物理删），已 deprecated 的不重复写；
5. 校验先于写入：任何一条不合法都不产生写语句（不半截落库）；
6. 唯一读取口纪律：除 topology.py 与迁移文件外，不得有第二处 SELECT 该表（防双口径）；
7. 迁移幂等：CREATE TABLE IF NOT EXISTS + UTC_TIMESTAMP 默认值（写本地时会让按 UTC
   窗口的监控页恒为空）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.semantics import topology as tp

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "docker" / "mysql" / "ontology_carrier_migration.sql"

MODEL_ID = 1789467214865307
# 写侧用例一律用虚拟模型 id：万一假连接没接住（打桩目标错），也不会往真实模型上落脏数据
WRITE_MODEL_ID = 9990003


# ── 校验（读侧依赖全部打桩，不连库）──────────────────────────────────

@pytest.fixture(autouse=True)
def _stub_lookups(monkeypatch):
    monkeypatch.setattr(tp, "_model_object_keys", lambda mid: {"user", "case"})
    # 拓扑校验走 L1 的 core.ontology_index（不得反向依赖 modules，分层契约 R3）
    import backend.core.ontology_index as oi
    monkeypatch.setattr(oi, "datasource_name_set", lambda: {"Doris", "arms-prd-cn", "prd-cn"})
    monkeypatch.setattr(oi, "active_source_ontology_index", lambda: {
        "数据仓库模型": {"id": 1, "datasource_name": "Doris",
                          "object_keys": {"user_registration_stat", "case_stat"}},
        "ARMS RUM 观测本体": {"id": 2, "datasource_name": "arms-prd-cn",
                              "object_keys": {"rum_event"}},
    })
    sites = {"cn": {"site_code": "cn", "display_name": "中国站点", "is_active": 1, "is_aggregate": 0},
             "jp": {"site_code": "jp", "display_name": "日本站点", "is_active": 1, "is_aggregate": 0},
             "global": {"site_code": "global", "display_name": "全球站点", "is_active": 1,
                        "is_aggregate": 1}}
    monkeypatch.setattr(tp, "_site_or_none", lambda code: sites.get(code))


def _errors(rows):
    return tp.validate_carriers(MODEL_ID, rows)


def _global_carrier(**over):
    row = {"object_key": "user", "carrier_key": "global", "display_name": "数仓全局汇总",
           "carrier_type": "global", "datasource": "Doris",
           "source_model": "数据仓库模型", "source_object": "user_registration_stat",
           "grain": "日汇总", "freshness": "t_plus_1", "as_of_note": "T+1，不含今天"}
    row.update(over)
    return row


def test_valid_carriers_pass():
    assert _errors([_global_carrier(),
                    {"object_key": "user", "carrier_key": "detail", "carrier_type": "detail",
                     "display_name": "各站点明细", "sites": ["cn", "jp"]},
                    {"object_key": "case", "carrier_key": "sls_log", "carrier_type": "log",
                     "datasource": "arms-prd-cn", "source_model": "ARMS RUM 观测本体",
                     "source_object": "rum_event"}]) == []


def test_rejects_object_not_in_model_doc():
    errs = _errors([_global_carrier(object_key="ghost")])
    assert any("不在本模型的对象清单" in e for e in errs)
    assert any("user" in e for e in errs)          # 报错带可用候选（可操作提示）


def test_rejects_missing_carrier_key_and_duplicate_key():
    assert any("缺少承载标识" in e for e in _errors([_global_carrier(carrier_key="")]))
    assert any("重复" in e for e in _errors([_global_carrier(), _global_carrier()]))


def test_rejects_illegal_type_and_freshness():
    errs = _errors([_global_carrier(carrier_type="guess", freshness="hourly")])
    assert any("承载类型" in e for e in errs)
    assert any("新鲜度" in e for e in errs)


def test_global_carrier_requires_datasource_and_source_object():
    errs = _errors([{"object_key": "user", "carrier_key": "global", "carrier_type": "global"}])
    assert any("必须指定数据源" in e for e in errs)
    assert any("必须指明「源本体对象」" in e for e in errs)


def test_rejects_dangling_source_object():
    errs = _errors([_global_carrier(source_object="no_such_obj")])
    assert any("不存在对象" in e for e in errs)


def test_rejects_unknown_source_model_with_candidates():
    errs = _errors([_global_carrier(source_model="无此源本体")])
    assert any("没有生效版本" in e for e in errs)
    assert any("数据仓库模型" in e for e in errs)   # 带可用源本体清单


def test_rejects_datasource_mismatch_with_source_model():
    """数据源与源本体实际归属不一致 → 防错配拒绝（拿 A 源形状查 B 源是错数）。"""
    errs = _errors([_global_carrier(datasource="arms-prd-cn")])
    assert any("不一致" in e for e in errs)


def test_rejects_unknown_datasource():
    errs = _errors([_global_carrier(datasource="已删除的源")])
    assert any("不存在" in e for e in errs)


def test_rejects_aggregate_site_and_unknown_site():
    errs = _errors([_global_carrier(sites=["global", "mars"])])
    assert any("聚合态" in e for e in errs)
    assert any("不在启用站点清单" in e for e in errs)


def test_rejects_multiple_authority_per_object():
    errs = _errors([_global_carrier(is_authority=1),
                    _global_carrier(carrier_key="global2", is_authority=1)])
    assert any("权威承载" in e for e in errs)


def test_error_messages_carry_no_physical_ids():
    """错误文案只含业务名（护栏 §7）：不得出现 id/物理表/host。"""
    errs = _errors([_global_carrier(object_key="ghost", datasource="?", source_model="?")])
    blob = json.dumps(errs, ensure_ascii=False)
    assert "datasource_id" not in blob and "physical_table" not in blob
    assert not re.search(r"\bMODEL_ID\b|\bid\s*[:=]\s*\d", blob, re.I)


# ── LLM 视图：脱敏 + 授权裁剪 ──────────────────────────────────────────

_FULL = {
    "id": 7, "model_id": MODEL_ID, "object_key": "user", "carrier_key": "global",
    "display_name": "数仓全局汇总", "carrier_type": "global", "datasource": "Doris",
    "sites": ["cn"], "source_model": "数据仓库模型", "source_object": "user_registration_stat",
    "grain": "日汇总", "freshness": "t_plus_1", "as_of_note": "T+1，不含今天",
    "is_authority": 1, "priority": 10, "size_class": "large", "status": "active",
    "created_by": "admin", "updated_at": "2026-10-28 00:00:00",
}


@pytest.fixture
def _one_carrier(monkeypatch):
    monkeypatch.setattr(tp, "carriers_by_object", lambda keys: {"user": [dict(_FULL)]})


@pytest.fixture
def _auth(monkeypatch):
    """打桩角色授权（数据源 id 集）与可查站点派生。"""
    state = {"ids": [101], "sites": [{"site_code": "cn", "display_name": "中国站点"}]}

    from backend.core.role_service import role_service
    monkeypatch.setattr(role_service, "get_user_allowed_datasources",
                        lambda uid, wid=0: list(state["ids"]))
    monkeypatch.setattr(tp, "_datasource_id_by_name", lambda: {"Doris": 101, "prd-cn": 102})
    import backend.core.site_service as ss
    monkeypatch.setattr(ss, "authorized_sites", lambda uid, wid=0: list(state["sites"]))
    return state


def test_llm_view_is_name_level_only(_one_carrier, _auth):
    out = tp.carriers_for_llm(["user"], user_id=1, workspace_id=1)
    view = out["carriers"]["user"][0]
    assert view["name"] == "数仓全局汇总" and view["type"] == "global"
    assert view["datasource"] == "Doris" and view["sites"] == ["中国站点"]
    assert view["authority"] is True and view["as_of_note"] == "T+1，不含今天"
    blob = json.dumps(view, ensure_ascii=False)
    for forbidden in ("source_model", "source_object", "user_registration_stat",
                      "datasource_id", "physical_table", "\"id\""):
        assert forbidden not in blob, f"LLM 视图泄露物理/内部字段: {forbidden}"


def test_empty_role_grant_is_fail_closed(_one_carrier, _auth):
    """空授权不解释为全量（waker-datasource-domain §1）：视图空，但计数可区分。"""
    _auth["ids"] = []
    out = tp.carriers_for_llm(["user"], user_id=1, workspace_id=1)
    assert out["carriers"] == {}
    assert out["carriers_filtered"] == 1        # "有承载但无权" ≠ "确实没有承载"


def test_unauthorized_datasource_carrier_hidden(_one_carrier, _auth):
    _auth["ids"] = [999]                        # 只有别的源
    out = tp.carriers_for_llm(["user"], user_id=1, workspace_id=1)
    assert out["carriers"] == {} and out["carriers_filtered"] == 1


def test_site_only_carrier_requires_one_authorized_site(_auth):
    detail = {**_FULL, "carrier_key": "detail", "carrier_type": "detail", "datasource": ""}
    detail["sites"] = ["jp"]
    from backend.semantics import topology as mod
    orig = mod.carriers_by_object
    mod.carriers_by_object = lambda keys: {"user": [detail]}
    try:
        out = mod.carriers_for_llm(["user"], user_id=1, workspace_id=1)
        assert out["carriers"] == {} and out["carriers_filtered"] == 1   # 只授权 cn，jp 承载不可见
        detail["sites"] = ["cn"]
        assert mod.carriers_for_llm(["user"], user_id=1, workspace_id=1)["carriers"]["user"]
    finally:
        mod.carriers_by_object = orig


def test_no_object_keys_short_circuits():
    assert tp.carriers_for_llm([], user_id=1) == {"carriers": {}, "carriers_filtered": 0}


# ── 写入：整模型替换 + 校验先于写 ──────────────────────────────────────

class _RecordingCursor:
    def __init__(self, existing, sink):
        self._existing = existing
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sink.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self._existing


class _RecordingConn:
    def __init__(self, existing):
        self.existing = existing
        self.sink = []
        self.committed = False

    def cursor(self):
        return _RecordingCursor(self.existing, self.sink)

    def commit(self):
        self.committed = True

    def close(self):
        pass


@pytest.fixture
def _fake_write_conn(monkeypatch):
    """既有承载：case/detail 本次未列出→应置 deprecated；case/old 已 deprecated→不重复写。"""
    existing = [
        {"id": 11, "object_key": "user", "carrier_key": "detail", "status": "active"},
        {"id": 12, "object_key": "user", "carrier_key": "global", "status": "active"},
        {"id": 13, "object_key": "case", "carrier_key": "old", "status": "deprecated"},
        {"id": 14, "object_key": "case", "carrier_key": "detail", "status": "active"},
    ]
    conn = _RecordingConn(existing)
    monkeypatch.setattr(tp, "get_metadata_conn", lambda: conn)
    monkeypatch.setattr(tp, "validate_carriers", lambda mid, rows: [])
    monkeypatch.setattr(tp, "list_carriers",
                        lambda **kw: [{"object_key": "user", "carrier_key": "global",
                                       "display_name": "数仓全局汇总", "carrier_type": "global"}])
    monkeypatch.setattr(tp, "invalidate_topology_cache", lambda: None)
    # 防误写真实库：本 fixture 下所有连接必须落在假连接上
    return conn


def test_save_replaces_whole_model_set(_fake_write_conn):
    rows = [{"object_key": "user", "carrier_key": "global", "carrier_type": "global",
             "display_name": "数仓全局汇总", "datasource": "Doris",
             "source_model": "数据仓库模型", "source_object": "user_registration_stat"},   # 既有 → UPDATE
            {"object_key": "user", "carrier_key": "detail", "carrier_type": "detail"},      # 既有 → UPDATE
            {"object_key": "case", "carrier_key": "sls_log", "carrier_type": "detail"}]     # 新增 → INSERT
    result = tp.save_carriers(WRITE_MODEL_ID, rows, created_by="tester")
    sqls = [s for s, _ in _fake_write_conn.sink]
    # 假连接确实接住了（打桩目标错就会往真实库写，这行断言是护栏）
    assert any(s.startswith("SELECT id, object_key, carrier_key, status FROM adh_ontology_carriers")
               for s in sqls)
    assert result["model_id"] == WRITE_MODEL_ID and result["saved"] == 1
    # 未列出的 active 行 → 置 deprecated（可回退，不物理删除）
    assert any("SET status = 'deprecated'" in s and "WHERE id = %s" in s for s in sqls)
    assert any(s.startswith("UPDATE adh_ontology_carriers SET") for s in sqls)
    assert any(s.startswith("INSERT INTO adh_ontology_carriers") for s in sqls)
    # 已是 deprecated 的行不重复写（避免无意义 updated_at 抖动）
    assert sum(1 for s in sqls if "SET status = 'deprecated'" in s) == 1
    assert _fake_write_conn.committed is True


def test_created_by_only_on_insert(_fake_write_conn):
    """created_by 只在新增时写（不覆盖既有承载的创建人）；身份服务端注入。"""
    tp.save_carriers(WRITE_MODEL_ID, [
        {"object_key": "user", "carrier_key": "global", "carrier_type": "global",
         "display_name": "数仓全局汇总"},                       # 既有行 → UPDATE
        {"object_key": "user", "carrier_key": "brand_new", "carrier_type": "detail"},  # 新行 → INSERT
    ], created_by="tester")
    insert = [(s, p) for s, p in _fake_write_conn.sink if s.startswith("INSERT")][0]
    assert insert[1][-1] == "tester"
    upsert = [(s, p) for s, p in _fake_write_conn.sink
              if s.startswith("UPDATE adh_ontology_carriers SET display_name")]
    assert upsert and "tester" not in (upsert[0][1] or ())   # 不覆盖既有创建人


def test_updated_at_written_in_utc(_fake_write_conn):
    """写 UPDATE 负列上的 ON UPDATE CURRENT_TIMESTAMP 是服务器本地时区，
    会把 updated_at 抬成 +8 时刻（按 UTC 窗口的监控页永远看不到）→ 必须显式写 UTC。"""
    tp.save_carriers(WRITE_MODEL_ID, [{"object_key": "user", "carrier_key": "detail",
                                       "carrier_type": "detail"}], created_by="tester")
    writes = [s for s, _ in _fake_write_conn.sink if s.startswith("UPDATE adh_ontology_carriers")]
    assert writes and all("UTC_TIMESTAMP(6)" in s for s in writes)


def test_invalid_config_writes_nothing(monkeypatch):
    conn = _RecordingConn([])
    monkeypatch.setattr(tp, "get_metadata_conn", lambda: conn)
    monkeypatch.setattr(tp, "_model_object_keys", lambda mid: {"user"})
    with pytest.raises(tp.CarrierError):
        tp.save_carriers(WRITE_MODEL_ID, [{"object_key": "user", "carrier_key": "x",
                                       "carrier_type": "bogus"}])
    assert [s for s, _ in conn.sink if s.upper().startswith(("INSERT", "UPDATE"))] == []


def test_save_requires_model_id(monkeypatch):
    monkeypatch.setattr(tp, "validate_carriers", lambda mid, rows: [])
    with pytest.raises(tp.CarrierError):
        tp.save_carriers(0, [])


# ── 唯一读取口 + 迁移幂等（静态护栏）──────────────────────────────────

def test_only_topology_reads_carrier_table():
    """除 topology.py 外，不得有第二处读写承载表（统一经唯一投影口）。

    双口径漂移的历史教训：站点映射曾在 doc/图谱/提示词各写一份，改一处漏两处。
    只比 SQL 语句形态，注释/docstring 里提表名不算第二读取口。
    """
    usage = re.compile(r"(FROM|INTO|UPDATE)\s+adh_ontology_carriers\b", re.I)
    offenders = []
    roots = [REPO_ROOT / "backend", REPO_ROOT / "frontend" / "src"]
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".ts", ".tsx"):
                continue
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if not usage.search(text):
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel == "backend/semantics/topology.py":
                continue
            offenders.append(rel)
    assert offenders == [], f"承载表出现第二读写口（应统一经 topology.py）: {offenders}"


def test_migration_is_idempotent_and_utc_defaulted():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS adh_ontology_carriers" in sql
    assert "NOT LIKE" in sql                       # 权限补写可重跑（不重复 CONCAT）
    assert "DEFAULT (UTC_TIMESTAMP())" in sql      # 写本地时会让 UTC 窗口监控页恒为空
    assert "ON UPDATE CURRENT_TIMESTAMP" in sql
    for col in ("carrier_type", "datasource", "source_model", "source_object",
                "freshness", "is_authority", "priority", "status"):
        assert col in sql
    assert "uk_carrier" in sql                     # 对象内承载标识唯一


def test_carrier_endpoints_registered_on_ontology_router():
    """路由存在性 + 仅业务本体可写（源本体是单源物理投影，不适用承载）。"""
    from backend.modules.catalog.api import ontology as api
    paths = {getattr(r, "path", "") for r in api.router.routes}
    assert "/models/{model_id}/carriers" in paths
    src = Path(api.__file__).read_text(encoding="utf-8")
    assert "仅业务本体适用承载清单" in src
