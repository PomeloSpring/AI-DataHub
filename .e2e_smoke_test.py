#!/usr/bin/env python3
"""E2E API smoke test v2 — frontend-exact paths via proxy (port 3000).

Skips AI chat endpoints per request. Validates responses are JSON
(detects Vite SPA HTML fallback = wrong proxy routing).
"""
import json
import urllib.request
import urllib.error

BASE = "http://localhost:3000"
TOKEN = None
WORKSPACE_ID = 0

def req(method, path, body=None, token=True, timeout=20):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token and TOKEN:
        r.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode(errors="replace"), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:400], e.headers.get("Content-Type", "") if e.headers else ""
    except Exception as e:
        return -1, str(e)[:300], ""

# ── Login ──
status, raw, _ = req("POST", "/api/auth/login", {"username": "admin", "password": "admin123"}, token=False)
print(f"[LOGIN] /api/auth/login -> {status}")
if status != 200:
    print(f"FATAL: login failed: {raw[:200]}")
    raise SystemExit(1)
TOKEN = json.loads(raw)["access_token"]
print("        token acquired")

results = []
def check(name, method, path, body=None, allow=None):
    status, raw, ctype = req(method, path, body)
    is_html = "text/html" in ctype
    ok = status in (allow or (200, 201)) and not is_html
    results.append((ok, name, path, status, raw if not ok else "", is_html))
    mark = "PASS" if ok else "FAIL"
    extra = " [HTML-fallback!]" if is_html else ""
    print(f"[{mark}] {name:38s} {method:6s} {path} -> {status}{extra}" + ("" if ok else f"  | {raw[:130]}"))

# ── AuthService ──
check("auth: me",             "GET", "/api/users/me")
check("auth: user list",      "GET", "/api/users/")
check("auth: workspaces",     "GET", "/api/workspaces/")
check("auth: roles",          "GET", "/api/roles/")
check("auth: audit logs",     "GET", "/api/audit/logs?page=1&page_size=5")

# workspace id for later tests
status, raw, _ = req("GET", "/api/workspaces/")
if status == 200:
    try:
        ws_list = json.loads(raw)
        if ws_list:
            WORKSPACE_ID = ws_list[0].get("id", 0)
    except Exception:
        pass
print(f"        (using workspace_id={WORKSPACE_ID})")

if WORKSPACE_ID:
    check("auth: ws tools",       "GET", f"/api/workspaces/{WORKSPACE_ID}/tools")
    check("auth: ws users",       "GET", f"/api/workspaces/{WORKSPACE_ID}/users")

# ── DataCatalog ──
check("catalog: tables",      "GET", "/api/catalog/tables")
check("catalog: datasources", "GET", "/api/datasources/")
check("catalog: metrics",     "GET", "/api/metrics/")
check("catalog: tag cats",    "GET", "/api/tags/categories")
check("catalog: tags",        "GET", "/api/tags/")
check("catalog: admin meta",  "GET", "/api/admin/metadata?page=1&page_size=5")
check("catalog: templates",   "GET", "/api/admin/templates/")
check("catalog: terms",       "GET", "/api/admin/terms/")
check("catalog: relations",   "GET", "/api/admin/relations/")
check("catalog: menu tree",   "GET", "/api/admin/menu-tree")

# ── DataGov ──
check("gov: quality rules",   "GET", f"/api/quality/rules?workspace_id={WORKSPACE_ID}")
check("gov: quality results", "GET", "/api/quality/results")
check("gov: quality reports", "GET", f"/api/quality/reports?workspace_id={WORKSPACE_ID}")
check("gov: quality dash",    "GET", f"/api/quality/dashboard?workspace_id={WORKSPACE_ID}")
check("gov: lineage graph",   "GET", f"/api/lineage/graph?workspace_id={WORKSPACE_ID}")
check("gov: lineage nodes",   "POST", "/api/lineage/nodes", body={"name": "e2e_test_node", "type": "table"}, allow=(200, 201, 400, 422))
check("gov: standards",       "GET", f"/api/standards/?workspace_id={WORKSPACE_ID}")
check("gov: sensitive flds",  "GET", "/api/security/sensitive-fields")

# ── DataFlow (corrected paths) ──
check("flow: sync tasks",     "GET", "/api/sync/tasks")
check("flow: sync logs",      "GET", "/api/sync/logs")
check("flow: sched tasks",    "GET", f"/api/scheduled-tasks/tasks?workspace_id={WORKSPACE_ID}")
check("flow: notif channels", "GET", f"/api/notification/channels?workspace_id={WORKSPACE_ID}")
check("flow: templates",      "GET", f"/api/scheduled-tasks/templates?workspace_id={WORKSPACE_ID}")

# ── DataViz ──
check("viz: dashboards",      "GET", f"/api/dashboard/?workspace_id={WORKSPACE_ID}")
check("viz: reports",         "GET", f"/api/reports/?workspace_id={WORKSPACE_ID}")
check("viz: datasources",     "GET", "/api/dashboard/datasources")

# ── DataMind (non-chat only) ──
check("mind: health",         "GET", "/api/health")
check("mind: history",        "GET", "/api/history/")
check("mind: playground",     "GET", "/api/playground/queries")
check("mind: llm config",     "GET", "/api/model-config/llm")

# ── AIPlatform ──
check("ai: mcp servers",      "GET", "/api/admin/mcp-servers/")
check("ai: agents",           "GET", "/api/admin/agents/")
check("ai: embed apps",       "GET", "/api/embed/applications")
check("ai: mcp market",       "GET", "/api/mcp-market/")
check("ai: model lab info",   "GET", "/api/model-lab/info")
check("ai: model train stat", "GET", "/api/model-train/stats")
check("ai: workflows",        "GET", "/api/admin/workflows")
check("ai: workflow prompts", "GET", "/api/admin/prompts")
check("ai: brand",            "GET", "/api/admin/brand/")
check("ai: cache stats",      "GET", "/api/admin/cache/stats")
check("ai: admin llm cfg",    "GET", "/api/admin/model-config/llm")

# ── Summary ──
fails = [r for r in results if not r[0]]
print("\n" + "=" * 70)
print(f"TOTAL: {len(results)}, PASS: {len(results) - len(fails)}, FAIL: {len(fails)}")
for ok, name, path, status, raw, is_html in fails:
    tag = " [HTML]" if is_html else ""
    print(f"  x {name} ({path}) -> {status}{tag}: {raw[:110]}")
