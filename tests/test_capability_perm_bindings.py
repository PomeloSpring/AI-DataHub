"""能力↔权限码「补口」回归：有页面/有接口的能力必须真有码，且码真能放行。

为什么需要：2026-11-20 用 openapi 全路由 × adh_perm_registry 逐条 fnmatch 实探，
发现一批接口"页面上有、接口上无码"——中间件 fail-closed（403）下除 admin 旁路谁都调不动：
  1. GET /api/tags*（tags:manage 只写，读面漏码）；
  2. GET/PUT/DELETE /api/playground/queries*（playground:read 的 api_pattern 当时为空串）；
  3. POST /api/lineage/{nodes,edges,parse-sql}（lineage:read 只 GET）；
  4. /api/embed/applications*、/api/embed/logs（集成应用整域无码）；
  5. PATCH /api/scheduled-tasks/tasks/*/toggle、PATCH /api/sync/tasks/*/toggle
     （manage 码方法集漏了 PATCH）。
同时锁住两类**不该补码**的面，防止下次又按"没码"去配码：
  * 路由层 require_admin 把门（/api/admin/terms*、/api/task-monitor* 等）；
  * 个人自助（中间件 _SELF_ROUTE_PATTERNS 属主校验：/api/assets*、/api/release-notes* 读）。

迁移：`docker/mysql/capability_perm_gap_migration.sql`（改口径必须同批改它并应用）。
"""
from __future__ import annotations

import fnmatch
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB_TESTS") == "1", reason="显式跳过 DB 用例")


def _db_conn():
    try:
        import pymysql

        env = {}
        path = os.path.join(REPO_ROOT, "backend", ".env")
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
        return pymysql.connect(
            host=env["METADATA_DB_HOST"], port=int(env["METADATA_DB_PORT"]),
            user=env["METADATA_DB_USER"], password=env["METADATA_DB_PASSWORD"],
            database=env["METADATA_DB_DATABASE"], charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor, autocommit=True)
    except Exception:  # noqa: BLE001
        return None


@pytest.fixture(scope="module")
def conn():
    c = _db_conn()
    if c is None:
        pytest.skip("元数据库不可用，跳过权限码口径用例")
    yield c
    c.close()


def _rows(conn, sql, args=()):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def _rules(conn):
    """(pattern, methods, perm_code) —— 与 backend/common/api_permission 同口径展开。"""
    rows = _rows(conn, """SELECT perm_code, api_pattern, api_method FROM adh_perm_registry
                          WHERE is_active=1 AND api_pattern IS NOT NULL AND api_pattern<>''""")
    out = []
    for r in rows:
        pats = [p.strip() for p in str(r["api_pattern"]).split(",") if p.strip()]
        ms = [m.strip().upper() for m in str(r["api_method"] or "GET").split(",") if m.strip()]
        for p in pats:
            out.append((p, ms, r["perm_code"]))
    return out


def _owners(conn, method, path):
    return [code for (p, ms, code) in _rules(conn)
            if fnmatch.fnmatch(path, p) and ("*" in ms or method.upper() in ms)]


# 这些路由必须有码（缺一即"给了入口给不了能力"）
REQUIRED_COVERED = [
    ("GET", "/api/tags/"),
    ("GET", "/api/tags/categories"),
    ("GET", "/api/tags/12/values"),
    ("GET", "/api/playground/queries"),
    ("PUT", "/api/playground/queries/7"),
    ("DELETE", "/api/playground/queries/7"),
    ("POST", "/api/lineage/nodes"),
    ("POST", "/api/lineage/edges"),
    ("POST", "/api/lineage/parse-sql"),
    ("GET", "/api/embed/applications"),
    ("POST", "/api/embed/applications"),
    ("PUT", "/api/embed/applications/3"),
    ("DELETE", "/api/embed/applications/3"),
    ("GET", "/api/embed/logs"),
    ("PATCH", "/api/scheduled-tasks/tasks/5/toggle"),
    ("PATCH", "/api/sync/tasks/5/toggle"),
]


