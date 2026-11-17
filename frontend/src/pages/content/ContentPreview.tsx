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
import { useTranslation } from 'react-i18next';

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
  const { t } = useTranslation('ui');
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
          <ArrowLeft className="mr-1 h-4 w-4" />{t('k_8cfbef1bc1')}
        </Button>
        {/* 期次切换：与报告中心“只有一版不给下拉”同一判据（候选 ≤ 1 就不画开关） */}
        {releases.length > 1 && (
          <Select value={String(release)} onValueChange={v => setRelease(Number(v))}>
            <SelectTrigger className="w-[210px]" aria-label={t("k_f4793ce0f0")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="0">{t('k_dd89c5ed8d', { p0: article?.release ?? 0 })}</SelectItem>
              {releases.map(r => (
                <SelectItem key={r.release} value={String(r.release)}>
                  {t('k_9cc86af559', { p0: r.release, p1: r.label, p2: r.is_current ? t('k_437ed0e858') : '' })}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {release === 0 && article?.has_pending_change && (
          <Button size="sm" variant={revision === 'head' ? 'default' : 'outline'} className="ml-auto"
            onClick={() => setRevision(r => (r === 'head' ? 'live' : 'head'))}>
            <PencilLine className="mr-1 h-3.5 w-3.5" />
            {revision === 'head' ? t('k_ebebf44318') : t('k_73619cbf97')}
          </Button>
        )}
      </div>

      <div className="flex items-start gap-2 rounded-lg border bg-muted/30 p-3 text-xs leading-5">
        <EyeOff className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="text-muted-foreground">
          {t('k_b11ec67e21')}
        </span>
      </div>

      {loading && <LoadingRow text={t('k_5537e7bc0b')} />}
      <ErrorBanner message={error} onRetry={load} />

      {/* 历史期次的三个事实必须同时说出：它是哪一期、它现在是不是线上展示的那一期、
          附件为什么不跟着换（附件不进版本链是已知取舍，静默会让人以为翻到了当期附件） */}
      {!loading && article && release > 0 && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-xs leading-5">
          <p>
            {t('k_f5278865ea')}<strong>{t('k_59151b6db0', { p0: article.viewing_release ?? release })}</strong>
            {article.viewing_release_label ? `〈${article.viewing_release_label}〉` : ''}
            {article.viewing_is_current
              ? t('k_947a344185')
              : t('k_77064f1024', { p0: article.release, p1: article.release_label })}
            {t('k_81197c2d02')}
          </p>
          {article.history_hidden && (
            <p className="mt-1 text-amber-700 dark:text-amber-400">
              {t('k_1be348feb6', { p0: article.versioning_label || VERSIONING_LABELS.latest_only })}</p>
          )}
          {attachmentsShared && (
            <p className="mt-1 text-muted-foreground">
              {t('k_f3bd7b68f6')}
            </p>
          )}
        </div>
      )}

      {!loading && !error && !article && (
        <div className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
          {t('k_6225db39c1')}
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
                    ? t('k_b050086876') : t('k_c291314fdc')}>
                  {t('k_b6e6d6552d', { p0: article.release, p1: article.release_label ? `·${article.release_label}` : '', p2: article.release_count > 1 ? t('k_ec0696f41e', { p0: article.release_count }) : '' })}
                </span>
              )}
              {revision === 'head' && release === 0 && article.has_pending_change && (
                <span className="rounded-md bg-amber-500/15 px-1.5 py-0.5 font-medium text-amber-600 dark:text-amber-400">
                  {t('k_34fd7ecda1')}
                </span>
              )}
            </div>
            <h1 className="text-2xl font-semibold leading-8">{article.title}</h1>
            <p className="text-sm leading-6 text-muted-foreground">{article.summary}</p>
            <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
              <span>{t('k_07353da050', { p0: (article.published_at || '—').slice(0, 10) || '—' })}</span>
              <span>{t('k_d86da8537c', { p0: article.author })}</span>
              <span>{t('k_66f24f95ab', { p0: article.updated_by || article.author, p1: article.updated_at.slice(0, 16).replace('T', ' ') })}</span>
              {article.review_by && (
                <span className="flex items-center gap-1">
                  <CalendarClock className="h-3 w-3" />{t('k_7eda415cd1', { p0: formatDate(article.review_by) })}
                </span>
              )}
            </div>
            <div><TagChips tags={article.tags} /></div>
          </header>

          {article.kind === 'link' && article.link_url && (
            <div className="rounded-xl border bg-card p-4">
              <p className="mb-2 text-sm">
                {t('k_02c0040664')}
              </p>
              <Button asChild variant="outline" size="sm">
                <a href={article.link_url} target="_blank" rel="noopener noreferrer">
                  <ExternalLink className="mr-1.5 h-3.5 w-3.5" />{article.link_label || t('k_28f2fe4b7f')}
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
              : <p className="text-center text-sm text-muted-foreground">{t("k_9e4a662dd6")}</p>}
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
                <FileText className="h-4 w-4" />{t('k_b2fc15da9e')}
              </h2>
              <p className="mb-2 text-[11px] text-muted-foreground">
                {t('k_c5b776890b')}
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
