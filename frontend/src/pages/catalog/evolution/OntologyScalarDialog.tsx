/**
 * OntologyScalarDialog — 本体对象标量编辑（显示名 / 说明 / 别名）。
 *
 * 为什么只有这三个字段：对象的 key、属性清单、links 属于建模结构，改它等于重画模型，
 * 必须回「本体建模」按建模流程走。这里只允许改"这个名字怎么叫、这段说明怎么写"——
 * 业务纠正里有八成就是这个，而它恰好是 AI 最容易讲错、改起来成本最低的部分。
 *
 * 两条纪律：
 *  1. **只落草案**（`save_draft_only`，不级联、不生效）。发布是独立的第二段
 *     （`ontology:publish`），发布会重展开对象、重建图谱并同步知识库——所以这里
 *     把「已存草案」与「已发布」分成两个状态显示，绝不把草案说成已生效。
 *  2. **系统能力本体不出现在清单里**：它由声明目录维护，界面改完会被下次刷新覆盖。
 *     服务端不给入口（并附说明），前端如实展示，不自己判断 kind。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from '@/lib/toast';
import client from '@/api/client';
import { ontologyApi } from '@/api/ontology';
import { useTranslation } from 'react-i18next';

export interface OntologyObjectOption {
  key: string;
  display_name: string;
  description: string;
  aliases: string[];
  model_id: number;
  model_name: string;
  datasource_names: string[];
}

/** 提议预填：只给业务名，落到哪个对象由人用选择框确认（不传内部 id）。 */
export interface OntologyScalarPrefill {
  objectName?: string;
  displayName?: string;
  description?: string;
  aliases?: string[];
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  prefill?: OntologyScalarPrefill | null;
  canPublish?: boolean;
  /** 草案写入成功回执：值是「模型名 / 对象名」这类可读落点，不是内部 id 拼串 */
  onApplied?: (ref: string) => void;
}

const splitAliases = (s: string): string[] =>
  s.split(/[,，]/).map(a => a.trim()).filter(Boolean);

const errText = (e: any, fallback: string): string =>
  e?.response?.data?.detail || e?.message || fallback;