def test_previously_unowned_routes_are_now_covered(conn):
    missing = [(m, p) for (m, p) in REQUIRED_COVERED if not _owners(conn, m, p)]
    assert not missing, "以下接口仍无任何权限码（fail-closed 下只有 admin 可用）: " + repr(missing)


def test_new_codes_are_narrow_and_active(conn):
    expected = {
        "tags:read": ("/api/tags*", "GET"),
        "playground:manage": ("/api/playground/queries*", "PUT,DELETE"),
        "lineage:manage": ("/api/lineage*", "POST"),
        "embed:read": ("/api/embed/applications*,/api/embed/logs", "GET"),
        "embed:manage": ("/api/embed/applications*", "POST,PUT,DELETE"),
    }
    for code, (pattern, method) in expected.items():
        row = _rows(conn, "SELECT api_pattern, api_method, is_active FROM adh_perm_registry WHERE perm_code=%s", (code,))
        assert row, f"{code} 未登记"
        r = row[0]
        assert r["is_active"] == 1, f"{code} 未启用"
        assert r["api_pattern"] == pattern, f"{code} 的 api_pattern 漂移: {r['api_pattern']!r}"
        assert r["api_method"] == method, f"{code} 的 api_method 漂移: {r['api_method']!r}"


def test_materialize_pages_are_covered_by_their_menu_codes(conn):
    """定时物化两页的接口落点必须被各自的菜单码覆盖（否则“看得见菜单、点进去 403”）。

    历史缺口：页面从 `/api/sync/*` 改读 `/api/dag/*` 后，菜单仍是 sync:* / sync-logs:read，
    而这两组码只绑了 `/api/sync*` → 非 admin 角色进去就 403。
    """
    # 菜单真值 key 必须是侧栏 toMenuKey('/data/sync/logs') 的结果（不是退役的 data:sync-logs）
    menus = {r["menu_key"]: r for r in _rows(
        conn, "SELECT menu_key, perm_code, ai_access, label FROM adh_menu_registry "
              "WHERE menu_key IN ('data:sync','data:sync/logs','data:udfs')")}
    assert set(menus) == {"data:sync", "data:sync/logs", "data:udfs"}, sorted(menus)
    assert menus["data:sync/logs"]["label"] == "运行记录"
    for key, row in menus.items():
        assert row["perm_code"], f"{key} 未挂权限码（角色页渲染不出）"
        assert row["ai_access"] == "none", f"{key} 属数据工程域，菜单侧 AI 默认级别必须为 none"

    # 页面实际调用的接口：物化任务（读写/执行）+ 运行记录（只读）
    task_read = [("GET", "/api/dag/workflows"), ("GET", "/api/dag/workflows/12"),
                 ("GET", "/api/dag/workflows/12/runs")]
    task_write = [("POST", "/api/dag/workflows"), ("PUT", "/api/dag/workflows/12"),
                  ("DELETE", "/api/dag/workflows/12"), ("DELETE", "/api/dag/runs/9"),
                  ("POST", "/api/dag/runs/batch-delete"), ("POST", "/api/dag/runs/batch-stop")]
    task_exec = [("POST", "/api/dag/workflows/12/run"), ("POST", "/api/dag/runs/9/retry")]
    logs_read = [("GET", "/api/dag/runs"), ("GET", "/api/dag/runs/9"),
                 ("GET", "/api/dag/workflows")]

    for method, path in task_read:
        assert "sync:read" in _owners(conn, method, path), f"{method} {path} 未被 sync:read 覆盖"
    for method, path in task_write:
        assert "sync:manage" in _owners(conn, method, path), f"{method} {path} 未被 sync:manage 覆盖"
    for method, path in task_exec:
        assert "sync:execute" in _owners(conn, method, path), f"{method} {path} 未被 sync:execute 覆盖"
    for method, path in logs_read:
        assert "sync-logs:read" in _owners(conn, method, path), f"{method} {path} 未被 sync-logs:read 覆盖"

    # 不扩权：没有 sync:read 的角色不应因本批变更凭空获得运行记录权限
    have_read = {r["role_id"] for r in _rows(
        conn, "SELECT role_id FROM adh_role_perms WHERE perm_code='sync:read'")}
    have_logs = {r["role_id"] for r in _rows(
        conn, "SELECT role_id FROM adh_role_perms WHERE perm_code='sync-logs:read'")}
    assert have_read <= have_logs, f"已见物化任务却看不到运行记录的角色: {sorted(have_read - have_logs)}"


