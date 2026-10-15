"""评测门禁的**绝对下限**口径（与相对回退判定正交）。

为什么必须新增本模块
--------------------
``compare.py`` 只判「基线 pass → 当前 fail」的**相对回退**。一条用例如果长期坏着，
每次运行都与上一次同样坏，于是永远不产生 regression，``baseline_ok`` 恒为 1。
实测事故：retrieval 集 14 条只通过 5 条（35.7%），页面却显示
「各评测集均未低于基线，可以发布」。这是 ``no-silent-degradation`` §1 的正面违反
—— 缺陷被"看起来能用"掩盖了。

本模块补的是**绝对口径**：每个（层 / 能力契约组）配一条 ``adh_eval_gate_policy``，
同时卡两件事：

* ``floor_accuracy`` —— 正确率下限。低于即 ``floor_ok=False``。
* ``min_cases`` —— 计入分母的活跃用例数下界。**不足即不通过**：这一条专门拦
  "把难用例删掉/退役掉来刷通过率"（``compare.py`` 把 ``coverage_loss`` 单列出来
  是为了让流失可见，本条是把流失变成会疼的门禁）。

两个口径**分开存列、不得互相顶替**：把 floor 结果塞进 ``baseline_ok`` 会让
"版本对比"的历史语义漂移，那只是换了一种掩盖。
"""

from __future__ import annotations

#: 能力契约组（横轴）。评测集内容"是否符合要求"由 capability × layer 覆盖矩阵判定，
#: 而不是用例条数 —— 每条用例必须登记其一，未登记视为用例不合法（contract 侧硬校验）。
CAPABILITIES: tuple[str, ...] = (
    "resolution",   # 名字/别名/口径解析落在哪一档
    "calc",         # 计算形状：组合/JOIN/窗口/派生指标/近似去重/百分位/排序分页过滤
    "time",         # 相对时间、日历 vs 滚动优先级、粒度
    "recall",       # 检索召回：关键词/问句 → 命中对象集合（L3 主体）
    "routing",      # 跨源命中、多源歧义、未命中不猜
    "governance",   # RLS 谓词实际施加、敏感列 block/mask、列级 hidden
    "honesty",      # 查不到就说查不到、fuzzy 只回抛候选、不拿别处数据充数
    "leak",         # 出站无 SQL/物理表列/连接信息/IP/报错栈（护栏 §7）
    "clarify",      # 该追问时追问、写操作前必须确认
    "evolution",    # 收编前 fail → 收编后 pass 的可翻转用例
)

CAPABILITY_LABELS: dict[str, str] = {
    "resolution": "口径解析",
    "calc":       "计算形状",
    "time":       "时间语义",
    "recall":     "检索召回",
    "routing":    "跨源路由",
    "governance": "权限治理",
    "honesty":    "诚实性",
    "leak":       "出站脱敏",
    "clarify":    "澄清确认",
    "evolution":  "自进化验证",
}

#: 用例状态三态。语义差别很重要，混用即掩盖：
#:   active        —— 跑，计入分母
#:   known_defect  —— 跑，**计入分母**（继续拖累下限，逼着修，不许藏）
#:   retired       —— 不跑、不计分母，但条数与原因必须常驻可见（防"删用例刷通过率"）
CASE_STATUSES: tuple[str, ...] = ("active", "known_defect", "retired")

#: 计入分母的状态
SCORED_STATUSES: frozenset[str] = frozenset({"active", "known_defect"})


class GateError(ValueError):
    """门禁策略本身不合法（未知层/未知契约/阈值越界）。fail-loud，不静默放行。"""


