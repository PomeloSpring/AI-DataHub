"""ExecutionLayerManager — 根据数据库配置实例化执行层适配器."""

import logging

from backend.modules.mind.execution.adapters.base import ExecutionLayerAdapter
from backend.modules.mind.execution.models import ExecutionResult, ExecutionTask
from backend.modules.mind.execution.tool_catalog import (
    CANONICAL_NAMES,
    LEGACY_ALIASES,
    parse_allowed_tools,
)

logger = logging.getLogger(__name__)


def _tool_key(name: str) -> str:
    """白名单条目 → 用于交集比较的规范键.

    标准名/历史别名归一到 canonical;具体工具名(mcp__srv__tool、execute_sql 等)原样返回。
    """
    n = (name or "").strip()
    if not n:
        return ""
    canonical = LEGACY_ALIASES.get(n)
    if canonical is None and n in CANONICAL_NAMES:
        canonical = n
    return canonical or n


def _merge_allowed_tools(layer_tools, ws_tools) -> list[str]:
    """层级默认白名单 ∩ 工作空间绑定白名单.

    未配置/NULL 表示继承；显式空数组表示全部禁止；两方配置时取交集。
    """
    layer = None if layer_tools is None else parse_allowed_tools(layer_tools)
    ws = None if ws_tools is None else parse_allowed_tools(ws_tools)
    if layer is None:
        return ws
    if ws is None:
        return layer
    ws_keys = {_tool_key(t) for t in ws}
    return [t for t in layer if _tool_key(t) in ws_keys]


def _normalize_dirs(raw) -> list[str]:
    """allowed_dirs → 去空的字符串目录列表(非 list 时容错)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, list):
        return [str(d).strip() for d in raw if str(d or "").strip()]
    return []


class ExecutionLayerManager:
    """执行层管理器.

    从 adh_execution_layers 加载配置,按 layer_type 构建适配器实例。
    """

    def build_adapter(self, row: dict) -> ExecutionLayerAdapter:
        """根据数据库行构建适配器实例.

        权限合并语义(D2):
        - allowed_tools = 层级默认(config.allowed_tools) ∩ 工作空间绑定(row.allowed_tools)。
        - allowed_dirs  = 层级默认(config.allowed_dirs),工作空间级目录覆盖本期不做。
        合并结果写回 config,由各适配器在执行时统一落实。
        """
        layer_type = row.get("layer_type", "")
        name = row.get("name", "")
        config = dict(row.get("config") or {})
        config["_layer_id"] = row.get("id")

        # allowed_tools: 层级 ∩ 工作空间
        merged_tools = _merge_allowed_tools(config.get("allowed_tools"), row.get("allowed_tools"))
        config["allowed_tools"] = merged_tools

        # allowed_dirs: 层级默认(全局);工作空间覆盖预留
        dirs = _normalize_dirs(config.get("allowed_dirs"))
        if dirs:
            config["allowed_dirs"] = dirs
        else:
            config.pop("allowed_dirs", None)

        if layer_type == "cli":
            # mode=sdk 才进入 AS-BOT 安全执行链；后端二选一（工具注册/鉴权/护栏/脱敏同源，
            # 只有模型通道不同）：
            #   qoder  → Qoder 网关 + 账号 Credits（BYOK 受组织策略开关约束）
            #   claude → Anthropic 协议直连供应商（凭据来自模型中心，不依赖 Qoder 账号）
            if config.get("mode") == "sdk" and config.get("cli_name") == "qoder":
                from backend.modules.mind.execution.adapters.qoder_sdk_adapter import QoderSDKAdapter
                return QoderSDKAdapter(name, config)
            if config.get("mode") == "sdk" and config.get("cli_name") == "claude":
                from backend.modules.mind.execution.adapters.claude_sdk_adapter import ClaudeSDKAdapter
                return ClaudeSDKAdapter(name, config)
            raise ValueError(
                "该 CLI Harness 不支持 AS-BOT 安全执行：仅允许 mode=sdk 的 Qoder / Claude Harness。"
                "请在「Harness 管理 → 编辑 → 执行方式」选“SDK 安全执行链”后保存（历史上表单不携带"
                "该字段会把它抹掉，已改为合并保存）。当前配置: "
                f"cli_name={config.get('cli_name')!r}, mode={config.get('mode')!r}")

        raise ValueError(f"不支持的 Harness 类型: {layer_type}")

    def get_adapter_by_id(self, layer_id: int) -> ExecutionLayerAdapter:
        from backend.modules.mind.execution import service

        row = service.get_layer(layer_id)
        if not row:
            raise KeyError(f"Harness 不存在: id={layer_id}")
        return self.build_adapter(row)

    def get_adapter_by_name(self, name: str) -> ExecutionLayerAdapter:
        from backend.modules.mind.execution import service

        row = service.get_layer_by_name(name)
        if not row:
            raise KeyError(f"Harness 不存在: name={name}")
        return self.build_adapter(row)

    async def resolve_workspace_layer(self, workspace_id: int) -> dict:
        """解析当前生效的执行层（默认层口径走 `service.pick_active_layer` 唯一实现）。

        执行层全局生效, 不按工作空间绑定(AS-BOT 可用点按用户角色裁决);
        workspace_id 参数保留兼容签名, 不参与解析。
        以前此处自己写了一份“active → is_default → 首个”，与 service 那份双口径并存；
        现在两边同一实现，默认层只认 `adh_execution_layers.is_default` 一个真值源。
        """
        from backend.modules.mind.execution import service

        row = service.pick_active_layer(workspace_id)
        if row is None:
            raise KeyError("系统无可用的 Harness")
        return row

    async def get_workspace_adapter(self, workspace_id: int):
        """获取工作空间的默认执行层适配器."""
        return self.build_adapter(await self.resolve_workspace_layer(workspace_id))

    async def execute(self, layer_id: int, task: ExecutionTask) -> ExecutionResult:
        """向指定执行层派发任务."""
        adapter = self.get_adapter_by_id(layer_id)
        return await adapter.execute(task)


_manager: ExecutionLayerManager | None = None


def get_execution_layer_manager() -> ExecutionLayerManager:
    """全局单例."""
    global _manager
    if _manager is None:
        _manager = ExecutionLayerManager()
    return _manager
