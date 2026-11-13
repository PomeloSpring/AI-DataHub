import { useCallback, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { BarChart3, Info, LayoutDashboard, Search, Settings } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { hasPerm } from '@/stores/permissionStore';
import { fetchVisibleGroups, flattenGroupBoards, filterBoards, collectBoardTags,
  type GroupView, type BoardFilter } from '@/lib/dashboardCatalog';
import { BoardCard, GroupCard, groupEntryBoard } from '@/components/DashboardCatalogCards';
import { toast } from '@/lib/toast';
import { useVisLibrary } from '@/hooks/useVisLibrary';

// 工作空间「数据看板」= 组合卡片清单：一级看的是被授权的组合，点组合卡**直接进该组合的看板详情**
// （默认看板优先），组内浏览由详情页顶部的同组切换条/组下拉承担 —— 不再中间插一层看板卡。
// 旧版把组合做成一排过滤按钮，用户看不出“组合”是个可进入、可轮播的对象；上一版又多了一层看板列表，
// 两者都不得：组合 = 一个入口，点下去就是看板。
// 数据口径 = GET /dashboard/groups/visible(组按角色分配 ∩ 角色可见, fail-closed)；
// 浏览域只消费已启用看板 —— 设计中/已关闭看板不混进清单，但数量显式提示。
// 搜索是例外：跨组合直接找看板，命中即以看板卡呈现（找看板的意图不该被层级挡住）。
// 本页纯消费(查看/播放), 不提供编辑: 看板编辑只在系统配置「看板管理」(仅属主/admin 可写)。

export default function WorkspaceDashboards() {
  const { workspaceId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [groups, setGroups] = useState<GroupView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  // 标签筛选（与看板管理页同口径：独立下拉框，不是芯片行）：看板上量后按名字找不动，
  // 按「单App看板 / 三方集成App / 低样本」这类标签找才是真需求。只影响本页清单。
  const [tag, setTag] = useState('');
  const canManage = hasPerm('dashboard:manage');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setGroups(await fetchVisibleGroups());
    } catch {
      setError('加载看板清单失败，请重试');
      setGroups([]);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const boards = useMemo(() => flattenGroupBoards(groups), [groups]);
  const enabled = useMemo(() => boards.filter(b => b.status === 'enabled'), [boards]);
  const filter: BoardFilter = { query: search, tag };
  const filtering = Boolean(search.trim() || tag);
  // 搜索与标签同一套判据（与组内切换条、看板管理页同口径）：命中就以看板卡平铺，
  // 跨组合直接找板——找看板的意图不该被层级挡住。
  const hits = useMemo(() => (filtering ? filterBoards(enabled, filter) : []), [enabled, search, tag, filtering]);
  const allTags = useMemo(() => collectBoardTags(enabled), [enabled]);
  // 空结果文案要逐个说出刚筛了什么（只写一个条件等于把另一个条件隐掉）
  const hitsCondition = [search.trim() && `匹配「${search.trim()}」`, tag && `带「${tag}」标签`]
    .filter(Boolean).join(' 且 ') || '符合筛选条件';
  // 只列「组里有东西」的组合：全空壳的组合卡点了必然落到空态，不如不出现
  const shownGroups = useMemo(() => groups.filter(g => g.dashboards.length > 0), [groups]);
  // 「未分组」(id=0 兑底组) 不再当作一张组合卡：其看板平铺成单看板卡（用户拍板：未分组平铺，不塞进一个“未分组”组内）
  const realGroups = useMemo(() => shownGroups.filter(g => g.id !== 0), [shownGroups]);
  const ungroupedBoards = useMemo(
    () => flattenGroupBoards(groups.filter(g => g.id === 0)).filter(b => b.status === 'enabled'),
    [groups],
  );
  const unreadyCount = boards.length - enabled.length;
  // 字模库加载失败时缩影会退灰：必须显式告知，不能假装已按主题配色
  const library = useVisLibrary();

  const openBoard = (id: number) => navigate(`/ws/${workspaceId}/dashboards/${id}`, { state: { from: location.pathname } });
  // 点组合 = 直接进组内看板详情（默认看板优先）；空组不静默跳走，给出可见原因
  const enterGroup = (group: GroupView) => {
    const entry = groupEntryBoard(group);
    if (!entry) { toast(`组合「${group.name}」里还没有可看的看板，可联系管理员启用或编排看板。`); return; }
    openBoard(entry.id);
  };
  const playBoard = (id: number, groupId: number) => navigate(
    `/screen/${id}?group=${groupId}&from=${encodeURIComponent(location.pathname)}`, { state: { from: location.pathname } });
  const playGroup = (group: GroupView) => {
    // 轮播起点与“进入看板”同一个看板，否则点卡与点轮播看到的不是同一块屏
    const entry = groupEntryBoard(group);
    if (entry) playBoard(entry.id, group.id);
  };

  return <div className="mx-auto max-w-[1800px] space-y-4 p-5 md:p-6">
    {/* 页头压成一行：旧版“eyebrow + 2xl 大标题 + 说明句 + border-y 工具条”三叠，卡片还没开始看就已经滑掉小半屏；
        工作空间顶栏已有面包屑/模块切换，这里只需“锚点 + 标题 + 搜索 + 计数 + 管理入口”一行。 */}
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <div className="flex min-w-0 items-center gap-2.5">
        <span aria-hidden className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary ring-1 ring-primary/20">
          <BarChart3 className="h-4 w-4" />
        </span>
        <h1 className="shrink-0 text-lg font-semibold tracking-tight">数据看板</h1>
        <p className="hidden min-w-0 truncate text-xs text-muted-foreground xl:block">
          {filtering ? '搜索结果跨组合直接匹配看板，点卡片进入看板详情。' : '点组合卡直接进入该组合的看板，页内可切换同组看板。'}
        </p>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <div className="relative w-52 sm:w-64">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input aria-label="搜索看板" placeholder="搜索名称或描述…" className="h-8 pl-8 text-xs"
            value={search} onChange={e => setSearch(e.target.value)} />
        </div>
        {/* 标签下拉（与看板管理页、组内切换条同一判据）：无标签时置灰并说出原因，不做一个点开是空的死开关 */}
        <select aria-label="标签筛选" role="combobox" className="h-8 max-w-[11rem] rounded-md border bg-background px-2 text-xs"
          disabled={allTags.length === 0} value={tag} onChange={e => setTag(e.target.value)}>
          <option value="">{allTags.length === 0 ? '全部标签（暂无）' : '全部标签'}</option>
          {allTags.map(t => <option key={t} value={t}>#{t}</option>)}
        </select>
        <span className="whitespace-nowrap text-xs text-muted-foreground">
          {filtering ? `${hits.length} 个匹配看板` : `${realGroups.length} 个组合 · ${enabled.length} 个看板`}
        </span>
        {canManage && <Button size="sm" variant="outline" className="h-8 shrink-0" onClick={() => navigate('/system/dashboards')}>
          <Settings className="mr-1.5 h-3.5 w-3.5" />看板管理
        </Button>}
      </div>
      {/* 未启用/已关闭的可见看板显式提示数量: 不混进消费清单, 也不静默消失（留在页头内，不再开一条带边框的横栏） */}
      {!loading && !error && !!unreadyCount && <p className="flex w-full items-center gap-1.5 text-xs text-muted-foreground">
        <Info aria-hidden className="h-3.5 w-3.5 shrink-0" />
        另有 {unreadyCount} 个可见看板未启用（设计中/已关闭），不进入浏览清单；
        {canManage ? '可在「看板管理」中查看并启用。' : '可联系管理员启用后查看。'}
      </p>}
    </header>

    {loading ? <p role="status" className="text-sm text-muted-foreground">正在加载看板清单…</p>
      : error ? <div role="alert" className="flex flex-wrap items-center gap-3 rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-sm">
        {error}<Button size="sm" variant="outline" onClick={() => void load()}>重试</Button>
      </div>
      : !enabled.length ? <div className="rounded-xl border border-dashed py-24 text-center">
        <LayoutDashboard className="mx-auto mb-4 h-10 w-10 text-muted-foreground" />
        <p className="font-medium">还没有分配给你的看板</p>
        <p className="mt-2 text-sm text-muted-foreground">
          {canManage ? '在看板管理中创建看板、编排组合并分配角色后，这里就会出现。' : '当前角色没有被分配看板组合，可联系管理员为你的角色分配看板。'}
        </p>
        {canManage && <Button size="sm" className="mt-4" onClick={() => navigate('/system/dashboards')}>去配置仪表盘</Button>}
      </div>
      : <>
        {/* 字模库加载失败时缩影会退灰度：必须显式告知，不能假装已按主题配色 */}
        {library.error && <div role="alert" className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-amber-500/40 bg-amber-50 p-3 text-sm text-amber-800">
          字模库加载失败，卡片预览暂时使用灰度配色（与看板管理页不一致）。
          <Button size="sm" variant="outline" onClick={library.refresh}>重试</Button>
        </div>}
        {filtering ? (hits.length ? <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
          {hits.map(b => <BoardCard key={`${b.group_id}-${b.id}`} board={b} groupName={b.group_name}
            items={library.items} onOpen={openBoard} onPlay={id => playBoard(id, b.group_id)} />)}
        </div> : <div className="rounded-xl border border-dashed py-16 text-center text-sm text-muted-foreground">
          没有{hitsCondition}的看板，可换个关键词、选其他标签，或清空筛选按组合浏览。
        </div>) : <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
          {realGroups.map(g => <GroupCard key={g.id} group={g} items={library.items}
            onEnter={enterGroup} onPlay={playGroup} onOpenBoard={openBoard} />)}
          {/* 未分组看板：平铺为单看板卡（不塑进「未分组」组合卡内），group 上下文传 0 保证页内切换条/返回落点一致 */}
          {ungroupedBoards.map(b => <BoardCard key={`ung-${b.id}`} board={b}
            items={library.items} onOpen={openBoard} onPlay={id => playBoard(id, 0)} />)}
        </div>}
      </>}
  </div>;
}
