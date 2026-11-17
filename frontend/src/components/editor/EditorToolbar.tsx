import { Check, Minimize2, PanelLeftOpen, PanelRightOpen, MoreHorizontal, Eye, Undo2, Redo2, ZoomIn, ZoomOut, Scan, Hand, MousePointer2, Grid3x3, Play, MousePointerClick, Ruler, SlidersHorizontal, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { PAGE_WIDTHS, PAGE_LABELS, type PageBreakpoint } from '@/lib/dashboardPageLayout';
import { t } from '@/i18n/runtime';

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
  /** 点开变更清单（badge 不再只是一个不可交互的数字） */
  onOpenChanges: () => void;
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
  /** select=控件 inert（默认，可正常选中/拖动）；interact=试运行，控件真实联动 */
  interactionMode: 'select' | 'interact';
  onInteractionMode: (mode: 'select' | 'interact') => void;
  onOpenTemplates: () => void;
  /** 打开「看板参数」管理区：遗留 params 过去只能在列表页设置弹窗改，编辑器里看不见（无法操作） */
  onOpenParams: () => void;
  /** 打开「批量切换执行数据源」：把目标源一次性钉到数据集支持它的图表上 */
  onOpenSourceSwitch: () => void;
  /** 没有被画布控件驱动的参数个数（>0 就意味着页面上会长出一个顶部输入口） */
  paramsUncontrolled: number;
  onSave: () => void;
  onExit: () => void;
  onOpenLeftPanel: () => void;
  onOpenRightPanel: () => void;
  saving: boolean;
  showGrid: boolean;
  onToggleGrid: () => void;
  showRulers: boolean;
  onToggleRulers: () => void;
  onPreview: () => void;
  onSaveVis: () => void;
  snapEnabled: boolean;
  onToggleSnap: () => void;
  onUseLibrary: () => void;
}

