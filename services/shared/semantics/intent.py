"""Intent parsing / normalization for the semantic layer.

LLM(ChatBI 侧) 输出的是"意图 JSON", 本模块负责:
1. 宽容解析(LLM 常把 JSON 塞在 markdown 代码块里、或把 value/values 混用);
2. 归一化到 SemanticQuery(pydantic), 严格拒收 SQL / 未知字段;
3. 返回可读错误, 供 Agent 自我纠正 (Palantir "确定性规则校验" 的那一环)。

用法:
    from services.shared.semantics.intent import parse_intent
    q, err = parse_intent(llm_output_str)
    if err: ...回喂给 LLM 重试
"""

from __future__ import annotations

import json
import re
from typing import Any

from services.shared.semantics.models import SemanticQuery, normalize_object_ref

# LLM 常把 JSON 包在 ```json ... ``` 代码块里
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(\{.*?\})\s*```", re.DOTALL)
# 兜底:找第一个平衡的 {...}
_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)

# 明确禁止的字段:任何 SQL 出口都必须被拒, 让 LLM 改出意图 (Palantir 决策 A)
_FORBIDDEN_KEYS = {"sql", "query", "rawSql", "raw_sql", "statement", "table", "tables"}


def _loads_lenient(text: str) -> dict[str, Any] | None:
    """把 LLM 自由文本抽出一个 dict。失败返回 None。"""
    if not text:
        return None
    s = text.strip()
    # 直接 JSON
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except (ValueError, TypeError):
        pass
    # 代码块里的 JSON
    m = _FENCE_RE.search(s)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    # 贪婪大括号
    m = _BRACE_RE.search(s)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    return None


def _normalize_payload(obj: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """把常见别名/容错项归一到 SemanticQuery 字段; 返回 (payload, notes)。"""
    notes: list[str] = []
    payload = dict(obj)

    # 禁止 SQL 旁路
    hit = [k for k in _FORBIDDEN_KEYS if k in payload]
    if hit:
        notes.append(f"forbidden_keys:{','.join(hit)} — 意图不得携带 SQL, 请改用 metrics/dimensions/filters")
        for k in hit:
            payload.pop(k, None)

    # 别名归一
    if "object" not in payload and "entity" in payload:
        payload["object"] = payload.pop("entity"); notes.append("alias:entity->object")
    if "object" in payload and isinstance(payload["object"], str):
        payload["object"] = normalize_object_ref(payload["object"])
    for k in ("metrics", "dimensions"):
        if k in payload and payload[k] is None:
            payload[k] = []
        elif k in payload and isinstance(payload[k], str):
            payload[k] = [payload[k]]; notes.append(f"coerce:{k}=str->list")
    if "filter" in payload and "filters" not in payload:
        payload["filters"] = payload.pop("filter")

    # filters 归一: 允许 [{dim, op, value}] 也允许 {dim: value} 简易形式
    if isinstance(payload.get("filters"), dict):
        payload["filters"] = [
            {"dim": k, "op": "eq", "value": v} for k, v in payload["filters"].items()
        ]
        notes.append("coerce:filters=dict->list")
    elif isinstance(payload.get("filters"), list):
        cleaned: list[dict[str, Any]] = []
        for f in payload["filters"]:
            if isinstance(f, dict) and "dim" in f:
                cleaned.append(f)
            elif isinstance(f, dict) and len(f) == 1:
                (k, v), = f.items()
                cleaned.append({"dim": k, "op": "eq", "value": v})
        payload["filters"] = cleaned

    # limit 容错
    if "limit" in payload and payload["limit"] in (None, "", 0):
        payload.pop("limit")

    # timeGrain -> time_grain (GraphQL-subset 驼峰别名)
    if "timeGrain" in payload and "time_grain" not in payload:
        payload["time_grain"] = payload.pop("timeGrain")

    # 剔除未识别字段: pydantic extra=forbid 会显式失败, 但给 LLM 一次自动纠错机会
    allowed = set(SemanticQuery.model_fields.keys())
    unknown = [k for k in payload if k not in allowed]
    if unknown:
        notes.append(f"dropped_unknown_keys:{','.join(sorted(unknown))}")
        for k in unknown:
            payload.pop(k, None)

    return payload, notes


def parse_intent(text_or_dict: Any) -> tuple[SemanticQuery | None, str | None, list[str]]:
    """宽容解析 -> (query, error, notes)。

    - query: 成功时的 SemanticQuery; 失败为 None
    - error: 人类可读错误; 无错为 None (可直接回喂 LLM 重试)
    - notes: 归一化过程的提示(别名/丢弃未知字段), 供审计展示
    """
    if isinstance(text_or_dict, SemanticQuery):
        return text_or_dict, None, []
    if isinstance(text_or_dict, str):
        raw = _loads_lenient(text_or_dict)
        if raw is None:
            return None, "intent is not a JSON object", []
    elif isinstance(text_or_dict, dict):
        raw = text_or_dict
    else:
        return None, f"unsupported intent type: {type(text_or_dict).__name__}", []

    payload, notes = _normalize_payload(raw)
    # SQL 旁路 = 确定性拒绝(Palantir "约束到合法 IRI" 那一环): 回供错误 -> LLM 重试
    sql_hit_note = next((n for n in notes if n.startswith("forbidden_keys:")), None)
    if sql_hit_note:
        return None, sql_hit_note, notes
    try:
        q = SemanticQuery.model_validate(payload)
    except Exception as e:
        return None, f"intent validation failed: {e}", notes
    if not q.object:
        return None, "intent.object is required", notes
    return q, None, notes
