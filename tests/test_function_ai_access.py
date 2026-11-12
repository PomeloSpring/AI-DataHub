"""功能项 AI 能力接入的回归门禁。

锁定四件事（对应 perm_link / function_tools / outbound_guard 的安全契约）：

1. **继承不静默**：角色缺权限码时功能动作进 unavailable 且带可读原因，绝不悄悄少注册。
2. **涉密不可抬**：代码层硬上界卡死，DB 把 ai_access 配成 write 也抬不上去。
3. **出站 fail-loud**：结果含敏感信息时拒绝下发并给出命中类别，不静默清空字段。
4. **两维度分区**：功能能力走角色权限码，数据工具（execute_sql 等）走 as_bot.tools，
   互不门控 —— 否则权限语义会与 enforcer/gates 打架。
"""
from types import SimpleNamespace

import pytest

from backend.core import perm_link
from backend.modules.mind.execution import function_tools, outbound_guard
from backend.modules.mind.execution import tool_policy as policies


# ── 夹具：一份"全权限"的角色授权 ───────────────────────────────────────────────

def _all_write_perms():
    return {
        spec["perm_code"]: {"ai_access": perm_link.AI_LEVEL_WRITE,
                            "label": spec["label"], "ai_note": ""}
        for spec in function_tools.FUNCTION_ACTION_SPECS.values()
    }


def _as_bot(tools=None):
    return {"as_bot_key": "test", "tools": tools if tools is not None else {}}


# ═══════════════════════════════════════════════════════════════════════════
# 1. 继承不静默
# ═══════════════════════════════════════════════════════════════════════════

def test_no_role_perms_registers_nothing_but_no_phantom_entry():
    """role_perms=None：不参与继承（非生产路径），不产生 unavailable 噪音条目。"""
    p = policies.compile_policy(_as_bot())
    assert not [t for t in p.allowed if t.startswith("mcp__datahub_functions__")]
    assert "__function_actions__" not in p.unavailable


def test_empty_role_perms_reports_every_action_with_reason():
    """角色无权限码：每个功能动作都要出现在 unavailable 里并给出可读原因。"""
    p = policies.compile_policy(_as_bot(), role_perms={})
    denied = {k: v for k, v in p.unavailable.items() if k.startswith("mcp__datahub_functions__")}
    assert set(denied) == {function_tools.qualified_tool_name(a)
                           for a in function_tools.FUNCTION_ACTION_SPECS}
    assert all(reason and "未授予" in reason for reason in denied.values())
    assert not p.function_names


def test_manifest_exposes_unavailable_reasons_to_user():
    """unavailable 必须能透传到 manifest（前端/会话能力面板要显示原因）。"""
    p = policies.compile_policy(_as_bot(), role_perms={})
    listed = {t["name"] for t in p.manifest()["unavailable_tools"]}
    assert function_tools.qualified_tool_name("report.list") in listed


def test_role_perms_inherit_function_tools():
    """角色有码 → 功能工具自动进入 allowed（AS-BOT 不用再勾一次）。"""
    p = policies.compile_policy(_as_bot(), role_perms=_all_write_perms())
    assert function_tools.qualified_tool_name("report.list") in p.allowed
    assert "list_reports" in p.function_names
    assert function_tools.qualified_tool_name("report.list") not in p.unavailable


def test_as_bot_subtraction_only_removes():
    """AS-BOT 仅做减法：disabled 关掉的动作不再注册，且给出原因。"""
    perms = _all_write_perms()
    p = policies.compile_policy(
        _as_bot({"functions": {"disabled": ["report.list"]}}), role_perms=perms)
    assert function_tools.qualified_tool_name("report.list") not in p.allowed
    assert "list_reports" not in p.function_names
    assert "已关闭" in p.unavailable[function_tools.qualified_tool_name("report.list")]
    # 其余动作不受影响
    assert function_tools.qualified_tool_name("dataset.list") in p.allowed


def test_invalid_functions_config_rejected():
    """减法开关写错形态要报错，不能默默忽略。"""
    with pytest.raises(ValueError):
        policies.compile_policy(_as_bot({"functions": ["report.list"]}), role_perms={})


# ═══════════════════════════════════════════════════════════════════════════
# 2. 涉密不可抬（配置不可覆盖）
# ═══════════════════════════════════════════════════════════════════════════

def test_secret_none_perm_cannot_be_raised_by_config():
    """涉密项即便配成 write，有效级别仍是 none。"""
    assert perm_link.cap_level("datasource:manage", "write") == perm_link.AI_LEVEL_NONE
    assert perm_link.cap_level("user:manage", "write") == perm_link.AI_LEVEL_NONE
    assert perm_link.cap_level("rls:read", "read") == perm_link.AI_LEVEL_NONE
    assert perm_link.cap_level("prompt:manage", "write") == perm_link.AI_LEVEL_NONE