export default function EditorToolbar({
  dashboardName, scale, canvasSize, hasUnsavedChanges, pendingCount, onOpenChanges,
  layoutMode, onLayoutMode, breakpoint, onBreakpoint, onResetPage, onCapturePage,
  leftPanelOpen, rightPanelOpen,
  onZoomIn, onZoomOut, onResetZoom, onActualSize, canUndo, canRedo, onUndo, onRedo, panMode, onPanMode, interactionMode, onInteractionMode, onOpenTemplates, onSave, onExit,
  onOpenLeftPanel, onOpenRightPanel, saving, showGrid, onToggleGrid, showRulers, onToggleRulers, onPreview, onSaveVis, snapEnabled, onToggleSnap, onUseLibrary,
  onOpenParams, onOpenSourceSwitch, paramsUncontrolled,
}: Props) {
  return (
    <div className="flex flex-wrap items-center gap-2 px-2 py-2 border-b bg-background flex-shrink-0 min-w-0">
      <div className="flex items-center gap-2 min-w-0 flex-1">
        {!leftPanelOpen && (
          <Button variant="ghost" size="sm" aria-label={t("k_9a8b09c7e0")} className="h-7 w-7 p-0 shrink-0" onClick={onOpenLeftPanel}>
            <PanelLeftOpen className="h-4 w-4" />
          </Button>
        )}
        <span className="hidden xl:block max-w-40 truncate text-sm" title={dashboardName}>{dashboardName}</span>
        <select aria-label={t("k_c6b568b516")} disabled={saving} className="min-w-0 rounded border bg-background p-1 text-sm" value={layoutMode} onChange={e => onLayoutMode(e.target.value as 'page' | 'screen')}>
          <option value="page">{t("k_56cb71ddba")}</option><option value="screen">{t("k_a20fe87020")}</option>
        </select>
        {layoutMode === 'page' ? <select aria-label={t("k_d27f3f7148")} disabled={saving} className="min-w-0 rounded border bg-background p-1 text-sm" value={breakpoint} onChange={e => onBreakpoint(e.target.value as PageBreakpoint)}>
          {Object.entries(PAGE_LABELS).map(([bp, label]) => <option key={bp} value={bp}>{label} · {PAGE_WIDTHS[bp as PageBreakpoint]}px</option>)}
        </select> : <span className="hidden md:inline text-xs text-muted-foreground">{Math.round(scale * 100)}% | {canvasSize.width}×{canvasSize.height}</span>}
        {hasUnsavedChanges && <button type="button" className="shrink-0" onClick={onOpenChanges} title={t("k_d51bb449fc")}>
          <Badge variant="secondary" className="hidden cursor-pointer text-xs lg:inline-flex hover:underline">未保存 ({pendingCount})</Badge>
        </button>}
      </div>

      <div role="toolbar" aria-label={t("k_45e1fa5647")} className="order-last flex w-full items-center gap-1 overflow-x-auto xl:order-none xl:w-auto xl:shrink-0">
        <Button variant="ghost" size="sm" className="h-7 w-7 shrink-0 p-0" aria-label={t("k_9fcefd8dc8")} title="回退 / 撤销（Ctrl/⌘+Z）；保存后以已保存内容为新基线" disabled={saving || !canUndo} onClick={onUndo}><Undo2 className="h-4 w-4" /></Button>
        <Button variant="ghost" size="sm" className="h-7 w-7 shrink-0 p-0" aria-label={t("k_1238f0d363")} title="前进 / 重做（Ctrl/⌘+Shift+Z 或 Ctrl+Y）" disabled={saving || !canRedo} onClick={onRedo}><Redo2 className="h-4 w-4" /></Button>
        <span className="mx-1 h-4 border-l" />
        <Button variant={!panMode ? 'secondary' : 'ghost'} size="sm" className="h-7 w-7 shrink-0 p-0" aria-label={t("k_d547d97b46")} title={t("k_de0850ee4c")} aria-pressed={!panMode} disabled={saving} onClick={() => onPanMode(false)}><MousePointer2 className="h-4 w-4" /></Button>
        <Button variant={panMode ? 'secondary' : 'ghost'} size="sm" className="h-7 w-7 shrink-0 p-0" aria-label={t("k_ab34cd249b")} title={t("k_a35e788872")} aria-pressed={panMode} disabled={saving} onClick={() => onPanMode(true)}><Hand className="h-4 w-4" /></Button>
        <Button variant={showGrid ? 'secondary' : 'ghost'} size="sm" className="h-7 w-7 shrink-0 p-0" aria-label={t("k_4bf2f800ba")} title={t("k_2cf7b7f5e2")} aria-pressed={showGrid} onClick={onToggleGrid}><Grid3x3 className="h-4 w-4" /></Button>
        <Button variant={showRulers ? 'secondary' : 'ghost'} size="sm" className="h-7 w-7 shrink-0 p-0" aria-label={t("k_1280951970")} title={t("k_9061a0870c")} aria-pressed={showRulers} onClick={onToggleRulers}><Ruler className="h-4 w-4" /></Button>
        <Button variant={interactionMode === 'interact' ? 'secondary' : 'ghost'} size="sm" className="h-7 shrink-0 px-2" aria-label={t("k_ff237757b4")} title={t("k_25f762b754")} aria-pressed={interactionMode === 'interact'} disabled={saving} onClick={() => onInteractionMode(interactionMode === 'interact' ? 'select' : 'interact')}>
          {interactionMode === 'interact' ? <Play className="h-4 w-4" /> : <MousePointerClick className="h-4 w-4" />}<span className="ml-1 text-xs">{t("k_2d0d3fe949")}</span>
        </Button>
        <span className="mx-1 h-4 border-l" />
        <Button variant="ghost" size="sm" className="h-7 w-7 shrink-0 p-0" aria-label={t("k_b7f9e266e1")} title={t("k_fd0d73da18")} disabled={saving} onClick={onZoomOut}><ZoomOut className="h-4 w-4" /></Button>
        <Button variant="ghost" size="sm" className="h-7 min-w-12 shrink-0 px-1 font-mono text-xs" aria-label={t("k_dede906356")} title={t("k_f1bbbf3481")} disabled={saving} onClick={onActualSize}>{Math.round(scale * 100)}%</Button>
        <Button variant="ghost" size="sm" className="h-7 w-7 shrink-0 p-0" aria-label={t("k_55c0d6cce3")} title="放大画布（+）" disabled={saving} onClick={onZoomIn}><ZoomIn className="h-4 w-4" /></Button>
        <Button variant="ghost" size="sm" className="h-7 shrink-0 px-2" aria-label={t("k_f424c22ed9")} title={t("k_3edb836d49")} disabled={saving} onClick={onResetZoom}><Scan className="h-4 w-4" /><span className="ml-1 text-xs">{t("k_a4fe592642")}</span></Button>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <DropdownMenu><DropdownMenuTrigger asChild><Button variant="ghost" size="icon" aria-label={t("k_ae0faa182d")}><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {layoutMode === 'page' ? <><DropdownMenuItem disabled={saving} onSelect={onCapturePage}>{t("k_e944e01a19")}</DropdownMenuItem><DropdownMenuItem disabled={saving} onSelect={onResetPage}>{t("k_b0b837db00")}</DropdownMenuItem></> : <DropdownMenuItem onSelect={onToggleSnap}>{snapEnabled ? '关闭' : '开启'}自动吸附</DropdownMenuItem>}
            <DropdownMenuSeparator /><DropdownMenuItem onSelect={onOpenTemplates}>{t("k_06d0f38dd2")}</DropdownMenuItem>
            <DropdownMenuItem disabled={saving} onSelect={onOpenSourceSwitch}><RefreshCw className="h-4 w-4 mr-2" />{t("k_cadc055362")}</DropdownMenuItem>
            <DropdownMenuItem disabled={saving} onSelect={onUseLibrary}>{t("k_5f5df7ec7f")}</DropdownMenuItem><DropdownMenuItem disabled={saving} onSelect={onSaveVis}>{t("k_bc21a6255a")}</DropdownMenuItem>
            <DropdownMenuSeparator /><DropdownMenuItem disabled={saving} onSelect={onExit}>{t("k_bbbcc7565b")}</DropdownMenuItem>
          </DropdownMenuContent></DropdownMenu>
        <Button size="sm" variant="outline" aria-label={t("k_9c967cfb7e")} title={paramsUncontrolled
          ? `${paramsUncontrolled} 个参数没有画布控件驱动，会在页面顶部出现输入口`
          : '所有参数都由画布控件驱动，顶部不会重复出现输入口'}
          onClick={onOpenParams}>
          <SlidersHorizontal className="h-4 w-4" /><span className="hidden md:inline ml-1">看板参数
            {paramsUncontrolled > 0 && <span className="ml-1 rounded bg-amber-100 px-1 text-[10px] text-amber-900 dark:bg-amber-900/40 dark:text-amber-200">{paramsUncontrolled}</span>}</span>
        </Button>
        <Button size="sm" variant="outline" aria-label={t("k_1525ef5f38")} onClick={onPreview}><Eye className="h-4 w-4" /><span className="hidden md:inline ml-1">{t("k_1525ef5f38")}</span></Button>
        <Button size="sm" aria-label={t("k_cd5cd93ce1")} onClick={onSave} disabled={saving || !hasUnsavedChanges}>
          <Check className="h-4 w-4" /><span className="hidden sm:inline ml-1">{saving ? '保存中…' : `保存 (${pendingCount})`}</span>
        </Button>
        <Button size="sm" className="hidden lg:inline-flex" variant={hasUnsavedChanges ? 'outline' : 'default'} onClick={onExit} disabled={saving}>
          <Minimize2 className="h-4 w-4 mr-1" />退出编辑
        </Button>

        {!rightPanelOpen && (
          <Button variant="ghost" size="sm" aria-label={t("k_d2c374abbf")} className="h-7 w-7 p-0" onClick={onOpenRightPanel}>
            <PanelRightOpen className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
