"""ExecutionLayerManager — 根据数据库配置实例化执行层适配器."""

import logging

from services.datamind.execution.adapters.base import ExecutionLayerAdapter
from services.datamind.execution.models import ExecutionResult, ExecutionTask

logger = logging.getLogger(__name__)


class ExecutionLayerManager:
    """执行层管理器.

    从 adh_execution_layers 加载配置,按 layer_type 构建适配器实例。
    """

    def build_adapter(self, row: dict) -> ExecutionLayerAdapter:
        """根据数据库行构建适配器实例.

        工作空间绑定行携带的 allowed_tools(tools 权限白名单)
        会合并进适配器 config,由各适配器在执行时落实。
        """
        layer_type = row.get("layer_type", "")
        name = row.get("name", "")
        config = dict(row.get("config") or {})
        if row.get("allowed_tools"):
            config["allowed_tools"] = row["allowed_tools"]

        if layer_type == "cli":
            # config.mode=sdk 时走各家 SDK 适配器
            if config.get("mode") == "sdk":
                if config.get("cli_name") == "qoder":
                    from services.datamind.execution.adapters.qoder_sdk_adapter import QoderSDKAdapter
                    return QoderSDKAdapter(name, config)
                if config.get("cli_name") == "opencode":
                    from services.datamind.execution.adapters.opencode_sdk_adapter import OpencodeSDKAdapter
                    return OpencodeSDKAdapter(name, config)
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
