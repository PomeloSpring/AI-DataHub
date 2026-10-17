"""参数化 SQL 的渲染与方言正文选择 —— 确定性、无 LLM。

数据集（`adh_datasets` 的 SQL 形态）是平台内**唯一**的"可信任预制 SQL"载体：
人写 SQL 形状（两级聚合、窗口函数、自关联），机器负责三件确定性的事：

1. **方言正文选择**：`sql_bodies` 按 引擎 → 方言族 → generic 取用，三者都没有即拒绝执行，
   **绝不回退到别的引擎正文**（把 Doris 专有函数下发给 MySQL 会静默出错数）；
2. **参数渲染**：`${name}` 按 `variables` 声明类型严格校验后转成字面量。
   必须服务端字面量渲染而非绑定参数：SLS 的 PG 端点不支持 prepared statement
   （见 `semantics/execution/connectors/sls.py`），且模板正文本身要能被治理解析器读通；
3. **注入面收敛**：未声明的参数键、缺失的必填参数、含危险片段（`;` `--` `/*` `*/`）的值
   一律拒绝；enum 只允许注册时列出的候选值。

信任边界与 `adh_metrics.formula` 同级：正文由建模人员编写并经只读校验后注册，
调用方（LLM/看板/Playground）只能填参数值，不能改正文。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from backend.semantics import dialect as sem_dialect

# 参数类型受控集合（与 _render_scalar 一一对应，新增两边同步）
VARIABLE_TYPES = frozenset({
    "int", "integer", "long", "bigint",
    "float", "number", "decimal", "double",
    "bool", "boolean", "date", "datetime", "timestamp",
    "enum", "string", "list",
})
# 值内容里一律禁止的片段（多语句/注释是注入的主要通道）
FORBIDDEN_VALUE_FRAG = (";", "--", "/*", "*/")

PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$")


def parse_json_obj(raw: Any, fallback: Any = None) -> Any:
    """DB JSON/TEXT 列 → Python 对象；解不出返回 fallback（调用方按"未声明"处理）。"""
    v = raw
    if isinstance(v, (bytes, bytearray)):
        try:
            v = v.decode()
        except Exception:  # noqa: BLE001
            return fallback
    if isinstance(v, str):
        if not v.strip():
            return fallback
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return fallback
    return v if v is not None else fallback


def parse_variables(raw: Any) -> dict[str, dict[str, Any]]:
    """`variables` 列 → {name: decl}。支持 dict 形式与 [{name,...}] 数组形式。

    解析失败视为"无声明"（返回 {}），由调用方按未声明参数键拒绝。
    """
    v = parse_json_obj(raw, None)
    out: dict[str, dict[str, Any]] = {}
    if isinstance(v, dict):
        for k, item in v.items():
            out[str(k)] = ({**item, "name": str(k)} if isinstance(item, dict)
                           else {"name": str(k), "type": str(item)})
    elif isinstance(v, list):
        for item in v:
            if isinstance(item, dict) and item.get("name"):
                out[str(item["name"])] = item
    return out


def literal(value: Any) -> str:
    """SQL 字面量渲染（各方言一致：单引号翻倍转义）。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def render_scalar(name: str, val: Any, decl: dict[str, Any]) -> tuple[str, Optional[str]]:
    """按声明类型渲染单个参数值。返回 (SQL 片段, 错误)。"""
    vtype = str(decl.get("type") or "string").lower()

    if vtype in ("int", "integer", "long", "bigint"):
        if isinstance(val, bool):
            return "", f"参数 '{name}' 需为整数"
        s = str(val).strip()
        if not re.match(r"^-?\d+$", s):
            return "", f"参数 '{name}' 需为整数, 实得 {val!r}"
        return s, None
    if vtype in ("float", "number", "decimal", "double"):
        if isinstance(val, bool):
            return "", f"参数 '{name}' 需为数值"
        try:
            f = float(str(val).strip())
        except ValueError:
            return "", f"参数 '{name}' 需为数值, 实得 {val!r}"
        if f != f or f in (float("inf"), float("-inf")):
            return "", f"参数 '{name}' 需为有限数值"
        return repr(f), None
    if vtype in ("bool", "boolean"):
        if isinstance(val, bool):
            return ("1" if val else "0"), None
        low = str(val).strip().lower()
        if low in ("true", "1"):
            return "1", None
        if low in ("false", "0"):
            return "0", None
        return "", f"参数 '{name}' 需为布尔值, 实得 {val!r}"
    if vtype == "date":
        s = str(val).strip()
        if not _DATE_RE.match(s):
            return "", f"参数 '{name}' 需为日期 YYYY-MM-DD, 实得 {val!r}"
        return literal(s), None
    if vtype in ("datetime", "timestamp"):
        s = str(val).strip().replace("T", " ")
        if not _DATETIME_RE.match(str(val).strip()) and not _DATETIME_RE.match(s):
            return "", f"参数 '{name}' 需为日期时间, 实得 {val!r}"
        return literal(s), None
    if vtype == "enum":
        allowed = [str(x) for x in (decl.get("values") or [])]
        if allowed and str(val) in allowed:
            return literal(str(val)), None
        return "", f"参数 '{name}' 必须为 {allowed} 之一"
    # string / 未知类型: 先拒危险片段再转义
    s = str(val)
    for frag in FORBIDDEN_VALUE_FRAG:
        if frag in s:
            return "", f"参数 '{name}' 含非法片段 '{frag}'"
    return literal(s), None