#: 能证明"链路真的跑了并返回了东西"的正向断言键。
#: 只有否定/上限类断言的用例（如 `max_results:50`、`hit:false`）在**空结果上恒真**：
#: 整条能力死掉时它们会集体通过，正确率好看而无人知晓。
#: 本轮 L3 实测就是这个情形：不传候选表时 rag_source 仍为 graphrag，
#: 但六张清单全空，4 条用例靠"命中 0 条"通过了断言。
POSITIVE_ASSERTIONS: dict[str, frozenset[str]] = {
    "compile": frozenset({"ok", "sql_contains", "resolution_source"}),
    "semantic": frozenset({"allowed", "bound_object", "binding_source", "resolution_source",
                            "sql_contains", "rls_applied", "masked_column_count_min",
                            "unresolved_terms_contains", "warnings_contains"}),
    "retrieval": frozenset({"hit", "contains_any", "contains_all", "min_results",
                             "retrieval_source", "routes", "route_bucket"}),
    "llm": frozenset({"calls_include", "denied_tools", "answer_contains", "max_calls",
                       "write_requires_confirm"}),
}

#: 实测规模数的字段名（适配器负责上报，不放进 sources 归因桶）
_ITEM_COUNT_FIELD = "item_count"


def weak_passes(suite: str, results: list[dict]) -> list[dict]:
    """找出"通过但其实什么都没观察到"的用例。

    不判失败也不计入分母门槛：它是**可诊断提醒**，交给人在评测页把断言改成
    可证伪的形式（或直接把链路修好）。静默忽略它就是把已知问题重新藏回去。

    读 ``item_count`` 独立字段（由 runner 从适配器 observed 带上来），而不是读
    ``sources`` —— sources 是归因分桶，装数字进去会把分桶语义弄脏。
    """
    positive = POSITIVE_ASSERTIONS.get(suite) or frozenset()
    out: list[dict] = []
    for r in results or []:
        if not r.get("passed"):
            continue
        expected = r.get("expected") or {}
        if not expected or (set(expected) & positive):
            continue          # 本身就有正向断言：通过意味着确实观察到了东西
        count = r.get(_ITEM_COUNT_FIELD)
        if count is None:
            continue          # 该层没记录规模数，不下结论（宁缺勿误报）
        try:
            empty = int(count) == 0
        except (TypeError, ValueError):
            continue
        if empty:
            out.append({"case_key": r.get("case_key"),
                        "capability": r.get("capability"),
                        "expected_keys": sorted(expected),
                        "reason": "仅含否定/上限类断言且实测结果为空 —— 本次通过不能证明能力在用"})
    return out


def check_policy(policy: dict) -> dict:
    """校验并归一一条门禁策略。非法即抛 ``GateError``（不得默认成"通过"）。"""
    suite = str(policy.get("suite") or "").strip()
    capability = str(policy.get("capability") or "").strip()
    if not suite:
        raise GateError("门禁策略缺少 suite")
    if capability and capability not in CAPABILITIES:
        raise GateError(f"未知能力契约组 {capability!r}，应为 {list(CAPABILITIES)}")
    try:
        floor = float(policy.get("floor_accuracy"))
    except (TypeError, ValueError) as exc:
        raise GateError(f"floor_accuracy 不是数值: {policy.get('floor_accuracy')!r}") from exc
    if not 0.0 <= floor <= 1.0:
        raise GateError(f"floor_accuracy 越界: {floor}（应在 0~1）")
    try:
        min_cases = int(policy.get("min_cases") or 0)
    except (TypeError, ValueError) as exc:
        raise GateError(f"min_cases 不是整数: {policy.get('min_cases')!r}") from exc
    if min_cases < 0:
        raise GateError(f"min_cases 不得为负: {min_cases}")
    return {"suite": suite, "capability": capability, "floor_accuracy": round(floor, 4),
            "min_cases": min_cases, "enabled": int(policy.get("enabled") or 0) == 1}


