"""`run_semantic_query` 的行载荷预算（防运行时把整份结果截成文件指针）。

来历：会话 1783069459337 里 200 行「页面URL」高基数结果 = 73,446 / 62,406 字符，被 agent 运行时
判「输出过大」落盘换成文件指针 —— 模型只读到半本数据却当全量作答，随后反复试错。
`_MAX_ROWS` 只封条数不封体积，所以另加字符预算，且截断事实必须显式（no-silent-degradation §1）。
"""
import asyncio
import json

import pytest

from backend.modules.mind.execution.sdk_tools import semantic_query as sq


def _envelope(out: dict) -> dict:
    """解 `_text` 信封：断言的是模型真正看到的那份正文，不是内存里的 dict。"""
    return json.loads(out["content"][0]["text"])


def _wide_rows(n: int) -> list[dict]:
    return [{"页面URL": f"https://www.example.com/p/{i:04d}?from=direct-ip&uv=1", "浏览量": 1000 + i}
            for i in range(n)]


def _payload(rows: list[dict], **extra) -> dict:
    return {"object": "rum_event", "columns": ["页面URL", "浏览量"], "rows": rows,
            "row_count": extra.get("row_count", len(rows)), "execution_ms": 12,
            **{k: v for k, v in extra.items() if k != "row_count"}}


def _run(args: dict) -> dict:
    return asyncio.run(sq.run_semantic_query(args))


@pytest.fixture
def single_result(monkeypatch):
    """把执行口换成可控载荷（不连库、不跑语义层），只留本文件要测的信封收口逻辑。"""
    state = {"payload": {}, "is_error": False}

    async def fake_single(intent, ctx):
        return state["payload"], state["is_error"]

    from backend.modules.mind.execution.sdk_tools import context as exec_ctx
    monkeypatch.setattr(sq, "_execute_single", fake_single)
    monkeypatch.setattr(exec_ctx, "get_execution_context", lambda: None)
    return state


def test_wide_rows_are_cut_to_budget_and_say_so(single_result):
    rows = _wide_rows(200)
    assert len(json.dumps(rows, ensure_ascii=False, indent=2)) > sq._MAX_PAYLOAD_CHARS
    single_result["payload"] = _payload(rows, row_count=4321, truncated=True)

    out = _envelope(_run({"intent_json": "{}"}))

    assert len(json.dumps(out["rows"], ensure_ascii=False, indent=2)) <= sq._MAX_PAYLOAD_CHARS
    assert out["row_count"] == 4321, "被砍的是展示行，真实行数不得跟着改口"
    assert out["truncated"] is True and 0 < out["rows_shown"] < len(rows)
    assert out["rows"][0]["浏览量"] == 1000, "只回前缀，不得乱序或跳行"
    # 截断必须可诊断：说清原因 + 指明"别拿截断行当全量"的下一步
    assert "字符预算" in out["truncated_note"] and "4321" in out["truncated_note"]
    assert "聚合" in out["aggregation_warning"]


def test_small_result_is_not_marked_truncated(single_result):
    rows = [{"渠道": "自然搜索", "会话": 10}, {"渠道": "直接访问", "会话": 90}]
    single_result["payload"] = _payload(rows)

    out = _envelope(_run({"intent_json": "{}"}))

    assert out["rows"] == rows
    assert "truncated_note" not in out and "truncated" not in out
    assert out["rows_shown"] == len(rows), "未截断也要如实回已下发行数"


def test_over_budget_single_row_keeps_one_row(single_result):
    """预算再小也留一行：空 rows 会被模型读成「查到了但没有」，那是错数不是降级。"""
    single_result["payload"] = _payload([{"正文": "x" * 40000}, {"正文": "y" * 40000}], row_count=2)

    out = _envelope(_run({"intent_json": "{}"}))

    assert len(out["rows"]) == 1 and out["rows_shown"] == 1 and out["truncated"] is True


def test_error_payload_is_left_alone(single_result):
    single_result["payload"] = {"object": "rum_event", "error": "该对象未绑定物理表",
                                "columns": [], "rows": [], "row_count": 0}
    single_result["is_error"] = True

    out = _envelope(_run({"intent_json": "{}"}))

    assert "truncated_note" not in out and "rows_shown" not in out


def test_batch_budget_split_keeps_whole_call_within_budget(single_result):
    """批量（多维下钻）按条数均分预算：整次调用的行载荷不得撞穿运行时上限。"""
    rows = _wide_rows(200)
    single_result["payload"] = _payload(rows, row_count=999)
    intents = json.dumps([{"object": "rum_event"}] * sq._MAX_BATCH)

    out = _envelope(_run({"intents_json": intents}))

    assert len(out["results"]) == sq._MAX_BATCH
    total = sum(len(json.dumps(r["rows"], ensure_ascii=False, indent=2)) for r in out["results"])
    assert total <= sq._MAX_PAYLOAD_CHARS + sq._MAX_BATCH * 8, "均分下限不得把整次调用抬过预算"
    assert all(r["truncated"] and r["rows_shown"] < len(rows) for r in out["results"])


def test_floor_only_binds_at_max_batch():
    """`_MIN_PAYLOAD_CHARS` 只在批量取满时兜底（8×2000=16000），否则均分值必然更小。"""
    assert sq._MIN_PAYLOAD_CHARS * sq._MAX_BATCH <= sq._MAX_PAYLOAD_CHARS
    assert max(sq._MIN_PAYLOAD_CHARS, sq._MAX_PAYLOAD_CHARS // 2) == sq._MAX_PAYLOAD_CHARS // 2
