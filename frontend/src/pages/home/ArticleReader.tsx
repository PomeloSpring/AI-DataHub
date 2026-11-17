// 阅读正文共享组件：/read/:slug 独立页 与 报告中心内嵌查看**共用同一份**阅读逻辑。
//
// 抽这一层的唯一理由是「不得有两套阅读口径」（原 Spec §4 警告点）：正文渲染、历史期次、
// 附件、引用、同栏目推荐、就本主题提问 —— 若各自实现，改一处忘一处就会出现"内嵌能读、
// 独立页读不通"的分裂。期次/跳转由宿主决定（页面走 URL、内嵌走本地态与切换），
// 本组件只负责渲染与取数，count_view 恒为读者行为（与门户统计同源）。
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, CalendarClock, ExternalLink, FileText, MessageSquareQuote } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { t } from '@/i18n/runtime';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import MarkdownWithCharts from '@/components/MarkdownWithCharts';
import HtmlArticle from '@/components/HtmlArticle';
import { containerForKind, widthModeForKind } from '@/lib/pageWidth';
import { revealClass } from '@/lib/motion';
import { useMotion } from '@/hooks/useMotion';
import { contentApi, contentError, type ContentArticle, type ReleaseItem } from '@/api/content';
import { useTaxonomy, ErrorBanner } from '@/pages/content/shared';
import ArticleFiles from '@/pages/content/ArticleFiles';
import { CategoryBadge, categoryOfArticle, relTime, stashQuestion, useReadState, viewCount } from './shared';

