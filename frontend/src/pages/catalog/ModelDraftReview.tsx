/**
 * ModelDraftReview — 本体草案的可视化审阅面板（「变更对比」页签）。
 *
 * 为什么存在：两段式生效（草案 → 发布）里，用户要判断的是"这份草案动了什么、能不能发"。
 * 此前唯一入口是把草案正文当 JSON 摊出来让人自己比，等于把 diff 计算甩给用户；
 * 而 AS-BOT 写草案时人根本看不到改了什么，才会出现"说加了对象、页面上找不到"的困惑。
 *
 * 设计口径（对齐 Foundry proposal / PR review 惯例）：
 *  - 状态优先分组：新增 → 修改 → 删除 → 未变，未改动默认折叠（不占注意力）；
 *  - 每条变更用人话写清"加了哪个属性、动了哪条关系"，明细由服务端 draft_diff 算好；
 *  - 发布/丢弃/去编辑三个动作在同一屏闭环，不必回顶栏找按钮；
 *  - 原始 JSON 只作可折叠附录（排查用），不是主视图。
 */
import { useState, useCallback, useMemo } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { t } from '@/i18n/runtime';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Code2, Loader2,
  Pencil, Search, Trash2, Undo2,
} from 'lucide-react';
import { ontologyApi, type DraftObjectDiff, type DraftStatus } from '@/api/ontology';
import { useTranslation } from 'react-i18next';

const STATE_META: Record<string, { label: string; cls: string; dot: string }> = {
  added: { label: t('k_2cd9e6ce81'), cls: 'border-green-500/40 text-green-600 bg-green-500/5', dot: 'bg-green-500' },
  changed: { label: t('k_c9c77517fe'), cls: 'border-blue-500/40 text-blue-600 bg-blue-500/5', dot: 'bg-blue-500' },
  removed: { label: t('k_3755f56f2f'), cls: 'border-red-500/40 text-red-600 bg-red-500/5', dot: 'bg-red-500' },
  unchanged: { label: t('k_bb48ba5958'), cls: 'border-border text-muted-foreground', dot: 'bg-muted-foreground/40' },
};

type StateFilter = 'all' | 'added' | 'changed' | 'removed' | 'unchanged';

/** 变更计数摘要（列表行上的一行话）：让"改了多少"不展开也能判断量级 */
function deltaSummary(o: DraftObjectDiff): string {
  const d = o.delta;
  if (!d) {
    if (o.state === 'added') {
      return t('k_107ebf8737', { p0: o.property_count ?? 0, p1: o.link_count ?? 0 });
    }
    if (o.state === 'removed') return t('k_7f1c2a5b14');
    return t('k_107ebf8737', { p0: o.property_count ?? 0, p1: o.link_count ?? 0 });
  }
  const parts: string[] = [];
  const pa = d.properties_added?.length || 0;
  const pr = d.properties_removed?.length || 0;
  const pm = d.properties_modified?.length || 0;
  if (pa || pr || pm) {
    parts.push(t('k_74704fd62d', { p0: [pa ? `+${pa}` : '', pr ? `-${pr}` : '', pm ? `~${pm}` : ''].filter(Boolean).join(' ') }));
  }
  const la = d.links_added?.length || 0;
  const lr = d.links_removed?.length || 0;
  if (la || lr) parts.push(t('k_a3790f4089', { p0: [la ? `+${la}` : '', lr ? `-${lr}` : ''].filter(Boolean).join(' ') }));
  const aa = d.aliases_added?.length || 0;
  const ar = d.aliases_removed?.length || 0;
  if (aa || ar) parts.push(t('k_19ed0af3ab', { p0: [aa ? `+${aa}` : '', ar ? `-${ar}` : ''].filter(Boolean).join(' ') }));
  if (d.renamed_from) parts.push(t('k_83a8fa64e6', { p0: d.renamed_from, p1: o.display_name }));
  if (d.table_changed) parts.push(t('k_d8dd7beea5'));
  if (d.description_changed) parts.push(t('k_728c30fe8f'));
  return parts.join(' · ') || t('k_eaba010237');
}

