-- Execution Layer Migration v3
-- 新增 claude 执行层(claude-agent-sdk,mode=sdk)
-- 运行时为 SDK 内置的自包含二进制,无需外部 CLI / Node

INSERT IGNORE INTO adh_execution_layers (name, display_name, description, layer_type, config, status)
VALUES (
    'claude',
    'Claude Agent SDK',
    'Anthropic 官方 Agent SDK(Claude Code 内核):内置文件/命令/网页工具、子代理、会话恢复与上下文自动压缩,平台工具(execute_sql/元数据/语义)经进程内 MCP 注入',
    'cli',
    JSON_OBJECT('mode', 'sdk', 'cli_name', 'claude', 'sdk_tools', 'all'),
    'active'
);
