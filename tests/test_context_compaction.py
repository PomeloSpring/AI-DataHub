"""会话上下文用量与手动压缩的契约：数字只有一套口径、未测量≠0%、压缩进行中不能双跑。

背景（为什么要这一套）：
- 聊天框要给用户一条上下文进度条。唯一权威口径是执行层 SDK 的 `get_context_usage()`
  （等价 CLI `/context`，返回百分比而非 token）。平台若自己估算 token，就会出现
  "显示条满了却没压缩 / 没满就压缩"这种无从解释的分叉；
- 执行层客户端是**每轮新建**（resume=sdk_session_id），轮与轮之间没有活客户端可问，
  所以占用只能在"本来就有连接"的回合内采集后落库；
- 手动压缩（`/compact`）必须与问答回合共用同一把执行权锁：任务进行中天然不能压缩，
  压缩中也不能发新消息 —— 两个 SDK 客户端同时操作同一个 resume 会话会互相踩状态。

本文件锁定：
① 采集口径与失败边界（未测量→available=false 且不回 0%；采集失败只写 context_error 不抛）；
② CLI autoCompact 的 PreCompact/PostCompact 变成用户可见 SSE，采集点在 disconnect 之前；
③ 提供商繁忙只做有限重试（可恢复分类 + 未流出正文 + 次数/等待封顶）；
④ 压缩的执行权获取沿用条件更新纪律，被占时给可操作原因（不"无锁直接压缩"）。
"""
import asyncio
import inspect
from types import SimpleNamespace

import pytest

from backend.modules.mind.execution import context_usage, secure_sdk
from backend.modules.mind.execution.models import ExecutionContext, ExecutionTask


# ── 测试替身（与 test_sdk_done_first 同一套流协议）─────────────────

class TextBlock:
    """类名是判据：_execute_stream 按 `type(block).__name__ == "TextBlock"` 分派。"""

    def __init__(self, text):
        self.text = text


class AssistantMessage:
    def __init__(self, text):
        self.content = [TextBlock(text)]


class ResultMessage:
    def __init__(self, *, is_error=False, subtype="success", result="答案正文",
                 errors=None, terminal_reason="", stop_reason=""):
        self.is_error = is_error
        self.subtype = subtype
        self.result = result
        self.errors = errors or []
        self.terminal_reason = terminal_reason
        self.stop_reason = stop_reason
        self.num_turns = 1
        self.duration_ms = 5
        self.session_id = "sdk-sess-1"


USAGE = {"contextWindow": {"usedPercentage": 63.5},
         "autoCompact": {"enabled": True, "thresholdPercentage": 80},
         "categories": [{"type": "messages", "percentage": 40},
                        {"type": "free_space", "percentage": 36.5}],
         "session": {"messageCount": 12}, "model": "auto"}


class _FakeClient:
    """按回合脚本回放响应流；`get_context_usage` 可配成成功/抛错。"""

    def __init__(self, scripts, *, usage=USAGE, usage_raises=None, hook=None):
        self._scripts = list(scripts)
        self._round = -1
        self._usage = usage
        self._usage_raises = usage_raises
        self._hook = hook
        self.queries = []
        self.disconnected = False
        self.usage_calls = 0
        self._transport = SimpleNamespace(_process=SimpleNamespace(pid=4321, returncode=None))

    async def connect(self):
        pass

    async def query(self, prompt):
        self.queries.append(prompt)
        if self._hook is not None:
            self._hook()          # 模拟 CLI 在回合中间触发 PreCompact/PostCompact

    async def receive_response(self):
        self._round += 1
        for msg in self._scripts[min(self._round, len(self._scripts) - 1)]:
            yield msg

    async def get_context_usage(self):
        self.usage_calls += 1
        if self._usage_raises is not None:
            raise self._usage_raises
        return self._usage

    async def disconnect(self):
        self.disconnected = True