def test_secret_readonly_perm_forbids_all_writes():
    """上限 read 的涉密项：禁止一切写。"""
    assert perm_link.cap_level("datasource:read", "write") == perm_link.AI_LEVEL_READ
    assert perm_link.cap_level("sensitive:read", "write") == perm_link.AI_LEVEL_READ


def test_effective_level_reports_capping_reason():
    """被硬上界压下来时必须给出原因（不能让人以为改了就生效）。"""
    level, reason = perm_link.effective_level("datasource:manage", "write", "输出含连接凭据")
    assert level == perm_link.AI_LEVEL_NONE
    assert "涉密边界收窄" in reason and "输出含连接凭据" in reason


def test_secret_action_blocked_even_with_write_config():
    """涉密动作即使角色有码 + 配 write，也被代码层硬上界卡死。

    用合成 spec 直接验证两条硬边界：
      * SECRET_BOUND_NONE_PERMS 上的动作 —— 连只读都不给（根本不进工具面）；
      * SECRET_BOUND_READ_ONLY_PERMS 上的动作 —— 只能只读，写被拒。
    """
    specs = {
        "ds.config": {"perm_code": "datasource:manage", "label": "配置数据源", "read_only": False},
        "ds.list": {"perm_code": "datasource:read", "label": "查看数据源", "read_only": True},
        "ds.write": {"perm_code": "datasource:read", "label": "改数据源", "read_only": False},
    }
    perms = {c: {"ai_access": perm_link.AI_LEVEL_WRITE, "label": c, "ai_note": ""}
             for c in ("datasource:manage", "datasource:read")}
    allowed, denied = perm_link.resolve_function_actions(specs, perms)

    # none 硬上界：连只读都不给
    assert "ds.config" not in allowed
    assert "涉密边界收窄" in denied["ds.config"]
    # read 硬上界：只读可用，写被拒
    assert allowed["ds.list"] == perm_link.AI_LEVEL_READ
    assert "ds.write" not in allowed
    # 原因要能解释给用户：点明它是写动作、当前只有只读、且被涉密边界收窄
    assert "写操作" in denied["ds.write"]
    assert "只读可见" in denied["ds.write"] and "收窄" in denied["ds.write"]


def test_registered_actions_stay_within_secret_bounds():
    """真实注册表里的动作配 write 后不得越出各自 perm 的硬上界。"""
    perms = {spec["perm_code"]: {"ai_access": perm_link.AI_LEVEL_WRITE,
                                  "label": spec["label"], "ai_note": ""}
             for spec in function_tools.FUNCTION_ACTION_SPECS.values()}
    allowed, _ = perm_link.resolve_function_actions(function_tools.FUNCTION_ACTION_SPECS, perms)
    for action, level in allowed.items():
        perm = function_tools.FUNCTION_ACTION_SPECS[action]["perm_code"]
        assert level == perm_link.cap_level(perm, perm_link.AI_LEVEL_WRITE), action
        # 涉密只读上限的 perm 上，绝不允许出现写动作
        if perm_link.cap_level(perm, perm_link.AI_LEVEL_WRITE) != perm_link.AI_LEVEL_WRITE:
            assert function_tools.FUNCTION_ACTION_SPECS[action]["read_only"], action


def test_write_action_rejected_when_level_is_read():
    """写动作在 read 级别下必须被拒，且原因可读。"""
    perms = {"report:manage": {"ai_access": perm_link.AI_LEVEL_READ,
                               "label": "管理报告", "ai_note": ""}}
    allowed, denied = perm_link.resolve_function_actions(
        function_tools.FUNCTION_ACTION_SPECS, perms)
    assert "report.generate" not in allowed
    assert "写操作" in denied["report.generate"]


def test_invalid_ai_level_raises_not_falls_back():
    """级别取值非法要抛错，不静默回落成 none。"""
    with pytest.raises(ValueError):
        perm_link.normalize_level("allow_all")


# ═══════════════════════════════════════════════════════════════════════════
# 3. 出站 fail-loud
# ═══════════════════════════════════════════════════════════════════════════

def _tool_result(text):
    return {"content": [{"type": "text", "text": text}]}


