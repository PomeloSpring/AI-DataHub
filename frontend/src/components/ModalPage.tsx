import { useState, useEffect } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ScrollArea } from '@/components/ui/scroll-area';
import client from '../api/client';
import DashboardChart from './DashboardChart';
import { useChartDesign } from '@/hooks/useChartDesign';

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

  useEffect(() => {
    if (!open || !pageId) {
      setPageData(null);
      return;
    }

    setLoading(true);
    client.get(`/dashboard/${pageId}`)
      .then(({ data }) => {
        setPageData(data);
      })
      .catch(() => {
        setPageData(null);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [open, pageId]);

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="max-w-[90vw] max-h-[85vh] w-[1200px]">
        <DialogHeader>
          <DialogTitle>{pageData?.name || '加载中...'}</DialogTitle>
        </DialogHeader>

        <ScrollArea className="h-[calc(85vh-100px)]">
          {loading ? (
            <div className="flex items-center justify-center h-[300px] text-muted-foreground">
              加载中...
            </div>
          ) : pageData ? (
            <div className="relative" style={{ minHeight: 600 }}>
              {pageData.charts?.map((chart: any) => {
                const pos = chart.position || { x: 0, y: 0, w: 400, h: 300 };
                const chartData = chart.data_cache ? JSON.parse(chart.data_cache) : { columns: [], rows: [] };
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
      </DialogContent>
    </Dialog>
  );
}
