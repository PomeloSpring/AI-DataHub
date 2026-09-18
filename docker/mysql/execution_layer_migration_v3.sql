-- Execution Layer Migration v3
-- 全面切换 Qoder 执行层:
--   1) 停用并移除历史 claude 执行层(及其工作空间绑定);ClaudeSDKAdapter 代码保留但不作为默认
--   2) 幂等确保 qoder 执行层(cli-qoder)存在;PAT 由进程环境变量 QODER_PERSONAL_ACCESS_TOKEN 提供,不落库
-- 统一语义层下取数走声明式 semantic 工具 run_semantic_query(execute_sql 已下线)

-- 1) 移除 claude 执行层
DELETE FROM adh_workspace_execution_layers
WHERE execution_layer_id IN (SELECT id FROM adh_execution_layers WHERE name = 'claude');
DELETE FROM adh_execution_layers WHERE name = 'claude';

-- 2) 确保 qoder 执行层存在
INSERT IGNORE INTO adh_execution_layers (name, display_name, description, layer_type, config, status)
VALUES (
    'cli-qoder',
    'qoder CLI',
    'Qoder 官方 Agent SDK(qoder-agent-sdk):子代理/会话恢复/上下文压缩,平台工具(元数据/语义)经进程内 MCP 注入;取数统一走 run_semantic_query',
    'cli',
    JSON_OBJECT('mode', 'sdk', 'cli_name', 'qoder', 'cli_path', 'qodercli', 'sdk_tools', 'all'),
    'active'
);
