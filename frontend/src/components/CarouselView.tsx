import { useState, useEffect, useCallback } from 'react';
import { ChevronLeft, ChevronRight, X, Settings } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import DashboardChart from './DashboardChart';
import { useThemeStore } from '../stores/themeStore';
import type { Dashboard } from '../stores/dashboardStore';

interface Props {
  dashboards: Dashboard[];
  startIndex?: number;
  interval?: number;
  onClose: () => void;
}

// Default carousel settings
const DEFAULT_SETTINGS = {
  backgroundColor: null as string | null,
  backgroundImage: '',
  backgroundSize: 'cover',
  backgroundPosition: 'center',
  overlayOpacity: 0.6,
};

export default function CarouselView({ dashboards, startIndex = 0, interval = 10, onClose }: Props) {
  const [current, setCurrent] = useState(startIndex);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settings, setSettings] = useState(() => {
    try {
      const saved = localStorage.getItem('carousel_settings');
      return saved ? { ...DEFAULT_SETTINGS, ...JSON.parse(saved) } : DEFAULT_SETTINGS;
    } catch {
      return DEFAULT_SETTINGS;
    }
  });

  const isDark = useThemeStore(s => s.isDark);

  const themeBg = isDark ? '#141414' : '#f5f5f5';
  const themeCardBg = isDark ? '#1f1f1f' : '#ffffff';
  const themeCardBorder = isDark ? '#303030' : '#e8e8e8';
  const themeText = isDark ? '#e8e8e8' : '#333333';
  const themeTextSecondary = isDark ? '#888888' : '#666666';

  const bgColor = settings.backgroundColor || themeBg;

  const next = useCallback(() => {
    setCurrent(prev => (prev + 1) % dashboards.length);
  }, [dashboards.length]);

  const prev = useCallback(() => {
    setCurrent(prev => (prev - 1 + dashboards.length) % dashboards.length);
  }, [dashboards.length]);

  useEffect(() => {
    if (interval <= 0 || dashboards.length <= 1) return;
    const timer = setInterval(next, interval * 1000);
    return () => clearInterval(timer);
  }, [interval, next, dashboards.length]);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowRight') next();
      if (e.key === 'ArrowLeft') prev();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose, next, prev]);

  const saveSettings = useCallback((newSettings: typeof DEFAULT_SETTINGS) => {
    setSettings(newSettings);
    localStorage.setItem('carousel_settings', JSON.stringify(newSettings));
  }, []);

  if (!dashboards.length) return null;

  const db = dashboards[current];
  const charts = db.charts || [];

  const COLS = 12;
  let maxCol = 0, maxRow = 0;
  charts.forEach(c => {
    const px = (c.position?.x ?? 0) + (c.position?.w ?? 6);
    const py = (c.position?.y ?? 0) + (c.position?.h ?? 4);
    if (px > maxCol) maxCol = px;
    if (py > maxRow) maxRow = py;
  });
  if (maxCol === 0) maxCol = COLS;
  if (maxRow === 0) maxRow = charts.length * 4;

  const bgStyle: React.CSSProperties = {
    background: bgColor,
  };

  if (settings.backgroundImage) {
    bgStyle.backgroundImage = `url(${settings.backgroundImage})`;
    bgStyle.backgroundSize = settings.backgroundSize;
    bgStyle.backgroundPosition = settings.backgroundPosition;
    bgStyle.backgroundRepeat = 'no-repeat';
  }

  return (
    <>
      <div className="fixed inset-0 z-50 flex flex-col overflow-hidden" style={bgStyle}>
        {settings.backgroundImage && (
          <div
            className="absolute inset-0 z-0"
            style={{
              background: isDark ? `rgba(0,0,0,${settings.overlayOpacity})` : `rgba(255,255,255,${settings.overlayOpacity})`,
            }}
          />
        )}

        <div className="flex items-center justify-between p-4 relative z-10">
          <h2 className="text-xl font-bold" style={{ color: themeText }}>{db.name}</h2>
          <div className="flex items-center gap-2">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="sm" onClick={() => setSettingsOpen(true)} style={{ color: themeText }}>
                  <Settings className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>播放设置</TooltipContent>
            </Tooltip>
            <Button variant="ghost" size="sm" onClick={onClose} style={{ color: themeText }}>
              <X className="h-4 w-4" />
            </Button>
          </div>
        </div>

        <div className="flex-1 flex items-center justify-center p-4 relative z-10">
          <div
            className="relative w-full max-w-[1600px]"
            style={{
              aspectRatio: `${maxCol} / ${maxRow}`,
              maxHeight: '100%',
            }}
          >
            {charts.map(chart => {
              let chartData = null;
              if (chart.data_cache) {
                try { chartData = JSON.parse(chart.data_cache); } catch { /* ignore */ }
              }
              if (!chartData && chart.config?.data) {
                chartData = chart.config.data;
              }

              const isWidget = chart.chart_type?.startsWith('widget_');
              const ws = chart.config?.widgetStyle || {};

              const left = ((chart.position?.x ?? 0) / maxCol) * 100;
              const top = ((chart.position?.y ?? 0) / maxRow) * 100;
              const width = ((chart.position?.w ?? 6) / maxCol) * 100;
              const height = ((chart.position?.h ?? 4) / maxRow) * 100;

              return (
                <div
                  key={chart.id}
                  className="absolute p-1"
                  style={{
                    left: `${left}%`, top: `${top}%`,
                    width: `${width}%`, height: `${height}%`,
                  }}
                >
                  <div
                    className="h-full flex flex-col overflow-hidden rounded-xl p-3"
                    style={{
                      background: isWidget ? (ws.backgroundColor || themeCardBg) : themeCardBg,
                      border: `1px solid ${ws.borderColor || themeCardBorder}`,
                      borderRadius: ws.borderRadius != null ? ws.borderRadius : undefined,
                      boxShadow: ws.boxShadow || undefined,
                      opacity: ws.opacity != null ? ws.opacity : undefined,
                    }}
                  >
                    {!isWidget && (
                      <div className="text-sm font-medium mb-1.5 flex-shrink-0" style={{ color: themeText }}>
                        {chart.name}
                      </div>
                    )}
                    <div className="flex-1 min-h-0"
                      style={{ color: ws.textColor || undefined, fontSize: ws.fontSize || undefined }}>
                      <DashboardChart
                        chartType={chart.chart_type}
                        data={chartData || { columns: [], rows: [] }}
                        config={chart.config || {}}
                      />
                    </div>
                  </div>
                </div>
              );
            })}
            {charts.length === 0 && (
              <div className="absolute inset-0 flex items-center justify-center" style={{ color: themeTextSecondary }}>
                该仪表盘暂无图表
              </div>
            )}
          </div>
        </div>

        {dashboards.length > 1 && (
          <div className="flex items-center justify-center gap-6 p-4 relative z-10">
            <Button variant="ghost" size="sm" onClick={prev} style={{ color: themeText }}>
              <ChevronLeft className="h-5 w-5" />
            </Button>
            <div className="flex gap-2">
              {dashboards.map((_, i) => (
                <div
                  key={i}
                  onClick={() => setCurrent(i)}
                  className="cursor-pointer transition-all rounded-full"
                  style={{
                    width: i === current ? 24 : 8,
                    height: 8,
                    background: i === current ? '#1677ff' : (isDark ? '#555' : '#bbb'),
                  }}
                />
              ))}
            </div>
            <Button variant="ghost" size="sm" onClick={next} style={{ color: themeText }}>
              <ChevronRight className="h-5 w-5" />
            </Button>
          </div>
        )}
      </div>

      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent className="max-w-[500px]">
          <DialogHeader>
            <DialogTitle>播放设置</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-2">
              <Label>背景色</Label>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  value={settings.backgroundColor || themeBg}
                  onChange={(e) => saveSettings({ ...settings, backgroundColor: e.target.value })}
                  className="w-10 h-10 rounded cursor-pointer"
                />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => saveSettings({ ...settings, backgroundColor: null })}
                >
                  恢复默认（跟随主题）
                </Button>
              </div>
            </div>

            <div className="space-y-2">
              <Label>背景图片</Label>
              <Input
                placeholder="输入图片URL地址"
                value={settings.backgroundImage}
                onChange={e => saveSettings({ ...settings, backgroundImage: e.target.value })}
              />
              {settings.backgroundImage && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => saveSettings({ ...settings, backgroundImage: '' })}
                >
                  清除背景图
                </Button>
              )}
            </div>

            {settings.backgroundImage && (
              <>
                <div className="space-y-2">
                  <Label>图片尺寸</Label>
                  <div className="flex gap-2">
                    {['cover', 'contain', '100% 100%'].map(size => (
                      <Button
                        key={size}
                        variant={settings.backgroundSize === size ? 'default' : 'outline'}
                        size="sm"
                        onClick={() => saveSettings({ ...settings, backgroundSize: size })}
                      >
                        {size === 'cover' ? '覆盖' : size === 'contain' ? '包含' : '拉伸'}
                      </Button>
                    ))}
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>遮罩透明度: {Math.round(settings.overlayOpacity * 100)}%</Label>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.1}
                    value={settings.overlayOpacity}
                    onChange={e => saveSettings({ ...settings, overlayOpacity: parseFloat(e.target.value) })}
                    className="w-full"
                  />
                </div>
              </>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
