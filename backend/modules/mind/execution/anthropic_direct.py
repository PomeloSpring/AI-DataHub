"""Anthropic 协议直连通道 — 把「模型中心」登记的供应商配置变成执行层可用的凭据.

## 为什么需要这一层
Qoder CLI 的自定义模型（BYOK）由**组织策略**裁决：企业侧可整体禁用（实测错误
`BYOK is disabled by your enterprise admin`，且 `/model` 的 Custom 目录返回空）。
`claude-agent-sdk` 走 Anthropic 兼容协议直连模型供应商，只吃模型中心里登记的
`base_url + api_key`，**不依赖 Qoder 账号**，因此不受该开关约束。

## 口径（不做静默切换 / 不做静默降级）
- **是否直连由模型中心是否登记该模型决定**：未登记 → 返回 `None`，调用方保持
  Qoder 网关原行为（对存量链路零变化）；
- 登记了但凭据/地址不全 → **抛错**并给出可操作提示，绝不回退成"用网关跑一遍假装是百炼"
  （那会让用户在可观测页看到 Credits 消耗却以为在按 Token 结算）；
- `api_key` **只进子进程环境变量**，不进提示词、不进日志、不进工具返回（护栏 §7）；
- 直连回合**不产生 Qoder Credits**，成本按 Token 由供应商结算：可观测侧必须显式标注
  通道与"成本为估算值"，不得把 CLI 自报的 costUSD 当账单。

## 百炼双协议入口（同一实例、不同 path）
    https://<host>/compatible-mode/v1     OpenAI 兼容（chat/completions、models）
    https://<host>/apps/anthropic         Anthropic 兼容（**仅** /v1/messages，无 /v1/models）
模型中心按运维习惯通常填 OpenAI 兼容地址，因此这里做确定性归一（见 `anthropic_base_url`）。
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

ANTHROPIC_PATH_SUFFIX = "/apps/anthropic"
#: 直连必需的 env（供应商凭据 + 模型映射）；Claude Code 的内部小调用（haiku/子代理）
#: 也必须映射到同一供应商模型，否则会去请求未开放的 Anthropic 官方模型名而失败。
_MODEL_ENV_KEYS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)
#: 关掉与数据面无关的外联（自动升级、非必要遥测），使直连子进程只访问 base_url。
_HYGIENE_ENV = {
    "DISABLE_AUTOUPDATER": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}


class DirectChannelError(ValueError):
    """直连通道配置不可用（对外可读、可操作；不含密钥）。"""


def anthropic_base_url(base_url: str) -> str:
    """把模型中心填的 API 地址归一为 Anthropic 兼容端点（以 /apps/anthropic 结尾）.

    规则确定、可复述，不做猜测式拼接：
    - 已是 ``.../apps/anthropic``      → 原样（去尾斜杠）；
    - 含 ``/compatible-mode``          → 保留 scheme+host，path 换为 /apps/anthropic；
    - 以 ``/v1``（或 /v1/）结尾         → 去掉该段后接 /apps/anthropic；
    - 其他形态                          → 报错，要求管理员显式填写 Anthropic 端点。
    """
    raw = (base_url or "").strip().rstrip("/")
    if not raw:
        raise DirectChannelError("模型中心该条配置的「API 地址」为空，无法建立直连通道")
    if not re.match(r"^https?://", raw):
        raise DirectChannelError(f"API 地址必须是以 http(s):// 开头的完整地址，当前为: {raw!r}")
    if raw.endswith(ANTHROPIC_PATH_SUFFIX):
        return raw
    scheme_host = re.match(r"^(https?://[^/]+)", raw)
    if not scheme_host:
        raise DirectChannelError(f"API 地址无法解析主机名，当前为: {raw!r}")
    host = scheme_host.group(1)
    if "/compatible-mode" in raw:
        return host + ANTHROPIC_PATH_SUFFIX
    if re.search(r"/v\d+/?$", raw):
        return re.sub(r"/v\d+/?$", "", raw).rstrip("/") + ANTHROPIC_PATH_SUFFIX
    raise DirectChannelError(
        f"无法从 API 地址推断 Anthropic 兼容端点: {raw!r}。"
        f"直连后端要求填写以 {ANTHROPIC_PATH_SUFFIX} 结尾的地址"
        f"（百炼同一实例的 Anthropic 入口形如 {host}{ANTHROPIC_PATH_SUFFIX}）。")


def resolve_direct_channel(ref: str = "") -> Optional[dict]:
    """按模型引用判定是否走供应商直连.

    Args:
        ref: 执行层解析出的模型引用（`{provider}/{model_name}`、配置名或 model_name）。
             空值一律返回 None —— 没有显式引用就保持 Qoder 网关原行为，
             绝不把「模型中心里的默认行」当成全局切换开关（那是静默改道）。

    Returns:
        直连通道 dict:
            {model, env, base_url, provider, display_name, channel, context_window}
        未登记（含 ref 为空）→ None。

    Raises:
        DirectChannelError: 登记了但配置不可用（缺 key / 地址形态无法归一）。
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    from backend.modules.mind.execution.llm_resources import resolve_system_llm_model

    row = resolve_system_llm_model(ref)
    if not row:
        return None  # 未登记 → 不是直连模型，交回调用方走网关
    base_url = str(row.get("base_url") or "").strip()
    api_key = str(row.get("api_key") or "").strip()
    model_name = str(row.get("model_name") or "").strip()
    if not (base_url and api_key and model_name):
        missing = [label for label, val in (("API 地址", base_url), ("API Key", api_key),
                                           ("模型", model_name)) if not val]
        raise DirectChannelError(
            f"模型中心「{row.get('name') or ref}」已登记但缺 {('、'.join(missing))}，"
            "无法建立供应商直连；请补全配置，或把该 Harness/AS-BOT 改回 Qoder 网关模型。")

    url = anthropic_base_url(base_url)
    env: dict[str, str] = {
        "ANTHROPIC_BASE_URL": url,
        "ANTHROPIC_AUTH_TOKEN": api_key,
    }
    for key in _MODEL_ENV_KEYS:
        env[key] = model_name
    env.update(_HYGIENE_ENV)
    # 上下文窗口 > 200K 时显式抬高（Claude Code 默认 200K，不因供应商模型更大就自动放宽）
    ctx_window = int(row.get("context_window") or 0)
    if ctx_window > 200_000:
        env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(ctx_window)
    return {
        "model": model_name,
        "env": env,
        "base_url": url,
        "provider": str(row.get("provider") or ""),
        "display_name": str(row.get("name") or model_name),
        "context_window": ctx_window,
        "channel": "anthropic-direct",
    }


