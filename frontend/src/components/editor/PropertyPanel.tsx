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
import { CHART_TYPES, ChartIcon } from '../DashboardChart';
import { DEFAULT_CHART_SIZE, CANVAS_WIDTH, CANVAS_HEIGHT } from '../../hooks/useCanvasInteraction';

type ElementType = 'chart' | 'control' | 'canvas';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  selectedChart: any;
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
}

export default function PropertyPanel({
  isOpen, onClose, selectedChart, selectedElementType,
  canvasSize, setCanvasSize, canvasBgColor, setCanvasBgColor,
  gridSize, setGridSize, scale, setScale, allCharts,
  isNewChart, onPropertyChange, onPositionChange, onWidgetConfigChange,
  onDelete, onChartConfig,
}: Props) {
  return (
    <div className={`flex-shrink-0 flex flex-col border-l bg-muted/30 transition-all duration-200 overflow-hidden ${isOpen ? 'w-[300px]' : 'w-0'}`}>
      <div className="flex items-center justify-between p-3 border-b">
        <h3 className="font-semibold text-sm flex items-center gap-2">
          {selectedElementType === 'canvas' ? (
            <><Monitor className="h-4 w-4" />画布属性</>
          ) : selectedElementType === 'control' ? (
            <><Settings className="h-4 w-4" />控件属性</>
          ) : (
            <><BarChart3 className="h-4 w-4" />图表属性</>
          )}
        </h3>
        <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={onClose}>
          <PanelRightClose className="h-4 w-4" />
        </Button>
      </div>

      <ScrollArea className="flex-1 min-h-0 p-3">
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
                <Select value={selectedChart.chart_type}
                  onValueChange={v => onPropertyChange(selectedChart.id, 'chart_type', v)}>
                  <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CHART_TYPES.map(ct => (
                      <SelectItem key={ct.value} value={ct.value} className="text-xs">
                        <span className="flex items-center gap-2">
                          <ChartIcon name={ct.icon} className="h-3 w-3" />
                          {ct.label}
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <Separator />

            {/* Position & Size */}
            <div className="space-y-3">
              <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1">
                <Move className="h-3 w-3" /> 位置与大小
              </div>
              <div className="grid grid-cols-2 gap-2">
                {(['x', 'y', 'w', 'h'] as const).map(axis => (
                  <div key={axis} className="space-y-1">
                    <Label className="text-[10px] text-muted-foreground">{axis === 'x' ? 'X' : axis === 'y' ? 'Y' : axis === 'w' ? '宽度' : '高度'}</Label>
                    <Input type="number" className="h-7 text-xs"
                      value={selectedChart.position?.[axis] ?? (axis === 'w' ? DEFAULT_CHART_SIZE.w : axis === 'h' ? DEFAULT_CHART_SIZE.h : 0)}
                      onChange={e => onPositionChange(selectedChart.id, axis, Number(e.target.value) || 0)} />
                  </div>
                ))}
              </div>
            </div>

            <Separator />

            {/* Widget config */}
            {selectedChart.chart_type?.startsWith('widget_') && (
              <WidgetConfig
                chart={selectedChart}
                onWidgetConfigChange={onWidgetConfigChange}
              />
            )}

            <Separator />

            {/* Style settings */}
            <StyleSettings
              chart={selectedChart}
              onWidgetConfigChange={onWidgetConfigChange}
            />

            {/* Chart-specific: SQL info */}
            {!selectedChart.chart_type?.startsWith('widget_') && selectedChart.sql_query && (
              <>
                <Separator />
                <div className="space-y-2">
                  <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">数据源</div>
                  <pre className="text-[10px] mt-1 p-2 bg-muted rounded font-mono max-h-[80px] overflow-auto whitespace-pre-wrap">
                    {selectedChart.sql_query}
                  </pre>
                </div>
              </>
            )}

            <div className="h-2" />
          </div>
        ) : (
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
      </ScrollArea>

      {/* Fixed footer */}
      {selectedChart && selectedElementType !== 'canvas' && (
        <div className="flex-shrink-0 border-t p-3 space-y-2 bg-background">
          {!selectedChart.chart_type?.startsWith('widget_') && !isNewChart(selectedChart.id) && (
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
}

// ========== Sub-components ==========

function WidgetConfig({ chart, onWidgetConfigChange }: { chart: any; onWidgetConfigChange: (id: number, key: string, value: any) => void }) {
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

      {!['widget_label', 'widget_search', 'widget_reset', 'widget_export'].includes(chart.chart_type) && (
        <div className="space-y-2">
          <Label className="text-xs">参数 Key</Label>
          <Input className="h-7 text-xs font-mono"
            value={chart.config?.paramKey || ''}
            onChange={e => onWidgetConfigChange(chart.id, 'paramKey', e.target.value)}
            placeholder="如 site, date_start" />
        </div>
      )}

      <div className="space-y-2">
        <Label className="text-xs">
          {['widget_search', 'widget_reset', 'widget_export'].includes(chart.chart_type) ? '按钮文字' : chart.chart_type === 'widget_label' ? '名称' : '显示标签'}
        </Label>
        <Input className="h-7 text-xs"
          value={chart.config?.label || ''}
          onChange={e => onWidgetConfigChange(chart.id, 'label', e.target.value)} />
      </div>

      {!['widget_label', 'widget_search', 'widget_reset', 'widget_export'].includes(chart.chart_type) && (
        <>
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
        </>
      )}

      {(chart.chart_type === 'widget_select' || chart.chart_type === 'widget_multi_select') && (
        <div className="space-y-2">
          <Label className="text-xs">选项（逗号分隔）</Label>
          <Input className="h-7 text-xs"
            value={(chart.config?.options || []).map((o: any) => o.label || o.value).join(',')}
            onChange={e => {
              const opts = e.target.value.split(',').map(s => s.trim()).filter(Boolean)
                .map(s => ({ label: s, value: s }));
              onWidgetConfigChange(chart.id, 'options', opts);
            }}
            placeholder="北京,上海,广州" />
        </div>
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
        <Slider min={10} max={32} step={1}
          value={[ws.fontSize ?? 14]}
          onValueChange={([v]) => updateStyle('fontSize', v)} />
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
            onChange={e => { setCanvasBgColor(e.target.value); try { localStorage.setItem('editor_canvas_bg', e.target.value); } catch {} }}
            className="w-8 h-8 rounded cursor-pointer border" />
          <Input className="h-7 text-xs font-mono flex-1" value={canvasBgColor || ''}
            onChange={e => { setCanvasBgColor(e.target.value); try { localStorage.setItem('editor_canvas_bg', e.target.value); } catch {} }}
            placeholder="默认背景" />
          <Button variant="outline" size="sm" className="h-7 text-xs flex-shrink-0"
            onClick={() => { setCanvasBgColor(''); try { localStorage.removeItem('editor_canvas_bg'); } catch {} }}>
            重置
          </Button>
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
        从左侧组件库拖拽图表或控件到画布。点击画布上的元素可编辑其属性。按 Delete 键删除选中元素。所有操作暂存本地，点击「保存」提交。
      </p>
    </div>
  );
}