class _FakeRuntime:
    def __init__(self):
        from backend.modules.mind.execution.tool_policy import compile_policy
        self.policy = compile_policy({"as_bot_key": "analyst", "name": "n",
                                      "tools": {"groups": []}, "models": []})
        self.sdk_session_id = ""
        self.sdk_pid = 0
        self.closing = False
        self.tool_tasks = set()
        self.finished = False
        self.finish_args = ()

    def record_client(self, client):
        pass

    def confirm_stopped(self):
        pass

    def finish(self, sdk_session_id="", success=False, initialized_only=False):
        self.finished = True
        self.finish_args = (sdk_session_id, success)


def _patch_startup(monkeypatch, runtime, client):
    async def _fake_owned(function, *args):
        return function(*args)
    monkeypatch.setattr(secure_sdk, "run_owned_sync", _fake_owned)
    monkeypatch.setattr(secure_sdk, "_persist_turn_fallback", lambda fallback: None)
    # 产物上云与进度条无关（真实实现会读会话目录/对象存储）：换成 no-op 保持日志干净
    monkeypatch.setattr(secure_sdk, "_sync_turn_files", lambda runtime, user_id: None)
    import backend.modules.mind.execution.session_workspace as sw
    monkeypatch.setattr(sw, "claim_session", lambda *a, **k: runtime)
    import backend.modules.mind.execution.resource_guard as rg
    monkeypatch.setattr(rg, "bind_resources", lambda *a, **k: set())
    import backend.modules.mind.execution.sdk_tools.compat as compat
    fake_sdk = SimpleNamespace(QoderSDKClient=lambda options=None: client,
                               QoderAgentOptions=lambda **kw: SimpleNamespace(**kw),
                               HookMatcher=lambda **kw: kw)
    monkeypatch.setattr(compat, "load_sdk", lambda backend_: fake_sdk)
    monkeypatch.setattr(secure_sdk, "build_options",
                        lambda adapter, task, backend_: SimpleNamespace(model="m", cli_path="cli"))
    # 采集是展示旁路：落库换成 spy（单测不得碰元数据库）
    saved = {"snapshot": None, "error": None}
    monkeypatch.setattr(context_usage, "save_snapshot",
                        lambda cid, snapshot: saved.update(snapshot=snapshot, cid=cid))
    monkeypatch.setattr(context_usage, "save_measure_error",
                        lambda cid, reason: saved.update(error=reason, cid=cid))
    return saved


def _make_task(extra=None, context_events=None):
    ctx = ExecutionContext(workspace_id=7, user_id=42, username="u", user_role="admin",
                           extra={"conversation_id": 100, **(extra or {})})
    ctx.extra["_context_events"] = [] if context_events is None else context_events
    return ExecutionTask(task_id="t1", question="q", history=[], context=ctx)


def _drain(monkeypatch, client, extra=None, context_events=None):
    task = _make_task(extra, context_events)
    runtime = _FakeRuntime()
    saved = _patch_startup(monkeypatch, runtime, client)
    events = []

    async def scenario():
        async for ev in secure_sdk._execute_stream(
                SimpleNamespace(config={}, model="m",
                                _prompt_with_attachments=lambda t, include_history=True: "prompt"),
                task, "qoder"):
            events.append(ev)
        await asyncio.gather(*secure_sdk._teardown_tasks)

    asyncio.run(scenario())
    return events, task, runtime, saved


# ── ① 采集口径与失败边界 ──────────────────────────────────────────

def test_snapshot_maps_categories_with_business_labels():
    snap = context_usage.snapshot_from_usage(USAGE)
    assert snap["used_percentage"] == 63.5
    assert snap["threshold_percentage"] == 80
    assert snap["auto_compact_enabled"] is True
    labels = {c["type"]: c["label"] for c in snap["categories"]}
    assert labels["messages"] == "对话消息"
    assert labels["free_space"] == "剩余空间"


