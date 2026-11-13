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
- **记账**：每次加载写 `skill_guide` span（加载了哪个、是否已在上下文、正文字数），
  这是复盘"模型到底有没有读细则"的唯一依据 —— 没有它，A2 就是不可观测的赌博。
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


def _ctx():
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    return get_execution_context()


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


def _resident_skill_names(ctx) -> list[str]:
    """本轮系统提示词里已常驻展开正文的技能名（由 compose_system_prompt 经 skill_state 回写 ctx.extra）。

    读不到（旧链路/未传 skill_state）就当全部未常驻：宁可多发一份正文，也不能把“已展开”误判
    成“未展开”而拒给细则 —— 后者会让模型拿不到作业口径，前者只是多占字数。
    """
    state = (getattr(ctx, "extra", None) or {}).get(RESIDENT_KEY)
    if isinstance(state, dict):
        return [str(n) for n in (state.get("loaded") or []) if str(n).strip()]
    return [str(n) for n in (state or []) if str(n).strip()]


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

    if hit in _resident_skill_names(ctx):
        # 正文本轮已在系统提示词里：再下发一份纯属重复占位，直接告诉模型不必调
        _record_guide(ctx, skill=hit, status="success", started_at=started,
                      already_in_prompt=True)
        return _text({
            "name": hit,
            "already_in_prompt": True,
            "note": "该技能本轮的作业细则已随系统提示词下发，无需重复加载；"
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
    if truncated:
        payload["truncated"] = (f"正文超过 {SKILL_GUIDE_MAX_CHARS} 字符已截断，"
                                "截断部分未下发；如需完整口径请让管理员精简该技能。")
    if missing:
        payload["requires_missing"] = missing
        payload["requires_note"] = (f"该细则提到的工具 {'/'.join(missing)} 本会话未授权，"
                                    "照做会失败；请改用已授权通道并如实说明限制。")
    return _text(payload)


TOOL_SPECS = [
    {"name": "load_skill_guide",
     "description": (
         "按需加载**技能作业细则**（渐进披露）。系统提示词的「已绑定技能」清单只给一句话契约；"
         "需要该域的完整口径（入口分诊顺序、指标/维度速览、分析剧本、自我校验清单）时调本工具取回正文。"
         "参数 name 用清单里的业务名（不是 id）。"
         "边界：返回的是方法细则文本，**不是数据**，不得当作查询结果引用；"
         "只回当前助手已绑定的技能，未绑定会被拒并回可加载清单（不猜名字）；"
         "清单已注明「正文本轮已常驻」的技能不必再调（重复下发只是占位）。"),
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