def describe_channel(channel: Optional[dict]) -> str:
    """通道的人可读标注（供日志/观测登记；不含密钥）。"""
    if not channel:
        return "qoder-gateway"
    return f"anthropic-direct({channel.get('provider') or 'unknown'}:{channel.get('model') or ''})"


def cli_session_id(raw: str) -> str:
    """把本系统会话 id 归一为 Claude CLI 要求的 canonical UUID（带横线）。

    实测（CLI 2.1.277 + 百炼直连）：
    - `--resume <32 位无横线 hex>`        → 直接报 "not a UUID"；
    - `--resume <合法但不存在的 UUID>`   → "No conversation found"；
    - `--session-id <新 UUID>`            → 以该 id 建会话，后续 `--resume` 同 id 可续。
    所以会话 id 必须归一为 dashed UUID，且**首轮用 session_id、后续用 resume**
    （见 `cli_session_kwargs`）。无法归一（空/非 UUID 形态）返回空串，
    调用方按"无 CLI 会话"处理（不猜一个 id 出来）。
    """
    import uuid

    s = (raw or "").strip()
    if not s:
        return ""
    try:
        return str(uuid.UUID(s))          # 已带横线或 32 位 hex 都能解
    except (ValueError, AttributeError, TypeError):
        return ""


def cli_session_exists(session_id: str, config_dir: str) -> bool:
    """CLI 转录是否已存在（决定本轮用 resume 还是新建 session_id）。

    为什么自己拼路径而不用 SDK 的 `get_session_info`：那个助手从**进程**环境读
    `CLAUDE_CONFIG_DIR`，而本平台的会话隔离是把 config dir 放在每个会话自己的
    runtime 目录里（web 单进程同时服务多个会话，临时改 os.environ 会串台）。
    也不依赖 SDK 私有的 `_sanitize_path`：直接按 `<config>/projects/*/<sid>.jsonl`
    匹配，SDK 改目录命名规则也不会误判（扫不到就是“不存在”，会走 session_id 重建，
    不会错读到别的会话）。
    """
    if not session_id or not config_dir:
        return False
    from pathlib import Path

    projects = Path(config_dir) / "projects"
    if not projects.is_dir():
        return False
    try:
        return next(projects.glob(f"*/{session_id}.jsonl"), None) is not None
    except OSError as exc:  # 目录不可读不属于“不存在”，必须显式报出原因
        logger.warning("检查 CLI 会话转录失败(%s): %s", config_dir, exc)
        raise DirectChannelError(f"无法读取 CLI 会话目录以判断转录是否存在: {exc}") from exc


def cli_session_kwargs(sdk_session_id: str, config_dir: str) -> dict:
    """本轮该传的会话参数：首轮 `session_id`，已有转录则 `resume`.

    返回 {} 表示会话 id 不可归一（不传任何会话参数，由 CLI 自建）。
    `_adh_new_cli_session=True` 时调用方必须把历史带进提示词：CLI 侧此刻还没有转录，
    不这么做就是静默丢多轮上下文。
    """
    sid = cli_session_id(sdk_session_id)
    if not sid:
        return {}
    if cli_session_exists(sid, config_dir):
        return {"resume": sid}
    return {"session_id": sid, "_adh_new_cli_session": True}