def test_snapshot_without_percentage_is_error_not_zero():
    """CLI 没回占用时抛错：把"测不到"记成"占用 0%"是最坏的假信号。"""
    with pytest.raises(ValueError):
        context_usage.snapshot_from_usage({"contextWindow": {}})


# Claude Code CLI 2.1.277 实测返回体（不是编的样本）：字段名与 Qoder 网关完全不同
CLAUDE_USAGE = {
    "percentage": 2, "totalTokens": 4249, "maxTokens": 200000, "rawMaxTokens": 200000,
    "autoCompactThreshold": 167000, "isAutoCompactEnabled": True,
    "autocompactSource": "unknown-model", "model": "qwen3.7-plus",
    "categories": [
        {"name": "System prompt", "tokens": 4076, "kind": "used"},
        {"name": "Messages", "tokens": 173, "kind": "used"},
        {"name": "Autocompact buffer", "tokens": 33000, "kind": "buffer"},
        {"name": "Free space", "tokens": 162751, "kind": "free"},
        {"name": "MCP tools", "tokens": 120, "kind": "used"},
    ],
}


def test_claude_payload_normalized_by_backend():
    """claude 返回体走 claude 解析：占用/阈值/分类都要落进平台字段。"""
    snap = context_usage.snapshot_from_usage(CLAUDE_USAGE, backend="claude")
    assert snap["used_percentage"] == 2
    assert snap["auto_compact_enabled"] is True
    labels = {c["type"]: (c["label"], c["percentage"]) for c in snap["categories"]}
    assert labels["system_prompt"][0] == "系统提示词"
    assert labels["free_space"][0] == "剩余空间"
    assert labels["auto_compact"][0] == "自动压缩预留"
    assert labels["messages"][1] == round(173 / 200000 * 100, 2)
    assert snap["model"] == "qwen3.7-plus"


def test_claude_token_threshold_converted_to_percent():
    """claude 的 autoCompactThreshold 是 **Token 数**（167000）。

    不换算就会当成百分比存进去、页面显示成 167000%（_pct 会拦成 None，但阈值就永远空）。
    """
    snap = context_usage.snapshot_from_usage(CLAUDE_USAGE, backend="claude")
    assert snap["threshold_percentage"] == 83.5


def test_qoder_parser_on_claude_payload_raises_not_zero():
    """拿 Qoder 键名解 claude 返回体必须报错，不能静默给出“占用 0%”。

    这就是进度条长期 --% 的现场复现：字段名不匹配时旧实现只写一句
    “执行层未返回上下文占用百分比”，看不出是两套结构。
    """
    with pytest.raises(ValueError):
        context_usage.snapshot_from_usage(CLAUDE_USAGE)          # 默认按 qoder 解析
    with pytest.raises(ValueError):
        context_usage.snapshot_from_usage(CLAUDE_USAGE, backend="openai-agents")


def test_claude_percentage_missing_falls_back_to_token_ratio():
    """CLI 的 percentage 是取整值；缺失时用同源 totalTokens/maxTokens 换算（不是外部估算）。"""
    usage = {k: v for k, v in CLAUDE_USAGE.items() if k != "percentage"}
    snap = context_usage.snapshot_from_usage(usage, backend="claude")
    assert snap["used_percentage"] == round(4249 / 200000 * 100, 2)
    # 两者都缺 → 依旧报错，不拿 0 充数
    with pytest.raises(ValueError):
        context_usage.snapshot_from_usage({"model": "x"}, backend="claude")


def test_snapshot_rejects_out_of_range_values():
    assert context_usage._pct(120) is None
    assert context_usage._pct(-3) is None
    assert context_usage._pct(True) is None       # bool 不是百分比
    assert context_usage._pct("55") is None        # 不猜字符串


def _patch_query(monkeypatch, row):
    seen = {}

    def fake_execute_query(sql, params=None, fetchone=False, **kw):
        seen["sql"] = " ".join(sql.split())
        seen["params"] = params
        return row
    monkeypatch.setattr(context_usage, "execute_query", fake_execute_query)
    return seen


