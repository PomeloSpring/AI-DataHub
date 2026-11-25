// 工具名 → 界面人话：会话时间线与报告「用最新数据重新生成」的过程面板共用这一份。
//
// 为什么单独一个模块：执行层送出的工具名是 SDK 限定名（`mcp__server__tool`），
// 各页面自己 split 一遍就会长出两套叫法（同一工具在聊天里叫「语义搜索」、
// 在报告里叫「MCP · datahub/run_semantic_query」）。口径只留这一处，
// 新工具登记一次两处同时生效（见 components/ToolCallTimeline.tsx 与本目录 reportLayout.ts 的消费者）。
//
// 多语言：文案登记在 chat:tools.<工具名>（zh/en 语言包），本模块只留 key 映射；
// getToolLabel 在调用期经 i18n.t 取值（不在模块顶层冻结，切语言即时生效）。
import i18n from '@/i18n';

const TOOL_LABEL_KEYS: Record<string, string> = {
  // 内置 Agent 管线工具
  analyze_question: 'tools.analyze_question',
  search_semantic_model: 'tools.search_semantic_model',
  get_schema: 'tools.get_schema',
  execute_sql: 'tools.execute_sql',
  search_chat_history: 'tools.search_chat_history',
  browse_data_catalog: 'tools.browse_data_catalog',
  query_execute: 'tools.query_execute',
  sql_validate: 'tools.sql_validate',
  // 执行层进程内工具(sdk_tools)
  search_metadata: 'tools.search_metadata',
  get_table_schema: 'tools.get_table_schema',
  list_datasources: 'tools.list_datasources',
  get_metrics: 'tools.get_metrics',
  get_glossary: 'tools.get_glossary',
  query_by_tags: 'tools.query_by_tags',
  knowledge_search: 'tools.knowledge_search',
  get_object_definition: 'tools.get_object_definition',
  // 无人值守取数的正主（报告重跑与定时任务最常走的一步）
  run_semantic_query: 'tools.run_semantic_query',
  // 平台算好的血缘链路（溯源/影响面/新鲜度）
  get_lineage: 'tools.get_lineage',
  // 工作空间资产/磁盘治理工具
  asset_list: 'tools.asset_list',
  asset_get: 'tools.asset_get',
  asset_archive: 'tools.asset_archive',
  disk_status: 'tools.disk_status',
  cleanup_candidates: 'tools.cleanup_candidates',
  cleanup_execute: 'tools.cleanup_execute',
  // 系统运营工具
  system_usage: 'tools.system_usage',
  system_overview: 'tools.system_overview',
  // 系统能力本体的任务流计划（场景 → 有序步骤 → 每步通道/权限码）
  system_capability_plan: 'tools.system_capability_plan',
  // 执行层内置文件/通用工具
  Read: 'tools.Read',
  Write: 'tools.Write',
  Edit: 'tools.Edit',
  Bash: 'tools.Bash',
  Glob: 'tools.Glob',
  Grep: 'tools.Grep',
  WebSearch: 'tools.WebSearch',
  WebFetch: 'tools.WebFetch',
  Task: 'tools.Task',
};

/** 工具限定名去掉 `mcp__` 与服务器段后的尾段（`mcp__datahub_x__run_semantic_query` → run_semantic_query）。 */
export function toolBaseName(tool: string): string {
  return String(tool || '').split('__').filter(Boolean).pop() || '';
}

/** 工具名显示文案：登记过就用人话（当前语言），没登记就退成 `MCP · server/tool`（不裸抛内部限定名）。 */
export function getToolLabel(tool: string): string {
  const base = toolBaseName(tool);
  const key = TOOL_LABEL_KEYS[base];
  if (key && i18n.exists(`chat:${key}`)) return i18n.t(`chat:${key}` as 'chat:tools.Read');
  if (base === tool) return tool;
  const server = String(tool || '').split('__').filter(Boolean).slice(1, -1).join('/');
  return server ? `MCP · ${server}/${base}` : base;
}