def evaluate_buckets(suite: str, buckets: dict[str, dict], policies: list[dict]) -> dict:
    """按**已聚合的分桶结果**判绝对下限（低层入口，precheck 重算时走这里）。

    Args:
        buckets: ``{capability 或 "": {"passed": int, "total": int}}``；
                 ``""`` 代该层整体，其余为能力契约组。

    这里故意**只算不存**：历史运行行里的 ``floor_ok`` 只是当时结论的快照，
    阈值调了、用例改了、或者那一条运行早于本次迁移时，快照就代表不了现在。
    对外结论一律按当前数据+当前策略重算，不得信存列。
    """
    mine = [p for p in (policies or []) if str(p.get("suite") or "") == suite
            and int(p.get("enabled") or 0) == 1]

    breaches: list[dict] = []
    checked: list[dict] = []
    if not mine:
        overall = buckets.get("") or {}
        breaches.append({"suite": suite, "capability": "", "capability_label": "整体",
                         "accuracy": None, "passed": int(overall.get("passed") or 0),
                         "total": int(overall.get("total") or 0),
                         "floor_accuracy": None, "min_cases": None,
                         "reason": "该层未配置任何启用的门禁策略，门禁失效（不得当作通过）"})
        return {"floor_ok": False, "breaches": breaches, "checked": checked}

    for raw in mine:
        p = check_policy(raw)
        bucket = buckets.get(p["capability"]) or {}
        total = int(bucket.get("total") or 0)
        passed = int(bucket.get("passed") or 0)
        acc = round(passed / total, 4) if total else 0.0
        entry = {**p, "capability_label": CAPABILITY_LABELS.get(p["capability"], "整体"),
                 "accuracy": acc, "passed": passed, "total": total}

        if total < p["min_cases"]:
            breaches.append({**entry,
                             "reason": (f"活跃用例 {total} 条，低于覆盖度下界 {p['min_cases']} 条"
                                        f"（删/退役难用例会在此被拦住）")})
            continue
        if acc < p["floor_accuracy"]:
            breaches.append({**entry,
                             "reason": (f"正确率 {acc:.1%}（{passed}/{total}）"
                                        f"低于下限 {p['floor_accuracy']:.0%}")})
            continue
        checked.append(entry)

    return {"floor_ok": not breaches, "breaches": breaches, "checked": checked}


def evaluate_gate(suite: str, scored: list[dict], policies: list[dict]) -> dict:
    """按启用的策略判绝对下限（逐用例结果版，runner 用）。

    Args:
        suite:   被测层（``adh_eval_cases.suite``）
        scored:  **计入分母**的逐用例结果 ``[{case_key, passed, capability}]``；
                 retired 用例必须由调用方剔除后再传入（本模块不做状态判断，
                 避免两处口径）
        policies: ``adh_eval_gate_policy`` 行（可含其它层，本函数自行过滤）

    Returns:
        {
          "floor_ok": bool,
          "breaches": [{suite, capability, capability_label, accuracy, passed,
                        total, floor_accuracy, min_cases, reason}],
          "checked":  [同结构的通过桶, 供页面呈现"判了哪些桶"],
        }

    策略为空（该层未配门禁）时**不视为通过**，而是记一条 breach：
    "未配门禁" 本身就是门禁失效，静默放行等于没有门禁。
    """
    buckets: dict[str, dict] = {"": {"passed": 0, "total": 0}}
    for r in (scored or []):
        cap = str(r.get("capability") or "")
        for key in ("", cap):
            b = buckets.setdefault(key, {"passed": 0, "total": 0})
            b["total"] += 1
            if r.get("passed"):
                b["passed"] += 1
    return evaluate_buckets(suite, buckets, policies)


def gate_verdict(floor_ok: bool, relative_ok: bool) -> str:
    """两口径合成一句结论标识（页面据此出色）。"""
    if not floor_ok and not relative_ok:
        return "both"
    if not floor_ok:
        return "below_floor"
    if not relative_ok:
        return "regression"
    return "ok"


def gate_text(res: dict) -> str:
    """给用户看的绝对下限结论文案（breach 全列，不做截断掩盖）。"""
    if res.get("floor_ok"):
        return "各桶均达绝对下限"
    items = res.get("breaches") or []
    head = "；".join(f"[{i['suite']}/{i['capability'] or '整体'}] {i['reason']}" for i in items[:3])
    more = f" 等 {len(items)} 个桶" if len(items) > 3 else ""
    return f"低于绝对下限：{head}{more}"