def render_value(name: str, val: Any, decl: dict[str, Any]) -> tuple[str, Optional[str]]:
    """渲染参数值；list 渲染为逗号序列（供 `IN (${x})`），元素类型取 item_type。"""
    vtype = str(decl.get("type") or "").lower()
    if isinstance(val, (list, tuple)):
        if not val:
            return "", f"参数 '{name}' 不接受空列表"
        declared_item = str(decl.get("item_type") or "").lower()
        item_type = declared_item or (vtype if vtype and vtype != "list" else "string")
        parts: list[str] = []
        for it in val:
            s, err = render_scalar(name, it, {**decl, "type": item_type})
            if err:
                return "", err
            parts.append(s)
        return ", ".join(parts), None
    return render_scalar(name, val,
                         {**decl, "type": "string" if vtype == "list" else (vtype or "string")})


def render_body(sql: str, variables: dict[str, dict[str, Any]],
                args: dict[str, Any]) -> tuple[str, list[str]]:
    """把 `${name}` 渲染为受控字面量。返回 (正文, 错误清单)——有错即不可执行。

    未声明键/缺参/类型不符全部收进错误清单（一次报全，供调用方 fail-loud）。
    声明了但正文里没出现的参数**同样校验**：避免"传了不合法的值但没生效所以看不见"
    的静默差异（多方言正文下，某个引擎版本用不到该参数很常见，但值必须合法）。
    """
    errors: list[str] = []
    decl = variables if isinstance(variables, dict) else {}

    def _sub(m: "re.Match[str]") -> str:
        name = m.group(1)
        var_decl = decl.get(name) or {}
        if isinstance(args, dict) and name in args:
            val = args[name]
        elif "default" in var_decl:
            val = var_decl["default"]
        else:
            errors.append(f"缺少参数: {name}")
            return m.group(0)
        out, err = render_value(name, val, var_decl)
        if err:
            errors.append(err)
            return m.group(0)
        return out

    rendered = PLACEHOLDER_RE.sub(_sub, sql or "")
    in_body = set(PLACEHOLDER_RE.findall(sql or ""))
    for name, val in (args or {}).items():
        if name in decl and name not in in_body:
            _out, err = render_value(name, val, decl.get(name) or {})
            if err:
                errors.append(err)
    # 剩下的 ${...}：若只是“已声明但缺值”（已报缺参），不再重复报“占位符非法”；
    # 只有那些根本不是合法声明名的残留才是写法错误。
    leftovers = set(PLACEHOLDER_RE.findall(rendered))
    for name in leftovers:
        if name not in decl:
            errors.append(f"正文存在未声明的占位符 ${{{name}}}（应先在 variables 声明）")
    if "${" in rendered:
        malformed = [seg for seg in re.findall(r"\$\{[^}]*$|\$\{(?!\w+\})", rendered)]
        if malformed or not leftovers:
            errors.append("正文存在写法非法的占位符（应为 ${name}）")
    return rendered, errors


def declared_names(sql: str, variables: dict[str, Any]) -> set[str]:
    """正文占位符 + 声明的参数名集合：用于判定"调用方多传了没声明的键"。"""
    return set(variables or {}) | set(PLACEHOLDER_RE.findall(sql or ""))


def pick_body(
    sql_bodies: Any, sql_main: str, engine: str, family: str,
) -> tuple[str, Optional[str]]:
    """选正文：`sql_bodies` 按 引擎 → 方言族 → generic；无 `sql_bodies` 时用 `sql_main`。

    返回 (正文, 错误)。有 sql_bodies 但三档都不命中时**报错而不是回退**：
    拿别的引擎正文执行 = 静默错数，比拒绝执行危害大得多。
    """
    bodies = parse_json_obj(sql_bodies, None)
    if isinstance(bodies, dict) and bodies:
        expr, unsupported = sem_dialect.pick_variant(bodies, engine, family)
        if unsupported:
            return "", f"未提供 {engine} 引擎实现（显式声明不支持），已拒绝执行"
        if not expr:
            expr = str(bodies.get("generic") or "").strip()
        if not expr:
            return "", (f"未提供 {engine}/{family} 方言实现"
                        f"（已注册: {sorted(str(k) for k in bodies)}），已拒绝执行")
        return expr.strip(), None
    if not isinstance(bodies, dict) and bodies not in (None, "", [], {}):
        return "", "sql_bodies 必须是对象（{引擎|方言族: SQL 正文}）"
    return str(sql_main or "").strip(), None


def placeholder_names(sql: str) -> list[str]:
    return sorted(set(PLACEHOLDER_RE.findall(sql or "")))
