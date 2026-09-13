"""ExecutionLayerManager — 根据数据库配置实例化执行层适配器."""

import logging

from services.datamind.execution.adapters.base import ExecutionLayerAdapter
from services.datamind.execution.models import ExecutionResult, ExecutionTask
from services.datamind.execution.tool_catalog import (
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

    - 两者非空:取交集(按 canonical 键比较,保留层级顺序)。
    - 仅一方非空:用该方。
    - 都空:返回 [](不限制)。
    """
    layer = parse_allowed_tools(layer_tools)
    ws = parse_allowed_tools(ws_tools)
    if layer and ws:
        ws_keys = {_tool_key(t) for t in ws}
        merged = [t for t in layer if _tool_key(t) in ws_keys]
        if not merged:
            logger.warning(
                "allowed_tools 交集为空(层级=%s, 工作空间=%s);视为不限制。",
                layer, ws,
            )
        return merged
    return layer or ws


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

        # allowed_tools: 层级 ∩ 工作空间
        merged_tools = _merge_allowed_tools(config.get("allowed_tools"), row.get("allowed_tools"))
        if merged_tools:
            config["allowed_tools"] = merged_tools
        else:
            config.pop("allowed_tools", None)

        # allowed_dirs: 层级默认(全局);工作空间覆盖预留
        dirs = _normalize_dirs(config.get("allowed_dirs"))
        if dirs:
            config["allowed_dirs"] = dirs
        else:
            config.pop("allowed_dirs", None)

        if layer_type == "cli":
            # config.mode=sdk 时走各家 SDK 适配器
            if config.get("mode") == "sdk":
                if config.get("cli_name") == "qoder":
                    from services.datamind.execution.adapters.qoder_sdk_adapter import QoderSDKAdapter
                    return QoderSDKAdapter(name, config)
                if config.get("cli_name") == "claude":
                    from services.datamind.execution.adapters.claude_sdk_adapter import ClaudeSDKAdapter
                    return ClaudeSDKAdapter(name, config)
            from services.datamind.execution.adapters.cli_adapter import CLIProcessAdapter
            return CLIProcessAdapter(name, config)
        if layer_type == "builtin":
            from services.datamind.execution.adapters.builtin_adapter import BuiltInAdapter
            return BuiltInAdapter(name, config)

        raise ValueError(f"不支持的执行层类型: {layer_type}")

    def get_adapter_by_id(self, layer_id: int) -> ExecutionLayerAdapter:
        from services.datamind.execution import service

        row = service.get_layer(layer_id)
        if not row:
            raise KeyError(f"执行层不存在: id={layer_id}")
        return self.build_adapter(row)

    def get_adapter_by_name(self, name: str) -> ExecutionLayerAdapter:
        from services.datamind.execution import service

        row = service.get_layer_by_name(name)
        if not row:
            raise KeyError(f"执行层不存在: name={name}")
        return self.build_adapter(row)

    async def resolve_workspace_layer(self, workspace_id: int) -> dict:
        """解析工作空间的默认执行层配置行;未绑定时回退到内置执行层."""
        from services.datamind.execution import service

        layers = service.get_workspace_layers(workspace_id)
        row = None
        for l in layers:
            if l.get("is_default") and l.get("status") == "active":
                row = l
                break
        if row is None:
            for l in layers:
                if l.get("status") == "active":
                    row = l
                    break
        if row is None:
            row = service.get_layer_by_name("builtin")
        if row is None:
            raise KeyError("没有可用的执行层(内置执行层缺失)")
        return row

    async def get_workspace_adapter(self, workspace_id: int):
        """获取工作空间的默认执行层适配器;未绑定时回退到内置执行层."""
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
