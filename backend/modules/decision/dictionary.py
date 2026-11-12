"""指标/维度字典的业务名解析（决策树条件的唯一解析入口）。

为什么要单独一层：决策树的条件必须**可审计地**绑定到已建模的指标/维度口径，
不能让人拿物理列名或表达式蒙混过去（否则「建议」就变成"看起来能用、实际口径不明"
的空承诺）。本层按 `ontology-modeling.md` §7 的固定优先级解析：

    字典精确名(name / name_en) → 字典别名(aliases) → 回抛候选

**包含式模糊只回抛候选，绝不静默绑定**（宁缺勿错）；解析结果只带业务名与
`bound_object_key`，物理表/列一律不出这层。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from backend.common.db import execute_query

logger = logging.getLogger(__name__)


@dataclass
class DictHit:
    """一条字典命中（业务层视图，无物理细节）。"""
    kind: str                  # metric | dimension
    name: str                  # 字典权威业务名
    object_key: str            # bound_object_key（可能为空 = 字典行缺归属）
    source: str                # dict_exact | dict_alias
    name_en: str = ""
    aliases: list[str] = field(default_factory=list)


@dataclass
class DictMiss:
    """一次未命中（带可操作候选，只含业务名）。"""
    term: str
    kind: str
    reason: str                # not_found | fuzzy_rejected | no_object
    candidates: list[str] = field(default_factory=list)


def norm(s) -> str:
    """大小写/分隔符归一（与 binding_resolver._norm_ref 同口径，避免两套判等）。

    对外公开：引擎的列名匹配与本层必须用同一归一口径，否则同一名字两处判定不一致。
    """
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(s or "").lower())


_norm = norm


def _json_list(val) -> list:
    if isinstance(val, (list, tuple)):
        return [str(x).strip() for x in val if str(x).strip()]
    if not val:
        return []
    try:
        arr = json.loads(val) if isinstance(val, str) else val
    except (TypeError, ValueError):
        return []
    return [str(x).strip() for x in arr if str(x).strip()] if isinstance(arr, list) else []


def load_dictionary(datasource_id: int = 0) -> dict[str, list[DictHit]]:
    """加载指标 + 维度字典为 ``{kind: [DictHit]}``。

    `datasource_id`>0 只看本源 + 全局(0) 的字典行，与 terminology_manager 的分桶
    口径一致（避免跨数据源串味）；0 = 全量（无会话源的调用，如决策中心配置页校验）。

    刻意不加载 target_table / target_column：字典行的物理落点是语义层的事，
    决策树这层拿到 `bound_object_key` 就够了，再多给一点就多一条泄露面。
    """
    out: dict[str, list[DictHit]] = {"metric": [], "dimension": []}
    ds_filter = ""
    params: list = []
    if int(datasource_id or 0) > 0:
        ds_filter = "AND (datasource_id = 0 OR datasource_id = %s)"
        params = [int(datasource_id)]

    for kind, table in (("metric", "adh_metrics"), ("dimension", "adh_dimensions")):
        sql = (f"SELECT name, name_en, COALESCE(aliases, '[]') AS aliases, "
               f"COALESCE(bound_object_key, '') AS bound_object_key "
               f"FROM {table} WHERE is_active = 1 {ds_filter}")
        try:
            rows = execute_query(sql, params) or []
        except Exception as exc:  # noqa: BLE001 — 字典加载失败必须显式，不回落"没有字典"
            logger.error("[decision] %s 字典加载失败: %s", table, exc)
            raise
        for r in rows:
            name = str(r.get("name") or "").strip()
            if not name:
                continue
            out[kind].append(DictHit(
                kind=kind, name=name,
                object_key=str(r.get("bound_object_key") or "").strip(),
                source="dict_exact",
                name_en=str(r.get("name_en") or "").strip(),
                aliases=_json_list(r.get("aliases")),
            ))
    return out


def resolve(term: str, kind: str, index: dict[str, list[DictHit]]) -> tuple[DictHit | None, DictMiss | None]:
    """按固定优先级解析一个业务名；未命中回抛候选（不静默绑定）。"""
    term = str(term or "").strip()
    if not term:
        return None, DictMiss(term=term, kind=kind, reason="not_found")
    hits = index.get(kind) or []
    n = _norm(term)

    for h in hits:
        if n == _norm(h.name) or (h.name_en and n == _norm(h.name_en)):
            return _with_origin(h, "dict_exact"), None
    for h in hits:
        if any(n == _norm(a) for a in h.aliases):
            return _with_origin(h, "dict_alias"), None

    # 包含式模糊：只给候选，不绑定（错数比无答案危害大）
    approx = sorted({h.name for h in hits if n and (n in _norm(h.name) or _norm(h.name) in n)})
    if approx:
        return None, DictMiss(term=term, kind=kind, reason="fuzzy_rejected", candidates=approx[:8])
    return None, DictMiss(term=term, kind=kind, reason="not_found")


def _with_origin(hit: DictHit, source: str) -> DictHit:
    return DictHit(kind=hit.kind, name=hit.name, object_key=hit.object_key,
                   source=source, name_en=hit.name_en, aliases=list(hit.aliases))


def suggest_metrics(index: dict[str, list[DictHit]], limit: int = 40) -> list[str]:
    """给配置页/错误文案用的指标业务名清单（排序稳定，不含物理名）。"""
    return sorted({h.name for h in (index.get("metric") or [])})[:limit]