def test_unmeasured_usage_says_not_measured(monkeypatch):
    seen = _patch_query(monkeypatch, {"status": "idle", "context_used_pct": None,
                                      "context_threshold_pct": None, "context_categories": None,
                                      "context_model": "", "context_measured_at": None,
                                      "context_error": None, "compact_count": 0,
                                      "last_compact_at": None})
    out = context_usage.get_usage(123, 7)
    assert out["available"] is False
    assert out["used_percentage"] is None          # 不是 0
    assert "尚未测量" in out["reason"]
    # error 为空 = “没测过”（新会话），前端据此不渲染进度条；真失败不得走这一档，否则会被一起藏掉
    assert out["error"] == ""
    assert "c.user_id=%s" in seen["sql"], "占用读取未按属主过滤 → 可探测他人会话"


def test_measure_failure_is_reported_as_error_reason(monkeypatch):
    """测过但上次失败：仍带上一次失败原因，用户看得见"这次没刷新出来"。"""
    _patch_query(monkeypatch, {"status": "running", "context_used_pct": None,
                               "context_threshold_pct": None, "context_categories": None,
                               "context_model": "", "context_measured_at": None,
                               "context_error": "执行层未返回上下文占用百分比",
                               "compact_count": 0, "last_compact_at": None})
    out = context_usage.get_usage(123, 7)
    assert out["available"] is False
    assert out["reason"] == "执行层未返回上下文占用百分比"
    assert out["error"] == "执行层未返回上下文占用百分比"   # 机器可判的失败事实，不靠比对中文文案
    assert out["busy"] is True                     # running → 前端禁用压缩按钮


def test_usage_never_leaks_internal_identifiers(monkeypatch):
    _patch_query(monkeypatch, {"status": "idle", "context_used_pct": 42,
                               "context_threshold_pct": 80,
                               "context_categories": '[{"type":"messages","label":"对话消息","percentage":30}]',
                               "context_model": "auto", "context_measured_at": "2026-11-04T10:00:00",
                               "context_error": None, "compact_count": 2, "last_compact_at": None})
    out = context_usage.get_usage(123, 7)
    assert out["available"] is True and out["used_percentage"] == 42
    assert out["categories"][0]["label"] == "对话消息"
    text = str(out).lower()
    for leak in ("sdk_session_id", "session_key", "execution_token", "owner_pid"):
        assert leak not in text


def test_collect_failure_writes_error_and_does_not_raise(monkeypatch):
    """采集是旁路：任何失败都不得打断已算完的答案，但事实要落库可诊断。"""
    client = _FakeClient([[AssistantMessage("答"), ResultMessage()]],
                         usage_raises=RuntimeError("control request timeout"))
    events, task, runtime, saved = _drain(monkeypatch, client)
    done = [e for e in events if e["type"] == "done"][0]
    assert done["result"].success is True, "采集失败不得影响取数主链路"
    assert done["result"].meta.get("context_usage") is None
    assert saved["error"] == "control request timeout"
    assert saved["cid"] == 100


# ── ② 压缩可见性与采集点 ─────────────────────────────────────────

def test_build_options_registers_compact_hooks():
    """PreCompact/PostCompact 必须注册：autoCompact 发生在回合中间，不接住就永远看不见。"""
    source = inspect.getsource(secure_sdk.build_options)
    assert '"PreCompact"' in source and '"PostCompact"' in source
    assert '_context_events' in source


