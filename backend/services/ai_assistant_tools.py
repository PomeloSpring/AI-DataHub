"""AI Assistant Tools — tool definitions and execution for AI assistant.

Provides tools that AI assistant can use to help users configure the system.
"""

import logging
import json
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


# ── Tool Definitions ──────────────────────────────────────────────────

TOOLS = [
    {
        "name": "navigate_to_page",
        "description": "导航到指定页面。用于打开系统中的特定配置页面。",
        "input_schema": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "string",
                    "description": "目标页面路径，如 /system/scheduled-tasks"
                },
                "params": {
                    "type": "object",
                    "description": "页面参数，如编辑模式下的ID",
                    "additionalProperties": True
                }
            },
            "required": ["page"]
        }
    },
    {
        "name": "open_form",
        "description": "打开表单编辑页面。用于创建或编辑配置。会自动导航到对应页面并打开创建/编辑表单。",
        "input_schema": {
            "type": "object",
            "properties": {
                "form_type": {
                    "type": "string",
                    "enum": ["scheduled_task", "notification_channel", "datasource", "agent", "workflow"],
                    "description": "表单类型"
                },
                "mode": {
                    "type": "string",
                    "enum": ["create", "edit"],
                    "description": "创建或编辑模式"
                },
                "id": {
                    "type": "string",
                    "description": "编辑模式下的记录ID"
                }
            },
            "required": ["form_type", "mode"]
        }
    },
    {
        "name": "click_create_button",
        "description": "点击创建按钮打开新建表单。用于在当前页面打开创建配置的表单。",
        "input_schema": {
            "type": "object",
            "properties": {
                "button_text": {
                    "type": "string",
                    "description": "创建按钮的文本，如'新建数据源'、'添加'等",
                    "default": "新建"
                }
            }
        }
    },
    {
        "name": "fill_form_field",
        "description": "填写表单字段。用于自动填写配置表单。",
        "input_schema": {
            "type": "object",
            "properties": {
                "field_name": {
                    "type": "string",
                    "description": "字段名称或选择器"
                },
                "value": {
                    "type": "string",
                    "description": "字段值"
                }
            },
            "required": ["field_name", "value"]
        }
    },
    {
        "name": "submit_form",
        "description": "提交表单。保存当前配置。",
        "input_schema": {
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "boolean",
                    "description": "是否确认提交",
                    "default": True
                }
            }
        }
    },
    {
        "name": "cancel_form",
        "description": "取消表单编辑。放弃当前修改。",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "click_button",
        "description": "点击页面上的按钮。",
        "input_schema": {
            "type": "object",
            "properties": {
                "button_text": {
                    "type": "string",
                    "description": "按钮文本"
                },
                "button_selector": {
                    "type": "string",
                    "description": "按钮CSS选择器"
                }
            }
        }
    },
    {
        "name": "get_form_state",
        "description": "获取当前表单的状态和字段值。",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "validate_form",
        "description": "验证表单数据是否完整和正确。",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "show_confirmation",
        "description": "显示确认对话框。用于在执行重要操作前确认。",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "确认对话框标题"
                },
                "message": {
                    "type": "string",
                    "description": "确认消息内容"
                },
                "confirm_text": {
                    "type": "string",
                    "description": "确认按钮文本",
                    "default": "确认"
                },
                "cancel_text": {
                    "type": "string",
                    "description": "取消按钮文本",
                    "default": "取消"
                }
            },
            "required": ["title", "message"]
        }
    }
]


# ── Tool Execution ────────────────────────────────────────────────────