function DeltaDetail({ o }: { o: DraftObjectDiff }) {
  const { t } = useTranslation('ui');
  const d = o.delta;
  if (!d) return null;
  const rows: { kind: 'add' | 'remove' | 'mod'; text: string; sub?: string }[] = [];
  (d.properties_added || []).forEach((p) => rows.push({ kind: 'add', text: p.name || p.key, sub: t('k_24d67862f8') }));
  (d.properties_removed || []).forEach((p) => rows.push({ kind: 'remove', text: p.name || p.key, sub: t('k_24d67862f8') }));
  (d.properties_modified || []).forEach((p) => rows.push({ kind: 'mod', text: p.name || p.key, sub: `${p.from || '—'} → ${p.to || '—'}` }));
  (d.links_added || []).forEach((l) => rows.push({ kind: 'add', text: l.label, sub: t('k_eefd831645') }));
  (d.links_removed || []).forEach((l) => rows.push({ kind: 'remove', text: l.label, sub: t('k_eefd831645') }));
  (d.aliases_added || []).forEach((a) => rows.push({ kind: 'add', text: a, sub: t('k_ec537c546d') }));
  (d.aliases_removed || []).forEach((a) => rows.push({ kind: 'remove', text: a, sub: t('k_ec537c546d') }));
  if (d.table_changed) {
    rows.push({ kind: 'mod', text: t('k_17dbbad889'), sub: `${d.primary_table_from || '—'} → ${d.primary_table_to || '—'}` });
  }
  if (!rows.length) return null;
  return (
    <div className="mt-2 rounded-md border border-border/60 bg-muted/20 px-2.5 py-1.5">
      {rows.map((r, i) => (
        <div key={i} className="flex items-center gap-2 py-0.5 text-xs">
          <span className={`w-3 shrink-0 font-mono ${r.kind === 'add' ? 'text-green-600' : r.kind === 'remove' ? 'text-red-600' : 'text-blue-600'}`}>
            {r.kind === 'add' ? '+' : r.kind === 'remove' ? '−' : '~'}
          </span>
          <span className="truncate">{r.text}</span>
          <Badge variant="outline" className="text-[10px] px-1 h-4 shrink-0 text-muted-foreground">{r.sub}</Badge>
          {r.kind === 'mod' && r.sub && (
            <span className="ml-auto truncate text-[11px] text-muted-foreground font-mono">{r.sub}</span>
          )}
        </div>
      ))}
    </div>
  );
}

