"""工具 schema 的 required 生成契约.

历史缺陷: qodercn_agent_sdk 对 dict 形态 schema 直接
`"required": list(properties.keys())` —— 所有键一律必填, 完全忽略 Optional,
导致 save_dimension 的可选 `id` 被判为必填, **新建维度/指标永远失败**
(报 "params must have required property 'id'")。
compat.to_json_schema 在交给 SDK 前先转好, 本文件锁死该契约。
"""

from typing import Annotated, Optional

from backend.modules.mind.execution.sdk_tools.compat import to_json_schema


def test_optional_params_are_not_required():
    schema = to_json_schema({
        "display_name": Annotated[str, "维度业务名"],
        "bound_object_key": Annotated[str, "归属对象 key"],
        "property_name": Annotated[Optional[str], "对象属性业务名"],
        "id": Annotated[Optional[int], "更新已有维度时传其 id；新增不传"],
    })
    assert sorted(schema["required"]) == ["bound_object_key", "display_name"]
    assert "id" not in schema["required"]
    assert "property_name" not in schema["required"]


def test_save_dimension_spec_matches_real_call_shape():
    """LLM 新建维度只传 display_name/bound_object_key/property_name —— 必须能通过校验。"""
    from backend.modules.mind.execution.sdk_tools.ontology_tools import TOOL_SPECS

    spec = next(s for s in TOOL_SPECS if s["name"] == "save_dimension")
    schema = to_json_schema(spec["schema"])
    # 只有 display_name / bound_object_key 真必填(与 handler 的 missing 校验一致)
    assert sorted(schema["required"]) == ["bound_object_key", "display_name"]
    assert "id" not in schema["required"]
    for optional_key in ("id", "property_name", "category", "name_en", "aliases",
                         "value_labels", "description"):
        assert optional_key not in schema["required"], optional_key
        assert optional_key in schema["properties"]


def test_save_metric_optional_id_not_required():
    from backend.modules.mind.execution.sdk_tools.ontology_tools import TOOL_SPECS

    spec = next(s for s in TOOL_SPECS if s["name"] == "save_metric")
    schema = to_json_schema(spec["schema"])
    assert "id" not in schema["required"]
    assert sorted(schema["required"]) == ["agg_type", "bound_object_key", "display_name", "formula"]


def test_optional_union_syntax_also_optional():
    """X | None (PEP 604) 与 Optional[X] 行为一致。"""
    schema = to_json_schema({"a": Annotated[str, ""], "b": Annotated[int | None, ""]})
    assert schema["required"] == ["a"]


def test_prop_schema_carries_description_and_type():
    schema = to_json_schema({
        "n": Annotated[int, "数量"],
        "tags": Annotated[Optional[list], "标签"],
        "labels": Annotated[Optional[dict], "枚举码→标签"],
    })
    assert schema["properties"]["n"] == {"type": "integer", "description": "数量"}
    assert schema["properties"]["tags"]["type"] == "array"
    assert schema["properties"]["labels"]["type"] == "object"


def test_already_json_schema_passes_through():
    """调用方显式给了 JSON Schema 就原样透传，不覆盖它的 required。"""
    raw = {"type": "object", "properties": {"a": {"type": "string"}}, "required": []}
    assert to_json_schema(raw) is raw


def test_empty_and_non_dict_spec():
    assert to_json_schema({}) == {"type": "object", "properties": {}}
    assert to_json_schema(None) == {"type": "object", "properties": {}}


def test_tool_error_text_extracts_readable_reason():
    """工具错因要能取出来透传给用户，不得被通用文案覆盖。"""
    from backend.modules.mind.execution.sdk_tools.compat import _tool_error_text

    assert _tool_error_text({"content": [{"type": "text", "text": "数据源 0 无有效表元数据，请先执行元数据同步"}]}) == (
        "数据源 0 无有效表元数据，请先执行元数据同步")
    assert _tool_error_text({"content": []}) == ""
    assert _tool_error_text({}) == ""
    assert _tool_error_text(None) == ""
    # 只取 text 块，其他类型跳过
    assert _tool_error_text({"content": [{"type": "image"}, {"type": "text", "text": "x"}]}) == "x"


def test_tool_error_not_masked_by_generic_message():
    """源码级护拦：工具错因不得被「联系管理员」类通用文案替换（no-silent-degradation §1）。"""
    from pathlib import Path

    src = Path("backend/modules/mind/execution/sdk_tools/compat.py").read_text(encoding="utf-8")
    # 曾经的缺陷：isError 时直接回写固定文案，把 handler 给的可操作错因丢了
    assert "工具执行失败，请联系管理员查看服务端日志。" not in src
    assert "_tool_error_text" in src
