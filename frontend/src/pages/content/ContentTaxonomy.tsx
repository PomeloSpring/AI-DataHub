import { useCallback, useEffect, useState } from 'react';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { contentApi, contentError, type ContentCategory, type ContentTag } from '@/api/content';
import { ConfirmDialog } from './ArticleTable';
import { EmptyState, ErrorBanner, LoadingRow, NoticeBanner, useContentPerms, useTaxonomy } from './shared';

/** 新条目排到清单末尾（不用 Array#at，工程 lib 目标低于 es2022） */
const nextSort = (sorts: number[]): number => (sorts.length ? Math.max(...sorts) : 0) + 10;

/**
 * 栏目与标签（受控词表）。
 *
 * 标签**不是自由输入**：词表是唯一真值源，服务端会拒掉词表外的标签（否则历史条目里
 * 会积累出一堆点不到的标签，侧栏标签云逐渐失效）。因此改名走词表级联，不逐条改条目。
 */
export default function ContentTaxonomy() {
  const { has } = useContentPerms();
  const canEdit = has('article:manage');
  const { categories, tags, loading, error, reload } = useTaxonomy(true);

  const [notice, setNotice] = useState('');
  const [localError, setLocalError] = useState('');
  const [catDraft, setCatDraft] = useState<Partial<ContentCategory> | null>(null);
  const [newTag, setNewTag] = useState('');
  const [tagEdit, setTagEdit] = useState<ContentTag | null>(null);
  const [catDelete, setCatDelete] = useState<ContentCategory | null>(null);
  const [tagDelete, setTagDelete] = useState<ContentTag | null>(null);

  const run = useCallback((fn: () => Promise<unknown>, ok: string) => {
    setLocalError(''); setNotice('');
    fn()
      .then(() => { setNotice(ok); reload(); })
      // 悬空保护的服务端原因（"该栏目下还有 N 条内容"）必须原样给管理员看到
      .catch(err => setLocalError(contentError(err)));
  }, [reload]);

  if (loading) return <LoadingRow text="正在加载栏目与标签" />;

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">栏目与标签</h1>
        <p className="text-sm text-muted-foreground">
          栏目决定门户的分区导航；标签是受控词表，编辑器里只能从这份清单里选。
        </p>
      </header>

      <ErrorBanner message={error || localError} onRetry={reload} />
      <NoticeBanner message={notice} onDismiss={() => setNotice('')} />

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium">栏目（{categories.length}）</h2>
          {canEdit && (
            <Button size="sm" variant="outline" onClick={() => setCatDraft({ key: '', label: '', description: '', icon: '', color: '#6366f1', sort: nextSort(categories.map(c => c.sort)), is_active: 1 })}>
              <Plus className="mr-1 h-3.5 w-3.5" />新建栏目
            </Button>
          )}
        </div>
        {categories.length === 0 ? (
          <EmptyState title="还没有栏目" hint="栏目是内容的必填归属，至少建一个才能开始编写。" />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {categories.map(c => (
              <div key={c.id} className={`rounded-xl border p-3 ${c.is_active ? '' : 'opacity-60'}`}>
                <div className="flex items-start gap-2">
                  <span
                    className="flex size-8 shrink-0 items-center justify-center rounded-lg text-base"
                    style={{ backgroundColor: `${c.color}1a`, color: c.color }}
                  >
                    {c.icon || '📄'}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {c.label}
                      {!c.is_active && <span className="ml-1.5 rounded bg-destructive/10 px-1.5 text-[10px] text-destructive">已停用</span>}
                    </p>
                    <p className="mt-0.5 line-clamp-2 text-[11px] text-muted-foreground">{c.description || '（无说明）'}</p>
                    <p className="mt-1 text-[11px] text-muted-foreground">{c.key} · 在线 {c.count ?? 0} 篇 · 排序 {c.sort}</p>
                  </div>
                </div>
                {canEdit && (
                  <div className="mt-2 flex items-center gap-1 border-t pt-2">
                    <Button size="sm" variant="ghost" className="h-6 px-2 text-xs" onClick={() => setCatDraft(c)}>编辑</Button>
                    <Button size="sm" variant="ghost" className="h-6 px-2 text-xs"
                      onClick={() => run(() => contentApi.updateCategory(c.id, { is_active: c.is_active ? 0 : 1 }),
                        c.is_active ? '栏目已停用' : '栏目已启用')}>
                      {c.is_active ? '停用' : '启用'}
                    </Button>
                    <Button size="sm" variant="ghost" className="ml-auto h-6 px-2 text-xs text-destructive" onClick={() => setCatDelete(c)}>删除</Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
        <p className="text-[11px] text-muted-foreground">
          停用的栏目不能再被选去写新内容，已挂在它下面的内容会被提交门禁硬阻断（提示"所选栏目已停用，请改选在用栏目"）—— 这是为了不出现"栏目消失了但内容还挂着"的悬空。
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-medium">标签词表（{tags.length}）</h2>
        {canEdit && (
          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label htmlFor="tag-new">新标签</Label>
              <Input id="tag-new" className="w-48" value={newTag} onChange={e => setNewTag(e.target.value)}
                placeholder="如「修复体材料」" maxLength={32} />
            </div>
            <Button size="sm" disabled={!newTag.trim()} onClick={() => {
              run(() => contentApi.createTag({ tag: newTag.trim(), sort: nextSort(tags.map(t => t.sort)) }), '标签已添加');
              setNewTag('');
            }}>加入词表</Button>
          </div>
        )}
        {tags.length === 0 ? (
          <EmptyState title="标签词表是空的" hint="编辑器里的标签选择框会没有选项，请先加入词表。" />
        ) : (
          <div className="flex flex-wrap gap-2">
            {tags.map(t => (
              <div key={t.id} className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs ${t.is_active ? '' : 'opacity-60'}`}>
                <button type="button" className="font-medium hover:text-primary" onClick={() => canEdit && setTagEdit(t)}>
                  {t.tag}
                </button>
                <span className="text-muted-foreground">{t.count} 篇</span>
                {t.count === 0 && <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">未被使用</span>}
                {!t.is_active && <span className="rounded bg-destructive/10 px-1 text-[10px] text-destructive">已停用</span>}
                {canEdit && (
                  <button
                    type="button"
                    className="text-muted-foreground hover:text-foreground"
                    aria-label={`${t.is_active ? '停用' : '启用'}标签 ${t.tag}`}
                    onClick={() => run(() => contentApi.updateTag(t.id, { is_active: t.is_active ? 0 : 1 }), '已更新')}
                  >
                    {t.is_active ? '停用' : '启用'}
                  </button>
                )}
                {canEdit && (
                  <button type="button" className="text-destructive" aria-label={`删除标签 ${t.tag}`} onClick={() => setTagDelete(t)}>
                    ×
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
        <p className="text-[11px] text-muted-foreground">
          命中 0 篇的标签不会出现在门户侧栏（侧栏只列命中 ≥2 篇的标签），但保留在词表里供新稿使用。改名会同步替换已在用的条目。
        </p>
      </section>

      <CategoryDialog draft={catDraft} onClose={() => setCatDraft(null)} onSaved={msg => { setNotice(msg); reload(); }} onError={setLocalError} />

      <TagRenameDialog tag={tagEdit} onClose={() => setTagEdit(null)} onSaved={msg => { setNotice(msg); reload(); }} onError={setLocalError} />

      <ConfirmDialog
        open={!!catDelete}
        title="删除栏目"
        description={catDelete
          ? `确定删除栏目「${catDelete.label}」？该栏目下若还有内容（含草稿），服务端会拒绝删除并要求先移走内容 —— 这是为了不留下没有栏目的条目。`
          : ''}
        confirmLabel="删除"
        destructive
        onClose={() => setCatDelete(null)}
        onConfirm={() => { const c = catDelete!; setCatDelete(null); run(() => contentApi.removeCategory(c.id), '栏目已删除'); }}
      />

      <ConfirmDialog
        open={!!tagDelete}
        title="删除标签"
        description={tagDelete
          ? `确定从词表删除标签「${tagDelete.tag}」？若仍有条目在使用它，服务端会拒绝。只想暂时不再使用请选「停用」—— 停用会保留历史条目的标签归属。`
          : ''}
        confirmLabel="删除"
        destructive
        onClose={() => setTagDelete(null)}
        onConfirm={() => { const t = tagDelete!; setTagDelete(null); run(() => contentApi.removeTag(t.id), '标签已删除'); }}
      />
    </div>
  );
}

function CategoryDialog({ draft, onClose, onSaved, onError }: {
  draft: Partial<ContentCategory> | null;
  onClose: () => void;
  onSaved: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  const [form, setForm] = useState<Partial<ContentCategory>>(draft ?? {});
  useEffect(() => { setForm(draft ?? {}); }, [draft]);
  if (!draft) return null;
  const isNew = !draft.id;
  const patch = (p: Partial<ContentCategory>) => setForm(f => ({ ...f, ...p }));

  const submit = () => {
    onError('');
    const body = {
      key: (form.key ?? '').trim(), label: (form.label ?? '').trim(),
      description: (form.description ?? '').trim(), icon: (form.icon ?? '').trim(),
      color: (form.color ?? '#6366f1').trim(), sort: Number(form.sort ?? 0),
      is_active: form.is_active ? 1 : 0,
    };
    const req = isNew ? contentApi.createCategory(body) : contentApi.updateCategory(draft.id!, body);
    req.then(() => { onSaved(isNew ? '栏目已创建' : '栏目已更新'); onClose(); })
      .catch(err => onError(contentError(err)));
  };

  return (
    <Dialog open onOpenChange={v => { if (!v) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{isNew ? '新建栏目' : '编辑栏目'}</DialogTitle>
          <DialogDescription>
            栏目 key 是内容引用它的业务标识（编辑器与接口都按 key 传，不传内部 id），创建后不建议再改。
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="ct-key">栏目 key</Label>
            <Input id="ct-key" value={form.key ?? ''} disabled={!isNew} onChange={e => patch({ key: e.target.value })} placeholder="whitepaper" />
            {!isNew && <p className="text-[11px] text-muted-foreground">key 已锁定，改 key 会让存量引用悬空。</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ct-label">显示名</Label>
            <Input id="ct-label" value={form.label ?? ''} onChange={e => patch({ label: e.target.value })} placeholder="技术白皮书" />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="ct-desc">说明</Label>
            <Input id="ct-desc" value={form.description ?? ''} onChange={e => patch({ description: e.target.value })} placeholder="这个栏目收什么内容，写清边界" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ct-icon">图标（一个 emoji）</Label>
            <Input id="ct-icon" value={form.icon ?? ''} onChange={e => patch({ icon: e.target.value })} placeholder="📄" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ct-color">标识色</Label>
            <Input id="ct-color" type="color" className="h-9 p-1" value={form.color ?? '#6366f1'} onChange={e => patch({ color: e.target.value })} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ct-sort">排序</Label>
            <Input id="ct-sort" type="number" value={form.sort ?? 0} onChange={e => patch({ sort: Number(e.target.value) })} />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={!!form.is_active} onCheckedChange={v => patch({ is_active: v ? 1 : 0 })} />在用
          </label>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button onClick={submit} disabled={!(form.label ?? '').trim() || !(form.key ?? '').trim()}>保存</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function TagRenameDialog({ tag, onClose, onSaved, onError }: {
  tag: ContentTag | null;
  onClose: () => void;
  onSaved: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  const [value, setValue] = useState('');
  useEffect(() => { setValue(tag?.tag ?? ''); }, [tag]);
  if (!tag) return null;

  return (
    <Dialog open onOpenChange={v => { if (!v) onClose(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>重命名标签</DialogTitle>
          <DialogDescription>
            改名会把当前挂着「{tag.tag}」的条目一起换成新名（词表是唯一真值源，不允许历史条目留旧名）。
          </DialogDescription>
        </DialogHeader>
        <Input value={value} maxLength={32} onChange={e => setValue(e.target.value)} aria-label="新标签名" />
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button
            disabled={!value.trim() || value.trim() === tag.tag}
            onClick={() => {
              contentApi.updateTag(tag.id, { tag: value.trim() })
                .then(() => { onSaved('标签已重命名，在用条目已同步'); onClose(); })
                .catch(err => onError(contentError(err)));
            }}
          >
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
