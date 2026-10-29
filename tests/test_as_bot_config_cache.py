"""轮次起点配置事实缓存（L2）契约 — 只缓存配置、绝不缓存裁决。

背景：一次 AS-BOT 轮次起点要串读用户角色 id、AS-BOT 行、角色权限码、知识库清单、
提示词装饰用的 RLS/角色属性；远端 MySQL 下实测 claim_session 1.1~1.6s、
prompt_build 0.12~0.34s。这些是管理员低频改动的**配置事实**，同角色多用户逐轮重读纯属浪费。

本文件锁定的边界（`backend/common/as_bot_config_cache.py` 模块注释的可执行版本）：
1. 默认不缓存 —— `use_cache` 不传时行为与改造前逐行一致；
2. 配置事实分**两档** TTL：轮次起点（preflight/claim/提示词组装）走默认档（60s），
   工具级实时裁决 `check_tool` 走 **live 档**（5s + `live:` key 前缀）——
   前提是本文件 §5 的纪律：**安全项写入口必须同批 invalidate**（漏挂的最坏影响
   被限定为 live TTL 窗口，且按 §2 只表现为「多一次显式重发」而非越权）；
3. 工厂抛错（fail-loud 校验）绝不落缓存，否则瞬时抖动被固化成一整段 TTL 的错误答案；
4. 空集也缓存（零权限用户不得退化成每轮回源，fail-closed 由消费方判定）；
5. 缓存值必须 JSON 可序列化（线上 Redis 用 `default=str` 静默转脏数据）；
6. `AS_BOT_CFG_CACHE=0` 一键停用即每轮回源；`AS_BOT_CFG_LIVE_TTL=0` 使工具裁决不吃缓存；
7. 配置写入口必须同批接 `invalidate()`（AST 漂移守卫）。
"""

import ast
import copy
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.common import as_bot_config_cache as config_cache
from backend.core import role_service as role_service_module
from backend.core.role_service import role_service
from backend.modules.mind.execution import as_bots, prompt_composer
from backend.modules.mind.execution import scheduled_analysis, secure_sdk
from backend.modules.mind.execution import session_workspace as sessions
from backend.modules.mind.execution import tool_policy as policies
from backend.modules.mind.execution.sdk_tools.context import ExecutionContext

BACKEND = Path(__file__).resolve().parent.parent / "backend"


# ── 测试替身：与 RedisCache 同语义的内存后端（结果不依赖本机 Redis 是否可用）──

class _MemoryCache:
    def __init__(self):
        self.data: dict = {}
        self.ttls: dict = {}
        self.writes = 0
        self.clears = 0

    def get(self, key):
        value = self.data.get(key)
        return copy.deepcopy(value) if value is not None else None

    def set(self, key, value, ttl=None):
        # 线上 RedisCache 用 json.dumps(default=str)：datetime 等会被静默转成字符串
        # 存成脏数据，这里严格序列化，让“缓存值必须纯数据”在测试里就炸出来。
        json.dumps(value)
        self.writes += 1
        self.ttls[key] = ttl
        self.data[key] = copy.deepcopy(value)

    def invalidate(self, key=None):
        self.clears += 1
        self.data.clear()
        self.ttls.clear()

    def stats(self):
        return {"hits": 0, "misses": 0, "size": len(self.data)}


@pytest.fixture
def cache(monkeypatch):
    fake = _MemoryCache()
    monkeypatch.setattr(config_cache, "_cache", fake)
    monkeypatch.setattr(config_cache, "_ENABLED", True)
    return fake


# ── 1. 缓存原语 ────────────────────────────────────────────────────────

def test_get_or_set_reads_source_only_once(cache):
    calls = []

    def factory():
        calls.append(1)
        return [{"id": 7}]

    assert config_cache.get_or_set("bots:x", factory) == [{"id": 7}]
    assert config_cache.get_or_set("bots:x", factory) == [{"id": 7}]
    assert len(calls) == 1 and cache.writes == 1


