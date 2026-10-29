import { useState, useEffect, useMemo, type ReactNode } from 'react';
import { useDraggable } from '@dnd-kit/core';
import { VIS_CATEGORIES, type VisComponent } from '@/api/visLibrary';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import VisComponentPreview, { ShapePreview, typeLabelsOf } from '@/components/VisComponentPreview';
import { compatible, resolveDashboardDesign, chartZIndex, isChartHidden, isChartLocked } from '@/lib/dashboardDesign';
import { libraryDragId, type LibraryDragData } from '@/lib/editorDrag';
import { Layers, BarChart3, Settings, PanelLeftClose, Database, Download, ChevronUp, ChevronDown, Eye, EyeOff, Lock, LockOpen } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import client from '@/api/client';
import { CHART_TYPES, CHART_TYPE_CATEGORIES, ChartIcon, type ChartTypeItem } from '../DashboardChart';

const CHART_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  CHART_TYPES.map(t => [t.value, t.label]),
);

/**
 * 库项统一走 dnd-kit 拖拽（Pointer + Keyboard sensor），不再用 HTML5 DnD：
 * 后者在触摸设备上完全不工作，库项也没有点击/键盘添加的兜底。
 */
function Draggable({ id, data, onAdd, title, className, children }: {
  id: string; data: LibraryDragData; onAdd: () => void; title: string; className: string; children: ReactNode;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id, data });
  return <div ref={setNodeRef} {...listeners} {...attributes} onClick={onAdd} role="button" tabIndex={0}
    aria-label={`${title}（拖入画布或按回车添加）`}
    className={`${className} cursor-grab active:cursor-grabbing select-none ${isDragging ? 'opacity-40' : ''}`}>
    {children}
  </div>;
}

interface Props {
  allCharts: any[];
  selectedChart: any;
  isOpen: boolean;
  onClose: () => void;
  /** 点选/回车添加（与拖入同一结果） */
  onAddItem: (item: ChartTypeItem, component?: VisComponent) => void;
  onSelectChart: (chart: any) => void;
  onApplyVis: (component: VisComponent) => void;
  onAddFromDataset: (dataset: any) => void;
  design: ReturnType<typeof resolveDashboardDesign>;
  /** 当前布局模式：仅大屏模式支持叠放顺序 */
  layoutMode: 'page' | 'screen';
  /** 受控页签：空画布引导「从数据集引入」要能把面板切到数据集页 */
  tab: string;
  onTab: (tab: string) => void;
  /** 已有未保存修改的图表 id（图层列表标「改」） */
  dirtyIds: number[];
  onLayerAction: (chart: any, action: 'up' | 'down' | 'hide' | 'show' | 'lock' | 'unlock' | 'rename', name?: string) => void;
}

