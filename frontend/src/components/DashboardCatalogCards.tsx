import { Layers, Play } from 'lucide-react';
import { DashboardThumbnail } from '@/components/DashboardCanvas';
import { DashboardThumbnailFrame } from '@/components/DashboardThumbnailFrame';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { VisComponent } from '@/api/visLibrary';
import type { DashboardPreview, GroupBoard, GroupView } from '@/lib/dashboardCatalog';
import type { Dashboard } from '@/stores/dashboardStore';

// 工作空间「数据看板」的共享卡片：组合卡（一级）+ 看板卡（搜索结果）。
// 组合卡点下去 = 直接进该组合的看板详情（组内浏览由详情页的切换条承担），不再中间插一层看板列表。
// 两个层级必须用同一张看板卡 + 同一个缩略图渲染器/固定框（DashboardThumbnail / DashboardThumbnailFrame），
// 与系统配置「看板管理」也共用这套尺寸口径，不得各画一套几何。

/**
 * 目录预览摘要 → 唯一渲染器 DashboardThumbnail 的输入。
 *
 * 两个看板列表必须用同一个组件、同一个布局源（resolvePageLayout / getCanvas）：
 * 此前这里自画一套 blocks 画布，而后端按 `position` 像素算 blocks 不读 `config.pageLayout`，
 * 于是 page 布局的看板在本页退化成大屏像素图（内容只占顶部一小块），
 * 与系统配置「看板管理」的效果图对不上 —— 同一数据不应有两套几何。
 */
export function previewSource(preview?: DashboardPreview): Pick<Dashboard, 'filters' | 'charts'> {
  return {
    filters: { design: { canvas: { width: preview?.width, height: preview?.height }, page: preview?.page || undefined } },
    charts: (preview?.charts || []).map(c => ({
      id: c.id, chart_type: c.chart_type, position: c.position,
      config: { ...(c.pageLayout ? { pageLayout: c.pageLayout } : {}), ...(c.hidden ? { hidden: true } : {}) },
    })) as unknown as Dashboard['charts'],
  };
}

/** 组内"进入"目标看板：默认看板优先，其次首个已启用看板（服务端目录已按 is_default → sort_order 排） */
export function groupEntryBoard(group: GroupView): GroupBoard | undefined {
  const enabled = group.dashboards.filter(d => d.status === 'enabled');
  return enabled.find(d => d.is_default) || enabled[0];
}

/** 单看板卡片：清单页搜索结果用 */
export function BoardCard({ board, groupName, items, onOpen, onPlay }: {
  board: GroupBoard; groupName?: string; items: VisComponent[];
  onOpen: (id: number) => void; onPlay: (id: number) => void;
}) {
  const source = previewSource(board.preview);
  return <article className="group overflow-hidden rounded-xl border bg-card transition-shadow hover:shadow-md">
    <button type="button" aria-label={`打开看板 ${board.name}`} className="block w-full border-b text-left"
      onClick={() => onOpen(board.id)}>
      <DashboardThumbnailFrame dashboard={source}>
        <DashboardThumbnail dashboard={source} items={items} />
      </DashboardThumbnailFrame>
    </button>
    <div className="space-y-2.5 p-3">
      <div className="min-w-0">
        <h2 className="truncate text-sm font-semibold" title={board.name}>{board.name}</h2>
        <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">{board.description || '暂无描述'}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {/* 跨组合列看板时标出归属 */}
        {!!groupName && <Badge variant="secondary">{groupName}</Badge>}
        {!!board.is_default && <Badge variant="outline">默认</Badge>}
        <span className="text-muted-foreground">{board.chart_count ?? 0} 个图表</span>
        <span className="ml-auto text-muted-foreground">更新于 {board.updated_at ? new Date(board.updated_at).toLocaleString('zh-CN') : '—'}</span>
      </div>
      <div className="flex gap-2 border-t pt-2.5">
        <Button size="sm" className="flex-1" onClick={() => onOpen(board.id)}>进入看板</Button>
        <Button size="sm" variant="outline" title="大屏播放" aria-label={`大屏播放 ${board.name}`} onClick={() => onPlay(board.id)}>
          <Play className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  </article>;
}

/** 组合卡片：点封面/按钮直接进入组内看板详情（不是再看一层看板卡），组内切换由详情页切换条承担 */
export function GroupCard({ group, items, onEnter, onPlay, onOpenBoard }: {
  group: GroupView; items: VisComponent[];
  onEnter: (group: GroupView) => void; onPlay: (group: GroupView) => void; onOpenBoard: (id: number) => void;
}) {
  const boards = group.dashboards.filter(d => d.status === 'enabled');
  const unready = group.dashboards.length - boards.length;
  const entry = groupEntryBoard(group);
  const source = entry ? previewSource(entry.preview) : null;
  return <article className="group overflow-hidden rounded-xl border bg-card transition-shadow hover:shadow-md">
    <button type="button" aria-label={`进入组合 ${group.name}`} className="block w-full border-b text-left"
      onClick={() => onEnter(group)}>
      <DashboardThumbnailFrame dashboard={source || { filters: {}, charts: [] }}>
        {source ? <DashboardThumbnail dashboard={source} items={items} />
          : <span className="flex h-40 flex-col items-center justify-center gap-1 text-xs text-muted-foreground">
            <Layers className="h-5 w-5" />组合内暂无可看看板
          </span>}
      </DashboardThumbnailFrame>
    </button>
    <div className="space-y-2.5 p-3">
      <div className="min-w-0">
        <h2 className="truncate text-sm font-semibold" title={group.name}>{group.name}</h2>
        <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">{group.description || '暂无描述'}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge variant="secondary">{boards.length} 个可看看板</Badge>
        {/* 未启用/已关闭不混进浏览集，但数量必须显式可见，不能静默消失 */}
        {!!unready && <Badge variant="outline">{unready} 个未启用</Badge>}
      </div>
      {!!boards.length && <ul className="flex flex-wrap items-center gap-1.5">
        {boards.slice(0, 3).map(b => <li key={b.id}>
          <button type="button" className="max-w-[9rem] truncate rounded-md bg-muted/60 px-2 py-0.5 text-[11px] hover:bg-muted"
            title={b.name} onClick={() => onOpenBoard(b.id)}>{b.name}</button>
        </li>)}
        {boards.length > 3 && <li className="text-[11px] text-muted-foreground">等 {boards.length} 个</li>}
      </ul>}
      <div className="flex gap-2 border-t pt-2.5">
        <Button size="sm" className="flex-1" disabled={!entry}
          title={entry ? `进入「${entry.name}」，页内可切换本组 ${boards.length} 个看板` : '组合内暂无可看看板'}
          onClick={() => onEnter(group)}>进入看板</Button>
        <Button size="sm" variant="outline" disabled={!boards.length}
          title={boards.length ? '大屏轮播本组合' : '组合内暂无可看看板'}
          aria-label={`大屏轮播 ${group.name}`} onClick={() => onPlay(group)}>
          <Play className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  </article>;
}