def test_factory_exception_is_never_cached(cache):
    """fail-loud 校验异常不得被固化成整段 TTL 的错误答案。"""
    calls = []

    def factory():
        calls.append(1)
        raise ValueError("绑定的知识库校验失败")

    for _ in range(3):
        with pytest.raises(ValueError, match="知识库校验失败"):
            config_cache.get_or_set("kb:1,2", factory)
    assert len(calls) == 3
    assert cache.data == {} and cache.writes == 0


def test_empty_result_is_cached_for_fail_closed_consumers(cache):
    """零角色/零权限用户：空集是合法事实，不得每轮回源（判定仍由消费方 fail-closed）。"""
    calls = []

    def factory():
        calls.append(1)
        return []

    assert config_cache.get_or_set("roles:99", factory) == []
    assert config_cache.get_or_set("roles:99", factory) == []
    assert len(calls) == 1


def test_kill_switch_disables_cache_without_code_change(cache, monkeypatch):
    calls = []

    def factory():
        calls.append(1)
        return [{"id": 1}]

    monkeypatch.setattr(config_cache, "_ENABLED", False)
    for _ in range(2):
        config_cache.get_or_set("bots:y", factory)
    assert len(calls) == 2 and cache.writes == 0 and cache.data == {}


def test_invalidate_clears_namespace_with_reason(cache, caplog):
    config_cache.put("perms:5", {"ontology:read": {"ai_access": "read"}})
    assert cache.data
    with caplog.at_level("INFO"):
        config_cache.invalidate("set_role_permissions role=5")
    assert cache.data == {} and cache.clears == 1
    assert "set_role_permissions role=5" in caplog.text, "失效原因必须可观测（谁清的）"


# ── 2. 消费方语义：默认零变化，显式才走缓存 ────────────────────────────

def _as_bot_row(id_=1, key="analyst", **overrides):
    row = {
        "id": id_, "as_bot_key": key, "name": key, "display_name": key,
        "description": "", "category": "custom", "system_prompt": "",
        "persona": {}, "tools": {}, "mcp_server_ids": [], "knowledge_base_ids": [],
        "skills": [], "models": [], "chart_enabled": 1, "permission_mode": "inherit",
        "workspace_id": 0, "is_default": 0,
    }
    row.update(overrides)
    return row


def _spy_query(as_bot_rows, *, raise_on_bots=None):
    """按 SQL 路由的 as_bots._query 替身，并统计 DB 往返次数。"""
    stats = {"roles": 0, "bots": 0}

    def _q(sql, params=()):
        s = " ".join(sql.split())
        if "FROM adh_roles WHERE name" in s:
            stats["roles"] += 1
            return [{"id": 2}]
        if "FROM adh_as_bots WHERE role_id IN" in s:
            stats["bots"] += 1
            if raise_on_bots:
                raise raise_on_bots
            return [dict(r) for r in as_bot_rows]
        if "FROM adh_knowledge_bases" in s:
            stats["bots"] += 1
            ids = list(params or ())
            return [{"id": i, "name": f"kb{i}", "kb_type": "qmind", "status": "active"}
                    for i in ids if i != 404]
        return []
    return _q, stats


def test_resolve_as_bots_default_reads_db_every_call(cache, monkeypatch):
    """不传 use_cache = 与改造前逐行一致（每轮回源），避免默认路径被缓存污染。"""
    q, stats = _spy_query([_as_bot_row()])
    monkeypatch.setattr(as_bots, "_query", q)
    for _ in range(2):
        result = as_bots.resolve_as_bots(5, "analyst")
        assert result[0]["as_bot_key"] == "analyst"
    assert stats["bots"] == 2


def test_resolve_as_bots_use_cache_reuses_config_facts(cache, monkeypatch):
    q, stats = _spy_query([_as_bot_row()])
    monkeypatch.setattr(as_bots, "_query", q)
    first = as_bots.resolve_as_bots(5, "analyst", use_cache=True)
    second = as_bots.resolve_as_bots(5, "analyst", use_cache=True)
    assert first == second
    assert stats["roles"] == 1 and stats["bots"] == 1, "轮次起点应只读一次配置"


