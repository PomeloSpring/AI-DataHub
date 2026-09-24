import { Check, Minimize2, PanelLeftOpen, PanelRightOpen, MoreHorizontal, Eye, Undo2, Redo2, ZoomIn, ZoomOut, Scan, Hand, MousePointer2, Grid3x3 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { PAGE_WIDTHS, PAGE_LABELS, type PageBreakpoint } from '@/lib/dashboardPageLayout';

interface Props {
  dashboardName: string;
  layoutMode: 'page' | 'screen';
  onLayoutMode: (mode: 'page' | 'screen') => void;
  breakpoint: PageBreakpoint;
  onBreakpoint: (bp: PageBreakpoint) => void;
  onResetPage: () => void;
  onCapturePage: () => void;
  scale: number;
  canvasSize: { width: number; height: number };
  hasUnsavedChanges: boolean;
  pendingCount: number;
  pendingNewCount: number;
  pendingChangeCount: number;
  pendingDeleteCount: number;
  leftPanelOpen: boolean;
  rightPanelOpen: boolean;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onResetZoom: () => void;
  onActualSize: () => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  panMode: boolean;
  onPanMode: (pan: boolean) => void;
  onOpenTemplates: () => void;
  onSave: () => void;
  onExit: () => void;
  onOpenLeftPanel: () => void;
  onOpenRightPanel: () => void;
  saving: boolean;
  showGrid: boolean;
  onToggleGrid: () => void;
  onPreview: () => void;
  onSaveVis: () => void;
  snapEnabled: boolean;
  onToggleSnap: () => void;
  onUseLibrary: () => void;
}

export default function EditorToolbar({
  dashboardName, scale, canvasSize, hasUnsavedChanges, pendingCount,
  layoutMode, onLayoutMode, breakpoint, onBreakpoint, onResetPage, onCapturePage,
  leftPanelOpen, rightPanelOpen,
  onZoomIn, onZoomOut, onResetZoom, onActualSize, canUndo, canRedo, onUndo, onRedo, panMode, onPanMode, onOpenTemplates, onSave, onExit,
  onOpenLeftPanel, onOpenRightPanel, saving, showGrid, onToggleGrid, onPreview, onSaveVis, snapEnabled, onToggleSnap, onUseLibrary,
}: Props) {
  return (
    <div className="flex flex-wrap items-center gap-2 px-2 py-2 border-b bg-background flex-shrink-0 min-w-0">
      <div className="flex items-center gap-2 min-w-0 flex-1">
        {!leftPanelOpen && (
          <Button variant="ghost" size="sm" aria-label="打开组件库" className="h-7 w-7 p-0 shrink-0" onClick={onOpenLeftPanel}>
            <PanelLeftOpen className="h-4 w-4" />
          </Button>
        )}
        <span className="hidden xl:block max-w-40 truncate text-sm" title={dashboardName}>{dashboardName}</span>
        <select aria-label="布局模式" disabled={saving} className="min-w-0 rounded border bg-background p-1 text-sm" value={layoutMode} onChange={e => onLayoutMode(e.target.value as 'page' | 'screen')}>
          <option value="page">页面布局</option><option value="screen">大屏布局</option>
        </select>
        {layoutMode === 'page' ? <select aria-label="设备断点" disabled={saving} className="min-w-0 rounded border bg-background p-1 text-sm" value={breakpoint} onChange={e => onBreakpoint(e.target.value as PageBreakpoint)}>
          {Object.entries(PAGE_LABELS).map(([bp, label]) => <option key={bp} value={bp}>{label} · {PAGE_WIDTHS[bp as PageBreakpoint]}px</option>)}
        </select> : <span className="hidden md:inline text-xs text-muted-foreground">{Math.round(scale * 100)}% | {canvasSize.width}×{canvasSize.height}</span>}
        {hasUnsavedChanges && <Badge variant="secondary" className="hidden lg:inline-flex shrink-0 text-xs">未保存 ({pendingCount})</Badge>}
      </div>

      <div role="toolbar" aria-label="画布编辑工具" className="order-last flex w-full items-center gap-1 overflow-x-auto xl:order-none xl:w-auto xl:shrink-0">
        <Button variant="ghost" size="sm" className="h-7 w-7 shrink-0 p-0" aria-label="撤销" title="回退 / 撤销（Ctrl/⌘+Z）；保存后以已保存内容为新基线" disabled={saving || !canUndo} onClick={onUndo}><Undo2 className="h-4 w-4" /></Button>
        <Button variant="ghost" size="sm" className="h-7 w-7 shrink-0 p-0" aria-label="重做" title="前进 / 重做（Ctrl/⌘+Shift+Z 或 Ctrl+Y）" disabled={saving || !canRedo} onClick={onRedo}><Redo2 className="h-4 w-4" /></Button>
        <span className="mx-1 h-4 border-l" />
        <Button variant={!panMode ? 'secondary' : 'ghost'} size="sm" className="h-7 w-7 shrink-0 p-0" aria-label="选择图表" title="选择图表（V）" aria-pressed={!panMode} disabled={saving} onClick={() => onPanMode(false)}><MousePointer2 className="h-4 w-4" /></Button>
        <Button variant={panMode ? 'secondary' : 'ghost'} size="sm" className="h-7 w-7 shrink-0 p-0" aria-label="拖动画布" title="拖动画布（H）；按住空格或中键可临时平移" aria-pressed={panMode} disabled={saving} onClick={() => onPanMode(true)}><Hand className="h-4 w-4" /></Button>
        <Button variant={showGrid ? 'secondary' : 'ghost'} size="sm" className="h-7 w-7 shrink-0 p-0" aria-label="网格线" title="显示 / 隐藏网格线（G）" aria-pressed={showGrid} onClick={onToggleGrid}><Grid3x3 className="h-4 w-4" /></Button>
        <span className="mx-1 h-4 border-l" />
        <Button variant="ghost" size="sm" className="h-7 w-7 shrink-0 p-0" aria-label="缩小画布" title="缩小画布（-）" disabled={saving} onClick={onZoomOut}><ZoomOut className="h-4 w-4" /></Button>
        <Button variant="ghost" size="sm" className="h-7 min-w-12 shrink-0 px-1 font-mono text-xs" aria-label="恢复原始比例" title="恢复 100% 比例" disabled={saving} onClick={onActualSize}>{Math.round(scale * 100)}%</Button>
        <Button variant="ghost" size="sm" className="h-7 w-7 shrink-0 p-0" aria-label="放大画布" title="放大画布（+）" disabled={saving} onClick={onZoomIn}><ZoomIn className="h-4 w-4" /></Button>
        <Button variant="ghost" size="sm" className="h-7 shrink-0 px-2" aria-label="适应编辑窗口" title="等比例适应编辑窗口（F）" disabled={saving} onClick={onResetZoom}><Scan className="h-4 w-4" /><span className="ml-1 text-xs">适应</span></Button>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <DropdownMenu><DropdownMenuTrigger asChild><Button variant="ghost" size="icon" aria-label="更多编辑操作"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {layoutMode === 'page' ? <><DropdownMenuItem disabled={saving} onSelect={onCapturePage}>固定当前页面布局到草稿</DropdownMenuItem><DropdownMenuItem disabled={saving} onSelect={onResetPage}>恢复当前断点自动适配</DropdownMenuItem></> : <DropdownMenuItem onSelect={onToggleSnap}>{snapEnabled ? '关闭' : '开启'}自动吸附</DropdownMenuItem>}
            <DropdownMenuSeparator /><DropdownMenuItem onSelect={onOpenTemplates}>模板</DropdownMenuItem>
            <DropdownMenuItem disabled={saving} onSelect={onUseLibrary}>全部使用字模</DropdownMenuItem><DropdownMenuItem disabled={saving} onSelect={onSaveVis}>存为字模</DropdownMenuItem>
            <DropdownMenuSeparator /><DropdownMenuItem disabled={saving} onSelect={onExit}>退出编辑</DropdownMenuItem>
          </DropdownMenuContent></DropdownMenu>
        <Button size="sm" variant="outline" aria-label="草稿预览" onClick={onPreview}><Eye className="h-4 w-4" /><span className="hidden md:inline ml-1">草稿预览</span></Button>
        <Button size="sm" aria-label="保存更改" onClick={onSave} disabled={saving || !hasUnsavedChanges}>
          <Check className="h-4 w-4" /><span className="hidden sm:inline ml-1">{saving ? '保存中…' : `保存 (${pendingCount})`}</span>
        </Button>
        <Button size="sm" className="hidden lg:inline-flex" variant={hasUnsavedChanges ? 'outline' : 'default'} onClick={onExit} disabled={saving}>
          <Minimize2 className="h-4 w-4 mr-1" />退出编辑
        </Button>

        {!rightPanelOpen && (
          <Button variant="ghost" size="sm" aria-label="打开属性面板" className="h-7 w-7 p-0" onClick={onOpenRightPanel}>
            <PanelRightOpen className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
