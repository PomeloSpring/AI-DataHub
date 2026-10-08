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
                               "label": "管理报表", "ai_note": ""}}
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
    ('{"password":"abc"}', "sensitive_field"),
    ('{"datasource_id": 5}', "sensitive_field"),
    ("jdbc:mysql://47.103.50.8:611/adh2", "connection_string"),
    ("mysql://user:pass@db/x", "credential_in_url"),
    ("SELECT * FROM adh_reports", "generated_sql"),
    ("physical_table = adh_reports", "legacy_leak"),
    ("Traceback (most recent call last):", "error_stack"),
])
def test_outbound_guard_detects_leaks(text, expected):
    assert expected in outbound_guard.find_sensitive_hits(text)


@pytest.mark.parametrize("text", [
    '{"报表标题":"季度分析","生成时间":"2026-10-03 14:30:00","浏览次数":12}',
    '{"任务名称":"日报","执行周期表达式":"0 8 * * *","上次状态":"success"}',
    "created_at: 2026-10-03T14:30:00, run_count: 5",
    "pass_rate 98.5% total_rows 1000",
    "select the best option from the list of candidates",
])
def test_outbound_guard_no_false_positive_on_normal_output(text):
    """时间戳/散文不能被误杀，否则正常取数结果全被判失败。"""
    assert outbound_guard.find_sensitive_hits(text) == []


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
                            "services", ".env")
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
    """DB 的 ai_action_key 与 FUNCTION_ACTION_SPECS 必须一一对应，perm_code 也要对得上。"""
    conn = _db_conn()
    if conn is None:
        pytest.skip("元数据库不可用，跳过双侧一致性断言")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ai_action_key, perm_code FROM adh_perm_registry "
                        "WHERE ai_action_key <> ''")
            db = {r[0]: r[1] for r in cur.fetchall()}
    finally:
        conn.close()

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
