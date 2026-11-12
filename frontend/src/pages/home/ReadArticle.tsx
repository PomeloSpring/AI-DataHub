import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight, CalendarClock, ExternalLink, FileText, MessageSquareQuote } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { useBrandStore } from '@/stores/brandStore';
import { DEFAULT_APP_NAME } from '@/lib/brand';
import BrandLogo from '@/components/BrandLogo';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import SectionSwitcher from '@/components/SectionSwitcher';
import ReleaseNotesButton from '@/components/ReleaseNotesButton';
import MarkdownWithCharts from '@/components/MarkdownWithCharts';
import HtmlArticle from '@/components/HtmlArticle';
import { containerForKind, widthModeForKind } from '@/lib/pageWidth';
import { revealClass } from '@/lib/motion';
import { useMotion } from '@/hooks/useMotion';
import { contentApi, contentError, type ContentArticle, type ReleaseItem } from '@/api/content';
import { useTaxonomy } from '@/pages/content/shared';
import ArticleFiles from '@/pages/content/ArticleFiles';
import { ErrorBanner } from '@/pages/content/shared';
import { CategoryBadge, relTime, stashQuestion, useReadState, viewCount } from './shared';

/**
 * 文章阅读页 `/read/:slug` —— 首页资讯区的落点（面向全体登录读者，不是编排预览）。
 *
 * 与 `/content/preview/:slug` 的分工是刻意的，两页不能合并：
 * | | 阅读页（本页） | 编排预览页 |
 * |---|---|---|
 * | 谁能进 | 具备 `article:read` 的登录用户 | 仅具备 `article:manage` 的人 |
 * | 读哪个版本 | 只有 live（未发布直接 404，服务端裁决）+ 已发布的历史期次 | live 与 head 可切 |
 * | 计不计阅读数 | **计**（`count_view=1`，门户统计的唯一来源） | 不计（否则"最多阅读"会排成"最近被同事审过几篇"） |
 *
 * 另外两件事不在这里做，也不是漏做：
 * - **附件只下载、不站内预览**：服务端下载头恒为 `attachment`（把上传的 HTML/PDF 以本站
 *   源 inline 渲染 = 存储型 XSS），Office 版式也无法站内还原；
 * - **不做「上一篇/下一篇」**：那需要按门户顺序的定位接口（现在只有同栏目推荐
 *   `related`），硬凑会把跨栏目的文章串成假邻接。底部给「同栏目其它内容」。
 *
 * 期次（历史版本）在本页的三条口径：
 * - 期次号走 URL（`/read/:slug?release=N`）：它业务可读、能直接分享，revision id 不下发也不收；
 * - **只有一期时不画下拉**（与报告中心“只有一版不给下拉”同口径）：一个只能选中自己的开关
 *   比没有开关更容易让人以为“历史丢了”；
 * - 策略为「只保留最新版本」时不画下拉并说明“该内容只发布最新版本”：读者页面拿不到旧期
 *   是产品策略，不是漏做（服务端 `releases` 已按同一口径收过，前端不另判）。
 */