def test_patch_toggles_are_writable_by_manage_codes(conn):
    # 启停是 PATCH：方法集漏 PATCH 时，持 manage 码的角色点开关恒 403
    for code in ("scheduled:manage", "sync:manage"):
        r = _rows(conn, "SELECT api_method FROM adh_perm_registry WHERE perm_code=%s", (code,))[0]
        assert "PATCH" in r["api_method"].upper(), f"{code} 缺少 PATCH"


def test_existing_role_grants_are_mirrored(conn):
    """新码必须给既有同域角色的默认授权（否则角色页"有码无人"，原页面继续 403）。"""
    pairs = [("tags:read", "tags:manage"), ("playground:manage", "playground:execute"),
             ("lineage:manage", "lineage:read")]
    for new_code, sibling in pairs:
        holders = {r["role_id"] for r in _rows(conn, "SELECT role_id FROM adh_role_perms WHERE perm_code=%s", (sibling,))}
        granted = {r["role_id"] for r in _rows(conn, "SELECT role_id FROM adh_role_perms WHERE perm_code=%s", (new_code,))}
        assert holders <= granted, f"{new_code} 漏授给持有 {sibling} 的角色: {sorted(holders - granted)}"


def test_self_service_and_admin_routes_are_not_double_gated(conn):
    """个人自助/路由层 require_admin 的面不该再套域码（两套裁决必然漂移）。

    这里只锁"不新增域码去重复裁决"的现状：这些路径若被登记成码，就必须是本文件的显式例外。
    """
    self_service = [
        ("GET", "/api/assets"),
        ("POST", "/api/assets/archive"),
        ("GET", "/api/release-notes/"),
    ]
    # 允许的既有匹配（菜单码/自助叠码都不影响属主校验）
    allowed = {"article:read", "asset:read"}
    for method, path in self_service:
        owners = set(_owners(conn, method, path)) - allowed
        assert not owners, f"{path} 被 {sorted(owners)} 门控，与属主自助重复裁决"


def test_catalog_no_code_rule_lists_only_real_admin_faces():
    """系统本体的「无码 = 仅管理员」规则不得再引用已补码的路径（历史误判）。"""
    from backend.modules.catalog.services.system_ontology_catalog import RULES

    rule = next(r for r in RULES if r["key"] == "perm_no_code_is_admin_only")
    stmt = rule["statement"]
    for stale in ("/api/sites*", "/api/apps*", "/api/saved-queries*", "/api/tags "):
        assert stale not in stmt, f"规则仍把已补码的 {stale} 当作无码能力"
    assert "/api/task-monitor*" in stmt and "/api/admin/terms*" in stmt

    self_service = next(r for r in RULES if r["key"] == "perm_self_service_owner_only")
    assert "/api/assets*" in self_service["statement"]


def test_catalog_capability_map_has_no_stale_gaps():
    """上一轮把站点/集成应用/已保存查询误判成「无码」，纠正后不得回退。"""
    from backend.modules.catalog.services.system_ontology_catalog import (
        CAPABILITY_NO_OWN_CODE, CAPABILITY_PERM_CODES)

    for key in ("site", "application", "saved_query", "tag", "lineage_node", "lineage_edge"):
        assert key in CAPABILITY_PERM_CODES, f"{key} 又变回无码能力"
        assert key not in CAPABILITY_NO_OWN_CODE
    assert set(CAPABILITY_NO_OWN_CODE) == {"user_asset"}, (
        "新增无码能力必须显式评审（目前只允许个人自助的 user_asset）")
    assert "datasource:read" in CAPABILITY_PERM_CODES["site"]
    assert "embed:manage" in CAPABILITY_PERM_CODES["application"]
    assert "playground:manage" in CAPABILITY_PERM_CODES["saved_query"]