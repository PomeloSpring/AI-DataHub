"""看板筛选参数 → 语义层 intent 的翻译（计划 §四出口 4）。

**这里不解释参数值本身**：日期区间/相对天数的解析复用
`backend.semantics.param_plan.parse_window_value` —— 与数据集 `${}` 绑定、看板参数带
下发端用的是同一个函数。所以「看板上的这段日期」在看板图表上和在看板决策控件上
必然是同一个口径；若在本模块再写一遍 `start/end` 解析，就是第二套参数通道（计划明令
禁止，也是历史上"看着正常其实没筛"那类缺陷的成因）。

三类槽位（控件配置里显式声明，不按参数名猜）：

* ``time_window`` —— 看板日期区间/相对天数 → intent 的 ``time_start/time_end``。
  相对天数（"近 30 天"）也由 param_plan 展开成同一段起止日期，与看板图表拿到
  的区间逐字一致（不在此处换成 ``30d`` 走另一套相对窗口，那会让控件与图表
  隔一天就错位）；
* ``dimension``   —— 看板下拉/多选的值 → ``filters`` 里的一条
  ``SemanticFilter``（``dim`` 必须是维度字典业务名，配置时由建模人员填）；
* ``site``        —— 站点值**不进 filters**（语义层契约明写"站点不是 SQL 维度"），
  只回填请求的 ``site``，仍由 ``site_service`` 在角色授权集内解析。

任何"给了参数但这次用不上"的情况都记进 notes 并原样回给前端，绝不静默按默认口径
出数（no-silent-degradation §1）。
"""

from __future__ import annotations

from typing import Any

from backend.semantics import param_plan

SLOTS = ("time_window", "dimension", "site")


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return [v for v in value if str(v or "").strip() != ""]
    return [value] if str(value or "").strip() != "" else []


def translate(values: dict | None, bindings: list | None) -> dict:
    """看板参数值 + 绑定清单 → intent 附加条件。

    返回 ``{filters, time_start, time_end, site, notes}``；
    ``bindings`` 为空时全部退化成"什么都没改"（决策中心/Chat 出口不经过这里）。
    """
    out: dict[str, Any] = {"filters": [], "time_start": "",
                           "time_end": "", "site": "", "notes": []}
    values = values if isinstance(values, dict) else {}

    for raw_binding in bindings or []:
        if not isinstance(raw_binding, dict):
            continue
        key = str(raw_binding.get("key") or "").strip()
        slot = str(raw_binding.get("slot") or "").strip()
        label = str(raw_binding.get("label") or key)
        if not key:
            continue

        def note(text: str) -> None:
            out["notes"].append(f"参数「{label}」{text}")

        if slot not in SLOTS:
            note(f"绑到了不认识的槽位「{slot or '空'}」，本次未套用")
            continue

        given = values.get(key)
        if given is None or (isinstance(given, str) and not given.strip()):
            note("没有取值，本次按决策树自身口径评估（未套用该筛选）")
            continue

        if slot == "site":
            out["site"] = str(given).strip()
            continue

        if slot == "dimension":
            dim = str(raw_binding.get("dimension") or "").strip()
            if not dim:
                note("要当维度筛，但没填维度业务名，本次未套用")
                continue
            items = _as_list(given)
            if len(items) > 1:
                out["filters"].append({"dim": dim, "op": "in", "values": items})
            else:
                out["filters"].append({"dim": dim, "op": "eq", "value": items[0]})
            continue

        # time_window：形状解析整体交给 param_plan，与数据集入参同一套口径
        try:
            start, end, days = param_plan.parse_window_value(given, label=label)
        except ValueError as exc:
            note(f"的取值不是可识别的时间区间（{exc}），本次未套用")
            continue
        if start is None and end is None and days is None:
            # param_plan 对“真没填”和“形状认不出来”都回三个 None，可要说的话不一样：
            # 后者是配置/上游递错了东西，说成“被清空”会让人以为是自己日期没选。
            if isinstance(given, (dict, list, tuple)):
                note(f"的取值形态认不出来（应为 start/end 或 days/preset，"
                     f"实得 {type(given).__name__}），本次未套用该筛选")
            else:
                note("没有取值，本次按决策树自身口径评估（未套用该筛选）")
            continue
        if not (start and end):
            note("只给到一端，套上去等于换个范围，本次未套用该筛选")
            continue
        out["time_start"], out["time_end"] = start.isoformat(), end.isoformat()

    return out
