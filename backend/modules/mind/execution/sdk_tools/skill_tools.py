"""skills 工具组 — 技能作业细则的**按需加载口**（渐进披露 progressive disclosure）。

为什么要这个工具：技能正文（SKILL.md body，单份 5~7K 字符）过去由服务端按触发词打分
决定注入哪几份，模型自己没有拉取途径 —— 于是有两个后果：
① 触发词重叠的技能互相抢名额，纯业务问题会白付一份别域的 RUM/大屏细则；
② 名额外的技能只剩一句话 description 常驻，模型想用那份作业口径时**没有任何办法**，
   只能凭浓缩契约作答（口径细节丢失，且这种丢失不可诊断）。

业界口径（Anthropic Agent Skills / MS Agent Framework）是把披露分成三层：
metadata（name+description，常驻）→ instructions（正文，模型判断相关时调 `load_skill`
取回）→ resources（附件，正文里点名才读）。本工具就是第二层，与平台功能动作早已采用的
惰性壳（`list_platform_functions` / `run_platform_function`）同一范式：**按事实自动接入，
不新增第二套授权判断**。

职责边界：
- **只读方法细则，不返回数据行**：正文是"怎么做"的文字，不是查询结果；不经取数执行口，
  也不落任何数据（护栏 §1 约束的是返回数据行的路径）。
- **只回当前助手绑定的技能**：未绑定的剧本属于别的角色/助手，不对外可读；命中不到回
  **可加载清单**（业务名），不猜名字（与报告模板同一口径）。
- **正文已在上下文里就不重复下发**：pinned 指定、单职责角色、无人值守链路仍走常驻展开，
  那些回合调本工具只会拿到"已在上下文"的说明，省掉整份正文的 token。
- **记账**：每次加载写 `skill_guide` span（加载了哪个、是否已在上下文、正文字数），并把
  实际取回的技能名落进回合上下文（`FETCHED_KEY` → `meta.skills_fetched`，与 compose 落下的
  `meta.skills_loaded`=常驻正文、`guide_block_for_tool` 落下的 `meta.skills_attached`=工具边界
  附带一道供会话侧栏显示）。span 只在观测开启时有，回合 meta 恒有：
  复盘"模型到底有没有读细则"两处都能看，不再依赖观测开关。

正文到达模型的三条通道（同一份细则，一个回合只走第一条命中的）：
1. **常驻**（`prompt_composer`）：只给用户 `/` 点名的技能、只绑 1 个技能的单职责角色、无人值守
   `force_all` —— 这些场景不能拿产出口径冒险换字数；
2. **工具边界附带**（`guide_block_for_tool`，由 `compat.guarded` 在首次调用该域工具时挂进结果）：
   多技能角色的常规通道。**实测**（会话 1783069459335/336/337 与 340 的纠正轮）只给一句话契约、
   指望模型自己调 `load_skill_guide`，它一次都没调过 → 口径整轮丢失。工具边界是确定性的：
   用到该域动作就必到，用不到一个字节都不占，且不动系统提示词（提示词缓存也不破）；
3. **模型主动取回**（本工具）：还没动手、但需要先读口径时用（如规划阶段）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Annotated

from backend.modules.mind.execution.sdk_tools.catalog_tools import _text

logger = logging.getLogger(__name__)

# 单次下发的正文上限：技能正文实测 5~7K 字符，留足余量；超限必须**显式标注截断**，
# 不得静默砍尾（模型会以为那份细则就是全文，按残缺口径作业）。
SKILL_GUIDE_MAX_CHARS = 24000

# 与 prompt_composer 的回合决议对接的键（compose 把“本轮已常驻展开的技能名”写回这里）。
# 放在 ctx.extra 而不是 runtime 属性上：ExecutionContext 每回合新建，天然回合级、无跳回合泄漏。
RESIDENT_KEY = "resident_skill_bodies"
# 本回合**模型主动取回**过正文的技能名（与"常驻"是两件事，会话侧栏要分开显示）。
# 为什么另存一份而不是只记 span：观测未开启时 record_span 全链路 no-op（护栏 §9），
# 侧栏与排障就没有事实源；ctx.extra 这份由执行层写进回合 meta，与观测开关无关。
FETCHED_KEY = "fetched_skill_bodies"
# 本回合**随工具结果自动附带**过正文的技能名（工具边界披露：首次调用该域工具时把细则挂在结果里）。
# 这是"正文不常驻提示词"的配套通道：口径在用到那个域的那一刻到达模型，既没调用就不付费。
ATTACHED_KEY = "attached_skill_bodies"
# 本回合已通过工具边界送达的**互斥组**（一份组内细则就够，同组第二份不再附带）。
# 只在进程内使用，不进回合 meta（侧栏要的是"哪份细则到过模型"，组名是实现细节）。
ATTACHED_GROUP_KEY = "attached_skill_groups"


def _ctx():
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    return get_execution_context()


def _record_fetched(ctx, name: str) -> None:
    """把"本回合真取回过这份正文"记进回合上下文（供 secure_sdk 落 meta.skills_fetched）。"""
    extra = getattr(ctx, "extra", None)
    if not isinstance(extra, dict) or not name:
        return
    fetched = extra.setdefault(FETCHED_KEY, [])
    if isinstance(fetched, list) and name not in fetched:
        fetched.append(name)


def _runtime(ctx):
    return (getattr(ctx, "extra", None) or {}).get("secure_runtime")


def _bound_skill_names(ctx) -> list[str]:
    """本会话生效 AS-BOT 绑定的技能名（唯一口径 = as_bots.collect_skill_names）。

    取的是 policy.as_bot 原文，不取 compose 时算出的装载列表 —— 后者是"本轮展开了哪几份
    正文"的决议结果，用它当可加载集合会把"因名额未展开"的技能误判成不可加载。
    """
    from backend.modules.mind.execution import as_bots
    runtime = _runtime(ctx)
    as_bot = getattr(getattr(runtime, "policy", None), "as_bot", None)
    if not isinstance(as_bot, dict):
        return []
    return [str(n) for n in as_bots.collect_skill_names([as_bot]) if str(n).strip()]


def _delivered_skill_names(ctx) -> list[str]:
    """本轮正文已经到达模型的技能（常驻 ∪ 模型取回 ∪ 工具边界附带）—— 同一份不重复下发。"""
    extra = getattr(ctx, "extra", None) or {}
    out: list[str] = []
    for key in (RESIDENT_KEY, FETCHED_KEY, ATTACHED_KEY):
        val = extra.get(key)
        names = val.get("loaded") if isinstance(val, dict) else val
        for n in (names or []):
            text = str(n).strip()
            if text and text not in out:
                out.append(text)
    return out


def _turn_question(ctx) -> str:
    """本回合用户问题（轮次起点由 ``secure_sdk`` 写进 ``ctx.extra["question"]``）。空串 = 不知道问什么。"""
    return str((getattr(ctx, "extra", None) or {}).get("question") or "").strip()


def _scope_hits(skill: dict, question: str) -> list[str]:
    """frontmatter `attach_scope` 的作用域词命中（与 triggers 同机制：子串、大小写不敏感）。"""
    q = str(question or "").strip().lower()
    if not q:
        return []
    return [w for w in (str(x).strip() for x in (skill.get("attach_scope") or []))
            if w and w.lower() in q]


def _match_bound(name: str, bound: list[str]) -> str:
    """在绑定清单里按业务名精确命中（大小写不敏感、允许 display_name）。不猜、不做包含式模糊。"""
    want = str(name or "").strip().lower()
    if not want:
        return ""
    for candidate in bound:
        if str(candidate).strip().lower() == want:
            return str(candidate).strip()
    try:
        from backend.modules.mind.config.skill_loader import load_skill
    except Exception:  # noqa: BLE001 — 读不到加载器就按未命中处理，错误文案里已给清单
        return ""
    for candidate in bound:
        hit = load_skill(candidate) or {}
        for key in (hit.get("display_name"), hit.get("name")):
            if str(key or "").strip().lower() == want:
                return candidate
    return ""


def _record_guide(ctx, *, skill: str, status: str, started_at: float, **payload) -> None:
    """加载记账：观测未开启即 no-op、绝不抛出（护栏 §9），不影响作答主链路。"""
    try:
        from backend.observability.context import record_span
        record_span(kind="skill_guide", name=f"load_skill_guide:{skill}", status=status,
                    duration_ms=int((time.time() - started_at) * 1000),
                    input_text=json.dumps({"name": skill}, ensure_ascii=False)[:500],
                    output_text=json.dumps(payload, ensure_ascii=False)[:2000])
    except Exception as e:  # noqa: BLE001
        logger.debug("[skill_tools] skill_guide 记账跳过: %s", e)


async def load_skill_guide(args):
    """按需取回本助手已绑定技能的作业细则正文（SKILL.md body）。"""
    started = time.time()
    ctx = _ctx()
    want = str(args.get("name") or "").strip()
    bound = _bound_skill_names(ctx)

    if not bound:
        _record_guide(ctx, skill=want or "(空)", status="rejected", started_at=started,
                      reason="no_bound_skill")
        return _text({
            "error": "当前助手没有绑定任何技能，没有可加载的作业细则。"
                     "需要该域的专业口径时，请到「AS-BOT 配置」为该助手绑定技能后重试。",
        }, is_error=True)

    if not want:
        _record_guide(ctx, skill="(空)", status="rejected", started_at=started,
                      reason="empty_name")
        return _text({"error": "需要指明技能业务名；本会话可加载：" + "、".join(bound)},
                     is_error=True)

    hit = _match_bound(want, bound)
    if not hit:
        # 未绑定 = 属于别的助手/角色的剧本，不下发；给可加载清单而不是模糊猜一个
        _record_guide(ctx, skill=want, status="rejected", started_at=started,
                      reason="not_bound", bound=bound)
        return _text({
            "error": f"技能「{want}」未绑定到当前助手，不会加载未绑定的作业细则。"
                     f"可加载：{'、'.join(bound)}",
        }, is_error=True)

    if hit in _delivered_skill_names(ctx):
        # 正文本轮已到达模型（提示词常驻，或已随某个工具结果附带，或此前已取回）：
        # 再下发一份纯属重复占位，直接告诉模型不必调
        _record_guide(ctx, skill=hit, status="success", started_at=started,
                      already_in_prompt=True)
        return _text({
            "name": hit,
            "already_in_prompt": True,
            "note": "该技能本轮的作业细则已随系统提示词或工具结果下发，无需重复加载；"
                    "直接按已注入的口径执行即可。",
        })

    from backend.modules.mind.execution import as_bots
    skill = (as_bots.load_skills([hit]) or [{}])[0]
    body = str(skill.get("system_prompt") or "").strip()
    if not body:
        _record_guide(ctx, skill=hit, status="error", started_at=started, reason="empty_body")
        return _text({
            "error": f"技能「{hit}」已绑定但读不到作业细则正文（文件缺失或 DB 覆盖为空）。"
                     "本轮请按通用分析口径作答，并在回答里如实说明该技能细则不可用。",
        }, is_error=True)

    truncated = len(body) > SKILL_GUIDE_MAX_CHARS
    if truncated:
        body = body[:SKILL_GUIDE_MAX_CHARS]

    # requires_any 未授权的工具要点名提醒：细则里写着的动作本会话调不到，不说明就是诱导。
    # 口径只读 policy.allowed（与逐工具授权同源），按最后一段工具名比（与 compat 的 ceiling 判定同口径），
    # 不列服务器白名单 —— 白名单会随新服务器漂移。
    allowed = set(getattr(getattr(_runtime(ctx), "policy", None), "allowed", ()) or ())
    needs = [str(x).strip() for x in (skill.get("requires_any") or []) if str(x).strip()]
    missing = [n for n in needs if not any(str(a).rsplit("__", 1)[-1] == n for a in allowed)] if allowed else []

    _record_guide(ctx, skill=hit, status="success", started_at=started,
                  guide_chars=len(body), truncated=truncated, missing_requires=missing)
    _record_fetched(ctx, hit)
    payload = {
        "name": hit,
        "display_name": skill.get("display_name") or hit,
        "description": skill.get("description") or "",
        "guide": body,
        "guide_chars": len(str(skill.get("system_prompt") or "").strip()),
        "already_in_prompt": False,
        "note": "以上是作业细则（方法文字），**不是查询结果**：不得把它当数据引用，"
                "也不得在未真正取数时声称已按该技能完成分析。",
    }
    scope = [str(x).strip() for x in (skill.get("attach_scope") or []) if str(x).strip()]
    question = _turn_question(ctx)
    if scope and question and not _scope_hits(skill, question):
        # 显式取回不拒绝（用户/模型可能确实要读全文），但必须点明“本轮问题不在该作用域”，
        # 否则这份细则会被当成这个业务问题的作业口径（历史缺陷：业务取数被观测剧本带偏）。
        payload["scope_note"] = (
            "⚠ 本轮问题未命中该技能的作用域（" + "、".join(scope[:8]) + " 等）："
            "它只管站点网页端的观测数据，案例/订单/客户等内容型业务取数不属于它。"
            "若这其实是业务取数问题，请改用业务数据分析技能，不要把观测数据当业务数字。")
    if truncated:
        payload["truncated"] = (f"正文超过 {SKILL_GUIDE_MAX_CHARS} 字符已截断，"
                                "截断部分未下发；如需完整口径请让管理员精简该技能。")
    if missing:
        payload["requires_missing"] = missing
        payload["requires_note"] = (f"该细则提到的工具 {'/'.join(missing)} 本会话未授权，"
                                    "照做会失败；请改用已授权通道并如实说明限制。")
    return _text(payload)


def _record_attached(ctx, name: str, group: str = "") -> None:
    """把"本回合已随工具结果附带过这份正文"记进回合上下文（供 secure_sdk 落 meta.skills_attached）。"""
    extra = getattr(ctx, "extra", None)
    if not isinstance(extra, dict) or not name:
        return
    attached = extra.setdefault(ATTACHED_KEY, [])
    if isinstance(attached, list) and name not in attached:
        attached.append(name)
    if group:
        groups = extra.setdefault(ATTACHED_GROUP_KEY, [])
        if isinstance(groups, list) and group not in groups:
            groups.append(group)


def _delivered_groups(ctx) -> list[str]:
    """本回合已附带过细则的互斥组名（同组第二份不再下发，理由见 guide_block_for_tool）。"""
    groups = (getattr(ctx, "extra", None) or {}).get(ATTACHED_GROUP_KEY)
    return [str(g) for g in (groups or []) if str(g).strip()]


def guide_block_for_tool(ctx, tool_suffix: str) -> str:
    """工具边界披露：本回合**首次**用到某域的工具时，返回该域技能的作业细则块（无匹配回空串）。

    为什么在这里给而不是写进系统提示词：提示词常驻是"每回合都为可能不发生的任务付费"，
    而实测（会话 1783069459335/336/337 与 340 的纠正轮）证明反过来——只给一句话契约、
    让模型自己调 `load_skill_guide`，它一次都没调过，口径整轮丢失。工具边界是**确定性**的
    第三方：用到该域的动作就必到，用不到就一个字节都不占，且不动系统提示词（提示词缓存也不破）。

    域→技能的绑定沿用 frontmatter `requires_any`（与 `_select_skill_bodies` 的能力门控、
    `load_skill_guide` 的缺工具提醒同一份口径），不另造第二套映射。

    作用域（frontmatter `attach_scope`，可选）是**同一个工具被多个域共用**时的隔离阀：
    声明了它且本轮问题一个词都没命中 → 不附带（如「案例有多少」不该拿到 ARMS 观测口径）。
    这是“问错域”的隔离，不是对失败的降级：正文可由模型显式取回，取回时会被提示作用域不符。
    """
    if not tool_suffix:
        return ""
    bound = _bound_skill_names(ctx)
    if not bound:
        return ""
    delivered = set(_delivered_skill_names(ctx))
    delivered_groups = set(_delivered_groups(ctx))
    from backend.modules.mind.execution import as_bots
    for name in bound:
        if name in delivered:
            continue
        skill = (as_bots.load_skills([name]) or [{}])[0]
        scope = [str(x).strip() for x in (skill.get("attach_scope") or []) if str(x).strip()]
        question = _turn_question(ctx)
        if scope and question and not _scope_hits(skill, question):
            # 作用域外不附带。典型场景：ARMS 可观测与业务数据分析共用 run_semantic_query，
            # 「案例有多少」这类业务取数问题不该拿到观测口径（历史缺陷：业务问题被 RUM 剧本带偏）。
            # 问题为空时不判（宁附带也不静默丢口径）；模型仍可显式 load_skill_guide 取回，
            # 那里会写明“本轮问题不在该技能作用域”，不会把两份口径混成一份。
            logger.debug("[skill_tools] 技能 %s 不在本轮问题作用域内，跳过附带", name)
            continue
        group = str(skill.get("exclusive_group") or "").strip()
        if group and group in delivered_groups:
            # 同域互斥剧本（如「知识问答」与「数据分析」）只发一份：两份口径同时进上下文
            # 就是让答案取决于模型先读到哪条（与 prompt_composer 规则 5 同一理由）。
            continue
        needs = {str(x).strip() for x in (skill.get("requires_any") or []) if str(x).strip()}
        if tool_suffix not in needs:
            continue
        body = str(skill.get("system_prompt") or "").strip()
        if not body:
            # 细则读不到 = 该技能配置有问题，模型有权知道（不静默当"没有这一域"）
            return (f"⚠ 技能「{name}」的作业细则未能随本次结果下发（SKILL.md 正文为空或读取失败）。"
                    "请让管理员核对该技能文件，本轮只能按概述执行。")
        truncated = len(body) > SKILL_GUIDE_MAX_CHARS
        if truncated:
            body = body[:SKILL_GUIDE_MAX_CHARS]
        _record_attached(ctx, name, group=group)
        _record_guide(ctx, skill=name, status="success", started_at=time.time(),
                      attached_via_tool=tool_suffix, guide_chars=len(body), truncated=truncated)
        title = str(skill.get("display_name") or name)
        note = ("以上是该域的作业细则（方法文字，**不是查询结果**，不得当数据引用）。"
                "本轮首次用到该域工具时自动附带，无需再调 load_skill_guide；"
                "请按细则核对刚才这一步的口径，不符合就先修正再答复用户。")
        if truncated:
            note += f"（正文超过 {SKILL_GUIDE_MAX_CHARS} 字符已截断，截断部分未下发）"
        return f"## 技能作业细则 · {title}\n{note}\n\n{body}"
    return ""


def attach_to_result(ctx, tool_suffix: str, result):
    """工具边界披露的**接线点**：`compat.guarded` 在工具成功返回后调用，把该域细则挂到结果尾部。

    读细则失败**不掩藏**（no-silent-degradation §1）：不因此把成功的工具调用判成失败（那是拿
    口径问题掩盖业务结果），但在同一个结果里显式写明"细则没送达"，模型与复盘都能看到。
    """
    try:
        block = guide_block_for_tool(ctx, tool_suffix)
    except Exception:  # noqa: BLE001 — 附件失败不得改写字典外的业务结果
        logger.exception("[skill_tools] 作业细则附带失败: %s", tool_suffix)
        block = (f"⚠ 技能「{tool_suffix}」所属域的作业细则未能随本次结果下发（服务端读取异常，"
                 "已记录日志）。本轮只能按系统提示词里的一句话契约执行；"
                 "需要完整口径时调 `load_skill_guide` 取回，并在答复里如实说明细则不可用。")
    if not block or not isinstance(result, dict):
        return result
    content = result.get("content")
    if not isinstance(content, list):
        content = []
        result["content"] = content
    content.append({"type": "text", "text": block})
    return result


TOOL_SPECS = [
    {"name": "load_skill_guide",
     "description": (
         "按需加载**技能作业细则**。系统提示词的「已绑定技能」清单每项只给一句话契约（正文不常驻）；"
         "本回合首次调用某域的工具时，该域细则会**自动附在工具结果里**，通常你不需要调本工具。"
         "只有在动手之前就想读完整口径（规划剧本、校验清单）时才调，参数 name 用清单里的业务名（不是 id）。"
         "边界：返回的是方法细则文本，**不是数据**，不得当作查询结果引用；"
         "只回当前助手已绑定的技能，未绑定会被拒并回可加载清单（不猜名字）；"
         "本轮已送达过（常驻、随工具结果附带、或此前取回）的技能不必再调（重复下发只是占位）。"),
     "schema": {"name": Annotated[str, "技能业务名（「已绑定技能」清单里的那个名字，不是 id）"]},
     "handler": load_skill_guide},
]


def build_skill_server(backend: str = "qoder", tool_names=None):
    """构建 skills 进程内 MCP server（qoder / claude）。tool_names 给定时按逐工具粒度注册。"""
    from backend.modules.mind.execution.sdk_tools.compat import make_server, make_tool

    specs = list(TOOL_SPECS)
    if tool_names is not None:
        selected = set(tool_names)
        specs = [s for s in specs if s["name"] in selected]
    if not specs:
        return None
    tools = [make_tool(backend, s["name"], s["description"], s["schema"], s["handler"],
                       annotations={"readOnlyHint": True}) for s in specs]
    return make_server(backend, "datahub_skills", tools)
