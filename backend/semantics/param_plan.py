"""数据集入参 → 看板逻辑参数 的归一计划（确定性、无 LLM、**不枚举字段名**。

要解决的问题：一个看板上多个数据集常常需要"同一个东西"（统计时间窗口、站点、
机型、序列号……），但各数据集声明的变量名/类型/形状都可能不一样。用户手工在三处
（数据集入参、看板参数、控件绑定）对齐一个名字，拼错就静默失效——而静默失效的后果
不是报错，是**出一个"看着正常其实没筛选"的数**。

归一判据一律取**正文里的谓词形状**与**声明的类型**，绝不写"哪个字段名算时间窗口"这种
白名单——那样每来一个新业务字段就要改代码。现网实测（44 个声明了入参的 active 数据集）
零白名单命中 43 个，剩下 1 个是"函数实参"形状，已单独成为一条通用判据（`func_arg`）。

三层职责（与 2026-11-06 拍板一致）：
 - 数据集 `variables`：**唯一口径权威**，声明"我有哪些入参、什么类型、正文怎么用"；
 - 看板 `page_params` + 控件：只持有**逻辑参数**（一个控件一个逻辑参数）；
 - 本模块：把逻辑参数值**翻译**成各数据集的 `${}` args（唯一翻译点，前后端同源）。

`dash_key`（可选，写在变量声明里）是人工归一的落库位置：显式声明永远优先于形状推断。
推断不出来的（`unknown`）与口径常数（`func_arg`）**一律不自动建控件**，交调用方显式列出，
不得猜一个控件凑数，也不得静默用数据集默认值出数（no-silent-degradation）。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from backend.semantics.sql_params import parse_variables, placeholder_names

PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)}")
_TIME_UNIT_RE = re.compile(r"\b(DAY|DAYS|WEEK|MONTH|QUARTER|YEAR|HOUR|MINUTE)\b", re.I)
# 日期函数/字面量：判定"这个端点比的是时间"的通用证据（不是列名白名单）
_DATE_FN_RE = re.compile(
    r"\b(NOW|CURRENT_DATE|CURDATE|CURRENT_TIMESTAMP|SYSDATE|GETDATE|DATE_SUB|DATE_ADD|"
    r"TIMESTAMPADD|TO_DATE|DATE_FORMAT|STR_TO_DATE|PARSEDATETIME|DATEDIFF|DATE)\s*\(", re.I)
_DATE_LITERAL_RE = re.compile(r"'\d{4}-\d{2}-\d{2}")
# 类型转换壳：`CAST(${x} AS BIGINT)` 不改变参数语义，判形状前先剥掉，
# 否则占位符既不像比较端点也不像函数实参（实测 ds=4 的 window_sec 只能落 other）
_CAST_RE = re.compile(r"\bCAST\s*\(\s*(\$\{\w+\})\s+AS\s+[A-Za-z0-9_(\s)+]*\)", re.I)
# 被比较侧（列标识）的时间词根。这是**证据**而非入参名白名单：任意新表的新时间列
# （stat_date / event_time / warningTime / create_time / dt / updated_at …）都按词根命中，
# 不需要同步改这张表；扫描前会先把 `${x}` 占位符剔掉，避免"参数名里带 time 就当时间"的自证。
_TIME_ROOTS = ("date", "time", "timestamp", "_dt", "_at", "_ts")
_TIME_COLUMN_RE = re.compile(
    r"\w*(?:" + "|".join(_TIME_ROOTS) + r")\w*", re.I)

KIND_TIME_WINDOW = "time_window"      # 统计时间窗口（区间 + 可选相对天数回退）
KIND_SCALAR = "scalar"                # 单值筛选（等值 / 可空等值 / 枚举 / 数值）
KIND_RANGE = "range"                  # 非时间的成对区间（本期不自动建控件）
KIND_CONSTANT = "constant"            # 口径常数（函数实参）：不生成控件
KIND_UNKNOWN = "unknown"              # 形状判不出：不生成控件，交人确认

TIME_WINDOW_KEY = "time_window"       # 时间窗口的规范逻辑参数名（形状决定，非字段名）

# 入参值域（登记在数据集入参声明上，不按变量名猜）：'site' = 值是站点，
# 写入正文前要按目标字段翻译成该站点在此字段上的实际取值
# （现网实测：新加坡站点在业务表 site 列写 'global'，在日志侧 region 列写 'en'）
VALUE_DOMAIN_SITE = "site"
_PAIR_ROLES = ("range_start", "range_end", "window_len")
# 一个变量在正文里可能出现在多处，取优先级最高的那条形状作为它的角色
_ROLE_PRIORITY = ("window_len", "range_start", "range_end", "optional_eq", "eq", "func_arg", "other")
# 空值/类型壳：`NULLIF(${x}, '')`、`COALESCE(${x}, '')`、`CAST(${x} AS BIGINT)` 只是在给值
# 变形，不代表该入参是函数口径常数；判实参位时要跳过它们（实测 ds=51 的
# `collection_task_status = NULLIF(${x},'')` 曾因括号被误判成 window_funnel 那类实参）。
_WRAPPER_FN_RE = re.compile(r"\b(?:CAST|CONVERT|NULLIF|IFNULL|NVL|COALESCE|TRIM|IIF|IF)\s*\(\s*$", re.I)
_EMPTY_GUARD_TMPL = r"\b(?:NULLIF|IFNULL|NVL|COALESCE)\s*\(\s*%s\s*,\s*(?:''|NULL)\s*\)"

# "同一子表达式内"的邻近片段：不吃 AND/OR/逗号/另一个比较符（避免跨条件误判角色）
_NEAR = r"(?:(?!\bAND\b|\bOR\b|[,<>])[\s\S]){0,120}?"
# 时间锚点函数：谓词里出现它就说明这个条件在筛时间（与字段名无关）
_ANCHOR_FN_RE = re.compile(
    r"\b(?:NOW|NOW64|CURRENT_TIMESTAMP|CURRENT_DATE|CURDATE|SYSDATE|GETDATE|"
    r"TO_UNIXTIME|UNIX_TIMESTAMP|FROM_UNIXTIME|EXTRACT)\s*\(", re.I)
# 占位符**参与算术**（`now() - ${x}`、`${x} * 86400`）而不是被**直接比较**
# （`col >= ${x}`）：这是"相对偏移量（长度）"的形状特征。端点永远与列/字面量直接比，
# 把参与计算的量当端点会把天数当日期串下发（实测 ds=7
# `__time__ >= to_unixtime(now()) - ${days} * 86400`，日期串撞上声明 int 的 days，
# 被 sql_params 类型校验当场拒住 —— 好在没静默出错数）。
_ARITH_TAIL_RE = re.compile(r"^\s*(?:[*/]|[-+]\s*\d)")
_ARITH_HEAD_RE = re.compile(r"[*/+-]\s*$")

# 逻辑参数值类型 → 看板控件类型（必须都是 CHART_TYPES 里真实存在的 widget_*）
# 形状 → 建议显示名（按**形状**给，不是按字段名给；建模人员在数据集页改一次就落声明）
KIND_LABEL_HINTS = {
    KIND_TIME_WINDOW: "统计时间窗口",
    KIND_RANGE: "数值区间",
    KIND_CONSTANT: "口径常数",
    KIND_UNKNOWN: "未识别入参",
}

CONTROL_BY_VALUE_TYPE = {
    "date_range": "widget_daterange",
    "date": "widget_date",
    "number": "widget_number",
    "enum": "widget_select",
    "list": "widget_multi_select",
    "boolean": "widget_select",
    "string": "widget_text",
}


# ── 形状推断 ──────────────────────────────────────────────────────

def _split_predicates(sql: str) -> list[str]:
    """按顶层 AND/OR 把正文切成谓词块（括号深度感知）。

    同一块内的占位符属于**同一个条件的不同写法**（如 `${start_time}` 与
    `DATE_SUB(NOW(), INTERVAL ${days} DAY)` 都在">= 起"这一块里），这是把
    "区间端点 + 相对天数"归成一个逻辑窗口的依据，与字段名无关。
    """
    out: list[str] = []
    depth, buf = 0, []
    for tok in re.split(r"(\(|\)|\bAND\b|\bOR\b)", sql or "", flags=re.I):
        if tok == "(":
            depth += 1
            buf.append(tok)
        elif tok == ")":
            depth = max(0, depth - 1)
            buf.append(tok)
        elif tok and depth == 0 and tok.upper() in ("AND", "OR"):
            seg = "".join(buf).strip()
            if PLACEHOLDER_RE.search(seg):
                out.append(seg)
            buf = []
        else:
            buf.append(tok or "")
    seg = "".join(buf).strip()
    if PLACEHOLDER_RE.search(seg):
        out.append(seg)
    return out


def _is_relative_offset(pred: str, seg: str) -> bool:
    """占位符处在算术里 + 该谓词有时间证据 → 它是相对偏移量（窗口长度），不是区间端点。

    只看形状，不看名字：`INTERVAL ${x} DAY`、`${x} * 86400`、`now() - ${x}`、
    `DATEADD(unit, ${x}, …)` 都是同一件事的不同写法，任何新引擎/新字段的新写法
    只要仍是"参与算术"就落到这里，不需要加字段名白名单。
    """
    if not (_ANCHOR_FN_RE.search(pred) or _DATE_FN_RE.search(pred)
            or _DATE_LITERAL_RE.search(pred)
            or _TIME_COLUMN_RE.search(PLACEHOLDER_RE.sub(" ", pred))):
        return False
    for m in re.finditer(seg, pred):
        if _ARITH_TAIL_RE.match(pred[m.end():]) or _ARITH_HEAD_RE.search(pred[:m.start()]):
            return True
    return False


def _role_in_predicate(pred: str, name: str) -> str:
    """单个谓词块内判定该占位符的形状。"""
    pred = _CAST_RE.sub(r"\1", pred)
    seg = re.escape("${" + name + "}")
    # 1) `INTERVAL ${x} DAY`：相对窗口长度（天数/月数…），不是端点
    if re.search(r"\bINTERVAL\s+" + seg + r"\s+" + _TIME_UNIT_RE.pattern, pred, re.I):
        return "window_len"
    # 1b) 其余"参与算术"的时间偏移写法，同样按长度处理（必须在端点判定之前）
    if _is_relative_offset(pred, seg):
        return "window_len"
    # 端点判据要求"运算符与占位符在同一子表达式内"：中间不得出现 AND/OR/逗号。
    # 缺这条时，一个括号内的复合谓词会让 `<= COALESCE(...)` 命中后面 `AND (${x} = '' …)`
    # 里的无关参数（实测把 appmanager/app_code 误判成区间端点）。
    hit_start = re.search(r"(?:>=|>|BETWEEN)\s" + _NEAR + seg, pred, re.I)
    hit_end = re.search(r"(?:<=|<)\s" + _NEAR + seg, pred, re.I)
    if hit_start:
        return "range_start"
    if hit_end:
        return "range_end"
    # 2) `${x} = '' OR col = ${x}` / `NULLIF(${x},'') IS NULL OR …`：空值即不筛
    if re.search(seg + r"\s*=\s*''", pred) or re.search(_EMPTY_GUARD_TMPL % seg, pred, re.I):
        return "optional_eq"
    if re.search(r"=\s*" + seg, pred):
        return "eq"
    # 3) 紧跟 `(`/`,` 之后且后面是 `,`/`)`：函数实参 —— 改它会改算法本身，属口径常数
    for m in re.finditer(seg, pred):      # seg 已 escape，再 escape 一次就永远匹配不上
        before = pred[:m.start()].rstrip()
        after = pred[m.end():].lstrip()
        if (before.endswith(("(", ",")) and after.startswith((",", ")"))
                and not _WRAPPER_FN_RE.search(before)):
            return "func_arg"
    return "other"


def infer_var_roles(bodies: Iterable[str]) -> dict[str, str]:
    """{变量名: 形状角色}：同名变量取所有出现处里优先级最高的形状。"""
    seen: dict[str, str] = {}
    for sql in bodies:
        for pred in _split_predicates(sql or ""):
            for name in dict.fromkeys(PLACEHOLDER_RE.findall(pred)):
                role = _role_in_predicate(pred, name)
                if _ROLE_PRIORITY.index(role) < _ROLE_PRIORITY.index(seen.get(name, "other")):
                    seen[name] = role
    return seen


def _bodies_of(row: dict) -> list[str]:
    """正文集合：主 SQL + 全部方言正文（参数必须在任一正文的形状下都能被识别）。"""
    import json

    out = [str(row.get("sql_query") or "")]
    raw = row.get("sql_bodies")
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = None
    if isinstance(raw, dict):
        out += [str(v) for v in raw.values() if isinstance(v, str)]
    return out


# ── 计划：单数据集 → 逻辑参数清单 ────────────────────────────────

def _is_time_evidence(predicates: list[str]) -> bool:
    """"这对比值是在筛时间"的通用证据（不是入参名白名单）：

      1. 谓词里出现日期函数/日期字面量（`DATE_SUB(NOW(), INTERVAL …)`、`'2026-01-01'`）；
      2. 被比较的**列标识**带时间词根（`stat_date`/`even_time`/`warningTime`/`create_time`…）。

    第 2 条按"任意语言的时间词根"匹配物理列，而不是枚举某几个参数名——新表的新时间列
    照样命中；判不出就走 unknown（不猜控件），由人在数据集页/装配弹窗确认一次。
    """
    for p in predicates:
        if _DATE_FN_RE.search(p) or _DATE_LITERAL_RE.search(p):
            return True
        # 剔掉占位符只看"另一半"：比较对象是带时间词根的列才算证据
        if _TIME_COLUMN_RE.search(PLACEHOLDER_RE.sub(" ", p)):
            return True
    return False


# 被比较列标识的时间词根（列名证据，非参数名证据）
def _predicates_of(bodies: list[str], name: str) -> list[str]:
    return [p for b in bodies for p in _split_predicates(b) if "${" + name + "}" in p]


def _value_type(decl: dict, role: str, is_time: bool) -> str:
    """值类型：显式声明 > 声明类型 > 形状证据 > string。"""
    explicit = str(decl.get("value_type") or "").strip()
    if explicit:
        return explicit
    t = str(decl.get("type") or "").lower()
    if t in ("date", "datetime", "timestamp"):
        return "date"
    if t in ("int", "integer", "long", "bigint", "float", "number", "decimal", "double"):
        return "number"
    if t == "enum":
        return "enum"
    if t in ("list", "array"):
        return "list"
    if t in ("bool", "boolean"):
        return "boolean"
    # 端点声明成 string 是本平台常态（正文与 'YYYY-MM-DD' 文本比较）：
    # 有日期证据才升成 date；没有就照实 string（区间判不出时间 → 不自动建日期控件）
    if role in _PAIR_ROLES and is_time:
        return "date"
    return "string"


_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def logical_plan(variables: Any, bodies: Iterable[str] = ()) -> list[dict]:
    """一个数据集的入参 → 逻辑参数计划（每个逻辑参数带它在**本数据集**里的落法）。

    返回项：
      key/label/kind/value_type/control/options/default/required
      targets: [{var, role, type, value_type, default}]   # binder：逻辑值怎么写进正文变量
      conflicts: [str]        # 本数据集内部分歧（同键多型等）
      referenced: bool        # 正文里是否真的用到（没用到 = 只登记，不建控件）
      pending: bool           # 形状判不出 / 值形态未知 → 要人确认一次，不自动建控件
    """
    decls = parse_variables(variables)
    if not decls:
        return []
    body_list = [b for b in bodies if b]
    roles = infer_var_roles(body_list)
    referenced = set(placeholder_names(" ".join(body_list)))

    # 先算每个变量的 (键名, 角色, 值类型, 时间证据)。键名依赖证据：
    # 只有"比较的是时间"的成对端点才占用规范名 time_window，非时间区间另起键名，
    # 免得 `min_x/max_x` 这类被误当统计窗口（判不出的一律不猜）。
    resolved: dict[str, tuple[str, str, str, bool, Any]] = {}
    for name, decl in decls.items():
        role = roles.get(name, "other")
        is_time = _is_time_evidence(_predicates_of(body_list, name))
        vt = _value_type(decl, role, is_time)
        declared = str(decl.get("dash_key") or "").strip()
        bad_key = bool(declared) and not _KEY_RE.match(declared)
        if declared and not bad_key:
            key = declared
        elif role in _PAIR_ROLES:
            key = TIME_WINDOW_KEY if is_time else "range_" + name
        else:
            key = name
        resolved[name] = (key, role, vt, is_time, bad_key and declared)

    groups: dict[str, dict] = {}
    for name, decl in decls.items():
        key, role, vt, is_time, bad_key = resolved[name]
        g = groups.setdefault(key, {
            "key": key, "label": str(decl.get("label") or "").strip() or key,
            "kind": KIND_UNKNOWN, "value_type": "", "control": "",
            "options": [], "required": bool(decl.get("required")),
            "default": None, "targets": [], "conflicts": [],
            "referenced": False, "pending": False,
            "suggested_label": "", "fallback_days": None, "value_domain": "",
        })
        domain = str(decl.get("value_domain") or "").strip().lower()
        if domain:
            g["value_domain"] = domain
        g["targets"].append({
            "var": name, "role": role, "type": str(decl.get("type") or "string").lower(),
            "value_type": vt, "default": decl.get("default"), "time_evidence": is_time,
            # 数据集页要区分"这条是形状推断出来的"还是"人登记过 dash_key"：
            # 不区分就没法回答"我要不要把它并进另一个参数"
            "dash_key": str(decl.get("dash_key") or "").strip(),
            "label_declared": bool(str(decl.get("label") or "").strip()),
            # 值域（如 'site'）：决定这个入参的值要不要按站点→字段取值翻译，登记在入参声明上
            "value_domain": domain,
        })
        g["referenced"] = g["referenced"] or name in referenced
        if bad_key:
            # 非法键名不拿去拼看板参数（会污染 params 命名空间），退回形状推断并**说明白**
            g["conflicts"].append(
                f"入参 {name} 登记的逻辑参数名「{bad_key}」不合法"
                "（应为字母/下划线开头，只含字母数字下划线），本次按形状推断归属")
        for v in (decl.get("values") or []):
            if str(v) not in g["options"]:
                g["options"].append(str(v))
        if decl.get("required"):
            g["required"] = True

    for g in groups.values():
        targets = g["targets"]
        found = {t["role"] for t in targets}
        types = {t["value_type"] for t in targets}
        # 同一逻辑窗口里的 `days`（int）与 start/end（string/date）类型天然不同，
        # 归组只看形状 + 时间证据，所以这里按 target 逐条判定，不看类型集合是否齐一
        if any(t["role"] in _PAIR_ROLES and t["time_evidence"] for t in targets):
            g["kind"] = KIND_TIME_WINDOW
        elif found & set(_PAIR_ROLES):
            g["kind"] = KIND_RANGE
        elif found == {"func_arg"}:
            g["kind"] = KIND_CONSTANT
        elif "other" in found and not (found - {"other", "func_arg"}):
            g["kind"] = KIND_UNKNOWN
        else:
            g["kind"] = KIND_SCALAR

        if g["kind"] == KIND_TIME_WINDOW:
            g["value_type"] = "date_range"
        elif g["kind"] == KIND_CONSTANT:
            g["value_type"] = next(iter(types)) if types else ""
        elif len(types) == 1:
            g["value_type"] = next(iter(types))
        else:
            g["value_type"] = ""
            g["conflicts"].append("该参数在本数据集内的值类型不一致：" + "、".join(sorted(types)))
        # 控件只在"形状确定 + 值类型可映射 + 正文真的引用了"三者都成立时自动生成
        if g["kind"] in (KIND_SCALAR, KIND_TIME_WINDOW):
            g["control"] = CONTROL_BY_VALUE_TYPE.get(g["value_type"], "")
        else:
            g["control"] = ""
        if not g["referenced"]:
            g["control"] = ""
            g["conflicts"].append("正文未引用该入参（声明未被使用），不生成控件")
        if g["kind"] in (KIND_UNKNOWN, KIND_RANGE) and g["referenced"]:
            g["pending"] = True

        # 默认值一致性：只对**单值型**判分歧（同一个逻辑参数落到多个变量却给了不同默认值，
        # 是真分歧）。时间窗口内部的 `start='' / days=30` 是 binder 的回退写法，不是分歧——
        # 把它当冲突会把 41 个正常数据集全标红，反而淹没真问题。
        defaults = {repr(t["default"]) for t in targets if "default" in t}
        if g["kind"] == KIND_TIME_WINDOW:
            g["default"] = None
            lens = [t["default"] for t in targets
                    if t["role"] == "window_len" and isinstance(t["default"], (int, float))]
            if lens:
                g["fallback_days"] = int(lens[0])
        elif len(defaults) == 1:
            g["default"] = targets[0].get("default")
        elif len(defaults) > 1:
            g["conflicts"].append("同一参数在本数据集内的默认值不一致：" + "、".join(sorted(defaults)))
        if g["label"] == g["key"] and KIND_LABEL_HINTS.get(g["kind"]):
            g["suggested_label"] = KIND_LABEL_HINTS[g["kind"]]
    return list(groups.values())


def param_summary(row: dict) -> list[dict]:
    """给清单/编辑器用的精简视图（不含 SQL、不含物理细节）。"""
    return [{
        "key": g["key"], "label": g["label"], "kind": g["kind"],
        "value_type": g["value_type"], "control": g["control"],
        "options": g["options"], "required": g["required"], "default": g["default"],
        "referenced": g["referenced"], "conflicts": g["conflicts"],
        "pending": g["pending"], "suggested_label": g["suggested_label"],
        "fallback_days": g["fallback_days"],
        # 值域（'site' = 值是站点，取数时按字段映射翻译）：数据集入参页要能看见并改判
        "value_domain": g.get("value_domain") or "",
        "vars": [{"var": t["var"], "role": t["role"], "type": t["type"],
                  "default": t["default"], "dash_key": t["dash_key"],
                  "value_domain": t.get("value_domain") or "",
                  "label_declared": t["label_declared"]} for t in g["targets"]],
    } for g in logical_plan(row.get("variables"), _bodies_of(row))]


# ── 合并：多数据集 → 平台逻辑参数目录（派生视图，不是新实体）────────

def merge_logical_plans(items: Iterable[tuple[Any, Iterable[dict]]]) -> list[dict]:
    """把多个数据集的计划按逻辑参数 key 合并成一份目录，并标出分歧。

    分歧不阻塞、也不自动改数据集口径（那是建模层的权威），只显式列出来给人收敛。
    """
    catalog: dict[str, dict] = {}
    for dataset_id, plan in items:
        for g in plan:
            c = catalog.setdefault(g["key"], {
                "key": g["key"], "label": g["label"], "kind": g["kind"],
                "value_type": g["value_type"], "control": g["control"],
                "options": [], "datasets": [], "vars": [], "conflicts": [],
            })
            c["datasets"].append(int(dataset_id))
            if g["label"] and c["label"] == c["key"] and g["label"] != c["key"]:
                c["label"] = g["label"]
            if g["control"] and not c["control"]:
                c["control"] = g["control"]
            for v in g["targets"]:
                c["vars"].append({**v, "dataset_id": int(dataset_id)})
            for opt in g["options"]:
                if opt not in c["options"]:
                    c["options"].append(opt)
            if c["value_type"] and g["value_type"] and c["value_type"] != g["value_type"]:
                msg = (f"值类型分歧：数据集 {dataset_id} 是 {g['value_type']}，"
                       f"已登记的是 {c['value_type']}")
                if msg not in c["conflicts"]:
                    c["conflicts"].append(msg)
    return list(catalog.values())


# ── 值流转：逻辑参数值 → 数据集 ${} args（唯一翻译点）──────────────

def _as_date(text: Any, *, label: str) -> Any:
    from datetime import datetime

    s = str(text or "").strip().replace("/", "-")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # 静默按默认值出数 = 悄悄换个时间范围，必须报（且只报业务名，不吐内部细节）
    raise ValueError(f"参数「{label}」的取值不是可识别的日期：{text!r}")


def _parse_days(text: str) -> Any:
    """`"30d"` / `"30"` → 30；其他形态返回 None（由上层继续判或显式拒）。"""
    s = str(text or "").strip().lower()
    return int(s.rstrip("d")) if re.fullmatch(r"\d+d?", s) else None


# 逻辑窗口值的已知键（与前端 widget_daterange 输出同形）：只这几个算"给了值/清空"
_WINDOW_KEYS = ("start", "end", "days", "preset")


def _has_clock(raw: Any) -> bool:
    """控件给的值里是否带时分（选了“近 1 小时”这类亚天窗口）。"""
    if isinstance(raw, dict):
        return any(":" in str(raw.get(k) or "") for k in ("start", "end"))
    return ":" in str(raw or "")


def parse_window_value(raw: Any, *, label: str) -> tuple[Any, Any, Any]:
    """逻辑窗口值 → (start, end, days)，三者可为 None，但 start/end 至少给一端。

    只接受看板控件/参数**实际会产生**的形态（对齐 DashboardChart 的 widget_daterange 输出，
    不自造别名，也不给 `start_date/begin` 这类历史名字留后门——它们是数据集的变量名，
    不是逻辑参数的键名，混进来就等于把白名单写回代码）：
      {"start": "2026-11-01", "end": "2026-11-06"}               自定义区间
      {"preset": "30d", "start": …, "end": …}                    预设区间（前端已算好端点）
      {"days": 30} / 30 / "30d"                                  只给相对窗口 → 服务端算端点
      "2026-11-01,2026-11-06"                                    文本形态（看板参数默认值写法）
    """
    from datetime import date, timedelta

    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None, None
    days = None
    start_raw = end_raw = None
    if isinstance(raw, dict):
        start_raw, end_raw = raw.get("start"), raw.get("end")
        days = raw.get("days")
        if days is None and not (start_raw or end_raw):
            days = _parse_days(str(raw.get("preset") or ""))
        if days is None and not (start_raw or end_raw):
            # 控件被清空（start/end 是空串）= 没给值 → 交上层按"不参与检索"处理，
            # 不能报错（看板刚打开就刷一屏“刷新失败”）；
            # 但出现不认识的键不得退成"没给值"（那会让数据集按默认窗口出数，用户看不出来）
            unknown = sorted(k for k in raw if k not in _WINDOW_KEYS)
            if unknown:
                raise ValueError(f"参数「{label}」的取值形态无法识别：{unknown}"
                                 "（日期区间应给 start/end 或 days/preset）")
            return None, None, None
    elif isinstance(raw, str) and "," in raw:
        start_raw, end_raw = [p.strip() for p in raw.split(",", 1)]
    else:
        days = _parse_days(raw) if isinstance(raw, (int, float, str)) else None
    if days is not None:
        d = int(days)
        if d <= 0:
            raise ValueError(f"参数「{label}」的相对窗口需为正整数天数，实得 {days!r}")
        today = date.today()
        return today - timedelta(days=d - 1), today, d
    start = _as_date(start_raw, label=label) if start_raw else None
    end = _as_date(end_raw, label=label) if end_raw else None
    if start and end and start > end:
        raise ValueError(f"参数「{label}」的起始日期晚于结束日期：{start} > {end}")
    if start and end:
        return start, end, (end - start).days + 1     # 含首尾：与 `>= start AND <= end` 同口径
    return start, end, None


def _render_endpoint(day: "Any", target: dict, is_end: bool) -> Any:
    """按目标变量声明的类型给端点值（正文与什么形态比较，由数据集声明决定）。"""
    t = str(target.get("type") or "string").lower()
    if t in ("datetime", "timestamp"):
        return day.strftime("%Y-%m-%d 23:59:59" if is_end else "%Y-%m-%d 00:00:00")
    return day.strftime("%Y-%m-%d")


# 站点值域翻译的"不绑定"信号（如选了聚合态「全部站点」）
_DOMAIN_SKIP = object()


def _translate_domain(value: Any, target: dict, group: dict, notes: list) -> Any:
    """值域=站点的入参：把站点写成它在目标字段上的实际取值（映射配在「站点维护」页）。

    现网实测：新加坡站点（site_code=int）在业务表 `site` 列写 `global`，在日志侧 `region`
    列写 `en`。控件只有一个值（站点），而同一个控件要同时喂用 site 的和用 region 的多个
    数据集，所以翻译只能发生在这一处（唯一翻译点）。

    判据只用登记的 `value_domain`，不看变量叫什么名（换 area/zone 一样能配）。
    映射没配 / 值不在站点清单 / 元库读不到 → 原值下发 + note（不猜值、不静默换成别的站点）。
    选的是聚合态「全部站点」→ 不绑定（= 不给值，按数据集自身口径）：给它编一个具体取值
    等于把"不筛"变成"筛某个站点"，属于造错数。
    """
    domain = str(target.get("value_domain") or group.get("value_domain") or "").strip().lower()
    if domain != VALUE_DOMAIN_SITE or not isinstance(value, str) or not value.strip():
        return value
    from backend.core import site_service      # 延迟导入：站点清单是元库配置，不参与形状推断
    field = str(target.get("var") or "").strip().lower()
    code = site_service.normalize_site_ref(value)
    if not code:
        notes.append(f"「{group['label']}」的值不在站点清单里，本次按原值下发")
        return value
    if site_service.is_aggregate_site(code):
        notes.append(f"「{group['label']}」选了「全部站点」，本次不按 {field} 筛选")
        return _DOMAIN_SKIP
    display = str((site_service.get_site(code) or {}).get("display_name") or code)
    try:
        mapped = site_service.site_field_value(code, field)
    except Exception:      # 元库异常：不改值、不假装映射成功，说出来交人处理
        notes.append(f"站点「{display}」的取值映射没读到，本次按站点代码原样下发")
        return value
    if not mapped:
        notes.append(f"站点「{display}」在字段 {field} 上没配取值映射，"
                     "本次按站点代码原样下发（可能筛不到数）")
        return value
    return mapped


def bound_args(row: dict, values: dict) -> tuple[dict, dict]:
    """看板参数值 → 该数据集正文需要的 `${}` 参数。返回 (args, 诊断)。

    两条绑定通道，优先级从高到低：

    1. **逻辑参数通道**（`values` 的键 = plan 的 key，如 `time_window`）：按 binder 展开
       —— 区间拆端点、相对天数反推、单值直填。这是新装配出来的控件走的道。
    2. **同名兼容通道**（`values` 的键 = 本数据集的变量名，如 `start_time`）：原样绑定。
       存量看板的控件就是按变量名写的，砍掉这条通道会把"今天能生效"变成"静默不生效"，
       那属于制造新缺陷，不是收口。

    诊断字段：
      unbound : 本数据集用到、但两条通道都没给值的入参 → 它正按自身默认值出数，
                必须让前端可见（历史上这里静默走默认，是"看着正常其实没筛"的根因）；
                时间窗口只给到一端时同样上报，并带 `partial`（缺一端=口径不完整）；
      notes   : 派生事实（如"控件给的是起止日期，该数据集正文按相对天数落地"）；
      skipped : 给了值但本数据集不接（别的图可能用），只计数不告警。
    """
    plan = logical_plan(row.get("variables"), _bodies_of(row))
    values = values if isinstance(values, dict) else {}
    args: dict[str, Any] = {}
    notes: list[str] = []
    bound_groups: set[str] = set()
    bound_vars: set[str] = set()

    def _bind(target: dict, value: Any, *, via: str, g: dict) -> None:
        # 值域=站点的入参：写入正文前先按目标字段翻译成该站点的实际取值
        # （新加坡→site 列 global / region 列 en），两条绑定通道同源走这一处
        mapped = _translate_domain(value, target, g, notes)
        if mapped is _DOMAIN_SKIP:
            return
        args[target["var"]] = mapped
        bound_vars.add(target["var"])
        if via == "logical":
            bound_groups.add(g["key"])

    for g in plan:
        if g["kind"] == KIND_CONSTANT or not g["referenced"]:
            continue                       # 口径常数 / 正文没引用：既不绑定也不报未绑定
        key = g["key"]
        raw = values.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        targets = g["targets"]
        if g["kind"] == KIND_TIME_WINDOW:
            start, end, days = parse_window_value(raw, label=g["label"])
            if start is None and end is None and days is None:
                # 控件清空 = 没给值：不绑定也不报错，让正文按自身口径出数（这类数据集普遍写成
                # `${x} = '' OR ...`，空串即不参与筛选），并作为 unbound 上报给前端角标
                continue
            # 亚天窗口（近 1 小时）碰上按天落地端点：起始补 00:00:00、结束补 23:59:59（或只给日期），
            # 筛选会放宽到整天。不能默默换个范围，必须说出口径差异与改法（no-silent-degradation）。
            if _has_clock(raw) and targets:
                notes.append(f"「{g['label']}」给了具体时分，但该数据集正文的时间端点按天落地，"
                             "筛选范围会放宽到整天；要按小时筛需数据集正文改用带时分的比较（或声明相对天数窗口）")
            for t in targets:
                role = t["role"]
                if role == "range_start":
                    if start is None:
                        raise ValueError(
                            f"参数「{g['label']}」只给了结束侧，但数据集正文按起始端点"
                            f"（${{{t['var']}}}）筛选：请给完整区间，不要按默认值凑一个起点。")
                    _bind(t, _render_endpoint(start, t, is_end=False), via="logical", g=g)
                elif role == "range_end":
                    if end is None:
                        raise ValueError(
                            f"参数「{g['label']}」只给了起始侧，但数据集正文按结束端点"
                            f"（${{{t['var']}}}）筛选：请给完整区间，不要按默认值凑一个终点。")
                    _bind(t, _render_endpoint(end, t, is_end=True), via="logical", g=g)
                elif role == "window_len":
                    if days is None:
                        raise ValueError(
                            f"数据集正文按**相对窗口**（${{{t['var']}}} 天数）表达时间范围，"
                            f"但参数「{g['label']}」只给到区间一端，无法换算成整天数。"
                            "请给完整区间或直接给天数，不按默认值凑一个窗口。")
                    _bind(t, int(days), via="logical", g=g)
                    if not (isinstance(raw, dict) and (raw.get("days") or raw.get("preset"))):
                        notes.append(f"「{g['label']}」在该数据集按相对窗口落地（近 {days} 天），"
                                     "粒度由数据集正文决定")
            continue
        if g["kind"] == KIND_RANGE:
            rng = raw if isinstance(raw, dict) else {}
            for t in targets:
                val = rng.get(t["role"].replace("range_", ""))
                if val is not None:
                    _bind(t, val, via="logical", g=g)
            continue
        for t in targets:                  # 单值：一个逻辑值可同时喂归并后的多个变量
            _bind(t, raw, via="logical", g=g)
        if len(targets) > 1:
            notes.append(f"「{g['label']}」在本数据集写入 {len(targets)} 个入参："
                         + "、".join(str(t["var"]) for t in targets))

    # 同名兼容通道：控件写的就是本数据集的变量名（存量看板写法）
    for g in plan:
        if g["kind"] == KIND_CONSTANT or not g["referenced"] or g["key"] in bound_groups:
            continue
        for t in g["targets"]:
            var = t["var"]
            if var in bound_vars or var not in values:
                continue
            val = values[var]
            if val is None or (isinstance(val, str) and not val.strip()):
                continue
            _bind(t, val, via="var_name", g=g)

    unbound: list[dict] = []
    for g in plan:
        if g["kind"] == KIND_CONSTANT or not g["referenced"]:
            continue
        targets = [t for t in g["targets"] if t["role"] in _PAIR_ROLES] or g["targets"]
        missing = [t for t in targets if t["var"] not in bound_vars]
        if len(missing) == len(targets):
            unbound.append({"key": g["key"], "label": g["label"], "kind": g["kind"],
                            "value_type": g["value_type"], "partial": False,
                            "vars": [t["var"] for t in targets]})
            continue
        if missing and g["kind"] == KIND_TIME_WINDOW:
            # 只看**端点**缺不缺：`days` 是正文里的回退写法（`COALESCE(NULLIF(起,''),
            # DATE_SUB(NOW(), INTERVAL days DAY))`），起止都给准时它不参与结果，
            # 把它算作"缺一端"会给存量看板刷一堆假警告（实测）。
            gaps = [t for t in missing if t["role"] in ("range_start", "range_end")]
            if gaps:
                unbound.append({"key": g["key"], "label": g["label"], "kind": g["kind"],
                                "value_type": g["value_type"], "partial": True,
                                "vars": [t["var"] for t in gaps]})

    known = {g["key"] for g in plan} | {t["var"] for g in plan for t in g["targets"]}
    skipped = sum(1 for k in values if k not in known)
    # 同一句提示可能由两条绑定通道各写一次（如站点值域），去重后只留一条（保序）
    return args, {"unbound": unbound, "notes": list(dict.fromkeys(notes)), "skipped": skipped}
