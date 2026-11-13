import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowRight, Flame, Layers, Search, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { contentApi, contentError, type ContentArticle, type ContentCategory } from '@/api/content';
import {
  CategoryBadge, ReadMeta, ReleaseChip, categoryOfArticle, kindHint, useReadState, ABOUT_CATEGORY,
} from './shared';

type Sort = 'latest' | 'hot' | 'rec';
const SORTS: Array<{ key: Sort; label: string }> = [
  { key: 'latest', label: '最新' },
  { key: 'hot', label: '最热' },
  { key: 'rec', label: '精选' },
];

/**
 * 首页左栏：最新资讯流（首页的主内容位）。
 *
 * 三条实现判据：
 * 1. **只消费在线内容**：`status` 传空串，服务端就只回 `live_revision_id<>0` 且未下架的
 *    条目（草稿在服务端一道门里被挡，不是前端不显示）。所以这里的"空"是真的没有
 *    已发布内容，与"读取失败"必须是两个不同视图。
 * 2. **筛选态不渲染精选大卡**：只剩一张卡的"精选区"是噪声；有筛选就平铺。
 * 3. **未读只按本设备已读记录判**（见 shared.useReadState 的说明），并如实上报给
 *    首屏指标，不在 Home 里另算一套。
 * 4. **「关于本公司」不在这里重复**：该栏目已由 Hero 右侧简介卡整版展示，资讯流
 *    与服务端清单都按 exclude_category 让位（总数/筛选计数随同一口径，不是前端筛
 *    完再藏 —— 否则“共 N 篇”与卡片对不上）；内容管理页不受影响，编排面照常看全量。
 */
