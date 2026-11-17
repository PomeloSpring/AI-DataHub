import { useEffect, useState, type ComponentProps } from 'react';
import { Newspaper } from 'lucide-react';
import { contentApi, type ContentTag } from '@/api/content';
import { useTaxonomy } from '@/pages/content/shared';
import { TOUR } from '@/lib/onboarding/guides';
import NewsFeed from './NewsFeed';
import ActionRail, { type RailStats } from './ActionRail';
import { t , t as tr } from '@/i18n/runtime';

/**
 * 首页双栏主体（资讯流 + 行动栏）与它的取数容器。
 *
 * 单独成组件不是为了排版，而是两条硬性约束：
 * 1. **栏目/标签必须来自接口**：`useTaxonomy` 是唯一来源（`/content/categories`、
 *    `/content/tags`），任何"首页先画 6 个固定栏目"的写法都会在管理员改名后立刻失真；
 * 2. **无读者码时一个请求都不发**：资讯区对没配 `article:read` 的角色必然 403，
 *    而 hooks 不能写在条件分支里 —— 所以把请求收在这个按 `showFeed` 整体挂载/卸载的
 *    子组件里，而不是让 Home 无条件调用再忽略结果。
 */
export default function WorkbenchPane({
  showFeed, boards, stats, canManage, canReview, tag, onTag,
  onOpenBoard, onGoContent, onOpen, onAskAboutFeed, onConfigureDashboards,
}: {
  showFeed: boolean;
  boards: Array<{ id: number; name: string; description?: string }>;
  stats: RailStats;
  canManage: boolean;
  canReview: boolean;
  tag: string;
  onTag: (t: string) => void;
  onOpenBoard: (id: number) => void;
  onGoContent: () => void;
  onOpen: (slug: string) => void;
  onAskAboutFeed: () => void;
  onConfigureDashboards?: () => void;
}) {
  if (!showFeed) {
    return (
      <section className="mt-4 rounded-2xl border border-dashed bg-card/40 p-8">
        <h2 className="flex items-center gap-2 text-sm font-medium">
          <Newspaper className="h-4 w-4 text-primary" />{tr('k_11070e6455')}</h2>
        <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
          {tr('k_5119040bfa')}{' '}<code>article:read</code>{tr('k_665ba9bbdb')}</p>
      </section>
    );
  }
  return <PaneInner {...{ boards, stats, canManage, canReview, tag, onTag,
    onOpenBoard, onGoContent, onOpen, onAskAboutFeed, onConfigureDashboards }} />;
}

function PaneInner({
  boards, stats, canManage, canReview, tag, onTag,
  onOpenBoard, onGoContent, onOpen, onAskAboutFeed, onConfigureDashboards,
}: Omit<ComponentProps<typeof WorkbenchPane>, 'showFeed'>) {
  const { categories, error, reload } = useTaxonomy();
  const [tags, setTags] = useState<ContentTag[]>([]);

  useEffect(() => {
    let cancelled = false;
    contentApi.tags()
      .then(({ data }) => {
        if (cancelled) return;
        // 只渲染有命中的标签并按热度取前 10：首批踩过 26 个标签里 21 个是孤儿的坑，
        // 孤儿标签点进去必然是空列表，那不是筛选而是陷阱。
        setTags((data.items || []).filter(t => t.count > 0).sort((a, b) => b.count - a.count).slice(0, 10));
      })
      .catch(() => { if (!cancelled) setTags([]); });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_316px] lg:items-start">
      {/* data-tour 供新手引导定位资讯区；id 同时给首页「未读资讯」指标滚动用 */}
      <div id="home-feed" data-tour={TOUR.homeFeed}>
        <NewsFeed
          categories={categories}
          tag={tag}
          onTag={onTag}
          canEdit={canManage}
          onManage={onGoContent}
          onOpen={onOpen}
        />
        {/* 栏目/标签清单拉不到时资讯流会退化成"没有栏目"，必须说出来而不是静默少两块 */}
        {!!error && (
          <p className="mt-3 flex flex-wrap items-center gap-2 text-xs text-destructive">
            {tr('k_a6adb91a38')}{error}
            <button type="button" onClick={reload} className="underline underline-offset-2">{t("k_e2d53a6d3a")}</button>
          </p>
        )}
      </div>

      <div className="lg:sticky lg:top-[74px]">
        <ActionRail
          boards={boards}
          tags={tags}
          stats={stats}
          canManage={canManage}
          canReview={canReview}
          activeTag={tag}
          onTagPick={t => onTag(t === tag ? '' : t)}
          onOpenBoard={onOpenBoard}
          onGoContent={onGoContent}
          onAskAboutFeed={onAskAboutFeed}
          onConfigureDashboards={onConfigureDashboards}
        />
      </div>
    </div>
  );
}
