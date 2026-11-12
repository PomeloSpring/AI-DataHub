"""决策树契约模型（Pydantic，`extra="forbid"` 与 `semantics/models.py` 同风格）。

`tree_doc` 是决策树的**唯一可编辑事实源**（落 `adh_decision_trees.tree_doc`）；
`required_metrics` / `bound_object_keys` 是发布时从 doc 重算的派生列，禁止独立编辑。

契约刻意只收**业务名**：条件里的 `metric` / `dimension` 必须是指标/维度字典的业务名
或其别名，不接受物理表列名、`datasource_id`、SQL 片段 —— 由 `service.validate_doc`
在保存与发布两处把关（护栏 §7 黑盒 + ontology-modeling §7 解析守则）。
"""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 比较符白名单：求值段只做这些比较，不解释任何表达式文本。
COMPARATORS = ("=", "!=", ">", ">=", "<", "<=", "between", "in", "not_in")
SEVERITIES = ("low", "medium", "high", "critical")
# 服务端文书（报表区块/导出）用的中文口径，与前端 `api/decisions.ts` 同名常量同词。
# 只这两处：不得在调用点就地自译（同一条建议在报表与卡片上叫不同名字就是双口径）。
SEVERITY_LABELS = {"low": "低", "medium": "中", "high": "高", "critical": "紧急"}
VERDICT_LABELS = {"hit": "命中", "no_hit": "未命中",
                  "partial": "未完整评估", "skipped": "未评估"}
NODE_TYPES = ("branch", "leaf")
BRANCH_OPS = ("all", "any")

# 相对时间窗形状与语义层一致（护栏 §7：不得让 LLM/人算绝对日期）。
_WINDOW_PATTERN = r"^\d{1,4}[smhdwMy]$"

# ASCII「表.列」形态：两侧都是标识符字符且至少一个点。只用于拦物理限定名，
# 中文业务名（含「目标值 0.05」这种带小数点的写法）不会被它误杀。
_QUALIFIED_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")


class DecisionCondition(BaseModel):
    """一个判定条件 = 某指标（可按维度切分）实测值与阈值的比较。

    `metric` 是指标字典业务名（唯一解析入口），不是物理列名也不是表达式。
    `threshold_ref` 与 `value` 二选一：前者引用另一指标当阈值（如「高于同期均值的
    1.2 倍」这类相对阈值），后者是常量。两者都给或都不给视为契约错误。
    """
    model_config = ConfigDict(extra="forbid")

    metric: str = Field(..., min_length=1, max_length=200, description="指标字典业务名")
    comparator: str = Field(..., description="|".join(COMPARATORS))
    value: Optional[Any] = Field(None, description="常量阈值（between 传 [下, 上]，in 传清单）")
    threshold_ref: str = Field("", max_length=200, description="引用另一指标业务名当阈值")
    dimension: str = Field("", max_length=200, description="切分维度（维度字典业务名；空=整体口径）")
    dimension_value: Optional[Any] = Field(None, description="只看该维度的某个取值（配合 dimension）")
    time_window: Optional[str] = Field(None, pattern=_WINDOW_PATTERN,
                                       description="相对时间窗（7d/24h/2w/1M），与语义层同口径")
    time_column: str = Field("", max_length=200, description="事件时间维度业务名（缺省回落时间维度）")
    unit: str = Field("", max_length=32, description="展示单位（纯文案，不参与比较）")

    @field_validator("comparator")
    @classmethod
    def _check_comparator(cls, v: str) -> str:
        if v not in COMPARATORS:
            raise ValueError(f"比较符 '{v}' 不在白名单（仅 {'|'.join(COMPARATORS)}）")
        return v

    @field_validator("metric", "threshold_ref", "dimension", "time_column")
    @classmethod
    def _check_no_sql_fragment(cls, v: str) -> str:
        return _reject_physical_ref(v)


class DecisionLeaf(BaseModel):
    """叶子 = 条件 + 建议引用（建议文案收在文案库，叶子只存 `advice_key`）。"""
    model_config = ConfigDict(extra="forbid")

    type: Literal["leaf"] = "leaf"
    label: str = Field(..., min_length=1, max_length=200, description="这条判定说的是什么情形")
    when: DecisionCondition
    advice_key: str = Field(..., min_length=1, max_length=128, description="建议文案库的稳定标识")
    severity: Optional[str] = Field(None, description="覆盖文案库的严重级（空=沿用库值）")
    evidence_note: str = Field("", max_length=512, description="给人看的依据说明（纯文案）")

    @field_validator("advice_key")
    @classmethod
    def _check_advice_key(cls, v: str) -> str:
        return _reject_physical_ref(v)


