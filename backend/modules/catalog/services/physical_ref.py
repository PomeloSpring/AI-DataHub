"""带点列引用的**身份裁决**（表限定写法 vs 嵌套 schema 列名本体）—— 唯一实现。

历史缺陷（用户报「物理键绑错了 / view.url 怎么变成 view_url 了」的直接根因）：
把 `col_ref` 一律按 `split/rpartition(".")` 拆成「表.列」，于是
- `metrics_service.resolve_object_property` 把 SLS 嵌套列 `view.url` 拆成
  table=`view` / column=`url` → 写进维度字典的 target_table 是不存在的表 →
  planner 按 `target_table=物理表` 加载不到该维度（**建了维度但取数永远解析不出**）；
- `ontology_service.sync_enums_to_dimensions` 同样错拆 → 枚举/别名联动静默空转；
- `ontology_yaml_import._column_map_from_props` 一律剥前缀 → 部署绑定 column_map
  指向不存在的列。

裁决口径（不靠正则猜）：带点 token 只有两种身份，以**列元数据实存**为唯一依据 ——
- `session.id`  ∈ 该表实存列  → 列名本体含点（嵌套 schema，SLS/ARMS 的形状），原样保留；
- `t_order.amount` 前缀是主表且 `amount` 实存 → 表限定写法，取列名 `amount`；
- 其余（前缀是别表 / 两头都不实存）→ `bad`，交调用方显式暴露，**不猜**。

元数据拿不到时（如跳站部署目标源未同步列元数据，实测 arms-prd-en 零登记行）返回
`unverified` 并保留完整名：宁可留可诊断的限定名，绝不丢路径段（丢了就是错映射）。
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# 引擎内置列：真实可查但不出现在 information_schema（元数据同步不登记）。
# `__time__` = SLS LogStore 内置事件时间（epoch 秒，探针实测 max=1793245034）。
ENGINE_BUILTIN_COLUMNS = frozenset({"__time__"})

_QUOTE_CHARS = '"`[]'


def bare_column(col_ref: str) -> str:
    """去掉登记时可能带的引用符（`"view.url"` / `[view].[url]` 形态）。"""
    s = str(col_ref or "").strip()
    for ch in _QUOTE_CHARS:
        s = s.replace(ch, "")
    return s.strip()


def columns_of_table(datasource_id: int, table: str,
                     cache: Optional[dict] = None) -> set:
    """读表**实存列名**（带点列身份裁决用）。拿不到返回空集（调用方按保守口径处理）。"""
    if not table:
        return set()
    key = (int(datasource_id or 0), table)
    if cache is not None and key in cache:
        return cache[key]
    cols: set = set()
    try:
        from backend.common.db.metadata_db import get_metadata_conn

        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM adh_column_metadata "
                    "WHERE table_name = %s AND is_active = 1 "
                    "AND (datasource_id = %s OR datasource_id = 0)", (table, key[0]))
                cols = {str(r["column_name"]) for r in cur.fetchall() or []}
    except Exception as e:  # noqa: BLE001 — 元数据不可用时不因此误拒，回到保守口径
        logger.warning("[physical-ref] 读 %s 列元数据失败(按保守口径判定): %s", table, e)
        cols = set()
    if cache is not None:
        cache[key] = cols
    return cols


def resolve_column_ref(col_ref: str, primary_table: str = "",
                       known_columns: Optional[set] = None) -> tuple[str, str, str]:
    """把属性登记的列引用解析成 `(表, 列, 判定)`。

    判定取值：
    - ``ok``         实存列（含点是其名字本体）或主表限定写法 —— 可放心落库；
    - ``unverified`` 列元数据缺失，无法裁决 —— 保留完整写法不剥段，调用方需标注；
    - ``bad``        既不是该表实存列、也不是主表限定（幽灵列 / 跨表引用）——
                     调用方必须显式暴露（告警或回抛候选），不得静默绑一个列。
    """
    bare = bare_column(col_ref)
    pt = str(primary_table or "").strip()
    if not bare:
        return pt, "", "unverified"
    if bare in ENGINE_BUILTIN_COLUMNS:
        return pt, bare, "ok"
    known = known_columns or set()
    has_dot = "." in bare
    head, _, rest = bare.partition(".")
    if not known:
        # 无元数据可裁决：仅当限定前缀正好等于主表时才剥段（等价于表限定写法）
        if pt and head.lower() == pt.lower():
            return pt, rest, "unverified"
        if has_dot and not pt:
            # 对象本身没有可登记的物理表（绑定层无行）且无元数据：该对象目前取不到数，
            # 按存量建模的「表.列」写法解读，不改变 MySQL 建模行为（行为兼容位）。
            return head, rest, "unverified"
        return pt, bare, "unverified"
    lower = {str(c).lower() for c in known}
    if bare.lower() in lower:
        return pt, bare, "ok"                       # 列名本体含点（嵌套 schema）
    if has_dot and rest.lower() in lower and (not pt or head.lower() == pt.lower()):
        return (pt or head), rest, "ok"             # 表限定写法（前缀即主表）
    return pt, bare, "bad"


def dim_lookup_candidates(col_ref: str, primary_table: str = "") -> list[tuple[str, str]]:
    """维度字典 `(target_table, target_column)` 的候选定位清单（按优先级）。

    字典行本身就是"这列登记在哪张表的哪个列名上"的事实，因此这里不做正则裁决，
    而是给出候选让调用方按存在性命中：**嵌套列名（本对象物理表 + 完整名）优先**，
    传统「表.列」写法作为兼容位保留（存量 MySQL 建模不受影响）。
    """
    bare = bare_column(col_ref)
    out: list[tuple[str, str]] = []
    if primary_table and bare:
        out.append((primary_table, bare))
    if "." in bare:
        head, _, rest = bare.partition(".")
        out.append((head, rest))
    if not out and bare:
        out.append((primary_table, bare))
    seen: set[tuple[str, str]] = set()
    return [c for c in out if not (c in seen or seen.add(c))]


_TABLE_QUALIFIER = re.compile(r"^[A-Za-z_][\w]*\.[A-Za-z_][\w.]*$")


def looks_table_qualified(col_ref: str) -> bool:
    """形如「表.列」（用于文案里区分"未验证的完整列名"与"表限定写法"）。"""
    return bool(_TABLE_QUALIFIER.match(bare_column(col_ref)))