@pytest.mark.parametrize("text,expected", [
    ('{"host":"47.103.50.8","port":611}', "sensitive_field"),
    # 基础设施键（完整键名）仍拦：数据源 host/内网地址不得随工具结果下发
    ('{"ip":"47.103.50.8","endpoint":"http://10.0.0.7:8123"}', "sensitive_field"),
    ('{"password":"abc"}', "sensitive_field"),
    ('{"datasource_id": 5}', "sensitive_field"),
    ("jdbc:mysql://47.103.50.8:611/adh2", "connection_string"),
    ("mysql://user:pass@db/x", "credential_in_url"),
    ("SELECT * FROM adh_reports", "generated_sql"),
    ("physical_table = adh_reports", "legacy_leak"),
    ("Traceback (most recent call last):", "error_stack"),
    # 驱动原始报错天然带物理列/表名（历史缺陷：catalog 旧列名报错直接下发 LLM）
    ('(1054, "Unknown column \'metric_name\' in \'field list\'")', "raw_db_error"),
    ('''pymysql.err.ProgrammingError: (1146, "Table 'adh2.adh_orders' doesn't exist")''',
     "raw_db_error"),
    ("ORA-00904: \"FOO\": invalid identifier", "raw_db_error"),
])
def test_outbound_guard_detects_leaks(text, expected):
    assert expected in outbound_guard.find_sensitive_hits(text)


@pytest.mark.parametrize("text", [
    '{"报表标题":"季度分析","生成时间":"2026-10-03 14:30:00","浏览次数":12}',
    '{"任务名称":"日报","执行周期表达式":"0 8 * * *","上次状态":"success"}',
    "created_at: 2026-10-03T14:30:00, run_count: 5",
    "pass_rate 98.5% total_rows 1000",
    "select the best option from the list of candidates",
    # 可操作业务提示不得被判敏感（否则模型拿不到“先做元数据同步”这类下一步）
    "数据源 prd-cn 无有效表元数据，请先执行元数据同步",
    "The selected dataset doesn't exist; pick another one",
    # 业务敏感列（客户端 IP）不被出站层误杀：能不能下发由 adh_sensitive_fields 裁决
    '{"rows":[{"客户端IP":"47.103.50.8","页面浏览量":20}]}',
    '{"rows":[{"客户端IP":"20***"}]}',
])
def test_outbound_guard_no_false_positive_on_normal_output(text):
    """时间戳/散文不能被误杀，否则正常取数结果全被判失败。"""
    assert outbound_guard.find_sensitive_hits(text) == []


def test_business_ip_is_governed_by_data_permission_not_egress_regex():
    """业务敏感列的裁决权在数据权限，出站层不得 hardcode 内容形态规则（护栏 §7）。

    历史缺陷：`ipv4_literal` 裸 IPv4 正则 + 裸 `ip` 键名判定叠加，使已建模的
    「客户端IP」维度无论怎么配 mask 都整体拒发，而管理员在「敏感数据」页
    查不到任何配置依据（代码常量不是规则）。两侧同时锁定，防回归：
    """
    # 1) 值形态规则清单里不得重现业务数据内容正则（IPv4 字面量/手机号/邮箱）
    assert "ipv4_literal" not in {label for label, _ in outbound_guard._VALUE_RES}
    assert outbound_guard.find_sensitive_hits("47.103.50.8") == []
    assert outbound_guard.find_sensitive_hits("13800138000") == []
    # 2) 中文业务维度标签不得被当成内部键 `ip`（词边界是 Unicode 口径）
    assert outbound_guard.find_sensitive_hits('{"客户端IP":"47.103.50.8"}') == []
    # 3) 但基础设施键仍必须拦（放宽不得越界到配置面泄露）
    assert "sensitive_field" in outbound_guard.find_sensitive_hits('{"ip":"47.103.50.8"}')
    # 4) 业务列的 block/mask 真值源仍是 enforcer 的敏感基线（注入查询 SQL）
    import inspect

    from backend.core import enforcer
    baseline_src = inspect.getsource(enforcer.PermissionEnforcer._get_sensitive_policies)
    assert "adh_sensitive_fields" in baseline_src


def test_outbound_guard_fails_loud_with_category_not_silent_strip():
    """命中必须拒发并说明类别，不许把字段悄悄删掉再返回部分结果。"""
    leaked = _tool_result('{"name":"ds1","password":"s3cret"}')
    result = outbound_guard.assert_no_sensitive(leaked, "test_tool")
    assert result["isError"] is True
    body = result["content"][0]["text"]
    assert "sensitive_field" in body            # 类别可诊断
    assert "s3cret" not in body                 # 原文不回显
    assert "已拒绝" in body


def test_outbound_guard_passes_clean_result_untouched():
    clean = _tool_result('{"name":"ds1","db_type":"mysql"}')
    assert outbound_guard.assert_no_sensitive(clean, "test_tool") is clean


