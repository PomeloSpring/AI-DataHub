import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import { RefreshCw, Search } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Spinner } from '@/components/ui/spinner';
import client from '../../api/client';

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
  const [logs, setLogs] = useState<EmbedLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [filterAppId, setFilterAppId] = useState('');
  const [filterUserId, setFilterUserId] = useState('');
  const [filterStatus, setFilterStatus] = useState('');

  const load = useCallback(async (p?: number) => {
    setLoading(true);
    try {
      const params: any = { page: p ?? page, size: 50 };
      if (filterAppId) params.app_id = filterAppId;
      if (filterUserId) params.user_id = filterUserId;
      if (filterStatus) params.status = filterStatus;
      const { data } = await client.get('/embed/admin/embed-logs', { params });
      setLogs(data.items || []);
      setTotal(data.total || 0);
    } catch {
      toast.error('加载日志失败');
    } finally {
      setLoading(false);
    }
  }, [page, filterAppId, filterUserId, filterStatus]);

  useEffect(() => { load(); }, []);

  const actionLabels: Record<string, string> = {
    verify: '认证',
    chat_send: '发送消息',
    dashboard_view: '查看仪表盘',
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Input
          placeholder="应用ID"
          value={filterAppId}
          onChange={(e) => setFilterAppId(e.target.value)}
          className="w-32"
        />
        <Input
          placeholder="用户ID"
          value={filterUserId}
          onChange={(e) => setFilterUserId(e.target.value)}
          className="w-40"
        />
        <select
          className="border rounded px-3 py-1.5 text-sm bg-background"
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
        >
          <option value="">全部状态</option>
          <option value="success">成功</option>
          <option value="error">失败</option>
        </select>
        <Button variant="outline" size="sm" onClick={() => { setPage(1); load(1); }}>
          <Search className="h-4 w-4 mr-1" />筛选
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
                <th className="p-3 text-left">时间</th>
                <th className="p-3 text-left">应用ID</th>
                <th className="p-3 text-left">用户ID</th>
                <th className="p-3 text-left">操作</th>
                <th className="p-3 text-left">详情</th>
                <th className="p-3 text-left">IP</th>
                <th className="p-3 text-left">状态</th>
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
                      {log.status === 'success' ? '成功' : '失败'}
                    </Badge>
                    {log.error_message && (
                      <div className="text-xs text-red-500 mt-1">{log.error_message}</div>
                    )}
                  </td>
                </tr>
              ))}
              {logs.length === 0 && (
                <tr><td colSpan={7} className="p-8 text-center text-muted-foreground">暂无日志</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {total > 50 && (
        <div className="flex justify-center gap-2">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => { setPage(p => p - 1); load(page - 1); }}>上一页</Button>
          <span className="py-1 px-3 text-sm">第 {page} 页 / 共 {Math.ceil(total / 50)} 页</span>
          <Button variant="outline" size="sm" disabled={page * 50 >= total} onClick={() => { setPage(p => p + 1); load(page + 1); }}>下一页</Button>
        </div>
      )}
    </div>
  );
}
