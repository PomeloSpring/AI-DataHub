/**
 * KbDocUpdateDialog — 知识库文档更新（复用内容域既有写通道）。
 *
 * 为什么要复用 `/api/content/knowledge-bases*` 而不是自建写口：那条通道已经把代价
 * 最贵的护栏都做好了 —— 凭据/内部标识扫描（语料会被 AS-BOT 检索进回答）、
 * `expected_sha256` 乐观锁（别人先改过就 409）、派生文档与内容同步文档 403 拒改、
 * 先上传新件再删旧件、空正文与超体积拒写。另建一条等于把这些全部重造一遍并且必然漏。
 *
 * 三件必须在界面上说清、不能藏的代价：
 *  1. **整篇替换**：云端没有原地更新接口，保存 = 传新件 + 删旧件，`source_id` 会变；
 *  2. **重新解析有延迟**：存完不会立刻被检索命中（状态非 ready）；
 *  3. **派生/同步文档只读**：那是本体建模或内容中心自动生成的，在这里改下次同步就被覆盖。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from '@/lib/toast';
import {
  kbDocError, kbDocsApi, type KbBase, type KbDoc, type KbTreeNode,
} from '@/api/kbDocs';
import { useTranslation } from 'react-i18next';

export interface KbDocPrefill {
  kbName?: string;
  docTitle?: string;
  /** 提议正文：默认追加到原文之后（追加可逆、可审；覆盖式预填没有 diff 可看） */
  content?: string;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  prefill?: KbDocPrefill | null;
  canWrite: boolean;
  /** 保存成功回执：`知识库名 / 文档标题` */
  onApplied?: (ref: string) => void;
}

/** 目录树拍平成文档清单（只列文档，目录不能当正文改）。 */
function flattenDocs(nodes: KbTreeNode[], out: KbTreeNode[] = []): KbTreeNode[] {
  for (const n of nodes) {
    if (n.is_dir) flattenDocs(n.children || [], out);
    else out.push(n);
  }
  return out;
}