# ═══════════════════════════════════════════════════════════════════════════
# 4. 两维度分区
# ═══════════════════════════════════════════════════════════════════════════

def test_data_tools_not_gated_by_menu_perms():
    """数据工具（execute_sql 等）不受菜单权限码门控 —— 它们走 as_bot.tools.mcp。"""
    p = policies.compile_policy(_as_bot({"mcp": {"query": ["execute_sql"]}}), role_perms={})
    assert "mcp__datahub_query__execute_sql" in p.allowed
    # 同时功能能力因无权限码被拒，两者互不影响
    assert function_tools.qualified_tool_name("report.list") not in p.allowed


def test_function_tools_not_selectable_via_as_bot_tools():
    """功能工具不得通过 as_bot.tools.mcp 勾选 —— 那会绕过角色权限继承。"""
    with pytest.raises(ValueError):
        policies.compile_policy(
            _as_bot({"mcp": {"functions": ["list_reports"]}}), role_perms={})


def test_function_actions_cannot_carry_datasource_id():
    """功能动作入参不得要求 LLM 传 datasource_id（守 §7 黑盒 / §2 不猜 id）。"""
    for action, spec in function_tools.FUNCTION_ACTION_SPECS.items():
        props = (spec.get("schema") or {}).get("properties") or {}
        assert "datasource_id" not in props, f"{action} 的入参暴露了 datasource_id"
        assert "datasource_id" not in (spec.get("schema") or {}).get("required", []), action


# ═══════════════════════════════════════════════════════════════════════════
# 5. 只读视图不泄密（静态列白名单）
# ═══════════════════════════════════════════════════════════════════════════

def test_safe_views_never_select_star():
    """所有功能 handler 的 SQL 必须是显式列白名单，绝不 SELECT *。"""
    import inspect
    import re
    for action, spec in function_tools.FUNCTION_ACTION_SPECS.items():
        src = inspect.getsource(spec["handler"])
        assert not re.search(r"SELECT\s+\*", src, re.I), f"{action} 用了 SELECT *"


def test_scheduled_task_view_excludes_webhook_secrets():
    """调度任务视图不得查 webhook_token / webhook_secret / last_error / task_config。"""
    import inspect
    src = inspect.getsource(function_tools._list_scheduled_tasks)
    for forbidden in ("webhook_token", "webhook_secret", "last_error", "task_config"):
        assert forbidden not in src, f"scheduled_task.list 泄露了 {forbidden}"


def test_report_view_excludes_content_and_share_tokens():
    import inspect
    src = inspect.getsource(function_tools._list_reports)
    for forbidden in ("content", "access_token", "share_token_hash",
                      "security_context", "evidence_summary", "analysis_source"):
        assert forbidden not in src, f"report.list 泄露了 {forbidden}"


def test_quality_check_result_projects_away_physical_table(monkeypatch):
    """质量检核结果要投影掉 target_table（物理表名）与采样。"""
    from backend.modules.mind.execution import function_tools as ft

    monkeypatch.setattr(ft, "_identity",
                        lambda: {"user_id": 1, "workspace_id": 1, "role": "admin"})
    monkeypatch.setattr(ft, "_select", lambda *a, **k: [{
        "id": 7, "workspace_id": 1, "rule_name": "非空校验", "rule_type": "not_null",
        "rule_config": {}, "target_table": "secret_table", "target_column": "c", "severity": "high"}])

    import backend.modules.gov.services.quality_engine as qe
    monkeypatch.setattr(qe, "execute_single_rule", lambda *a, **k: {
        "rule_name": "非空校验", "rule_type": "not_null", "target_table": "secret_table",
        "passed": True, "total_rows": 10, "failed_rows": 0, "pass_rate": 100.0,
        "execution": {"user_id": 1}, "detail_samples": [{"c": 1}], "elapsed_ms": 5,
        "check_time": "2026-10-03 00:00:00"})

    out = ft._run_quality_check({"rule_id": 7})
    body = out["content"][0]["text"]
    assert out.get("isError") is not True
    assert "secret_table" not in body
    assert "detail_samples" not in body and "execution" not in body
    assert "pass_rate" in body


# ═══════════════════════════════════════════════════════════════════════════
# 6. resolve_policy 必传 role_perms（防功能能力静默消失）
# ═══════════════════════════════════════════════════════════════════════════

