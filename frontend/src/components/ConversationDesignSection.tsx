/**
 * 会话关联的仪表盘设计卡片区 + 设计面板。
 *
 * AS-BOT 侧栏与工作空间 Chat 共用同一实现（会话合并共用后，设计能力随会话走、
 * 不随入口走——两个入口只是位置不同，交互保持一致）。
 */
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import DashboardDesignPanel from './asbot/DashboardDesignPanel';
import { DESIGN_STATUS, listDesigns, type DashboardDesign } from '@/api/dashboardDesign';

export default function ConversationDesignSection({
  conversationId,
  refreshSignal = 0,
  disabled = false,
  onContinue,
}: {
  /** 当前会话 id；为空时不加载（新对话未落库） */
  conversationId: number | null;
  /** 刷新信号（如 messages.length）：一轮对话完成后重新拉取设计列表 */
  refreshSignal?: number;
  disabled?: boolean;
  onContinue?: (text: string) => void;
}) {
  const [designs, setDesigns] = useState<DashboardDesign[]>([]);
  const [activeDesignId, setActiveDesignId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!conversationId) {
      setDesigns([]);
      return;
    }
    try {
      setDesigns(await listDesigns(conversationId));
    } catch {
      // 设计列表不可用不阻断对话主流程；卡片区静默为空
      setDesigns([]);
    }
  }, [conversationId]);

  useEffect(() => {
    void reload();
  }, [reload, refreshSignal]);

  return (
    <>
      {designs.filter(design => design.status !== 'cancelled').map(design => (
        <div key={design.design_id} className="rounded-lg border bg-card p-3 space-y-2">
          <p className="text-sm font-medium">仪表盘设计 · {design.name || design.request.slice(0, 30)}</p>
          <p className="text-xs text-muted-foreground">
            {DESIGN_STATUS[design.status] || design.status} · 版本 {design.version}
          </p>
          <p className="text-xs">
            {design.selection ? `业务域：${design.selection.datasource_name}` : '请先选择目标仪表盘、业务范围和知识库'}
          </p>
          <Button size="sm" variant="outline" disabled={disabled}
            onClick={() => setActiveDesignId(design.design_id)}>打开设计面板</Button>
        </div>
      ))}

      {activeDesignId && (
        <DashboardDesignPanel
          key={activeDesignId}
          designId={activeDesignId}
          onClose={() => setActiveDesignId(null)}
          onChanged={() => void reload()}
          onContinue={text => {
            setActiveDesignId(null);
            onContinue?.(text);
          }}
        />
      )}
    </>
  );
}