export default function OntologyScalarDialog({
  open, onOpenChange, prefill = null, canPublish = false, onApplied,
}: Props) {
  const { t } = useTranslation('ui');
  const [objects, setObjects] = useState<OntologyObjectOption[]>([]);
  const [note, setNote] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  /** 选中项：`model_id::object_key`（两个都是服务端下发的，前端不手填） */
  const [picked, setPicked] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [description, setDescription] = useState('');
  const [aliasText, setAliasText] = useState('');
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  /** 已存草案的落点（发布按钮只在有草案时出现） */
  const [draftOf, setDraftOf] = useState<{ model_id: number; label: string } | null>(null);
  const [askPublish, setAskPublish] = useState(false);

  const applyObject = useCallback((obj: OntologyObjectOption, pre?: OntologyScalarPrefill | null) => {
    setPicked(`${obj.model_id}::${obj.key}`);
    setDraftOf(null);
    setAskPublish(false);
    setDisplayName(pre?.displayName || obj.display_name || obj.key);
    setDescription(pre?.description ?? obj.description ?? '');
    const aliases = pre?.aliases?.length ? [...obj.aliases, ...pre.aliases] : obj.aliases;
    setAliasText(Array.from(new Set(aliases.map(a => a.trim()).filter(Boolean))).join(', '));
  }, []);

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    client.get('/evolution/ontology-objects')
      .then(({ data }) => {
        const items: OntologyObjectOption[] = Array.isArray(data?.items) ? data.items : [];
        setObjects(items);
        setNote(String(data?.note || ''));
        // 提议预填：按业务名/对象名匹配到具体对象（匹配不上就留空，让人自己选，
        // 绝不"猜一个最像的"——改错对象的说明比不改更糟）
        const hint = String(prefill?.objectName || '').trim().toLowerCase();
        const hit = hint
          ? items.find(o => o.key.toLowerCase() === hint || o.display_name.toLowerCase() === hint)
          : undefined;
        if (hit) applyObject(hit, prefill);
      })
      .catch((e: any) => { setObjects([]); setError(errText(e, t('k_9aa34a93cb'))); })
      .finally(() => setLoading(false));
  }, [prefill, applyObject, t]);

  useEffect(() => {
    if (!open) return;
    setPicked(''); setDisplayName(''); setDescription(''); setAliasText('');
    setDraftOf(null); setAskPublish(false);
    load();
    // 只在打开时拉一次清单（prefill 身份每次渲染都会变，不能进依赖）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const current = useMemo(
    () => objects.find(o => `${o.model_id}::${o.key}` === picked) || null, [objects, picked]);

  const save = async () => {
    if (!current) { toast.error(t('k_754dac51ed')); return; }
    if (!displayName.trim()) { toast.error(t('k_8f93dcb3fc')); return; }
    setSaving(true);
    try {
      const { data } = await client.post('/evolution/ontology-objects/scalars', {
        model_id: current.model_id,
        object_key: current.key,
        display_name: displayName.trim(),
        description,
        aliases: splitAliases(aliasText),
      });
      if (data?.mode === 'unchanged') {
        toast.info(data?.note || t('k_609857a415'));
        return;
      }
      setDraftOf({
        model_id: current.model_id,
        label: `${current.model_name || t('k_e5b5171f27')} / ${displayName.trim()}`,
      });
      toast.success(t('k_609857a415'));
      onApplied?.(`${current.model_name || 'ontology'} / ${current.display_name || current.key}`);
    } catch (e: any) {
      toast.error(errText(e, t('k_6de920b4e4')));
    } finally {
      setSaving(false);
    }
  };

  const publish = async () => {
    if (!draftOf) return;
    setPublishing(true);
    try {
      await ontologyApi.publishDraft(draftOf.model_id);
      toast.success(t('k_176a2eb4eb'));
      setDraftOf(null);
      setAskPublish(false);
      load();
    } catch (e: any) {
      toast.error(errText(e, t('k_6de920b4e4')));
    } finally {
      setPublishing(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl max-h-[88vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('k_a80285df2d')}</DialogTitle>
          <DialogDescription>{note || t('k_6d5778907c')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {error && (
            <div className="flex items-center gap-2 text-xs text-destructive">
              {error}
              <Button variant="outline" size="sm" className="h-6 text-xs" onClick={load}>{t('k_e2d53a6d3a')}</Button>
            </div>
          )}

          <div>
            <Label>{t('k_754dac51ed')}</Label>
            <Select value={picked} disabled={loading}
              onValueChange={v => {
                const hit = objects.find(o => `${o.model_id}::${o.key}` === v);
                if (hit) applyObject(hit, prefill);
              }}>
              <SelectTrigger className="mt-1 w-full" aria-label={t('k_754dac51ed')}>
                <SelectValue placeholder={loading ? t('k_26b5bd4947') : t('k_382f4b5559')} />
              </SelectTrigger>
              <SelectContent>
                {objects.map(o => (
                  <SelectItem key={`${o.model_id}::${o.key}`} value={`${o.model_id}::${o.key}`}>
                    {o.model_name} · {o.display_name}（{o.key}）
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {/* 提议里的业务名对不上清单时要说出来：否则人只会看到「自己没选中」
                而以为预填坏了，转头去挑一个看起来像的对象——改错对象比不改更糟 */}
            {!!prefill?.objectName && !loading && !error && !current && objects.length > 0 && (
              <p className="mt-1 text-xs text-amber-600">
                {t('k_83a87bd55a', { p0: prefill.objectName })}
              </p>
            )}
          </div>

          {current && (
            <>
              <div className="rounded border bg-muted/30 p-2 text-[11px] leading-5 text-muted-foreground">
                <div><b>{t('k_e52c2701b6')}</b></div>
                <div>{current.display_name}（{current.key}）</div>
                <div>{current.description || '—'}</div>
                <div>{(current.aliases || []).join('、') || '—'}</div>
                {!!(current.datasource_names || []).length && (
                  <div>{current.datasource_names.join('、')}</div>
                )}
              </div>

              <div>
                <Label>{t('k_8f93dcb3fc')}</Label>
                <Input className="mt-1" value={displayName} onChange={e => setDisplayName(e.target.value)} />
              </div>
              <div>
                <Label>{t('k_26670dda42')}</Label>
                <Textarea className="mt-1" rows={4} value={description}
                  onChange={e => setDescription(e.target.value)} />
              </div>
              <div>
                <Label>{t('k_901c3d9c8f')}</Label>
                <Input className="mt-1" value={aliasText} onChange={e => setAliasText(e.target.value)} />
              </div>

              {draftOf && (
                <div className="rounded border border-amber-500/40 bg-amber-500/5 p-2 text-xs leading-5">
                  <div>{t('k_609857a415')}：{draftOf.label}</div>
                  {canPublish ? (
                    askPublish ? (
                      <div className="mt-1 space-y-1">
                        <div>{t('k_0de3f8658a')}</div>
                        <div className="flex gap-2">
                          <Button size="sm" onClick={publish} disabled={publishing}>
                            {publishing ? t('k_2a33020e5b') : t('k_e83a256e4f')}
                          </Button>
                          <Button size="sm" variant="outline" onClick={() => setAskPublish(false)}>
                            {t('k_4d0b4688c7')}
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <Button size="sm" variant="outline" className="mt-1" onClick={() => setAskPublish(true)}>
                        {t('k_83611abd5f')}
                      </Button>
                    )
                  ) : (
                    <div className="mt-1 text-muted-foreground">{t('k_8633ed4e22')}</div>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t('k_4d0b4688c7')}</Button>
          <Button onClick={save} disabled={saving || !current}>
            {saving ? t('k_2a33020e5b') : t('k_fadf24dbc5')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}