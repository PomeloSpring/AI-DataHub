import { useState, useCallback, useMemo } from 'react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  DASHBOARD_TEMPLATES, TEMPLATE_CANVAS, validateTemplates,
  type DashboardTemplate, type TemplateChartDef,
} from '@/lib/dashboardTemplates';
import { ChartGlyph } from '@/components/ChartGlyph';
import { readChartPalette } from '@/lib/chartTheme';
import { resolveDashboardDesign } from '@/lib/dashboardDesign';
import { useThemeStore } from '@/stores/themeStore';
import type { VisComponent } from '@/api/visLibrary';

/**
 * 模板选择弹窗。模板定义与示例数据唯一来源 `lib/dashboardTemplates.ts`；
 * 模板定位是**图表样式展示**（不携带 SQL，数据是内置演示数值），这里只负责挑选与预览。
 * 预览配色/底色走 `resolveDashboardDesign`（与看板/大屏渲染同源，模板本体不带样式，
 * ui-vis-library-style.md §1：预览与真实渲染不一致就是缺陷）。
 */

interface DashboardTemplatesProps {
  open: boolean;
  onClose: () => void;
  onApply: (template: DashboardTemplate) => void;
  /** 字模库清单（由页面传入，与看板同源取主题包色板；未传那么退主题令牌） */
  items?: VisComponent[];
}

/** 模板缩略示意：按真实栅格画卡片 + 图表类型符号，让“导入后长什么样”一眼可见 */
function TemplatePreview({ template, items }: { template: DashboardTemplate; items: VisComponent[] }) {
  const theme = useThemeStore(s => s.theme);
  const design = useMemo(() => resolveDashboardDesign({}, items, { theme }), [items, theme]);
  const dark = design.visual.mode === 'dark';
  const palette = design.visual.palette?.length ? design.visual.palette : readChartPalette(dark);
  const scale = 460 / template.canvas.width;
  const tileBg = design.card.cardBg || design.visual.viewBg || (dark ? '#111827' : 'hsl(var(--card))');
  return (
    <div className="relative w-full overflow-hidden rounded-md border" data-testid="template-preview"
      style={{ aspectRatio: `${template.canvas.width} / ${template.canvas.height}`,
        backgroundColor: design.background.backgroundColor || design.visual.viewBg || 'hsl(var(--muted))',
        backgroundImage: design.background.backgroundImage, backgroundSize: design.background.backgroundSize }}>
      {template.charts.map((chart: TemplateChartDef) => (
        <div key={chart.name} className="absolute overflow-hidden rounded-sm border p-1"
          style={{
            left: chart.position.x * scale, top: chart.position.y * scale,
            width: chart.position.w * scale, height: chart.position.h * scale,
            background: tileBg, borderColor: dark ? 'rgba(255,255,255,0.12)' : 'hsl(var(--border))',
          }}>
          <div className="truncate text-[7px] leading-3" style={{ color: design.visual.subTextColor || (dark ? '#cbd5e1' : 'hsl(var(--muted-foreground))') }}>{chart.name}</div>
          <div className="h-[calc(100%-14px)]"><ChartGlyph chartType={chart.chart_type} palette={palette} /></div>
        </div>
      ))}
      {!template.charts.length && <span className="absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">空白画布</span>}
    </div>
  );
}

export default function DashboardTemplates({ open, onClose, onApply, items = [] }: DashboardTemplatesProps) {
  const [selected, setSelected] = useState<DashboardTemplate | null>(null);
  const [searchText, setSearchText] = useState('');

  // 模板写坏（假类型/越界/示例数据缺列）时不静默渲出一张残缺卡片：弹窗顶部显式提示
  const templateErrors = useMemo(() => (open ? validateTemplates() : []), [open]);
  const filtered = useMemo(() => {
    const keyword = searchText.trim().toLowerCase();
    return DASHBOARD_TEMPLATES.filter(t => !keyword
      || `${t.name} ${t.description} ${t.scenario} ${t.tags.join(' ')}`.toLowerCase().includes(keyword));
  }, [searchText]);

  const handleApply = useCallback(() => {
    if (!selected) return;
    if (templateErrors.length) {
      toast.error(`模板定义有问题：${templateErrors[0]}`, { duration: 8000 });
      return;
    }
    onApply(selected);
    onClose();
    setSelected(null);
    const sampleCount = selected.charts.filter(c => c.sample).length;
    if (sampleCount) {
      toast.info(`已按模板创建 ${sampleCount} 个图卡：内置示例数据仅用于展示样式，不跑取数查询；要接真实数据请在「数据与字段配置」里写 SQL 或语义查询`, { duration: 8000 });
    }
  }, [selected, templateErrors, onApply, onClose]);

  const chartCount = (t: DashboardTemplate) => t.charts.filter(c => !c.chart_type.startsWith('widget_')).length;

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-[900px]">
        <DialogHeader>
          <DialogTitle>选择仪表盘模板</DialogTitle>
        </DialogHeader>

        {templateErrors.length > 0 && (
          <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            模板定义校验未通过（{templateErrors.length} 项），暂不可导入：{templateErrors[0]}
          </div>
        )}

        <div className="mb-3 flex flex-wrap items-center gap-3">
          <Input
            placeholder="搜索模板名称、场景或标签…"
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            className="max-w-[320px]"
          />
          <span className="text-xs text-muted-foreground">
            共 {DASHBOARD_TEMPLATES.length} 套 · 画布 {TEMPLATE_CANVAS.width} × {TEMPLATE_CANVAS.height} · 图表内置示例数据（只看样式，不取数）
          </span>
        </div>

        {filtered.length === 0 ? (
          <div className="py-12 text-center text-muted-foreground">未找到匹配的模板</div>
        ) : (
          <ScrollArea className="h-[440px] pr-3">
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
              {filtered.map(template => {
                const Icon = template.icon;
                const active = selected?.id === template.id;
                return (
                  <button
                    type="button"
                    key={template.id}
                    data-testid={`template-card-${template.id}`}
                    aria-pressed={active}
                    onClick={() => setSelected(template)}
                    className={`rounded-lg border p-3 text-left transition-all ${
                      active ? 'border-primary bg-primary/5' : 'border-border hover:border-primary/50'
                    }`}
                  >
                    <div className="mb-2 flex items-start gap-3">
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted">
                        <Icon className="h-5 w-5 text-primary" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <h3 className="truncate font-semibold text-sm">{template.name}</h3>
                        <p className="mt-0.5 text-xs text-muted-foreground">{template.scenario}</p>
                      </div>
                    </div>
                    <TemplatePreview template={template} items={items} />
                    <p className="mt-2 line-clamp-2 text-xs text-muted-foreground">{template.description}</p>
                    <div className="mt-2 flex flex-wrap items-center gap-1">
                      {template.tags.map(tag => <Badge key={tag} variant="outline" className="text-[10px]">{tag}</Badge>)}
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {chartCount(template) > 0 ? `${chartCount(template)} 个图卡 · 内置示例数据` : '空白画布'}
                    </p>
                  </button>
                );
              })}
            </div>
          </ScrollArea>
        )}

        {selected && (
          <div className="mt-4 flex items-center justify-between gap-3 rounded-lg bg-muted p-4">
            <div className="min-w-0">
              <span className="font-medium">已选择：{selected.name}</span>
              <span className="ml-2 text-sm text-muted-foreground">
                {selected.charts.length > 0
                  ? `将创建 ${chartCount(selected)} 个图卡，以内置示例数据展示样式`
                  : '空白看板'}
              </span>
            </div>
            <Button onClick={handleApply} disabled={!!templateErrors.length}>使用此模板</Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
