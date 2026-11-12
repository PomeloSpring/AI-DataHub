import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, CalendarClock, ExternalLink, FileText, PencilLine, EyeOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import MarkdownWithCharts from '@/components/MarkdownWithCharts';
import HtmlArticle from '@/components/HtmlArticle';
import { containerForKind, widthModeForKind } from '@/lib/pageWidth';
import { revealClass } from '@/lib/motion';
import { useMotion } from '@/hooks/useMotion';
import { contentApi, contentError, VERSIONING_LABELS,
  type ContentArticle, type ReleaseItem } from '@/api/content';
import ArticleFiles from './ArticleFiles';
import { ErrorBanner, LoadingRow, StatusBadge, TagChips, formatDate } from './shared';

/**
 * 稿件预览（内容中心内部视图，**不是资讯门户**）。
 *
 * 门户是给全员消费的模块（栏目导航、标签筛选、搜索、阅读统计），尚未开发。
 * 这一页只回答编排阶段的四个问题：
 * 1. 读者会看到什么 —— live 版本长什么样；
 * 2. 改动生效了吗 —— head 版本与 live 的切换（默认读 live，未发布时读 head）；
 * 3. 历史期次长什么样 —— 已发布过的每一期都能单独预览（编排面拿全量，含被
 *    「只保留最新版本」策略收掉读者展示面的那些期，并就地标注读者现在看不到它）；
 * 4. 发布前还缺什么 —— 复核到期日、出处清单、形态提示。
 *
 * 因此这里**不**计阅读数（`count_view` 默认 0）、不拉相关推荐：那些是门户的行为，
 * 在编排页做会把统计污染成"同事审了几次"。
 */
