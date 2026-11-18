/**
 * 审计日志页面 — 查看系统操作审计记录
 */
import { useState, useEffect, useCallback } from 'react';

import { RefreshCw, Search, Shield } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Spinner } from '@/components/ui/spinner';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { listAuditLogs, AuditLogItem } from '../../api/audit';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { useTranslation } from 'react-i18next';
import { t as i18nTr } from '@/i18n/runtime';

const MODULE_OPTIONS = [
  { value: '', label: i18nTr('k_ecbfa7c3f2') },
  { value: 'datasource', label: i18nTr('k_a3ccf702c5') },
  { value: 'metadata', label: i18nTr('k_34681d7fd5') },
  { value: 'model', label: i18nTr('k_98fd0cbd9c') },
  { value: 'workspace', label: i18nTr('k_1bf3defde7') },
  { value: 'scheduled_task', label: i18nTr('k_c97ffbda2e') },
  { value: 'content', label: i18nTr('k_0841684ad2') },
  { value: 'auth', label: i18nTr('k_b7158a42ca') },
  { value: 'system', label: i18nTr('k_1a1f6dff78') },
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
  login: i18nTr('k_402d19e50f'),
  logout: i18nTr('k_75c856b174'),
  create_user: i18nTr('k_ac5b586ced'),
  update_user: i18nTr('k_1ce6da36d5'),
  delete_user: i18nTr('k_10991ce420'),
  reset_password: i18nTr('k_7e422146dd'),
  change_password: i18nTr('k_7fc88aeeda'),
  update_user_status: i18nTr('k_e4d2d6e467'),
  create_datasource: i18nTr('k_a90be9ee5c'),
  update_datasource: i18nTr('k_b9b06aa197'),
  delete_datasource: i18nTr('k_cd72cca961'),
  execute_sql: i18nTr('k_b579d4bc20'),
  sync_metadata: i18nTr('k_9d6343628f'),
  create_metadata: i18nTr('k_31490ce602'),
  update_metadata: i18nTr('k_42f81889de'),
  delete_metadata: i18nTr('k_e9106c1a33'),
  create_table_info: i18nTr('k_13b2712f05'),
  update_table_info: i18nTr('k_775f318b7a'),
  delete_table_info: i18nTr('k_e967e3cafc'),
  clear_metadata_by_datasource: i18nTr('k_488e38783f'),
  clear_metadata_by_table: i18nTr('k_2f93cdcc2d'),
  create_template: i18nTr('k_74825dceed'),
  update_template: i18nTr('k_5e21f842aa'),
  delete_template: i18nTr('k_5a523ed208'),
  create_term: i18nTr('k_d7ea31cec0'),
  update_term: i18nTr('k_453db005ed'),
  delete_term: i18nTr('k_6ddc731855'),
  create_relation: i18nTr('k_9195e5d3a7'),
  update_relation: i18nTr('k_59d895f54c'),
  delete_relation: i18nTr('k_9fd824a171'),
  sync_relations: i18nTr('k_5dcf5ea82f'),
  update_brand_settings: i18nTr('k_3ab158a813'),
  create_llm_model: i18nTr('k_78d40a78a9'),
  update_llm_model: i18nTr('k_025c648eff'),
  delete_llm_model: i18nTr('k_7e10102d78'),
  set_default_model: i18nTr('k_9a6647daa6'),
  update_system_config: i18nTr('k_6e96fe555e'),
  create_workspace: i18nTr('k_c51d81a943'),
  update_workspace: i18nTr('k_fb26e45339'),
  delete_workspace: i18nTr('k_0717b53396'),
  create_scheduled_task: i18nTr('k_230a57d496'),
  update_scheduled_task: i18nTr('k_f961a3d235'),
  delete_scheduled_task: i18nTr('k_0b198eb3d3'),
  toggle_scheduled_task: i18nTr('k_53fea40bf1'),
  create_notification_channel: i18nTr('k_137cfae568'),
  update_notification_channel: i18nTr('k_12bdcb0bf3'),
  delete_notification_channel: i18nTr('k_a4450b243c'),
  create_report_template: i18nTr('k_f4bfaf6ab9'),
  update_report_template: i18nTr('k_5f87cce732'),
  delete_report_template: i18nTr('k_bd75bde838'),
  create_workflow: i18nTr('k_7a73b5e429'),
  update_workflow: i18nTr('k_2078b9e581'),
  delete_workflow: i18nTr('k_2b1e7d283a'),
  create_prompt: i18nTr('k_39824d441b'),
  update_prompt: i18nTr('k_053ffa1c88'),
  install_mcp: i18nTr('k_2bc4b71572'),
  account_locked: i18nTr('k_25210d5405'),
};