def test_resolve_as_bots_permission_error_not_cached(cache, monkeypatch):
    """未授权使用所选 AS-BOT：每轮真实拒绝，不因缓存把一次拒绝放大成 TTL 窗口。

    （角色 id 清单是成功读到的配置事实，照旧缓存；只有抛错的 AS-BOT 解析不复用。）
    """
    q, stats = _spy_query([_as_bot_row(key="analyst")])
    monkeypatch.setattr(as_bots, "_query", q)
    for _ in range(2):
        with pytest.raises(PermissionError, match="未授权使用所选 AS-BOT"):
            as_bots.resolve_as_bots(5, "analyst", as_bot_key="other", use_cache=True)
    assert stats["bots"] == 2
    assert not any(k.startswith(config_cache.KEY_AS_BOTS) for k in cache.data)


def test_load_knowledge_bases_validation_error_not_cached(cache, monkeypatch):
    """绑定漂移（缺失知识库）fail-loud 每轮生效，异常不进缓存。"""
    q, stats = _spy_query([])
    monkeypatch.setattr(as_bots, "_query", q)
    for _ in range(2):
        with pytest.raises(ValueError, match="知识库校验失败"):
            as_bots.load_knowledge_bases([7, 404], use_cache=True)
    assert stats["bots"] == 2 and cache.data == {}


def test_load_knowledge_bases_success_cached(cache, monkeypatch):
    """同一清单（AS-BOT 存储的固定数组）只读一次；返回顺序仍与传入顺序一致。"""
    q, stats = _spy_query([])
    monkeypatch.setattr(as_bots, "_query", q)
    assert [r["id"] for r in as_bots.load_knowledge_bases([9, 7], use_cache=True)] == [9, 7]
    assert [r["id"] for r in as_bots.load_knowledge_bases([9, 7], use_cache=True)] == [9, 7]
    assert stats["bots"] == 1
    # 不同书写顺序 = 不同 key：宁可多读一次，也不得静默改变对外契约的展示顺序
    assert [r["id"] for r in as_bots.load_knowledge_bases([7, 9], use_cache=True)] == [7, 9]
    assert stats["bots"] == 2


def test_user_role_ai_perms_cache_semantics(cache, monkeypatch):
    calls = []

    def fake_query(user_id):
        calls.append(user_id)
        return {"ontology:read": {"ai_access": "read", "label": "查看本体", "ai_note": ""}}

    monkeypatch.setattr(role_service, "_query_user_role_ai_perms", staticmethod(fake_query))
    live = role_service.get_user_role_ai_perms(11)
    cached = role_service.get_user_role_ai_perms(11, use_cache=True)
    role_service.get_user_role_ai_perms(11, use_cache=True)
    assert live == cached == {"ontology:read": {"ai_access": "read",
                                                 "label": "查看本体", "ai_note": ""}}
    assert calls == [11, 11], "默认实时读，只有 use_cache=True 复用"


# ── 3. 提示词装饰查询（轮次起点）────────────────────────────────────────

def test_rls_scope_lines_cached_and_error_not_cached(cache, monkeypatch):
    calls = []

    def _q(sql, params=None, fetchone=False):
        calls.append(params)
        if len(calls) == 1:
            return [{"table_name": "t_order", "filter_expr": "org_id = 1"},
                    {"table_name": "t_order", "filter_expr": "org_id = 1"}]
        return [{"table_name": "t_user", "filter_expr": "tenant = 2"}]

    monkeypatch.setattr("backend.common.db.execute_query", _q)
    lines = prompt_composer._rls_scope_lines(5, 9)
    assert lines == ["- RLS 行范围(t_order): org_id = 1"], "重复行须去重"
    assert prompt_composer._rls_scope_lines(5, 9) == lines
    assert len(calls) == 1, "同 (数据源, 工作空间) 只读一次"
    # 换维度不复用
    prompt_composer._rls_scope_lines(6, 9)
    assert len(calls) == 2
    # 无数据源 → 直接空，不查库
    assert prompt_composer._rls_scope_lines(5, 0) == [] and len(calls) == 2


