/**
 * 图谱重建按钮（「关系图谱」独立页与「本体建模」工作台共用同一实现）。
 *
 * 为什么抽出来：重建的作用域口径必须与**看图**口径同源——
 *   system → 系统图 ds:-1、business → 跨源聚合大图 ds:0、source/其它 → 本源图 ds:N。
 * 两处各写一份就会出现「徽章说需重建、按钮却重建了另一张图」的分叉（历史缺陷：
 * ds=0 且 kind 留空时服务端会把**全部 active 模型**塑进 ds:0，污染业务聚合图）。
 *
 * 纪律：canonical doc 仍是唯一可编辑源（ontology-modeling §1），本动作只重建派生的命名图，
 * 不写 doc；治理动作按 graph:manage 裁决（无权限不渲染必 403 的按钮）。
 */
import { useState } from 'react';
import client from '@/api/client';
import { Button } from '@/components/ui/button';
import { Loader2, RefreshCw } from 'lucide-react';
import { toast } from '@/lib/toast';
import { hasPerm } from '@/stores/permissionStore';
import { useTranslation } from 'react-i18next';

export type GraphScope = {
  datasource_id: number;
  system_scope: boolean;
  kind: string;
};

/** 重建作用域：与 ModelGraphTab / OntologyGraphView 的看图口径保持一致。 */
export function graphScopeForModel(kind?: string, datasourceId?: number): GraphScope {
  const ds = Number(datasourceId || 0);
  if (kind === 'system') return { datasource_id: 0, system_scope: true, kind };
  if (kind === 'business') return { datasource_id: 0, system_scope: false, kind };
  if (kind === 'source') return { datasource_id: ds, system_scope: false, kind };
  return { datasource_id: ds, system_scope: false, kind: '' };
}

export default function GraphRebuildButton({ kind, datasourceId, onRebuilt, className }: {
  kind?: string;
  datasourceId?: number;
  /** 重建成功后回调：调用方据此重取图/结构化视图/状态徽章（图里的滞后值会变） */
  onRebuilt?: () => void;
  className?: string;
}) {
  const { t } = useTranslation('ui');
  const [building, setBuilding] = useState(false);
  // 治理动作按 graph:manage 裁决（无权限不给点了必 403 的按钮）
  if (!hasPerm('graph:manage')) return null;

  const scope = graphScopeForModel(kind, datasourceId);
  const scopeLabel = kind === 'system' ? t('k_d1eb9b5b59')
    : kind === 'business' ? t('k_bbee149234')
      : kind === 'source' ? t('k_6721362c7e', { p0: datasourceId || 0 })
        : t('k_d912de7356', { p0: datasourceId || 0 });

  const run = async () => {
    setBuilding(true);
    try {
      const { data } = await client.post('/graph/sync', null, { params: scope });
      if (data?.success === false) throw new Error(data?.message || t('k_46c6521935'));
      toast.success(data?.message || t('k_a00040e6ac'));
      onRebuilt?.();
    } catch (e: any) {
      // 409=同图正在生成、503=互斥锁不可用：服务端原文案透传，不当成功也不静默重试
      toast.error(e?.response?.data?.detail || e?.message || t('k_75c414fe36'));
    } finally {
      setBuilding(false);
    }
  };

  return (
    <Button size="sm" variant="outline" className={className} onClick={run} disabled={building}
      title={t('k_6633708f34', { p0: scopeLabel })}>
      {building
        ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
        : <RefreshCw className="h-3.5 w-3.5 mr-1" />}
      {t('k_800021e226')}
    </Button>
  );
}