def test_resolve_policy_always_supplies_role_perms(monkeypatch):
    """生产路径必须把角色权限传进 compile_policy，否则功能能力会静默消失。"""
    captured = {}
    real_compile = policies.compile_policy

    def fake_compile(as_bot, ceiling=None, role_perms=None):
        captured["role_perms"] = role_perms
        return real_compile(as_bot, ceiling, role_perms={})

    monkeypatch.setattr(policies, "compile_policy", fake_compile)
    monkeypatch.setattr("backend.common.auth.resolve_execution_owner",
                        lambda *a, **k: {"role": "admin", "username": "u"})
    from backend.modules.mind.execution import as_bots
    monkeypatch.setattr(as_bots, "resolve_as_bots",
                        lambda *a, **k: [{"as_bot_key": "a", "tools": {}, "mcp_server_ids": [],
                                         "models": []}])
    monkeypatch.setattr(as_bots, "default_as_bot", lambda bots: bots[0] if bots else None)

    import backend.core.role_service as rs
    monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms",
                        lambda *a, **k: {"report:read": {"ai_access": "read", "label": "x", "ai_note": ""}})

    ctx = SimpleNamespace(user_id=1, workspace_id=0, user_role="", username="",
                          extra={"as_bot_key": ""})
    policies.resolve_policy(ctx, ceiling=None)
    assert captured["role_perms"] is not None, "resolve_policy 必须传 role_perms"
    assert "report:read" in captured["role_perms"]


# ═══════════════════════════════════════════════════════════════════════════
# 7. 注册表 ↔ DB 双侧一致（需真实元数据库；不可用则跳过，不假绿）
# ═══════════════════════════════════════════════════════════════════════════

def _db_conn():
    try:
        import pymysql
        import os
        env = {}
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "backend", ".env")
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


def test_registry_matches_db_ai_action_keys():
    """DB 的 ai_action_key 与 FUNCTION_ACTION_SPECS 必须一一对应，perm_code 也要对得上。

    ai_action_key 与 api_pattern/api_method 同口径：一个权限点可挂多个功能动作（逗号分隔，
    如 report:manage 同时挂 report.generate 与 report.save），展开后逐项比对。
    """
    conn = _db_conn()
    if conn is None:
        pytest.skip("元数据库不可用，跳过双侧一致性断言")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ai_action_key, perm_code FROM adh_perm_registry "
                        "WHERE ai_action_key <> ''")
            rows = cur.fetchall()
    finally:
        conn.close()

    db: dict[str, str] = {}
    for raw_key, code in rows:
        for action in str(raw_key).split(","):
            action = action.strip()
            if action:
                db[action] = code

    specs = function_tools.FUNCTION_ACTION_SPECS
    assert set(db) == set(specs), (
        f"DB 有注册表无: {sorted(set(db) - set(specs))}; "
        f"注册表有 DB 无: {sorted(set(specs) - set(db))}")
    mismatch = {k: (db[k], specs[k]["perm_code"]) for k in db if db[k] != specs[k]["perm_code"]}
    assert not mismatch, f"perm_code 不一致: {mismatch}"


def test_db_levels_respect_secret_bounds():
    """DB 里的 ai_access 不得高于代码层硬上界（否则配置就是误导）。"""
    conn = _db_conn()
    if conn is None:
        pytest.skip("元数据库不可用，跳过级别上界断言")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT perm_code, ai_access FROM adh_perm_registry")
            rows = cur.fetchall()
    finally:
        conn.close()
    over = [(code, level) for code, level in rows
            if perm_link.cap_level(code, level) != perm_link.normalize_level(level)]
    assert not over, f"以下权限点的 DB 级别高于涉密硬上界: {over}"


# ═══════════════════════════════════════════════════════════════════════════
# 8. 接入通道（级别配置到底有没有消费方）
# ═══════════════════════════════════════════════════════════════════════════
#
# 背景：角色页的「AI 可调用级别」只有两类消费方 —— FUNCTION_ACTION_SPECS 登记的功能
# 工具（`resolve_function_actions` 据此注册），以及执行期 `require_write_perm` 按码把关。
# 历史缺陷是把后者误标成「未接 AI」（管理员以为配了没用，去改本来生效的配置），
# 以及让零消费方的码挂着可选下拉框（选了以为开通，实际什么也没发生 —— 空承诺）。