def test_rls_scope_lines_failure_returns_empty_but_not_cached(cache, monkeypatch):
    """取不到 RLS 摘要：当轮按既有语义不阻断，但不把失败固化成 TTL 内的空摘要。"""
    def _boom(sql, params=None, fetchone=False):
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.common.db.execute_query", _boom)
    assert prompt_composer._rls_scope_lines(5, 9) == []
    assert prompt_composer._rls_scope_lines(5, 9) == []
    assert cache.data == {}, "异常结果不得入缓存"


def test_role_scope_lines_cached(cache, monkeypatch):
    calls = []

    def _q(sql, params=None, fetchone=False):
        calls.append(params)
        return [{"attr_key": "region", "attr_value": "华东"}]

    monkeypatch.setattr("backend.common.db.execute_query", _q)
    assert prompt_composer._role_scope_lines("analyst") == ["- 数据范围(region): 华东"]
    assert prompt_composer._role_scope_lines("analyst") == ["- 数据范围(region): 华东"]
    assert len(calls) == 1


# ── 4. 接线纪律：轮次起点走长档，工具裁决走 live 短档 ────────────────────

def _func_source(module, name: str) -> str:
    text = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"未找到函数 {name}（被重命名/删除即契约漂移）")


def test_check_tool_uses_live_tier_but_keeps_verification_realtime(cache, monkeypatch):
    """工具裁决：配置事实走 live 短 TTL 档；会话存活/执行层上限/领取版本不得复用。

    P0-1 前提（用户拍板）：允许 check_tool 吃缓存的条件是安全项写入口同批失效；
    而 `runtime.verify()`（会话被回收/重复领取）与 `live_ceiling()`（执行层停用）
    没有那样的缓存档，必须每个调用点实读 —— 本用例把这条分层锁住。
    """
    captured = {}

    def fake_resolve(ctx, ceiling=None, inputs=None, *, use_cache=False, live=False):
        captured.update(use_cache=use_cache, live=live, inputs=inputs, ceiling=ceiling)
        raise PermissionError("stop-here")

    runtime = SimpleNamespace(policy=SimpleNamespace(digest="d"), layer_id=0, verify=Mock())
    ctx = ExecutionContext(extra={"secure_runtime": runtime})
    monkeypatch.setattr(policies, "resolve_policy", fake_resolve)
    ceiling = Mock(return_value=None)
    monkeypatch.setattr(policies, "live_ceiling", ceiling)
    with pytest.raises(PermissionError, match="stop-here"):
        policies.check_tool(ctx, "mcp__datahub_workspace__read")
    assert captured["use_cache"] is True and captured["live"] is True, \
        "工具裁决走 live 档（不是轮次起点 60s 档）"
    assert captured["inputs"] is None, \
        "不得复用轮次起点的 DB 事实（inputs）——那样撤权在本轮永远看不到"
    runtime.verify.assert_called_once()
    ceiling.assert_called_once()


def test_check_tool_source_pins_live_tier():
    src = _func_source(policies, "check_tool")
    assert "use_cache=True" in src and "live=True" in src
    assert "inputs=" not in src, "check_tool 不得传 inputs"
    assert "runtime.verify()" in src, "会话存活校验仍是每调用点实读"


