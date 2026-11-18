"""方言族(family) + 引擎级变体(engine) 解析 —— 单一口径。

两级方言是必要的：
- **family** 决定 SQL 语法骨架（标识符引用、时间函数、INTERVAL 写法），编译层按族出文：
  `mysql|doris -> mysql 族`，`postgres|postgresql|pg|sls -> postgres 族`，`es -> es 族`；
- **engine** 决定"同族不同实现"的函数差异（Doris `window_funnel`、SLS 的 Presto 函数系、
  原生 PG 的 `date_trunc`），是**变体选择键**，不参与语法骨架生成。

ES 分两级登记（两件事，不是两条口径）：数据集正文的方言覆盖（`FAMILY_OF_DBTYPE`）
**不含** es（ES 数据集走透传，正文不被二次包裹编译）；单表对象编译骨架（
`COMPILE_FAMILY_OF_DBTYPE`）**含** es（平台编译形状需要 ES 自己的写法）。

历史缺陷（本模块要根治的那类）：`adh_metrics.formula_dialects` 早期只认族键，建模人员
按引擎写 `{"doris": "window_funnel(...)"}` 时，Doris 源解析成 `mysql` 族 → 键永不命中，
指标静默选不出来。因此：
- 变体取用一律走 `pick_variant()`，优先级 **engine → family → 通用回落**；
- 注册侧用 `unknown_variant_keys()` 把拼错/不支持的键显式报出来（fail-loud），
  不允许"写了但永远不生效"的死键（no-silent-degradation）。

消费方：planner（指标 formula_dialects / 模板 sql_bodies）、字典与模板注册校验。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# ES 型 db_type 判定的事实源在 L0（`common/db_types.py`）：连接工厂也要用同一个清单，
# 而分层契约禁止 common 引 semantics。这里引上来并再导出，保持既有消费方（core/modules）
# 的 `from backend.semantics.dialect import is_es_db_type` 写法不变。
from backend.common.db_types import ES_DB_TYPES, is_es_db_type  # noqa: F401 (re-export)

# db_type -> 数据集正文方言族（预制 SQL 的方言覆盖判定：sql_bodies 键、跨源兼容告警）
# 注意：`es` **故意不登在这里**。ES 型数据集在语义层走「透传正文」契约（正文已定形状，
# 平台只做参数渲染/投影裁剪/谓词下推/LIMIT 收紧），而 dataset_compiler 的聚合分支会产
# 两级包裹 `SELECT ... FROM (正文) ...` —— ES 无派生表（实机 400：派生表列不透传 /
# 子查询 LIMIT 与外层 GROUP BY 互斥），把 es 拉进本表等于让 ES 正文被二次包裹编译。
# ES 的**单表对象**编译骨架由下面的 COMPILE_FAMILY_OF_DBTYPE 单独登记（见该常量注释）。
FAMILY_OF_DBTYPE: dict[str, str] = {
    "mysql": "mysql", "doris": "mysql",
    "postgres": "postgres", "postgresql": "postgres", "pg": "postgres", "sls": "postgres",
}

# db_type -> **对象编译骨架**方言族（planner 把 SemanticQuery 编成单表 SQL 时用）
# 与 FAMILY_OF_DBTYPE 的分工（不是双口径，是两个不同问题）：
#   - 数据集对象 = 预制 SQL 透传，正文自带形状 -> 按正文方言覆盖判定（上表，es 不参与）；
#   - 单表对象 = 平台编译形状 `SELECT 维度, 聚合 FROM t WHERE ... GROUP BY ... LIMIT n`
#     -> 需要 ES 自己的骨架（双引号标识符、必带 LIMIT、时间桶走 SUBSTRING、无 JOIN/窗口/派生表）。
# ES 骨架的形态约束由 planner 的 es 分支 + `es_dialect.es_rejection` 复核（产物级护栏）。
COMPILE_FAMILY_OF_DBTYPE: dict[str, str] = {
    **FAMILY_OF_DBTYPE,
    "es": "es", "elasticsearch": "es",
}

# db_type -> 引擎名（同族内的函数/特性差异 + 模板可用范围）
# es 登 engine：治理层（RLS 注入形态 / mask 函数系 / 渲染产物复核）与 planner 的 ES 代码
# 分支都按引擎名判定；取证见 `semantics/es_dialect.py` 与 `security/masking.py` 的 ES 支。
ENGINE_OF_DBTYPE: dict[str, str] = {
    "mysql": "mysql", "doris": "doris",
    "postgres": "postgres", "postgresql": "postgres", "pg": "postgres", "sls": "sls",
    "es": "es", "elasticsearch": "es",
}

# 变体声明里允许的键：引擎名 ∪ 方言族名（族键=同族所有引擎共享的写法）
VARIANT_KEYS: frozenset = frozenset(set(ENGINE_OF_DBTYPE.values()) | set(FAMILY_OF_DBTYPE.values()))

_GENERIC = "generic"


def _norm(db_type: Any) -> str:
    return str(db_type or "").strip().lower()


def family_of(db_type: str) -> str:
    """db_type -> 数据集正文方言族；未知类型保守回落 mysql（与既有行为一致，调用方须出 warning）。

    **ES 不在此列**（数据集透传口径，见 FAMILY_OF_DBTYPE 注释）：对象编译请用
    `compile_family_of`。
    """
    return FAMILY_OF_DBTYPE.get(_norm(db_type), "mysql")


def compile_family_of(db_type: str) -> str:
    """db_type -> 对象编译方言族（planner 单表编译用）；ES 单独一族，其余同 FAMILY_OF_DBTYPE。"""
    return COMPILE_FAMILY_OF_DBTYPE.get(_norm(db_type), "mysql")


def engine_of(db_type: str) -> str:
    """db_type -> 引擎名；未知类型回落方言族名（族内最保守的通用写法）。"""
    key = _norm(db_type)
    if key in ENGINE_OF_DBTYPE:
        return ENGINE_OF_DBTYPE[key]
    return FAMILY_OF_DBTYPE.get(key, "mysql")


def is_known_db_type(db_type: Any) -> bool:
    return _norm(db_type) in FAMILY_OF_DBTYPE


def pick_variant(
    declared: Optional[dict[str, Any]], engine: str, family: str,
) -> tuple[Optional[Any], bool]:
    """按 engine → family 取变体声明里的实现。

    Returns:
        (值, 显式不支持)。语义与既有 `formula_dialects` 保持一致：
        - 命中键且值非空 → (值, False)
        - 命中键但值为空/null → (None, True)  # 显式声明"本引擎/本族不支持"
        - 两个键都不存在 → (None, False)      # 未声明，调用方回落通用实现
    引擎键优先于族键，使 `{"mysql": null, "doris": "window_funnel(...)"}` 在 Doris 上可用、
    在普通 MySQL 上显式不支持（不会把 Doris 函数下发给 MySQL）。
    """
    if not isinstance(declared, dict) or not declared:
        return None, False
    for key in (engine, family):
        if key and key in declared:
            raw = declared.get(key)
            text = str(raw or "").strip()
            return (text or None), (not text)
    return None, False


def unknown_variant_keys(declared: Any, extra_allowed: Iterable[str] = ()) -> list[str]:
    """返回变体声明里无法识别的键（拼错的引擎/族名 = 永不生效的死键）。

    declared 允许是 dict 或 JSON 文本；解析不出来时返回 []（由调用方按"无声明"处理）。
    """
    if isinstance(declared, (bytes, bytearray)):
        try:
            declared = declared.decode()
        except Exception:  # noqa: BLE001 — 解不出即视为无声明
            return []
    if isinstance(declared, str):
        import json

        try:
            declared = json.loads(declared)
        except (ValueError, TypeError):
            return []
    if not isinstance(declared, dict):
        return []
    allowed = set(VARIANT_KEYS) | {str(x).strip().lower() for x in extra_allowed if x}
    allowed.discard("")
    bad = []
    for k in declared:
        key = str(k or "").strip().lower()
        if not key:
            continue
        if key not in allowed:
            bad.append(str(k))
    return sorted(bad)


def variant_key_hint() -> str:
    """给注册校验/报错用的合法键清单（业务可读，不含内部实现）。"""
    return (f"合法键: 引擎 {sorted(set(ENGINE_OF_DBTYPE.values()))} "
            f"或方言族 {sorted(set(FAMILY_OF_DBTYPE.values()))}")


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EXPR_CHAR_RE = re.compile(r"[\s(),;'\".\[}=+*/-]")
# 名字（而非表达式）字符集：词字符 + $ + 中文 + 连字符。含点的串不在这里
# （`t.col` 在多数引擎里是「表别名.列」的限定写法，不能当单个名字包引号；
#   SLS 那种列名本体带点的场景由 planner `_col` 按引擎显式处理）。
# 探针：SLS LogStore 名就是 `logstore-rum` 这种带连字符的名字，不加引号被解析成
#   `logstore - rum` 表达式（FROM logstore-rum 直接语法错），必须整体包引号。
_NAME_WITH_DASH_RE = re.compile(r"^[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_$\u4e00-\u9fff-]*$")


def quote_ident(name: Any, family: str = "mysql") -> str:
    """标识符引用：MySQL 族反引号 / PG 族双引号；运算表达式原样透传。

    含空白/括号/引号/逗号等运算结构的表达式不能当标识符包引号（否则 `SUM(a)` 被引号化）；
    其余（含中文词）一律视为标识符加引号，否则中文列别名在 ORDER BY/AS 处裸奔，PG 直接语法错。
    带连字符的名字（如 SLS LogStore `logstore-rum`）不是运算式：早先被 `_EXPR_CHAR_RE`
    误判为表达式而裸奔，生成 `FROM logstore-rum` 被引擎当减法。本函数修正这一类。
    **别名（AS 右侧）请用 `quote_alias`**：别名是名字不是表达式，含 ()/% 等字符的业务名
    若走本函数会被当表达式透传，生成 `AS 层间转化率(%)` 这种非法 SQL。
    """
    s = str(name or "").strip()
    if not s:
        return s
    if not _IDENT_RE.match(s) and _NAME_WITH_DASH_RE.match(s) and "-" in s:
        return quote_alias(s, family)
    if not _IDENT_RE.match(s) and _EXPR_CHAR_RE.search(s):
        return s
    return quote_alias(s, family)


# ANSI 双引号族：postgres 家族 + es（ES SQL 用双引号标识符，反引号直接 400）。
# es 既是引擎名也是它的编译骨架族名（COMPILE_FAMILY_OF_DBTYPE），故一并处理。
_ANSI_QUOTE_FAMILIES = frozenset({"postgres", "es"})


def quote_alias(name: Any, family: str = "mysql") -> str:
    """投影/排序别名引用：无论长什么样都当标识符包引号（别名永远是名字，不是表达式）。"""
    s = str(name or "").strip()
    if not s:
        return s
    if family in _ANSI_QUOTE_FAMILIES:
        return '"' + s.replace('"', '""') + '"'
    return f"`{s}`"
