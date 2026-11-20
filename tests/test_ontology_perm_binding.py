"""本体域「接口 ↔ 权限码」绑定回归（漂移守卫）。

为什么需要：本体的每项能力都由权限码裁决，且**三处必须同码**——
前端显隐 `hasPerm('<码>')`、HTTP 中间件 `api_permission`（读 `adh_perm_registry.api_pattern`
+ `api_method`，按角色持码取**并集**放行，fnmatch 的 `*` 会跨 `/`）、AS-BOT 写动作
`require_write_perm('<码>')`。只要 pattern 退化成宽模式，就会出现"某个码单独就能干别的动作"
（历史漂移：`ontology:activate` 用 `POST /models*` 连带放行 publish 与 kb-sync；
`GET /kb-options`、`/kb-sync-options`、`/generate/tasks` 只被 `catalog:read` 兜住，
只持 `ontology:read` 的角色 403）。

锁定口径：
1. 本体/图谱路由除声明的例外外，必须被**本体域码**（ontology:* / graph:* / kb:*）覆盖；
   反向也锁——例外集合只能少不能多（新增未覆盖路由即红）。
2. `publish` 只归 `ontology:publish`（不得被 activate 的宽模式带过）。
3. `kb-sync` 归 `kb:manage`。
4. 本体前缀下全部 GET 归 `ontology:read`（含 kb 候选、生成任务等旁路读）。

迁移：`docker/mysql/ontology_perm_binding_migration.sql`（改绑定必须同批改它并应用）。
"""
from __future__ import annotations

import ast
import fnmatch
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 路由文件 → 挂载前缀（backend/app/main.py 的 mount(...)）
ROUTER_FILES = {
    "backend/modules/catalog/api/ontology.py": "/api/catalog/ontology",
    "backend/modules/semhub/api/graph.py": "/api/graph",
}
DOMAIN_PREFIXES = ("ontology:", "graph:", "kb:")

# 已声明例外：不由本体域码把关（只能少不能多）。
#   * 两个 DELETE：目前只被 catalog 写域（catalog:manage）覆盖，且与同路径的 PUT/POST 共享受限
#     的 api_method（一行一个方法集），拆不出来；收敛方向：给删除动作单独的码。
#   * 两个图三元组写入：裸 SPARQL update 级接口（POST /graph/nodes、/relations），
#     面向管理员/内部脚本；面向用户的 SPARQL 视图走 POST /graph/query（已归 graph:manage）。
DECLARED_UNOWNED_ROUTES = {
    ("DELETE", "/api/catalog/ontology/models/{model_id}"),
    ("DELETE", "/api/catalog/ontology/models/{model_id}/draft"),
    ("POST", "/api/graph/nodes"),
    ("POST", "/api/graph/relations"),
}


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
            database=env["METADATA_DB_DATABASE"], charset="utf8mb4", autocommit=True)
    except Exception:  # noqa: BLE001
        return None


def _routes():
    """(method, full_path) —— 直接解析装饰器，不导入路由模块（避免拉起整套依赖）。"""
    out = set()
    for rel, prefix in ROUTER_FILES.items():
        tree = ast.parse(open(os.path.join(REPO_ROOT, rel), encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                    continue
                if not (isinstance(dec.func.value, ast.Name) and dec.func.value.id == "router"):
                    continue
                if dec.func.attr not in ("get", "post", "put", "delete", "api_route"):
                    continue
                if not dec.args or not isinstance(dec.args[0], ast.Constant):
                    continue
                out.add((dec.func.attr.upper(), prefix + dec.args[0].value))
    return out


def _rules():
    conn = _db_conn()
    if conn is None:
        pytest.skip("元数据库不可用，跳过权限绑定断言")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT perm_code, api_pattern, api_method FROM adh_perm_registry "
                        "WHERE is_active=1")
            rows = cur.fetchall()
    finally:
        conn.close()
    rules = []
    for code, pattern, method in rows:
        for p in str(pattern or "").split(","):
            for m in str(method or "*").split(","):
                if p.strip() and m.strip():
                    rules.append((str(code), p.strip(), m.strip().upper()))
    return rules


def _codes_for(rules, method, path, domain_only=True):
    return {code for code, pattern, m in rules
            if m in ("*", method) and fnmatch.fnmatch(path, pattern)
            and (not domain_only or code.startswith(DOMAIN_PREFIXES))}


def test_every_ontology_route_is_owned_by_a_domain_code():
    """未被本体域码覆盖的路由必须恰好等于已声明例外（双向锁，防宽模式与漏配）。"""
    rules, routes = _rules(), _routes()
    unowned = {(method, path) for method, path in routes if not _codes_for(rules, method, path)}
    assert unowned == DECLARED_UNOWNED_ROUTES, (
        f"本体域权限绑定漂移：新出现未被本体码覆盖的路由 {sorted(unowned - DECLARED_UNOWNED_ROUTES)}；"
        f"已声明的例外被覆盖（可从 DECLARED_UNOWNED_ROUTES 移除） "
        f"{sorted(DECLARED_UNOWNED_ROUTES - unowned)}")


def test_publish_and_kb_sync_keep_their_own_codes():
    """publish 只归 ontology:publish；kb-sync 归 kb:manage（不被 activate 宽模式带过）。"""
    rules = _rules()
    publish = "/api/catalog/ontology/models/{model_id}/publish"
    assert _codes_for(rules, "POST", publish) == {"ontology:publish"}, (
        "publish 被其它本体码带过（宽 pattern 复活？）："
        f"{sorted(_codes_for(rules, 'POST', publish))}")
    kb_sync = "/api/catalog/ontology/models/{model_id}/kb-sync"
    assert _codes_for(rules, "POST", kb_sync) == {"kb:manage"}, (
        f"kb-sync 应只归 kb:manage，实际 {sorted(_codes_for(rules, 'POST', kb_sync))}")


def test_ontology_reads_are_owned_by_ontology_read():
    """本体前缀下全部 GET 归 ontology:read（含 kb 候选、生成任务等旁路读）。"""
    rules = _rules()
    missing = sorted(
        path for method, path in _routes()
        if method == "GET" and path.startswith("/api/catalog/ontology/")
        and "ontology:read" not in _codes_for(rules, method, path))
    assert not missing, f"以下 GET 未被 ontology:read 覆盖（只持该码的角色会 403）: {missing}"