def test_live_tier_key_isolated_from_round_start(cache, monkeypatch):
    """同一事实两档分 key + live 档 TTL 必须短：否则谁先写谁定 TTL，
    撤权检测窗口会被 60s 轮次档悄悄放大。"""
    calls = []

    def fake_query(user_id):
        calls.append(user_id)
        return {"report:read": {"ai_access": "read", "label": "查看报表", "ai_note": ""}}

    monkeypatch.setattr(role_service, "_query_user_role_ai_perms", staticmethod(fake_query))
    round_start = role_service.get_user_role_ai_perms(11, use_cache=True)
    assert role_service.get_user_role_ai_perms(11, use_cache=True) == round_start
    assert len(calls) == 1
    live = role_service.get_user_role_ai_perms(11, use_cache=True, live=True)
    assert live == round_start and len(calls) == 2, "live 档不复用轮次档的值"
    keys = set(cache.data)
    assert {k for k in keys if not k.startswith(config_cache.KEY_LIVE)} == \
        {f"{config_cache.KEY_ROLE_PERMS}11"}
    assert {k for k in keys if k.startswith(config_cache.KEY_LIVE)} == \
        {f"{config_cache.KEY_LIVE}{config_cache.KEY_ROLE_PERMS}11"}
    assert cache.ttls[f"{config_cache.KEY_ROLE_PERMS}11"] == config_cache._TTL
    assert cache.ttls[f"{config_cache.KEY_LIVE}{config_cache.KEY_ROLE_PERMS}11"] == config_cache._LIVE_TTL
    assert 0 < config_cache._LIVE_TTL <= config_cache._TTL, \
        "live 档必须短于轮次档，否则分档无意义"


def test_live_tier_zero_disables_cache_for_tool_adjudication(monkeypatch):
    """运维逃生阀：AS_BOT_CFG_LIVE_TTL=0 → 工具裁决回到每个调用点实读 DB。"""
    monkeypatch.setattr(config_cache, "_LIVE_TTL", 0)
    monkeypatch.setattr(config_cache, "_cache", _MemoryCache())
    monkeypatch.setattr(config_cache, "_ENABLED", True)
    calls = []
    for _ in range(3):
        config_cache.get_or_set("perms:12", lambda: (calls.append(1), {"x": 1})[1], live=True)
    assert len(calls) == 3, "live TTL=0 时必须回源"
    # 轮次档不受影响（只是工具裁决关缓存，不是全局停缓存）
    config_cache.get_or_set("perms:12", lambda: {"x": 1})
    config_cache.get_or_set("perms:12", lambda: calls.append(2) or {"x": 1})
    assert len(calls) == 3


def test_perm_revocation_plus_invalidate_forces_explicit_resend(cache, monkeypatch):
    """用户拍板的前提必须是真：安全项变更 → invalidate → 下一次裁决重查并显式报错。

    同时锁定另一面：没接失效钩子时，陈旧窗口的上限是 live TTL（旧值仍可用），
    而 digest 比对只会造成“要求重发”，**不可能多授权**。
    """
    holder = {"perms": {"report:read": {"ai_access": "read", "label": "查看报表", "ai_note": ""}}}
    monkeypatch.setattr(role_service, "_query_user_role_ai_perms",
                        staticmethod(lambda user_id: dict(holder["perms"])))
    monkeypatch.setattr(role_service, "get_user_roles",
                        staticmethod(lambda user_id, workspace_id=0: [{"id": 2}]))
    q, _stats = _spy_query([_as_bot_row(key="analyst", tools={"standard": ["read"]})])
    monkeypatch.setattr(as_bots, "_query", q)
    monkeypatch.setattr("backend.common.auth.resolve_execution_owner",
                        lambda user_id, workspace_id: {"id": 11, "username": "u11", "role": "analyst"})

    ctx = ExecutionContext(user_id=11, workspace_id=5, user_role="analyst")
    ctx.extra["as_bot_key"] = "analyst"
    live_policy = policies.resolve_policy(ctx, None, use_cache=True, live=True)
    runtime = SimpleNamespace(policy=live_policy, digest_policy=None,
                             verify=Mock(), layer_id=0, backend="qodercli", ceiling=None)
    ctx.extra["secure_runtime"] = runtime
    monkeypatch.setattr(policies, "live_ceiling", lambda *a: None)
    # 基线：未撤权时裁决通过
    policies.check_tool(ctx, "mcp__datahub_workspace__read")

    # 管理员收权（写库）但**未**接失效：live 档仍供旧值 → 本工具调用仍通过（窗口上限 = _LIVE_TTL）
    holder["perms"] = {}
    policies.check_tool(ctx, "mcp__datahub_workspace__read")

    # 接上失效钩子（安全项写入口的同批义务）：下一次裁决就重查到新值，
    # digest 不一致 → 显式报错要求重发，不静默按旧权限继续跑。
    config_cache.invalidate("set_role_permissions role=2")
    with pytest.raises(PermissionError, match="权限已变化"):
        policies.check_tool(ctx, "mcp__datahub_workspace__read")


