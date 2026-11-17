"""字典 aliases 双重编码修复（幂等，默认 dry-run）。

缺陷形态（L2 真实语义评测集实测抓到）
------------------------------------
``adh_metrics.aliases`` / ``adh_dimensions.aliases`` 里出现这种值::

    ["[\\"case数\\"", "\\"total_cases\\"", "\\"口扫案例数\\"", "\\"病例数\\"]",
     "['病例数'", "'病例总量'", "'检查数'", "'检查次数']"]

外层是合法 JSON 数组，但**数组元素本身是被逗号切开的序列化片段**：残留着 ``[``、
``]``、``"``、``'``。planner 按别名精确匹配时永远命中不了 ``病例数``，于是
「案例数量」这条指标明明登记了别名「病例数」，用户问「病例数」却报 unresolved。

**只修编码，不动口径**：本脚本仅在"元素有装饰残留"时改写；与 name_en 同名的
合法别名（如 ``equipment_count``）一概保留 —— 用户真的会这么输入。早期版本顺手
删了这类别名，dry-run 报了出来，已收窄。

成因（必须一起记住，否则修完还会复发）
--------------------------------------
``metrics_service._norm_aliases()`` 对**字符串入参**按逗号切分。当调用方把已经
序列化好的列表字符串（``'["a", "b"]'``）传进来时，它会被切成 ``['["a"', ' "b"]']``
再重新序列化 —— 每多走一次这种调用，脏一层。所以：

* 本脚本只把现存脏数据洗回"元素=干净业务词"，不改 ``_norm_aliases`` 的既有行为
  （改它会影响所有调用方的入参兼容，属独立评审项，已在报告中提出）；
* 写入仍复用 ``_norm_aliases(list)`` 的 list 分支，保证序列化口径唯一。

用法（repo 根目录）:
  venv/bin/python backend/scripts/repair_dict_aliases.py            # dry-run 报告
  venv/bin/python backend/scripts/repair_dict_aliases.py --apply    # 落库

安全性
------
* 逐行打印 before/after，人工看过再 --apply；
* 只删装饰字符与洗后的重复项，**不删任何业务词**；清洗后为空则保留原值并告警
  （宁可不改，也不静默把别名洗成空）；
* 幂等：第二次执行应报 0 处需修。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

TABLES = ("adh_metrics", "adh_dimensions")
#: 只剥外层装饰字符；不拆词、不做大小写归一（业务词区分中英习惯，交给人判断）
_STRIP_CHARS = " \t\r\n[]\"'，,、"


def clean_aliases(raw: str) -> tuple[list[str], bool]:
    """返回 (清洗后的别名列表, 是否存在装饰残留需改写)。

    只修**本缺陷的特征**：元素外层多出的 ``[`` ``]`` ``"`` ``'``，以及因此产生的
    完全重复项（如 牙位 的 ``["牙齿位置"]`` / ``['牙齿位置']`` / ``牙齿位置`` 洗后同源）。

    故意**不动**的两类（上一版于此前误删了合法数据）：
    * 与 name_en 同名的别名（如 ``equipment_count``）—— 用户真的会这么输入，
      删它等于改口径，不属于本缺陷范围；
    * 本来元素干净的行 —— 本脚本只修编码，不做别名治理。

    解析失败或洗完全空时 needs_write=False：看不懂的东西不硬改。
    """
    try:
        arr = json.loads(raw)
    except (TypeError, ValueError):
        return [], False
    if not isinstance(arr, list):
        return [], False

    out: list[str] = []
    dirty = False
    for item in arr:
        if not isinstance(item, str):
            dirty = True            # 非字符串元素本就不该出现在别名列
            continue
        cleaned = item.strip(_STRIP_CHARS).strip()
        if cleaned != item.strip():
            dirty = True
        if not cleaned:
            dirty = True
            continue
        if cleaned not in out:
            out.append(cleaned)
        else:
            dirty = True            # 洗后重名 = 本缺陷的副产物

    if not out:
        return [], False
    return out, dirty


def _looks_dirty(raw: str) -> bool:
    """元素带装饰字符就等于脏（这是本缺陷的可识别特征）。"""
    try:
        arr = json.loads(raw)
    except (TypeError, ValueError):
        return True
    if not isinstance(arr, list):
        return True
    return any(isinstance(x, str) and x != x.strip(_STRIP_CHARS) for x in arr)


def _fetch(conn, table: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, name, name_en, aliases FROM {table} "
                    "WHERE aliases IS NOT NULL AND aliases <> '' AND aliases <> '[]'")
        return list(cur.fetchall())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真正落库（默认只报告）")
    args = ap.parse_args()

    from backend.common.db.metadata_db import get_metadata_conn
    from backend.modules.catalog.services.metrics_service import _norm_aliases

    plans: list[tuple[str, int, str, list[str], str]] = []
    skipped: list[tuple[str, int, str, str]] = []

    conn = get_metadata_conn()
    try:
        for table in TABLES:
            for row in _fetch(conn, table):
                raw = row.get("aliases") or "[]"
                cleaned, dirty = clean_aliases(raw)
                if dirty and cleaned and cleaned != json.loads(raw):
                    plans.append((table, int(row["id"]), str(row.get("name") or ""),
                                  cleaned, raw))
                    continue
                # 看上去脏但脚本不敢改的（解析失败 / 洗完全空）：单独列出交人工，
                # 绝不能归到"已修好"里 —— 否则就是把没处理装成处理了
                if dirty and _looks_dirty(raw):
                    skipped.append((table, int(row["id"]), str(row.get("name") or ""), raw))
    finally:
        conn.close()

    print(f"需修 {len(plans)} 行")
    for table, rid, name, cleaned, raw in plans:
        print(f"\n  {table}#{rid} {name}")
        print(f"    before: {raw[:150]}")
        print(f"    after : {json.dumps(cleaned, ensure_ascii=False)[:150]}")
    if skipped:
        print(f"\n另有 {len(skipped)} 行形态可疑但未被自动改写（交人工判断，不当作已修）：")
        for table, rid, name, raw in skipped:
            print(f"    {table}#{rid} {name}: {raw[:120]}")

    if not args.apply:
        print("\n(dry-run，未落库；确认无误后加 --apply)")
        return 0
    if not plans:
        print("已是干净状态，无需写入。")
        return 0

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            for table, rid, _name, cleaned, _raw in plans:
                # 走既有 list 入参分支，序列化口径与写入口保持一致
                cur.execute(f"UPDATE {table} SET aliases = %s WHERE id = %s",
                            (_norm_aliases(cleaned), rid))
        conn.commit()
    finally:
        conn.close()
    print(f"\n已修复 {len(plans)} 行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
