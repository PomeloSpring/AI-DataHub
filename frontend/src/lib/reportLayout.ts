// 「用最新数据重新生成」的两类结局回显（报告中心专用，与后端判定一对一）。
//
// 后端产出时写死两份事实：layout_result（版式沿用没沿用）与 evidence_summary（证据成不成立），
// 这里只把它们翻成给用户看的文案：
// * 版式没达成必须显式说。旧口径不达版式就改产「平台事实表格」并标 ready，用户看到的是
//   “报告被改坏了”，而那张表又会被下一代重跑当成原版式基线（逐代劣化）；现在改为直接判失败。
// * 证据口径也只翻译不重算——旧实现把「尚未完成证据校验、不可对外发布」当常量印在所有报告上
//   （全仓从没实现过证据校验，degraded 是写死的），用户早当套话忽略，那是假降级。
import type { ReportEvidence, ReportLayoutStatus } from '@/api/report';
import i18n from '@/i18n';

/** 版本清单里的短标签（下拉一项放不下整句原因）；沿用版式是默认预期，不给徽章 */
export function layoutBadge(layout?: ReportLayoutStatus): string {
  return layout?.status === 'fact_only' || layout?.status === 'rejected' ? i18n.t('report:layout.notOriginalBadge') : '';
}

/** 未沿用原版式的完整说明（详情正文上方用，与徽章同一口径） */
export function layoutNote(layout?: ReportLayoutStatus): string {
  const reasons = (layout?.reasons || []).filter(Boolean);
  if (layout?.status === 'rejected') {
    // 2026-11 口径：不达版式 = 本轮失败，不拿事实表格充数。原因在上方 access_warning 里已经
    // 逐条带出（generation_failure_reason），这里只说结局与出路，不重复一遍免得看着像报错堆砌
    return i18n.t('report:layout.rejectedNote');
  }
  if (layout?.status !== 'fact_only') return '';
  // 存量历史版本：当时确实退回了平台事实报告，照实说（不改写历史）
  const reasonsPart = reasons.length
    ? `${i18n.t('report:layout.reasonPrefix')}${reasons.join(i18n.t('report:layout.reasonSeparator'))}`
    : '';
  return i18n.t('report:layout.factOnlyNote', { reasons: reasonsPart });
}

/** 证据口径说明：只翻译服务端判定。ready 且未截断 = 不打扰；其余都要显式说 */
export function evidenceNote(evidence?: ReportEvidence): string {
  const reasons = (evidence?.reasons || []).filter(Boolean);
  if (reasons.length) return i18n.t('report:layout.evidenceIncomplete', { reasons: reasons.join(i18n.t('report:layout.reasonSeparator')) });
  if (evidence?.completeness === 'sample_only') {
    return i18n.t('report:layout.evidenceSample', { count: evidence.query_count ?? 0 });
  }
  return '';
}