def test_resolve_policy_only_forwards_cache_flag():
    """`resolve_policy` 只把 use_cache 透传给配置读取（AS-BOT 行/角色权限码），
    不得放大到数据源裁决（bind_resources 本轮不缓存，工具侧恒实时）。"""
    src = _func_source(policies, "resolve_policy")
    assert "resolve_as_bots" in src and "get_user_role_ai_perms" in src
    assert "bind_resources" not in src, "数据源作用域不属本缓存职责"


def test_round_start_sites_opt_into_cache():
    """轮次起点两处必须显式 use_cache=True（漏挂 = 白做 L2）。"""
    for func in ("preflight_request", "claim_session"):
        assert "use_cache=True" in _func_source(sessions, func), func


def test_prompt_assembly_sites_pass_cache():
    """提示词组装（知识库清单）属轮次起点，必须吃到缓存。"""
    assert "use_cache=True" in _func_source(secure_sdk, "build_options")
    assert "use_cache=True" in _func_source(scheduled_analysis, "_build_oneshot_options")


# ── 5. 写入口失效守卫（AST 漂移检测）──────────────────────────────────

_CONFIG_TABLES = ("adh_as_bots", "adh_role_perms", "adh_perm_registry",
                  "adh_knowledge_bases", "adh_rls_policies", "adh_user_roles",
                  "adh_role_attributes", "adh_roles")
_WRITE_RE = re.compile(
    r"\b(INSERT INTO|UPDATE|DELETE FROM)\s+(" + "|".join(_CONFIG_TABLES) + r")\b", re.I)
# 经这些 helper 间接失效（helper 自己必须接 invalidate，由下一条测试保证）
_DELEGATES = ("write_user_role_binding", "_apply_user_role")
# 显式登记豁免（写的是配置表，但不落任何被缓存字段 / 不在服务进程内）
_INVALIDATE_EXCEPTIONS = {
    # 只回写 source_config 里的 notebook_id（重建 notebook），缓存字段 id/name/kb_type/status 不变
    "backend/modules/catalog/services/ontology_kb_sync.py::ensure_notebook":
        "只改 source_config，缓存清单字段不变",
    # 只更新 last_sync_at
    "backend/modules/mind/api/knowledge_bases.py::sync_knowledge_base":
        "只改 last_sync_at，缓存清单字段不变",
}


def _iter_functions(path: Path):
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, (ast.get_source_segment(text, node) or "")


def test_every_config_write_site_invalidates_the_cache():
    offenders = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = str(path.relative_to(BACKEND.parent))
        if "__pycache__" in rel or rel.startswith("backend/scripts/"):
            # scripts 为离线迁移/播种，不在服务进程内；改动后由重启或短 TTL 生效
            continue
        for name, src in _iter_functions(path):
            if not _WRITE_RE.search(src):
                continue
            if "invalidate" in src or any(d in src for d in _DELEGATES):
                continue
            key = f"{rel}::{name}"
            if key in _INVALIDATE_EXCEPTIONS:
                continue
            offenders.append(key)
    assert not offenders, (
        "以下函数写了轮次起点缓存的配置表却未接 invalidate()，"
        f"必须同批接线或登记豁免：{offenders}")


def test_delegating_helpers_actually_invalidate():
    """上一测试放行的“间接失效”必须真生效：
    `write_user_role_binding` 直接调 invalidate，`_apply_user_role` 只经它写绑定。"""
    assert "invalidate" in _func_source(role_service_module, "write_user_role_binding")
    body = _func_source(role_service_module, "_apply_user_role")
    assert "write_user_role_binding" in body and "adh_user_roles" not in body, (
        "绕过唯一写入口自己改绑定行 = 失效钩子失效")