// 配置变更审计的 action 是**运行时从路径推导**的（如 update_as_bot / create_eval_case），
// 静态表盖不全，这里把动词前缀拆出来译一下，剩下的当作资源名展示。
const VERB_LABELS: Record<string, string> = {
  create: i18nTr('k_fcbd093292'), update: i18nTr('k_d9db02d07a'), delete: i18nTr('k_3755f56f2f'), patch: i18nTr('k_d9db02d07a'), post: i18nTr('k_2cd9e6ce81'),
};

function formatAction(action: string): string {
  if (!action) return '-';
  const mapped = ACTION_LABELS[action];
  if (mapped) return mapped;
  const idx = action.indexOf('_');
  if (idx > 0) {
    const verb = VERB_LABELS[action.slice(0, idx)];
    if (verb) return `${verb} ${action.slice(idx + 1).replace(/_/g, ' ')}`;
  }
  return action;
}

export default function AuditLog() {
  const { t } = useTranslation('ui');
  const [logs, setLogs] = useState<AuditLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [filterModule, setFilterModule] = useState('');
  const [filterKeyword, setFilterKeyword] = useState('');
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [error, setError] = useState('');

  const load = useCallback(async (p?: number) => {
    setLoading(true);
    setError('');
    try {
      const params: any = { page: p ?? page, size: pageSize };
      if (filterModule) params.module = filterModule;
      if (filterKeyword.trim()) params.keyword = filterKeyword.trim();
      const data = await listAuditLogs(params);
      setLogs(data.items || []);
      setTotal(data.total || 0);
    } catch (err: any) {
      // 不得静默成“暂无审计日志”（no-silent-degradation）：接口失败必须让管理员看到
      const msg = err?.response?.data?.detail || err?.message;
      setError(typeof msg === 'string' && msg ? msg : i18nTr('k_73026bb9e2'));
      setLogs([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, filterModule, filterKeyword]);

  useEffect(() => { load(); }, []);

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
    <div className="flex flex-col h-full gap-4">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <Shield className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-semibold">{t("k_7666cd4323")}</h2>
          <Badge variant="secondary" className="ml-2">{t('k_cb30354a0e', { p0: total })}</Badge>
        </div>
        <Button variant="outline" size="sm" onClick={() => load()}>
          <RefreshCw className="h-4 w-4 mr-1" /> {t('k_38108eaa1d')}
        </Button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 flex-wrap shrink-0">
        <Select value={filterModule} onValueChange={(v) => { setFilterModule(v === '__all__' ? '' : v); }}>
          <SelectTrigger className="w-36">
            <SelectValue placeholder={t("k_ecbfa7c3f2")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">{t("k_ecbfa7c3f2")}</SelectItem>
            {MODULE_OPTIONS.filter(m => m.value).map(m => (
              <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder={t('k_0ba822eac2')}
            value={filterKeyword}
            onChange={(e) => setFilterKeyword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            className="pl-8"
          />
        </div>
        <Button variant="secondary" size="sm" onClick={handleSearch}>{t("k_711363c424")}</Button>
      </div>

      {/* Table */}
      <div className="flex-1 min-h-0 overflow-auto">
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Spinner className="h-6 w-6 mr-2" /> {t('k_26b5bd4947')}
        </div>
      ) : error ? (
        <div className="text-center py-12 text-destructive" role="alert">
          <Shield className="h-12 w-12 mx-auto mb-3 opacity-30" />
          <p className="mb-3">{i18nTr('k_73026bb9e2')}：{error}</p>
          <Button variant="outline" size="sm" onClick={() => load()}>
            <RefreshCw className="h-4 w-4 mr-1" /> {t('k_38108eaa1d')}
          </Button>
        </div>
      ) : logs.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground">
          <Shield className="h-12 w-12 mx-auto mb-3 opacity-30" />
          <p>{t("k_4e95331245")}</p>
        </div>
      ) : (
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full" aria-label={t("k_c0aeecbe7b")}>
            <thead>
              <tr className="bg-muted/50 text-sm text-muted-foreground">
                <th className="text-left p-3 font-medium">{t("k_89b4aa6364")}</th>
                <th className="text-left p-3 font-medium">{t("k_9ba763ea34")}</th>
                <th className="text-left p-3 font-medium">{t("k_c1bebd4ab3")}</th>
                <th className="text-left p-3 font-medium">{t("k_f3ea6d345e")}</th>
                <th className="text-left p-3 font-medium">{t("k_4f55ee1e68")}</th>
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
                    {formatAction(log.action)}
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
      </div>

      {/* Pagination —— 统一分页条：固定底栏（仅清单区滚动，页头/分页不随页面滚走） */}
      <PaginationBar
        page={page}
        pageSize={pageSize}
        total={total}
        busy={loading}
        onChange={(p: number) => { setPage(p); load(p); }}
        onPageSizeChange={(n: number) => { setPageSize(n); setPage(1); load(1); }}
        hideWhenSingle
        className="shrink-0 mt-2"
      />
    </div>
  );
}