def test_compact_events_surface_as_sse_in_order(monkeypatch):
    """autoCompact 不能只存在于服务端日志：压缩中/已压缩必须是用户看得见的事件。"""
    compact_events = []

    def hook():
        compact_events.append({"event": "compacting", "trigger": "auto"})
        compact_events.append({"event": "compacted", "trigger": "auto", "summary_chars": 900})

    client = _FakeClient([[AssistantMessage("答案"), ResultMessage()]], hook=hook)
    events, task, runtime, saved = _drain(monkeypatch, client, context_events=compact_events)

    kinds = [(e["type"], e.get("event")) for e in events if e["type"] == "context"]
    assert kinds == [("context", "compacting"), ("context", "compacted")], "按发生顺序透出，不重不漏"
    # hook 登记表取完即清空：残留会在下一轮/重试回合里把旧事件再发一遍
    assert task.context.extra["_context_events"] == []
    done_idx = [e["type"] for e in events].index("done")
    assert done_idx > max(i for i, e in enumerate(events) if e["type"] == "context")


def test_collect_happens_before_teardown(monkeypatch):
    """采集点必须在 disconnect 之前：客户端每轮新建，错过这一轮就付一次额外建连。"""
    client = _FakeClient([[AssistantMessage("答案"), ResultMessage()]])
    events, task, runtime, saved = _drain(monkeypatch, client)
    assert client.usage_calls == 1
    assert saved["snapshot"]["used_percentage"] == 63.5
    done = [e for e in events if e["type"] == "done"][0]
    assert done["result"].meta["context_usage"]["threshold_percentage"] == 80
    assert client.disconnected is True, "收尾仍要断连（采集不替代收尾）"


def test_stream_source_orders_collection_before_spawn_teardown():
    source = inspect.getsource(secure_sdk._execute_stream)
    assert source.find("_collect_context_usage") < source.find("_spawn_teardown"), \
        "采集若在收尾之后，客户端已断开，进度条永远停在上一轮"


# ── ③ 提供商繁忙的有限重试 ────────────────────────────────────────

def test_provider_busy_retries_then_succeeds(monkeypatch):
    busy = ResultMessage(is_error=True, subtype="error_during_execution",
                         errors=["529 overloaded_error"], terminal_reason="rate_limit")
    client = _FakeClient([[busy], [AssistantMessage("重试后的答案"), ResultMessage()]])
    monkeypatch.setenv("AGENT_BUSY_MAX_RETRIES", "2")
    monkeypatch.setenv("AGENT_BUSY_MAX_RETRY_WAIT_S", "1")
    events, task, runtime, saved = _drain(monkeypatch, client)

    retrying = [e for e in events if e["type"] == "capacity" and e["event"] == "retrying"]
    assert len(retrying) == 1 and retrying[0]["max_attempts"] == 2
    assert len(client.queries) == 2, "繁忙应当真的重发一次，而不是只报个错"
    done = [e for e in events if e["type"] == "done"][0]
    assert done["result"].success is True
    assert done["result"].meta["retry_attempts"] == 1
    assert any("自动重试" in w for w in done["result"].meta.get("warnings") or []), \
        "试了两次才成不能伪装成一次就很快"


def test_provider_busy_exhausted_is_actionable_not_raw(monkeypatch):
    busy = ResultMessage(is_error=True, subtype="error_during_execution",
                         errors=["upstream rate_limit, retry after 2"])
    client = _FakeClient([[busy]])
    monkeypatch.setenv("AGENT_BUSY_MAX_RETRIES", "1")
    monkeypatch.setenv("AGENT_BUSY_MAX_RETRY_WAIT_S", "1")
    events, task, runtime, saved = _drain(monkeypatch, client)

    done = [e for e in events if e["type"] == "done"][0]
    result = done["result"]
    assert result.success is False
    assert result.meta["error_kind"] == "rate_limited"
    assert result.meta["retryable"] is True
    assert "模型服务" in result.error
    # 原始 subtype/errors 只进日志：对外文案不带内部字段（护栏 §7）
    assert "subtype=" not in result.error and "rate_limit," not in result.error