class ToolExecutor:
    """工具执行器"""

    def __init__(self):
        """初始化工具执行器"""
        self._pending_actions = []
        self._form_state = {}

    def get_tools(self) -> List[Dict[str, Any]]:
        """获取工具定义列表

        Returns:
            list: 工具定义列表
        """
        return TOOLS

    async def execute_tool(
        self,
        tool_name: str,
        tool_input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """执行工具

        Args:
            tool_name: 工具名称
            tool_input: 工具输入参数

        Returns:
            dict: 执行结果
        """
        logger.info(f"Executing tool: {tool_name} with input: {tool_input}")

        try:
            # 查找工具
            tool_def = None
            for tool in TOOLS:
                if tool["name"] == tool_name:
                    tool_def = tool
                    break

            if not tool_def:
                return {
                    "success": False,
                    "error": f"Unknown tool: {tool_name}"
                }

            # 执行工具
            if tool_name == "navigate_to_page":
                return self._navigate_to_page(tool_input)
            elif tool_name == "open_form":
                return self._open_form(tool_input)
            elif tool_name == "click_create_button":
                return self._click_create_button(tool_input)
            elif tool_name == "fill_form_field":
                return self._fill_form_field(tool_input)
            elif tool_name == "submit_form":
                return self._submit_form(tool_input)
            elif tool_name == "cancel_form":
                return self._cancel_form(tool_input)
            elif tool_name == "click_button":
                return self._click_button(tool_input)
            elif tool_name == "get_form_state":
                return self._get_form_state(tool_input)
            elif tool_name == "validate_form":
                return self._validate_form(tool_input)
            elif tool_name == "show_confirmation":
                return self._show_confirmation(tool_input)
            else:
                return {
                    "success": False,
                    "error": f"Tool not implemented: {tool_name}"
                }

        except Exception as e:
            logger.error(f"Tool execution error: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    def _navigate_to_page(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """导航到指定页面"""
        page = params.get("page")
        page_params = params.get("params", {})

        # 添加到待执行队列
        action = {
            "type": "navigate",
            "page": page,
            "params": page_params
        }
        self._pending_actions.append(action)

        return {
            "success": True,
            "action": action,
            "message": f"正在导航到页面: {page}"
        }

    def _open_form(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """打开表单"""
        form_type = params.get("form_type")
        mode = params.get("mode", "create")
        record_id = params.get("id")

        # 映射表单类型到页面路径和创建按钮文本
        form_config_map = {
            "scheduled_task": {
                "page": "/system/scheduled-tasks",
                "create_button": "新建任务"
            },
            "notification_channel": {
                "page": "/system/notification-channels",
                "create_button": "新建渠道"
            },
            "datasource": {
                "page": "/system/datasources",
                "create_button": "添加数据源"
            },
            "agent": {
                "page": "/system/mcp-agent",
                "create_button": "添加 Agent"
            },
            "workflow": {
                "page": "/system/workflow-editor",
                "create_button": "创建"
            }
        }

        config = form_config_map.get(form_type)
        if not config:
            return {
                "success": False,
                "error": f"Unknown form type: {form_type}"
            }

        page = config["page"]

        # 添加导航动作
        navigate_action = {
            "type": "navigate",
            "page": page,
            "params": {}
        }
        self._pending_actions.append(navigate_action)

        # 如果是创建模式，添加点击创建按钮的动作
        if mode == "create":
            click_action = {
                "type": "click_button",
                "button_text": config["create_button"]
            }
            self._pending_actions.append(click_action)

        # 重置表单状态
        self._form_state = {
            "form_type": form_type,
            "mode": mode,
            "fields": {}
        }

        return {
            "success": True,
            "actions": [navigate_action] + ([{"type": "click_button", "button_text": config["create_button"]}] if mode == "create" else []),
            "message": f"正在打开{form_type}{'新建' if mode == 'create' else '编辑'}表单"
        }

    def _click_create_button(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """点击创建按钮"""
        button_text = params.get("button_text", "新建")

        # 添加到待执行队列
        action = {
            "type": "click_button",
            "button_text": button_text
        }
        self._pending_actions.append(action)

        return {
            "success": True,
            "action": action,
            "message": f"正在点击创建按钮: {button_text}"
        }

    def _fill_form_field(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """填写表单字段"""
        field_name = params.get("field_name")
        value = params.get("value")

        if not field_name:
            return {
                "success": False,
                "error": "field_name is required"
            }

        # 更新表单状态
        self._form_state.setdefault("fields", {})[field_name] = value

        # 添加到待执行队列
        action = {
            "type": "fill_field",
            "field_name": field_name,
            "value": value
        }
        self._pending_actions.append(action)

        return {
            "success": True,
            "action": action,
            "field_name": field_name,
            "value": value,
            "message": f"已设置字段 {field_name} = {value}"
        }

    def _submit_form(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """提交表单"""
        confirm = params.get("confirm", True)

        if not confirm:
            return {
                "success": False,
                "message": "用户取消了提交"
            }

        # 添加到待执行队列
        action = {
            "type": "submit_form",
            "form_state": self._form_state.copy()
        }
        self._pending_actions.append(action)

        return {
            "success": True,
            "action": action,
            "form_state": self._form_state,
            "message": "正在提交表单..."
        }

    def _cancel_form(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """取消表单"""
        # 清空表单状态
        self._form_state = {}

        # 添加到待执行队列
        action = {
            "type": "cancel_form"
        }
        self._pending_actions.append(action)

        return {
            "success": True,
            "action": action,
            "message": "已取消表单编辑"
        }

    def _click_button(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """点击按钮"""
        button_text = params.get("button_text")
        button_selector = params.get("button_selector")

        # 添加到待执行队列
        action = {
            "type": "click_button",
            "button_text": button_text,
            "button_selector": button_selector
        }
        self._pending_actions.append(action)

        return {
            "success": True,
            "action": action,
            "message": f"正在点击按钮: {button_text or button_selector}"
        }

    def _get_form_state(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """获取表单状态"""
        return {
            "success": True,
            "form_state": self._form_state,
            "message": "已获取表单状态"
        }

    def _validate_form(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """验证表单"""
        form_type = self._form_state.get("form_type")
        fields = self._form_state.get("fields", {})

        # 根据表单类型验证必填字段
        required_fields_map = {
            "scheduled_task": ["task_name", "execution_mode", "datasource_id", "cron_expression"],
            "notification_channel": ["channel_name", "channel_type", "webhook_url"],
            "datasource": ["name", "type", "host", "port", "database", "username", "password"],
        }

        required_fields = required_fields_map.get(form_type, [])
        missing_fields = [f for f in required_fields if not fields.get(f)]

        if missing_fields:
            return {
                "success": False,
                "valid": False,
                "missing_fields": missing_fields,
                "message": f"以下必填字段未填写: {', '.join(missing_fields)}"
            }

        return {
            "success": True,
            "valid": True,
            "message": "表单验证通过"
        }

    def _show_confirmation(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """显示确认对话框"""
        title = params.get("title")
        message = params.get("message")
        confirm_text = params.get("confirm_text", "确认")
        cancel_text = params.get("cancel_text", "取消")

        # 添加到待执行队列
        action = {
            "type": "show_confirmation",
            "title": title,
            "message": message,
            "confirm_text": confirm_text,
            "cancel_text": cancel_text
        }
        self._pending_actions.append(action)

        return {
            "success": True,
            "action": action,
            "message": f"确认: {title} - {message}"
        }

    def get_pending_actions(self) -> List[Dict[str, Any]]:
        """获取待执行的操作列表

        Returns:
            list: 操作列表
        """
        return self._pending_actions.copy()

    def clear_pending_actions(self):
        """清空待执行的操作"""
        self._pending_actions.clear()

    def pop_next_action(self) -> Optional[Dict[str, Any]]:
        """获取并移除下一个待执行的操作

        Returns:
            dict: 操作，如果没有则返回None
        """
        if self._pending_actions:
            return self._pending_actions.pop(0)
        return None


# ── Global Instance ───────────────────────────────────────────────────

_tool_executor = None


def get_tool_executor() -> ToolExecutor:
    """获取全局工具执行器实例

    Returns:
        ToolExecutor: 工具执行器实例
    """
    global _tool_executor
    if _tool_executor is None:
        _tool_executor = ToolExecutor()
    return _tool_executor
