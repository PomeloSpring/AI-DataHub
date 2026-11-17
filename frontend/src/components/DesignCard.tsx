/**
 * 历史消息里的 ```design 块 —— **不再是"设计卡片"**。
 *
 * 设计流程已退役（2026-11-17 拍板：会话内不再打开设计面板、不再让人填/确认 SQL、
 * AI 也不再输出 ```design 块）。这里只剩一个残留用途：那份设计如果**已经落成了看板**，
 * 给一条「预览看板」的入口。
 *
 * 所以：没有看板的设计（已取消/未发布）**整块不渲染** —— 界面上不再出现
 * "仪表盘设计 · 名称 / 状态 · 版本 / 请先选择业务范围"这类卡片，也不再摆一个
 * 点了没有结果的按钮；有看板的设计也只渲染一个按钮，不重复贴设计元信息。
 * 设计入口读取失败给一行可重试的小字（不静默吞掉入口，也不做降级掩盖）。
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import DashboardPreviewDialog from './asbot/DashboardPreviewDialog';
import { designDashboardId, getDesign, type DashboardDesign } from '@/api/dashboardDesign';

export default function DesignCard({
  designId,
}: {
  /** ```design 块携带的设计 ID */
  designId: string;
}) {
  const { t } = useTranslation('chat');
  const [design, setDesign] = useState<DashboardDesign | null>(null);
  const [failed, setFailed] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);

  const reload = useCallback(async () => {
    setFailed(false);
    try {
      setDesign(await getDesign(designId));
    } catch {
      // 设计不可用(已删除/无权)时不能假装没有入口：给一行可重试的小字
      setDesign(null);
      setFailed(true);
    }
  }, [designId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // 已发布 → result.dashboard_id（老版本只有 result.url，从路径解析）；追加/改图 → selection.dashboard_id
  const dashboardId = designDashboardId(design);

  if (!dashboardId) {
    return failed ? (
      <button type="button" className="text-xs text-muted-foreground underline"
        onClick={() => { void reload(); }}>{t('designCard.unavailable')}</button>
    ) : null;
  }

  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setPreviewOpen(true)}>{t('designCard.preview')}</Button>
      {previewOpen ? (
        <DashboardPreviewDialog
          key={dashboardId}
          dashboardId={dashboardId}
          onClose={() => setPreviewOpen(false)}
        />
      ) : null}
    </>
  );
}