export default function ContentPreview() {
  const { slug = '' } = useParams();
  const navigate = useNavigate();
  // 正文出现效果与门户同一份解析（预览里看到的就等读者看到的）；默认档 reveal=none 不加任何 class
  const { motion, cssVars } = useMotion();
  const reveal = revealClass(motion.reveal);

  const [article, setArticle] = useState<ContentArticle | null>(null);
  const [revision, setRevision] = useState<'live' | 'head'>('live');
  // 正在预览的历史期次（0 = 不按期次读，走 revision 指针）
  const [release, setRelease] = useState(0);
  const [releases, setReleases] = useState<ReleaseItem[]>([]);
  const [attachmentsShared, setAttachmentsShared] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    // 选了历史期次就不能再要 head（服务端拒同时给）：历史那一期只能是已发布版本
    const rev = release > 0 ? 'live' : revision;
    contentApi.detail(slug, rev, false, release)
      .then(res => setArticle(res.data))
      .catch(err => { setError(contentError(err)); setArticle(null); })
      .finally(() => setLoading(false));
  }, [slug, revision, release]);

  useEffect(load, [load]);

  // 期次候选单独取一次（与详情同源的服务端清单，前端不自己拼期次）
  useEffect(() => {
    if (!slug) return;
    let alive = true;
    contentApi.releases(slug)
      .then(res => {
        if (!alive) return;
        setReleases(res.data.items);
        setAttachmentsShared(!!res.data.attachments_shared);
      })
      // 拉不到就只隐藏期次控件并保留错误：不能伪装成“这篇没有历史”
      .catch(err => { if (alive) setError(contentError(err)); });
    return () => { alive = false; };
  }, [slug]);

  // 宽度与门户阅读页同一口径（lib/pageWidth）：预览窄、线上宽（或反过来）就是双口径，
  // 作者在这里看到的栅格布局就不是读者看到的那一份。
  const shell = containerForKind(article?.kind);

  return (
    <div data-page-width={widthModeForKind(article?.kind)} className={`${shell} space-y-4`}>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="ghost" onClick={() => navigate('/content/manage')}>
          <ArrowLeft className="mr-1 h-4 w-4" />返回内容管理
        </Button>
        {/* 期次切换：与报告中心“只有一版不给下拉”同一判据（候选 ≤ 1 就不画开关） */}
        {releases.length > 1 && (
          <Select value={String(release)} onValueChange={v => setRelease(Number(v))}>
            <SelectTrigger className="w-[210px]" aria-label="选择要预览的期次">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="0">当前线上（第 {article?.release ?? 0} 期）</SelectItem>
              {releases.map(r => (
                <SelectItem key={r.release} value={String(r.release)}>
                  第 {r.release} 期·{r.label}{r.is_current ? '（线上）' : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {release === 0 && article?.has_pending_change && (
          <Button size="sm" variant={revision === 'head' ? 'default' : 'outline'} className="ml-auto"
            onClick={() => setRevision(r => (r === 'head' ? 'live' : 'head'))}>
            <PencilLine className="mr-1 h-3.5 w-3.5" />
            {revision === 'head' ? '看线上版本' : '看改动后的新版本'}
          </Button>
        )}
      </div>

      <div className="flex items-start gap-2 rounded-lg border bg-muted/30 p-3 text-xs leading-5">
        <EyeOff className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="text-muted-foreground">
          这是内容中心的编排预览，只有具备编辑权限的人看得到，也不计入阅读数。
          正式对全员展示的资讯门户是独立模块，内容必须经这里发布后才能进入门户。
        </span>
      </div>

      {loading && <LoadingRow text="正在加载稿件" />}
      <ErrorBanner message={error} onRetry={load} />

      {/* 历史期次的三个事实必须同时说出：它是哪一期、它现在是不是线上展示的那一期、
          附件为什么不跟着换（附件不进版本链是已知取舍，静默会让人以为翻到了当期附件） */}
      {!loading && article && release > 0 && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-xs leading-5">
          <p>
            正在预览<strong>第 {article.viewing_release ?? release} 期</strong>
            {article.viewing_release_label ? `〈${article.viewing_release_label}〉` : ''}
            {article.viewing_is_current
              ? '（就是当前线上展示的那一期）'
              : '，它不是当前展示的那一期：读者现在从门户进去看到的是第 '
                + `${article.release} 期〈${article.release_label}〉`}
            。
          </p>
          {article.history_hidden && (
            <p className="mt-1 text-amber-700 dark:text-amber-400">
              本篇策略为「{article.versioning_label || VERSIONING_LABELS.latest_only}」：
              除线上那一期外的历史期次只对编排面开放，读者页面拿不到它们。
            </p>
          )}
          {attachmentsShared && (
            <p className="mt-1 text-muted-foreground">
              附件挂在条目上、不进期次版本链：这里列出的附件就是当前那份，不会随期次而变。
            </p>
          )}
        </div>
      )}

      {!loading && !error && !article && (
        <div className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
          内容不存在或已被删除。
        </div>
      )}

      {article && (
        <article className="space-y-4">
          <header className="space-y-2 border-b pb-4">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span
                className="rounded-md px-1.5 py-0.5 font-medium"
                style={{ backgroundColor: `${article.category_color}1a`, color: article.category_color }}
              >
                {article.category_icon ? `${article.category_icon} ` : ''}{article.category_label}
              </span>
              <StatusBadge article={article} />
              {!!article.release && (
                <span className="rounded-md bg-sky-500/15 px-1.5 py-0.5 font-medium text-sky-600 dark:text-sky-400"
                  title={article.history_hidden
                    ? '只保留最新版本：历史期次不对读者展示' : '读者可在本页翻看历史期次'}>
                  第 {article.release} 期{article.release_label ? `·${article.release_label}` : ''}
                  {article.release_count > 1 ? ` / 共 ${article.release_count} 期` : ''}
                </span>
              )}
              {revision === 'head' && release === 0 && article.has_pending_change && (
                <span className="rounded-md bg-amber-500/15 px-1.5 py-0.5 font-medium text-amber-600 dark:text-amber-400">
                  未发布的新版本
                </span>
              )}
            </div>
            <h1 className="text-2xl font-semibold leading-8">{article.title}</h1>
            <p className="text-sm leading-6 text-muted-foreground">{article.summary}</p>
            <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
              <span>发布日 {(article.published_at || '—').slice(0, 10) || '—'}</span>
              <span>作者 {article.author}</span>
              <span>最后编辑 {article.updated_by || article.author} · {article.updated_at.slice(0, 16).replace('T', ' ')}</span>
              {article.review_by && (
                <span className="flex items-center gap-1">
                  <CalendarClock className="h-3 w-3" />建议 {formatDate(article.review_by)} 前复核
                </span>
              )}
            </div>
            <div><TagChips tags={article.tags} /></div>
          </header>

          {article.kind === 'link' && article.link_url && (
            <div className="rounded-xl border bg-card p-4">
              <p className="mb-2 text-sm">
                外链条目：正文在原始站点维护，站内只保留导读，避免抄一份后两边口径不一致。
              </p>
              <Button asChild variant="outline" size="sm">
                <a href={article.link_url} target="_blank" rel="noopener noreferrer">
                  <ExternalLink className="mr-1.5 h-3.5 w-3.5" />{article.link_label || '打开原文'}
                </a>
              </Button>
            </div>
          )}

          <div className="rounded-xl border bg-card p-5">
            {article.body?.trim()
              ? (article.kind === 'html'
                // 与门户同一份清洗渲染口：预览里看到的就等读者看到的，不给“这里好看那里缺块”留空间
                ? (reveal
                  ? <div className={reveal} style={cssVars}><HtmlArticle html={article.body} className="text-sm leading-7" /></div>
                  : <HtmlArticle html={article.body} className="text-sm leading-7" />)
                : <MarkdownWithCharts text={article.body} className="text-sm leading-7" reveal />)
              : <p className="text-center text-sm text-muted-foreground">这篇没有站内正文。</p>}
          </div>

          {/* 附件卡：预览页只给下载（与门户一致），上传/删除回编辑器做 */}
          {!!article.files?.length && (
            <section className="rounded-xl border bg-card p-4">
              <ArticleFiles
                slug={article.slug}
                files={article.files}
                scope="articles"
                onRefresh={load}
              />
            </section>
          )}

          {!!article.refs?.length && (
            <section className="rounded-xl border bg-muted/30 p-4">
              <h2 className="mb-2 flex items-center gap-1.5 text-sm font-medium">
                <FileText className="h-4 w-4" />引用出处
              </h2>
              <p className="mb-2 text-[11px] text-muted-foreground">
                文中数值以原文为准。审核人据此核对，正文里的外部数字必须有出处。
              </p>
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
        </article>
      )}
    </div>
  );
}