def _perm_codes_gated_in_code() -> set:
    """扫出代码里所有 `require_write_perm` / `check_write_perm` 调用点的 perm_code 字面量。

    两种形参形态都要覆盖（早期只扫三参形式，漏掉 `_require_write_perm(code, label)`
    封装，导致 ontology:create/activate/import 被当成未接入）：
      * `require_write_perm(user_id, workspace_id, "code", label)` → 第 3 个位置参；
      * `check_write_perm(role_perms, "code", label)` → 第 2 个；
      * 本模块外封装 `_require_write_perm("code", label)` → 第 1 个。
    """
    import ast
    import os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    found: set = set()
    for base, dirs, files in _os.walk(_os.path.join(root, "backend")):
        if "__pycache__" in base:
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = _os.path.join(base, name)
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                fname = fn.id if isinstance(fn, ast.Name) else (
                    fn.attr if isinstance(fn, ast.Attribute) else "")
                if fname.lstrip("_") not in ("require_write_perm", "check_write_perm"):
                    continue
                idx = 0 if (isinstance(fn, ast.Name) and fn.id.startswith("_")) else (
                    2 if fname.lstrip("_") == "require_write_perm" else 1)
                args = node.args
                if len(args) > idx and isinstance(args[idx], ast.Constant) \
                        and isinstance(args[idx].value, str):
                    found.add(args[idx].value)
    return found


def test_perm_gated_action_perms_match_call_sites():
    """PERM_GATED_ACTION_PERMS 必须与代码调用点一致（漂移守卫）。

    少登记 = 真实生效的码被标成「未接入」（误导管理员）；
    多登记 = 把没有把关点的码广告成已接入（空承诺）。两个方向都算缺陷。
    """
    sites = _perm_codes_gated_in_code()
    declared = set(perm_link.PERM_GATED_ACTION_PERMS)
    assert sites == declared, (
        f"代码有把关点但清单未登记: {sorted(sites - declared)}; "
        f"清单登记了但代码无把关点: {sorted(declared - sites)}")


def test_ai_channel_precedence_and_none():
    """通道判定：已登记功能工具优先，其次执行期按码把关，两者都无 = none。"""
    assert perm_link.ai_channel("report:read", "report.list") == perm_link.AI_CHANNEL_FUNCTION_ACTION
    # 双通道码（article:manage 既挂 content_* 动作又有把关点）以工具登记为准
    assert perm_link.ai_channel("article:manage", "content_draft.create") == perm_link.AI_CHANNEL_FUNCTION_ACTION
    # 故意不登记 ai_action_key 但执行期真把关的码不得被当成未接入
    for code in ("ontology:save", "ontology:create", "ontology:activate",
                 "ontology:import", "dashboard:manage", "scheduled:manage"):
        assert perm_link.ai_channel(code, "") == perm_link.AI_CHANNEL_PERM_GATE, code
    assert perm_link.ai_channel("quality-review:read", "") == perm_link.AI_CHANNEL_NONE
    assert perm_link.ai_channel("vis:manage", "  ") == perm_link.AI_CHANNEL_NONE


def test_action_spec_perm_codes_are_declared():
    """每个已登记功能动作必须绑一个非空 perm_code。

    perm_code 为空的动作在 ``resolve_function_actions`` 里永远命中
    “角色未授予”→ 工具注册了却永远不可用，且角色页无从归类（既不是
    function_action 也不是 perm_gate）—— 静默死工具，比不登记更坑。
    """
    empty = sorted(k for k, s in function_tools.FUNCTION_ACTION_SPECS.items()
                   if not (s.get("perm_code") or "").strip())
    assert not empty, f"以下功能动作未声明 perm_code（注册了也永远不可用）: {empty}"


# 当前“配了级别但无消费方”的已声明缺口：新增码必须走登记（补投影或补把关点），
# 不得默默多一个选了不生效的开关（no-silent-degradation §1）。
# 收敛方向：要么在 FUNCTION_ACTION_SPECS 补安全投影，要么把 ai_access 改回 none。
KNOWN_NO_CHANNEL_PERMS = frozenset({
    "asbot:use", "asset:read", "audit:read", "catalog:manage", "catalog:read", "chat:use",
    "dataset:query", "glossary:read", "graph:manage", "graph:read", "kb:manage", "kbdoc:read",
    "knowledge:manage", "knowledge:read", "lineage:read", "metrics:manage", "metrics:read",
    "model:read", "monitor:read", "observability:read", "ontology:read", "playground:execute",
    "playground:read", "quality-review:manage", "quality-review:read", "quality-rules:manage",
    "quality-rules:read", "report-template:manage", "report-template:read", "scheduled:execute",
    "sensitive:read", "standard:manage", "standard:read", "sync-logs:read", "sync:execute",
    "sync:manage", "sync:read", "tags:manage", "task-monitor:manage", "vis:manage", "vis:read",
    "workspace:manage", "workspace:read",
})


