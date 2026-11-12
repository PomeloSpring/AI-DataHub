"""回合结局的诚实性：断连没跑完不得记成 success，done 必须把实况带给前端（Phase E/D 口径）。

证据（会话 1783069459150）：客户端断开 → 本仓侧提前 return，`observability.finalize`
拿不到任何结果状态就回落成 `success`，`final_answer=''` —— 管理端 trace 里这是一条
"跑了 126s 的成功回合"，而用户什么都没拿到、消息表里 `messages=[]`。
假成功比失败更难发现：它让所有统计（成功率、平均耗时、credit 产出）都往好的方向偏。

锁定：
① 断连的两个出口都把 trace 标注为 error + 可诊断原因（不再靠默认值）；
② done 事件把 warnings（执行层给的可见告警）与服务端口径耗时下发，不硬编码空列表；
③ 中止回合同样回带 trace_id/message_uuid 与计数，让前端与审计能对上号。
"""
import inspect

from backend.modules.mind.services import chat_service as cs


def test_mark_turn_interrupted_records_error_not_success(monkeypatch):
    from backend import observability

    seen = {}
    monkeypatch.setattr(observability, "set_result",
                        lambda **kw: seen.update(kw))
    cs._mark_turn_interrupted("客户端已断开，本回合结果未送达")
    assert seen == {"status": "error", "error": "客户端已断开，本回合结果未送达"}


def test_every_disconnect_exit_annotates_the_turn():
    """两个 is_disconnected 出口都必须显式标注：漏一个就还有一条假成功路径。"""
    src = inspect.getsource(cs.ChatService._try_dispatch_via_execution_layer)
    assert src.count("if await request.is_disconnected():") == 2, src
    assert src.count("_mark_turn_interrupted(") == 2, "断连出口与标注数量必须一致"


def test_done_events_never_hardcode_empty_warnings():
    """warnings 是"本轮是否完整"的载体：派发之后的两条 done 都不得写死空列表。

    派发前的失败（执行层缺失/权限配置不可用）本就没有执行层告警可带，属合法空列表，
    所以判据切在"拿到 result 之后"那一段。
    """
    src = inspect.getsource(cs.ChatService._try_dispatch_via_execution_layer)
    assert src.count('"warnings": list(result.meta.get("warnings") or [])') == 2, src
    tail = src.split("if result is None:", 1)
    assert len(tail) == 2, "派发段结构变化，判据需同步（不得放着可能的假空列表不管）"
    assert '"warnings": [],' not in tail[1], "拿到 result 之后仍写死空列表会吞掉执行层告警"


def test_abort_done_carries_counts_and_trace_keys():
    src = inspect.getsource(cs.ChatService._try_dispatch_via_execution_layer)
    for key in ('"elapsed_ms": _server_elapsed_ms(result)', '"tool_call_count"',
                '"queries"', '"timed_out"', '"turn_limit_seconds"',
                '_err_done["trace_id"]', '_err_done["message_uuid"]'):
        assert key in src, key


def test_turn_warnings_are_rendered_by_the_frontend_card():
    """服务端把告警发出去还不够：前端消息卡必须渲染 warnings（否则等于没提醒）。"""
    import pathlib
    text = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "Chat.tsx"
            ).read_text(encoding="utf-8")
    assert "msg.warnings" in text, "warnings 需在气泡上可见"
