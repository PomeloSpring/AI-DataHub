import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft, ChevronRight, FileText, FolderClosed, FolderOpen, Plus, RefreshCw,
  Save, ShieldAlert,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  kbDocError, kbDocsApi, kbStatusHint, kbTypeLabel,
  type KbDoc, type KbTree, type KbTreeNode,
} from '@/api/kbDocs';
import { ErrorBanner, LoadingRow, NoticeBanner, useContentPerms } from './shared';
import { useTranslation } from 'react-i18next';

/**
 * 知识库文档 — 详情页（`/content/kb-docs/:kb`）。
 *
 * 知识库是一个目录，所以进来先看到**目录树**，点树里的文档才取正文；库级信息
 * （名称/描述/类型/状态）由目录接口一起带回，不再多发一次清单请求。
 *
 * 四条必须演出来的约束（服务端把关，这里负责如实显示）：
 * 1. **只提交改动的这一篇**。云端没有原地更新接口（服务端"传新件→删旧件"），
 *    「保存」只 PUT 当前文档；未改动的文档连请求都不发（那会白换 id、白重跑解析）。
 * 2. **保存后 source_id 会变**。树上按 `previous_source_id → source_id` 迁移选中态，
 *    继续拿旧 id 编辑就是改一篇不存在的文档。
 * 3. **派生文档只读**。带本体版本戳的文档由「本体建模 → 上云同步」生成，在这里改
 *    下次同步就被覆盖 —— 编辑区禁用并把原因和去处一起说清。
 * 4. **目录结构调整（改名/移动/删除）本批未开放**。云端无回收站，误删即失；页面显式
 *    告知"本批未开放"，而不是放个点了报错的按钮。
 *
 * 正文区不做"渲染预览"：语料本身就是 Markdown，维护动作是改源码，预览只是把同一份
 * 文本再画一遍，多一次切换只会挡路（用户 2026-11-05 反馈后移除）。
 */
