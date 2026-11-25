/**
 * 同步知识库按钮（本体建模工作台工具栏）— 把生效模型的脱敏上云文档推送到目标知识库。
 *
 * 为什么收在本页：本条原先只在「关系图谱」独立页的工具栏里（那张图现在已是本体建模的
 * graph 页签），用户要在两个页面之间来回；派生重建动作（图谱生成 / 同步知识库）与
 * 状态徽章（SyncBadges，30s 轮询）本就该同屏——做完立刻看到回显，不用猜推没推到。
 *
 * 口径：
 *   · 治理动作按 kb:manage 裁决（无权限不渲染点了必 403 的按钮）；
 *   · 仅 active 模型可同步（draft/archived 服务端会 skip），按钮禁用并给原因；
 *   · 目标库默认按模型 kind 路由（业务/系统→全局库、源本体不同步），也可一次性定向推送到所选库；
 *   · 成败必须把**库名**说给用户（只报“同步了 1 个”等于没说去了哪）。
 */
import { useEffect, useMemo, useState } from 'react';
import { ontologyApi } from '@/api/ontology';
import { Button } from '@/components/ui/button';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Loader2, UploadCloud } from 'lucide-react';
import { toast } from '@/lib/toast';
import { hasPerm } from '@/stores/permissionStore';
import { useTranslation } from 'react-i18next';
import { t as i18nTr } from '@/i18n/runtime';

/** 知识库同步跳过原因 → 可读文案（不向用户直接回英文码） */
const KB_SKIP_LABEL: Record<string, string> = {
  model_not_found: i18nTr('k_88ef9e602a'),
  no_kb_bound: i18nTr('k_4899f0779a'),
  no_kb_with_sync_ontology: i18nTr('k_3071c5e8db'),
  // 手动选库但目标库已被删/不可用（不静默换库，显式回错）
  specified_kb_unavailable: i18nTr('k_4d1ff7968b'),
  status_draft: i18nTr('k_343a6fc70c'),
  status_archived: i18nTr('k_e2abed3ee1'),
  superseded_by_active_version: i18nTr('k_1e43830379'),
  no_md_content: i18nTr('k_9b2a743eaf'),
};

export default function KbSyncButton({ modelId, active, onSynced, className }: {
  modelId: number;
  /** 仅生效模型可同步（draft/archived 服务端会 skip，这里显式禁用并给原因） */
  active: boolean;
  /** 同步动作结束后回调（无论成败）：调用方据此重取状态徽章 */
  onSynced?: () => void;
  className?: string;
}) {
  const { t } = useTranslation('ui');
  const canSync = hasPerm('kb:manage');
  const [options, setOptions] = useState<{ id: number; name: string }[]>([]);
  const [optionsFailed, setOptionsFailed] = useState(false);
  // '0'=按模型 kind 默认路由；选中某库=一次性定向推送到该库（含源本体手动例外）
  const [targetKbId, setTargetKbId] = useState<string>('0');
  const [syncing, setSyncing] = useState(false);

  useEffect(() => {
    if (!canSync) return;
    let cancelled = false;
    ontologyApi.kbSyncOptions()
      .then(({ data }) => {
        if (cancelled) return;
        setOptions(data.items || []);
        setOptionsFailed(false);
      })
      .catch(() => {
        // 候选库拉不到不阻断默认路由同步，但必须标出来（下拉里给可见提示，不装成“没有库”）
        if (!cancelled) { setOptions([]); setOptionsFailed(true); }
      });
    return () => { cancelled = true; };
  }, [canSync]);

  // 目标库是一次性选择：切模型回到默认路由，不把 A 模型的选择带到 B 模型上
  useEffect(() => { setTargetKbId('0'); }, [modelId]);

  const targetName = useMemo(
    () => (targetKbId !== '0' ? options.find((k) => String(k.id) === targetKbId)?.name || '' : ''),
    [targetKbId, options]);

  if (!canSync) return null;

  const run = async () => {
    setSyncing(true);
    try {
      const { data } = await ontologyApi.syncKb(modelId, targetKbId !== '0' ? Number(targetKbId) : null);
      const names = (data.targets || []).join(' / ');
      if (data.removed) toast.success(t('k_3d2270d690', { p0: data.removed, p1: names ? `（${names}）` : '' }));
      else if (data.synced && data.error) toast.warning(t('k_730e1fa099', { p0: data.synced, p1: names ? `（${names}）` : '', p2: data.error }));
      else if (data.synced) toast.success(t('k_55326b89f6', { p0: names || data.synced }));
      else if (data.error) toast.error(t('k_c7afee6143', { p0: data.error }));
      else toast.warning(data.skipped ? t('k_0b8625abe0', { p0: KB_SKIP_LABEL[data.skipped] || data.skipped }) : t('k_0844deea63'));
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_feb50f1fda'));
    } finally {
      setSyncing(false);
      // 成败都要重取状态徽章：失败时徽章也应回显服务端的真实状态
      onSynced?.();
    }
  };

  return (
    <>
      <Select value={targetKbId} onValueChange={setTargetKbId} disabled={syncing}>
        <SelectTrigger className="w-44 h-8 text-xs"
          title={optionsFailed ? t('k_5c1d9f0a7b') : t('k_343e6fa5bb')}>
          <SelectValue placeholder={t('k_85bab14432')} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="0">{t('k_85bab14432')}</SelectItem>
          {options.map((kb) => (
            <SelectItem key={kb.id} value={String(kb.id)}>
              <span className="truncate max-w-[12rem]">{kb.name}</span>
            </SelectItem>
          ))}
          {optionsFailed && (
            <SelectItem value="__options_failed" disabled>{t('k_5c1d9f0a7b')}</SelectItem>
          )}
        </SelectContent>
      </Select>
      <Button size="sm" variant="outline" className={className} onClick={run}
        disabled={syncing || !active}
        title={!active
          ? t('k_e0c202beb0')
          : (targetKbId !== '0' && targetName
            ? t('k_bb6f402308', { p0: targetName })
            : t('k_1c44d22877'))}>
        {syncing
          ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
          : <UploadCloud className="h-3.5 w-3.5 mr-1" />}
        {t('k_010f64ab9b')}
      </Button>
    </>
  );
}