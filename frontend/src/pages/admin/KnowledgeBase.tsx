import { useState, useEffect } from 'react';
import { t , t as tr } from '@/i18n/runtime';
import {
  Plus, Edit2, Trash2, Database, Cloud, Folder,
  RefreshCw, Sparkles, Plug,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Spinner } from '@/components/ui/spinner';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';

import client from '../../api/client';
import { useTranslation } from 'react-i18next';

// ── Types ──────────────────────────────────────────────────────────

interface KnowledgeBase {
  id: number;
  name: string;
  description: string;
  kb_type: 'local' | 'vector_db' | 'cloud_rag' | 'qmind';
  source_config: Record<string, any>;
  status: 'active' | 'inactive' | 'error';
  document_count: number;
  chunk_count: number;
  last_sync_at: string | null;
  created_at: string;
  updated_at: string;
  // 检索通道可用性：由服务端 provider 注册表派生（前端不重写一份 kb_type 白名单，
  // 否则就是“配置页能选、检索时问不到”的漂移成因）。原因文案也原文来自服务端。
  retrieval?: { readable?: boolean; provider?: string; corpus_origin?: string; reason?: string };
}

interface QMindNotebook {
  notebook_id: string;
  title: string;
  org_id?: string;
  description?: string;
  status?: string;
  imported?: boolean;
  kb_id?: number | null;
}

// ── KB Type Config ─────────────────────────────────────────────────

const KB_TYPES = [
  {
    value: 'qmind',
    label: t('k_fd31541d3d'),
    icon: Sparkles,
    desc: t('k_5781a0f8c9'),
    color: 'bg-indigo-100 text-indigo-800',
  },
  {
    value: 'local',
    label: t('k_ca35b7aca0'),
    icon: Folder,
    desc: t('k_857612f288'),
    color: 'bg-blue-100 text-blue-800',
  },
  {
    value: 'vector_db',
    label: t('k_f9f851e574'),
    icon: Database,
    desc: t('k_2062480063'),
    color: 'bg-green-100 text-green-800',
  },
  {
    value: 'cloud_rag',
    label: t('k_e2acf1a8aa'),
    icon: Cloud,
    desc: t('k_2b676e592f'),
    color: 'bg-purple-100 text-purple-800',
  },
];

// ── Main Component ─────────────────────────────────────────────────