class DecisionBranch(BaseModel):
    """分支 = 若干子节点的 all / any 组合（不开放自由表达式）。"""
    model_config = ConfigDict(extra="forbid")

    type: Literal["branch"] = "branch"
    label: str = Field(..., min_length=1, max_length=200)
    op: str = Field("all", description="all|any")
    children: list["DecisionNode"] = Field(..., min_length=1)

    @field_validator("op")
    @classmethod
    def _check_op(cls, v: str) -> str:
        if v not in BRANCH_OPS:
            raise ValueError(f"分支组合 '{v}' 无效（仅 {'|'.join(BRANCH_OPS)}）")
        return v


DecisionNode = DecisionBranch | DecisionLeaf


class DecisionTreeDoc(BaseModel):
    """tree_doc 顶层：一个根分支 + 可选全局口径说明。"""
    model_config = ConfigDict(extra="forbid")

    root: DecisionBranch
    note: str = Field("", max_length=1000, description="这棵树的判断口径说明（人可读）")


DecisionTreeDoc.model_rebuild()


class SiteRule(BaseModel):
    """站点适用性一条 = 站点代码 + 该站的数据源**业务名**（不存 id，见护栏 §7）。"""
    model_config = ConfigDict(extra="forbid")

    site_code: str = Field(..., min_length=1, max_length=64)
    datasource: str = Field("", max_length=200, description="数据源业务名（空=沿用站点派生）")


class DecisionOutcome(BaseModel):
    """引擎对外产物（服务端内部结构；对外投影由 service 再做一次脱敏）。

    `verdict` 四态刻意区分「没命中」与「没评估」：后者必须带原因，不能让调用方
    把「按原因跳过」误读成「数据正常所以没建议」（no-silent-degradation §1）。
    """
    model_config = ConfigDict(extra="forbid")

    tree_key: str = ""
    tree_name: str = ""
    tree_version: int = 0
    verdict: Literal["hit", "no_hit", "partial", "skipped"] = "skipped"
    reason: str = Field("", description="非 hit 必须可解释（含缺失的指标名与原因）")
    hit_path: list[str] = Field(default_factory=list, description="命中路径的节点 label 序列")
    advice_keys: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    missing_metrics: list[str] = Field(default_factory=list,
                                       description="partial 时：本轮没取到、需要补取数的指标业务名")
    scope_notes: list[str] = Field(default_factory=list,
                                   description="本次评估口径的附加说明（看板筛选参数是否套用）；"
                                               "即使命中也得让人看到口径，不能只当非 hit 的附属")
    unresolved: list[dict[str, Any]] = Field(default_factory=list,
                                             description="解析失败的业务名 + 候选（不含物理名）")
    site_code: str = ""
    mode: str = "full"
    elapsed_ms: int = 0


def _reject_physical_ref(val: str) -> str:
    """拒绝把物理标识/表达式塞进契约（保存与发布两处都会命中）。

    只拦**明确**的物理引用形态，不做内容形态的正则猜测（那属于 outbound_guard
    的职责边界，硬编码在这里会变成第二套裁决）：空白、点限定名、SQL 关键字、
    引号/反引号/括号/分号等表达式字符。

    点限定名单独用 ASCII 标识符形状判定（``table.col`` / ``db.tbl.col``）：字典里的
    英文指标名可以带下划线，但不会写成 ``a.b``；把形态写宽会误杀业务名里的
    小数点（如「目标值 0.05」），反而让配置人改往字段里塞表达式。
    """
    s = str(val or "").strip()
    if not s:
        return s
    lowered = s.lower()
    for kw in ("select ", "from ", "join ", "where ", "group by ", "count(", "sum("):
        if kw in lowered:
            raise ValueError(f"'{s}' 看起来是 SQL 表达式；条件只能引用指标/维度字典的业务名")
    if any(ch in s for ch in ("`", '"', "'", ";", "\n")):
        raise ValueError(f"'{s}' 含非法字符；请填业务名（不带引号/分号）")
    if _QUALIFIED_NAME_RE.match(s):
        raise ValueError(f"'{s}' 是「表.列」这种物理限定名；请填指标/维度字典的业务名")
    if s.lower().startswith(("datasource_id", "catalog_ref", "physical_table")):
        raise ValueError(f"'{s}' 是系统内部标识，不是业务名")
    return s


def collect_condition_names(doc: DecisionTreeDoc) -> tuple[list[str], list[str]]:
    """走 doc 收集 (指标业务名清单, 维度业务名清单)，按首次出现去重。

    供发布时重算派生列 `required_metrics`，以及引擎决定要取哪些数。
    """
    metrics: list[str] = []
    dims: list[str] = []

    def add(bucket: list[str], name: str) -> None:
        name = str(name or "").strip()
        if name and name not in bucket:
            bucket.append(name)

    def walk(node: DecisionNode) -> None:
        if isinstance(node, DecisionLeaf):
            add(metrics, node.when.metric)
            add(metrics, node.when.threshold_ref)
            add(dims, node.when.dimension)
            add(dims, node.when.time_column)
            return
        for child in node.children:
            walk(child)

    walk(doc.root)
    return metrics, dims