export default function ReadArticle() {
  const { slug = '' } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  // 期次在 URL 里（分享链接能直接指到旧那一期）：非法值归 0 = 看当前那一期
  const release = Number.isFinite(Number(searchParams.get('release')))
    ? Math.max(0, Math.trunc(Number(searchParams.get('release') || 0))) : 0;
  const { brand, fetchBrand } = useBrandStore();
  const getDefaultWorkspaceId = useWorkspaceStore(s => s.getDefaultWorkspaceId);
  const { categories } = useTaxonomy();
  const { mark } = useReadState();

  const [article, setArticle] = useState<ContentArticle | null>(null);
  const [related, setRelated] = useState<ContentArticle[]>([]);
  const [releases, setReleases] = useState<ReleaseItem[]>([]);
  const [historyHidden, setHistoryHidden] = useState(false);
  const [attachmentsShared, setAttachmentsShared] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    setArticle(null);
    // revision 固定 live + countView=true：这条路径就是"读者在读"，没有第二种口径
    // release>0 读历史期次（服务端按策略裁决可读性：只留最新时旧期按不存在处理）
    contentApi.detail(slug, 'live', true, release)
      .then(({ data }) => {
        setArticle(data);
        mark(data.slug);
        contentApi.related(data.slug).then(r => setRelated(r.data.items || [])).catch(() => setRelated([]));
      })
      .catch(err => setError(contentError(err)))
      .finally(() => setLoading(false));
  }, [slug, release, mark]);

  useEffect(load, [load]);
  useEffect(() => { void fetchBrand(); }, [fetchBrand]);

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

  const pickRelease = (value: string) => {
    const next = new URLSearchParams(searchParams);
    // 0 = 当前那一期，不往 URL 里留个无意义的参数（保持旧链接形状不变）
    if (value === '0') next.delete('release');
    else next.set('release', value);
    setSearchParams(next, { replace: true });
    // 换期次要回顶：不然会停在旧位置读下一期的中段
    window.scrollTo({ top: 0 });
  };

  // 带上下文进智能问数：落点与首页/模块切换同口径（默认工作空间的 chat），
  // 没有默认空间时落 /workspace 由入口页解析 —— 不硬编码 /ws/0。
  // 不传 send：递过去的是一句开头（等人补全具体要问什么），预填即可，自动发送只会问出一句空话。
  const askAbout = () => {
    if (!article) return;
    stashQuestion(`关于《${article.title}》：`);
    const wsId = getDefaultWorkspaceId();
    navigate(wsId ? `/ws/${wsId}/chat` : '/workspace');
  };

  const cat = categories.find(c => c.key === (article?.category_label ?? '') || c.label === article?.category_label);
  // 正文出现效果（个人偏好面，不看板上挂载）：默认档 reveal=none → 下面一个 class 都不加。
  // HTML 版式只作用外层容器（不往正文里注 <style>/animation，sanitize 白名单不因此动一格）。
  const { motion, cssVars } = useMotion();
  const reveal = revealClass(motion.reveal);
  // 宽度按内容形态分流（口径只有一份，见 lib/pageWidth）：HTML 版式与报告类富排版充满页面，
  // markdown 长文保持窄栏。加载中还不知道形态，先按阅读宽度占位（拉到文章后可能变宽，
  // 只发生在首屏骨架阶段，不是一边读一边跳）。
  const shell = containerForKind(article?.kind);

  // 滚动口径：#root 是 100vh + overflow:hidden（全站约定），顶层路由没有布局壳就得自带滚动口 ——
  // 外壳 flex 列、头部当固定项、正文区自己 overflow-auto。少了这一层，超出视口的部分不是
  // "藏起来了"，而是根本滚不到；HTML 版式与长文最先撞上这个差别。
  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header className="z-10 border-b bg-background/95 backdrop-blur">
        <div className={`${shell} flex items-center justify-between py-2.5`}>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => navigate('/')}
              className="flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              <ArrowLeft className="h-4 w-4" />
              返回首页
            </button>
            <span className="flex items-center gap-2">
              <BrandLogo brand={brand} size={24} />
              {brand.show_text && <span className="text-sm font-semibold tracking-tight">{brand.app_name || DEFAULT_APP_NAME}</span>}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <ReleaseNotesButton />
            <SectionSwitcher current="home" />
          </div>
        </div>
      </header>

      {/* 滚动口在 main 自身；relative 让正文里的 position:absolute 后代（时间轴竖线与圆点）
          以本口为包含块，不会越过裁剪去撑高 document（那会变成整页被滚动） */}
      <main data-page-width={widthModeForKind(article?.kind)} className="relative min-h-0 flex-1 overflow-auto">
        <div className={`${shell} py-6`}>
        {loading && (
          <div className="space-y-4">
            <Skeleton className="h-5 w-64" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        )}

        {!loading && error && (
          <ErrorBanner message={error} onRetry={load} />
        )}

        {!loading && !error && !article && (
          <div className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
            这篇内容不存在，或已被下架。
          </div>
        )}

        {article && (
          <article>
            {/* 面包屑：栏目用业务名，不显示 category_id */}
            <nav aria-label="面包屑" className="mb-3 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <button type="button" onClick={() => navigate('/')} className="hover:text-primary">首页</button>
              <span>·</span>
              <span>{article.category_label || '栏目未知'}</span>
              <span>·</span>
              <span className="truncate text-foreground/80">{article.title}</span>
            </nav>

            <header className="border-b pb-4">
              <div className="flex flex-wrap items-center gap-1.5">
                <CategoryBadge category={cat} />
                {article.pinned ? <span className="rounded-md bg-primary/12 px-1.5 py-0.5 text-[11px] font-medium text-primary">置顶</span> : null}
                {/* 期次写给读者：“你读到的是哪一期”，与月报这类递进内容的时态直接相关 */}
                {(article.viewing_release ?? article.release) > 0 && (
                  <span className="rounded-md bg-sky-500/15 px-1.5 py-0.5 text-[11px] font-medium text-sky-600 dark:text-sky-400"
                    title={article.release_count > 1 ? `本篇共 ${article.release_count} 期` : undefined}>
                    第 {article.viewing_release || article.release} 期
                    {article.viewing_release_label || article.release_label
                      ? `·${article.viewing_release_label || article.release_label}` : ''}
                  </span>
                )}
                {/* markdown 与 HTML 版式都不画形态徽标：两者都是站内正文，只是排版能力不同，
                    给读者标「HTML 版式」纯属编排细节噪声；link/file 才需要预告“点不开” */}
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

            {/* 历史期次入口：候选 > 1 才画（只有一期时下拉只能选中自己）；
                策略为只留最新时不画下拉，但要把“为什么没有历史”说出来，不然像功能没做 */}
            {releases.length > 1 && (
              <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span>历史期次</span>
                <Select value={String(release)} onValueChange={pickRelease}>
                  <SelectTrigger className="w-[240px]" aria-label="选择要阅读的历史期次">
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
                <button type="button" className="ml-1.5 text-primary hover:underline" onClick={() => pickRelease('0')}>
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
                  // HTML 版式走清洗渲染口（白名单与服务端门禁同源），不进 markdown 管线
                  ? (reveal
                    ? <div className={reveal} style={cssVars}><HtmlArticle html={article.body} className="text-sm leading-7" /></div>
                    : <HtmlArticle html={article.body} className="text-sm leading-7" />)
                  : <MarkdownWithCharts text={article.body} className="text-sm leading-7" reveal />)
                : <p className="text-center text-sm text-muted-foreground">这篇没有站内正文，内容以附件或原文形式提供。</p>}
            </div>

            {!!article.files?.length && (
              <section className="mt-4 rounded-xl border bg-card p-4">
                <h2 className="mb-2 flex items-center gap-1.5 text-sm font-medium">
                  <FileText className="h-4 w-4" />附件
                </h2>
                {/* editable=false：读者只能取文件，上传/删除回内容中心做 */}
                <ArticleFiles slug={article.slug} files={article.files} scope="articles" editable={false} />
                {/* 附件不进期次版本链是已知取舍：读旧期时不说这句，读者会以为拿到的是当期那份 */}
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
                <p className="mb-2 text-[11px] text-muted-foreground">文中数值以原文为准；外部数字必须有出处才能发布。</p>
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
                <h2 className="mb-2.5 text-sm font-medium">同栏目其它内容</h2>
                <div className="grid gap-2.5 sm:grid-cols-2">
                  {related.map(r => (
                    <button
                      key={r.slug}
                      type="button"
                      onClick={() => navigate(`/read/${encodeURIComponent(r.slug)}`)}
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
      </main>
    </div>
  );
}
