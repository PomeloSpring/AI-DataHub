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
import { t as tr } from '@/i18n/runtime';
import { useTranslation } from 'react-i18next';

/** 新条目排到清单末尾（不用 Array#at，工程 lib 目标低于 es2022） */
const nextSort = (sorts: number[]): number => (sorts.length ? Math.max(...sorts) : 0) + 10;

/**
 * 栏目与标签（受控词表）。
 *
 * 标签**不是自由输入**：词表是唯一真值源，服务端会拒掉词表外的标签（否则历史条目里
 * 会积累出一堆点不到的标签，侧栏标签云逐渐失效）。因此改名走词表级联，不逐条改条目。
 */
export default function ContentTaxonomy() {
  const { t } = useTranslation('ui');
  const { has } = useContentPerms();
  // 词表管理走专属码 taxonomy:manage（迁移已把菜单显隐与 categories/tags 读写面拆出）：
  // article:manage 仍覆盖内容创作中的选择/打标签，同域有意重叠，不再做本页的编辑显隐。
  const canEdit = has('taxonomy:manage');
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

  if (loading) return <LoadingRow text={t('k_a2b57aa0fc')} />;

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">{t('k_939cc75db8')}</h1>
        <p className="text-sm text-muted-foreground">
          {t('k_10c29648f5')}
        </p>
      </header>

      <ErrorBanner message={error || localError} onRetry={reload} />
      <NoticeBanner message={notice} onDismiss={() => setNotice('')} />

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium">{t('k_57b4e514da', { p0: categories.length })}</h2>
          {canEdit && (
            <Button size="sm" variant="outline" onClick={() => setCatDraft({ key: '', label: '', description: '', icon: '', color: '#6366f1', sort: nextSort(categories.map(c => c.sort)), is_active: 1 })}>
              <Plus className="mr-1 h-3.5 w-3.5" />{t('k_8dd2bf88bc')}
            </Button>
          )}
        </div>
        {categories.length === 0 ? (
          <EmptyState title={t('k_8bb6aade8c')} hint={t('k_d93cc82e40')} />
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
                      {!c.is_active && <span className="ml-1.5 rounded bg-destructive/10 px-1.5 text-[10px] text-destructive">{t('k_6c7dcbb73a')}</span>}
                    </p>
                    <p className="mt-0.5 line-clamp-2 text-[11px] text-muted-foreground">{c.description || t('k_69d2bebd58')}</p>
                    <p className="mt-1 text-[11px] text-muted-foreground">{t('k_04b1e05dd2', { p0: c.key, p1: c.count ?? 0, p2: c.sort })}</p>
                  </div>
                </div>
                {canEdit && (
                  <div className="mt-2 flex items-center gap-1 border-t pt-2">
                    <Button size="sm" variant="ghost" className="h-6 px-2 text-xs" onClick={() => setCatDraft(c)}>{t('k_a7f814c0a4')}</Button>
                    <Button size="sm" variant="ghost" className="h-6 px-2 text-xs"
                      onClick={() => run(() => contentApi.updateCategory(c.id, { is_active: c.is_active ? 0 : 1 }),
                        c.is_active ? t('k_77d9a9258e') : t('k_6da7796bb2'))}>
                      {c.is_active ? t('k_5c56a88945') : t('k_d4e9ca3dd4')}
                    </Button>
                    <Button size="sm" variant="ghost" className="ml-auto h-6 px-2 text-xs text-destructive" onClick={() => setCatDelete(c)}>{t('k_3755f56f2f')}</Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
        <p className="text-[11px] text-muted-foreground">
          {t('k_f924c6ed70')}
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-medium">{t('k_5e84ee0ba9', { p0: tags.length })}</h2>
        {canEdit && (
          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label htmlFor="tag-new">{t('k_4a9223d889')}</Label>
              <Input id="tag-new" className="w-48" value={newTag} onChange={e => setNewTag(e.target.value)}
                placeholder={t('k_5cbd88102f')} maxLength={32} />
            </div>
            <Button size="sm" disabled={!newTag.trim()} onClick={() => {
              run(() => contentApi.createTag({ tag: newTag.trim(), sort: nextSort(tags.map(t => t.sort)) }), t('k_3ea58d9992'));
              setNewTag('');
            }}>{t('k_c7c5817be0')}</Button>
          </div>
        )}
        {tags.length === 0 ? (
          <EmptyState title={t('k_221d3d5012')} hint={t('k_da0927eef1')} />
        ) : (
          <div className="flex flex-wrap gap-2">
            {tags.map(t => (
              <div key={t.id} className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs ${t.is_active ? '' : 'opacity-60'}`}>
                <button type="button" className="font-medium hover:text-primary" onClick={() => canEdit && setTagEdit(t)}>
                  {t.tag}
                </button>
                <span className="text-muted-foreground">{tr('k_5ede5ddd6b', { p0: t.count })}</span>
                {t.count === 0 && <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">{tr('k_e010d4f979')}</span>}
                {!t.is_active && <span className="rounded bg-destructive/10 px-1 text-[10px] text-destructive">{tr('k_6c7dcbb73a')}</span>}
                {canEdit && (
                  <button
                    type="button"
                    className="text-muted-foreground hover:text-foreground"
                    aria-label={tr('k_b99a3fc8db', { p0: t.is_active ? tr('k_5c56a88945') : tr('k_d4e9ca3dd4'), p1: t.tag })}
                    onClick={() => run(() => contentApi.updateTag(t.id, { is_active: t.is_active ? 0 : 1 }), tr('k_1b92092d63'))}
                  >
                    {t.is_active ? tr('k_5c56a88945') : tr('k_d4e9ca3dd4')}
                  </button>
                )}
                {canEdit && (
                  <button type="button" className="text-destructive" aria-label={tr('k_8b7d38a9b8', { p0: t.tag })} onClick={() => setTagDelete(t)}>
                    ×
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
        <p className="text-[11px] text-muted-foreground">
          {t('k_b73f2b3d38')}
        </p>
      </section>

      <CategoryDialog draft={catDraft} onClose={() => setCatDraft(null)} onSaved={msg => { setNotice(msg); reload(); }} onError={setLocalError} />

      <TagRenameDialog tag={tagEdit} onClose={() => setTagEdit(null)} onSaved={msg => { setNotice(msg); reload(); }} onError={setLocalError} />

      <ConfirmDialog
        open={!!catDelete}
        title={t('k_70c7cd7164')}
        description={catDelete
          ? t('k_6ddb6af4a4', { p0: catDelete.label })
          : ''}
        confirmLabel={t('k_3755f56f2f')}
        destructive
        onClose={() => setCatDelete(null)}
        onConfirm={() => { const c = catDelete!; setCatDelete(null); run(() => contentApi.removeCategory(c.id), t('k_05f8c70333')); }}
      />

      <ConfirmDialog
        open={!!tagDelete}
        title={t('k_ff6fe44cc4')}
        description={tagDelete
          ? t('k_adca9effa2', { p0: tagDelete.tag })
          : ''}
        confirmLabel={t('k_3755f56f2f')}
        destructive
        onClose={() => setTagDelete(null)}
        onConfirm={() => { const t = tagDelete!; setTagDelete(null); run(() => contentApi.removeTag(t.id), tr('k_722459eec7')); }}
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
  const { t } = useTranslation('ui');
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
    req.then(() => { onSaved(isNew ? t('k_d55122b7e5') : t('k_dc513bba3c')); onClose(); })
      .catch(err => onError(contentError(err)));
  };

  return (
    <Dialog open onOpenChange={v => { if (!v) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{isNew ? t('k_8dd2bf88bc') : t('k_42ae659a7a')}</DialogTitle>
          <DialogDescription>
            {t('k_eaa6b03b97')}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="ct-key">{t('k_511660472f')}</Label>
            <Input id="ct-key" value={form.key ?? ''} disabled={!isNew} onChange={e => patch({ key: e.target.value })} placeholder="whitepaper" />
            {!isNew && <p className="text-[11px] text-muted-foreground">{t('k_72836b1022')}</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ct-label">{t('k_c10bbf5ddd')}</Label>
            <Input id="ct-label" value={form.label ?? ''} onChange={e => patch({ label: e.target.value })} placeholder={t('k_3d24bdb08a')} />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="ct-desc">{t('k_26670dda42')}</Label>
            <Input id="ct-desc" value={form.description ?? ''} onChange={e => patch({ description: e.target.value })} placeholder={t('k_302ce2198d')} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ct-icon">{t('k_73ffd56bf3')}</Label>
            <Input id="ct-icon" value={form.icon ?? ''} onChange={e => patch({ icon: e.target.value })} placeholder="📄" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ct-color">{t('k_a624ea8033')}</Label>
            <Input id="ct-color" type="color" className="h-9 p-1" value={form.color ?? '#6366f1'} onChange={e => patch({ color: e.target.value })} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ct-sort">{t('k_dc35af8d69')}</Label>
            <Input id="ct-sort" type="number" value={form.sort ?? 0} onChange={e => patch({ sort: Number(e.target.value) })} />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={!!form.is_active} onCheckedChange={v => patch({ is_active: v ? 1 : 0 })} />{t('k_60136ec935')}
          </label>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>{t('k_4d0b4688c7')}</Button>
          <Button onClick={submit} disabled={!(form.label ?? '').trim() || !(form.key ?? '').trim()}>{t('k_fadf24dbc5')}</Button>
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
  const { t } = useTranslation('ui');
  const [value, setValue] = useState('');
  useEffect(() => { setValue(tag?.tag ?? ''); }, [tag]);
  if (!tag) return null;

  return (
    <Dialog open onOpenChange={v => { if (!v) onClose(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t('k_190d93375e')}</DialogTitle>
          <DialogDescription>
            {t('k_db16434d81', { p0: tag.tag })}</DialogDescription>
        </DialogHeader>
        <Input value={value} maxLength={32} onChange={e => setValue(e.target.value)} aria-label={t('k_359d629430')} />
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>{t('k_4d0b4688c7')}</Button>
          <Button
            disabled={!value.trim() || value.trim() === tag.tag}
            onClick={() => {
              contentApi.updateTag(tag.id, { tag: value.trim() })
                .then(() => { onSaved(t('k_75cb5b12b8')); onClose(); })
                .catch(err => onError(contentError(err)));
            }}
          >
            {t('k_fadf24dbc5')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
