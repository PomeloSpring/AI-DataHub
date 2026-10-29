"""指标公式「合法单表聚合」判定 —— 三处写入口的唯一口径。

历史缺陷：同一判定在 `ontology_service`(本体生成/归并)、`metrics_service`(字典写 API)、
`scripts/backfill_dict_target_table`(存量回填) 各抄一份正则，且都把带点 token 一律当
「表限定列」。而 SLS/ARMS LogStore 的**列名本体就含点**（`session.id`、
`resource.duration`、`view.url`），于是 19 条 RUM 指标全部被误判跨表 → target_table 留空
→ planner 按 `target_table=物理表` 加载不到指标 → 语义问数「无法解析」。

口径（宁缺勿错）：
- 仅当公式以聚合函数开头、不含子查询/内联 WHERE，且**没有真正的跨表限定**时，
  才可把 target_table 绑定为对象主表；
- 带点 token 有两种身份，不靠正则猜，以**列元数据实存**为唯一裁决：
  `session.id` ∈ 该表实存列 → 列名本体含点（合法）；
  `Case.id`     ∉ 实存列且前缀不是主表 → 跨表限定（回炉，需 JOIN 建模）。
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from backend.modules.catalog.services.physical_ref import columns_of_table

logger = logging.getLogger(__name__)

# 带点 token 的身份裁决（列名本体含点 vs 表限定写法）唯一实现收口在 physical_ref；
# 本模块只负责「合法单表聚合」判定，列元数据读取沿用同源入口。
__all__ = ["AGG_HEAD", "SELECT_TOKEN", "WHERE_TOKEN", "QUALIFIED", "LITERAL",
           "columns_of_table", "is_cross_table", "metric_safe_target"]

# 聚合头部（以聚合函数开头才可能是单表聚合）
AGG_HEAD = re.compile(r"^\s*(SUM|AVG|COUNT|MIN|MAX|approx_percentile|count_if|"
                      r"APPROX_DISTINCT)\s*\(", re.I)
SELECT_TOKEN = re.compile(r"\bSELECT\b", re.I)
WHERE_TOKEN = re.compile(r"\bWHERE\b", re.I)
# 完整带点链（两段式只能看到 `resource.sse`，会把嵌套字段误判成跨表限定）
QUALIFIED = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)")
# 字符串字面量 / 已加引号片段：扫描前先剥掉，避免把 'view' 之类码值当限定名
LITERAL = re.compile(r"'[^']*'|\"[^\"]*\"")


def is_cross_table(formula: str, primary_table: str,
                   known_columns: Optional[set] = None) -> bool:
    """公式里是否存在真正的跨表限定（既不是主表自身限定、也不是实存的带点列名）。"""
    t = (primary_table or "").strip()
    scan = LITERAL.sub(" ", str(formula or ""))
    for token in QUALIFIED.findall(scan):
        if known_columns and token in known_columns:
            continue                        # 实存列名（带点是其本体形状）
        if t and token.split(".", 1)[0].lower() == t.lower():
            continue                        # 主表自身限定
        return True
    return False


def metric_safe_target(formula: str, primary_table: str,
                       known_columns: Optional[set] = None) -> str:
    """公式为「合法单表聚合」时返回其 target_table(=对象主表)，否则空串。

    排除内联 WHERE / 子查询 / 跨表限定 / 非聚合(布尔标记·标量) —— 这些属建模回炉项，
    激活会让 planner 生成非法/错误 SQL，故留空 target_table 使其维持 fail-loud
    （宁缺勿错，不把「目录挂着、编译用不了」变成「生成坏 SQL」）。
    """
    f = (formula or "").strip()
    t = (primary_table or "").strip()
    if not f or not t:
        return ""
    scan = LITERAL.sub(" ", f)
    if SELECT_TOKEN.search(scan) or WHERE_TOKEN.search(scan):
        return ""
    if is_cross_table(f, t, known_columns):
        return ""
    return t if AGG_HEAD.match(f) else ""
