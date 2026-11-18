import { useState, useEffect } from 'react';
import { RefreshCw } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Button } from '@/components/ui/button';
import client from '../api/client';
import DashboardChart from './DashboardChart';
import { useChartDesign } from '@/hooks/useChartDesign';
import { useDashboardStore, isRefreshableDataChart } from '@/stores/dashboardStore';

interface ModalPageProps {
  pageId: number;
  params?: Record<string, any>;
  open: boolean;
  onClose: () => void;
}

interface DashboardPageData {
  id: number;
  name: string;
  charts: any[];
  layout: any[];
  filters?: any;
}

export default function ModalPage({ pageId, params, open, onClose }: ModalPageProps) {
  const [pageData, setPageData] = useState<DashboardPageData | null>(null);
  const [loading, setLoading] = useState(false);
  // 图表样式走 UI 字模库主题包管线(useChartDesign): 弹窗页与看板同一套字模(含该页自身设计)
  const { chartStyle } = useChartDesign({ filters: pageData?.filters });
  // 实时取数覆盖层：与看板/大屏/弹窗预览共用一份刷新结果（刷新后不必重新打开页面才能看到新数）
  const chartCache = useDashboardStore(s => s.chartCache);
  const refreshing = useDashboardStore(s => s.refreshing);

  useEffect(() => {
    if (!open || !pageId) {
      setPageData(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    client.get(`/dashboard/${pageId}`)
      .then(({ data }) => {
        if (cancelled) return;
        setPageData(data);
        // 与看板预览同口径：默认读缓存，只补从来没有缓存的图（否则每次打开都逐图重查一遍）
        const missing = (data?.charts || []).filter((c: any) => isRefreshableDataChart(c) && !c.data_cache);
        if (missing.length) {
          void useDashboardStore.getState().refreshCharts(Number(data?.id) || pageId, missing);
        }
      })
      .catch(() => {
        if (!cancelled) setPageData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [open, pageId]);

  const reload = () => {
    if (!pageData?.id) return;
    void useDashboardStore.getState().refreshCharts(pageData.id, pageData.charts || []);
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="max-w-[90vw] max-h-[85vh] w-[1200px]">
        <DialogHeader>
          <DialogTitle>{pageData?.name || '加载中...'}</DialogTitle>
        </DialogHeader>

        <ScrollArea className="h-[calc(85vh-150px)]">
          {loading ? (
            <div className="flex items-center justify-center h-[300px] text-muted-foreground">
              加载中...
            </div>
          ) : pageData ? (
            <div className="relative" style={{ minHeight: 600 }}>
              {pageData.charts?.map((chart: any) => {
                const pos = chart.position || { x: 0, y: 0, w: 400, h: 300 };
                // 覆盖层优先：点「刷新数据」后的新结果要即时上屏（不回读 DB 缓存）
                const live = chartCache[chart.id];
                const chartData = (() => {
                  const raw = live || chart.data_cache;
                  if (!raw) return { columns: [], rows: [] };
                  try { return typeof raw === 'string' ? JSON.parse(raw) : raw; } catch { return { columns: [], rows: [] }; }
                })();
                const styled = chartStyle({ ...(chart.config || {}), _pageParams: params }, chart.chart_type);

                return (
                  <div
                    key={chart.id}
                    style={{
                      position: 'absolute',
                      left: pos.x,
                      top: pos.y,
                      width: pos.w,
                      height: pos.h,
                    }}
                  >
                    <DashboardChart
                      chartType={chart.chart_type}
                      data={chartData}
                      config={styled.config}
                      visual={styled.visual}
                      chartId={chart.id}
                      dashboardId={pageData.id}
                      chartName={chart.name}
                    />
                  </div>
                );
              })}
              {(!pageData.charts || pageData.charts.length === 0) && (
                <div className="flex items-center justify-center h-[300px] text-muted-foreground">
                  该页面暂无图表
                </div>
              )}
            </div>
          ) : (
            <div className="flex items-center justify-center h-[300px] text-muted-foreground">
              页面加载失败
            </div>
          )}
        </ScrollArea>
        {/* 预览一律给刷新按钮：页面打开时读缓存（不反复重查数据源），要新数由用户显式发起 */}
        <div className="flex shrink-0 items-center justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={reload} disabled={refreshing || loading || !pageData}
            aria-label="刷新数据">
            <RefreshCw className={`mr-1 h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />刷新数据
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