export default function KnowledgeDocDetail() {
  const { t } = useTranslation('ui');
  const navigate = useNavigate();
  const { kb } = useParams<{ kb: string }>();
  const baseName = kb ? decodeURIComponent(kb) : '';

  const { has } = useContentPerms();
  const canWrite = has('kbdoc:write');

  const [tree, setTree] = useState<KbTree | null>(null);
  const [treeLoading, setTreeLoading] = useState(true);
  const [treeError, setTreeError] = useState('');

  const [openIds, setOpenIds] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState('');
  const [doc, setDoc] = useState<KbDoc | null>(null);
  const [draft, setDraft] = useState('');
  const [docLoading, setDocLoading] = useState(false);
  const [docError, setDocError] = useState('');
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState<null | { kind: 'doc' | 'folder'; parentId: string }>(null);
  const [newTitle, setNewTitle] = useState('');

  const dirty = !!doc && draft !== doc.content;
  const readOnly = !!doc && (doc.derived || !canWrite);
  const flatTree = useMemo(() => tree?.flat ?? false, [tree]);

  const loadTree = useCallback((keepSelection = true) => {
    if (!baseName) { setTreeLoading(false); return; }
    setTreeLoading(true); setTreeError('');
    kbDocsApi.tree(baseName)
      .then(res => {
        setTree(res.data);
        if (!keepSelection) { setSelectedId(''); setDoc(null); setDraft(''); }
      })
      // 拉不到树不能画一棵空树：那是把"云端查不到"演成"这个库没文档"
      .catch(err => { setTreeError(kbDocError(err)); setTree(null); })
      .finally(() => setTreeLoading(false));
  }, [baseName]);

  useEffect(() => { loadTree(false); }, [loadTree]);

  const openDoc = useCallback((node: KbTreeNode) => {
    if (node.is_dir) {
      setOpenIds(prev => {
        const next = new Set(prev);
        next.has(node.source_id) ? next.delete(node.source_id) : next.add(node.source_id);
        return next;
      });
      return;
    }
    setSelectedId(node.source_id); setDocError(''); setNotice('');
    setDocLoading(true);
    kbDocsApi.doc(baseName, node.source_id)
      .then(res => { setDoc(res.data); setDraft(res.data.content); })
      .catch(err => { setDoc(null); setDraft(''); setDocError(kbDocError(err)); })
      .finally(() => setDocLoading(false));
  }, [baseName]);

  /** 保存只发这一篇；成功后云端返回新 source_id，选中态与树一起跟着换 */
  const save = async () => {
    if (!doc) return;
    setSaving(true); setDocError(''); setNotice('');
    try {
      const res = await kbDocsApi.saveDoc(baseName, doc.source_id, draft, doc.content_sha256);
      const out = res.data;
      setDoc(d => (d ? { ...d, source_id: out.source_id, content_sha256: out.content_sha256 ?? '', content: draft } : d));
      if (selectedId) setSelectedId(prev => (prev === out.previous_source_id ? out.source_id : prev));
      setNotice(out.message + (out.warning ? `｜${out.warning}` : '')
        + ((out.warnings?.length ?? 0) ? t('k_63110cd074', { p0: out.warnings!.join('；') }) : ''));
      loadTree();
    } catch (err) {
      setDocError(kbDocError(err));
    } finally {
      setSaving(false);
    }
  };

  /** 冲突（409）后的正确动作是重新加载别人的版本，不做"强行覆盖" */
  const reloadDoc = () => {
    if (!doc) return;
    const node = findNode(tree?.nodes ?? [], doc.source_id);
    if (node) openDoc(node);
    else loadTree(false);
  };

  const create = async () => {
    const title = newTitle.trim();
    if (!title || !creating) return;
    setDocError(''); setNotice(''); setSaving(true);
    try {
      const res = creating.kind === 'doc'
        ? await kbDocsApi.createDoc(baseName, title, '', creating.parentId)
        : await kbDocsApi.createFolder(baseName, title, creating.parentId);
      setNotice(res.data.message);
      setCreating(null); setNewTitle('');
      loadTree();
    } catch (err) {
      setDocError(kbDocError(err));
    } finally {
      setSaving(false);
    }
  };

  if (!baseName) {
    return (
      <ErrorBanner message={t('k_d617149917')} />
    );
  }

  return (
    <div className="space-y-4">
      <header className="space-y-2">
        <Button size="sm" variant="ghost" className="-ml-2 text-xs" onClick={() => navigate('/content/kb-docs')}>
          <ArrowLeft className="mr-1 h-3.5 w-3.5" />{t('k_cf989ec1b6')}
        </Button>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-semibold">{tree?.base.name || baseName}</h1>
          {tree && (
            <>
              <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                {kbTypeLabel(tree.base.kb_type)}
              </span>
              <span className={`rounded px-1.5 py-0.5 text-[11px]
                ${tree.base.status === 'active'
                  ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
                  : 'bg-amber-500/15 text-amber-700 dark:text-amber-400'}`}>
                {tree.base.status === 'active' ? t('k_53ace4305f') : t('k_a376b96a3b', { p0: tree.base.status || t('k_1622dc9b6b') })}
              </span>
            </>
          )}
        </div>
        {tree?.base.description && (
          <p className="max-w-3xl text-sm text-muted-foreground">{tree.base.description}</p>
        )}
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={() => loadTree()} disabled={treeLoading}>
          <RefreshCw className={`mr-1 h-3.5 w-3.5 ${treeLoading ? 'animate-spin' : ''}`} />{t('k_90b5a467c1')}
        </Button>
        {canWrite && (
          <>
            <Button size="sm" variant="outline" onClick={() => setCreating({ kind: 'doc', parentId: '' })}>
              <Plus className="mr-1 h-3.5 w-3.5" />{t('k_ebdff5c4a3')}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setCreating({ kind: 'folder', parentId: '' })}>
              <Plus className="mr-1 h-3.5 w-3.5" />{t('k_547ee197e5')}
            </Button>
          </>
        )}
      </div>

      {!!notice && <NoticeBanner message={notice} onDismiss={() => setNotice('')} />}

      <div className="grid gap-4 lg:grid-cols-[minmax(260px,340px)_1fr]">
        <aside className="space-y-2 rounded-xl border bg-card p-3">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>{t("k_0d791f13ed")}</span>
            {tree && <span>{t('k_1b906869f8', { p0: tree.doc_count, p1: tree.folder_count, p2: flatTree ? t('k_1f8600a2b2') : '' })}</span>}
          </div>
          {treeLoading && <LoadingRow text={t('k_9da1de4207')} />}
          <ErrorBanner message={treeError} onRetry={() => loadTree()} />
          {!treeLoading && !treeError && tree && tree.nodes.length === 0 && (
            <p className="py-6 text-center text-xs text-muted-foreground">
              {t('k_08b9b0e9b1')}
            </p>
          )}
          {!treeLoading && tree && (
            <ul className="space-y-0.5 text-sm">
              {tree.nodes.map(n => (
                <TreeNode key={n.source_id} node={n} depth={0} openIds={openIds} selectedId={selectedId}
                  onNode={openDoc} canWrite={canWrite} onCreateIn={id => setCreating({ kind: 'doc', parentId: id })} />
              ))}
            </ul>
          )}
        </aside>

        <section className="space-y-3">
          {creating && (
            <div className="space-y-2 rounded-xl border bg-card p-3">
              <Label htmlFor="kbd-new">{creating.kind === 'doc' ? t('k_2d130a1b93') : t('k_f0fa905a23')}</Label>
              <div className="flex items-center gap-2">
                <Input id="kbd-new" value={newTitle} onChange={e => setNewTitle(e.target.value)}
                  placeholder={creating.kind === 'doc' ? t('k_a2031fbe42') : t('k_3ea0c09eb0')} className="flex-1" />
                <Button size="sm" onClick={create} disabled={saving || !newTitle.trim()}>{t("k_f526c89937")}</Button>
                <Button size="sm" variant="ghost" onClick={() => { setCreating(null); setNewTitle(''); }}>{t("k_4d0b4688c7")}</Button>
              </div>
              {creating.parentId && (
                <p className="text-[11px] text-muted-foreground">
                  {t('k_cb603e8714', { p0: findNode(tree?.nodes ?? [], creating.parentId)?.title ?? creating.parentId })}</p>
              )}
            </div>
          )}

          {docLoading && <LoadingRow text={t('k_217694cd8f')} />}
          <ErrorBanner message={docError} onRetry={doc ? reloadDoc : undefined} />

          {!docLoading && !doc && !docError && (
            <div className="rounded-xl border border-dashed bg-muted/20 p-10 text-center">
              <p className="text-sm font-medium">{t("k_16bae4965a")}</p>
              <p className="mt-1 text-xs text-muted-foreground">{t("k_3816a3e586")}</p>
            </div>
          )}

          {doc && !docLoading && (
            <div className="space-y-3 rounded-xl border bg-card p-4">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-base font-medium">{doc.title}</h2>
                <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">{doc.source_type || 'markdown'}</span>
                {doc.status !== 'ready' && (
                  <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] text-amber-600 dark:text-amber-400">
                    {kbStatusHint(doc.status)}
                  </span>
                )}
                <span className="ml-auto text-[11px] text-muted-foreground">{t('k_69f398b01a', { p0: doc.updated_at || '—' })}</span>
              </div>

              {doc.derived && (
                <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-xs leading-5">
                  <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
                  <span>{doc.read_only_reason}</span>
                </div>
              )}
              {!canWrite && (
                <p className="text-xs text-muted-foreground">
                  {t('k_bb6e97883c')}
                </p>
              )}

              <Textarea
                value={draft} onChange={e => setDraft(e.target.value)} rows={26}
                aria-label={t("k_96950885c6")} className="font-mono text-xs leading-6"
                readOnly={readOnly} disabled={readOnly}
              />

              <div className="flex flex-wrap items-center gap-2">
                <Button onClick={save} disabled={!canWrite || !dirty || saving || doc.derived}>
                  <Save className="mr-1.5 h-4 w-4" />{saving ? t('k_7ef44a7d1d') : t('k_81c2b5ccda')}
                </Button>
                {dirty && (
                  <Button size="sm" variant="ghost" onClick={() => setDraft(doc.content)} disabled={saving}>{t("k_cf1fe2f1b4")}</Button>
                )}
                {dirty && <span className="text-[11px] text-amber-600">{t("k_06c84d4a53")}</span>}
                {!dirty && (
                  <span className="text-xs text-muted-foreground">{t("k_def69a36b7")}</span>
                )}
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function findNode(nodes: KbTreeNode[], id: string): KbTreeNode | null {
  for (const n of nodes) {
    if (n.source_id === id) return n;
    const hit = n.children?.length ? findNode(n.children, id) : null;
    if (hit) return hit;
  }
  return null;
}

/** 目录树节点（递归渲染；扁平库自然只有一层，不假造缩进） */
function TreeNode({ node, depth, openIds, selectedId, onNode, canWrite, onCreateIn }: {
  node: KbTreeNode; depth: number; openIds: Set<string>; selectedId: string;
  onNode: (n: KbTreeNode) => void; canWrite: boolean; onCreateIn: (id: string) => void;
}) {
  const { t } = useTranslation('ui');
  const open = openIds.has(node.source_id);
  const selected = node.source_id === selectedId;
  const hint = node.is_dir ? '' : node.status !== 'ready' ? node.status : '';
  return (
    <li>
      <div className={`flex items-center gap-1 rounded-md pr-1
        ${selected ? 'bg-primary text-primary-foreground' : 'hover:bg-accent'}`}
      style={{ paddingLeft: depth * 14 }}>
        <button
          type="button"
          onClick={() => onNode(node)}
          aria-label={t('k_d864da428e', { p0: node.is_dir ? (open ? t('k_e082621c93') : t('k_e2edde5adb')) : t('k_d7098f5050'), p1: node.title })}
          className="flex min-w-0 flex-1 items-center gap-1.5 py-1.5 text-left text-sm"
        >
          {node.is_dir
            ? (open
              ? <FolderOpen className="h-3.5 w-3.5 shrink-0" />
              : <FolderClosed className="h-3.5 w-3.5 shrink-0" />)
            : <FileText className="h-3.5 w-3.5 shrink-0 opacity-70" />}
          {!node.is_dir && <ChevronRight className="h-3 w-3 shrink-0 opacity-40" />}
          <span className="truncate">{node.title}</span>
          {node.is_dir && <span className="shrink-0 text-[10px] opacity-60">{(node.children ?? []).length}</span>}
          {node.orphan && <span className="shrink-0 rounded bg-amber-500/20 px-1 text-[10px] text-amber-600 dark:text-amber-400">{t("k_6b471d1d33")}</span>}
          {hint && <span className="shrink-0 rounded bg-amber-500/20 px-1 text-[10px] text-amber-600 dark:text-amber-400">{hint}</span>}
        </button>
        {node.is_dir && canWrite && (
          <button type="button" onClick={() => onCreateIn(node.source_id)}
            aria-label={t('k_c1198e65bc', { p0: node.title })}
            className="shrink-0 rounded p-1 opacity-60 hover:opacity-100">
            <Plus className="h-3 w-3" />
          </button>
        )}
      </div>
      {node.is_dir && open && !!node.children?.length && (
        <ul className="space-y-0.5">
          {node.children.map(c => (
            <TreeNode key={c.source_id} node={c} depth={depth + 1} openIds={openIds} selectedId={selectedId}
              onNode={onNode} canWrite={canWrite} onCreateIn={onCreateIn} />
          ))}
        </ul>
      )}
    </li>
  );
}
