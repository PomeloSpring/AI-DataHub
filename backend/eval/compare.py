"""基线对比与发布前检查。

**"回退"的定义**：基线里通过、当前失败或消失的用例。这是唯一会让
``baseline_ok=False`` 的情况——发布前检查只拦真回退，不拦既有失败。

另有两类变化**单独呈现、不直接判失败**，但必须让评审看得见：
    * ``coverage_loss`` —— 基线里通过、当前用例已删除（覆盖度流失，容易被当成
      "删掉难用例让通过率变好看"，所以要显式列出）；
    * ``new`` / ``fixed`` —— 新增用例、或既有失败被修好。

既定决策：评测对比不看 LLM 评分，只看确定性 ``passed``。
"""

from __future__ import annotations


def compare_runs(current: list[dict], baseline: list[dict] | None) -> dict:
    """按 case_key 对齐两次运行的逐用例结果。

    Args:
        current:  本次 ``adh_eval_results`` 行列表
        baseline: 基线行列表；None/空表示无基线（首次运行），此时 baseline_ok=True

    Returns:
        {
          "baseline_ok": bool,
          "regression":  {case_key: {"from": "pass", "to": "fail"/"missing", "reason": ...}},
          "coverage_loss": [case_key, ...],   # 基线通过但用例已删
          "fixed": [case_key, ...],           # 基线失败、本次通过
          "new":   [case_key, ...],           # 本次新增
          "removed": [case_key, ...],         # 本次已删除（含基线失败的）
          "baseline_accuracy": float | None,
          "current_accuracy": float | None,
        }
    """
    cur = {r.get("case_key"): r for r in (current or [])}
    base = {r.get("case_key"): r for r in (baseline or [])} if baseline else {}

    def _acc(rows):
        rows = [r for r in (rows or [])]
        if not rows:
            return None
        return round(sum(1 for r in rows if r.get("passed")) / len(rows), 4)

    regression: dict[str, dict] = {}
    coverage_loss: list[str] = []
    fixed: list[str] = []
    new: list[str] = []
    removed: list[str] = []

    for key, brow in base.items():
        was_pass = bool(brow.get("passed"))
        crow = cur.get(key)
        if crow is None:
            removed.append(key)
            if was_pass:
                coverage_loss.append(key)
                regression[key] = {"from": "pass", "to": "missing",
                                   "reason": "用例已删除（基线时是通过的，属覆盖度流失）"}
            continue
        if was_pass and not bool(crow.get("passed")):
            regression[key] = {"from": "pass", "to": "fail",
                               "reason": crow.get("reason") or "由通过转为失败"}

    for key, crow in cur.items():
        if key not in base:
            new.append(key)
        elif not bool(base[key].get("passed")) and bool(crow.get("passed")):
            fixed.append(key)

    return {
        "baseline_ok": not regression,
        "regression": regression,
        "coverage_loss": sorted(coverage_loss),
        "fixed": sorted(fixed),
        "new": sorted(new),
        "removed": sorted(removed),
        "baseline_accuracy": _acc(baseline),
        "current_accuracy": _acc(current),
    }


def verdict_text(cmp: dict) -> str:
    """给用户看的一句话结论（发布前检查用）。"""
    if cmp.get("baseline_ok"):
        extra = []
        if cmp.get("coverage_loss"):
            extra.append(f"覆盖度流失 {len(cmp['coverage_loss'])} 条（基线通过但用例已删）")
        if cmp.get("new"):
            extra.append(f"新增 {len(cmp['new'])} 条")
        if cmp.get("fixed"):
            extra.append(f"修好 {len(cmp['fixed'])} 条")
        suffix = "；" + "，".join(extra) if extra else ""
        return f"未低于基线{suffix}"
    keys = sorted(cmp.get("regression") or {})
    head = keys[:5]
    more = f" 等 {len(keys)} 条" if len(keys) > 5 else ""
    return f"出现回退：{'、'.join(head)}{more}"
