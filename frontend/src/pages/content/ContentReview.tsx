import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, CalendarClock, CheckCircle2, Eye } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import MarkdownWithCharts from '@/components/MarkdownWithCharts';
import {
  contentApi, contentError, type ContentArticle,
} from '@/api/content';
import ArticleFiles from './ArticleFiles';
import {
  EmptyState, ErrorBanner, GateList, LoadingRow, NoticeBanner, StatusBadge,
  TagChips, formatDate, useContentPerms,
} from './shared';

/**
 * 审核发布工作台。
 *
 * 三条硬约束（都在服务端把关，这里负责把它们**演出来**而不是藏起来）：
 * 1. 审核人必须**逐条**确认软提醒才能通过 —— 少勾一条服务端就 409，所以勾选状态要真回传；
 * 2. 作者不能审核自己写的稿（服务端 403），界面直接把这条原因显示出来，不做"看起来成功了"；
 * 3. 通过与发布是两个动作、两个权限码（`article:review` / `article:publish`）。
 *    一个只被勾了审核权的角色可以批，但批完必须交给有发布权的人 —— 这是分级审核的前提。
 */
export default function ContentReview() {
  const navigate = useNavigate();
  const { has } = useContentPerms();
  const canReview = has('article:review');
  const canPublish = has('article:publish');

  const [queue, setQueue] = useState<ContentArticle[]>([]);
  const [expired, setExpired] = useState<ContentArticle[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const [activeSlug, setActiveSlug] = useState('');
  const [draft, setDraft] = useState<ContentArticle | null>(null);
  const [draftLoading, setDraftLoading] = useState(false);
  const [acks, setAcks] = useState<Record<string, boolean>>({});
  const [comment, setComment] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    contentApi.pendingReviews({ page: 1, page_size: 50 })
      .then(res => {
        setQueue(res.data.items);
        setTotal(res.data.total);
        setExpired(res.data.review_expired);
        if (res.data.items.length && !activeSlug) setActiveSlug(res.data.items[0].slug);
      })
      .catch(err => setError(contentError(err)))
      .finally(() => setLoading(false));
  }, [activeSlug]);

  useEffect(load, [load]);

  const openDraft = useCallback((slug: string) => {
    setDraftLoading(true);
    setAcks({});
    setComment('');
    contentApi.reviewDraft(slug)
      .then(res => setDraft(res.data))
      .catch(err => { setError(contentError(err)); setDraft(null); })
      .finally(() => setDraftLoading(false));
  }, []);

  useEffect(() => { if (activeSlug) openDraft(activeSlug); }, [activeSlug, openDraft]);

  const warnings = draft?.gate_warnings ?? [];
  const allAcked = warnings.every(w => acks[w.code]);

  const approve = () => {
    if (!draft) return;
    setError(''); setNotice('');
    contentApi.approve(draft.slug, comment.trim(), warnings.map(w => ({ ...w, acknowledged: !!acks[w.code] })))
      .then(res => { setNotice(res.data.message); load(); })
      .catch(err => setError(contentError(err)));
  };

  const reject = () => {
    if (!draft) return;
    if (!comment.trim()) {
      // 前端先提醒一次，服务端仍会拒（驳回必须有原因，作者要据此改）
      setError('驳回必须写原因，作者要据此修改。');
      return;
    }
    setError(''); setNotice('');
    contentApi.reject(draft.slug, comment.trim())
      .then(res => { setNotice(res.data.message); load(); })
      .catch(err => setError(contentError(err)));
  };

  const publish = (slug: string) => {
    setError(''); setNotice('');
    contentApi.publish(slug, {})
      .then(res => { setNotice(res.data.message); load(); })
      .catch(err => setError(contentError(err)));
  };

  if (!canReview && !canPublish) {
    return (
      <EmptyState
        title="没有审核或发布权限"
        hint="「审核发布」需要 article:review（审核）或 article:publish（发布）权限，请联系管理员在「角色与菜单」里配置。"
      />
    );
  }

  return (
    <div className="space-y-4">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">审核发布</h1>
        <p className="text-sm text-muted-foreground">
          待审 {total} 篇 · 到期复核 {expired.length} 篇。审核是人的判断，AI 不参与批与发。
        </p>
      </header>

      <ErrorBanner message={error} onRetry={load} />
      <NoticeBanner message={notice} onDismiss={() => setNotice('')} />

      {!!expired.length && (
        <section className="rounded-xl border border-amber-500/40 bg-amber-500/5 p-3">
          <h2 className="mb-2 flex items-center gap-1.5 text-sm font-medium text-amber-700 dark:text-amber-400">
            <CalendarClock className="h-4 w-4" />到期未复核（已上线，但建议复核日已过）
          </h2>
          <ul className="space-y-1 text-xs">
            {expired.map(e => (
              <li key={e.slug} className="flex items-center gap-2">
                <button type="button" className="flex-1 truncate text-left hover:text-primary" onClick={() => navigate(`/content/preview/${e.slug}`)}>
                  {e.title}
                </button>
                <span className="text-muted-foreground">复核日 {formatDate(e.review_by)}</span>
                <Button size="sm" variant="ghost" aria-label={`查看 ${e.title}`} onClick={() => navigate(`/content/preview/${e.slug}`)}>
                  <Eye className="h-3.5 w-3.5" />
                </Button>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] text-muted-foreground">
            这里只提醒不自动下架：过期内容仍在线，需要人确认数字与法规条款是否还成立，再决定改稿或下架。
          </p>
        </section>
      )}

      {loading && <LoadingRow text="正在加载待审队列" />}

      {/* 队列拉失败时不得画“没有待处理的内容”：那会把接口失败伪装成空队列 */}
      {!loading && !error && queue.length === 0 && (
        <EmptyState title="没有待处理的内容" hint="新提交审核的稿子会出现在这里。" />
      )}

      {!loading && queue.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
          <aside className="space-y-1.5">
            {queue.map(a => (
              <button
                key={a.slug}
                type="button"
                onClick={() => setActiveSlug(a.slug)}
                className={`w-full rounded-lg border p-3 text-left transition-colors
                  ${activeSlug === a.slug ? 'border-primary bg-primary/5' : 'hover:bg-accent'}`}
              >
                <p className="truncate text-sm font-medium">{a.title}</p>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px]">
                  <span className="rounded bg-muted px-1.5 py-0.5">{a.category_label}</span>
                  <StatusBadge article={a} />
                  {!!a.gate_warnings?.length && (
                    <span className="flex items-center gap-1 text-amber-600">
                      <AlertTriangle className="h-3 w-3" />{a.gate_warnings.length}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-[11px] text-muted-foreground">提交人 {a.submitted_by || a.author}</p>
              </button>
            ))}
          </aside>

          <section className="space-y-3">
            {draftLoading && <LoadingRow text="正在加载稿件" />}

            {!draftLoading && draft && (
              <>
                <div className="rounded-xl border bg-card p-4">
                  <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                    <span className="rounded-md bg-muted px-1.5 py-0.5">{draft.category_label}</span>
                    <StatusBadge article={draft} />
                    {!draft.is_first_release && (
                      <span className="rounded-md bg-sky-500/15 px-1.5 py-0.5 text-sky-600 dark:text-sky-400">
                        改动稿（线上已有版本）
                      </span>
                    )}
                    <Button size="sm" variant="ghost" className="ml-auto h-6 px-2 text-xs" onClick={() => navigate(`/content/preview/${draft.slug}`)}>
                      看线上版本
                    </Button>
                  </div>
                  <h2 className="text-lg font-semibold leading-7">{draft.title}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">{draft.summary}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
                    <span>作者 {draft.author}</span>
                    <span>提交人 {draft.submitted_by || draft.author}</span>
                    <span className="flex items-center gap-1"><Eye className="h-3 w-3" />{draft.view_count}</span>
                    {draft.review_by && <span>建议复核日 {formatDate(draft.review_by)}</span>}
                  </div>
                  <div className="mt-2"><TagChips tags={draft.tags} /></div>
                  {!!draft.refs?.length && (
                    <ol className="mt-3 list-decimal space-y-0.5 border-t pt-2 pl-5 text-[11px] text-muted-foreground">
                      {draft.refs.map((r, i) => <li key={i}>{r.title}{r.url ? ` · ${r.url}` : ''}</li>)}
                    </ol>
                  )}
                </div>

                {!!draft.gate_hard?.length && <GateList items={draft.gate_hard} kind="hard" />}
                {!!warnings.length && (
                  <GateList
                    items={warnings}
                    kind="warning"
                    acknowledge={acks}
                    onAck={(code, v) => setAcks(prev => ({ ...prev, [code]: v }))}
                  />
                )}

                <div className="space-y-1.5 rounded-xl border bg-card p-4">
                  <div className="max-h-[420px] overflow-y-auto">
                    <MarkdownWithCharts text={draft.body || ''} className="text-sm leading-7" />
                  </div>
                </div>

                {/* 附件：审核人必能下载。挂 scope=reviews 走 article:review 自己裁决 ——
                    只勾审核权的角色拿不到 articles*，看不到附件的审核是空承诺。 */}
                <div className="rounded-xl border bg-card p-4">
                  <ArticleFiles
                    slug={draft.slug}
                    files={draft.files ?? []}
                    scope="reviews"
                    onRefresh={() => openDraft(draft.slug)}
                  />
                </div>

                <div className="space-y-2 rounded-xl border bg-card p-4">
                  <Label htmlFor="rv-comment">审核意见</Label>
                  <Textarea
                    id="rv-comment" rows={2} value={comment} onChange={e => setComment(e.target.value)}
                    placeholder="通过可留空；驳回必须写清要怎么改"
                  />
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    {canReview && (
                      <>
                        <Button onClick={approve} disabled={!allAcked}>
                          <CheckCircle2 className="mr-1.5 h-4 w-4" />审核通过
                        </Button>
                        <Button variant="outline" onClick={reject}>驳回</Button>
                      </>
                    )}
                    {canPublish && draft.status === 'approved' && (
                      <Button variant="default" onClick={() => publish(draft.slug)}>发布上线</Button>
                    )}
                    {!canReview && canPublish && (
                      <span className="text-xs text-muted-foreground">你只有发布权：需要审核人先通过后才能发布。</span>
                    )}
                    {canReview && draft.status === 'approved' && !canPublish && (
                      <span className="text-xs text-muted-foreground">
                        已审核通过。发布需要「发布与下架内容」权限，本次改动不会自动上线。
                      </span>
                    )}
                    {!!warnings.length && !allAcked && (
                      <span className="text-xs text-amber-600">
                        还有 {warnings.filter(w => !acks[w.code]).length} 条提醒未确认。
                      </span>
                    )}
                  </div>
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
