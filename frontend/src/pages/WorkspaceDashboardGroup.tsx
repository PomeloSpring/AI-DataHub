import { useCallback, useEffect, useMemo, useState } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { fetchVisibleGroups, type GroupView } from '@/lib/dashboardCatalog';
import { groupEntryBoard } from '@/components/DashboardCatalogCards';

// 组合入口：/ws/:ws/dashboards/group/:groupId → **直接跳该组合的看板详情**（默认看板优先）。
// 与组合卡点击同一语义：组合是一个入口，点下去就是看板，中间不再插一层看板列表。
// 本页只负责解析目标与把失败暴露出来（组不存在/已收回/组内无可看看板都显式说明，
// 不静默跳到别的组合或"看全部"——可见集由服务端 fail-closed 裁决）。
// 保留这条路由是为了可分享/可收藏的「打开某个组合」链接（含旧域 /dashboards/<groupId> 的重定向）。

export default function WorkspaceDashboardGroup() {
  const { t } = useTranslation(['dashboard', 'common']);
  const { workspaceId, groupId } = useParams();
  const navigate = useNavigate();
  const [groups, setGroups] = useState<GroupView[] | null>(null);
  const [error, setError] = useState('');
  // 「未分组」兜底组 id = 0，因此不能用 falsy 判断缺失
  const id = Number(groupId);

  const load = useCallback(async () => {
    setGroups(null);
    setError('');
    try {
      setGroups(await fetchVisibleGroups());
    } catch {
      setError(t('browse.groupLoadFailed'));
    }
  }, [t]);
  useEffect(() => { void load(); }, [load]);

  const group = useMemo(() => (groups || []).find(g => g.id === id) || null, [groups, id]);
  const entry = group ? groupEntryBoard(group) : undefined;
  const listPath = `/ws/${workspaceId}/dashboards`;

  if (!error && group && entry)
    return <Navigate to={`/ws/${workspaceId}/dashboards/${entry.id}`} replace />;

  const reason = error ? t('browse.reasonLoadFailed')
    : groups && !group ? t('browse.reasonMissing')
      : groups && group && !entry ? t('browse.reasonEmpty', { name: group.name })
        : t('browse.reasonOpening');

  return <div className="mx-auto max-w-[1800px] space-y-4 p-5 md:p-8">
    <div role={error || groups ? 'alert' : undefined} className="rounded-xl border border-dashed py-20 text-center">
      <p className="font-medium">{reason}</p>
      <p className="mt-2 text-sm text-muted-foreground">
        {error ? t('browse.hintError')
          : groups ? t('browse.hintMissing')
            : ''}
      </p>
      <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
        <Button size="sm" variant="outline" onClick={() => navigate(listPath)}>{t('browse.backToGroups')}</Button>
        {!!error && <Button size="sm" onClick={() => void load()}>{t('common:action.retry')}</Button>}
      </div>
    </div>
  </div>;
}
