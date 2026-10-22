import { useState, useEffect } from 'react';
import { Dialog, DialogContent, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { PAGE_COLUMNS, PAGE_LABELS, PAGE_ROW, pageGap, pageMinHeight, pageRows, type PageBreakpoint } from '@/lib/dashboardPageLayout';
import { naturalSizeOf, type Rect } from '@/lib/dashboardDesign';
import {
  Settings, BarChart3, Move, Paintbrush, Monitor, Grid3x3, Palette, PanelRightClose,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { CHART_TYPES, ChartIcon } from '../DashboardChart';
import { ParamNameSelect, WidgetOptionsEditor } from './ParamSelect';
import { CUSTOM_PRESET, DEFAULT_TIME_PRESET_KEYS, TIME_PRESETS, needsTime } from '@/lib/timePresets';
import type { PageParam } from '@/stores/dashboardStore';
import { DEFAULT_CHART_SIZE, CANVAS_WIDTH, CANVAS_HEIGHT } from '../../hooks/useCanvasInteraction';

type ElementType = 'chart' | 'control' | 'canvas';
const PROPERTY_TABS: [string, string][] = [['appearance', '外观'], ['data', '数据'], ['position', '位置']];

interface Props {
  isOpen: boolean;
  onClose: () => void;
  selectedChart: any;
  pageBreakpoint?: PageBreakpoint;
  pageRect?: Rect;
  onResetPage?: () => void;
  selectedElementType: ElementType;
  canvasSize: { width: number; height: number };
  setCanvasSize: (s: { width: number; height: number }) => void;
  canvasBgColor: string;
  setCanvasBgColor: (c: string) => void;
  gridSize: number;
  setGridSize: (g: number) => void;
  scale: number;
  setScale: (s: number) => void;
  allCharts: any[];
  isNewChart: (id: number) => boolean;
  onPropertyChange: (chartId: number, field: string, value: any) => void;
  onPositionChange: (chartId: number, axis: 'x' | 'y' | 'w' | 'h', value: number) => void;
  onWidgetConfigChange: (chartId: number, key: string, value: any) => void;
  onDelete: (chart: any) => void;
  onChartConfig: (chart: any) => void;
  tab: string;
  onTab: (tab: string) => void;
  /** 看板页面参数清单：控件参数名从这儿选，不手填 */
  pageParams?: PageParam[];
  onCreatePageParam?: (param: PageParam) => void;
}

export default function PropertyPanel({
  isOpen, onClose, selectedChart, selectedElementType,
  canvasSize, setCanvasSize, canvasBgColor, setCanvasBgColor,
  gridSize, setGridSize, scale, setScale, allCharts,
  isNewChart, onPropertyChange, onPositionChange, onWidgetConfigChange,
  onDelete, onChartConfig, pageBreakpoint, pageRect, onResetPage, tab, onTab, pageParams, onCreatePageParam,
}: Props) {
  const [narrow, setNarrow] = useState(() => window.innerWidth < 1024);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 1023px)');
    const change = () => setNarrow(media.matches);
    change(); media.addEventListener('change', change);
    return () => media.removeEventListener('change', change);
  }, []);
  // 无鼠标用户可用←/→ 在 tab 间移动（WAI-ARIA tablist 惯例）
  const moveTab = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft' && e.key !== 'Home' && e.key !== 'End') return;
    e.preventDefault();
    const current = PROPERTY_TABS.findIndex(([key]) => key === tab);
    const next = e.key === 'ArrowRight' ? (current + 1) % PROPERTY_TABS.length
      : e.key === 'ArrowLeft' ? (current - 1 + PROPERTY_TABS.length) % PROPERTY_TABS.length
      : e.key === 'Home' ? 0 : PROPERTY_TABS.length - 1;
    onTab(PROPERTY_TABS[next][0]);
    document.getElementById(`property-tab-${PROPERTY_TABS[next][0]}`)?.focus();
  };
  const content = (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-background">
      <div className="flex items-center justify-between p-3 border-b">
        <h3 className="font-semibold text-sm flex items-center gap-2">
          {selectedElementType === 'canvas' ? (
            <><Monitor className="h-4 w-4" />{pageBreakpoint ? '页面属性' : '画布属性'}</>
          ) : selectedElementType === 'control' ? (
            <><Settings className="h-4 w-4" />控件属性</>
          ) : (
            <><BarChart3 className="h-4 w-4" />图表属性</>
          )}
        </h3>
        <Button variant="ghost" size="sm" aria-label="关闭属性面板" className="h-7 w-7 p-0" onClick={onClose}>
          <PanelRightClose className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex border-b p-1" role="tablist" aria-label="属性分类" onKeyDown={moveTab}>
        {PROPERTY_TABS.map(([key, label]) =>
          <Button key={key} id={`property-tab-${key}`} role="tab" aria-selected={tab === key}
            aria-controls="property-tabpanel" tabIndex={tab === key ? 0 : -1}
            size="sm" variant={tab === key ? 'secondary' : 'ghost'} onClick={() => onTab(key)} className="flex-1">{label}</Button>)}
      </div>
      <ScrollArea className="flex-1 min-h-0 p-3">
        <div id="property-tabpanel" role="tabpanel" aria-labelledby={`property-tab-${tab}`} tabIndex={-1}>
        {selectedChart && selectedElementType !== 'canvas' ? (
          <div className="space-y-4">
            {/* Type badge */}
            <div className="flex items-center gap-2">
              <Badge variant={selectedElementType === 'control' ? 'secondary' : 'default'}>
                {CHART_TYPES.find(t => t.value === selectedChart.chart_type)?.label || selectedChart.chart_type}
              </Badge>
              {isNewChart(selectedChart.id) && <Badge variant="outline" className="text-xs">新增</Badge>}
            </div>

            <Separator />

            {/* Basic info */}
            <div className="space-y-3">
              <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">基本信息</div>
              <div className="space-y-2">
                <Label className="text-xs">名称</Label>
                <Input className="h-7 text-xs"
                  value={selectedChart.name || ''}
                  onChange={e => onPropertyChange(selectedChart.id, 'name', e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label className="text-xs">类型</Label>
                {/* 图表卡与控件卡各自只能在同类内换型：跨类改型会留下不匹配的 source_type 与数据配置 */}
                <Select value={selectedChart.chart_type}
                  onValueChange={v => onPropertyChange(selectedChart.id, 'chart_type', v)}>
                  <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CHART_TYPES.filter(ct => ct.value.startsWith('widget_') === selectedChart.chart_type?.startsWith('widget_')).map(ct => (
                      <SelectItem key={ct.value} value={ct.value} className="text-xs">
                        <span className="flex items-center gap-2">
                          <ChartIcon name={ct.icon} className="h-3 w-3" />
                          {ct.label}
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {selectedChart.chart_type?.startsWith('widget_') && (
                  <p className="text-[10px] text-muted-foreground">换型会保留已填的参数与样式，但不同控件的专有字段会重置。</p>
                )}
              </div>
            </div>

            <Separator />

            {/* Position & Size：与其他 tab 一致按条件渲染，不再用 hidden 常驻 DOM */}
            {tab === 'position' && <><div className="space-y-3">
              <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1">
                <Move className="h-3 w-3" /> {pageBreakpoint ? `${PAGE_LABELS[pageBreakpoint]}网格位置与大小` : '位置与大小'}
              </div>
              <div className="grid grid-cols-2 gap-2">
                {(['x', 'y', 'w', 'h'] as const).map(axis => (
                  <div key={axis} className="space-y-1">
                    <Label className="text-[10px] text-muted-foreground">{pageBreakpoint ? { x: '起始列（从 0 开始）', y: '起始行（从 0 开始）', w: '跨列数', h: '跨行数' }[axis] : axis === 'x' ? 'X' : axis === 'y' ? 'Y' : axis === 'w' ? '宽度' : '高度'}</Label>
                    <Input type="number" aria-label={`${pageBreakpoint ? '页面' : '大屏'}布局 ${axis}`} className="h-7 text-xs"
                      min={axis === 'w' || axis === 'h' ? 1 : 0}
                      value={(pageBreakpoint ? pageRect?.[axis] : selectedChart.position?.[axis]) ?? (axis === 'w' ? DEFAULT_CHART_SIZE.w : axis === 'h' ? DEFAULT_CHART_SIZE.h : 0)}
                      onChange={e => onPositionChange(selectedChart.id, axis, Number(e.target.value) || 0)} />
                  </div>
                ))}
              </div>
              {/* 不设下限：大小由人主动控制；提示只交代网格量子，不静默夹值 */}
              <p className="text-[10px] text-muted-foreground">
                {pageBreakpoint
                  ? `不限制最小值（网格以 1 行 ≈ ${PAGE_ROW + pageGap(pageBreakpoint)}px 为量子）；本类型建议 ${pageRows(pageMinHeight(selectedChart.chart_type), pageBreakpoint)} 行起步，可自由缩小。`
                  : `不限制最小值，大小由你控制；本类型建议 ${naturalSizeOf(selectedChart.chart_type).w}×${naturalSizeOf(selectedChart.chart_type).h}px 起步。`}
              </p>
            </div><Separator /></>}

            {/* Widget config */}
            {tab === 'data' && selectedChart.chart_type?.startsWith('widget_') && (
              <WidgetConfig
                chart={selectedChart}
                allCharts={allCharts}
                pageParams={pageParams || []}
                onCreatePageParam={onCreatePageParam}
                onWidgetConfigChange={onWidgetConfigChange}
              />
            )}

            <Separator />

            {/* Style settings */}
            {tab === 'appearance' && (selectedChart.chart_type?.startsWith('widget_') ? <StyleSettings
              chart={selectedChart} onWidgetConfigChange={onWidgetConfigChange}
            /> : <div className="space-y-3">
              <p className="text-xs text-muted-foreground">字模提供基础外观，下列设置为当前图表的显式覆盖。</p>
              {[['cardBg', '卡片背景'], ['titleColor', '标题颜色'], ['valueColor', '指标颜色'], ['labelColor', '标签颜色']].map(([key, label]) =>
                <label key={key} className="block space-y-1 text-xs">{label}<Input value={selectedChart.config?.[key] || ''} placeholder="沿用字模" onChange={e => onWidgetConfigChange(selectedChart.id, key, e.target.value)} /></label>)}
              <label className="block space-y-1 text-xs">系列配色（逗号分隔）<Input value={(selectedChart.config?.colorScheme || []).join(', ')} placeholder="沿用字模" onChange={e => onWidgetConfigChange(selectedChart.id, 'colorScheme', e.target.value.split(',').map(s => s.trim()).filter(Boolean))} /></label>
            </div>)}

            {/* Chart-specific: SQL info */}
            {tab === 'data' && !selectedChart.chart_type?.startsWith('widget_') && <div className="space-y-3 text-xs">
              <p>{selectedChart.query_source === 'semantic' || selectedChart.semantic_query ? '语义图表：声明式查询保持不变，不展示生成 SQL。' : '通过人工数据配置入口绑定数据。'}</p>
              <Button size="sm" variant="outline" onClick={() => onChartConfig(selectedChart)}>数据与字段配置</Button>
            </div>}

            <div className="h-2" />
          </div>
        ) : pageBreakpoint ? <div className="space-y-3 text-sm">
          <p>{PAGE_LABELS[pageBreakpoint]} · {PAGE_COLUMNS[pageBreakpoint]} 列 · 间距 {pageGap(pageBreakpoint)}px</p>
          <p className="text-xs text-muted-foreground">基础行高 {PAGE_ROW}px；图表高度包含行间距。仅修改当前断点，未调整的平板和手机布局从桌面派生。</p>
          <Button variant="outline" size="sm" onClick={onResetPage}>恢复当前断点自动适配</Button>
        </div> : (
          /* Canvas Properties */
          <CanvasProperties
            canvasSize={canvasSize}
            setCanvasSize={setCanvasSize}
            canvasBgColor={canvasBgColor}
            setCanvasBgColor={setCanvasBgColor}
            gridSize={gridSize}
            setGridSize={setGridSize}
            scale={scale}
            setScale={setScale}
            allCharts={allCharts}
          />
        )}
        </div>
      </ScrollArea>

      {/* Fixed footer */}
      {selectedChart && selectedElementType !== 'canvas' && (
        <div className="flex-shrink-0 border-t p-3 space-y-2 bg-background">
          {!selectedChart.chart_type?.startsWith('widget_') && (
            <Button className="w-full" size="sm" variant="outline" onClick={() => onChartConfig(selectedChart)}>
              <Settings className="h-4 w-4 mr-2" />高级配置
            </Button>
          )}
          <Button className="w-full" size="sm" variant="destructive" onClick={() => onDelete(selectedChart)}>
            <Settings className="h-4 w-4 mr-2" />
            删除{selectedElementType === 'control' ? '控件' : '图表'}
          </Button>
        </div>
      )}
    </div>
  );
  if (narrow) return <Dialog open={isOpen} onOpenChange={open => { if (!open) onClose(); }}><DialogContent className="left-auto right-0 top-0 h-[100dvh] w-[min(320px,95vw)] translate-x-0 translate-y-0 rounded-none p-0 flex flex-col gap-0">
    <DialogTitle className="sr-only">属性面板</DialogTitle><DialogDescription className="sr-only">修改当前选中元素的外观、数据与布局。</DialogDescription>{content}
  </DialogContent></Dialog>;
  return <aside className={`shrink-0 overflow-hidden border-l ${isOpen ? 'w-[300px]' : 'hidden'}`}>{content}</aside>;
}

// ========== Sub-components ==========

function WidgetConfig({ chart, allCharts, pageParams, onCreatePageParam, onWidgetConfigChange }: {
  chart: any; allCharts: any[]; pageParams: PageParam[]; onCreatePageParam?: (param: PageParam) => void;
  onWidgetConfigChange: (id: number, key: string, value: any) => void;
}) {
  const type: string = chart.chart_type;
  const isButton = ['widget_search', 'widget_reset', 'widget_export'].includes(type);
  const isLabel = type === 'widget_label';
  const takesParam = !isButton && !isLabel;
  const targetChartId = typeof chart.config?.targetChartId === 'number' ? chart.config.targetChartId : 0;
  // 选项显示图表 name（ui-resource-display）；value 用 id 是实现细节
  const exportTargets = (allCharts || []).filter(c => !String(c.chart_type || '').startsWith('widget_'));
  return (
    <div className="space-y-3">
      <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">控件配置</div>

      {chart.chart_type === 'widget_label' && (
        <div className="space-y-2">
          <Label className="text-xs">显示内容</Label>
          <textarea
            className="w-full text-xs rounded-md border border-input bg-background px-2 py-1.5 min-h-[60px] resize-y"
            value={chart.config?.content || ''}
            onChange={e => onWidgetConfigChange(chart.id, 'content', e.target.value)}
            placeholder="输入要显示的文本内容"
          />
        </div>
      )}

      {takesParam && (
        <ParamNameSelect label="参数 Key" value={chart.config?.paramKey || ''} params={pageParams}
          placeholder="如 site / date_start" onChange={v => onWidgetConfigChange(chart.id, 'paramKey', v)}
          onCreate={p => onCreatePageParam?.(p)} />
      )}

      <div className="space-y-2">
        <Label className="text-xs">
          {isButton ? '按钮文字' : isLabel ? '名称' : '标签文字'}
        </Label>
        <Input className="h-7 text-xs"
          value={chart.config?.label || ''}
          onChange={e => onWidgetConfigChange(chart.id, 'label', e.target.value)} />
      </div>

      {takesParam && (
        <>
          <div className="grid grid-cols-2 items-center gap-2">
            <div className="space-y-1">
              <Label className="text-xs">显示标签</Label>
              <Switch checked={chart.config?.showLabel !== false}
                onCheckedChange={v => onWidgetConfigChange(chart.id, 'showLabel', v)} />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">标签位置</Label>
              <Select value={chart.config?.labelPosition || 'left'} onValueChange={v => onWidgetConfigChange(chart.id, 'labelPosition', v)}>
                <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="left" className="text-xs">左侧</SelectItem>
                  <SelectItem value="top" className="text-xs">上方</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-2">
            <Label className="text-xs">占位提示</Label>
            <Input className="h-7 text-xs"
              value={chart.config?.placeholder || ''}
              onChange={e => onWidgetConfigChange(chart.id, 'placeholder', e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label className="text-xs">默认值</Label>
            <Input className="h-7 text-xs"
              value={chart.config?.defaultValue || ''}
              onChange={e => onWidgetConfigChange(chart.id, 'defaultValue', e.target.value)} />
          </div>
          {type === 'widget_daterange' ? (
            <div className="grid grid-cols-2 gap-2">
              {(['start', 'end'] as const).map((slot, i) => {
                const parts = String(chart.config?.bind_param || '').split(',').map(s => s.trim());
                return <ParamNameSelect key={slot} label={slot === 'start' ? '绑定开始参数' : '绑定结束参数'}
                  value={parts[i] || ''} params={pageParams} onChange={v => {
                    const next = [...parts]; next[i] = v;
                    onWidgetConfigChange(chart.id, 'bind_param', next.filter(Boolean).join(','));
                  }} onCreate={p => onCreatePageParam?.(p)} />
              })}
            </div>
          ) : (
            <ParamNameSelect label="绑定页面参数" value={chart.config?.bind_param || ''} params={pageParams}
              placeholder="不绑定" onChange={v => onWidgetConfigChange(chart.id, 'bind_param', v)}
              onCreate={p => onCreatePageParam?.(p)} />
          )}
        </>
      )}

      {type === 'widget_daterange' && <>
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <Label className="text-xs">时间粒度</Label>
            <Select value={chart.config?.granularity || 'date'} onValueChange={v => onWidgetConfigChange(chart.id, 'granularity', v)}>
              <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="date" className="text-xs">按天（日期列）</SelectItem>
                <SelectItem value="datetime" className="text-xs">到时分秒（时间戳列）</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">默认选项</Label>
            <Select value={chart.config?.defaultPreset || CUSTOM_PRESET} onValueChange={v => onWidgetConfigChange(chart.id, 'defaultPreset', v === CUSTOM_PRESET ? undefined : v)}>
              <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {TIME_PRESETS.map(p => <SelectItem key={p.key} value={p.key} className="text-xs">{p.label}</SelectItem>)}
                <SelectItem value={CUSTOM_PRESET} className="text-xs">不预设（用最大间隔）</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">快捷选项</Label>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {TIME_PRESETS.map(p => {
              const checked = !Array.isArray(chart.config?.presets) || chart.config.presets.includes(p.key);
              return <label key={p.key} className="flex items-center gap-1 text-[11px]">
                <input type="checkbox" checked={checked} onChange={e => {
                  const base: string[] = Array.isArray(chart.config?.presets) ? chart.config.presets : DEFAULT_TIME_PRESET_KEYS;
                  const next = e.target.checked ? [...base, p.key] : base.filter((k: string) => k !== p.key);
                  onWidgetConfigChange(chart.id, 'presets', DEFAULT_TIME_PRESET_KEYS.filter(k => next.includes(k)));
                }} />
                {p.label}
              </label>;
            })}
          </div>
          {Array.isArray(chart.config?.presets) && chart.config.presets.length === 0 && (
            <p className="text-[10px] text-muted-foreground">已全部取消：控件只保留两个日期输入框，不再显示快捷下拉。</p>
          )}
        </div>
        <div className="space-y-2">
          <Label className="text-xs">最大跨度（天）</Label>
          <Input type="number" className="h-7 text-xs" min={1}
            value={chart.config?.rangeMaxDays ?? 365}
            onChange={e => onWidgetConfigChange(chart.id, 'rangeMaxDays', e.target.value ? Number(e.target.value) : undefined)} />
          <p className="text-[10px] text-muted-foreground">限制自由选择时结束与开始的间隔；快捷选项不受此限制。</p>
        </div>
        {(chart.config?.granularity === 'datetime' || needsTime(chart.config?.defaultPreset)) && (
          <p className="text-[10px] text-muted-foreground">
            当前会下发带时分秒的区间，请确认图表 SQL 比较的列是时间戳；若列是 DATE，小时级窗口会退化成整天。
          </p>
        )}
      </>}

      {type === 'widget_export' && (
        <div className="space-y-2">
          <Label className="text-xs">导出目标图表</Label>
          <Select value={targetChartId ? String(targetChartId) : ''} onValueChange={v => onWidgetConfigChange(chart.id, 'targetChartId', Number(v))}>
            <SelectTrigger className="h-7 text-xs"><SelectValue placeholder="请选择要导出的图表" /></SelectTrigger>
            <SelectContent>
              {exportTargets.map(c => <SelectItem key={c.id} value={String(c.id)} className="text-xs">{c.name}</SelectItem>)}
            </SelectContent>
          </Select>
          {!targetChartId && (
            <p role="alert" className="text-[10px] text-destructive">未选择目标图表，运行时导出按钮会置灰（不会默默导出别的图表）。</p>
          )}
          {!!targetChartId && !exportTargets.some(c => c.id === targetChartId) && (
            <p role="alert" className="text-[10px] text-destructive">原目标图表已不在当前看板，请重新选择。</p>
          )}
          {!!targetChartId && exportTargets.some(c => c.id === targetChartId) && (
            <p className="text-[10px] text-muted-foreground">导出该图表当前缓存的数据行，取数口径与图表一致。</p>
          )}
        </div>
      )}

      {(type === 'widget_select' || type === 'widget_multi_select') && (
        <WidgetOptionsEditor
          value={(chart.config?.options || []) as { label: string; value: string }[]}
          onChange={opts => onWidgetConfigChange(chart.id, 'options', opts)} />
      )}

      {chart.chart_type === 'widget_number' && (
        <div className="grid grid-cols-3 gap-2">
          {['min', 'max', 'step'].map(field => (
            <div key={field} className="space-y-1">
              <Label className="text-[10px] text-muted-foreground">{field === 'min' ? '最小值' : field === 'max' ? '最大值' : '步长'}</Label>
              <Input type="number" className="h-7 text-xs"
                value={chart.config?.[field] ?? (field === 'step' ? 1 : '')}
                onChange={e => onWidgetConfigChange(chart.id, field, e.target.value ? Number(e.target.value) : undefined)} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StyleSettings({ chart, onWidgetConfigChange }: { chart: any; onWidgetConfigChange: (id: number, key: string, value: any) => void }) {
  const ws = chart.config?.widgetStyle || {};

  const updateStyle = (key: string, value: any) => {
    onWidgetConfigChange(chart.id, 'widgetStyle', { ...ws, [key]: value });
  };

  return (
    <div className="space-y-3">
      <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1">
        <Paintbrush className="h-3 w-3" /> 样式设置
      </div>

      {/* Background color */}
      <div className="space-y-1">
        <Label className="text-xs">背景色</Label>
        <div className="flex items-center gap-2">
          <input type="color" value={ws.backgroundColor || '#ffffff'}
            onChange={e => updateStyle('backgroundColor', e.target.value)}
            className="w-7 h-7 rounded cursor-pointer border flex-shrink-0" />
          <Input className="h-7 text-xs font-mono flex-1"
            value={ws.backgroundColor || ''}
            onChange={e => updateStyle('backgroundColor', e.target.value || undefined)}
            placeholder="transparent" />
        </div>
      </div>

      {/* Text color */}
      <div className="space-y-1">
        <Label className="text-xs">文字颜色</Label>
        <div className="flex items-center gap-2">
          <input type="color" value={ws.textColor || '#000000'}
            onChange={e => updateStyle('textColor', e.target.value)}
            className="w-7 h-7 rounded cursor-pointer border flex-shrink-0" />
          <Input className="h-7 text-xs font-mono flex-1"
            value={ws.textColor || ''}
            onChange={e => updateStyle('textColor', e.target.value || undefined)}
            placeholder="inherit" />
        </div>
      </div>

      {/* Border color */}
      <div className="space-y-1">
        <Label className="text-xs">边框颜色</Label>
        <div className="flex items-center gap-2">
          <input type="color" value={ws.borderColor || '#e5e7eb'}
            onChange={e => updateStyle('borderColor', e.target.value)}
            className="w-7 h-7 rounded cursor-pointer border flex-shrink-0" />
          <Input className="h-7 text-xs font-mono flex-1"
            value={ws.borderColor || ''}
            onChange={e => updateStyle('borderColor', e.target.value || undefined)}
            placeholder="默认" />
        </div>
      </div>

      {/* Border width + style */}
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <Label className="text-xs">边框宽度</Label>
          <Input type="number" className="h-7 text-xs"
            value={ws.borderWidth ?? ''}
            onChange={e => updateStyle('borderWidth', e.target.value ? Number(e.target.value) : undefined)}
            placeholder="1" />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">边框样式</Label>
          <Select value={ws.borderStyle || 'solid'} onValueChange={v => updateStyle('borderStyle', v)}>
            <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="solid" className="text-xs">实线</SelectItem>
              <SelectItem value="dashed" className="text-xs">虚线</SelectItem>
              <SelectItem value="dotted" className="text-xs">点线</SelectItem>
              <SelectItem value="none" className="text-xs">无</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Border radius */}
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <Label className="text-xs">圆角</Label>
          <span className="text-[10px] text-muted-foreground">{ws.borderRadius ?? 8}px</span>
        </div>
        <Slider min={0} max={32} step={1}
          value={[ws.borderRadius ?? 8]}
          onValueChange={([v]) => updateStyle('borderRadius', v)} />
      </div>

      {/* Padding */}
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <Label className="text-xs">内边距</Label>
          <span className="text-[10px] text-muted-foreground">{ws.padding ?? 8}px</span>
        </div>
        <Slider min={0} max={40} step={2}
          value={[ws.padding ?? 8]}
          onValueChange={([v]) => updateStyle('padding', v)} />
      </div>

      {/* Font size */}
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <Label className="text-xs">字号</Label>
          <span className="text-[10px] text-muted-foreground">{ws.fontSize ?? 14}px</span>
        </div>
        <Slider min={11} max={32} step={1}
          value={[ws.fontSize ?? 14]}
          onValueChange={([v]) => updateStyle('fontSize', v)} />
      </div>

      {/* Line height */}
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <Label className="text-xs">行高</Label>
          <span className="text-[10px] text-muted-foreground">{ws.lineHeight ? `${ws.lineHeight}px` : '自动'}</span>
        </div>
        <Slider min={0} max={40} step={1}
          value={[ws.lineHeight ?? 0]}
          onValueChange={([v]) => updateStyle('lineHeight', v || undefined)} />
        <p className="text-[10px] text-muted-foreground">0 = 自动（跟随字号）</p>
      </div>

      {/* Control height — 接管多选框等内层写死高度，小尺寸控件才能真正缩小 */}
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <Label className="text-xs">控件高</Label>
          <span className="text-[10px] text-muted-foreground">{ws.height ? `${ws.height}px` : '默认'}</span>
        </div>
        <Slider min={0} max={160} step={2}
          value={[ws.height ?? 0]}
          onValueChange={([v]) => updateStyle('height', v || undefined)} />
        <p className="text-[10px] text-muted-foreground">0 = 默认；不得低于当前控件类型的最小尺寸</p>
      </div>

      {/* Label width */}
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <Label className="text-xs">标签宽度</Label>
          <span className="text-[10px] text-muted-foreground">{ws.labelWidth ? `${ws.labelWidth}px` : '自适应'}</span>
        </div>
        <Slider min={0} max={160} step={4}
          value={[ws.labelWidth ?? 0]}
          onValueChange={([v]) => updateStyle('labelWidth', v || undefined)} />
      </div>

      {/* Gap between label and control */}
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <Label className="text-xs">间距</Label>
          <span className="text-[10px] text-muted-foreground">{ws.gap ? `${ws.gap}px` : '默认'}</span>
        </div>
        <Slider min={0} max={24} step={1}
          value={[ws.gap ?? 0]}
          onValueChange={([v]) => updateStyle('gap', v || undefined)} />
      </div>

      {/* Font weight */}
      <div className="space-y-1">
        <Label className="text-xs">字重</Label>
        <Select value={ws.fontWeight || 'normal'} onValueChange={v => updateStyle('fontWeight', v)}>
          <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="normal" className="text-xs">常规 (400)</SelectItem>
            <SelectItem value="medium" className="text-xs">中等 (500)</SelectItem>
            <SelectItem value="semibold" className="text-xs">半粗 (600)</SelectItem>
            <SelectItem value="bold" className="text-xs">粗体 (700)</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Opacity */}
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <Label className="text-xs">透明度</Label>
          <span className="text-[10px] text-muted-foreground">{Math.round((ws.opacity ?? 1) * 100)}%</span>
        </div>
        <Slider min={0} max={100} step={5}
          value={[Math.round((ws.opacity ?? 1) * 100)]}
          onValueChange={([v]) => updateStyle('opacity', v / 100)} />
      </div>

      {/* Box shadow */}
      <div className="space-y-1">
        <Label className="text-xs">阴影</Label>
        <Select value={ws.boxShadow || 'none'} onValueChange={v => updateStyle('boxShadow', v === 'none' ? undefined : v)}>
          <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="none" className="text-xs">无</SelectItem>
            <SelectItem value="0 1px 3px rgba(0,0,0,0.12)" className="text-xs">轻微</SelectItem>
            <SelectItem value="0 2px 8px rgba(0,0,0,0.15)" className="text-xs">中等</SelectItem>
            <SelectItem value="0 4px 16px rgba(0,0,0,0.2)" className="text-xs">较强</SelectItem>
            <SelectItem value="0 8px 32px rgba(0,0,0,0.25)" className="text-xs">强烈</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Alignment (for widgets) */}
      {chart.chart_type?.startsWith('widget_') && (
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <Label className="text-xs">垂直对齐</Label>
            <Select value={ws.verticalAlign || 'center'} onValueChange={v => updateStyle('verticalAlign', v)}>
              <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="flex-start" className="text-xs">顶部</SelectItem>
                <SelectItem value="center" className="text-xs">居中</SelectItem>
                <SelectItem value="flex-end" className="text-xs">底部</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">水平对齐</Label>
            <Select value={ws.horizontalAlign || 'center'} onValueChange={v => updateStyle('horizontalAlign', v)}>
              <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="flex-start" className="text-xs">左对齐</SelectItem>
                <SelectItem value="center" className="text-xs">居中</SelectItem>
                <SelectItem value="flex-end" className="text-xs">右对齐</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      )}

      {/* Reset style button */}
      <Button variant="outline" size="sm" className="w-full h-7 text-xs"
        onClick={() => onWidgetConfigChange(chart.id, 'widgetStyle', {})}>
        重置样式
      </Button>
    </div>
  );
}

function CanvasProperties({
  canvasSize, setCanvasSize, canvasBgColor, setCanvasBgColor,
  gridSize, setGridSize, scale, setScale, allCharts,
}: {
  canvasSize: { width: number; height: number };
  setCanvasSize: (s: { width: number; height: number }) => void;
  canvasBgColor: string;
  setCanvasBgColor: (c: string) => void;
  gridSize: number;
  setGridSize: (g: number) => void;
  scale: number;
  setScale: (s: number) => void;
  allCharts: any[];
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Monitor className="h-4 w-4" />
        <span>画布设置</span>
      </div>
      <Separator />

      <div className="space-y-3">
        <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">尺寸</div>
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <Label className="text-[10px] text-muted-foreground">宽度 (px)</Label>
            <Input type="number" className="h-7 text-xs" value={canvasSize.width}
              onChange={e => setCanvasSize({ ...canvasSize, width: Number(e.target.value) || CANVAS_WIDTH })} />
          </div>
          <div className="space-y-1">
            <Label className="text-[10px] text-muted-foreground">高度 (px)</Label>
            <Input type="number" className="h-7 text-xs" value={canvasSize.height}
              onChange={e => setCanvasSize({ ...canvasSize, height: Number(e.target.value) || CANVAS_HEIGHT })} />
          </div>
        </div>
        <div className="flex items-center justify-between text-xs text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
          <span>分辨率</span>
          <span className="font-mono">{canvasSize.width} × {canvasSize.height} px</span>
        </div>
      </div>

      <Separator />

      <div className="space-y-3">
        <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1">
          <Palette className="h-3 w-3" /> 背景
        </div>
        <div className="flex items-center gap-2">
          <input type="color" value={canvasBgColor || '#f5f5f5'}
            onChange={e => setCanvasBgColor(e.target.value)}
            className="w-8 h-8 rounded cursor-pointer border" />
          <Input className="h-7 text-xs font-mono flex-1" value={canvasBgColor || ''}
            onChange={e => setCanvasBgColor(e.target.value)}
            placeholder="默认背景" />
          <Button variant="outline" size="sm" className="h-7 text-xs flex-shrink-0"
            onClick={() => setCanvasBgColor('')}
          >重置</Button>
        </div>
      </div>

      <Separator />

      <div className="space-y-3">
        <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1">
          <Grid3x3 className="h-3 w-3" /> 网格
        </div>
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label className="text-xs">网格大小</Label>
            <span className="text-xs text-muted-foreground">{gridSize}px</span>
          </div>
          <Slider min={10} max={50} step={5} value={[gridSize]} onValueChange={([v]) => setGridSize(v)} />
        </div>
      </div>

      <Separator />

      <div className="space-y-3">
        <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">缩放</div>
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">当前缩放</span>
          <span className="text-xs font-mono">{Math.round(scale * 100)}%</span>
        </div>
        <Slider min={20} max={200} step={5} value={[Math.round(scale * 100)]} onValueChange={([v]) => setScale(v / 100)} />
      </div>

      <Separator />

      <div className="space-y-2">
        <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">统计</div>
        <div className="grid grid-cols-2 gap-2">
          <div className="bg-muted/50 rounded px-2 py-1.5">
            <div className="text-[10px] text-muted-foreground">图表</div>
            <div className="text-sm font-medium">{allCharts.filter(c => !c.chart_type?.startsWith('widget_')).length}</div>
          </div>
          <div className="bg-muted/50 rounded px-2 py-1.5">
            <div className="text-[10px] text-muted-foreground">控件</div>
            <div className="text-sm font-medium">{allCharts.filter(c => c.chart_type?.startsWith('widget_')).length}</div>
          </div>
        </div>
      </div>

      <p className="text-[10px] text-muted-foreground leading-relaxed">
        从左侧组件库拖入图表或控件到画布（也可点击添加）。点击画布上的元素可编辑其属性，按 Delete 键删除选中元素。所有操作暂存本地，点击「保存」提交。
      </p>
    </div>
  );
}