export default function KnowledgeBase() {
  const { t } = useTranslation('ui');
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(false);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [editingKB, setEditingKB] = useState<KnowledgeBase | null>(null);
  const [showDeleteDialog, setShowDeleteDialog] = useState<KnowledgeBase | null>(null);

  // Qoder QMind notebook 检索/导入
  const [showQmindDialog, setShowQmindDialog] = useState(false);
  const [qmindLoading, setQmindLoading] = useState(false);
  const [qmindImporting, setQmindImporting] = useState(false);
  const [qmindAvailable, setQmindAvailable] = useState(true);
  const [qmindNotebooks, setQmindNotebooks] = useState<QMindNotebook[]>([]);
  const [qmindSelected, setQmindSelected] = useState<string[]>([]);

  useEffect(() => {
    loadKnowledgeBases();
  }, []);

  const loadKnowledgeBases = async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/knowledge-bases');
      setKnowledgeBases(data || []);
    } catch (error) {
      console.error('Failed to load knowledge bases:', error);
      toast.error(t('k_92701373f4'));
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!showDeleteDialog) return;
    try {
      await client.delete(`/knowledge-bases/${showDeleteDialog.id}`);
      toast.success(t('k_a59c4fef05', { p0: showDeleteDialog.name }));
      setShowDeleteDialog(null);
      loadKnowledgeBases();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || t('k_acf0664a54'));
    }
  };

  // 「测试连接」：按 provider 实调一次连通性自检（百炼=带凭据真打一次检索，qmind=探测 notebook 可达）。
  // 成功/失败原样显示，失败原因不含凭据（服务端已保证）；不做“看起来通过了”的静默兜底。
  const [probingId, setProbingId] = useState<number | null>(null);
  const handleProbe = async (kb: KnowledgeBase) => {
    setProbingId(kb.id);
    try {
      const { data } = await client.post(`/knowledge-bases/${kb.id}/probe`);
      if (data?.ok) toast.success(t('k_172557e8b8', { p0: kb.name }));
      else toast.error(tr('k_8446a4e37d', { p0: kb.name, p1: data?.reason || tr('k_5adfee075a') }));
    } catch (error: any) {
      toast.error(error.response?.data?.detail || t('k_f72128a3f6'));
    } finally {
      setProbingId(null);
    }
  };

  const handleSync = async (kb: KnowledgeBase) => {
    try {
      await client.post(`/knowledge-bases/${kb.id}/sync`);
      toast.success(t('k_871db6a31f', { p0: kb.name }));
      loadKnowledgeBases();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || t('k_d61036e7f5'));
    }
  };

  const getKBTypeConfig = (type: string) => {
    return KB_TYPES.find(t => t.value === type) || KB_TYPES[0];
  };

  const openQmindDialog = async () => {
    setShowQmindDialog(true);
    setQmindLoading(true);
    setQmindSelected([]);
    try {
      const { data } = await client.get('/knowledge-bases/qmind/notebooks');
      setQmindAvailable(!!data?.available);
      setQmindNotebooks(data?.notebooks || []);
      // 默认勾选尚未导入的 notebook
      setQmindSelected((data?.notebooks || [])
        .filter((n: QMindNotebook) => !n.imported)
        .map((n: QMindNotebook) => n.notebook_id));
    } catch (error: any) {
      toast.error(error.response?.data?.detail || t('k_4b07af4c09'));
      setQmindAvailable(false);
      setQmindNotebooks([]);
    } finally {
      setQmindLoading(false);
    }
  };

  const toggleQmindNotebook = (id: string) => {
    setQmindSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const importQmind = async () => {
    if (qmindSelected.length === 0) {
      toast.error(t('k_442e45a4d9'));
      return;
    }
    setQmindImporting(true);
    try {
      const { data } = await client.post('/knowledge-bases/qmind/import', {
        notebook_ids: qmindSelected,
      });
      toast.success(data?.message || t('k_b6d16a8173'));
      setShowQmindDialog(false);
      loadKnowledgeBases();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || t('k_fddcd7c6e4'));
    } finally {
      setQmindImporting(false);
    }
  };

  return (
    <div className="p-6 w-full">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">{t("k_053fc4ecbf")}</h1>
          <p className="text-muted-foreground mt-1">
            {t('k_50f15db60d')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={openQmindDialog}>
            <Sparkles className="h-4 w-4 mr-2" />
            {t('k_895970ee5d')}
          </Button>
          <Button onClick={() => setShowCreateDialog(true)}>
            <Plus className="h-4 w-4 mr-2" />
            {t('k_f93ee39a94')}
          </Button>
        </div>
      </div>

      {/* Knowledge Base List */}
      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner size={32} />
        </div>
      ) : knowledgeBases.length === 0 ? (
        <div className="border rounded-lg p-12 text-center">
          <Database className="h-12 w-12 mx-auto mb-4 text-muted-foreground" />
          <h3 className="text-lg font-medium mb-2">{t("k_1674dc8d7c")}</h3>
          <p className="text-muted-foreground mb-4">
            {t('k_001d6361e3')}
          </p>
          <Button onClick={() => setShowCreateDialog(true)}>
            <Plus className="h-4 w-4 mr-2" />
            {t('k_8b78e23d5e')}
          </Button>
        </div>
      ) : (
        <div className="border rounded-lg overflow-hidden">
          <p className="p-3 text-sm text-muted-foreground">{t("k_1f06c0cd08")}</p>
          <table className="w-full">
            <thead className="bg-muted/50">
              <tr>
                <th className="text-left p-3 font-medium">{t("k_1be7ae4fc2")}</th>
                <th className="text-left p-3 font-medium">{t("k_e4e46c7235")}</th>
                <th className="text-left p-3 font-medium">{t("k_62e951a692")}</th>
                <th className="text-left p-3 font-medium">{t("k_f1da3be14b")}</th>
                <th className="text-left p-3 font-medium">{t("k_d4845b4b39")}</th>
                <th className="text-right p-3 font-medium">{t("k_f3ea6d345e")}</th>
              </tr>
            </thead>
            <tbody>
              {knowledgeBases.map((kb) => {
                const typeConfig = getKBTypeConfig(kb.kb_type);
                const TypeIcon = typeConfig.icon;
                return (
                  <tr key={kb.id} className="border-t hover:bg-muted/30">
                    <td className="p-3">
                      <div className="flex items-center gap-2">
                        <TypeIcon className="h-4 w-4 text-muted-foreground" />
                        <div>
                          <div className="font-medium">{kb.name}</div>
                          {kb.description && (
                            <div className="text-xs text-muted-foreground truncate max-w-[200px]">
                              {kb.description}
                            </div>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className="p-3">
                      <Badge className={typeConfig.color}>
                        {typeConfig.label}
                      </Badge>
                      {kb.retrieval && kb.retrieval.readable === false && (
                        // 未接入检索通道的库照常列出并给出服务端原因原文：藏起来会被读成“库没了”，
                        // 只标灰不放原因又会被读成“能查”（no-silent-degradation）。
                        <div className="mt-1 text-xs text-destructive" title={kb.retrieval.reason || ''}>
                          {t('k_3a125a88b6')}
                        </div>
                      )}
                    </td>
                    <td className="p-3">
                      <Badge variant={kb.status === 'active' ? 'default' : kb.status === 'error' ? 'destructive' : 'secondary'}>
                        {kb.status === 'active' ? t('k_fd6e80f1e0') : kb.status === 'error' ? t('k_c195df6308') : t('k_5c56a88945')}
                      </Badge>
                    </td>
                    <td className="p-3 text-muted-foreground">
                      {kb.document_count.toLocaleString()}
                    </td>
                    <td className="p-3 text-muted-foreground text-sm">
                      {kb.last_sync_at
                        ? new Date(kb.last_sync_at).toLocaleString('zh-CN')
                        : t('k_9109822ef6')}
                    </td>
                    <td className="p-3 text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleProbe(kb)}
                          disabled={probingId === kb.id}
                          title={kb.retrieval && kb.retrieval.readable === false
                            ? (kb.retrieval.reason || t('k_c48dda7ab6'))
                            : t('k_10b7d8eccc')}
                        >
                          <Plug className={`h-4 w-4 ${probingId === kb.id ? 'animate-pulse' : ''}`} />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleSync(kb)}
                          title={t("k_e88ab5ba61")}
                        >
                          <RefreshCw className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setEditingKB(kb)}
                          title={t("k_a7f814c0a4")}
                        >
                          <Edit2 className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setShowDeleteDialog(kb)}
                          title={t("k_3755f56f2f")}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Create Dialog */}
      <CreateKnowledgeBaseDialog
        open={showCreateDialog}
        onClose={() => setShowCreateDialog(false)}
        onCreated={() => {
          setShowCreateDialog(false);
          loadKnowledgeBases();
        }}
      />

      {/* Edit Dialog */}
      {editingKB && (
        <EditKnowledgeBaseDialog
          knowledgeBase={editingKB}
          onClose={() => setEditingKB(null)}
          onSaved={() => {
            setEditingKB(null);
            loadKnowledgeBases();
          }}
        />
      )}

      {/* Delete Confirmation */}
      <Dialog open={!!showDeleteDialog} onOpenChange={() => setShowDeleteDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("k_3c06abe116")}</DialogTitle>
            <DialogDescription>
              {t('k_5a50570066', { p0: showDeleteDialog?.name })}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDeleteDialog(null)}>
              {t('k_4d0b4688c7')}
            </Button>
            <Button variant="destructive" onClick={handleDelete}>
              {t('k_3755f56f2f')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Qoder QMind Notebook Import Dialog */}
      <Dialog open={showQmindDialog} onOpenChange={(v) => { if (!v) setShowQmindDialog(false); }}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t("k_8024647e78")}</DialogTitle>
            <DialogDescription>
              {t('k_f2cc3a28b1')}
            </DialogDescription>
          </DialogHeader>

          {qmindLoading ? (
            <div className="flex justify-center py-12">
              <Spinner size={32} />
            </div>
          ) : !qmindAvailable ? (
            <div className="border rounded-lg p-8 text-center">
              <Sparkles className="h-10 w-10 mx-auto mb-3 text-muted-foreground" />
              <h3 className="text-base font-medium mb-1">{t("k_ee3dbb5ab0")}</h3>
              <p className="text-sm text-muted-foreground">
                {tr('k_644d3b6064')}{' '}<code>qmind</code> CLI（<code>qmind login</code>{tr('k_8d520d2e88')}{' '}</p>
            </div>
          ) : qmindNotebooks.length === 0 ? (
            <div className="border rounded-lg p-8 text-center">
              <p className="text-sm text-muted-foreground">{t("k_43f85f89b4")}</p>
            </div>
          ) : (
            <div className="space-y-2 max-h-[50vh] overflow-y-auto py-2">
              {qmindNotebooks.map((nb) => {
                const checked = qmindSelected.includes(nb.notebook_id);
                return (
                  <label
                    key={nb.notebook_id}
                    className={`flex items-start gap-3 p-3 border rounded-lg cursor-pointer transition-colors ${
                      checked ? 'border-primary bg-primary/5' : 'hover:border-muted-foreground/50'
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={checked}
                      onChange={() => toggleQmindNotebook(nb.notebook_id)}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{nb.title}</span>
                        {nb.imported && <Badge variant="secondary">{t("k_5c5b7cfb12")}</Badge>}
                      </div>
                      {nb.description && (
                        <div className="text-xs text-muted-foreground truncate">{nb.description}</div>
                      )}
                      <div className="text-xs text-muted-foreground/70 font-mono mt-0.5">{nb.notebook_id}</div>
                    </div>
                  </label>
                );
              })}
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => setShowQmindDialog(false)}>{t("k_4d0b4688c7")}</Button>
            <Button
              onClick={importQmind}
              disabled={qmindLoading || !qmindAvailable || qmindSelected.length === 0 || qmindImporting}
            >
              {qmindImporting ? t('k_763476f8dd') : t('k_ef0819b67f', { p0: qmindSelected.length })}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Create Knowledge Base Dialog ───────────────────────────────────

function CreateKnowledgeBaseDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { t } = useTranslation('ui');
  const [step, setStep] = useState<'type' | 'config'>('type');
  const [form, setForm] = useState({
    name: '',
    description: '',
    kb_type: '' as string,
    source_config: {} as Record<string, any>,
  });
  const [saving, setSaving] = useState(false);

  const handleCreate = async () => {
    if (!form.name.trim()) {
      toast.error(t('k_76e4ff8ce9'));
      return;
    }
    if (!form.kb_type) {
      toast.error(t('k_b50b8a0661'));
      return;
    }
    setSaving(true);
    try {
      await client.post('/knowledge-bases', form);
      toast.success(t('k_e3572dd027'));
      onCreated();
      setStep('type');
      setForm({ name: '', description: '', kb_type: '', source_config: {} });
    } catch (error: any) {
      toast.error(error.response?.data?.detail || t('k_a889286a51'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) { onClose(); setStep('type'); } }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("k_f93ee39a94")}</DialogTitle>
          <DialogDescription>
            {t('k_ee19908158')}
          </DialogDescription>
        </DialogHeader>

        {step === 'type' ? (
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label>{t("k_82e28f0dc2")}</Label>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder={t("k_df96327e71")}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("k_412f54dc38")}</Label>
              <Textarea
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder={t("k_08cf9a7631")}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("k_baeb7cc9ba")}</Label>
              <div className="grid grid-cols-3 gap-3">
                {KB_TYPES.map((type) => {
                  const Icon = type.icon;
                  return (
                    <button
                      key={type.value}
                      onClick={() => setForm({ ...form, kb_type: type.value })}
                      className={`p-4 border rounded-lg text-left transition-colors ${
                        form.kb_type === type.value
                          ? 'border-primary bg-primary/5'
                          : 'hover:border-muted-foreground/50'
                      }`}
                    >
                      <Icon className="h-6 w-6 mb-2" />
                      <div className="font-medium text-sm">{type.label}</div>
                      <div className="text-xs text-muted-foreground mt-1">{type.desc}</div>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        ) : (
          <div className="py-4">
            <SourceConfigForm
              kbType={form.kb_type}
              config={form.source_config}
              onChange={(config) => setForm({ ...form, source_config: config })}
            />
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => {
            if (step === 'config') {
              setStep('type');
            } else {
              onClose();
            }
          }}>
            {step === 'config' ? t('k_75ef1241c0') : t('k_4d0b4688c7')}
          </Button>
          {step === 'type' ? (
            <Button
              onClick={() => {
                if (!form.kb_type) {
                  toast.error(t('k_b50b8a0661'));
                  return;
                }
                setStep('config');
              }}
              disabled={!form.kb_type}
            >
              {t('k_38ce27d846')}
            </Button>
          ) : (
            <Button onClick={handleCreate} disabled={saving}>
              {saving ? t('k_b26107b6c5') : t('k_fcbd093292')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Source Config Form ─────────────────────────────────────────────

function SourceConfigForm({
  kbType,
  config,
  onChange,
}: {
  kbType: string;
  config: Record<string, any>;
  onChange: (config: Record<string, any>) => void;
}) {
  const { t } = useTranslation('ui');
  if (kbType === 'qmind') {
    return (
      <div className="space-y-4">
        <div className="p-4 bg-indigo-50 rounded-lg">
          <p className="text-sm text-indigo-800">
            {tr('k_fa857b2ea5')}{' '}<code>qmind</code> {t('k_80972f94c1')}
          </p>
        </div>
        <div className="space-y-2">
          <Label>Notebook ID</Label>
          <Input
            value={config.notebook_id || ''}
            readOnly
            className="font-mono bg-muted/40"
            placeholder={t("k_9fca6dc36a")}
          />
          <p className="text-xs text-muted-foreground">{t("k_a8dcab3794")}</p>
        </div>
        {config.org_id && (
          <div className="space-y-2">
            <Label>{t("k_0f322223f6")}</Label>
            <Input value={config.org_id} readOnly className="font-mono bg-muted/40" />
          </div>
        )}
        <div className="space-y-2">
          <Label>{t('k_d909d150bf')}</Label>
          <Input
            type="number"
            value={config.top_k || '5'}
            onChange={(e) => onChange({ ...config, top_k: parseInt(e.target.value) || 5 })}
            placeholder="5"
          />
        </div>
      </div>
    );
  }

  if (kbType === 'local') {
    return (
      <div className="space-y-4">
        <div className="p-4 bg-blue-50 rounded-lg">
          <p className="text-sm text-blue-800">
            <span className="font-medium">{t("k_43b839d78e")}</span>{t('k_9d9dd7f490')}
          </p>
        </div>
        <div className="space-y-2">
          <Label>{t("k_d9dea994c9")}</Label>
          <Input
            value={config.directory_path || ''}
            onChange={(e) => onChange({ ...config, directory_path: e.target.value })}
            placeholder="/data/knowledge/docs"
          />
        </div>
        <div className="space-y-2">
          <Label>{t("k_01b2ab2a85")}</Label>
          <Input
            value={config.file_extensions || ''}
            onChange={(e) => onChange({ ...config, file_extensions: e.target.value })}
            placeholder=".md,.txt,.pdf,.docx"
          />
          <p className="text-xs text-muted-foreground">{t("k_db81f2fe02")}</p>
        </div>
        <div className="space-y-2">
          <Label>{t("k_793b43b660")}</Label>
          <Select
            value={config.recursive ? 'true' : 'false'}
            onValueChange={(v) => onChange({ ...config, recursive: v === 'true' })}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="true">{t("k_30160a21b9")}</SelectItem>
              <SelectItem value="false">{t("k_8bf5c10ad9")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
    );
  }

  if (kbType === 'vector_db') {
    return (
      <div className="space-y-4">
        <div className="p-4 bg-green-50 rounded-lg">
          <p className="text-sm text-green-800">
            <span className="font-medium">{t("k_6f66e3eb36")}</span>{t('k_674e1172a2')}
          </p>
        </div>
        <div className="space-y-2">
          <Label>{t("k_32faaa6d1f")}</Label>
          <Select
            value={config.db_type || ''}
            onValueChange={(v) => onChange({ ...config, db_type: v })}
          >
            <SelectTrigger>
              <SelectValue placeholder={t("k_6d58cccb14")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="doris">Apache Doris</SelectItem>
              <SelectItem value="milvus">Milvus</SelectItem>
              <SelectItem value="pinecone">Pinecone</SelectItem>
              <SelectItem value="qdrant">Qdrant</SelectItem>
              <SelectItem value="weaviate">Weaviate</SelectItem>
              <SelectItem value="chroma">Chroma</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label>{t("k_c5db6b99f1")}</Label>
            <Input
              value={config.host || ''}
              onChange={(e) => onChange({ ...config, host: e.target.value })}
              placeholder="localhost"
            />
          </div>
          <div className="space-y-2">
            <Label>{t("k_6cbb7335b5")}</Label>
            <Input
              value={config.port || ''}
              onChange={(e) => onChange({ ...config, port: e.target.value })}
              placeholder="9030"
            />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label>{t("k_a1aaf352cb")}</Label>
            <Input
              value={config.username || ''}
              onChange={(e) => onChange({ ...config, username: e.target.value })}
              placeholder="root"
            />
          </div>
          <div className="space-y-2">
            <Label>{t('k_8b3b1dc4d2')}</Label>
            <Input
              value={config.credential_ref || ''}
              onChange={(e) => onChange({ ...config, credential_ref: e.target.value })}
              placeholder={t('k_1ff853833e')}
            />
            {/* 凭据纪律对所有类型一致：密钥不入库、不回显（服务端写入即拒）。
                这里原来摆的是密码框 —— 用户填了只会收到 400，是“配了没用”的陷阱。 */}
            <p className="text-xs text-muted-foreground">
              {t('k_8b565dd6e5')}
            </p>
          </div>
        </div>
        <div className="space-y-2">
          <Label>{t("k_8a98ff0618")}</Label>
          <Input
            value={config.database || ''}
            onChange={(e) => onChange({ ...config, database: e.target.value })}
            placeholder="knowledge_db"
          />
        </div>
        <div className="space-y-2">
          <Label>{t("k_a24a449c29")}</Label>
          <Input
            value={config.collection || ''}
            onChange={(e) => onChange({ ...config, collection: e.target.value })}
            placeholder="documents"
          />
        </div>
      </div>
    );
  }

  if (kbType === 'cloud_rag') {
    // 百炼按「只读外部语料」接入（2026-11-05 拍板）：平台只检索、不向其推送语料。
    // 凭据一律不入库也不回显（真值只从 backend/.env 读），所以这里**不放密钥输入框** ——
    // 摆了框就等于承诺“填了能存”，而服务端写入即拒（400）。
    const vendorSupported = !config.provider || config.provider === 'aliyun';
    const heldSecrets = (config._secrets_configured || []) as string[];
    return (
      <div className="space-y-4">
        <div className="p-4 bg-purple-50 rounded-lg">
          <p className="text-sm text-purple-800">
            {t('k_51099a8074')}
          </p>
        </div>
        <div className="space-y-2">
          <Label>{t("k_bd17c79b28")}</Label>
          <Select
            value={config.provider || ''}
            onValueChange={(v) => onChange({ ...config, provider: v })}
          >
            <SelectTrigger>
              <SelectValue placeholder={t("k_d60227698a")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="aliyun">{t("k_84c0bb2d76")}</SelectItem>
              <SelectItem value="tencent">{t("k_8ff9750345")}</SelectItem>
              <SelectItem value="baidu">{t("k_6d972ddb35")}</SelectItem>
              <SelectItem value="zhipu">{t("k_5bfa2ad8c7")}</SelectItem>
              <SelectItem value="custom">{t("k_04813d8238")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {!vendorSupported && (
          <div className="p-3 rounded-lg bg-destructive/10 text-xs text-destructive">
            {t('k_fb2fd4be2f')}
          </div>
        )}
        {heldSecrets.length > 0 && (
          // 存量旧行可能仍有明文密钥（历史缺陷）：读取已被掩码，这里只告知“曾经存过”，不回显值。
          <div className="p-3 rounded-lg bg-destructive/10 text-xs text-destructive">
            {t('k_9b654813b9', { p0: heldSecrets.join('、') })}</div>
        )}
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label>{t('k_43dd0a88bc')}</Label>
            <Input
              value={config.index_id || ''}
              onChange={(e) => onChange({ ...config, index_id: e.target.value })}
              placeholder={t("k_8762f607c4")}
            />
            {/* 外部系统标识：本系统没有可枚举的清单，按例外允许手输（原因必须写在这里，
                否则用户不知道该填库名还是 ID）；接入 ListIndices 后升级为可搜索选择框。 */}
            <p className="text-xs text-muted-foreground">
              {t('k_7f0cb0a411')}
            </p>
          </div>
          <div className="space-y-2">
            <Label>{t('k_0c54981118')}</Label>
            <Input
              value={config.workspace_id || ''}
              onChange={(e) => onChange({ ...config, workspace_id: e.target.value })}
              placeholder={t("k_4dbe085916")}
            />
            <p className="text-xs text-muted-foreground">{t("k_3407763ec0")}</p>
          </div>
        </div>
        <div className="space-y-2">
          <Label>{t('k_8b3b1dc4d2')}</Label>
          <Input
            value={config.credential_ref || ''}
            onChange={(e) => onChange({ ...config, credential_ref: e.target.value })}
            placeholder={t('k_d3c68fe32e')}
          />
          <p className="text-xs text-muted-foreground">
            {t('k_e0907c6109')}
          </p>
        </div>
        <div className="space-y-2">
          <Label>{t('k_d909d150bf')}</Label>
          <Input
            type="number"
            value={config.top_k || '5'}
            onChange={(e) => onChange({ ...config, top_k: parseInt(e.target.value) || 5 })}
            placeholder="5"
          />
        </div>
      </div>
    );
  }

  return null;
}

// ── Edit Knowledge Base Dialog ─────────────────────────────────────

function EditKnowledgeBaseDialog({
  knowledgeBase,
  onClose,
  onSaved,
}: {
  knowledgeBase: KnowledgeBase;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation('ui');
  const [form, setForm] = useState({
    name: knowledgeBase.name,
    description: knowledgeBase.description || '',
    source_config: knowledgeBase.source_config || {},
  });
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await client.put(`/knowledge-bases/${knowledgeBase.id}`, form);
      toast.success(t('k_9cce0f69f2'));
      onSaved();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || t('k_930442e2f4'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={true} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("k_f8cf989d71")}</DialogTitle>
          <DialogDescription>
            {t('k_e8c0d95c02', { p0: knowledgeBase.name })}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label>{t("k_1be7ae4fc2")}</Label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label>{t("k_412f54dc38")}</Label>
            <Textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </div>
          <SourceConfigForm
            kbType={knowledgeBase.kb_type}
            config={form.source_config}
            onChange={(config) => setForm({ ...form, source_config: config })}
          />
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t('k_4d0b4688c7')}
          </Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? t('k_2a33020e5b') : t('k_fadf24dbc5')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
