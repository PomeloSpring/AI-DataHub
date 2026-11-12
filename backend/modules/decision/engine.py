"""决策树确定性求值引擎（无 LLM、无随机、可复现）。

    evaluate_tree(tree_row, ctx) → DecisionOutcome

三段职责：
  ① 取数：把条件引用编译成语义层声明式意图，经 `backend.semantics.execute.execute`
     （护栏 §1 全平台唯一执行口；PolicyContext 由服务端注入可信身份，fail-closed）；
  ② 求值：纯 Python 逐节点比较（比较符白名单在 models.COMPARATORS）；
  ③ 产出：verdict + 命中路径 + 依据（业务名/阈值/实测值）+ 建议引用。

两种求值模式的差别只在①，判断逻辑完全同源（不允许两套口径）：

  * ``full``  —— 自己按指标取数，完整求值（决策中心手动评估 / 定时预警 / 看板控件）；
  * ``turn``  —— **只用本轮已经取到的结果**求值，不补取数（Chat 首答不因此变慢）。
    本轮取不全的指标记进 ``missing_metrics``，verdict 落成 ``partial`` 并说明原因，
    由卡片上的「完整评估」按钮走 full 补齐 —— 这是**显式暴露**，不是降级掩盖。
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.modules.decision import dictionary as dic
from backend.modules.decision.models import (
    DecisionBranch, DecisionCondition, DecisionLeaf, DecisionNode,
    DecisionOutcome, DecisionTreeDoc, collect_condition_names,
)

logger = logging.getLogger(__name__)

MAX_FULL_EVAL_LEAVES = 24   # 一棵树最多发这么多次取数，防止把配置错误的树当压测工具打库


@dataclass
class EvalContext:
    """一次求值的上下文（身份全部服务端注入，绝不从请求体取值 —— 护栏 §2）。"""
    mode: str = "full"                       # full | turn
    user_id: int = 0
    username: str = ""
    role: str = ""
    workspace_id: int = 0
    datasource_id: int = 0
    site_code: str = ""
    # 实例归因：哪个出口发起的（默认按 mode 推，调用方可显式覆盖）
    trigger_type: str = ""
    conversation_id: int = 0
    message_ordinal: int = -1
    # Chat 出口的回合定位键：消息由前端持久化，服务端在 done 时拿不到 seq，
    # 所以回放用 message_uuid（可观测给这条消息生成的稳定键）定位实例。
    message_uuid: str = ""
    # 定时出口的幂等键（墙钟分钟）；Beat 重投/多 beat 实例不会双发预警
    run_key: str = ""
    # turn 模式：本轮结果（列名清单 + 行），由调用方从 done 事件/消息体带入
    turn_columns: list[str] = field(default_factory=list)
    turn_rows: list[Any] = field(default_factory=list)
    # 看板出口（计划 §四出口 4）：看板逻辑参数已由 `decision.params.translate` 经
    # `semantics.param_plan` 翻译成的 intent 附加条件。引擎只**消费**翻译结果，
    # 不在这里再解释一遍参数形状（那会变成第二套参数通道）。
    intent_filters: list[dict] = field(default_factory=list)
    filter_time_start: str = ""
    filter_time_end: str = ""
    param_notes: list[str] = field(default_factory=list)
    dictionary: Optional[dict] = None
    # 单次求值内的取数复用缓存：{(object_key, metric, dim, window): value}
    _fetch_cache: dict = field(default_factory=dict, repr=False)

    def resolved_trigger(self) -> str:
        if self.trigger_type:
            return self.trigger_type
        return "chat_turn" if self.mode == "turn" else "manual"

    def filter_signature(self) -> str:
        """筛选附加条件的稳定指纹（进取数缓存键与评估结果缓存键）。

        少了这一截，同一棵树上「带看板筛选」与「不带筛选」两次求值会命中同一条
        缓存 —— 那正是「数字看着正常其实没筛」的成因。

        本次参数的口径说明（notes）也算一截：notes 不同就是两次语义不同的请求
        （一次“带了筛选但形状认不出来、根本没套用”，一次“确实什么都没传”），
        共用一条缓存条目就会把全量口径的回答发给带筛选的那个人。
        """
        parts = [f"{f.get('dim')}:{f.get('op')}:{f.get('value') or f.get('values')}"
                 for f in (self.intent_filters or [])]
        notes = ";".join(str(n) for n in (self.param_notes or []))
        if notes:
            parts.append("notes=" + hashlib.sha1(notes.encode("utf-8")).hexdigest()[:8])
        return ",".join([*parts, self.filter_time_start or "", self.filter_time_end or ""])


@dataclass
class LeafValue:
    """一个叶子条件的实测值（业务层视图）。"""
    ok: bool = False
    measured: Any = None
    threshold: Any = None
    missing: bool = False            # 本轮没取到（turn 模式）→ partial
    note: str = ""                   # 不可比/无数据等可诊断说明


# ── 入口 ──────────────────────────────────────────────────────────

def evaluate_tree(tree_row: dict, ctx: EvalContext) -> DecisionOutcome:
    """求值一棵已发布的决策树。任何不产出建议的分支都带原因（§1 不静默）。"""
    t0 = time.monotonic()
    outcome = DecisionOutcome(
        tree_key=str(tree_row.get("tree_key") or ""),
        tree_name=str(tree_row.get("name") or ""),
        tree_version=int(tree_row.get("version") or 0),
        site_code=ctx.site_code or "",
        mode=ctx.mode,
    )

    if str(tree_row.get("status") or "") != "active":
        outcome.reason = (f"决策树「{outcome.tree_name}」当前状态是 "
                          f"{tree_row.get('status') or '未知'}，只有已发布（active）的树参与评估")
        outcome.verdict = "skipped"
        return outcome
    if not int(tree_row.get("is_active") or 1):
        outcome.reason = f"决策树「{outcome.tree_name}」已停用"
        outcome.verdict = "skipped"
        return outcome

    site_issue = _site_gate(tree_row, ctx)
    if site_issue:
        outcome.reason = site_issue
        outcome.verdict = "skipped"
        return outcome

    doc, err = load_doc(tree_row.get("tree_doc"))
    if err:
        outcome.reason = err
        outcome.verdict = "skipped"
        logger.error("[decision] tree=%s tree_doc 解析失败: %s", outcome.tree_key, err)
        return outcome

    if ctx.dictionary is None:
        try:
            ctx.dictionary = dic.load_dictionary(ctx.datasource_id)
        except Exception as exc:  # noqa: BLE001 — 字典读不到就无法证明口径，必须显式而非"没建议"
            outcome.reason = f"指标/维度字典读取失败，本次无法核对判断口径（{type(exc).__name__}）"
            outcome.verdict = "skipped"
            return outcome

    state = _WalkState(ctx=ctx)
    matched = _eval_node(doc.root, state, path=[])

    outcome.evidence = state.evidence
    outcome.unresolved = state.unresolved
    outcome.missing_metrics = sorted(state.missing)
    outcome.advice_keys = list(state.advice_keys)
    outcome.hit_path = list(state.hit_labels)

    if state.unresolved:
        # 口径核对不上 = 判断不可信，绝不能"其余条件照样出建议"（那会把半截口径当结论）
        outcome.verdict = "skipped"
        outcome.reason = ("决策树里有条件的指标/维度业务名无法在字典中确认，"
                          f"未确认前不给出建议：{[u['term'] for u in state.unresolved]}")
    elif matched and state.advice_keys:
        outcome.verdict = "hit"
    elif state.unjudged and not state.judged and not state.missing:
        # 一个条件都没判成（取数被拒 / 无数据源授权 / 值不可比较）= **“没评估”**，
        # 不是“评了但没命中”。把前者说成后者就是静默降级（no-silent-degradation §1）：
        # 一个长期取不到数的树会永远显示“无风险”。
        outcome.verdict = "skipped"
        outcome.reason = (f"决策树的 {len(state.unjudged)} 个条件本次一个都没能判定："
                          f"{'；'.join(state.unjudged)}。这不是“没有风险”，"
                          f"请先解决取数或数据源授权")
    elif state.missing or state.unjudged:
        # 部分判得成、部分判不成：只能给 partial，并点名是哪几条拖了后腿
        parts = []
        if state.missing:
            parts.append(f"本轮取数未覆盖这些指标：{sorted(state.missing)}")
        if state.unjudged:
            parts.append(f"这些条件本次没能判定：{'；'.join(state.unjudged)}")
        outcome.verdict = "partial"
        outcome.reason = "；".join(parts) + "；当前结论只依据已取到的部分，未完整评估"
    else:
        outcome.verdict = "no_hit"
        outcome.reason = "已按决策树逐条判定，没有条件成立"

    outcome.elapsed_ms = int((time.monotonic() - t0) * 1000)
    # 看板参数的“没套用/已改口径”必须说出口（即使已命中）：否则人看到的建议像是全量口径。
    # 结构化一份（前端据此在 hit 上也展示）+ 进 reason 一份（实例落库后还能回放出口径）。
    outcome.scope_notes = list(ctx.param_notes)
    if ctx.param_notes:
        notes = "；".join(str(n) for n in ctx.param_notes)
        outcome.reason = f"{outcome.reason} 筛选参数：{notes}" if outcome.reason else f"筛选参数：{notes}"
    return outcome


def load_doc(raw: Any) -> tuple[Optional[DecisionTreeDoc], str]:
    """tree_doc（dict/JSON 字符串）→ 契约模型；失败返回可读原因，不抛给调用方猜。"""
    import json

    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (TypeError, ValueError) as exc:
            return None, f"tree_doc 不是合法 JSON（{exc}）"
    if not isinstance(data, dict):
        return None, "tree_doc 缺失或不是对象"
    try:
        return DecisionTreeDoc.model_validate(data), ""
    except Exception as exc:  # noqa: BLE001 — pydantic 校验错误文案已可诊断，原样回传
        return None, f"tree_doc 不符合契约：{exc}"


# ── 站点门（派生视图，不新建第二套裁决）────────────────────────────

def _site_gate(tree_row: dict, ctx: EvalContext) -> str:
    """`apply_sites` 空 = 沿用角色授权站点派生；非空 = 只在所列站点评估。

    这里只读"本次生效站点"（由调用方按会话/授权派生后传入），不自己判权限，
    也不新建站点授权表（waker-datasource-domain §1）。
    """
    import json

    raw = tree_row.get("apply_sites")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return "apply_sites 不是合法 JSON，无法判定这棵树在哪个站点生效"
    rules = raw or []
    if not rules:
        return ""
    allowed = set()
    for r in rules if isinstance(rules, list) else []:
        code = str((r or {}).get("site_code") if isinstance(r, dict) else r or "").strip()
        if code:
            allowed.add(code)
    if not allowed:
        return ""
    site = (ctx.site_code or "").strip()
    if not site:
        return (f"决策树「{tree_row.get('name') or tree_row.get('tree_key')}」只在站点 "
                f"{sorted(allowed)} 生效，本次会话没有确定生效站点，未评估")
    if site not in allowed:
        return (f"决策树「{tree_row.get('name') or tree_row.get('tree_key')}」只在站点 "
                f"{sorted(allowed)} 生效，本次站点是「{site}」，未评估")
    return ""


# ── 节点求值 ──────────────────────────────────────────────────────

@dataclass
class _WalkState:
    ctx: EvalContext
    evidence: list[dict] = field(default_factory=list)
    advice_keys: list[str] = field(default_factory=list)
    hit_labels: list[str] = field(default_factory=list)
    missing: set = field(default_factory=set)
    unresolved: list[dict] = field(default_factory=list)
    # judged = 真正比对过的条件数；unjudged = 没能判定的条件原因（取数被拒/不可比）。
    # 两者一起决定树级 verdict：_no_hit_ 只能建立在“真的判过”的基础上。
    judged: int = 0
    unjudged: list[str] = field(default_factory=list)
    fetches: int = 0

    def note_unjudged(self, text: str) -> None:
        """同一个原因反复命中时只说一次（十个叶子都因为同一个源被拒≠十条信息）。"""
        if text and text not in self.unjudged:
            self.unjudged.append(text)


def _eval_node(node: DecisionNode, st: _WalkState, path: list[str]) -> bool:
    label_path = path + [node.label]
    if isinstance(node, DecisionLeaf):
        return _eval_leaf(node, st, label_path)
    results = [_eval_node(child, st, label_path) for child in node.children]
    return any(results) if node.op == "any" else all(results)


def _eval_leaf(leaf: DecisionLeaf, st: _WalkState, path: list[str]) -> bool:
    """求一个叶子；无论命中与否都留一条可诊断依据（评估过程本身要能被复盘）。"""
    cond = leaf.when
    lv = _resolve_condition(cond, st)
    row = {
        "metric": cond.metric,
        "comparator": cond.comparator,
        "threshold": lv.threshold,
        "measured": lv.measured,
        "unit": cond.unit,
        "dimension": cond.dimension,
        "time_window": cond.time_window,
        "label": leaf.label,
        "path": path,
    }
    if lv.note:
        row["note"] = lv.note
    st.evidence.append(row)

    if lv.missing:
        st.missing.add(cond.metric)
        if cond.threshold_ref:
            st.missing.add(cond.threshold_ref)
        return False
    if not lv.ok:
        # 值没拿到（取数被拒/结果里没这一列）与"拿到了但不成立"是两回事：
        # 前者要显式写进依据，否则一个长期失败的取数会伪装成"风险未出现"。
        note = lv.note or "实测值未能取得，本条未判定"
        row["note"] = note
        row["judged"] = False
        st.note_unjudged(f"「{leaf.label}」{cond.metric}：{note}")
        return False

    matched, why = _compare(lv.measured, cond.comparator, lv.threshold)
    if why:
        # 不可比不是"条件不成立"，而是"这个条件本条判不了"：必须留在依据里，
        # 否则一个拼错单位的阈值会长期伪装成"风险未出现"（§1 不静默）。
        row["note"] = f"{row.get('note', '')} {why}".strip()
        row["judged"] = False
        st.note_unjudged(f"「{leaf.label}」{cond.metric}：{why}")
        return False
    st.judged += 1
    if matched:
        # 面包屑 = 根到叶的路径，但同一层分支名只说一次：`op=any` 下两个叶子都
        # 命中时，直接 extend 会写成「根/叶一/根/叶二」，读起来像绕回了根。
        for label in path:
            if label not in st.hit_labels:
                st.hit_labels.append(label)
        if leaf.advice_key not in st.advice_keys:
            st.advice_keys.append(leaf.advice_key)
    return matched


def _compare(measured: Any, comparator: str, threshold: Any) -> tuple[bool, str]:
    """白名单比较，回 ``(是否成立, 不可比原因)``。

    不可比时返回 (False, 原因)而不是无声判 False：前者能被复盘，后者会伪装成
    "风险没出现"。类型归一只动百分比/千分位这类展示写法，不猜内容。
    """
    try:
        if comparator == "=":
            return _scalar_eq(measured, threshold), ""
        if comparator == "!=":
            return not _scalar_eq(measured, threshold), ""
        if comparator == "between":
            lo, hi = _pair(threshold)
            m = _num(measured)
            if m is None:
                return False, f"实测值「{measured}」不是数值，无法与区间比较"
            return lo <= m <= hi, ""
        if comparator == "in":
            return any(_scalar_eq(measured, v) for v in (threshold or [])), ""
        if comparator == "not_in":
            return not any(_scalar_eq(measured, v) for v in (threshold or [])), ""
        m, t = _num(measured), _num(threshold)
        if m is None and t is None:
            return False, f"实测值「{measured}」与阈值都不可比较"
        if m is None:
            return False, f"实测值「{measured}」不是数值，无法比较"
        if t is None:
            return False, f"阈值「{threshold}」不是数值，无法比较"
        return {">": m > t, ">=": m >= t, "<": m < t, "<=": m <= t}[comparator], ""
    except (TypeError, ValueError, IndexError, KeyError) as exc:
        return False, f"条件写法无法求值（{exc}）"


def _scalar_eq(a: Any, b: Any) -> bool:
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return abs(na - nb) < 1e-12
    return str(a).strip() == str(b).strip()


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s.endswith("%"):
        s = s[:-1]
    try:
        return float(s)
    except ValueError:
        return None


def _pair(threshold: Any) -> tuple:
    if not isinstance(threshold, (list, tuple)) or len(threshold) != 2:
        raise ValueError("between 需要 [下界, 上界] 两个阈值")
    lo, hi = _num(threshold[0]), _num(threshold[1])
    if lo is None or hi is None:
        raise ValueError("between 阈值不是数值")
    return (lo, hi) if lo <= hi else (hi, lo)


# ── 取数 / 本轮结果取值 ───────────────────────────────────────────

def _resolve_condition(cond: DecisionCondition, st: _WalkState) -> LeafValue:
    """把条件折叠成 (实测值, 阈值)；full 模式在此取数，turn 模式从本轮结果取。"""
    threshold = cond.value
    if cond.threshold_ref:
        ref = _obtain(cond.threshold_ref, "", cond, st)
        if ref.missing:
            return LeafValue(missing=True, threshold=None, note=f"阈值指标「{cond.threshold_ref}」本轮未取到")
        if not ref.ok:
            return LeafValue(note=f"阈值指标「{cond.threshold_ref}」不可用：{ref.note}")
        threshold = ref.measured

    got = _obtain(cond.metric, cond.dimension, cond, st)
    if got.missing:
        return LeafValue(missing=True, threshold=threshold, note=got.note)
    if not got.ok:
        return LeafValue(threshold=threshold, note=got.note)
    return LeafValue(ok=True, measured=got.measured, threshold=threshold, note=got.note)


def _obtain(metric: str, dimension: str, cond: DecisionCondition, st: _WalkState) -> LeafValue:
    """按模式取一个（指标[, 维度切片]）的值，并把业务名先在字典核对。"""
    ctx = st.ctx
    hit, miss = dic.resolve(metric, "metric", ctx.dictionary or {})
    if miss:
        st.unresolved.append({"term": metric, "type": "metric",
                              "reason": miss.reason, "candidates": miss.candidates})
        return LeafValue(note=f"指标业务名「{metric}」未在字典确认")
    if not hit.object_key:
        st.unresolved.append({"term": metric, "type": "metric", "reason": "no_object", "candidates": []})
        return LeafValue(note=f"指标「{hit.name}」的字典行没有归属对象，口径不可追溯")

    dim_hit = None
    if dimension:
        dhit, dmiss = dic.resolve(dimension, "dimension", ctx.dictionary or {})
        if dmiss:
            st.unresolved.append({"term": dimension, "type": "dimension",
                                  "reason": dmiss.reason, "candidates": dmiss.candidates})
            return LeafValue(note=f"维度业务名「{dimension}」未在字典确认")
        dim_hit = dhit

    if ctx.mode == "turn":
        return _from_turn(hit, dim_hit, cond, st)

    key = (hit.object_key, hit.name, dim_hit.name if dim_hit else "",
           cond.time_window or "", cond.dimension_value if cond.dimension_value is not None else "",
           ctx.filter_signature())
    if key in ctx._fetch_cache:
        return LeafValue(ok=True, measured=ctx._fetch_cache[key])
    if st.fetches >= MAX_FULL_EVAL_LEAVES:
        return LeafValue(missing=True, note="单棵树取数量已达上限，未继续评估")
    st.fetches += 1
    value, err = _fetch_via_semantic(hit.object_key, hit.name, dim_hit, cond, ctx)
    if err:
        return LeafValue(note=err)
    ctx._fetch_cache[key] = value
    return LeafValue(ok=True, measured=value)


def _note_once(ctx: EvalContext, text: str) -> None:
    """同一句口径说明只记一次（多个叶子共享同一个看板区间，逐条重复会没顶）。"""
    if text not in ctx.param_notes:
        ctx.param_notes.append(text)


def _fetch_via_semantic(object_key: str, metric: str, dim_hit, cond: DecisionCondition,
                        ctx: EvalContext) -> tuple[Any, str]:
    """经语义层唯一执行口取一个指标值（可按维度切片取其中一行）。

    失败**不回落**：SemanticError 的原因已由执行口做过对外脱敏，这里原样带出；
    绝不在这里补一次裸连或换个源重试。
    """
    from backend.semantics.contract import SemanticError
    from backend.semantics.execute import PolicyContext, execute
    from backend.semantics.models import SemanticFilter, SemanticQuery

    query = SemanticQuery(
        object=object_key,
        metrics=[metric],
        dimensions=[dim_hit.name] if dim_hit else [],
        time_window=cond.time_window,
        limit=500 if dim_hit else 1,
    )
    if cond.time_column:
        query.time_column = cond.time_column
    if ctx.site_code:
        query.site = ctx.site_code
    if ctx.datasource_id:
        query.datasource_id = int(ctx.datasource_id)
    if ctx.workspace_id:
        query.workspace_id = int(ctx.workspace_id)

    # 看板出口附加条件（已在上游经 param_plan 翻译，这里只搬运不解释）：
    # 日期区间/相对窗口与树自身条件窗口同时存在时，**以看板显式范围为准**。
    # 保留相对窗口 = 控件拖了日期而结果不变（“看着正常其实没筛”）；这个取舍必须
    # 进 notes 说出口，不默换。
    for f in (ctx.intent_filters or []):
        if isinstance(f, dict) and f.get("dim"):
            query.filters.append(SemanticFilter(**f))
    if ctx.filter_time_start and ctx.filter_time_end:
        if cond.time_window:
            _note_once(ctx, f"决策树条件原本按相对窗口 {cond.time_window}，本次按看板筛选的日期区间评估")
        query.time_window = None
        query.time_start, query.time_end = ctx.filter_time_start, ctx.filter_time_end

    policy = PolicyContext(
        user_id=int(ctx.user_id or 0),
        username=ctx.username or "",
        role=ctx.role or "",
        workspace_id=int(ctx.workspace_id or 0),
        datasource_id=int(ctx.datasource_id or 0),
    )
    try:
        result = execute(query, policy)
    except SemanticError as exc:
        # 对外文案已脱敏；原始细节只在执行口日志里（护栏 §7）
        return None, f"取数被语义层拒绝（{exc.blocked_at or 'execute'}）：{exc.message}"
    except Exception as exc:  # noqa: BLE001 — 非语义错误也不能把堆栈透出去
        logger.exception("[decision] 取数异常 metric=%s", metric)
        return None, f"取数异常（{type(exc).__name__}），本次未评估"

    return _pick(result, metric, dim_hit.name if dim_hit else "", cond.dimension_value)


def _pick(result, metric: str, dim_name: str, dim_value: Any) -> tuple[Any, str]:
    """从结果里挑出要比较的那个值（列名匹配口径与 turn 一致，见 _column_index）。"""
    names = [str(getattr(c, "name", "") or "") for c in (result.columns or [])]
    headers = [str(h) for h in names]
    m_idx = _column_index(headers, metric)
    if m_idx < 0:
        return None, f"取数结果里没有列「{metric}」，无法比较"
    if not (result.rows or []):
        return None, "取数结果为空，无法判定该条件"
    if not dim_name:
        return result.rows[0][m_idx], ""
    d_idx = _column_index(headers, dim_name)
    if d_idx < 0:
        return None, f"取数结果里没有维度列「{dim_name}」"
    wanted = None if dim_value in (None, "") else str(dim_value).strip()
    for row in result.rows:
        cell = str(row[d_idx]).strip()
        if wanted is None or cell == wanted:
            return row[m_idx], ""
    return None, f"维度「{dim_name}」下没有取值「{dim_value}」这一行，无法判定"


def _from_turn(hit, dim_hit, cond: DecisionCondition, st: _WalkState) -> LeafValue:
    """turn 模式：从本轮已取到的结果里读值，读不到就如实记 missing（不补取数）。"""
    ctx = st.ctx
    headers = [str(c) for c in (ctx.turn_columns or [])]
    if not headers:
        return LeafValue(missing=True, note="本轮没有数据结果")

    m_idx = _column_index(headers, hit.name)
    if m_idx < 0:
        m_idx = _column_index(headers, cond.metric)
    if m_idx < 0:
        return LeafValue(missing=True, note=f"本轮结果里没有「{hit.name}」")

    rows = ctx.turn_rows or []
    if not rows:
        return LeafValue(missing=True, note="本轮结果没有数据行")

    cell = _cell_of(rows[0], m_idx)
    if dim_hit:
        d_idx = _column_index(headers, dim_hit.name)
        if d_idx < 0:
            return LeafValue(missing=True, note=f"本轮结果里没有维度「{dim_hit.name}」")
        wanted = None if cond.dimension_value in (None, "") else str(cond.dimension_value).strip()
        hit_row = None
        for r in rows:
            if wanted is None or str(_cell_of(r, d_idx)).strip() == wanted:
                hit_row = r
                break
        if hit_row is None:
            return LeafValue(missing=True, note=f"本轮结果里没有维度「{dim_hit.name}」={cond.dimension_value} 这一行")
        cell = _cell_of(hit_row, m_idx)
    elif len(rows) > 1:
        # 本轮是多行结果，而条件没有维度切片 —— 拿第一行当"整体口径"是猜，必须说明
        return LeafValue(ok=True, measured=cell,
                         note="本轮结果是多维明细，条件未指定维度取值，已按首行预览值判定（建议用完整评估核对）")
    return LeafValue(ok=True, measured=cell)


def _cell_of(row: Any, idx: int) -> Any:
    """行形状兼容 dict（会话消息里常是 records）与 list（语义层标准形状）。"""
    if isinstance(row, dict):
        keys = list(row.keys())
        if idx < len(keys):
            return row[keys[idx]]
        return None
    try:
        return row[idx]
    except (IndexError, TypeError):
        return None


def _column_index(headers: list[str], name: str) -> int:
    """列名匹配：先精确（归一），再"业务名(单位)"这类带后缀的形式。

    刻意不做包含式模糊：语义层投影别名本来就是业务名，模糊匹配会把
    「案例数」错配到「近7天案例数」—— 错配出来的建议比没有建议更坏。
    """
    target = dic.norm(name)
    if not target:
        return -1
    for i, h in enumerate(headers):
        if dic._norm(h) == target:
            return i
    return -1


def tree_required_names(tree_row: dict) -> tuple[list[str], list[str]]:
    """从 tree_doc 重算 (指标业务名, 维度业务名) —— 派生列的唯一算法。"""
    doc, err = load_doc(tree_row.get("tree_doc"))
    if err or doc is None:
        return [], []
    return collect_condition_names(doc)
