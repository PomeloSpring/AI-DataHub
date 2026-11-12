"""Chat 输入框「/」快捷指令目录（技能 / 定时任务之外的第三类候选）。

为什么单独一个模块：候选清单（给前端 `/` 菜单）与指令语义（注入提示词的作业要求）
必须同源，否则前端能选、后端不认，就成了点了没反应的空承诺。

这里只登记**服务端真有对应能力**的指令，并用 `kind` 分成两类（面板与执行链不得合弄）：
- `kind="directive"`：翻译成提示词里的作业要求，本轮仍走模型。
  如 ``create_scheduled_task`` 对应 function tool ``scheduled_task.create``
  （``perm_code=scheduled:manage`` + ``require_write_perm`` 把关，无码即拒，见
  as-bot-system-waker §3）。本模块只负责把用户意图翻译成作业要求，
  **不做任何授权判断**——授权仍由工具侧唯一裁决点决定，拒绝原因可解释。
- `kind="action"`：**客户端会话动作**（如清空当前会话）。选中就地执行，
  **永不下发给 LLM**：它不是模型手里的工具，当成作业要求下发就是向 LLM 广告
  一个它做不到的事（空承诺），所以 `_assert_slash_command` 对这一类直接拒。

反面清单（仍不登记）：**上下文压缩**。它虽然也是会话动作，但入口已在聊天框右下角
的上下文小徽标（见 frontend ContextUsageChip），且受「任务进行中不得压缩」的执行权
互斥；再开一个 `/` 入口等于同一件事两个入口，两边状态容易飘。
"""

import logging

logger = logging.getLogger(__name__)


#: 指令清单：key 随请求体回传（不进 LLM 视野），label/description 面向用户。
#: kind=action 的项额外带 `action`（客户端动作名），前端据此就地执行而不是挂成本轮指令。
COMMANDS = [
    {
        "key": "create_scheduled_task",
        "label": "创建定时任务",
        "description": "把本轮问题创建为周期执行的 Agent 分析任务（需任务调度权限）",
        "kind": "directive",
        "action": "",
    },
    {
        "key": "clear",
        "label": "清空当前会话",
        "description": "擦掉本会话的历史消息与模型上下文，不新开窗口（不可撤销；会话运行中需先停止）",
        "kind": "action",
        "action": "clear_conversation",
    },
]

_COMMAND_KEYS = {c["key"] for c in COMMANDS}


def list_commands() -> list[dict]:
    """`/` 菜单可选的指令清单（不含 directive 内部文案，但带 kind/action 供前端分流）。"""
    return [{"key": c["key"], "label": c["label"], "description": c["description"],
             "kind": c.get("kind") or "directive", "action": c.get("action") or ""}
            for c in COMMANDS]


def is_known_command(key: str) -> bool:
    return str(key or "") in _COMMAND_KEYS


def command_kind(key: str) -> str:
    """指令类型：directive（可下发作业要求）/ action（客户端动作，不得下发）。"""
    for c in COMMANDS:
        if c["key"] == str(key or ""):
            return c.get("kind") or "directive"
    return ""


#: 指令 → 注入提示词的作业要求。措辞要求先确认关键参数再落库，避免拿猜出来的
#: 周期建出一堆没人认领的任务；权限不足时工具自己回拒绝原因，不在这里承诺能做。
_DIRECTIVES = {
    "create_scheduled_task": (
        "用户通过 `/` 显式指定：本轮结束后把该分析需求创建为定时任务。\n"
        "要求：\n"
        "- 先给出拟创建任务的名称与分析问题，周期未明确时用 ask_user 询问（不要擅自假定 cron）；\n"
        "- 任务不选 AS-BOT、不选数据源：能力与可查范围按创建者角色实时继承，"
        "不要向用户询问这两项（跨站需求让用户写进问题或任务背景里）；\n"
        "- 用户确认后调用 create_scheduled_task 落库（task_config 只带 questions/context），"
        "并把返回的任务名告知用户；\n"
        "- 工具若因权限或配置拒绝，原样把拒绝原因转述给用户，不得改用其他方式冒充已创建。"
    ),
}


def directive_for(key: str) -> str:
    """把指令 key 翻译成提示词作业要求；未知 key 返回空串（由调用方显式暴露）。"""
    return _DIRECTIVES.get(str(key or ""), "")


