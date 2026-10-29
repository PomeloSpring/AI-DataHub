"""方言族(family) + 引擎级变体(engine) 解析 —— 单一口径。

两级方言是必要的：
- **family** 决定 SQL 语法骨架（标识符引用、时间函数、INTERVAL 写法），编译层按族出文：
  `mysql|doris -> mysql 族`，`postgres|postgresql|pg|sls -> postgres 族`；
- **engine** 决定"同族不同实现"的函数差异（Doris `window_funnel`、SLS 的 Presto 函数系、
  原生 PG 的 `date_trunc`），是**变体选择键**，不参与语法骨架生成。

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

# db_type -> 编译方言族（SQL 语法骨架）
FAMILY_OF_DBTYPE: dict[str, str] = {
    "mysql": "mysql", "doris": "mysql",
    "postgres": "postgres", "postgresql": "postgres", "pg": "postgres", "sls": "postgres",
}

# db_type -> 引擎名（同族内的函数/特性差异 + 模板可用范围）
ENGINE_OF_DBTYPE: dict[str, str] = {
    "mysql": "mysql", "doris": "doris",
    "postgres": "postgres", "postgresql": "postgres", "pg": "postgres", "sls": "sls",
}

# 变体声明里允许的键：引擎名 ∪ 方言族名（族键=同族所有引擎共享的写法）
VARIANT_KEYS: frozenset = frozenset(set(ENGINE_OF_DBTYPE.values()) | set(FAMILY_OF_DBTYPE.values()))

_GENERIC = "generic"


def _norm(db_type: Any) -> str:
    return str(db_type or "").strip().lower()


def family_of(db_type: str) -> str:
    """db_type -> 编译方言族；未知类型保守回落 mysql（与既有行为一致，调用方须出 warning）。"""
    return FAMILY_OF_DBTYPE.get(_norm(db_type), "mysql")


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


def quote_alias(name: Any, family: str = "mysql") -> str:
    """投影/排序别名引用：无论长什么样都当标识符包引号（别名永远是名字，不是表达式）。"""
    s = str(name or "").strip()
    if not s:
        return s
    if family == "postgres":
        return '"' + s.replace('"', '""') + '"'
    return f"`{s}`"