def test_non_recoverable_error_is_not_retried(monkeypatch):
    """权限/配置类错误重试等于重复取数与重复写动作：只跑一次。"""
    fail = ResultMessage(is_error=True, subtype="error_during_execution",
                         errors=["permission denied by policy"])
    client = _FakeClient([[fail]])
    monkeypatch.setenv("AGENT_BUSY_MAX_RETRIES", "2")
    events, task, runtime, saved = _drain(monkeypatch, client)
    assert len(client.queries) == 1
    done = [e for e in events if e["type"] == "done"][0]
    assert done["result"].meta.get("retryable") is False


def test_aborted_turn_is_not_restarted_with_partial_answer(monkeypatch):
    """已流出正文的繁忙回合不重跑：已下发的 token 无法回滚，重跑会叠两条不一致正文。"""
    busy = ResultMessage(is_error=True, subtype="error_during_execution",
                         errors=["529 overloaded_error"])
    client = _FakeClient([[AssistantMessage("已经给用户看到的半截"), busy]])
    monkeypatch.setenv("AGENT_BUSY_MAX_RETRIES", "2")
    monkeypatch.setenv("AGENT_BUSY_MAX_RETRY_WAIT_S", "1")
    events, task, runtime, saved = _drain(monkeypatch, client)
    assert len(client.queries) == 1, "已有正文时不得自动重跑"
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert "".join(tokens).count("已经给用户看到的半截") == 1
    done = [e for e in events if e["type"] == "done"][0]
    assert done["result"].meta["error_kind"] == "provider_busy"


# ── ④ 手动压缩的执行权纪律 ────────────────────────────────────────

def test_compact_requires_free_execution_right(monkeypatch):
    """running（本轮任务进行中）时拿不到锁 → 显式拒绝 + 可操作原因，绝不无锁压缩。"""
    def fake_write(sql, params=None):
        return 0                              # 条件更新没命中 = 执行权不在自己手里
    def fake_query(sql, params=None, fetchone=False, **kw):
        return {"status": "running", "execution_token": "tok-other"}
    monkeypatch.setattr(context_usage, "execute_write", fake_write)
    monkeypatch.setattr(context_usage, "execute_query", fake_query)
    with pytest.raises(context_usage.ContextCompactBusy) as exc:
        context_usage._acquire_execution_right("sess-1")
    assert "本轮任务进行中" in str(exc.value)


def test_compact_grabs_idle_session_with_token(monkeypatch):
    """idle 且无残留 token 才放行，并写入新 execution_token（与 claim_session 同一纪律）。"""
    seen = {}

    def fake_write(sql, params=None):
        seen["sql"] = " ".join(sql.split())
        seen["params"] = params
        return 1
    monkeypatch.setattr(context_usage, "execute_write", fake_write)
    monkeypatch.setattr(context_usage, "process_identity", lambda pid: "start-1", raising=False)
    token = context_usage._acquire_execution_right("sess-1")
    assert token and len(token) == 32
    assert "status='idle'" in seen["sql"] and "execution_token IS NULL" in seen["sql"]
    assert seen["params"][0] == token
    assert seen["params"][-1] == "sess-1"


def test_load_compact_target_checks_owner(monkeypatch):
    """属主校验在读取时做：会话不存在或不属于本人一律 404 语义。"""
    rows = {"conv": None, "session": None}

    def fake_query(sql, params=None, fetchone=False, **kw):
        return rows["conv"] if "adh_conversations" in sql else rows["session"]
    monkeypatch.setattr(context_usage, "execute_query", fake_query)
    with pytest.raises(context_usage.ContextCompactNotFound):
        context_usage._load_compact_target(123, 7)
    rows["conv"] = {"id": 123, "user_id": 7, "workspace_id": 1, "datasource_id": 2,
                    "as_bot_key": ""}
    with pytest.raises(context_usage.ContextCompactBusy):
        context_usage._load_compact_target(123, 7)
    rows["session"] = {"session_key": "sess-1", "user_id": 7, "sdk_session_id": "s"}
    conv, session = context_usage._load_compact_target(123, 7)
    assert session["session_key"] == "sess-1"
