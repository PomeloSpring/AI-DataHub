"""ES SQL 的 sqlglot 方言（engine='es'）+ 改写产物形态白名单复核。

**为什么必须自建方言**（2026-11-12/13 对本仓真实集群逐条实机取证）：

- sqlglot 没有 elasticsearch 方言，但 ES SQL 是 Calcite 子集：**Presto 方言对它的
  双引号标识符、GROUP BY/ORDER BY/LIMIT、`DATE_TRUNC('MONTH', CAST(x AS TIMESTAMP))`、
  `CONCAT`、`LENGTH`、`REPEAT`、`GREATEST`、`COUNT(DISTINCT x)` 都原样往返**；
- 唯一成问题的是取子串函数系 —— Presto 生成器会把
  `LEFT(x, n)` 渲染成 `SUBSTR(x, 1, n)`、`RIGHT(x, n)` 渲染成
  `SUBSTR(x, LENGTH(x) - (n - 1))`、`SUBSTRING(x, 1, n)` 降级成 `SUBSTR(x, 1, n)`，
  而 **ES 没有 SUBSTR**（实机 400：`Unknown function [SUBSTR], did you mean [SUBSTRING]`）；
- ES 的 `CONCAT` **只收 2 个参数**（3 参报 `expects exactly two arguments`），
  `SUBSTRING` **必须 3 个参数**（2 参报 `expects exactly three arguments`）；
- ES **没有哈希函数**：`MD5/SHA1/SHA2/TO_HEX/HEX` 全部 400 → `hash` 型脱敏在本引擎
  不可表达（不是"暂未实现"，是引擎没有），只能 fail-loud 让策略改用 partial 或走数据源 SQL 通道；
- ES 拒绝的形态：`||` 拼接运算符、反引号标识符、`OFFSET`、`WITH`/CTE、`JOIN`、
  `IN (SELECT ...)`（`IN query not supported yet`）、`EXISTS`（`EXISTS is not yet supported`）；
- 未知 db_type 在 `sql_guard.dialect_of_db_type` / `semantics.dialect.family_of` 里
  **回退 mysql**，而 mysql 生成器会把 `"appCode.keyword"` 变成**字符串字面量**
  `'appCode.keyword'`、把 `DATE_TRUNC` 重写成 `STR_TO_DATE(CONCAT(YEAR(...)))` ——
  那是"语法能过、结果全错"的静默错数。所以 ES 必须先在这里登记，才允许进任何改写路径。

**为什么用产物白名单复核，而不是仓里既有的"渲染保真门"**（`rls.renderer_faithful`）：
实测 24 条存量 ES 正文里只有 3 条能过严格字符串往返，其余差异全是 `<>`→`!=`、
逗号后补空格这类**装饰性**改写（语义等价且 ES 接受）。拿它当门会把合法查询误拒；
真正的风险在**函数与语法形态**，所以这里直接判产物里有没有 ES 不认的东西。
"""

from __future__ import annotations

import re
from typing import Optional

from sqlglot import exp
from sqlglot.dialects.presto import Presto

ES_DIALECT = "es"
ES_ENGINE = "es"


def _substring_sql(self, expression) -> str:
    """ES 只认 3 参 SUBSTRING：缺长度时按"到末尾"补 `LENGTH(x) - start + 1`。"""
    col = self.sql(expression, "this")
    start = self.sql(expression, "start") or "1"
    length = self.sql(expression, "length") or f"LENGTH({col}) - ({start}) + 1"
    return f"SUBSTRING({col}, {start}, {length})"


def _left_sql(self, expression) -> str:
    return f"SUBSTRING({self.sql(expression, 'this')}, 1, {self.sql(expression, 'expression')})"


def _right_sql(self, expression) -> str:
    col = self.sql(expression, "this")
    n = self.sql(expression, "expression")
    return f"SUBSTRING({col}, GREATEST(LENGTH({col}) - ({n} - 1), 1), {n})"


class Es(Presto):
    """Elasticsearch SQL 方言：语法骨架沿用 Presto（实机验证原样往返），函数渲染改回 ES 形态。"""

    class Generator(Presto.Generator):
        TRANSFORMS = {
            **Presto.Generator.TRANSFORMS,
            exp.Substring: _substring_sql,
            exp.Left: _left_sql,
            exp.Right: _right_sql,
        }


# 改写产物里一律拒绝的形态：命中即说明 sqlglot 或上游把 SQL 写成了 ES 不认的样子。
# 判定前先剥单引号字面量与双引号标识符（字段名里带 `with`/`in` 等词不该误伤）。
_ES_REJECTED: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bSUBSTR\s*\(", re.IGNORECASE), "SUBSTR()（ES 只有 SUBSTRING，请检查函数映射）"),
    (re.compile(r"\b(MD5|SHA1|SHA2|TO_HEX|HEX)\s*\(", re.IGNORECASE),
     "哈希函数（ES 无 MD5/SHA/TO_HEX，hash 型脱敏在本引擎不可表达）"),
    (re.compile(r"\|\|"), "|| 字符串拼接（ES 不支持，须用 CONCAT(a, b) 两参形式）"),
    (re.compile(r"`"), "反引号标识符（ES 用双引号）"),
    (re.compile(r"\bOFFSET\b", re.IGNORECASE), "OFFSET（ES SQL 没有 OFFSET 子句）"),
    (re.compile(r"\bJOIN\b", re.IGNORECASE), "JOIN（ES SQL 不支持连接）"),
    (re.compile(r"^\s*WITH\b", re.IGNORECASE), "WITH/CTE（ES SQL 不支持）"),
    (re.compile(r"\(\s*SELECT\b", re.IGNORECASE),
     "子查询/派生表（ES 侧 `SELECT *` 包裹不透传 .keyword 子字段）"),
)
_LIMIT_CLAUSE_RE = re.compile(r"\bLIMIT\s+\d+\b", re.IGNORECASE)


def _strip_quoted(sql: str) -> str:
    """剥掉单引号字面量与双引号标识符，只留语法骨架（供形态扫描）。"""
    text = re.sub(r"'(?:[^'\\]|\\.)*'", "''", str(sql or ""))
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)


def es_rejection(sql: str) -> Optional[str]:
    """返回首个"ES 不认"的形态说明；合规时返回 None。"""
    skeleton = _strip_quoted(sql)
    for pattern, reason in _ES_REJECTED:
        if pattern.search(skeleton):
            return reason
    if not _LIMIT_CLAUSE_RE.search(skeleton):
        return "缺少 LIMIT 子句（ES SQL 必须显式带 LIMIT）"
    return None


def assert_es_renderable(sql: str) -> str:
    """改写产物必须落在 ES 认的形态上；不合规直接拒（不回退未改写正文）。"""
    reason = es_rejection(sql)
    if reason:
        raise PermissionError(f"ES 改写产物形态不被引擎接受：{reason}，已拒绝执行")
    return sql