def test_no_channel_ai_levels_are_declared_gap():
    """无消费方却配了级别的码必须是已声明缺口（防新增空承诺）。"""
    conn = _db_conn()
    if conn is None:
        pytest.skip("元数据库不可用，跳过缺口清单断言")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT perm_code, ai_access, ai_action_key FROM adh_perm_registry "
                        "WHERE is_active=1")
            rows = cur.fetchall()
    finally:
        conn.close()

    gap = {str(r[0]) for r in rows
           if perm_link.ai_channel(str(r[0]), str(r[2] or "")) == perm_link.AI_CHANNEL_NONE
           and perm_link.normalize_level(r[1]) != perm_link.AI_LEVEL_NONE}
    fresh = gap - KNOWN_NO_CHANNEL_PERMS
    assert not fresh, (
        f"新出现的「配了 AI 级别但无消费方」权限码: {sorted(fresh)} —— "
        f"要么在 function_tools.FUNCTION_ACTION_SPECS 登记安全投影并回填 ai_action_key，"
        f"要么把 ai_access 改回 none；不得留一个选了不生效的开关。")


# ═══════════════════════════════════════════════════════════════════════════
# 9. 惰性接入（功能清单按需下发，不常驻模型上下文）
# ═══════════════════════════════════════════════════════════════════════════
#
# 三条契约：
#   ① 只注册 list/run 两个壳，动作 schema 不再逐个进上下文（17 份 = 每轮白付 token）；
#   ② 清单与派发复用既有裁决（policy.function_names / 逐动作 check_tool），壳不是新裁决点；
#   ③ 本会话没有任何功能能力时连壳也不注册（不留“调了只会报错”的空入口）。


def _payload(result):
    import json
    return json.loads(result["content"][0]["text"])


def test_shell_tools_registered_only_when_actions_exist():
    """有功能能力才注册壳；动作精确名仍留在 allowed 里。"""
    shells = {function_tools.qualified_shell_name(n)
              for n in function_tools.shell_tool_names()}
    assert len(shells) == 2
    p = policies.compile_policy(_as_bot(), role_perms=_all_write_perms())
    assert shells <= set(p.allowed)
    # 逐动作授权不因改壳而削弱：run 内部 check_tool 裁的就是这个精确名
    assert function_tools.qualified_tool_name("report.list") in p.allowed
    empty = policies.compile_policy(_as_bot(), role_perms={})
    assert not (shells & set(empty.allowed)), "无功能能力时不得注册壳（空入口）"


def test_build_function_server_registers_shells_not_actions(monkeypatch):
    """server 里只有两个壳工具（且注册名是壳的精确名）；tool_names 空 → 不建 server。"""
    from backend.modules.mind.execution.sdk_tools import compat
    registered = []
    monkeypatch.setattr(compat, "make_server",
                        lambda backend, name, tools: {"name": name, "tools": tools})

    def fake_make_tool(backend, n, d, s, h, annotations=None, qualified_name=""):
        registered.append({"name": n, "qualified_name": qualified_name})
        return {"name": n}

    monkeypatch.setattr(compat, "make_tool", fake_make_tool)
    cfg = function_tools.build_function_server("qoder", tool_names=["list_reports"])
    assert cfg
    assert sorted(r["name"] for r in registered) == sorted(function_tools.shell_tool_names())
    # 注册名必须是壳的精确名（check_tool 按名裁决，写错就是全部拒）
    for r in registered:
        assert r["qualified_name"] == function_tools.qualified_shell_name(r["name"])
    assert function_tools.build_function_server("qoder", tool_names=[]) is None
    with pytest.raises(ValueError):
        function_tools.build_function_server("qoder", tool_names=["不存在的工具"])


def test_list_platform_functions_filters_by_role(monkeypatch):
    """清单只列本角色可用的功能，并带上不可用原因（与能力面板同一份数据）。"""
    policy = SimpleNamespace(
        function_names=("list_reports",),
        unavailable={function_tools.qualified_tool_name("dataset.list"):
                     "角色未授予「dataset:read」权限，功能「查看数据集」不可用"})
    monkeypatch.setattr(function_tools, "_live_function_policy", lambda: (None, policy))
    data = _payload(function_tools._list_platform_functions({}))
    assert [f["function"] for f in data["functions"]] == ["list_reports"]
    item = data["functions"][0]
    assert item["read_only"] is True and item["label"] and item["description"]
    assert "limit" in item["params"]
    assert data["unavailable"][0]["function"] == "list_datasets"
    assert "未授予" in data["unavailable"][0]["reason"]
    # 不凭记忆猜：返回体必须自带调用方式说明
    assert function_tools.INVOKE_TOOL in data["how_to_call"]


def test_list_platform_functions_without_trusted_session_fails_loud(monkeypatch):
    """拿不到可信会话时报错，不静默返回空清单（会被当成“没功能”）。"""
    monkeypatch.setattr(function_tools, "_live_function_policy", lambda: (None, None))
    res = function_tools._list_platform_functions({})
    assert res.get("isError") and "身份" in _payload(res)["error"]