export default function ArticleReader({
  slug, release, onPickRelease, onOpenArticle, showHomeCrumb = false, asMain = false, countView = true,
}: {
  slug: string;
  /** 阅读的第几期（0 = 当前在线那一期）；由宿主持有（页面走 URL ?release=，内嵌走本地态） */
  release: number;
  onPickRelease: (value: string) => void;
  /** 点击同栏目推荐：页面跳 /read/:slug，内嵌在报告中心清单里就地切换 */
  onOpenArticle: (slug: string) => void;
  /** 面包屑首项「首页」链接：仅独立页需要；内嵌视图不该把人带回首页（跳走等于离开报告中心） */
  showHomeCrumb?: boolean;
  /** 独立页用 `<main>` 地标（单页可访问）；报告中心内嵌用 `<div>`，避免页面里嵌套多个 main */
  asMain?: boolean;
  /** 是否计入门户阅读数：读者主动打开=计；报告中心落地自动展示首项=不计（否则每次进页都顶高「最多阅读」） */
  countView?: boolean;
}) {
  const getDefaultWorkspaceId = useWorkspaceStore(s => s.getDefaultWorkspaceId);
  const navigate = useNavigate();
  const { categories } = useTaxonomy();
  const { mark } = useReadState();

  const [article, setArticle] = useState<ContentArticle | null>(null);
  const [related, setRelated] = useState<ContentArticle[]>([]);
  const [releases, setReleases] = useState<ReleaseItem[]>([]);
  const [historyHidden, setHistoryHidden] = useState(false);
  const [attachmentsShared, setAttachmentsShared] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    setArticle(null);
    // revision 固定 live；countView 由宿主定（读者主动读=计，落地自动展示首项=不计，避免污染门户统计）
    contentApi.detail(slug, 'live', countView, release)
      .then(({ data }) => {
        setArticle(data);
        mark(data.slug);
        contentApi.related(data.slug).then(r => setRelated(r.data.items || [])).catch(() => setRelated([]));
      })
      .catch(err => setError(contentError(err)))
      .finally(() => setLoading(false));
  }, [slug, release, mark, countView]);

  useEffect(load, [load]);

  // 换期次/换文章回到顶部（作用在自己的滚动口，不动整页 document）
  useEffect(() => { scrollRef.current?.scrollTo({ top: 0 }); }, [slug, release]);

  // 历史期次候选单独拉一次：清单与读者可读性是服务端同一份口径（history_hidden 直接用它）
  useEffect(() => {
    if (!slug) return;
    let alive = true;
    contentApi.releases(slug)
      .then(res => {
        if (!alive) return;
        setReleases(res.data.items);
        setHistoryHidden(res.data.history_hidden);
        setAttachmentsShared(!!res.data.attachments_shared);
      })
      // 拉不到就只隐藏下拉（不弹错）：期次是辅助阅读能力，不能因为它把主内容报错盖掉
      .catch(() => { if (alive) { setReleases([]); setHistoryHidden(false); } });
    return () => { alive = false; };
  }, [slug]);

  const askAbout = () => {
    if (!article) return;
    stashQuestion(`关于《${article.title}》：`);
    const wsId = getDefaultWorkspaceId();
    // 落点与首页/模块切换同口径（默认工作空间的 chat），无默认空间落 /workspace 由入口解析
    navigate(wsId ? `/ws/${wsId}/chat` : '/workspace');
  };

  const cat = article ? categoryOfArticle(article, categories) : undefined;
  const { motion, cssVars } = useMotion();
  const reveal = revealClass(motion.reveal);
  const shell = containerForKind(article?.kind);

  // 滚动口在自身（relative 让正文里 position:absolute 后代以此为包含块，不越过裁剪撑高 document）
  const Shell = asMain ? 'main' : 'div';
  return (
    <Shell ref={scrollRef as never} data-page-width={widthModeForKind(article?.kind)}
      className="relative min-h-0 flex-1 overflow-auto bg-background">
      <div className={`${shell} py-6`}>
        {loading && (
          <div className="space-y-4">
            <Skeleton className="h-5 w-64" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        )}

        {!loading && error && <ErrorBanner message={error} onRetry={load} />}

        {!loading && !error && !article && (
          <div className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
            这篇内容不存在，或已被下架。
          </div>
        )}

        {article && (
          <article>
            {/* 面包屑：栏目用业务名，不显示 category_id；内嵌视图不回首页 */}
            <nav aria-label={t("k_5b8edc5dfb")} className="mb-3 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              {showHomeCrumb && (
                <a href="/" className="hover:text-primary">{t("k_ff93ad0e4e")}</a>
              )}
              {showHomeCrumb && <span>·</span>}
              <span>{article.category_label || '栏目未知'}</span>
              <span>·</span>
              <span className="truncate text-foreground/80">{article.title}</span>
            </nav>

            <header className="border-b pb-4">
              <div className="flex flex-wrap items-center gap-1.5">
                <CategoryBadge category={cat} />
                {article.pinned ? <span className="rounded-md bg-primary/12 px-1.5 py-0.5 text-[11px] font-medium text-primary">{t("k_7bcf18641f")}</span> : null}
                {(article.viewing_release ?? article.release) > 0 && (
                  <span className="rounded-md bg-sky-500/15 px-1.5 py-0.5 text-[11px] font-medium text-sky-600 dark:text-sky-400"
                    title={article.release_count > 1 ? `本篇共 ${article.release_count} 期` : undefined}>
                    第 {article.viewing_release || article.release} 期
                    {article.viewing_release_label || article.release_label
                      ? `·${article.viewing_release_label || article.release_label}` : ''}
                  </span>
                )}
                {(article.kind !== 'markdown' && article.kind !== 'html') && (
                  <span className="rounded-md border px-1.5 py-0.5 text-[11px] text-muted-foreground">{article.kind_label}</span>
                )}
              </div>
              <h1 className="mt-2.5 text-[26px] font-semibold leading-snug tracking-tight">{article.title}</h1>
              {article.summary && (
                <p className="mt-2.5 border-l-[3px] border-primary bg-muted/50 px-3.5 py-2.5 text-[13px] leading-relaxed text-muted-foreground">
                  {article.summary}
                </p>
              )}
              <div className="mt-3 flex flex-wrap items-center gap-3 text-[11.5px] text-muted-foreground">
                <span>发布 {relTime(article.published_at || article.updated_at) || '日期未知'}</span>
                <span>·</span>
                <span>{article.author || '平台内容组'}</span>
                <span>·</span>
                <span>{viewCount(article.view_count)} 阅读</span>
                {article.review_by && (
                  <span className="flex items-center gap-1">
                    <CalendarClock className="h-3 w-3" />建议 {article.review_by.slice(0, 10)} 前复核
                  </span>
                )}
              </div>
            </header>

            {/* 双语条目：同 translation_group 有英文在线版就给一键切换；
                当前看的是中文、界面上又倾向英文（或用户显式在找英文版）时，
                必须**显式**说「暂无英文版」而不是静默回落 —— no-silent-degradation。 */}
            {(() => {
              const tr = article.translation || {};
              const en = tr.en || {};
              const zh = tr.zh || {};
              const cur = article.lang || 'zh-CN';
              const target: { slug: string; label: string; to: 'en-US' | 'zh-CN' } | null =
                cur !== 'en-US' && en.available && en.slug
                  ? { slug: en.slug, label: 'Read in English', to: 'en-US' }
                  : cur !== 'zh-CN' && zh.available && zh.slug
                    ? { slug: zh.slug, label: '切到中文版', to: 'zh-CN' }
                    : null;
              const missingEn = cur === 'zh-CN' && !en.available;
              if (!target && !missingEn) return null;
              return (
                <div className="mt-3 flex flex-wrap items-center gap-2 text-[11.5px]" data-testid="article-lang-bar">
                  {target && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-6 px-2 text-[11.5px]"
                      data-testid={`article-lang-switch-${target.to}`}
                      onClick={() => onOpenArticle(target.slug)}
                    >
                      {target.label}
                    </Button>
                  )}
                  {!target && missingEn && (
                    <span
                      className="rounded-md border border-dashed px-2 py-0.5 text-muted-foreground"
                      data-testid="article-lang-missing-en"
                      title="This article has not been translated yet; the Chinese version is shown instead of silently switching."
                    >
                      该篇暂无英文版 · No English version yet
                    </span>
                  )}
                </div>
              );
            })()}

            {releases.length > 1 && (
              <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span>{t("k_1d71660347")}</span>
                <Select value={String(release)} onValueChange={onPickRelease}>
                  <SelectTrigger className="w-[240px]" aria-label={t("k_fa62b17149")}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="0">
                      最新一期（第 {article.release} 期{article.release_label ? `·${article.release_label}` : ''}）
                    </SelectItem>
                    {releases.filter(r => !r.is_current).map(r => (
                      <SelectItem key={r.release} value={String(r.release)}>
                        第 {r.release} 期·{r.label}{r.released_at ? `（${r.released_at.slice(0, 10)}）` : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            {historyHidden && article.release_count > 1 && (
              <p className="mt-2 text-[11px] text-muted-foreground">
                该内容只发布最新版本，不提供历史期次阅读。
              </p>
            )}
            {release > 0 && article.viewing_is_current === false && (
              <div className="mt-3 rounded-xl border bg-muted/40 p-3 text-xs leading-5">
                你正在阅读第 {release} 期的旧内容，当前在线的是第 {article.release} 期
                {article.release_label ? `〈${article.release_label}〉` : ''}。
                <button type="button" className="ml-1.5 text-primary hover:underline" onClick={() => onPickRelease('0')}>
                  看最新一期
                </button>
              </div>
            )}

            {article.kind === 'link' && article.link_url && (
              <div className="mt-4 rounded-xl border bg-card p-4">
                <p className="mb-2.5 text-sm leading-relaxed">
                  外链条目：正文在原始站点维护，站内只保留导读，避免抄一份之后两边口径不一致。
                </p>
                <Button asChild variant="outline" size="sm">
                  <a href={article.link_url} target="_blank" rel="noopener noreferrer">
                    <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                    {article.link_label || '前往原文'}
                  </a>
                </Button>
              </div>
            )}

            <div className="mt-5 rounded-xl border bg-card p-5 sm:p-6">
              {article.body?.trim()
                ? (article.kind === 'html'
                  ? (reveal
                    ? <div className={reveal} style={cssVars}><HtmlArticle html={article.body} className="text-sm leading-7" /></div>
                    : <HtmlArticle html={article.body} className="text-sm leading-7" />)
                  : <MarkdownWithCharts text={article.body} className="text-sm leading-7" reveal />)
                : <p className="text-center text-sm text-muted-foreground">{t("k_2c1a2d30cb")}</p>}
            </div>

            {!!article.files?.length && (
              <section className="mt-4 rounded-xl border bg-card p-4">
                <h2 className="mb-2 flex items-center gap-1.5 text-sm font-medium">
                  <FileText className="h-4 w-4" />附件
                </h2>
                <ArticleFiles slug={article.slug} files={article.files} scope="articles" editable={false} />
                {release > 0 && attachmentsShared && (
                  <p className="mt-2 text-[11px] text-muted-foreground">
                    附件与期次无关：上面这些是条目当前挂载的附件，不会随阅读的历史期次而变。
                  </p>
                )}
              </section>
            )}

            {!!article.refs?.length && (
              <section className="mt-4 rounded-xl border bg-muted/30 p-4">
                <h2 className="mb-1.5 flex items-center gap-1.5 text-sm font-medium">
                  <FileText className="h-4 w-4" />引用出处
                </h2>
                <p className="mb-2 text-[11px] text-muted-foreground">{t("k_6ffe478471")}</p>
                <ol className="list-decimal space-y-1 pl-5 text-xs">
                  {article.refs.map((r, i) => (
                    <li key={`${r.url}-${i}`}>
                      {r.url ? (
                        <a href={r.url} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
                          {r.title}
                        </a>
                      ) : r.title}
                    </li>
                  ))}
                </ol>
              </section>
            )}

            <div className="mt-6 flex justify-center">
              <Button onClick={askAbout} size="sm">
                <MessageSquareQuote className="mr-1.5 h-4 w-4" />
                就本主题提问
              </Button>
            </div>

            {!!related.length && (
              <section className="mt-8">
                <h2 className="mb-2.5 text-sm font-medium">{t("k_b5ab51f558")}</h2>
                <div className="grid gap-2.5 sm:grid-cols-2">
                  {related.map(r => (
                    <button
                      key={r.slug}
                      type="button"
                      onClick={() => onOpenArticle(r.slug)}
                      className="rounded-xl border bg-card p-3.5 text-left transition-colors hover:border-primary/50 hover:bg-muted/50"
                    >
                      <span className="line-clamp-2 text-[13px] font-medium leading-snug">{r.title}</span>
                      <span className="mt-1.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                        {relTime(r.published_at || r.updated_at)}
                        <ArrowRight className="ml-auto h-3.5 w-3.5" />
                      </span>
                    </button>
                  ))}
                </div>
              </section>
            )}
          </article>
        )}
      </div>
    </Shell>
  );
}
