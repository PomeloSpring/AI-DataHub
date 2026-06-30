import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Settings, X, Maximize, Minimize, RefreshCw, ChevronLeft, ChevronRight,
  Play, Pause, Monitor, Timer,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import DashboardChart from '../components/DashboardChart';
import DashboardParams from '../components/DashboardParams';
import { useDashboardStore, type Dashboard } from '../stores/dashboardStore';
import { useThemeStore } from '../stores/themeStore';
import client from '../api/client';

const REFRESH_OPTIONS = [
  { value: '0', label: '关闭' },
  { value: '5', label: '5秒' },
  { value: '10', label: '10秒' },
  { value: '30', label: '30秒' },
  { value: '60', label: '1分钟' },
  { value: '300', label: '5分钟' },
];

const CAROUSEL_OPTIONS = [
  { value: '0', label: '关闭' },
  { value: '5', label: '5秒' },
  { value: '10', label: '10秒' },
  { value: '15', label: '15秒' },
  { value: '30', label: '30秒' },
  { value: '60', label: '1分钟' },
  { value: '120', label: '2分钟' },
  { value: '300', label: '5分钟' },
];

export default function Screen() {
  const navigate = useNavigate();
  const { dashboardId: urlDashboardId } = useParams<{ dashboardId: string }>();
  const {
    dashboards, currentId, paramValues, refreshing,
    loadDashboards, setCurrent, setParamValue, refreshCharts,
  } = useDashboardStore();
  const isDark = useThemeStore(s => s.isDark);

  const [isFullscreen, setIsFullscreen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState('0');
  const [isPaused, setIsPaused] = useState(false);
  const [carouselInterval, setCarouselInterval] = useState(() => {
    try {
      return localStorage.getItem('screen_carousel_interval') || '0';
    } catch { return '0'; }
  });
  const [carouselPaused, setCarouselPaused] = useState(false);
  const carouselTimer = useRef<number | null>(null);
  const [settings, setSettings] = useState(() => {
    try {
      const saved = localStorage.getItem('screen_settings');
      return saved ? JSON.parse(saved) : { bgColor: '', bgImage: '', overlayOpacity: 0.6 };
    } catch {
      return { bgColor: '', bgImage: '', overlayOpacity: 0.6 };
    }
  });
  const [showToolbar, setShowToolbar] = useState(true);
  const toolbarTimer = useRef<number | null>(null);
  const refreshTimer = useRef<number | null>(null);
  const [screenMenuIds, setScreenMenuIds] = useState<number[] | null>(null);

  useEffect(() => { loadDashboards(); }, []);

  // Load screen menu config from menu tree (items with link_type='screen')
  useEffect(() => {
    client.get('/admin/menu-tree')
      .then(({ data }) => {
        if (Array.isArray(data)) {
          // Collect all leaf nodes with link_type='screen'
          const ids: number[] = [];
          const collect = (items: any[]) => {
            for (const item of items) {
              if (item.page_id && item.link_type === 'screen') {
                ids.push(item.page_id);
              }
              if (item.children) collect(item.children);
            }
          };
          collect(data);
          if (ids.length > 0) {
            setScreenMenuIds(ids);
          }
        }
      })
      .catch(() => {});
  }, []);

  // Set current dashboard from URL param
  useEffect(() => {
    if (urlDashboardId && dashboards.length > 0) {
      const id = Number(urlDashboardId);
      if (id && id !== currentId) {
        setCurrent(id);
      }
    }
  }, [urlDashboardId, dashboards]);

  // Filter dashboards: use screen menu config if available, otherwise all enabled
  const enabledDashboards = screenMenuIds
    ? dashboards.filter(d => screenMenuIds.includes(d.id) && (d.status === 'enabled' || !d.status))
    : dashboards.filter(d => d.status === 'enabled' || !d.status);
  const current: Dashboard | undefined = enabledDashboards.find(d => d.id === currentId);

  // Auto-hide toolbar
  const resetToolbarTimer = useCallback(() => {
    setShowToolbar(true);
    if (toolbarTimer.current) window.clearTimeout(toolbarTimer.current);
    toolbarTimer.current = window.setTimeout(() => setShowToolbar(false), 5000);
  }, []);

  useEffect(() => {
    resetToolbarTimer();
    return () => { if (toolbarTimer.current) window.clearTimeout(toolbarTimer.current); };
  }, [resetToolbarTimer]);

  // Auto-refresh
  useEffect(() => {
    if (refreshTimer.current) window.clearInterval(refreshTimer.current);
    const interval = parseInt(autoRefresh);
    if (interval > 0 && !isPaused) {
      refreshTimer.current = window.setInterval(() => {
        refreshCharts();
      }, interval * 1000);
    }
    return () => { if (refreshTimer.current) window.clearInterval(refreshTimer.current); };
  }, [autoRefresh, isPaused, refreshCharts]);

  // Carousel — auto-rotate dashboards
  useEffect(() => {
    if (carouselTimer.current) window.clearInterval(carouselTimer.current);
    const interval = parseInt(carouselInterval);
    if (interval > 0 && !carouselPaused && enabledDashboards.length > 1) {
      carouselTimer.current = window.setInterval(() => {
        goTo(1);
      }, interval * 1000);
    }
    return () => { if (carouselTimer.current) window.clearInterval(carouselTimer.current); };
  }, [carouselInterval, carouselPaused, enabledDashboards.length, currentId]);

  // Fullscreen
  const toggleFullscreen = useCallback(() => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().then(() => setIsFullscreen(true));
    } else {
      document.exitFullscreen().then(() => setIsFullscreen(false));
    }
  }, []);

  useEffect(() => {
    const handler = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', handler);
    return () => document.removeEventListener('fullscreenchange', handler);
  }, []);

  // Keyboard
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (document.fullscreenElement) {
          document.exitFullscreen();
        } else {
          navigate('/dashboard');
        }
      }
      if (e.key === 'f' || e.key === 'F') toggleFullscreen();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [navigate, toggleFullscreen]);

  const saveSettings = useCallback((next: typeof settings) => {
    setSettings(next);
    localStorage.setItem('screen_settings', JSON.stringify(next));
  }, []);

  const idx = enabledDashboards.findIndex(d => d.id === currentId);

  const goTo = (dir: number) => {
    const nextIdx = (idx + dir + enabledDashboards.length) % enabledDashboards.length;
    setCurrent(enabledDashboards[nextIdx].id);
  };

  if (!current) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="text-center">
          <Monitor className="h-16 w-16 mx-auto mb-4 text-muted-foreground/30" />
          <p className="text-muted-foreground mb-4">暂无已启用的仪表盘可展示</p>
          <p className="text-sm text-muted-foreground/70 mb-4">请在仪表盘设置中将状态设为「已启用」</p>
          <Button onClick={() => navigate('/dashboard')}>前往仪表盘设计</Button>
        </div>
      </div>
    );
  }

  const charts = current.charts || [];
  const bgColor = settings.bgColor || (isDark ? '#0a0a0a' : '#f0f0f0');

  const bgStyle: React.CSSProperties = { background: bgColor };
  if (settings.bgImage) {
    bgStyle.backgroundImage = `url(${settings.bgImage})`;
    bgStyle.backgroundSize = 'cover';
    bgStyle.backgroundPosition = 'center';
    bgStyle.backgroundRepeat = 'no-repeat';
  }

  return (
    <>
      <div
        className="fixed inset-0 z-40 flex flex-col overflow-hidden"
        style={bgStyle}
        onMouseMove={resetToolbarTimer}
      >
        {settings.bgImage && (
          <div className="absolute inset-0 z-0"
            style={{ background: isDark ? `rgba(0,0,0,${settings.overlayOpacity})` : `rgba(255,255,255,${settings.overlayOpacity})` }}
          />
        )}

        {/* Top toolbar — hidden in fullscreen */}
        {!isFullscreen && (
        <div
          className="flex items-center justify-between px-6 py-3 relative z-10 transition-opacity duration-300"
          style={{ opacity: showToolbar ? 1 : 0, pointerEvents: showToolbar ? 'auto' : 'none' }}
        >
          <div className="flex items-center gap-3">
            <h1 className="text-lg font-bold" style={{ color: isDark ? '#eee' : '#333' }}>
              {current.name}
            </h1>
            {enabledDashboards.length > 1 && (
              <div className="flex items-center gap-1">
                <Button variant="ghost" size="sm" onClick={() => goTo(-1)} style={{ color: isDark ? '#aaa' : '#666' }}>
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <span className="text-xs" style={{ color: isDark ? '#888' : '#999' }}>
                  {idx + 1}/{enabledDashboards.length}
                </span>
                <Button variant="ghost" size="sm" onClick={() => goTo(1)} style={{ color: isDark ? '#aaa' : '#666' }}>
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            )}
          </div>

          <div className="flex items-center gap-2">
            {/* Dashboard selector */}
            <Select value={String(currentId)} onValueChange={v => setCurrent(Number(v))}>
              <SelectTrigger className="w-[180px] h-8 text-xs" style={{ background: isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.05)', border: 'none', color: isDark ? '#eee' : '#333' }}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {enabledDashboards.map(d => (
                  <SelectItem key={d.id} value={String(d.id)}>{d.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* Auto-refresh */}
            <Select value={autoRefresh} onValueChange={setAutoRefresh}>
              <SelectTrigger className="w-[100px] h-8 text-xs" style={{ background: isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.05)', border: 'none', color: isDark ? '#eee' : '#333' }}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {REFRESH_OPTIONS.map(o => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>

            {parseInt(autoRefresh) > 0 && (
              <Button variant="ghost" size="sm" onClick={() => setIsPaused(!isPaused)} style={{ color: isDark ? '#aaa' : '#666' }}>
                {isPaused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
              </Button>
            )}

            {/* Carousel */}
            {enabledDashboards.length > 1 && (
              <>
                <div className="w-px h-5" style={{ background: isDark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.1)' }} />
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="ghost" size="sm" onClick={() => setCarouselPaused(!carouselPaused)}
                      style={{ color: carouselPaused ? (isDark ? '#aaa' : '#666') : '#3b82f6' }}>
                      <Timer className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>{carouselPaused ? '恢复轮播' : '暂停轮播'}</TooltipContent>
                </Tooltip>
                <Select value={carouselInterval} onValueChange={v => {
                  setCarouselInterval(v);
                  localStorage.setItem('screen_carousel_interval', v);
                }}>
                  <SelectTrigger className="w-[100px] h-8 text-xs" style={{ background: isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.05)', border: 'none', color: isDark ? '#eee' : '#333' }}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CAROUSEL_OPTIONS.map(o => (
                      <SelectItem key={o.value} value={o.value}>轮播{o.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </>
            )}

            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="sm" onClick={() => refreshCharts()} disabled={refreshing}
                  style={{ color: isDark ? '#aaa' : '#666' }}>
                  <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
                </Button>
              </TooltipTrigger>
              <TooltipContent>刷新数据</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="sm" onClick={toggleFullscreen} style={{ color: isDark ? '#aaa' : '#666' }}>
                  {isFullscreen ? <Minimize className="h-4 w-4" /> : <Maximize className="h-4 w-4" />}
                </Button>
              </TooltipTrigger>
              <TooltipContent>{isFullscreen ? '退出全屏' : '全屏'}</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="sm" onClick={() => setSettingsOpen(true)} style={{ color: isDark ? '#aaa' : '#666' }}>
                  <Settings className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>显示设置</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="sm" onClick={() => navigate('/dashboard')} style={{ color: isDark ? '#aaa' : '#666' }}>
                  <X className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>退出大屏</TooltipContent>
            </Tooltip>
          </div>
        </div>
        )}

        {/* Params — hidden in fullscreen */}
        {!isFullscreen && current.params && current.params.length > 0 && showToolbar && (
          <div className="relative z-10">
            <DashboardParams params={current.params} values={paramValues} onChange={setParamValue} />
          </div>
        )}

        {/* Chart grid — fills entire screen */}
        <div className="flex-1 flex items-center justify-center relative z-10 overflow-hidden"
          style={{ padding: isFullscreen ? 0 : '1rem' }}>
          <ChartGrid charts={charts} isDark={isDark} fullscreen={isFullscreen} />
        </div>
      </div>

      {/* Settings dialog */}
      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent className="max-w-[450px]">
          <DialogHeader>
            <DialogTitle>大屏显示设置</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>背景色</Label>
              <div className="flex items-center gap-2">
                <input type="color" value={settings.bgColor || bgColor}
                  onChange={e => saveSettings({ ...settings, bgColor: e.target.value })}
                  className="w-10 h-10 rounded cursor-pointer" />
                <Button variant="outline" size="sm"
                  onClick={() => saveSettings({ ...settings, bgColor: '' })}>
                  恢复默认
                </Button>
              </div>
            </div>
            <div className="space-y-2">
              <Label>背景图片</Label>
              <Input placeholder="图片URL" value={settings.bgImage}
                onChange={e => saveSettings({ ...settings, bgImage: e.target.value })} />
              {settings.bgImage && (
                <Button variant="outline" size="sm"
                  onClick={() => saveSettings({ ...settings, bgImage: '' })}>
                  清除
                </Button>
              )}
            </div>
            {settings.bgImage && (
              <div className="space-y-2">
                <Label>遮罩透明度: {Math.round(settings.overlayOpacity * 100)}%</Label>
                <input type="range" min={0} max={1} step={0.1}
                  value={settings.overlayOpacity}
                  onChange={e => saveSettings({ ...settings, overlayOpacity: parseFloat(e.target.value) })}
                  className="w-full" />
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ── Chart Grid ────────────────────────────────────────────────────────

const CANVAS_W = 1920;
const CANVAS_H = 1080;
const DEF_CHART_SIZE = { w: 400, h: 350 };

function ChartGrid({ charts, isDark, fullscreen }: { charts: any[]; isDark: boolean; fullscreen?: boolean }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setContainerSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  if (!charts.length) {
    return (
      <div className="text-center" style={{ color: isDark ? '#666' : '#999' }}>
        <p className="text-lg">该仪表盘暂无图表</p>
      </div>
    );
  }

  const themeText = isDark ? '#eee' : '#333';
  const themeCard = isDark ? 'rgba(30,30,30,0.9)' : 'rgba(255,255,255,0.95)';
  const themeBorder = isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)';

  // Calculate canvas size from chart positions (absolute pixels)
  const canvasHeight = Math.max(
    CANVAS_H,
    ...charts.map(c => (c.position?.y ?? 0) + (c.position?.h ?? DEF_CHART_SIZE.h) + 100)
  );

  // Scale: fullscreen fills entire screen, non-fullscreen fits width only (capped at 1)
  const scaleW = containerSize.width > 0 ? containerSize.width / CANVAS_W : 0.1;
  const scaleH = containerSize.height > 0 ? containerSize.height / canvasHeight : 0.1;
  const scale = fullscreen
    ? Math.min(scaleW, scaleH)  // fill screen, maintain aspect ratio
    : Math.min(scaleW, 1);      // fit width, cap at 1x

  return (
    <div ref={containerRef} className="w-full h-full overflow-hidden"
      style={{ display: 'flex', justifyContent: 'center', alignItems: fullscreen ? 'center' : 'flex-start' }}>
      <div style={{
        width: CANVAS_W * scale,
        height: canvasHeight * scale,
        overflow: 'hidden',
        flexShrink: 0,
      }}>
        <div style={{
          width: CANVAS_W,
          height: canvasHeight,
          transformOrigin: 'top left',
          transform: `scale(${scale})`,
          position: 'relative',
        }}>
          {charts.map(chart => {
            let chartData = null;
            if (chart.data_cache) {
              try { chartData = JSON.parse(chart.data_cache); } catch { /* ignore */ }
            }
            if (!chartData && chart.config?.data) chartData = chart.config.data;

            const isWidget = chart.chart_type?.startsWith('widget_');
            const ws = chart.config?.widgetStyle || {};
            const pos = chart.position || {};

            return (
              <div key={chart.id}
                style={{
                  position: 'absolute',
                  left: pos.x ?? 0,
                  top: pos.y ?? 0,
                  width: pos.w ?? DEF_CHART_SIZE.w,
                  height: pos.h ?? DEF_CHART_SIZE.h,
                  padding: 4,
                }}>
                <div className="dashboard-chart-cell h-full flex flex-col overflow-hidden rounded-xl p-3"
                  style={{
                    background: isWidget ? (ws.backgroundColor || themeCard) : themeCard,
                    border: `1px solid ${ws.borderColor || themeBorder}`,
                    borderRadius: ws.borderRadius != null ? ws.borderRadius : undefined,
                    boxShadow: ws.boxShadow || undefined,
                    opacity: ws.opacity != null ? ws.opacity : undefined,
                    backdropFilter: 'blur(8px)',
                  }}>
                  {!isWidget && (
                    <div className="dashboard-chart-header text-sm font-medium mb-1.5 flex-shrink-0" style={{ color: themeText }}>
                      {chart.name}
                    </div>
                  )}
                  <div className="flex-1 min-h-0"
                    style={{ color: ws.textColor || undefined, fontSize: ws.fontSize || undefined }}>
                    <DashboardChart
                      chartType={chart.chart_type}
                      data={chartData || { columns: [], rows: [] }}
                      config={chart.config || {}}
                      chartId={chart.id}
                    />
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
