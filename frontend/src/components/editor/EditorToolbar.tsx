import { Check, Minimize2, ZoomIn, ZoomOut, RotateCcw, Layout, PanelLeftOpen, PanelRightOpen } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Separator } from '@/components/ui/separator';

interface Props {
  dashboardName: string;
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
  onOpenTemplates: () => void;
  onSave: () => void;
  onExit: () => void;
  onOpenLeftPanel: () => void;
  onOpenRightPanel: () => void;
}

export default function EditorToolbar({
  dashboardName, scale, canvasSize, hasUnsavedChanges, pendingCount,
  pendingNewCount, pendingChangeCount, pendingDeleteCount,
  leftPanelOpen, rightPanelOpen,
  onZoomIn, onZoomOut, onResetZoom, onOpenTemplates, onSave, onExit,
  onOpenLeftPanel, onOpenRightPanel,
}: Props) {
  return (
    <div className="flex items-center justify-between px-3 py-2 border-b bg-muted/30 flex-shrink-0">
      <div className="flex items-center gap-2">
        {!leftPanelOpen && (
          <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={onOpenLeftPanel}>
            <PanelLeftOpen className="h-4 w-4" />
          </Button>
        )}
        <Badge variant="outline">{dashboardName}</Badge>
        <Separator orientation="vertical" className="h-5" />
        <span className="text-xs text-muted-foreground">
          {Math.round(scale * 100)}% | {canvasSize.width}×{canvasSize.height}
        </span>
        {hasUnsavedChanges && (
          <Badge variant="secondary" className="text-xs">
            {pendingNewCount > 0 && `${pendingNewCount} 个新增 `}
            {pendingChangeCount > 0 && `${pendingChangeCount} 个修改 `}
            {pendingDeleteCount > 0 && `${pendingDeleteCount} 个删除`}
            未保存
          </Badge>
        )}
      </div>

      <div className="flex items-center gap-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={onZoomOut}>
              <ZoomOut className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>缩小</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={onResetZoom}>
              <RotateCcw className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>适应全部</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={onZoomIn}>
              <ZoomIn className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>放大</TooltipContent>
        </Tooltip>

        <Separator orientation="vertical" className="h-5 mx-1" />

        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={onOpenTemplates}>
              <Layout className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>模板</TooltipContent>
        </Tooltip>

        {hasUnsavedChanges && (
          <Button size="sm" onClick={onSave}>
            <Check className="h-4 w-4 mr-1" />保存 ({pendingCount})
          </Button>
        )}
        <Button size="sm" variant={hasUnsavedChanges ? 'outline' : 'default'} onClick={onExit}>
          <Minimize2 className="h-4 w-4 mr-1" />退出编辑
        </Button>

        {!rightPanelOpen && (
          <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={onOpenRightPanel}>
            <PanelRightOpen className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
