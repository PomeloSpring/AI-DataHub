/**
 * 内嵌于回答的设计卡片 — LLM 在需要用户操作设计面板时于回答中输出 ```design 块,
 * 本组件在该位置渲染卡片并挂载设计面板(不常驻、不集中列出,随消息持久化与回放)。
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import DashboardDesignPanel from './asbot/DashboardDesignPanel';
import { designStatusLabel, getDesign, type DashboardDesign } from '@/api/dashboardDesign';

export default function DesignCard({
  designId,
  onContinue,
}: {
  /** ```design 块携带的设计 ID */
  designId: string;
  /** 面板「返回对话继续完善」时回传的追问文本 */
  onContinue?: (text: string) => void;
}) {
  const { t } = useTranslation('chat');
  const [design, setDesign] = useState<DashboardDesign | null>(null);
  const [error, setError] = useState('');
  const [panelOpen, setPanelOpen] = useState(false);

  const reload = useCallback(async () => {
    setError('');
    try {
      setDesign(await getDesign(designId));
    } catch {
      // 设计不可用须显式可见(可能已删除/无权),不静默消失也不裸显 ID
      setDesign(null);
      setError(t('designCard.unavailable'));
    }
  }, [designId, t]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <>
      <div className="rounded-lg border bg-card p-3 space-y-2">
        {error ? (
          <p className="text-xs text-muted-foreground" title={designId}>{error}</p>
        ) : !design ? (
          <p className="text-xs text-muted-foreground">{t('designCard.loading')}</p>
        ) : (
          <>
            <p className="text-sm font-medium">{t('designCard.title', { name: design.name || design.request.slice(0, 30) })}</p>
            <p className="text-xs text-muted-foreground">
              {t('designCard.statusVersion', { status: designStatusLabel(design.status), version: design.version })}
            </p>
            <p className="text-xs">
              {design.selection ? t('designCard.selection', { name: design.selection.datasource_name }) : t('designCard.selectionEmpty')}
            </p>
            <Button size="sm" variant="outline" onClick={() => setPanelOpen(true)}>{t('designCard.openPanel')}</Button>
          </>
        )}
      </div>

      {panelOpen && (
        <DashboardDesignPanel
          key={designId}
          designId={designId}
          onClose={() => setPanelOpen(false)}
          onChanged={() => void reload()}
          onContinue={text => {
            setPanelOpen(false);
            onContinue?.(text);
          }}
        />
      )}
    </>
  );
}
