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
        ? await contentApi.update(article.slug, buildPayload(submit ? '提交审核前的最后一次保存' : '编辑草稿'))
        : await contentApi.create(buildPayload('新建草稿'))
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
          ? ' 下一步：在下方附件区上传文件（附件形态没文件时不能提交审核）。'
          : '';
        setNotice((editing ? '草稿已保存。线上版本没有被改动。' : '草稿已创建。保存草稿不会影响门户。') + next);
      }
      onSaved(saved, submit ? '已提交审核' : '已保存草稿');
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
          <DialogTitle>{editing ? '编辑内容' : '新建内容'}</DialogTitle>
          <DialogDescription>
            {article?.is_live
              ? '这篇已在线上。此处的改动只生成新的草稿版本，需要重新审核并发布才会替换线上正文。'
              : '保存为草稿，不会出现在门户里；提交审核并通过后才能发布。'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <ErrorBanner message={error} onRetry={editing ? load : undefined} />
          <NoticeBanner message={notice} onDismiss={() => setNotice('')} />
          {!!hard.length && <GateList items={hard} kind="hard" />}
          {!!warns.length && <GateList items={warns} kind="warning" />}

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="ce-title">标题</Label>
              <Input id="ce-title" value={form.title} onChange={e => patch({ title: e.target.value })} placeholder="会同时作为门户卡片与详情页的标题" />
            </div>

            <div className="space-y-1.5">
              <Label>栏目</Label>
              <Select value={form.category} onValueChange={v => patch({ category: v })}>
                <SelectTrigger aria-label="选择栏目"><SelectValue placeholder="选择栏目" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>请选择…</SelectItem>
                  {categories.map(c => (
                    <SelectItem key={c.key} value={c.key}>{c.icon ? `${c.icon} ` : ''}{c.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">栏目清单来自「栏目与标签」页，这里不硬编码。</p>
            </div>

            <div className="space-y-1.5">
              <Label>内容形态</Label>
              <Select value={form.kind} onValueChange={v => patch({ kind: v as DraftForm['kind'] })}>
                <SelectTrigger aria-label="选择内容形态"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="markdown">站内正文</SelectItem>
                  <SelectItem value="html">HTML 版式（时间轴/卡片这类富排版）</SelectItem>
                  <SelectItem value="link">外链（原文在外部站点）</SelectItem>
                  <SelectItem value="file">附件（Word / PPT / Excel / PDF / HTML）</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">
                附件只给下载、不做站内预览。「提交到内容管理审核」入口在
                「工作空间 → 报告中心」的报告卡片上（复制正文成站内版式，不搬报表快照）。
              </p>
            </div>

            <div className="space-y-1.5 sm:col-span-2">
              <Label>版本策略（历史期次给不给读者看）</Label>
              <Select value={form.versioning} onValueChange={v => patch({ versioning: v as DraftForm['versioning'] })}>
                <SelectTrigger id="ce-versioning" aria-label="选择版本策略"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(Object.keys(VERSIONING_LABELS) as ArticleVersioning[]).map(v => (
                    <SelectItem key={v} value={v}>{VERSIONING_LABELS[v]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">
                {VERSIONING_HINTS[form.versioning]}。
                {/* 开关只收读者展示面：这句话必说，否则有人会以为勾上「只保留最新」就删了旧内容 */}
                选「只保留最新版本」不删任何数据：已发布期次、审核流水与「切换展示版本」都还在，
                改回「保留历史期次」即恢复可见。
              </p>
            </div>

            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="ce-summary">摘要</Label>
              <Textarea id="ce-summary" rows={2} value={form.summary} onChange={e => patch({ summary: e.target.value })} placeholder="一两句说清这篇讲什么，卡片上只显示这段" />
            </div>

            {form.kind === 'link' && (
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="ce-link">原文地址</Label>
                  <Input id="ce-link" value={form.link_url} onChange={e => patch({ link_url: e.target.value })} placeholder="https://…" />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="ce-link-label">按钮文案</Label>
                  <Input id="ce-link-label" value={form.link_label} onChange={e => patch({ link_label: e.target.value })} placeholder="如「前往厂商发布页」" />
                </div>
              </div>
            )}

            {form.kind !== 'link' && (
              <div className="space-y-1.5">
                <Label htmlFor="ce-body">{form.kind === 'file' ? '导读正文（选填，Markdown）'
                  : form.kind === 'html' ? '正文（HTML）' : '正文（Markdown）'}</Label>
                <Textarea
                  id="ce-body"
                  rows={16}
                  value={form.body}
                  onChange={e => patch({ body: e.target.value })}
                  placeholder={form.kind === 'html'
                    ? '<section style="display:grid;gap:12px">…</section>\n\n只保留排版标签与 style/class/href；颜色请用 hsl(var(--primary)) 这类主题变量，别写死色值。'
                    : '# 小标题\n\n正文支持 Markdown。数值请写清来源，不要留【待填】。'}
                  className="font-mono text-xs leading-6"
                />
                <p className="text-[11px] text-muted-foreground">
                  {form.kind === 'html'
                    // 渲染侧白名单与入库门禁同源（HtmlArticle.tsx / content_service.HTML_UNSAFE_RE）：
                    // 写进去就存不下，比上线后渲染缺块好得多
                    ? 'HTML 正文里的 script/iframe/style 块、事件属性（onclick 等）与 javascript: 链接会被提交门禁硬拦；版式请写在元素 style 属性里。'
                    : '带「【待填」的占位、数据源/主机/连接串等内部标识会被提交门禁硬阻断。'}
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
              <Label>标签（从受控词表选择，不支持自由输入）</Label>
              <div className="flex flex-wrap gap-1.5">
                {vocab.length === 0 && <p className="text-xs text-muted-foreground">词表还是空的，请先去「栏目与标签」建标签。</p>}
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
                    {!t.is_active && <span className="ml-1 text-[10px] text-muted-foreground">已停用</span>}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-2 sm:col-span-2">
              <div className="flex items-center justify-between">
                <Label>引用出处</Label>
                <Button size="sm" variant="outline" onClick={() => patch({ refs: [...form.refs, { title: '', url: '' }] })}>
                  <Plus className="mr-1 h-3.5 w-3.5" />加一条
                </Button>
              </div>
              {form.refs.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  正文引用外部文献/标准时必填。全部数字都是自己公司产出的可以留空（提交时会以软提醒形式让审核人确认）。
                </p>
              )}
              <div className="space-y-2">
                {form.refs.map((r, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <Input
                      className="flex-1" value={r.title} placeholder="文献/标准名称"
                      onChange={e => patch({ refs: form.refs.map((x, j) => j === i ? { ...x, title: e.target.value } : x) })}
                    />
                    <Input
                      className="flex-[2]" value={r.url} placeholder="https:// 开头的可核对链接"
                      onChange={e => patch({ refs: form.refs.map((x, j) => j === i ? { ...x, url: e.target.value } : x) })}
                    />
                    <Button size="sm" variant="ghost" aria-label={`删除第 ${i + 1} 条出处`} className="text-destructive"
                      onClick={() => patch({ refs: form.refs.filter((_, j) => j !== i) })}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={form.pinned} onCheckedChange={v => patch({ pinned: v })} />置顶
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={form.featured} onCheckedChange={v => patch({ featured: v })} />首页推荐
              </label>
              <div className="space-y-1.5">
                <Label htmlFor="ce-review">建议复核日</Label>
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
                  <History className="mr-1.5 h-3.5 w-3.5" />{revisions ? '收起版本清单' : '查看版本清单'}
                </Button>
                {revisions && (() => {
                  // 期次与改稿痕迹分区：混列会让人以为“存一次盘读者就能看一次”
                  const released = revisions.filter(r => r.is_released);
                  const drafts = revisions.filter(r => !r.is_released);
                  const row = (r: RevisionItem, releasedRow: boolean) => (
                    <li key={r.id} className="flex items-center gap-2">
                      <span className="w-32 shrink-0 text-muted-foreground">{r.created_at.slice(0, 16).replace('T', ' ')}</span>
                      <span className="flex-1 truncate">{r.change_note || '（无说明）'}</span>
                      <span className="text-muted-foreground">{r.created_by}</span>
                      {releasedRow && (
                        <span className="rounded bg-sky-500/15 px-1.5 text-sky-600">
                          第 {r.release} 期{r.release_label ? `·${r.release_label}` : ''}
                        </span>
                      )}
                      {r.is_live && <span className="rounded bg-emerald-500/15 px-1.5 text-emerald-600">线上</span>}
                    </li>
                  );
                  return (
                    <div className="mt-2 space-y-2">
                      {!!released.length && (
                        <div>
                          <p className="text-[11px] font-medium text-muted-foreground">
                            已发布期次（共 {released.length} 期）
                          </p>
                          <ul className="mt-1 space-y-1 text-xs">{released.map(r => row(r, true))}</ul>
                        </div>
                      )}
                      {!!drafts.length && (
                        <div>
                          <p className="text-[11px] font-medium text-muted-foreground">编辑历史（未发布的改稿痕迹）</p>
                          <ul className="mt-1 space-y-1 text-xs">{drafts.map(r => row(r, false))}</ul>
                        </div>
                      )}
                      <p className="text-[11px] text-muted-foreground">
                        要把门户展示的那一期切回已发布过的某一期，用列表行上的「切换展示版本」
                        （需发布权限，并逐条填写原因）；未发布过的版本切不了。
                        {article?.versioning === 'latest_only'
                          && ' 当前策略为「只保留最新版本」：下面这些期次在编排面能看到，读者只能看到线上那一期。'}
                      </p>
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
        </div>

        <DialogFooter className="gap-2 sm:space-x-0">
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>取消</Button>
          <Button variant="outline" onClick={() => save(false)} disabled={saving || loading}>
            <Save className="mr-1.5 h-4 w-4" />{saving ? '保存中' : '保存草稿'}
          </Button>
          <Button onClick={() => save(true)} disabled={saving || loading}>
            <Send className="mr-1.5 h-4 w-4" />保存并提交审核
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
