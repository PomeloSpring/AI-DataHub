/**
 * 看板详情窗口（只读预览）—— 设计卡片「预览」的落点。
 *
 * 口径（2026-11-17 拍板）：会话内不再有"打开设计面板"这条路（AI 也不再产出新设计），
 * 历史 ```design 卡片只剩"看它生成的看板"这一个动作：按结果里的 dashboard_id 打开**已生成的
 * 看板**，不再提供选业务域 / 填 SQL / 确认发布这类协作入口。
 *
 * 为什么在弹窗内直渲画布，而不是 iframe 套播放页：播放页自带顶栏（返回/轮播/全屏/编辑外观），
 * 嵌进会话后任何一个都会把嵌帧导航走（Esc 直接跳去 /system/dashboards），用户点"看详情"
 * 却在对话里被带走。直渲与看板浏览页/大屏同一套画布组件（字模/布局/取数口径同源），
 * 关掉弹窗就还在会话里；要全屏轮播时再用底部的"在新窗口打开"去播放页。
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLink, Loader2, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ResponsiveDashboardCanvas } from '@/components/DashboardCanvas';
import { useDashboardStore, isRefreshableDataChart, type Dashboard } from '@/stores/dashboardStore';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import { useCancelChartRefreshOnLeave } from '@/hooks/useCancelChartRefreshOnLeave';
import client from '@/api/client';

/**
 * 播放页地址（"在新窗口打开"）。必须带 `group`：`/screen/:id` 缺 group 会被路由守卫
 * 导航回看板列表。`from=/system/...` = 管理域入口口径（管理员/属主可见编辑入口，
 * Esc 返回 /system/dashboards），适合"我要去大屏看这块板"的场景。
 */
export const screenPreviewPath = (dashboardId: number) =>
  `/screen/${dashboardId}?group=0&from=/system/dashboards`;

interface Props {
  dashboardId: number;
  onClose: () => void;
}

export default function DashboardPreviewDialog({ dashboardId, onClose }: Props) {
  const { t } = useTranslation('chat');
  const library = useVisLibrary();
  const refreshing = useDashboardStore(s => s.refreshing);
  const [board, setBoard] = useState<Dashboard | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  // 关闭弹窗即终止未完成的图表查询，别把连接继续耗在已经关掉的预览上
  useCancelChartRefreshOnLeave();

  useEffect(() => {
    let cancelled = false;
    setBoard(null);
    setError('');
    client.get(`/dashboard/${dashboardId}`).then(({ data }) => {
      if (cancelled) return;
      setBoard(data);
      // 登记为当前看板：卡片的参数提示/刷新/导出都以 store 上下文为唯一通道（与浏览页、大屏同口径）
      useDashboardStore.getState().adoptDashboard(data);
      // 预览默认**读缓存**（GET 只回 data_cache）：每次打开都逐图重查会把同一块板反复压到
      // 数据源上（用户反馈：“预览不要每次都刷新数据”），看板页/大屏本来也是缓存优先。
      // 例外只补“从来没有缓存”的图（发布期/老数据 cache 为空）：不补就会留白，被误读成
      // “这块板没数据”。其余图要看最新数字由下面的「刷新数据」按钮显式发起。
      const missing = ((data?.charts || []) as Dashboard['charts']).filter(c => isRefreshableDataChart(c) && !c.data_cache);
      if (missing.length) {
        void useDashboardStore.getState().refreshCharts(Number(data?.id) || dashboardId, missing);
      }
    }).catch(() => {
      // 看板不可用要显式可见（可能已删除/无可见权），不静默留一个空白框
      if (!cancelled) setError(t('dashboardPreview.unavailable'));
    });
    return () => { cancelled = true; };
  }, [dashboardId, retry, t]);

  const chartCount = board?.charts?.length || 0;
  // 缓存态要说在明处：预览不再自动重查，用户得知道“看到的是上次的缓存、要新数点刷新”
  const cachedCount = ((board?.charts || []) as Dashboard['charts']).filter(c => !!c.data_cache).length;
  const reload = () => {
    void useDashboardStore.getState().refreshCharts(Number(board?.id) || dashboardId, board?.charts || []);
  };

  return <Dialog open onOpenChange={open => { if (!open) onClose(); }}>
    <DialogContent className="flex h-[88vh] w-[95vw] max-w-[1200px] flex-col gap-3 overflow-hidden overflow-y-hidden">
      <DialogHeader>
        <DialogTitle>{board?.name || t('dashboardPreview.title')}</DialogTitle>
      </DialogHeader>
      {error ? <div className="flex min-h-0 flex-1 flex-col items-start gap-2">
        <p role="alert" className="text-sm text-muted-foreground">{error}</p>
        <Button size="sm" variant="outline" onClick={() => setRetry(v => v + 1)}>{t('dashboardPreview.retry')}</Button>
      </div> : <>
        <p className="shrink-0 text-xs text-muted-foreground">
          {board
            ? <>{t('dashboardPreview.meta', { count: chartCount })}{board.description ? ` · ${board.description}` : ''}</>
            : t('dashboardPreview.loading')}
          {board && cachedCount > 0 && <span className="ml-1">{t('dashboardPreview.cachedHint', { count: cachedCount })}</span>}
        </p>
        {/* 字模库没加载到：图表会退回默认样式，必须在弹窗里说在明处并给重试（不做静默降级） */}
        {library.error && <p role="alert" className="shrink-0 text-xs text-amber-600">
          {library.error}<Button variant="link" size="sm" onClick={() => { void library.refresh(); }}>{t('dashboardPreview.retry')}</Button>
        </p>}
        {board
          ? <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-border bg-background">
            <ResponsiveDashboardCanvas dashboard={board} items={library.items} />
          </div>
          : <div className="flex min-h-0 flex-1 items-center justify-center"><Loader2 className="animate-spin" aria-hidden /></div>}
      </>}
      <div className="flex shrink-0 items-center justify-end gap-2">
        {board && <Button size="sm" variant="ghost" onClick={reload} disabled={refreshing}
          aria-label={t('dashboardPreview.refresh')}>
          <RefreshCw className={`mr-1 h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />{t('dashboardPreview.refresh')}
        </Button>}
        <Button variant="outline" size="sm" asChild>
          <a href={screenPreviewPath(dashboardId)} target="_blank" rel="noreferrer">
            <ExternalLink className="mr-1 h-4 w-4" />{t('dashboardPreview.open')}
          </a>
        </Button>
      </div>
    </DialogContent>
  </Dialog>;
}