export default function NewsFeed({
  categories, tag, onTag, canEdit, onManage, onOpen, pageSize = 5,
}: {
  categories: ContentCategory[];
  /** 标签筛选由首页持有：右栏「热门标签」点的是同一个条件，不能各存一份 */
  tag: string;
  onTag: (t: string) => void;
  canEdit: boolean;
  onManage: () => void;
  onOpen: (slug: string) => void;
  /** 上报在线总数与本批未读数，供首屏「未读资讯」指标使用 */
  pageSize?: number;
}) {
  const [items, setItems] = useState<ContentArticle[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [category, setCategory] = useState('');
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<Sort>('latest');
  const [expanded, setExpanded] = useState(false);
  const { mark, isUnread } = useReadState();

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    contentApi.list({
      category, exclude_category: ABOUT_CATEGORY, tag, q, sort, page: 1, page_size: expanded ? 24 : pageSize,
    })
      .then(({ data }) => { setItems(data.items || []); setTotal(data.total || 0); })
      .catch(err => {
        // 失败必须显式：静默回空列表会把"权限没配/接口挂了"伪装成"今天没内容"
        setItems([]); setTotal(0);
        setError(contentError(err));
      })
      .finally(() => setLoading(false));
  }, [category, tag, q, sort, expanded, pageSize]);

  useEffect(load, [load]);

  const catOf = useCallback(
    (keyOrId: number | string) => categories.find(c => c.key === keyOrId || c.id === keyOrId),
    [categories]);
  // 卡片徽章/封面取色走文章 → 栏目的唯一解析口（按 category_id 命中，清单没有时用服务端
  // 随文章下发的 label 兜底）。历史缺陷：这里曾写成 catOf(catLabel(a))，把栏目名当 key/id 查，
  // 永远查不中 → 每张卡都显示「栏目未知」。
  const catOfArticle = useCallback((a: ContentArticle) => categoryOfArticle(a, categories), [categories]);

  const filteredHard = !!(category || tag || q);
  const featured = useMemo(() => {
    if (filteredHard || sort === 'hot') return null;
    return items.find(a => a.pinned) || items.find(a => a.featured) || null;
  }, [items, filteredHard, sort]);
  const rest = useMemo(() => items.filter(a => a !== featured), [items, featured]);

  const open = (slug: string) => { mark(slug); onOpen(slug); };
  const clearAll = () => { setCategory(''); onTag(''); setQ(''); };

  return (
    <section aria-labelledby="home-feed-title">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 id="home-feed-title" className="flex items-center gap-2 text-[15px] font-semibold">
          <Layers className="h-4 w-4 text-primary" />
          最新资讯
          {!loading && !error && (
            <span className="text-xs font-normal text-muted-foreground">
              共 {total} 篇在线{filteredHard ? ` · 筛出 ${items.length} 篇` : ''}
            </span>
          )}
        </h2>
        <div className="ml-auto flex items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={q}
              onChange={e => setQ(e.target.value)}
              placeholder="搜索标题与摘要"
              aria-label="搜索资讯"
              className="h-8 w-[190px] pl-8 text-xs"
            />
          </div>
          <div className="flex items-center gap-0.5 rounded-lg bg-muted p-0.5">
            {SORTS.map(s => (
              <button
                key={s.key}
                type="button"
                onClick={() => setSort(s.key)}
                aria-pressed={sort === s.key}
                className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                  sort === s.key ? 'bg-card font-medium shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 栏目筛选：label 与计数都来自接口，任何栏目名都不写在代码里；
          「关于本公司」由简介卡专属展示，不在此重复成可点筛选（点了只会筛出一堆被让位的内容） */}
      <div className="mb-3 flex flex-wrap gap-1.5">
        <CatChip active={!category} onClick={() => setCategory('')} label="全部" count={total} />
        {categories.filter(c => c.key !== ABOUT_CATEGORY).map(c => (
          <CatChip
            key={c.key}
            active={category === c.key}
            onClick={() => setCategory(category === c.key ? '' : c.key)}
            label={c.label}
            dot={c.color}
          />
        ))}
      </div>

      {filteredHard && (
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {category && <FilterChip label={`栏目：${catOf(category)?.label || category}`} onX={() => setCategory('')} />}
          {tag && <FilterChip label={`标签：${tag}`} onX={() => onTag('')} />}
          {q && <FilterChip label={`关键词：${q}`} onX={() => setQ('')} />}
          <button type="button" onClick={clearAll} className="hover:text-primary">清空筛选</button>
        </div>
      )}

      {loading && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-32 animate-pulse rounded-xl border bg-card" />
          ))}
        </div>
      )}

      {!loading && error && (
        <div role="alert" className="rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-sm">
          <div className="flex items-start gap-2">
            <X className="mt-0.5 h-4 w-4 flex-none text-destructive" />
            <div className="flex-1">
              <p className="font-medium">资讯读取失败，暂时拿不到已发布内容</p>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {error}。这里不把失败显示成「暂无内容」—— 两者需要你做的事完全不同。
              </p>
              <Button size="sm" variant="outline" className="mt-2.5" onClick={load}>重试</Button>
            </div>
          </div>
        </div>
      )}

      {!loading && !error && !items.length && (
        <div className="rounded-xl border border-dashed p-9 text-center">
          <p className="text-sm font-medium">
            {filteredHard ? '当前筛选条件下没有内容' : '还没有已发布的内容'}
          </p>
          <p className="mx-auto mt-1 max-w-[430px] text-xs leading-relaxed text-muted-foreground">
            {filteredHard
              ? '换一个栏目、清掉标签或关键词试试。'
              : '内容中心的稿件要经「提交审核 → 发布」才会出现在这里。'}
          </p>
          {filteredHard
            ? <Button size="sm" variant="outline" className="mt-3" onClick={clearAll}>清空筛选</Button>
            : canEdit && (
              <Button size="sm" className="mt-3" onClick={onManage}>去内容中心发布</Button>
            )}
        </div>
      )}

      {!loading && !error && !!items.length && (
        <>
          {featured && (
            <button
              type="button"
              onClick={() => open(featured.slug)}
              className="group mb-3 flex w-full gap-4 overflow-hidden rounded-2xl border bg-card p-4 text-left transition-all hover:-translate-y-0.5 hover:border-primary/50 hover:shadow-lg"
            >
              <span
                aria-hidden
                className="hidden w-[190px] flex-none items-center justify-center rounded-xl text-3xl text-white sm:flex"
                style={{ background: `linear-gradient(135deg, ${catOfArticle(featured)?.color || 'hsl(var(--primary))'}, ${catOfArticle(featured)?.color || 'hsl(var(--primary))'}99)` }}
              >
                {catOfArticle(featured)?.icon || '📄'}
              </span>
              <span className="flex min-w-0 flex-1 flex-col gap-2">
                <span className="flex flex-wrap items-center gap-1.5">
                  <CategoryBadge category={catOfArticle(featured)} />
                  {featured.pinned ? (
                    <span className="inline-flex items-center gap-1 rounded-md bg-primary/12 px-1.5 py-0.5 text-[11px] font-medium text-primary">
                      <Flame className="h-3 w-3" />置顶
                    </span>
                  ) : (
                    <span className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">编辑精选</span>
                  )}
                  {kindHint(featured) && (
                    <span className="rounded-md border px-1.5 py-0.5 text-[11px] text-muted-foreground">
                      {kindHint(featured)!.label}
                    </span>
                  )}
                  {/* 月报这类递进内容：“哪一期”就是读者判断新旧的第一信息 */}
                  <ReleaseChip article={featured} />
                </span>
                <span className="line-clamp-2 text-[17px] font-semibold leading-snug">{featured.title}</span>
                <span className="line-clamp-2 text-xs leading-relaxed text-muted-foreground">{featured.summary}</span>
                <span className="mt-auto flex items-center gap-3">
                  <ReadMeta article={featured} unread={isUnread(featured.slug)} />
                  <span className="flex items-center gap-1 text-xs text-primary opacity-0 transition-opacity group-hover:opacity-100">
                    阅读全文 <ArrowRight className="h-3.5 w-3.5" />
                  </span>
                </span>
              </span>
            </button>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {rest.map(a => (
              <button
                key={a.slug}
                type="button"
                onClick={() => open(a.slug)}
                className="group flex flex-col rounded-xl border bg-card p-3.5 text-left transition-all hover:-translate-y-0.5 hover:border-primary/50 hover:shadow-md"
              >
                <span className="flex flex-wrap items-center gap-1.5">
                  <CategoryBadge category={catOfArticle(a)} withIcon={false} />
                  {kindHint(a) && (
                    <span className="rounded-md border px-1.5 py-0.5 text-[11px] text-muted-foreground">
                      {kindHint(a)!.label}
                    </span>
                  )}
                  <ReleaseChip article={a} />
                </span>
                <span className="mt-2 line-clamp-2 text-sm font-semibold leading-snug">{a.title}</span>
                <span className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">{a.summary}</span>
                <span className="mt-2.5 border-t pt-2">
                  <ReadMeta article={a} unread={isUnread(a.slug)} />
                </span>
              </button>
            ))}
          </div>

          {!filteredHard && total > items.length && (
            <div className="mt-3.5 flex justify-center">
              <Button size="sm" variant="outline" onClick={() => setExpanded(v => !v)}>
                {expanded ? '收起' : `查看全部 ${total} 篇`}
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function CatChip({ active, onClick, label, count, dot }: {
  active: boolean; onClick: () => void; label: string; count?: number; dot?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors ${
        active
          ? 'border-transparent bg-foreground text-background font-medium'
          : 'border-border text-muted-foreground hover:border-foreground/30 hover:text-foreground'}`}
    >
      {dot && <span className="size-1.5 rounded-sm" style={{ backgroundColor: dot }} aria-hidden />}
      {label}
      {typeof count === 'number' && <span className="opacity-70">{count}</span>}
    </button>
  );
}

function FilterChip({ label, onX }: { label: string; onX: () => void }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/40 bg-primary/10 px-2.5 py-1 text-primary">
      {label}
      <button type="button" onClick={onX} aria-label={`移除筛选 ${label}`} className="flex hover:opacity-70">
        <X className="h-3 w-3" />
      </button>
    </span>
  );
}
