import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { LayoutDashboard } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Spinner } from '@/components/ui/spinner';
import DashboardChart from '../components/DashboardChart';
import { useDashboardStore } from '../stores/dashboardStore';

const CANVAS_WIDTH = 1920;
const CANVAS_HEIGHT = 1080;
const DEFAULT_CHART_SIZE = { w: 400, h: 350 };

export default function Analysis() {
  const { dashboardId } = useParams<{ dashboardId: string }>();
  const navigate = useNavigate();
  const { dashboards, loading, loadDashboards, setCurrent } = useDashboardStore();

  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(0);

  useEffect(() => { loadDashboards(); }, []);

  const current = useMemo(() => {
    return dashboards.find(d => d.id === Number(dashboardId)) || null;
  }, [dashboards, dashboardId]);

  useEffect(() => {
    if (current) setCurrent(current.id);
  }, [current?.id]);

  // Measure container width for responsive scaling
  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setContainerWidth(entry.contentRect.width);
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  // Auto-refresh data on mount
  useEffect(() => {
    if (current?.id) {
      useDashboardStore.getState().refreshCharts();
    }
  }, [current?.id]);

  if (loading && dashboards.length === 0) {
    return <div className="flex items-center justify-center h-screen"><Spinner size={48} /></div>;
  }

  if (!current) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-4">
        <LayoutDashboard className="h-16 w-16 text-muted-foreground/30 mb-4" />
        <p className="text-lg font-medium text-muted-foreground mb-2">未找到仪表盘</p>
        <Button onClick={() => navigate('/dashboard')}>前往仪表盘设计</Button>
      </div>
    );
  }

  if (current.status && current.status !== 'enabled') {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-4">
        <LayoutDashboard className="h-16 w-16 text-muted-foreground/30 mb-4" />
        <p className="text-lg font-medium text-muted-foreground mb-2">该仪表盘未启用</p>
        <Button onClick={() => navigate('/dashboard')}>前往仪表盘设计</Button>
      </div>
    );
  }

  const displayCharts = current.charts || [];

  // Calculate canvas height based on chart positions
  const canvasHeight = Math.max(
    CANVAS_HEIGHT,
    ...displayCharts.map(c => (c.position?.y ?? 0) + (c.position?.h ?? DEFAULT_CHART_SIZE.h) + 100)
  );

  // Scale to fit container width (start small until measured)
  const scale = containerWidth > 0 ? Math.min(containerWidth / CANVAS_WIDTH, 1) : 0.1;

  return (
    <div ref={containerRef} className="w-full h-full overflow-auto bg-background">
      {displayCharts.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-full text-center px-4">
          <p className="text-muted-foreground">该仪表盘暂无图表</p>
        </div>
      ) : (
        <div style={{
          width: CANVAS_WIDTH * scale,
          height: canvasHeight * scale,
          overflow: 'hidden',
          margin: '0 auto',
        }}>
        <div style={{
          width: CANVAS_WIDTH,
          height: canvasHeight,
          transformOrigin: 'top left',
          transform: `scale(${scale})`,
        }}>
          <div style={{ position: 'relative', width: '100%', height: '100%' }}>
            {displayCharts.map(chart => {
              let chartData = null;
              if (chart.data_cache) {
                try { chartData = JSON.parse(chart.data_cache); } catch { /* ignore */ }
              }
              if (!chartData && chart.config?.data) chartData = chart.config.data;

              const pos = chart.position || {};
              const isWidget = chart.chart_type?.startsWith('widget_');
              const ws = chart.config?.widgetStyle || {};

              return (
                <div
                  key={chart.id}
                  className="dashboard-chart-cell border rounded-lg bg-card overflow-hidden flex flex-col"
                  style={{
                    position: 'absolute',
                    left: pos.x ?? 0,
                    top: pos.y ?? 0,
                    width: pos.w ?? DEFAULT_CHART_SIZE.w,
                    height: pos.h ?? DEFAULT_CHART_SIZE.h,
                    ...(isWidget ? {
                      background: ws.backgroundColor || undefined,
                      borderRadius: ws.borderRadius != null ? ws.borderRadius : undefined,
                      borderLeft: `3px solid ${ws.borderColor || 'hsl(var(--primary))'}`,
                      borderWidth: ws.borderWidth != null ? ws.borderWidth : undefined,
                      borderColor: ws.borderColor || undefined,
                      borderStyle: ws.borderStyle || undefined,
                      boxShadow: ws.boxShadow || undefined,
                      opacity: ws.opacity != null ? ws.opacity : undefined,
                    } : {}),
                  }}
                >
                  {!isWidget && (
                    <div className="dashboard-chart-header px-3 py-2 border-b flex-shrink-0">
                      <span className="text-sm font-medium">{chart.name}</span>
                    </div>
                  )}
                  <div className="flex-1 min-h-0 p-2"
                    style={{ color: ws.textColor || undefined, fontSize: ws.fontSize || undefined }}>
                    <DashboardChart
                      chartType={chart.chart_type}
                      data={chartData || { columns: [], rows: [] }}
                      config={chart.config || {}}
                      chartId={chart.id}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
        </div>
      )}
    </div>
  );
}