# ── 快速入口（问答模式）：与 `/` 同源登记，前端选择框与提示词要求一体两面 ────────────
#: 两个模式是**包含关系**（快速问答 ⊂ 深度分析），差异落在两处：
#:   ① 作业深度：快速问答一轮内出结论，不做多轮下钻/归因链；深度分析不限（现状全量）。
#:   ② 副作用面：快速问答不做平台操作（建任务/改本体/发布看板），深度分析按权限码照常裁决。
#: 现状口径（重要）：本切换目前**只决定提示词里的作业要求与展开哪份剧本**，工具侧硬门控
#: （按模式 deny_tools / 收紧工具轮次上限 / 按模式定模型与执行层）尚未接线——不要误以为
#: 选快速问答就真拿不到取数/操作系统工具。能力剖面属于待落地项，不得在此提前承诺。
#: `skill` 指向对应作业剧本（绑定该剧本时强制展开正文；未绑定则只按下面的要求执行
#: 并显式告知用户剧本未绑定，不静默当成已生效）。
#: 清单顺序 = 前端选择框选项顺序（真值源只此一处，前端兜底清单必须同序）：轻任务在前。
#: 默认项 = 清单第一项（快速问答，用户拍板）；两者必须一致，否则“默认选中第二项”会
#: 让输入框初屏与候选顺序互相矛盾。MODE_DEFAULT 同时是服务端对“未传模式”的解释口径。
MODES = [
    {
        "key": "knowledge",
        "label": "快速问答",
        "description": "知识检索 + 查数 + 分析，一轮内给结论；不擅自猜测、不多轮下钻，也不执行平台操作",
        "skill": "knowledge-qa",
    },
    {
        "key": "analysis",
        "label": "深度分析",
        "description": "当前全量能力：多轮取数、下钻归因、跨站对比与图表产出（耗时较长）",
        "skill": "data-analysis",
    },
]
MODE_DEFAULT = "knowledge"

_MODE_SKILL = {m["key"]: m["skill"] for m in MODES}
_MODE_LABEL = {m["key"]: m["label"] for m in MODES}

_MODE_DIRECTIVES = {
    "knowledge": (
        "用户把输入框的问答模式切到「快速问答」，本轮以**一次到位的结论**为目标。\n"
        "要求：\n"
        "- 先检索再作答：概念与口径用 knowledge_search（当前知识来源为 QMind 云端知识库，"
        "本地术语表与图谱作兜底，兜底命中要如实说明）；名称/别名/枚举以 get_metrics、"
        "get_glossary 实时目录为权威；\n"
        "- 需要数字就取数（语义层 run_semantic_query 或受治理 execute_sql），但本轮以一到两次"
        "查询为限，取到就下结论；\n"
        "- 不猜测：口径未登记就直说未登记，不得用常识、系统表或物理元数据充数；\n"
        "- 不做多轮下钻与归因链：用户要继续深挖时，说明可切到「深度分析」；\n"
        "- 本轮不做平台操作（不建定时任务、不改本体/字典、不建或发布看板与大屏）："
        "需要时明确告知用户切到「深度分析」再做。"
    ),
    "analysis": (
        "用户把输入框的问答模式切到「深度分析」，本轮按全量能力作业。\n"
        "要求：\n"
        "- 需要数字/趋势/对比/归因时按语义层或受治理 SQL 取数，可多轮下钻与跨站扇出，并回显所用口径；\n"
        "- 模式切换不限制检索：遇到不认识的术语或口径仍应先查知识库再取数；\n"
        "- 平台写动作仍由菜单与功能权限码裁决（无码即拒，拒因转述给用户），不因选了深度分析而绕过。"
    ),
}


def list_modes() -> list[dict]:
    """快速入口清单（给前端渲染切换按钮；label/description 面向用户）。"""
    return [{"key": m["key"], "label": m["label"], "description": m["description"]}
            for m in MODES]


def is_known_mode(key: str) -> bool:
    return str(key or "") in _MODE_SKILL


def skill_for_mode(key: str) -> str:
    """模式对应的作业剧本名；未知模式返回空串（由调用方按默认模式处理）。"""
    return _MODE_SKILL.get(str(key or ""), "")


def label_for_mode(key: str) -> str:
    """模式的业务名（提示词与错误文案用它，不把内部 key 直接面用户）。"""
    return _MODE_LABEL.get(str(key or ""), str(key or ""))


def mode_directive(key: str) -> str:
    """把模式翻译成提示词作业要求；未知/空模式返回空串。"""
    return _MODE_DIRECTIVES.get(str(key or ""), "")