export default function ModelDraftReview({
  modelId, draft, canPublish, canEdit, publishing, onPublish, onDiscard, onEditInDraft,
}: {
  modelId: number;
  draft: DraftStatus | null;
  canPublish: boolean;
  canEdit: boolean;
  publishing: boolean;
  onPublish: () => void;
  onDiscard: () => void;
  onEditInDraft: () => void;
}) {
  const { t } = useTranslation('ui');
  const [filter, setFilter] = useState<StateFilter>('all');
  const [kw, setKw] = useState('');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [discarding, setDiscarding] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [jsonOpen, setJsonOpen] = useState(false);
  const [jsonLoading, setJsonLoading] = useState(false);
  const [jsonText, setJsonText] = useState('');
  const [jsonError, setJsonError] = useState('');

  const objects = draft?.objects || [];
  const counts = useMemo(() => ({
    added: objects.filter((o) => o.state === 'added').length,
    changed: objects.filter((o) => o.state === 'changed').length,
    removed: objects.filter((o) => o.state === 'removed').length,
    unchanged: objects.filter((o) => o.state === 'unchanged').length,
  }), [objects]);

  const visible = useMemo(() => {
    const k = kw.trim().toLowerCase();
    return objects.filter((o) => (filter === 'all' || o.state === filter)
      && (!k || o.display_name.toLowerCase().includes(k) || o.key.toLowerCase().includes(k)));
  }, [objects, filter, kw]);

  const openRaw = useCallback(async () => {
    setJsonOpen(true);
    setJsonLoading(true);
    setJsonError('');
    try {
      const { data } = await ontologyApi.getDraftContent(modelId);
      if (!data.has_draft) {
        setJsonError(t('k_fff858e418'));
        return;
      }
      try {
        setJsonText(JSON.stringify(JSON.parse(data.draft_json_content), null, 2));
      } catch {
        setJsonText(data.draft_json_content || '');
      }
    } catch (e: any) {
      setJsonError(e?.response?.data?.detail || t('k_4104f51904'));
    } finally {
      setJsonLoading(false);
    }
  }, [modelId]);

  const confirmDiscard = useCallback(async () => {
    setDiscarding(true);
    try {
      await ontologyApi.discardDraft(modelId);
      setDiscardOpen(false);
      onDiscard();
    } catch (e: any) {
      setDiscarding(false);
      setJsonError(e?.response?.data?.detail || t('k_d66df9d32c'));
    } finally {
      setDiscarding(false);
    }
  }, [modelId, onDiscard]);

  if (!draft?.has_draft) {
    return (
      <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
        <CheckCircle2 className="h-6 w-6 mx-auto mb-2 opacity-40" />
        {t('k_d3f26c60af')}
        {canEdit && (
          <div className="mt-2">
            <Button size="sm" variant="outline" onClick={onEditInDraft}>
              <Pencil className="h-3.5 w-3.5 mr-1" />{t('k_9ffcb205b4')}
            </Button>
          </div>
        )}
      </div>
    );
  }

  const FilterChip = ({ value, label, n, cls }: { value: StateFilter; label: string; n: number; cls: string }) => (
    <button
      onClick={() => setFilter(value)}
      className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
        filter === value ? cls + ' font-medium' : 'border-border text-muted-foreground hover:bg-muted/50'}`}
    >
      {label} {n}
    </button>
  );

  return (
    <div className="space-y-3">
      {/* 摘要 + 动作条 */}
      <div className="flex items-start gap-3 flex-wrap rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">
            {t('k_4f2e1a4806', { p0: draft.effective_object_count, p1: draft.draft_object_count })}
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              {t('k_08445af37d', { p0: draft.draft_updated_by || t('k_1622dc9b6b'), p1: draft.draft_updated_at?.slice(0, 16).replace('T', ' ') || '—' })}</span>
          </div>
          <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
            <FilterChip value="all" label={t("k_778fc8f994")} n={objects.length} cls={STATE_META.added.cls} />
            <FilterChip value="added" label={t("k_2cd9e6ce81")} n={counts.added} cls={STATE_META.added.cls} />
            <FilterChip value="changed" label={t("k_c9c77517fe")} n={counts.changed} cls={STATE_META.changed.cls} />
            <FilterChip value="removed" label={t("k_3755f56f2f")} n={counts.removed} cls={STATE_META.removed.cls} />
            <FilterChip value="unchanged" label={t("k_bb48ba5958")} n={counts.unchanged} cls={STATE_META.unchanged.cls} />
          </div>
          {draft.base_stale && (
            <div className="mt-2 text-xs text-red-600">
              <AlertTriangle className="h-3.5 w-3.5 mr-1 inline" />
              {t('k_300ba9e297')}
            </div>
          )}
          {draft.draft_parse_error && (
            <div className="mt-2 text-xs text-red-600">
              <AlertTriangle className="h-3.5 w-3.5 mr-1 inline" />
              {t('k_cdf29c4450')}
            </div>
          )}
          {jsonError && <div className="mt-2 text-xs text-red-600">{jsonError}</div>}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Button size="sm" variant="outline" onClick={openRaw}>
            <Code2 className="h-3.5 w-3.5 mr-1" />{t('k_ea1c18aebf')}
          </Button>
          {canEdit && (
            <Button size="sm" variant="outline" onClick={onEditInDraft}>
              <Pencil className="h-3.5 w-3.5 mr-1" />{t('k_9ffcb205b4')}
            </Button>
          )}
          {canEdit && (
            <Button size="sm" variant="ghost" className="text-red-500 hover:text-red-600"
              onClick={() => setDiscardOpen(true)} disabled={publishing}>
              <Trash2 className="h-3.5 w-3.5 mr-1" />{t('k_ec179f83bd')}
            </Button>
          )}
          {canPublish && (
            <Button size="sm" onClick={onPublish}
              disabled={publishing || draft.base_stale || !!draft.draft_parse_error}
              title={draft.base_stale ? t('k_01e1da98e1') : t('k_5a4798f93d')}>
              {publishing ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5 mr-1" />}
              {t('k_5c5ff23458')}
            </Button>
          )}
        </div>
      </div>

      {/* 变更清单 */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1 max-w-xs">
          <Search className="h-3.5 w-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input value={kw} onChange={(e) => setKw(e.target.value)} placeholder={t("k_81b7dcf612")}
            className="h-8 pl-7 text-xs" />
        </div>
        <div className="text-xs text-muted-foreground">
          {t('k_1e659736f4', { p0: visible.length, p1: filter !== 'all' ? t('k_555f53dcd7') : '' })}
        </div>
      </div>

      <ScrollArea className="h-[calc(62vh-13rem)] rounded-lg border">
        <div className="p-2 space-y-1.5">
          {visible.length === 0 && (
            <div className="py-8 text-center text-sm text-muted-foreground">{t("k_0c3c300b04")}</div>
          )}
          {visible.map((o) => {
            const meta = STATE_META[o.state || 'unchanged'] || STATE_META.unchanged;
            const open = !!expanded[o.key];
            const hasDetail = o.state === 'changed' || o.state === 'added';
            return (
              <div key={o.key} className={`rounded-md border px-3 py-2 ${o.state === 'removed' ? 'opacity-80' : ''}`}>
                <div className="flex items-center gap-2 flex-wrap">
                  {hasDetail ? (
                    <button className="shrink-0 text-muted-foreground" onClick={() => setExpanded((p) => ({ ...p, [o.key]: !p[o.key] }))}
                      title={open ? t('k_60940000f9') : t('k_a96aa977f1')}>
                      {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                    </button>
                  ) : <span className="w-3.5 shrink-0" />}
                  <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${meta.dot}`} />
                  <span className={`text-sm font-medium ${o.state === 'removed' ? 'line-through' : ''}`}>
                    {o.display_name}
                  </span>
                  <span className="font-mono text-[11px] text-muted-foreground">{o.key}</span>
                  <Badge variant="outline" className={`text-[10px] px-1.5 h-4 ${meta.cls}`}>{meta.label}</Badge>
                  <span className="ml-auto text-[11px] text-muted-foreground">{deltaSummary(o)}</span>
                </div>
                {o.state === 'removed' && (
                  <div className="mt-1 pl-5 text-[11px] text-muted-foreground">
                    {t('k_33db4cea91')}
                  </div>
                )}
                {open && <DeltaDetail o={o} />}
                {open && o.state === 'added' && !o.delta && (
                  <div className="mt-2 pl-5 text-[11px] text-muted-foreground">
                    {t('k_01e3a79217', { p0: o.primary_table || t('k_906ad18b48') })}</div>
                )}
              </div>
            );
          })}
        </div>
      </ScrollArea>

      {/* 丢弃草案确认 */}
      <Dialog open={discardOpen} onOpenChange={setDiscardOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t("k_a0eb9acff4")}</DialogTitle></DialogHeader>
          <div className="text-sm text-muted-foreground space-y-2">
            <p>
              {t('k_5d04331aae', { p0: draft.draft_updated_by || t('k_30b927775e'), p1: draft.draft_updated_at?.slice(0, 16).replace('T', ' ') || '—', p2: counts.added, p3: counts.changed, p4: counts.removed })}</p>
            <p className="text-red-600">{t("k_3b3fcfb34d")}</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDiscardOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={confirmDiscard} disabled={discarding}>
              {discarding ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Undo2 className="h-4 w-4 mr-1" />}
              {t('k_55bfb65260')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 原始内容（排查附录，不是主视图） */}
      <Dialog open={jsonOpen} onOpenChange={setJsonOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>{t("k_840aad6766")}</DialogTitle></DialogHeader>
          <div className="text-xs text-muted-foreground">
            {t('k_7b725b4ac7')}
          </div>
          <ScrollArea className="h-[52vh] rounded-md border mt-2">
            {jsonLoading ? (
              <div className="py-6 text-center text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 mr-1 inline animate-spin" />{t('k_300ee3dee4')}
              </div>
            ) : jsonError ? (
              <div className="py-6 text-center text-sm text-red-600">{jsonError}</div>
            ) : (
              <pre className="p-3 text-[11px] leading-relaxed whitespace-pre-wrap break-all">{jsonText}</pre>
            )}
          </ScrollArea>
          <DialogFooter>
            <Button variant="outline" onClick={() => setJsonOpen(false)}>{t("k_6c14bd7f6f")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
