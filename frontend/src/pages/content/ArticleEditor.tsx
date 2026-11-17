import { useCallback, useEffect, useState } from 'react';
import { History, Plus, Save, Send, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { contentApi, contentError, VERSIONING_HINTS, VERSIONING_LABELS,
  type ArticleKind, type ArticleVersioning, type ContentArticle, type ContentFile,
  type GateItem, type RevisionItem } from '@/api/content';
import ArticleFiles from './ArticleFiles';
import { ErrorBanner, GateList, NoticeBanner, useTaxonomy } from './shared';
import { t as tr } from '@/i18n/runtime';
import { useTranslation } from 'react-i18next';

const NONE = '__none__';

interface DraftForm {
  title: string;
  summary: string;
  category: string;
  tags: string[];
  kind: ArticleKind;
  body: string;
  link_url: string;
  link_label: string;
  refs: { title: string; url: string }[];
  pinned: boolean;
  featured: boolean;
  review_by: string;
  /** 读者可见性策略：只决定历史期次给不给读者看，不改 head/live 指针 */
  versioning: ArticleVersioning;
}

const EMPTY: DraftForm = {
  title: '', summary: '', category: NONE, tags: [], kind: 'markdown', body: '',
  link_url: '', link_label: '', refs: [], pinned: false, featured: false, review_by: '',
  versioning: 'keep_history',
};

/**
 * 内容编辑器（新建 / 编辑 head revision）。
 *
 * 四条不能省的约束：
 * 1. **保存只动 head**。已发布内容改完正文，线上仍是旧版 —— 界面上必须说清楚，
 *    否则作者以为"发布中的文章我改了就直接生效"（或反过来以为改动丢了）。
 * 2. **标签只能从受控词表里选**，不给自由输入。自由标签会让侧栏标签云逐渐失效
 *    （门禁 tag_uncontrolled 就是这条的软提醒，但录入侧直接禁掉才是根治）。
 * 3. **乐观锁**。提交时带上进入编辑时读到的 version；服务端返 409 就说明有人先改了，
 *    必须让用户选择重新加载，**不能**把 expected_version 悄悄去掉再试一次（那等于
 *    强制覆盖他人劳动）。
 * 4. **期次与编辑历史分区显示**。「已发布期次」是发布过的版本（读者能翻到的那些），
 *    「编辑历史」只是改稿痕迹。混成一列会让人以为“存一次盘读者就能看一次”。
 *    策略为「只保留最新」时这里**仍列全量**（编排面看得到是回滚与审计的前提），
 *    只标注读者当前看不到哪几期。
 */
export default function ArticleEditor({ open, slug, onOpenChange, onSaved }: {
  open: boolean;
  /** 空 = 新建 */
  slug?: string;
  onOpenChange: (v: boolean) => void;
  onSaved: (article: ContentArticle, note: string) => void;
}) {
  const { t } = useTranslation('ui');
  const { categories, tags: vocab } = useTaxonomy();
  const [form, setForm] = useState<DraftForm>(EMPTY);
  const [version, setVersion] = useState<number | undefined>(undefined);
  const [article, setArticle] = useState<ContentArticle | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [hard, setHard] = useState<GateItem[]>([]);
  const [warns, setWarns] = useState<GateItem[]>([]);
  const [revisions, setRevisions] = useState<RevisionItem[] | null>(null);
  // 附件清单单列一份真值：上传/删除不能走 load()（那会把用户还没保存的表单重置掉）
  const [files, setFiles] = useState<ContentFile[]>([]);

  const editing = !!slug;
  /** 已落库条目的 slug：新建时保存成功后也有（附件要挂在具体条目上） */
  const savedSlug = article?.slug || slug || '';

  const load = useCallback(() => {
    if (!open) return;
    setError(''); setNotice(''); setHard([]); setWarns([]); setRevisions(null); setFiles([]);
    if (!slug) { setForm(EMPTY); setArticle(null); setVersion(undefined); return; }
    setLoading(true);
    contentApi.detail(slug, 'head')
      .then(res => {
        const a = res.data;
        setArticle(a);
        setVersion(a.version);
        setForm({
          title: a.title, summary: a.summary,
          // 栏目以 key 为业务标识（内部 id 不进请求体）
          category: a.category_id ? (categories.find(c => c.id === a.category_id)?.key ?? NONE) : NONE,
          tags: a.tags ?? [], kind: a.kind as DraftForm['kind'], body: a.body ?? '',
          link_url: a.link_url, link_label: a.link_label, refs: a.refs ?? [],
          pinned: !!a.pinned, featured: !!a.featured, review_by: a.review_by ?? '',
          // 策略取服务端下发的值（存量行没这一列时服务端已回 keep_history，前端不自判）
          versioning: (a.versioning === 'latest_only' ? 'latest_only' : 'keep_history'),
        });
        setFiles(a.files ?? []);
      })
      .catch(err => setError(contentError(err)))
      .finally(() => setLoading(false));
  }, [open, slug, categories]);

  useEffect(load, [load]);

  const patch = (p: Partial<DraftForm>) => setForm(f => ({ ...f, ...p }));

  /** 附件上传/删除后只重拉清单（详情接口带正文，重拉会把未保存的编辑覆盖掉） */
  const refreshFiles = useCallback(() => {
    const id = article?.slug || slug;
    if (!id) { setFiles([]); return; }
    contentApi.files(id)
      .then(res => setFiles(res.data.items))
      // 拉不到不能把清单当成"空": 那会让用户以为附件丢了而重传一次
      .catch(err => setError(contentError(err)));
  }, [article?.slug, slug]);

  const buildPayload = (changeNote: string) => ({
    title: form.title.trim(),
    summary: form.summary.trim(),
    category: form.category === NONE ? '' : form.category,
    tags: form.tags,
    kind: form.kind,
    // 外链的正文在原始站点，站内不留副本；正文/附件都可以带导读文字（附件形态的导读选填）
    body: form.kind === 'link' ? '' : form.body,
    link_url: form.kind === 'link' ? form.link_url.trim() : '',
    link_label: form.kind === 'link' ? form.link_label.trim() : '',
    refs: form.refs.filter(r => r.title.trim() || r.url.trim()),
    pinned: form.pinned ? 1 : 0,
    featured: form.featured ? 1 : 0,
    review_by: form.review_by || '',
    versioning: form.versioning,
    change_note: changeNote,
    ...(version !== undefined ? { expected_version: version } : {}),
  });

  /** 保存 → 可选提交审核。返回 false 表示没存成（错误已就地显示）。 */
  const save = async (submit: boolean): Promise<boolean> => {
    setSaving(true);
    setError(''); setNotice(''); setHard([]); setWarns([]);
    try {
      const saved = (editing && article
        ? await contentApi.update(article.slug, buildPayload(submit ? tr('k_a204e09c04') : tr('k_e53cd8aa36')))
        : await contentApi.create(buildPayload(tr('k_b127f2bb81')))
      ).data;
      setArticle(saved);
      setVersion(saved.version);
      if (submit) {
        const res = await contentApi.submit(saved.slug);
        setNotice(res.data.message);
        setWarns(res.data.warnings ?? []);
      } else {
        const next = (saved.kind === 'file' && !(saved.files?.length))
          // 门禁会因 file_missing 硬阻断提交，先把下一步说清，不让用户去撞 422
          ? t('k_6e3e4c9138')
          : '';
        setNotice((editing ? t('k_ccb0e902cb') : t('k_b02bdd0ffb')) + next);
      }
      onSaved(saved, submit ? t('k_792e1c4f3f') : t('k_391772f9ca'));
      return true;
    } catch (err) {
      // 409 是并发信号：ErrorBanner 的「重试」就是重新加载当前稿，不做静默重试覆盖
      setError(contentError(err));
      return false;
    } finally {
      setSaving(false);
    }
  };

  const toggleTag = (t: string) =>
    patch({ tags: form.tags.includes(t) ? form.tags.filter(x => x !== t) : [...form.tags, t] });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] max-w-4xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{editing ? t('k_0f2061105e') : t('k_f048c7b9dc')}</DialogTitle>
          <DialogDescription>
            {article?.is_live
              ? t('k_3f0ef7e451')
              : t('k_2cc5b850f8')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <ErrorBanner message={error} onRetry={editing ? load : undefined} />
          <NoticeBanner message={notice} onDismiss={() => setNotice('')} />
          {!!hard.length && <GateList items={hard} kind="hard" />}
          {!!warns.length && <GateList items={warns} kind="warning" />}

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="ce-title">{t('k_748d7dc7e3')}</Label>
              <Input id="ce-title" value={form.title} onChange={e => patch({ title: e.target.value })} placeholder={t('k_2709e72d88')} />
            </div>

            <div className="space-y-1.5">
              <Label>{t('k_a1e7989cc1')}</Label>
              <Select value={form.category} onValueChange={v => patch({ category: v })}>
                <SelectTrigger aria-label={t('k_1c5c02cd20')}><SelectValue placeholder={t('k_1c5c02cd20')} /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>{t('k_fc728855a6')}</SelectItem>
                  {categories.map(c => (
                    <SelectItem key={c.key} value={c.key}>{c.icon ? `${c.icon} ` : ''}{c.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">{t('k_358f2c0a47')}</p>
            </div>

            <div className="space-y-1.5">
              <Label>{t('k_81686ac9e4')}</Label>
              <Select value={form.kind} onValueChange={v => patch({ kind: v as DraftForm['kind'] })}>
                <SelectTrigger aria-label={t('k_ae09774fa9')}><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="markdown">{t('k_7d99f875bc')}</SelectItem>
                  <SelectItem value="html">{t('k_e8daa96df9')}</SelectItem>
                  <SelectItem value="link">{t('k_41e16d8b16')}</SelectItem>
                  <SelectItem value="file">{t('k_b36b591ff8')}</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">
                {t('k_2b2462de63')}
              </p>
            </div>

            <div className="space-y-1.5 sm:col-span-2">
              <Label>{t('k_351393143b')}</Label>
              <Select value={form.versioning} onValueChange={v => patch({ versioning: v as DraftForm['versioning'] })}>
                <SelectTrigger id="ce-versioning" aria-label={t('k_7e0c30c4fd')}><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(Object.keys(VERSIONING_LABELS) as ArticleVersioning[]).map(v => (
                    <SelectItem key={v} value={v}>{VERSIONING_LABELS[v]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">
                {t('k_020b743533', { p0: VERSIONING_HINTS[form.versioning] })}</p>
            </div>

            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="ce-summary">{t('k_46d4c1b4e4')}</Label>
              <Textarea id="ce-summary" rows={2} value={form.summary} onChange={e => patch({ summary: e.target.value })} placeholder={t('k_32321190a9')} />
            </div>

            {form.kind === 'link' && (
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="ce-link">{t('k_47c5285137')}</Label>
                  <Input id="ce-link" value={form.link_url} onChange={e => patch({ link_url: e.target.value })} placeholder="https://…" />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="ce-link-label">{t('k_ead8c81324')}</Label>
                  <Input id="ce-link-label" value={form.link_label} onChange={e => patch({ link_label: e.target.value })} placeholder={t('k_59fd26db56')} />
                </div>
              </div>
            )}

            {form.kind !== 'link' && (
              <div className="space-y-1.5">
                <Label htmlFor="ce-body">{form.kind === 'file' ? t('k_55ae896b4c')
                  : form.kind === 'html' ? t('k_dda10acf10') : t('k_4310f19b35')}</Label>
                <Textarea
                  id="ce-body"
                  rows={16}
                  value={form.body}
                  onChange={e => patch({ body: e.target.value })}
                  placeholder={form.kind === 'html'
                    ? t('k_3002ea78e6')
                    : t('k_9a2f22d39f')}
                  className="font-mono text-xs leading-6"
                />
                <p className="text-[11px] text-muted-foreground">
                  {form.kind === 'html'
                    // 渲染侧白名单与入库门禁同源（HtmlArticle.tsx / content_service.HTML_UNSAFE_RE）：
                    // 写进去就存不下，比上线后渲染缺块好得多
                    ? t('k_bc3df418ac')
                    : t('k_dc3eeeca84')}
                </p>
              </div>
            )}

            <div className="space-y-2 sm:col-span-2">
              {/* 进得来编辑器就说明有编辑权，附件区按可上传渲染；未落库时由面板自己说清
                  "为什么现在传不了"（不能藏掉按钮让人以为功能没做） */}
              <ArticleFiles
                slug={savedSlug}
                files={files}
                scope="articles"
                editable
                onRefresh={refreshFiles}
              />
            </div>

            <div className="space-y-2 sm:col-span-2">
              <Label>{t('k_97decbb7f1')}</Label>
              <div className="flex flex-wrap gap-1.5">
                {vocab.length === 0 && <p className="text-xs text-muted-foreground">{t('k_fea2444d2e')}</p>}
                {vocab.map(t => (
                  <button
                    key={t.tag}
                    type="button"
                    onClick={() => toggleTag(t.tag)}
                    aria-pressed={form.tags.includes(t.tag)}
                    className={`rounded-md border px-2 py-1 text-xs transition-colors
                      ${form.tags.includes(t.tag)
                        ? 'border-primary bg-primary/10 font-medium text-primary'
                        : 'border-border hover:bg-accent'}
                      ${!t.is_active ? 'line-through opacity-60' : ''}`}
                  >
                    {t.tag}
                    {!t.is_active && <span className="ml-1 text-[10px] text-muted-foreground">{tr('k_6c7dcbb73a')}</span>}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-2 sm:col-span-2">
              <div className="flex items-center justify-between">
                <Label>{t('k_b2fc15da9e')}</Label>
                <Button size="sm" variant="outline" onClick={() => patch({ refs: [...form.refs, { title: '', url: '' }] })}>
                  <Plus className="mr-1 h-3.5 w-3.5" />{t('k_8e61c42134')}
                </Button>
              </div>
              {form.refs.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  {t('k_eabaa04acc')}
                </p>
              )}
              <div className="space-y-2">
                {form.refs.map((r, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <Input
                      className="flex-1" value={r.title} placeholder={t('k_d561736bc5')}
                      onChange={e => patch({ refs: form.refs.map((x, j) => j === i ? { ...x, title: e.target.value } : x) })}
                    />
                    <Input
                      className="flex-[2]" value={r.url} placeholder={t('k_fe19889fe1')}
                      onChange={e => patch({ refs: form.refs.map((x, j) => j === i ? { ...x, url: e.target.value } : x) })}
                    />
                    <Button size="sm" variant="ghost" aria-label={t('k_8220fe8194', { p0: i + 1 })} className="text-destructive"
                      onClick={() => patch({ refs: form.refs.filter((_, j) => j !== i) })}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={form.pinned} onCheckedChange={v => patch({ pinned: v })} />{t('k_7bcf18641f')}
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={form.featured} onCheckedChange={v => patch({ featured: v })} />{t('k_7646dfe865')}
              </label>
              <div className="space-y-1.5">
                <Label htmlFor="ce-review">{t('k_f532aef260')}</Label>
                <Input id="ce-review" type="date" value={form.review_by} onChange={e => patch({ review_by: e.target.value })} />
              </div>
            </div>

            {editing && (
              <div className="border-t pt-3">
                <Button size="sm" variant="outline" onClick={async () => {
                  if (revisions) { setRevisions(null); return; }
                  try { setRevisions((await contentApi.revisions(slug!)).data.items); }
                  catch (err) { setError(contentError(err)); }
                }}>
                  <History className="mr-1.5 h-3.5 w-3.5" />{revisions ? t('k_ee28ab34b2') : t('k_4f70435274')}
                </Button>
                {revisions && (() => {
                  // 期次与改稿痕迹分区：混列会让人以为“存一次盘读者就能看一次”
                  const released = revisions.filter(r => r.is_released);
                  const drafts = revisions.filter(r => !r.is_released);
                  const row = (r: RevisionItem, releasedRow: boolean) => (
                    <li key={r.id} className="flex items-center gap-2">
                      <span className="w-32 shrink-0 text-muted-foreground">{r.created_at.slice(0, 16).replace('T', ' ')}</span>
                      <span className="flex-1 truncate">{r.change_note || t('k_69d2bebd58')}</span>
                      <span className="text-muted-foreground">{r.created_by}</span>
                      {releasedRow && (
                        <span className="rounded bg-sky-500/15 px-1.5 text-sky-600">
                          {t('k_ba93f835f8', { p0: r.release, p1: r.release_label ? `·${r.release_label}` : '' })}
                        </span>
                      )}
                      {r.is_live && <span className="rounded bg-emerald-500/15 px-1.5 text-emerald-600">{t('k_c3544ecb1d')}</span>}
                    </li>
                  );
                  return (
                    <div className="mt-2 space-y-2">
                      {!!released.length && (
                        <div>
                          <p className="text-[11px] font-medium text-muted-foreground">
                            {t('k_134e008e7b', { p0: released.length })}</p>
                          <ul className="mt-1 space-y-1 text-xs">{released.map(r => row(r, true))}</ul>
                        </div>
                      )}
                      {!!drafts.length && (
                        <div>
                          <p className="text-[11px] font-medium text-muted-foreground">{t('k_5983471911')}</p>
                          <ul className="mt-1 space-y-1 text-xs">{drafts.map(r => row(r, false))}</ul>
                        </div>
                      )}
                      <p className="text-[11px] text-muted-foreground">
                        {t('k_1d10cc5c7a', { p0: article?.versioning === 'latest_only'
                          && t('k_8c98e98261') })}
                      </p>
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
        </div>

        <DialogFooter className="gap-2 sm:space-x-0">
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>{t('k_4d0b4688c7')}</Button>
          <Button variant="outline" onClick={() => save(false)} disabled={saving || loading}>
            <Save className="mr-1.5 h-4 w-4" />{saving ? t('k_7cbf2d09a3') : t('k_4d7ea6dfa6')}
          </Button>
          <Button onClick={() => save(true)} disabled={saving || loading}>
            <Send className="mr-1.5 h-4 w-4" />{t('k_6fa72dae95')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