def test_run_platform_function_dispatches_with_per_action_authorization(monkeypatch):
    """派发按**动作精确名**裁决并透传参数 —— 壳不得成为放宽口径的新裁决点。"""
    from backend.modules.mind.execution import resource_guard, tool_policy
    from backend.modules.mind.execution.sdk_tools import context as ctx_mod
    seen = {}
    monkeypatch.setattr(tool_policy, "check_tool",
                        lambda ctx, name: seen.setdefault("names", []).append(name))
    monkeypatch.setattr(resource_guard, "validate_tool_resources",
                        lambda ctx, name, args: dict(args, _injected=1))
    monkeypatch.setattr(ctx_mod, "get_execution_context",
                        lambda: SimpleNamespace(user_id=7, workspace_id=3))
    called = {}

    def fake_handler(args):
        called.update(args)
        return function_tools._text({"ok": True})

    monkeypatch.setitem(function_tools.FUNCTION_ACTION_SPECS["report.list"],
                        "handler", fake_handler)
    res = function_tools._run_platform_function(
        {"function": "list_reports", "arguments": {"limit": 5}})
    assert seen["names"] == [function_tools.qualified_tool_name("report.list")]
    assert called == {"limit": 5, "_injected": 1}
    assert _payload(res) == {"ok": True}


def test_run_platform_function_denies_unauthorized_with_reason(monkeypatch):
    """权限/资源拒绝：透传真因并补上业务功能名，不得换成“联系管理员”兼底文案。"""
    from backend.modules.mind.execution import tool_policy
    from backend.modules.mind.execution.sdk_tools import context as ctx_mod

    def deny(ctx, name):
        raise PermissionError("当前 AS-BOT 未授权该工具")

    monkeypatch.setattr(tool_policy, "check_tool", deny)
    monkeypatch.setattr(ctx_mod, "get_execution_context",
                        lambda: SimpleNamespace(user_id=7, workspace_id=3))
    res = function_tools._run_platform_function({"function": "list_datasets", "arguments": {}})
    assert res.get("isError")
    text = _payload(res)["error"]
    assert "查看数据集" in text and "未授权" in text


def test_run_platform_function_rejects_unknown_name_without_dispatch(monkeypatch):
    """未知功能名当场拒并指回清单，不进入任何裁决与 handler（防猜功能名）。"""
    from backend.modules.mind.execution import tool_policy

    def boom(ctx, name):
        raise AssertionError("未知功能不得走到裁决环节")

    monkeypatch.setattr(tool_policy, "check_tool", boom)
    for bad in ({"function": "list_secrets"}, {"arguments": {"limit": 1}},
                {"function": "list_reports", "arguments": "not-a-dict"}):
        res = function_tools._run_platform_function(bad)
        assert res.get("isError"), bad
        assert function_tools.DISCOVER_TOOL in _payload(res)["error"], bad


def test_prompt_rules_tell_lazy_flow():
    """提示词只教“先取清单再调用”，不再写死具体功能名清单。"""
    from backend.modules.mind.execution import prompt_composer as pc
    rules = pc.FUNCTION_ACTION_RULES
    assert function_tools.DISCOVER_TOOL in rules
    assert function_tools.INVOKE_TOOL in rules
    for spec in function_tools.FUNCTION_ACTION_SPECS.values():
        assert spec["tool_name"] not in rules, spec["tool_name"]


def test_prompt_lists_shells_not_action_names():
    """「实际工具权限」只列可调的壳，不列动作名（列了就是给模型一批调不了的入口）。

    不可用动作同样不逐条枚举（低权角色会刷出十几行），原因随清单返回。
    """
    from backend.modules.mind.execution.prompt_composer import compose_system_prompt
    p = policies.compile_policy(_as_bot(), role_perms={
        "report:read": {"ai_access": "read", "label": "查看报告", "ai_note": ""}})
    caps = p.manifest()
    prompt = compose_system_prompt(p.as_bot, [], username="u", user_role="viewer", capabilities=caps)
    assert function_tools.qualified_shell_name(function_tools.DISCOVER_TOOL) in prompt
    assert function_tools.qualified_tool_name("report.list") not in prompt
    # 被拒的动作也不得在提示词里逐条报“未启用”（它本来就不是可直接调的工具）
    assert function_tools.qualified_tool_name("dataset.list") not in prompt
    denied = {t["name"] for t in caps["unavailable_tools"]}
    assert function_tools.qualified_tool_name("dataset.list") in denied, "面板仍要逐条看到不可用原因"
