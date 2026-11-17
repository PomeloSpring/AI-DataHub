import { useState, useEffect, useCallback } from 'react';
import { toast } from '@/lib/toast';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { RefreshCw, Search } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Spinner } from '@/components/ui/spinner';
import client from '../../api/client';
import { useTranslation } from 'react-i18next';

interface EmbedLog {
  id: number;
  app_id: number;
  user_id: string;
  user_name: string;
  action: string;
  detail: string;
  ip_address: string;
  status: string;
  error_message: string;
  created_at: string;
}

export default function IntegrationLogs() {
  const { t } = useTranslation('ui');
  const [logs, setLogs] = useState<EmbedLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  const [filterAppId, setFilterAppId] = useState('');
  const [filterUserId, setFilterUserId] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  // 资源筛选用选择框(不手填 ID): 应用清单供下拉选择
  const [apps, setApps] = useState<Array<{ id: number; name: string }>>([]);
  useEffect(() => {
    client.get('/embed/admin/applications', { params: { page: 1, size: 100 } })
      .then(({ data }) => setApps(data.items || data || []))
      .catch(() => setApps([]));
  }, []);

  const load = useCallback(async (p?: number) => {
    setLoading(true);
    try {
      const params: any = { page: p ?? page, size: pageSize };
      if (filterAppId) params.app_id = filterAppId;
      if (filterUserId) params.user_id = filterUserId;
      if (filterStatus) params.status = filterStatus;
      const { data } = await client.get('/embed/admin/embed-logs', { params });
      setLogs(data.items || []);
      setTotal(data.total || 0);
    } catch {
      toast.error(t('k_077994c017'));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, filterAppId, filterUserId, filterStatus]);

  useEffect(() => { load(); }, []);

  const actionLabels: Record<string, string> = {
    verify: t('k_b7158a42ca'),
    chat_send: t('k_b5f15917b3'),
    dashboard_view: t('k_895f1917c7'),
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <select
          className="border rounded px-3 py-1.5 text-sm bg-background w-40"
          value={filterAppId}
          onChange={(e) => setFilterAppId(e.target.value)}
        >
          <option value="">{t("k_cf2c501336")}</option>
          {apps.map(app => <option key={app.id} value={String(app.id)}>{app.name}</option>)}
        </select>
        <Input
          placeholder={t("k_e0e375c2ba")}
          value={filterUserId}
          onChange={(e) => setFilterUserId(e.target.value)}
          className="w-40"
        />
        <select
          className="border rounded px-3 py-1.5 text-sm bg-background"
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
        >
          <option value="">{t("k_1a4c26d92d")}</option>
          <option value="success">{t("k_51991a5d11")}</option>
          <option value="error">{t("k_3e3c8068bb")}</option>
        </select>
        <Button variant="outline" size="sm" onClick={() => { setPage(1); load(1); }}>
          <Search className="h-4 w-4 mr-1" />{t('k_dcce9a144a')}
        </Button>
        <Button variant="outline" size="sm" onClick={() => load()}>
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12"><Spinner /></div>
      ) : (
        <div className="border rounded-lg">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="p-3 text-left">{t("k_89b4aa6364")}</th>
                <th className="p-3 text-left">{t("k_be8af550f3")}</th>
                <th className="p-3 text-left">{t("k_e0e375c2ba")}</th>
                <th className="p-3 text-left">{t("k_f3ea6d345e")}</th>
                <th className="p-3 text-left">{t("k_4f55ee1e68")}</th>
                <th className="p-3 text-left">IP</th>
                <th className="p-3 text-left">{t("k_62e951a692")}</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log) => (
                <tr key={log.id} className="border-b hover:bg-muted/30">
                  <td className="p-3 text-xs text-muted-foreground">
                    {new Date(log.created_at).toLocaleString()}
                  </td>
                  <td className="p-3 text-xs">{log.app_id}</td>
                  <td className="p-3 text-xs">{log.user_id}</td>
                  <td className="p-3">
                    <Badge variant="outline">{actionLabels[log.action] || log.action}</Badge>
                  </td>
                  <td className="p-3 text-xs max-w-[200px] truncate">{log.detail}</td>
                  <td className="p-3 text-xs text-muted-foreground">{log.ip_address}</td>
                  <td className="p-3">
                    <Badge variant={log.status === 'success' ? 'default' : 'destructive'}>
                      {log.status === 'success' ? t('k_51991a5d11') : t('k_3e3c8068bb')}
                    </Badge>
                    {log.error_message && (
                      <div className="text-xs text-red-500 mt-1">{log.error_message}</div>
                    )}
                  </td>
                </tr>
              ))}
              {logs.length === 0 && (
                <tr><td colSpan={7} className="p-8 text-center text-muted-foreground">{t("k_44060e77b1")}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <PaginationBar
        page={page}
        pageSize={pageSize}
        total={total}
        busy={loading}
        onChange={(p: number) => { setPage(p); load(p); }}
        onPageSizeChange={(n: number) => { setPageSize(n); setPage(1); load(1); }}
        hideWhenSingle
      />
    </div>
  );
}
