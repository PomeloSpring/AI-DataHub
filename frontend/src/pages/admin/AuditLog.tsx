/**
 * 审计日志页面 — 查看系统操作审计记录
 */
import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import { RefreshCw, Search, Shield, ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Spinner } from '@/components/ui/spinner';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { listAuditLogs, AuditLogItem } from '../../api/audit';

const MODULE_OPTIONS = [
  { value: '', label: '全部模块' },
  { value: 'datasource', label: '数据源' },
  { value: 'metadata', label: '元数据' },
  { value: 'model', label: '模型' },
  { value: 'workspace', label: '工作空间' },
  { value: 'scheduled_task', label: '定时任务' },
  { value: 'system', label: '系统' },
];

const MODULE_COLORS: Record<string, string> = {
  datasource: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  metadata: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  model: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
  workspace: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
  scheduled_task: 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-400',
  system: 'bg-gray-100 text-gray-700 dark:bg-gray-900/30 dark:text-gray-400',
};

const ACTION_LABELS: Record<string, string> = {
  login: '登录',
  logout: '登出',
  create_user: '创建用户',
  update_user: '更新用户',
  delete_user: '删除用户',
  reset_password: '重置密码',
  change_password: '修改密码',
  update_user_status: '更新用户状态',
  create_datasource: '创建数据源',
  update_datasource: '更新数据源',
  delete_datasource: '删除数据源',
  execute_sql: '执行SQL',
  sync_metadata: '同步元数据',
  create_metadata: '创建字段元数据',
  update_metadata: '更新字段元数据',
  delete_metadata: '删除字段元数据',
  create_table_info: '创建表信息',
  update_table_info: '更新表信息',
  delete_table_info: '删除表信息',
  clear_metadata_by_datasource: '清理数据源元数据',
  clear_metadata_by_table: '清理表元数据',
  create_template: '创建SQL模板',
  update_template: '更新SQL模板',
  delete_template: '删除SQL模板',
  create_term: '创建业务术语',
  update_term: '更新业务术语',
  delete_term: '删除业务术语',
  create_relation: '创建关联关系',
  update_relation: '更新关联关系',
  delete_relation: '删除关联关系',
  sync_relations: '同步表关联',
  update_brand_settings: '更新品牌设置',
  create_llm_model: '创建LLM模型',
  update_llm_model: '更新LLM模型',
  delete_llm_model: '删除LLM模型',
  set_default_model: '设置默认模型',
  update_embedding_config: '更新Embedding配置',
  reload_embedding: '重载Embedding模型',
  update_system_config: '更新系统配置',
  create_workspace: '创建工作空间',
  update_workspace: '更新工作空间',
  delete_workspace: '删除工作空间',
  create_scheduled_task: '创建定时任务',
  update_scheduled_task: '更新定时任务',
  delete_scheduled_task: '删除定时任务',
  toggle_scheduled_task: '切换定时任务状态',
  create_notification_channel: '创建通知渠道',
  update_notification_channel: '更新通知渠道',
  delete_notification_channel: '删除通知渠道',
  create_report_template: '创建报告模板',
  update_report_template: '更新报告模板',
  delete_report_template: '删除报告模板',
  create_workflow: '创建工作流',
  update_workflow: '更新工作流',
  delete_workflow: '删除工作流',
  create_prompt: '创建Prompt',
  update_prompt: '更新Prompt',
  install_mcp: '安装MCP服务',
  account_locked: '账号锁定',
};

export default function AuditLog() {
  const [logs, setLogs] = useState<AuditLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [filterModule, setFilterModule] = useState('');
  const [filterKeyword, setFilterKeyword] = useState('');
  const pageSize = 50;

  const load = useCallback(async (p?: number) => {
    setLoading(true);
    try {
      const params: any = { page: p ?? page, size: pageSize };
      if (filterModule) params.module = filterModule;
      if (filterKeyword.trim()) params.keyword = filterKeyword.trim();
      const data = await listAuditLogs(params);
      setLogs(data.items || []);
      setTotal(data.total || 0);
    } catch {
      toast.error('加载审计日志失败');
    } finally {
      setLoading(false);
    }
  }, [page, filterModule, filterKeyword]);

  useEffect(() => { load(); }, []);

  const totalPages = Math.ceil(total / pageSize);

  const handleSearch = () => {
    setPage(1);
    load(1);
  };

  const formatTime = (t: string) => {
    if (!t) return '-';
    try {
      const d = new Date(t);
      return d.toLocaleString('zh-CN', { hour12: false });
    } catch {
      return t;
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-semibold">审计日志</h2>
          <Badge variant="secondary" className="ml-2">{total} 条记录</Badge>
        </div>
        <Button variant="outline" size="sm" onClick={() => load()}>
          <RefreshCw className="h-4 w-4 mr-1" /> 刷新
        </Button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 flex-wrap">
        <Select value={filterModule} onValueChange={(v) => { setFilterModule(v === '__all__' ? '' : v); }}>
          <SelectTrigger className="w-36">
            <SelectValue placeholder="全部模块" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">全部模块</SelectItem>
            {MODULE_OPTIONS.filter(m => m.value).map(m => (
              <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="搜索操作详情..."
            value={filterKeyword}
            onChange={(e) => setFilterKeyword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            className="pl-8"
          />
        </div>
        <Button variant="secondary" size="sm" onClick={handleSearch}>查询</Button>
      </div>

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Spinner className="h-6 w-6 mr-2" /> 加载中...
        </div>
      ) : logs.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground">
          <Shield className="h-12 w-12 mx-auto mb-3 opacity-30" />
          <p>暂无审计日志</p>
        </div>
      ) : (
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full" aria-label="审计日志列表">
            <thead>
              <tr className="bg-muted/50 text-sm text-muted-foreground">
                <th className="text-left p-3 font-medium">时间</th>
                <th className="text-left p-3 font-medium">用户</th>
                <th className="text-left p-3 font-medium">模块</th>
                <th className="text-left p-3 font-medium">操作</th>
                <th className="text-left p-3 font-medium">详情</th>
                <th className="text-left p-3 font-medium">IP</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log) => (
                <tr key={log.id} className="border-t hover:bg-muted/30 transition-colors">
                  <td className="p-3 text-sm text-muted-foreground whitespace-nowrap">
                    {formatTime(log.created_at)}
                  </td>
                  <td className="p-3 text-sm font-medium">{log.username}</td>
                  <td className="p-3">
                    {log.module ? (
                      <Badge variant="secondary" className={`text-xs ${MODULE_COLORS[log.module] || ''}`}>
                        {MODULE_OPTIONS.find(m => m.value === log.module)?.label || log.module}
                      </Badge>
                    ) : (
                      <span className="text-muted-foreground text-sm">-</span>
                    )}
                  </td>
                  <td className="p-3 text-sm">
                    {ACTION_LABELS[log.action] || log.action}
                  </td>
                  <td className="p-3 text-sm max-w-[300px] truncate" title={log.detail}>
                    {log.detail || '-'}
                  </td>
                  <td className="p-3 text-sm text-muted-foreground">
                    {log.ip_address || '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">
            第 {page} / {totalPages} 页，共 {total} 条
          </span>
          <div className="flex items-center gap-1">
            <Button
              variant="outline" size="sm"
              disabled={page <= 1}
              onClick={() => { const p = page - 1; setPage(p); load(p); }}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="outline" size="sm"
              disabled={page >= totalPages}
              onClick={() => { const p = page + 1; setPage(p); load(p); }}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