def apply_direct_channel(env: dict, home: str, model_ref: str, sdk_session_id: str) -> dict:
    """把直连通道（env）与会话参数（session_id/resume）写进一次构造所需的东西.

    `home` 同时是子进程的 `CLAUDE_CONFIG_DIR`，转录就落在它下头的 `projects/` 里，
    所以判断“会话是否已存在”用的就是它。

    返回要并入 options 的 kwargs：``{model, session_id|resume, strict_mcp_config}``；
    同时就地修改 `env`（子进程环境变量）并返回额外标记：
      - `_adh_new_cli_session`: True 时调用方必须把历史带进提示词（CLI 侧无转录）。

    抽成一个函数的原因：交互链路（secure_sdk）、上下文压缩（context_usage）与无人值守
    定时任务都要建一次 claude options，两处各写一份必然漂移（一处带会话参数、一处不带
    → 压缩/定时任务后丢上下文或撞“Session ID already in use”）。
    """
    channel = resolve_direct_channel(model_ref or "")
    if channel is None:
        raise DirectChannelError(
            f"claude 后端需要模型中心里登记的直连配置，但模型「{model_ref or '未指定'}」未登记"
            "可用的 API 地址与 API Key。请在「模型中心 · 模型配置」补全，"
            "或把该 Harness 改回 qoder 后端（走 Qoder 网关与 Credits）。")
    # 剥除意图 → 显式置空（claude SDK 会继承 os.environ，None 会让 spawn 直接 TypeError）
    for _k, _v in list(env.items()):
        if _v is None:
            env[_k] = ""
    env.update(channel["env"])
    env["CLAUDE_CONFIG_DIR"] = home
    prepare_claude_home(home)
    kwargs: dict[str, Any] = {"model": channel["model"], "strict_mcp_config": True}
    kwargs.update(cli_session_kwargs(sdk_session_id, home))
    new_cli = bool(kwargs.pop("_adh_new_cli_session", False))
    kwargs["_adh_channel"] = channel
    kwargs["_adh_new_cli_session"] = new_cli
    return kwargs


def claude_bundled_cli() -> str:
    """claude-agent-sdk 自带的 Claude Code 二进制路径；没有则空串.

    随 wheel 分发（venv 内 `_bundled/claude`），因此**不依赖宿主机全局安装**，
    这与 qoder 后端把 CLI 钉死在项目 runtime 是同一诉求：避免 PATH 查找短路到
    机器上残留的另一版本。
    """
    try:
        import claude_agent_sdk
    except ImportError:
        return ""
    name = "claude.exe" if os.name == "nt" else "claude"
    for p in (Path(claude_agent_sdk.__file__).parent / "_bundled" / name,
              Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"):
        if p.is_file():
            return str(p)
    return ""


def resolve_claude_cli_path(raw: str = "") -> str:
    """解析 claude CLI 路径：显式配置 > SDK bundled 二进制 > PATH.

    找不到就 fail-loud（提示安装 claude-agent-sdk），不静默回退到 qoder CLI ——
    两个 SDK 的协议与凭据口径不同，回退等于换了条链路还不告诉任何人。
    """
    raw = (raw or "").strip()
    if raw:
        expanded = os.path.expanduser(os.path.expandvars(raw))
        if os.path.isabs(expanded):
            return expanded
        from backend.common.config import PROJECT_ROOT
        if os.sep in expanded or "/" in expanded or expanded.startswith("."):
            return str((PROJECT_ROOT / expanded).resolve())
    bundled = claude_bundled_cli()
    if bundled:
        return bundled
    import shutil
    found = shutil.which("claude")
    if found:
        return found
    raise DirectChannelError(
        "claude Harness 找不到 Claude Code CLI：请安装带 bundled 二进制的 "
        "claude-agent-sdk（pip install claude-agent-sdk），或在 Harness 配置里显式指定 cli_path。")


def prepare_claude_home(config_dir: str | os.PathLike) -> None:
    """为隔离的 CLI HOME 落最小配置，跳过 Anthropic 官方登录引导.

    未写 `hasCompletedOnboarding` 时，Claude Code 会尝试连 api.anthropic.com 做登录
    校验（表现为 "Unable to connect to Anthropic services"），与"只访问供应商端点"
    的直连口径冲突。目录由调用方（会话级隔离 runtime）决定，本函数不猜路径。
    """
    home = Path(config_dir)
    home.mkdir(parents=True, exist_ok=True)
    marker = home / ".claude.json"
    if marker.exists():
        return
    import json
    marker.write_text(json.dumps({"hasCompletedOnboarding": True,
                                  "theme": "system"}), encoding="utf-8")


def redact_secrets(payload: Any) -> Any:
    """防御性脱敏：任何要落日志/出站的结构里抹掉供应商密钥.

    出站守卫（outbound_guard）已按完整键名拦 `api_key`/`authorization` 等，这里补的是
    **直连通道自身的 dict**，避免 `env` 被整体打进日志时把 ANTHROPIC_AUTH_TOKEN 带出去。
    """
    if isinstance(payload, dict):
        out = {}
        for k, v in payload.items():
            if k in ("ANTHROPIC_AUTH_TOKEN", "api_key", "apiKey") or \
                    (isinstance(k, str) and k.upper().endswith("AUTH_TOKEN")):
                out[k] = "***"
            else:
                out[k] = redact_secrets(v)
        return out
    if isinstance(payload, (list, tuple)):
        return [redact_secrets(x) for x in payload]
    return payload
