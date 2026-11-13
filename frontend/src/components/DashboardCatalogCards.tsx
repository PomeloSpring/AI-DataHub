import { Layers, Play } from 'lucide-react';
import { DashboardThumbnail } from '@/components/DashboardCanvas';
import { DashboardThumbnailFrame, THUMB_FRAME_H } from '@/components/DashboardThumbnailFrame';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { VisComponent } from '@/api/visLibrary';
import type { DashboardPreview, GroupBoard, GroupView } from '@/lib/dashboardCatalog';
import { toTagList } from '@/lib/dashboardCatalog';
import type { Dashboard } from '@/stores/dashboardStore';

// 工作空间「数据看板」的共享卡片：组合卡（一级）+ 看板卡（搜索结果与未分组平铺）。
// 组合卡点下去 = 直接进该组合的看板详情（组内浏览由详情页的切换条承担），不再中间插一层看板列表。
// 两个层级必须用同一张看板卡 + 同一个缩略图渲染器/固定框（DashboardThumbnail / DashboardThumbnailFrame），
// 与系统配置「看板管理」也共用这套尺寸口径，不得各画一套几何。

/**
 * 卡片几何唯一口径 —— 组合卡与看板卡会**并排落在同一个网格里**（一级清单 = 真组合卡 + 未分组看板卡），
 * 因此两者的行结构必须一一对应，行数不能随内容增减：
 *   ① 预览槽（固定高度 THUMB_FRAME_H + cover 裁切，见 DashboardThumbnailFrame）
 *   ② 标题 + 描述
 *   ③ 信息行：徽标（数量/归属）与次级信息**同一行**——看板卡右侧是「更新于」，
 *      组合卡右侧是组内看板快捷项；无内容时给占位文案，不得因缺一项就掉一行
 *   ④ 操作条：`mt-auto` 贴卡片底
 * 历史缺陷：徽标行与次级行过去由内容决定有无（少一个标签/多一排 chips），同一行的两张卡底部按钮
 * 就错开几十像素，扫读时像两套组件；而两行各 20px 又让卡片上下偏厚。现在合成一行（定高 min-h-5，
 * 右侧内容溢出裁掉，完整名在 title 里），卡片高度交给网格拉伸（h-full + mt-auto）。
 */
const ARTICLE_CLS = 'group flex h-full flex-col overflow-hidden rounded-xl border bg-card transition-shadow hover:shadow-md';
const BODY_CLS = 'flex flex-1 flex-col gap-1.5 px-3 py-2';
/** 信息行定高（min-h-5）且不换行：单行内容换不成两行，两种卡也就不会错位 */
const ROW_CLS = 'flex min-h-5 items-center gap-2 overflow-hidden text-xs';
const FOOT_CLS = 'mt-auto flex gap-2 border-t pt-2';

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

/** 组内"进入"目标看板：取清单首位（服务端目录已按管理端编排顺序 adh_dashboard_group_items.sort 下发，
 * 未分组兑底组则是 is_default → sort_order）。
 * 不再拿 is_default 抢位：抢了就等于管理员在组合里排的顺序对“先进哪块板”不算数。 */
export function groupEntryBoard(group: GroupView): GroupBoard | undefined {
  return group.dashboards.filter(d => d.status === 'enabled')[0];
}

