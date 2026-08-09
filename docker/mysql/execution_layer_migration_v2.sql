-- Execution Layer Migration v2
-- 工作空间绑定执行层时支持 tools 权限限制(allowed_tools)
-- 空/NULL 表示不限制;否则为标准工具名列表(read/write/edit/list/search/bash/webfetch)
-- 或具体工具名(如 mcp__server__tool)

ALTER TABLE adh_workspace_execution_layers
    ADD COLUMN allowed_tools JSON NULL COMMENT '允许使用的工具白名单(JSON 数组),空表示不限制'
    AFTER priority;
