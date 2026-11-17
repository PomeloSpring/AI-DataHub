import { useState, useEffect, useCallback } from 'react';
import { toast } from '@/lib/toast';
import { RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { ScrollArea } from '@/components/ui/scroll-area';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import client from '../api/client';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

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

export default function History({ compact = false }: { compact?: boolean }) {
  const { t } = useTranslation('ui');
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [days, setDays] = useState('7');
  const [status, setStatus] = useState<string>('all');
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: DEFAULT_PAGE_SIZE,
    total: 0,
  });

  const load = useCallback(async (page = 1, pageSize = DEFAULT_PAGE_SIZE) => {
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
      toast.error(tr('k_866b795eae'));
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

  return (
    <div className={compact ? '' : 'flex flex-col p-6 h-full'}>
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between mb-4 gap-3">
        <h1 className="text-lg font-semibold text-foreground">{t("k_6079f66ae4")}</h1>
        <div className="flex items-center gap-3 flex-wrap">
          <Select value={status} onValueChange={(v) => { setStatus(v); setPagination(p => ({ ...p, current: 1 })); }}>
            <SelectTrigger className="w-[120px]">
              <SelectValue placeholder={t("k_1a4c26d92d")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("k_1a4c26d92d")}</SelectItem>
              <SelectItem value="success">{t("k_51991a5d11")}</SelectItem>
              <SelectItem value="error">{t("k_3e3c8068bb")}</SelectItem>
            </SelectContent>
          </Select>
          <Select value={days} onValueChange={(v) => { setDays(v); setPagination(p => ({ ...p, current: 1 })); }}>
            <SelectTrigger className="w-[120px]">
              <SelectValue placeholder={t("k_f297b996de")} />
            </SelectTrigger>
            <SelectContent>
              {[1, 3, 7, 14, 30].map((d) => (
                <SelectItem key={d} value={String(d)}>{tr('k_c68a31cb46')}{' '}{d} {tr('k_249aba7632')}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" size="sm" onClick={() => load(pagination.current, pagination.pageSize)}>
            <RefreshCw className="h-4 w-4 mr-2" />
            {tr('k_38108eaa1d')}</Button>
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
            <table className="w-full" aria-label={t("k_14e8883443")}>
              <thead>
                <tr className="border-b bg-muted/50">
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_89b4aa6364")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_9ba763ea34")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_a3ccf702c5")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_2fb49eec39")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">SQL</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_62e951a692")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_579f280b5d")}</th>
                  <th scope="col" className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">{t("k_a9704e1997")}</th>
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
                      <Badge variant="outline">{row.datasource_name || tr('k_c8d09cf955')}</Badge>
                    </td>
                    <td className="p-4 align-middle text-sm max-w-[200px] truncate" title={row.question}>{row.question}</td>
                    <td className="p-4 align-middle text-sm max-w-[300px] truncate" title={row.generated_sql}>
                      <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{row.generated_sql}</code>
                    </td>
                    <td className="p-4 align-middle">
                      <Badge variant={row.execution_status === 'success' ? 'default' : 'destructive'}>
                        {row.execution_status === 'success' ? tr('k_51991a5d11') : tr('k_3e3c8068bb')}
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
                <p className="text-sm">{t("k_ee7229352d")}</p>
                <p className="text-xs mt-1">{t("k_84f99dc007")}</p>
              </div>
            )}
          </ScrollArea>
        )}

        {/* 分页栏：固定在卡片底部（卡片 flex-col + ScrollArea flex-1 已保证不随内容滚动） */}
        <PaginationBar
          page={pagination.current}
          pageSize={pagination.pageSize}
          total={pagination.total}
          busy={loading}
          onChange={handlePageChange}
          onPageSizeChange={(n) => {
            setPagination(p => ({ ...p, pageSize: n, current: 1 }));
            load(1, n);
          }}
          className="flex-shrink-0"
        />
      </div>
    </div>
  );
}