/** 单看板卡片：未分组平铺与搜索结果用（与组合卡同一套行几何） */
export function BoardCard({ board, groupName, items, onOpen, onPlay }: {
  board: GroupBoard; groupName?: string; items: VisComponent[];
  onOpen: (id: number) => void; onPlay: (id: number) => void;
}) {
  const source = previewSource(board.preview);
  return <article className={ARTICLE_CLS}>
    <button type="button" aria-label={`打开看板 ${board.name}`} className="block w-full border-b text-left"
      onClick={() => onOpen(board.id)}>
      <DashboardThumbnailFrame dashboard={source}>
        <DashboardThumbnail dashboard={source} items={items} />
      </DashboardThumbnailFrame>
    </button>
    <div className={BODY_CLS}>
      <div className="min-w-0">
        <h2 className="truncate text-sm font-semibold" title={board.name}>{board.name}</h2>
        <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{board.description || '暂无描述'}</p>
      </div>
      <div className={ROW_CLS}>
        {/* 左侧连排（归属·默认·标签·图表数·更新时间），不靠 ml-auto 拉到两端：
            两端对齐会在中间留一大段虚空，同一行读不出是一组信息。
            标签 chips 插在中间而不另起一行：本行的弹性项只有「更新于」（min-w-0 truncate），
            多插几个 shrink-0 chip 只会把时间压短，不会把行撑高、也不会与旁边的组合卡错位。 */}
        {!!groupName && <Badge variant="secondary" className="shrink-0">{groupName}</Badge>}
        {!!board.is_default && <Badge variant="outline" className="shrink-0">默认</Badge>}
        {toTagList(board.tags).slice(0, 2).map(t => (
          <span key={t} title={`标签：${t}`}
            className="shrink-0 max-w-[6.5rem] truncate rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
            #{t}
          </span>))}
        {toTagList(board.tags).length > 2 && (
          <span className="shrink-0 text-[11px] text-muted-foreground"
            title={toTagList(board.tags).join('、')}>+{toTagList(board.tags).length - 2}</span>)}
        <span className="shrink-0 text-muted-foreground">{board.chart_count ?? 0} 个图表</span>
        <span aria-hidden className="shrink-0 text-muted-foreground/60">·</span>
        <span className="min-w-0 truncate text-muted-foreground"
          title={`更新于 ${board.updated_at ? new Date(board.updated_at).toLocaleString('zh-CN') : '—'}`}>
          更新于 {board.updated_at ? new Date(board.updated_at).toLocaleString('zh-CN') : '—'}</span>
      </div>
      <div className={FOOT_CLS}>
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
  return <article className={ARTICLE_CLS}>
    <button type="button" aria-label={`进入组合 ${group.name}`} className="block w-full border-b text-left"
      onClick={() => onEnter(group)}>
      <DashboardThumbnailFrame dashboard={source || { filters: {}, charts: [] }}>
        {source ? <DashboardThumbnail dashboard={source} items={items} />
          : <span className={`flex ${THUMB_FRAME_H} flex-col items-center justify-center gap-1 text-xs text-muted-foreground`}>
            <Layers className="h-5 w-5" />组合内暂无可看看板
          </span>}
      </DashboardThumbnailFrame>
    </button>
    <div className={BODY_CLS}>
      <div className="min-w-0">
        <h2 className="truncate text-sm font-semibold" title={group.name}>{group.name}</h2>
        <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{group.description || '暂无描述'}</p>
      </div>
      <div className={ROW_CLS}>
        <Badge variant="secondary" className="shrink-0">{boards.length} 个可看看板</Badge>
        {/* 未启用/已关闭不混进浏览集，但数量必须显式可见，不能静默消失 */}
        {!!unready && <Badge variant="outline" className="shrink-0">{unready} 个未启用</Badge>}
        {/* 组内快捷项紧跟徽标（与看板卡的「更新于」同一行同一高度）：chips 单行不换行，超出裁掉，完整名在 title 里 */}
        {!!boards.length
          ? <ul className="flex min-w-0 items-center gap-1.5 overflow-hidden">
            {boards.slice(0, 2).map(b => <li key={b.id} className="shrink-0">
              <button type="button" className="max-w-[8rem] truncate rounded-md bg-muted/60 px-2 py-0.5 text-[11px] hover:bg-muted"
                title={b.name} onClick={() => onOpenBoard(b.id)}>{b.name}</button>
            </li>)}
            {boards.length > 2 && <li className="shrink-0 text-[11px] text-muted-foreground">等 {boards.length} 个</li>}
          </ul>
          : <span className="min-w-0 truncate text-muted-foreground">组内暂无可看的看板</span>}
      </div>
      <div className={FOOT_CLS}>
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
