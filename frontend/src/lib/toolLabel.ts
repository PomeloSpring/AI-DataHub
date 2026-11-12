// 工具名 → 界面人话：会话时间线与报告「用最新数据重新生成」的过程面板共用这一份。
//
// 为什么单独一个模块：执行层送出的工具名是 SDK 限定名（`mcp__server__tool`），
// 各页面自己 split 一遍就会长出两套叫法（同一工具在聊天里叫「语义搜索」、
// 在报告里叫「MCP · datahub/run_semantic_query」）。口径只留这一处，
// 新工具登记一次两处同时生效（见 components/ToolCallTimeline.tsx 与本目录 reportLayout.ts 的消费者）。

const TOOL_LABELS: Record<string, string> = {
  // 内置 Agent 管线工具
  analyze_question: '分析问题',
  search_semantic_model: '语义搜索',
  get_schema: '表结构',
  execute_sql: '执行 SQL',
  search_chat_history: '历史搜索',
  browse_data_catalog: '数据目录',
  query_execute: '查询执行',
  sql_validate: 'SQL 校验',
  // 执行层进程内工具(sdk_tools)
  search_metadata: '元数据搜索',
  get_table_schema: '表结构',
  list_datasources: '数据源列表',
  get_metrics: '指标定义',
  get_glossary: '业务术语',
  query_by_tags: '标签查询',
  knowledge_search: '知识搜索',
  get_object_definition: '业务对象定义',
  // 无人值守取数的正主（报告重跑与定时任务最常走的一步）
  run_semantic_query: '语义取数',
  // 工作空间资产/磁盘治理工具
  asset_list: '资产清单',
  asset_get: '读取资产',
  asset_archive: '归档收藏',
  disk_status: '磁盘用量',
  cleanup_candidates: '清理候选',
  cleanup_execute: '执行清理',
  // 系统运营工具
  system_usage: '用量统计',
  system_overview: '系统概览',
  // 执行层内置文件/通用工具
  Read: '读取文件',
  Write: '写入文件',
  Edit: '编辑文件',
  Bash: '执行命令',
  Glob: '查找文件',
  Grep: '搜索内容',
  WebSearch: '网络搜索',
  WebFetch: '网页读取',
  Task: '子任务',
};

/** 工具限定名去掉 `mcp__` 与服务器段后的尾段（`mcp__datahub_x__run_semantic_query` → run_semantic_query）。 */
export function toolBaseName(tool: string): string {
  return String(tool || '').split('__').filter(Boolean).pop() || '';
}

/** 工具名显示文案：登记过就用人话，没登记就退成 `MCP · server/tool`（不裸抛内部限定名）。 */
export function getToolLabel(tool: string): string {
  const base = toolBaseName(tool);
  if (TOOL_LABELS[base]) return TOOL_LABELS[base];
  if (base === tool) return tool;
  const server = String(tool || '').split('__').filter(Boolean).slice(1, -1).join('/');
  return server ? `MCP · ${server}/${base}` : base;
}
