import type { ReactNode } from 'react';
import { AlertTriangle, ClipboardList, Hash, LayoutDashboard, MessageSquareQuote } from 'lucide-react';
import type { ContentTag } from '@/api/content';

export interface RailStats {
  /** 我的草稿（仅具备编辑权时取） */
  drafts: number | null;
  /** 待我审核（仅具备审核权时取） */
  pending: number | null;
  /** 已上线但过了复核期 */
  expired: number | null;
}

/**
 * 首页右栏：三块都必须是"点了会去干活"的东西，不放装饰性统计。
 *
 * 「待我处理」整块按权限码出现：草稿数只有具备编辑权的人取得到
 * （`status=draft` 在服务端就要 `article:manage`），审核队列只有具备审核权的人
 * 请求得动（`/content/reviews*` 是独立前缀）。所以这里不是"数字为 0 就藏起来"，
 * 而是**没有对应权限就不发请求、不显示该行** —— 发出去只会拿到 403 并把失败画成 0。
 */
export default function ActionRail({
  boards, tags, stats, canManage, canReview, onOpenBoard, onGoContent, onTagPick, activeTag, onAskAboutFeed,
  onConfigureDashboards,
}: {
  /** description 由服务端随看板返回；缺失时渲染层给「暂无描述」，不显示空串 */
  boards: Array<{ id: number; name: string; description?: string }>;
  /** 具备仪表盘配置权时首页会传：无看板的引导不能只说「联系管理员」而不给去处 */
  onConfigureDashboards?: () => void;
  tags: ContentTag[];
  stats: RailStats;
  canManage: boolean;
  canReview: boolean;
  onOpenBoard: (id: number) => void;
  onGoContent: () => void;
  onTagPick: (t: string) => void;
  activeTag: string;
  onAskAboutFeed: () => void;
}) {
  const todos = [
    canManage && { key: 'drafts', label: '我的草稿', hint: '继续修改或提交审核', count: stats.drafts, hot: false },
    canReview && { key: 'review', label: '待我审核', hint: '软警告要逐条确认才能通过', count: stats.pending, hot: true },
    canReview && { key: 'expired', label: '到期待复核', hint: '合规与法规类建议按季复核', count: stats.expired, hot: false },
  ].filter(Boolean) as Array<{ key: string; label: string; hint: string; count: number | null; hot: boolean }>;

  return (
    <aside className="flex flex-col gap-3.5">
      <Panel
        title="我的看板"
        icon={LayoutDashboard}
        action={boards.length ? { label: '全部', onClick: () => onOpenBoard(-1) } : undefined}
      >
        {!boards.length && (
          <div className="space-y-2 p-3.5 text-[11.5px] leading-relaxed text-muted-foreground">
            <p className="text-[13px] font-medium text-foreground">还没有分配仪表盘</p>
            <p>
              角色被分配看板组合后，登录会直达数据看板；也可以随时从工作空间侧栏「数据看板」进入。
              {onConfigureDashboards ? '在「系统配置 → 仪表盘」里创建看板并编排组合、分配角色。' : '可联系管理员为你的角色分配看板组合。'}
            </p>
            {onConfigureDashboards && (
              <button type="button" onClick={onConfigureDashboards}
                className="rounded-md border px-2.5 py-1 text-[12px] hover:border-primary/50 hover:text-primary">
                去配置仪表盘
              </button>
            )}
          </div>
        )}
        {boards.slice(0, 5).map(b => (
          <button
            key={b.id}
            type="button"
            onClick={() => onOpenBoard(b.id)}
            className="flex w-full items-center gap-2.5 border-b border-border/60 px-3.5 py-2.5 text-left last:border-0 hover:bg-muted/60"
          >
            <span className="flex h-7 w-7 flex-none items-center justify-center rounded-lg bg-primary/12 text-primary">
              <LayoutDashboard className="h-3.5 w-3.5" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-medium">{b.name}</span>
              <span className="block truncate text-[11px] text-muted-foreground">{b.description || '暂无描述'}</span>
            </span>
          </button>
        ))}
        {boards.length > 5 && (
          <div className="px-3.5 py-2 text-[11px] text-muted-foreground">另有 {boards.length - 5} 个，从「全部」查看</div>
        )}
      </Panel>

      {!!todos.length && (
        <Panel title="待我处理" icon={ClipboardList} action={{ label: '内容中心', onClick: onGoContent }}>
          {todos.map(t => (
            <button
              key={t.key}
              type="button"
              onClick={onGoContent}
              className="flex w-full items-center gap-2.5 border-b border-border/60 px-3.5 py-2.5 text-left last:border-0 hover:bg-muted/60"
            >
              <span className="min-w-0 flex-1">
                <span className="block text-[13px]">{t.label}</span>
                <span className="block text-[11px] leading-snug text-muted-foreground">{t.hint}</span>
              </span>
              <span className={`text-[13px] font-semibold tabular-nums ${t.hot && (t.count ?? 0) > 0 ? 'text-amber-600 dark:text-amber-400' : ''}`}>
                {t.count === null ? '—' : t.count}
              </span>
            </button>
          ))}
        </Panel>
      )}

      <Panel title="热门标签" icon={Hash} hidden={!tags.length}>
        <div className="flex flex-wrap gap-1.5 p-3.5">
          {tags.map(t => (
            <button
              key={t.tag}
              type="button"
              onClick={() => onTagPick(t.tag)}
              aria-pressed={activeTag === t.tag}
              className={`rounded-full border px-2.5 py-1 text-[11px] transition-colors ${
                activeTag === t.tag
                  ? 'border-primary/45 bg-primary/12 text-primary'
                  : 'border-border text-muted-foreground hover:border-primary/45 hover:text-primary'}`}
            >
              #{t.tag}
              <span className="ml-1 opacity-60">{t.count}</span>
            </button>
          ))}
        </div>
      </Panel>

      <Panel title="就着资讯问一句" icon={MessageSquareQuote}>
        <div className="space-y-2 p-3.5 text-[11.5px] leading-relaxed text-muted-foreground">
          <p>
            读到的结论想深挖，文章底部的
            <button type="button" onClick={onAskAboutFeed} className="text-primary underline-offset-2 hover:underline">「就本主题提问」</button>
            会带着这篇的标题与栏目进智能问数，不用你复述背景。
          </p>
          <p className="flex items-start gap-1.5 rounded-lg border bg-muted/40 p-2.5">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-none" />
            <span>
              资讯区只呈现<b className="text-foreground">已发布</b>内容；草稿与待审只出现在有编辑权、审核权的人的「待我处理」里。
            </span>
          </p>
        </div>
      </Panel>
    </aside>
  );
}

function Panel({ title, icon: Icon, action, hidden, children }: {
  title: string;
  icon: typeof Hash;
  action?: { label: string; onClick: () => void };
  hidden?: boolean;
  children: ReactNode;
}) {
  if (hidden) return null;
  return (
    <section className="overflow-hidden rounded-xl border bg-card">
      <div className="flex items-center gap-1.5 border-b px-3.5 py-2.5 text-[12.5px] font-semibold">
        <Icon className="h-3.5 w-3.5 text-primary" />
        {title}
        {action && (
          <button
            type="button"
            onClick={action.onClick}
            className="ml-auto text-[11.5px] font-normal text-muted-foreground hover:text-primary"
          >
            {action.label} ›
          </button>
        )}
      </div>
      {children}
    </section>
  );
}
