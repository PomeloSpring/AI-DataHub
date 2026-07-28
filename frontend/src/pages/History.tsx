import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import { RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { ScrollArea } from '@/components/ui/scroll-area';
import client from '../api/client';

function TableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="space-y-0">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 px-4 py-3 border-b">
          <Skeleton className="h-4 w-[140px]" />
          <Skeleton className="h-4 w-[80px]" />
          <Skeleton className="h-4 w-[100px]" />
          <Skeleton className="h-4 flex-1 max-w-[200px]" />
          <Skeleton className="h-4 flex-1 max-w-[300px]" />
          <Skeleton className="h-5 w-[48px]" />
          <Skeleton className="h-4 w-[60px]" />
          <Skeleton className="h-4 w-[80px]" />
        </div>
      ))}
    </div>
  );
}

export default function History() {
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [days, setDays] = useState('7');
  const [status, setStatus] = useState<string>('all');
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 50,
    total: 0,
  });

  const load = useCallback(async (page = 1, pageSize = 50) => {
    setLoading(true);
    try {
      const { data: res } = await client.get('/history/', {
        params: {
          days: Number(days),
          status: status !== 'all' ? status : undefined,
          page,
          page_size: pageSize,
        },
      });
      setData(res.data || []);
      setPagination({
        current: res.page || page,
        pageSize: res.page_size || pageSize,
        total: res.total || 0,
      });
    } catch (err) {
      console.error('History load error:', err);
      toast.error('加载失败');
    } finally {
      setLoading(false);
    }
  }, [days, status]);

  useEffect(() => {
    load(1, pagination.pageSize);
  }, [days, status]);

  const handlePageChange = (newPage: number) => {
    load(newPage, pagination.pageSize);
  };

  const totalPages = Math.ceil(pagination.total / pagination.pageSize);

  return (
    <div className="h-full flex flex-col p-4 md:p-6">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between mb-6 gap-3">
        <h1 className="text-2xl font-bold text-foreground">查询历史</h1>
        <div className="flex items-center gap-3 flex-wrap">
          <Select value={status} onValueChange={(v) => { setStatus(v); setPagination(p => ({ ...p, current: 1 })); }}>
            <SelectTrigger className="w-[120px]">
              <SelectValue placeholder="全部状态" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部状态</SelectItem>
              <SelectItem value="success">成功</SelectItem>
              <SelectItem value="error">失败</SelectItem>
            </SelectContent>
          </Select>
          <Select value={days} onValueChange={(v) => { setDays(v); setPagination(p => ({ ...p, current: 1 })); }}>
            <SelectTrigger className="w-[120px]">
              <SelectValue placeholder="近 7 天" />
            </SelectTrigger>
            <SelectContent>
              {[1, 3, 7, 14, 30].map((d) => (
                <SelectItem key={d} value={String(d)}>近 {d} 天</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" size="sm" onClick={() => load(pagination.current, pagination.pageSize)}>
            <RefreshCw className="h-4 w-4 mr-2" />
            刷新
          </Button>
        </div>
      </div>

      <div className="flex-1 rounded-lg border bg-card overflow-hidden flex flex-col">
        {loading ? (
          <div className="flex-1">
            <div className="border-b bg-muted/50 px-4 py-3">
              <div className="flex gap-4">
                <Skeleton className="h-4 w-[100px]" />
                <Skeleton className="h-4 w-[60px]" />
                <Skeleton className="h-4 w-[80px]" />
                <Skeleton className="h-4 w-[120px]" />
                <Skeleton className="h-4 w-[160px]" />
                <Skeleton className="h-4 w-[50px]" />
                <Skeleton className="h-4 w-[50px]" />
                <Skeleton className="h-4 w-[60px]" />
              </div>
            </div>
            <TableSkeleton />
          </div>
        ) : (
          <ScrollArea className="flex-1 min-h-0">
            <table className="w-full" aria-label="查询历史记录">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">时间</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">用户</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">数据源</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">问题</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">SQL</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">状态</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">行数</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">耗时</th>
                </tr>
              </thead>
              <tbody>
                {data.map((row) => (
                  <tr key={row.id} className="border-b hover:bg-muted/50 transition-colors">
                    <td className="p-4 align-middle text-sm">
                      {new Date(row.created_at).toLocaleString('zh-CN')}
                    </td>
                    <td className="p-4 align-middle text-sm">{row.username}</td>
                    <td className="p-4 align-middle text-sm">
                      <Badge variant="outline">{row.datasource_name || '默认'}</Badge>
                    </td>
                    <td className="p-4 align-middle text-sm max-w-[200px] truncate" title={row.question}>{row.question}</td>
                    <td className="p-4 align-middle text-sm max-w-[300px] truncate" title={row.generated_sql}>
                      <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{row.generated_sql}</code>
                    </td>
                    <td className="p-4 align-middle">
                      <Badge variant={row.execution_status === 'success' ? 'default' : 'destructive'}>
                        {row.execution_status === 'success' ? '成功' : '失败'}
                      </Badge>
                    </td>
                    <td className="p-4 align-middle text-sm tabular-nums">{row.row_count}</td>
                    <td className="p-4 align-middle text-sm tabular-nums">{row.execution_time_ms ? `${row.execution_time_ms}ms` : '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.length === 0 && (
              <div className="flex flex-col items-center justify-center h-48 text-muted-foreground">
                <RefreshCw className="h-10 w-10 mb-3 opacity-30" />
                <p className="text-sm">暂无查询历史</p>
                <p className="text-xs mt-1">在 Chat 中发起数据查询后，记录将显示在这里</p>
              </div>
            )}
          </ScrollArea>
        )}

        <div className="flex flex-col sm:flex-row items-center justify-between px-4 py-3 border-t gap-3">
          <span className="text-sm text-muted-foreground">
            共 {pagination.total} 条记录
          </span>
          <div className="flex items-center gap-2">
            <Select
              value={String(pagination.pageSize)}
              onValueChange={(v) => {
                const newSize = Number(v);
                setPagination(p => ({ ...p, pageSize: newSize, current: 1 }));
                load(1, newSize);
              }}
            >
              <SelectTrigger className="w-[100px] h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[20, 50, 100, 200].map((n) => (
                  <SelectItem key={n} value={String(n)}>{n} 条/页</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              size="sm"
              disabled={pagination.current <= 1}
              onClick={() => handlePageChange(pagination.current - 1)}
            >
              上一页
            </Button>
            <span className="text-sm min-w-[60px] text-center tabular-nums">
              {pagination.current} / {totalPages || 1}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={pagination.current >= totalPages}
              onClick={() => handlePageChange(pagination.current + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