export default function KbDocUpdateDialog({
  open, onOpenChange, prefill = null, canWrite, onApplied,
}: Props) {
  const { t } = useTranslation('ui');
  const [bases, setBases] = useState<KbBase[]>([]);
  const [kb, setKb] = useState('');
  const [docs, setDocs] = useState<KbTreeNode[]>([]);
  const [docId, setDocId] = useState('');
  const [doc, setDoc] = useState<KbDoc | null>(null);
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(false);
  const [docLoading, setDocLoading] = useState(false);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const prefillRef = useRef(prefill);
  prefillRef.current = prefill;

  const loadDoc = useCallback(async (kbName: string, sourceId: string) => {
    setDocLoading(true);
    setError('');
    try {
      const { data } = await kbDocsApi.doc(kbName, sourceId);
      setDoc(data);
      const body = data.content || '';
      const extra = (prefillRef.current?.content || '').trim();
      setDraft(extra ? [body.trim(), extra].filter(Boolean).join('\n\n') : body);
    } catch (e) {
      setDoc(null); setDraft(''); setError(kbDocError(e));
    } finally {
      setDocLoading(false);
    }
  }, []);

  const loadTree = useCallback(async (kbName: string) => {
    setLoading(true);
    setError('');
    try {
      const { data } = await kbDocsApi.tree(kbName);
      const list = flattenDocs(data.nodes || []);
      setDocs(list);
      // 提议预填：按标题匹配文档；匹配不上就留空让人自己选（不猜）
      const hint = String(prefillRef.current?.docTitle || '').trim().toLowerCase();
      const hit = hint ? list.find(d => d.title.toLowerCase() === hint) : undefined;
      if (hit) { setDocId(hit.source_id); await loadDoc(kbName, hit.source_id); }
    } catch (e) {
      setDocs([]); setError(kbDocError(e));
    } finally {
      setLoading(false);
    }
  }, [loadDoc]);

  useEffect(() => {
    if (!open) return;
    setDoc(null); setDraft(''); setDocId(''); setError('');
    const wanted = String(prefillRef.current?.kbName || '').trim();
    setKb(wanted);
    kbDocsApi.bases()
      .then(({ data }) => {
        const items = (data.items || []).filter(b => b.supports_docs);
        setBases(items);
        const target = wanted
          ? items.find(b => b.name === wanted)
          : items.find(b => b.can_edit) || items[0];
        if (target) { setKb(target.name); void loadTree(target.name); }
      })
      .catch(e => setError(kbDocError(e)));
    // 只在打开时初始化（prefill 每次渲染都是新对象）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const save = async () => {
    if (!doc || !kb) return;
    if (!draft.trim()) { toast.error(t('k_b1fdbf0e99')); return; }
    setSaving(true);
    try {
      const { data } = await kbDocsApi.saveDoc(kb, doc.source_id, draft, doc.content_sha256);
      toast.success(t('k_918df7c018'));
      if (data?.warnings?.length) toast.warning(data.warnings.join('；'));
      onApplied?.(`${kb} / ${data?.title || doc.title}`);
      onOpenChange(false);
    } catch (e) {
      toast.error(kbDocError(e));
    } finally {
      setSaving(false);
    }
  };

  const readOnly = !canWrite || !!doc?.derived;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[88vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('k_828ea6af0b')}</DialogTitle>
          <DialogDescription>{t('k_82ea2a1cf7')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {error && <div className="text-xs text-destructive">{error}</div>}

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label>{t('k_4185cc5f4d')}</Label>
              <Select value={kb || undefined} disabled={loading}
                onValueChange={v => { setKb(v); setDoc(null); setDraft(''); setDocId(''); void loadTree(v); }}>
                <SelectTrigger className="mt-1 w-full" aria-label={t('k_4185cc5f4d')}>
                  <SelectValue placeholder={loading ? t('k_26b5bd4947') : t('k_382f4b5559')} />
                </SelectTrigger>
                <SelectContent>
                  {bases.map(b => (
                    <SelectItem key={b.name} value={b.name} disabled={!b.can_edit}>
                      {b.name}{b.can_edit ? '' : `（${b.reason}）`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>{t('k_a70ee8be45')}</Label>
              <Select value={docId || undefined} disabled={!kb || loading}
                onValueChange={v => { setDocId(v); void loadDoc(kb, v); }}>
                <SelectTrigger className="mt-1 w-full" aria-label={t('k_a70ee8be45')}>
                  <SelectValue placeholder={loading ? t('k_26b5bd4947') : t('k_382f4b5559')} />
                </SelectTrigger>
                <SelectContent>
                  {docs.map(d => (
                    <SelectItem key={d.source_id} value={d.source_id}>{d.title}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {doc?.derived && (
            <div className="rounded border border-destructive/40 bg-destructive/5 p-2 text-xs leading-5">
              {t('k_20936f028f')}：{doc.read_only_reason}
            </div>
          )}
          {!canWrite && (
            <div className="text-xs text-muted-foreground">{t('k_bb6e97883c')}</div>
          )}

          {docLoading && <div className="text-xs text-muted-foreground">{t('k_26b5bd4947')}</div>}
          {doc && !docLoading && (
            <>
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                <span>{doc.title}</span>
                <span>·</span>
                <span>{doc.status || '—'}</span>
                <span>·</span>
                <span>{doc.updated_at || '—'}</span>
                {/* 目录树与全文编辑仍在内容中心：深链把当前这篇带过去（?doc=），
                    不在这里重造一棵树 */}
                <Link className="ml-auto text-primary underline"
                  title={t('k_27b6b0b850')}
                  to={`/content/kb-docs/${encodeURIComponent(kb)}?doc=${encodeURIComponent(doc.source_id)}`}>
                  {t('k_873070e46c')}
                </Link>
              </div>
              <Textarea value={draft} onChange={e => setDraft(e.target.value)} rows={18}
                className="font-mono text-xs leading-6" readOnly={readOnly} disabled={readOnly}
                aria-label={t('k_a0d95bc304')} />
            </>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t('k_4d0b4688c7')}</Button>
          <Button onClick={save} disabled={saving || !doc || readOnly}>
            {saving ? t('k_2a33020e5b') : t('k_cb8beef7cf')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}