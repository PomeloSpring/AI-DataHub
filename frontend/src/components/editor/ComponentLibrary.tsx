import { Layers, BarChart3, Settings, PanelLeftClose } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { CHART_TYPES, CHART_TYPE_CATEGORIES, ChartIcon, type ChartTypeItem } from '../DashboardChart';

interface Props {
  allCharts: any[];
  selectedChart: any;
  isOpen: boolean;
  onClose: () => void;
  onDragStart: (e: React.DragEvent, item: ChartTypeItem) => void;
  onDragEnd: () => void;
  onSelectChart: (chart: any) => void;
}

export default function ComponentLibrary({
  allCharts, selectedChart, isOpen, onClose, onDragStart, onDragEnd, onSelectChart,
}: Props) {
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

  return (
    <div className={`flex-shrink-0 flex flex-col border-r bg-muted/30 transition-all duration-200 overflow-hidden ${isOpen ? 'w-[260px]' : 'w-0'}`}>
      <div className="flex items-center justify-between p-3 border-b">
        <h3 className="font-semibold text-sm flex items-center gap-2">
          <Layers className="h-4 w-4" />
          组件库
        </h3>
        <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={onClose}>
          <PanelLeftClose className="h-4 w-4" />
        </Button>
      </div>

      <Tabs defaultValue="charts" className="flex-1 flex flex-col min-h-0">
        <TabsList className="w-full rounded-none border-b">
          <TabsTrigger value="charts" className="flex-1 text-xs">图表</TabsTrigger>
          <TabsTrigger value="widgets" className="flex-1 text-xs">控件</TabsTrigger>
        </TabsList>

        <TabsContent value="charts" className="flex-1 min-h-0 mt-0">
          <ScrollArea className="h-full p-2">
            {chartGroups.map(({ category, items }) => (
              <div key={category.key} className="mb-3">
                <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider px-2 mb-1.5">
                  {category.label}
                </div>
                <div className="grid grid-cols-2 gap-1">
                  {items.map(item => (
                    <div
                      key={item.value}
                      draggable
                      onDragStart={(e) => onDragStart(e, item)}
                      onDragEnd={onDragEnd}
                      className="flex items-center gap-1.5 px-2 py-1.5 rounded-md cursor-grab active:cursor-grabbing
                        border border-transparent hover:border-primary/30 hover:bg-accent/60 transition-colors select-none group"
                      title={`拖拽 ${item.label} 到画布`}
                    >
                      <ChartIcon name={item.icon} className="h-3.5 w-3.5 text-muted-foreground group-hover:text-primary flex-shrink-0" />
                      <span className="text-[11px] leading-tight truncate">{item.label}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </ScrollArea>
        </TabsContent>

        <TabsContent value="widgets" className="flex-1 min-h-0 mt-0">
          <ScrollArea className="h-full p-2">
            <div className="mb-2 px-2">
              <p className="text-[10px] text-muted-foreground">拖拽控件到画布，用于参数筛选和交互</p>
            </div>
            <div className="grid grid-cols-2 gap-1">
              {widgetItems.map(item => (
                <div
                  key={item.value}
                  draggable
                  onDragStart={(e) => onDragStart(e, item)}
                  onDragEnd={onDragEnd}
                  className="flex items-center gap-1.5 px-2 py-1.5 rounded-md cursor-grab active:cursor-grabbing
                    border border-transparent hover:border-primary/30 hover:bg-accent/60 transition-colors select-none group"
                  title={`拖拽 ${item.label} 到画布`}
                >
                  <ChartIcon name={item.icon} className="h-3.5 w-3.5 text-muted-foreground group-hover:text-primary flex-shrink-0" />
                  <span className="text-[11px] leading-tight truncate">{item.label}</span>
                </div>
              ))}
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>

      {/* Existing charts list */}
      <div className="border-t flex-shrink-0 max-h-[240px] flex flex-col overflow-hidden">
        <div className="p-2 flex flex-col min-h-0">
          <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider px-2 mb-1.5 flex-shrink-0">
            已添加 ({allCharts.length})
          </div>
          <ScrollArea className="flex-1 min-h-0">
            {allCharts.length === 0 ? (
              <div className="text-center py-4 text-muted-foreground text-xs">暂无组件</div>
            ) : (
              <div className="space-y-0.5">
                {allCharts.map(chart => {
                  const isWidget = chart.chart_type?.startsWith('widget_');
                  const isSelected = selectedChart?.id === chart.id;
                  const isNew = chart.id < 0;
                  return (
                    <div
                      key={chart.id}
                      className={`flex items-center gap-2 px-2 py-1.5 rounded-md cursor-pointer transition-colors ${
                        isSelected ? 'bg-primary/10 border border-primary/30' : 'hover:bg-muted border border-transparent'
                      }`}
                      onClick={() => onSelectChart(chart)}
                    >
                      {isWidget ? (
                        <Settings className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                      ) : (
                        <BarChart3 className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                      )}
                      <span className="text-xs truncate flex-1">{chart.name}</span>
                      {isNew && <Badge variant="secondary" className="text-[8px] px-1 py-0">新</Badge>}
                      <Badge variant="outline" className="text-[9px] px-1 py-0">
                        {isWidget ? '控件' : chart.chart_type}
                      </Badge>
                    </div>
                  );
                })}
              </div>
            )}
          </ScrollArea>
        </div>
      </div>
    </div>
  );
}