export default function ComponentLibrary({
  allCharts, selectedChart, isOpen, onClose, onAddItem, onSelectChart, onApplyVis, onAddFromDataset, design, layoutMode, dirtyIds, onLayerAction, tab, onTab,
}: Props) {
  const library = useVisLibrary();
  const [category, setCategory] = useState('theme_pack');
  const [renaming, setRenaming] = useState<number | null>(null);
  // 图层列表按叠放顺序倒序展示（上层在上），与 Photoshop/Figma 习惯一致
  const layerOrder = useMemo(() => allCharts.slice().sort((a, b) => chartZIndex(b) - chartZIndex(a) || b.id - a.id), [allCharts]);
  // 从数据集引入: 按 name 显示(ui-resource-display), 点「引入」生成预设图表卡片
  const [datasets, setDatasets] = useState<any[]>([]);
  const [datasetsLoading, setDatasetsLoading] = useState(false);
  useEffect(() => {
    if (!isOpen) return;
    setDatasetsLoading(true);
    client.get('/datasets/')
      .then(({ data }) => setDatasets(data.items || []))
      .catch(() => setDatasets([]))
      .finally(() => setDatasetsLoading(false));
  }, [isOpen]);
  const chartGroups = (() => {
    const groups: { category: typeof CHART_TYPE_CATEGORIES[number]; items: ChartTypeItem[] }[] = [];
    for (const cat of CHART_TYPE_CATEGORIES) {
      if (cat.key === 'widget') continue;
      const items = CHART_TYPES.filter(t => t.category === cat.key);
      if (items.length > 0) groups.push({ category: cat, items });
    }
    return groups;
  })();

  const widgetItems = CHART_TYPES.filter(t => t.category === 'widget');
  const preview = (item: ChartTypeItem) => {
    const styles = item.category === 'widget' ? design.pack?.widgets : design.pack?.charts;
    return <div className="w-full overflow-hidden rounded" style={{ background: design.card.cardBg || (design.visual.mode === 'dark' ? '#0f172a' : '#ffffff') }}>
      <ShapePreview type={item.value} palette={design.visual.palette || []} cfg={{ ...styles?.default, ...styles?.[item.value] }} />
    </div>;
  };

  return (
    <div className={`flex-shrink-0 flex flex-col border-r bg-background transition-all duration-200 overflow-hidden max-lg:absolute max-lg:inset-y-0 max-lg:left-0 max-lg:z-30 ${isOpen ? 'w-[260px]' : 'w-0 border-r-0'}`}>
      <div className="flex items-center justify-between p-3 border-b">
        <h3 className="font-semibold text-sm flex items-center gap-2">
          <Layers className="h-4 w-4" />
          UI 字模库
        </h3>
        <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={onClose}>
          <PanelLeftClose className="h-4 w-4" />
        </Button>
      </div>

      <Tabs value={tab} onValueChange={onTab} className="flex-1 flex flex-col min-h-0">
        <TabsList className="w-full rounded-none border-b">
          <TabsTrigger value="vis" className="flex-1 text-xs">字模</TabsTrigger>
          <TabsTrigger value="charts" className="flex-1 text-xs">图表</TabsTrigger>
          <TabsTrigger value="datasets" className="flex-1 text-xs">数据集</TabsTrigger>
          <TabsTrigger value="widgets" className="flex-1 text-xs">控件</TabsTrigger>
          <TabsTrigger value="layers" className="flex-1 text-xs">图层</TabsTrigger>
        </TabsList>

        <TabsContent value="vis" className="flex-1 min-h-0 mt-0 overflow-auto p-3 space-y-3">
          <select aria-label="字模分类" className="w-full rounded border bg-background p-2 text-xs" value={category} onChange={e => setCategory(e.target.value)}>
            {VIS_CATEGORIES.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
          </select>
          <p className="text-xs text-muted-foreground">{category === 'theme_pack' ? '主题包=一整套风格(背景+卡片+全部图表/控件)，点击一键套用。' : '点击应用到选中元素；图表字模可拖入新建。布局应用前可预览。'}</p>
          {library.error && <div role="alert" className="text-xs text-destructive">{library.error}<Button variant="link" onClick={library.refresh}>重试</Button></div>}
          {library.loading && <p className="text-xs">正在加载字模…</p>}
          {library.items.filter(c => c.category === category).map(c => {
            // chart_style / kpi_card 可拖入新建图表；其余分类只能应用到选中元素
            const creatable = ['chart_style', 'kpi_card'].includes(c.category);
            const item = creatable ? CHART_TYPES.find(t => t.value === (c.category === 'kpi_card' ? 'big_number_trend' : c.chart_type)) : undefined;
            // 与当前选中元素不兼容的字模置灰并说明原因，不得让用户点了才知道挂不上
            const incompatible = !!selectedChart && !compatible(c, selectedChart.chart_type) && !creatable;
            const body = <>
              <VisComponentPreview c={c} />
              <span className="mt-1 block text-xs font-medium">{c.name}</span>
              <span className="text-[10px] text-muted-foreground">{c.is_builtin ? '内置' : '自定义'} · {typeLabelsOf(c.chart_type)}{item ? ' · 可拖入新建' : ''}</span>
              {incompatible && <span className="mt-0.5 block text-[10px] text-amber-600">与选中的「{CHART_TYPES.find(t => t.value === selectedChart.chart_type)?.label || selectedChart.chart_type}」不匹配</span>}
            </>;
            return item
              ? <Draggable key={c.id} id={libraryDragId('vis', c.id)} data={{ item, component: c }}
                  onAdd={() => onApplyVis(c)} title={c.name}
                  className="block w-full rounded-lg border p-2 text-left hover:border-primary/50">{body}</Draggable>
              : <button key={c.id} type="button" disabled={incompatible}
                  className={`block w-full rounded-lg border p-2 text-left hover:border-foreground/50 ${incompatible ? 'opacity-50 hover:border' : ''}`}
                  onClick={() => onApplyVis(c)}>{body}</button>;
          })}
          {!library.loading && !library.error && !library.items.some(c => c.category === category) && <p className="text-xs">该分类暂无字模</p>}
        </TabsContent>
        <TabsContent value="charts" className="flex-1 min-h-0 mt-0">
          <ScrollArea className="h-full p-2">
            {chartGroups.map(({ category, items }) => (
              <div key={category.key} className="mb-3">
                <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider px-2 mb-1.5">
                  {category.label}
                </div>
                <div className="grid grid-cols-2 gap-1">
                  {items.map(item => (
                    <Draggable key={item.value} id={libraryDragId('chart', item.value)} data={{ item }}
                      onAdd={() => onAddItem(item)} title={item.label}
                      className="flex flex-col items-center gap-1.5 px-2 py-1.5 rounded-md border border-transparent hover:border-primary/30 hover:bg-accent/60 transition-colors">
                      {preview(item)}
                      <ChartIcon name={item.icon} className="h-3.5 w-3.5 text-muted-foreground" />
                      <span className="text-[11px] leading-tight truncate">{item.label}</span>
                    </Draggable>
                  ))}
                </div>
              </div>
            ))}
          </ScrollArea>
        </TabsContent>

        {/* 从数据集引入: 一键生成已配置图表(类型/字段映射取数据集预设) */}
        <TabsContent value="datasets" className="flex-1 min-h-0 mt-0 overflow-auto">
          <div className="p-2">
            <p className="text-[10px] text-muted-foreground px-2 mb-1.5">
              从数据集引入：按预设图表类型/字段映射一键生成图表卡片，取数走数据集统一治理入口
            </p>
            {datasetsLoading ? (
              <p className="text-xs px-2 text-muted-foreground">正在加载数据集…</p>
            ) : datasets.length === 0 ? (
              <p className="text-xs px-2 text-muted-foreground">暂无数据集，请先在「数据集」页创建</p>
            ) : (
              <div className="space-y-0.5">
                {datasets.map(ds => {
                  // 后端已按「可用范围 ∩ 当前角色授权源 ≠ ∅」过滤清单；仍留一道显式禁用，
                  // 避免旧响应/缓存把挂了必报错的空承诺拖到画布上
                  const usable = (ds.usable_now || []).length > 0;
                  return (
                    <div key={ds.id} className="flex items-center gap-2 px-2 py-1.5 rounded-md border border-transparent hover:bg-muted">
                      <Database className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                      <div className="flex-1 min-w-0">
                        <div className="text-xs truncate">{ds.name}</div>
                        <div className="text-[10px] text-muted-foreground truncate">
                          {ds.chart_type ? (CHART_TYPE_LABELS[ds.chart_type] || ds.chart_type) : '未预设图表类型(默认柱状图)'}
                          {(ds.available_source_names || []).length > 1 && (
                            <span className="ml-1">· 可用 {(ds.available_source_names || []).length} 源</span>
                          )}
                        </div>
                      </div>
                      <Button size="sm" variant="outline" className="h-6 px-2 text-[11px] flex-shrink-0"
                        disabled={!usable}
                        title={usable ? undefined : '当前角色无该数据集任何可用数据源的权限，请在数据集页扩大范围或申请源授权'}
                        onClick={() => onAddFromDataset(ds)}>
                        <Download className="h-3 w-3 mr-1" /> 引入
                      </Button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </TabsContent>

        <TabsContent value="widgets" className="flex-1 min-h-0 mt-0">
          <ScrollArea className="h-full p-2">
            <div className="mb-2 px-2">
              <p className="text-[10px] text-muted-foreground">拖入或点击添加到画布，用于参数筛选和交互</p>
            </div>
            <div className="grid grid-cols-2 gap-1">
              {widgetItems.map(item => (
                <Draggable key={item.value} id={libraryDragId('chart', item.value)} data={{ item }}
                  onAdd={() => onAddItem(item)} title={item.label}
                  className="flex flex-col items-center gap-1.5 px-2 py-1.5 rounded-md border border-transparent hover:border-primary/30 hover:bg-accent/60 transition-colors">
                  {preview(item)}
                  <ChartIcon name={item.icon} className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-[11px] leading-tight truncate">{item.label}</span>
                </Draggable>
              ))}
            </div>
          </ScrollArea>
        </TabsContent>
      {/* 图层面板：叠放顺序 / 显隐 / 锁定 / 就地重命名，并标注未保存状态 */}
      <TabsContent value="layers" className="flex-1 min-h-0 overflow-auto">
      <div className="p-2 flex flex-col min-h-0">
        <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider px-2 mb-1.5 flex-shrink-0">
          图层 ({allCharts.length})
        </div>
        <ScrollArea className="flex-1 min-h-0">
          {allCharts.length === 0 ? (
            <div className="text-center py-4 text-muted-foreground text-xs">暂无组件</div>
          ) : (
            <div className="space-y-0.5">
              {layerOrder.map(chart => {
                const isWidget = chart.chart_type?.startsWith('widget_');
                const isSelected = selectedChart?.id === chart.id;
                const hidden = isChartHidden(chart), locked = isChartLocked(chart);
                return (
                  <div key={chart.id}
                    className={`flex items-center gap-1 px-2 py-1.5 rounded-md transition-colors ${
                      isSelected ? 'bg-primary/10 border border-primary/30' : 'hover:bg-muted border border-transparent'
                    } ${hidden ? 'opacity-50' : ''}`}
                    onClick={() => onSelectChart(chart)}>
                    {isWidget ? <Settings className="h-3 w-3 flex-shrink-0 text-muted-foreground" /> : <BarChart3 className="h-3 w-3 flex-shrink-0 text-muted-foreground" />}
                    {renaming === chart.id
                      ? <input autoFocus className="h-5 flex-1 min-w-0 rounded border bg-background px-1 text-xs"
                          defaultValue={chart.name}
                          onClick={e => e.stopPropagation()}
                          onBlur={e => { setRenaming(null); const name = e.target.value.trim(); if (name && name !== chart.name) onLayerAction(chart, 'rename', name); }}
                          onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setRenaming(null); }} />
                      : <span className="text-xs truncate flex-1" title={`${chart.name}（双击重命名）`} onDoubleClick={() => setRenaming(chart.id)}>{chart.name}</span>}
                    {chart.id < 0 && <Badge variant="secondary" className="px-1 py-0 text-[8px]">新</Badge>}
                    {chart.id >= 0 && dirtyIds.includes(chart.id) && <Badge variant="secondary" className="px-1 py-0 text-[8px]">改</Badge>}
                    <span className="flex flex-shrink-0 items-center" onClick={e => e.stopPropagation()}>
                      <Button variant="ghost" size="sm" className="h-5 w-5 p-0" aria-label="上移一层" title="上移一层" disabled={layoutMode !== 'screen'}
                        onClick={() => onLayerAction(chart, 'up')}><ChevronUp className="h-3 w-3" /></Button>
                      <Button variant="ghost" size="sm" className="h-5 w-5 p-0" aria-label="下移一层" title="下移一层" disabled={layoutMode !== 'screen'}
                        onClick={() => onLayerAction(chart, 'down')}><ChevronDown className="h-3 w-3" /></Button>
                      <Button variant="ghost" size="sm" className="h-5 w-5 p-0" aria-label={hidden ? '显示' : '隐藏'} title={hidden ? '显示' : '隐藏（仅阅读态生效）'}
                        onClick={() => onLayerAction(chart, hidden ? 'show' : 'hide')}>{hidden ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}</Button>
                      <Button variant="ghost" size="sm" className="h-5 w-5 p-0" aria-label={locked ? '解锁' : '锁定'} title={locked ? '解锁后可拖动' : '锁定位置'}
                        onClick={() => onLayerAction(chart, locked ? 'unlock' : 'lock')}>{locked ? <Lock className="h-3 w-3" /> : <LockOpen className="h-3 w-3" />}</Button>
                    </span>
                  </div>
                );
              })}
            </div>
          )}
          {layoutMode !== 'screen' && <p className="px-2 pt-2 text-[10px] text-muted-foreground">叠放顺序仅大屏模式适用；网格模式由行列决层。</p>}
        </ScrollArea>
      </div>
      </TabsContent>
      </Tabs>
    </div>
  );
}
