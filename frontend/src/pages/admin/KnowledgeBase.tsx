import { useState, useEffect } from 'react';
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
    label: 'QMind 知识检索',
    icon: Sparkles,
    desc: '来自 Qoder QMind 笔记本，本地 qmind CLI 按需检索',
    color: 'bg-indigo-100 text-indigo-800',
  },
  {
    value: 'local',
    label: '本地数据目录',
    icon: Folder,
    desc: '从服务器本地目录读取文档文件',
    color: 'bg-blue-100 text-blue-800',
  },
  {
    value: 'vector_db',
    label: '向量数据库',
    icon: Database,
    desc: '连接 Doris、Milvus、Pinecone 等向量库',
    color: 'bg-green-100 text-green-800',
  },
  {
    value: 'cloud_rag',
    label: '云厂商 RAG',
    icon: Cloud,
    desc: '接入阿里云、腾讯云、百度云等 RAG 服务',
    color: 'bg-purple-100 text-purple-800',
  },
];

// ── Main Component ─────────────────────────────────────────────────

export default function KnowledgeBase() {
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
      toast.error('加载知识库失败');
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!showDeleteDialog) return;
    try {
      await client.delete(`/knowledge-bases/${showDeleteDialog.id}`);
      toast.success(`已删除知识库 "${showDeleteDialog.name}"`);
      setShowDeleteDialog(null);
      loadKnowledgeBases();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '删除失败');
    }
  };

  // 「测试连接」：按 provider 实调一次连通性自检（百炼=带凭据真打一次检索，qmind=探测 notebook 可达）。
  // 成功/失败原样显示，失败原因不含凭据（服务端已保证）；不做“看起来通过了”的静默兜底。
  const [probingId, setProbingId] = useState<number | null>(null);
  const handleProbe = async (kb: KnowledgeBase) => {
    setProbingId(kb.id);
    try {
      const { data } = await client.post(`/knowledge-bases/${kb.id}/probe`);
      if (data?.ok) toast.success(`「${kb.name}」连通正常`);
      else toast.error(`「${kb.name}」连不通：${data?.reason || '未返回原因'}`);
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '测试连接未完成');
    } finally {
      setProbingId(null);
    }
  };

  const handleSync = async (kb: KnowledgeBase) => {
    try {
      await client.post(`/knowledge-bases/${kb.id}/sync`);
      toast.success(`正在同步 "${kb.name}"`);
      loadKnowledgeBases();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '同步失败');
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
      toast.error(error.response?.data?.detail || '检索 Qoder 知识库失败');
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
      toast.error('请至少选择一个知识库');
      return;
    }
    setQmindImporting(true);
    try {
      const { data } = await client.post('/knowledge-bases/qmind/import', {
        notebook_ids: qmindSelected,
      });
      toast.success(data?.message || '导入成功');
      setShowQmindDialog(false);
      loadKnowledgeBases();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '导入失败');
    } finally {
      setQmindImporting(false);
    }
  };

  return (
    <div className="p-6 w-full">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">知识库管理</h1>
          <p className="text-muted-foreground mt-1">
            管理多个知识库，支持从 Qoder QMind 检索导入，以及本地目录、向量数据库、云厂商 RAG 等数据源
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={openQmindDialog}>
            <Sparkles className="h-4 w-4 mr-2" />
            从 Qoder 检索知识库
          </Button>
          <Button onClick={() => setShowCreateDialog(true)}>
            <Plus className="h-4 w-4 mr-2" />
            新建知识库
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
          <h3 className="text-lg font-medium mb-2">暂无知识库</h3>
          <p className="text-muted-foreground mb-4">
            创建知识库来管理您的业务知识，支持多种数据源类型
          </p>
          <Button onClick={() => setShowCreateDialog(true)}>
            <Plus className="h-4 w-4 mr-2" />
            创建第一个知识库
          </Button>
        </div>
      ) : (
        <div className="border rounded-lg overflow-hidden">
          <p className="p-3 text-sm text-muted-foreground">知识库使用范围统一在 AS-BOT 中绑定，无需关联工作空间。</p>
          <table className="w-full">
            <thead className="bg-muted/50">
              <tr>
                <th className="text-left p-3 font-medium">名称</th>
                <th className="text-left p-3 font-medium">类型</th>
                <th className="text-left p-3 font-medium">状态</th>
                <th className="text-left p-3 font-medium">文档数</th>
                <th className="text-left p-3 font-medium">最后同步</th>
                <th className="text-right p-3 font-medium">操作</th>
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
                          检索通道未接入
                        </div>
                      )}
                    </td>
                    <td className="p-3">
                      <Badge variant={kb.status === 'active' ? 'default' : kb.status === 'error' ? 'destructive' : 'secondary'}>
                        {kb.status === 'active' ? '正常' : kb.status === 'error' ? '异常' : '停用'}
                      </Badge>
                    </td>
                    <td className="p-3 text-muted-foreground">
                      {kb.document_count.toLocaleString()}
                    </td>
                    <td className="p-3 text-muted-foreground text-sm">
                      {kb.last_sync_at
                        ? new Date(kb.last_sync_at).toLocaleString('zh-CN')
                        : '从未同步'}
                    </td>
                    <td className="p-3 text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleProbe(kb)}
                          disabled={probingId === kb.id}
                          title={kb.retrieval && kb.retrieval.readable === false
                            ? (kb.retrieval.reason || '该库的检索通道本批未接入')
                            : '测试连接'}
                        >
                          <Plug className={`h-4 w-4 ${probingId === kb.id ? 'animate-pulse' : ''}`} />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleSync(kb)}
                          title="同步"
                        >
                          <RefreshCw className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setEditingKB(kb)}
                          title="编辑"
                        >
                          <Edit2 className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setShowDeleteDialog(kb)}
                          title="删除"
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
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除知识库 "{showDeleteDialog?.name}" 吗？此操作不可撤销，
              该知识库下的所有文档和向量索引都将被删除。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDeleteDialog(null)}>
              取消
            </Button>
            <Button variant="destructive" onClick={handleDelete}>
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Qoder QMind Notebook Import Dialog */}
      <Dialog open={showQmindDialog} onOpenChange={(v) => { if (!v) setShowQmindDialog(false); }}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>从 Qoder QMind 检索知识库</DialogTitle>
            <DialogDescription>
              列出当前账号下的 Qoder QMind 笔记本(notebook)，勾选后导入为知识库，可在 AS-BOT 中绑定检索
            </DialogDescription>
          </DialogHeader>

          {qmindLoading ? (
            <div className="flex justify-center py-12">
              <Spinner size={32} />
            </div>
          ) : !qmindAvailable ? (
            <div className="border rounded-lg p-8 text-center">
              <Sparkles className="h-10 w-10 mx-auto mb-3 text-muted-foreground" />
              <h3 className="text-base font-medium mb-1">未能检索到 Qoder 知识库</h3>
              <p className="text-sm text-muted-foreground">
                请确认服务器已安装并登录 <code>qmind</code> CLI（<code>qmind login</code>），稍后重试。
              </p>
            </div>
          ) : qmindNotebooks.length === 0 ? (
            <div className="border rounded-lg p-8 text-center">
              <p className="text-sm text-muted-foreground">当前账号下没有可用的 QMind 笔记本</p>
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
                        {nb.imported && <Badge variant="secondary">已导入</Badge>}
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
            <Button variant="outline" onClick={() => setShowQmindDialog(false)}>取消</Button>
            <Button
              onClick={importQmind}
              disabled={qmindLoading || !qmindAvailable || qmindSelected.length === 0 || qmindImporting}
            >
              {qmindImporting ? '导入中...' : `导入所选 (${qmindSelected.length})`}
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
      toast.error('请输入知识库名称');
      return;
    }
    if (!form.kb_type) {
      toast.error('请选择知识库类型');
      return;
    }
    setSaving(true);
    try {
      await client.post('/knowledge-bases', form);
      toast.success('知识库创建成功');
      onCreated();
      setStep('type');
      setForm({ name: '', description: '', kb_type: '', source_config: {} });
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '创建失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) { onClose(); setStep('type'); } }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>新建知识库</DialogTitle>
          <DialogDescription>
            选择知识库类型并配置数据源
          </DialogDescription>
        </DialogHeader>

        {step === 'type' ? (
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label>知识库名称</Label>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="输入知识库名称"
              />
            </div>
            <div className="space-y-2">
              <Label>描述</Label>
              <Textarea
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="输入知识库描述（可选）"
              />
            </div>
            <div className="space-y-2">
              <Label>选择类型</Label>
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
            {step === 'config' ? '上一步' : '取消'}
          </Button>
          {step === 'type' ? (
            <Button
              onClick={() => {
                if (!form.kb_type) {
                  toast.error('请选择知识库类型');
                  return;
                }
                setStep('config');
              }}
              disabled={!form.kb_type}
            >
              下一步
            </Button>
          ) : (
            <Button onClick={handleCreate} disabled={saving}>
              {saving ? '创建中...' : '创建'}
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
  if (kbType === 'qmind') {
    return (
      <div className="space-y-4">
        <div className="p-4 bg-indigo-50 rounded-lg">
          <p className="text-sm text-indigo-800">
            对应一个 Qoder QMind 笔记本(notebook)，通过本地 <code>qmind</code> CLI 按需检索；
            建议使用“从 Qoder 检索知识库”导入，而非手动新建。
          </p>
        </div>
        <div className="space-y-2">
          <Label>Notebook ID</Label>
          <Input
            value={config.notebook_id || ''}
            readOnly
            className="font-mono bg-muted/40"
            placeholder="由导入自动填充"
          />
          <p className="text-xs text-muted-foreground">Qoder QMind 笔记本唯一标识，导入时自动写入，请勿手动修改</p>
        </div>
        {config.org_id && (
          <div className="space-y-2">
            <Label>组织 ID</Label>
            <Input value={config.org_id} readOnly className="font-mono bg-muted/40" />
          </div>
        )}
        <div className="space-y-2">
          <Label>检索数量 (Top K)</Label>
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
            <span className="font-medium">本地目录：</span>支持 .md/.txt/.pdf/.docx 等格式。
            检索通道本批未接入：登记在这里不会被 AS-BOT 检索到，也不要绑进 AS-BOT
            （绑定校验会直接拒绝并说明原因）。
          </p>
        </div>
        <div className="space-y-2">
          <Label>目录路径</Label>
          <Input
            value={config.directory_path || ''}
            onChange={(e) => onChange({ ...config, directory_path: e.target.value })}
            placeholder="/data/knowledge/docs"
          />
        </div>
        <div className="space-y-2">
          <Label>文件类型过滤</Label>
          <Input
            value={config.file_extensions || ''}
            onChange={(e) => onChange({ ...config, file_extensions: e.target.value })}
            placeholder=".md,.txt,.pdf,.docx"
          />
          <p className="text-xs text-muted-foreground">多个扩展名用逗号分隔，留空表示所有文件</p>
        </div>
        <div className="space-y-2">
          <Label>递归扫描子目录</Label>
          <Select
            value={config.recursive ? 'true' : 'false'}
            onValueChange={(v) => onChange({ ...config, recursive: v === 'true' })}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="true">是</SelectItem>
              <SelectItem value="false">否</SelectItem>
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
            <span className="font-medium">向量数据库：</span>可登记 Doris、Milvus、Pinecone、Qdrant 等连接信息。
            检索通道本批未接入：登记在这里不会被 AS-BOT 检索到，也不要绑进 AS-BOT。
          </p>
        </div>
        <div className="space-y-2">
          <Label>数据库类型</Label>
          <Select
            value={config.db_type || ''}
            onValueChange={(v) => onChange({ ...config, db_type: v })}
          >
            <SelectTrigger>
              <SelectValue placeholder="选择数据库类型" />
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
            <Label>主机地址</Label>
            <Input
              value={config.host || ''}
              onChange={(e) => onChange({ ...config, host: e.target.value })}
              placeholder="localhost"
            />
          </div>
          <div className="space-y-2">
            <Label>端口</Label>
            <Input
              value={config.port || ''}
              onChange={(e) => onChange({ ...config, port: e.target.value })}
              placeholder="9030"
            />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label>用户名</Label>
            <Input
              value={config.username || ''}
              onChange={(e) => onChange({ ...config, username: e.target.value })}
              placeholder="root"
            />
          </div>
          <div className="space-y-2">
            <Label>凭据引用名（credential_ref）</Label>
            <Input
              value={config.credential_ref || ''}
              onChange={(e) => onChange({ ...config, credential_ref: e.target.value })}
              placeholder="环境变量名，如 VECTOR_DB_KB_PASSWORD"
            />
            {/* 凭据纪律对所有类型一致：密钥不入库、不回显（服务端写入即拒）。
                这里原来摆的是密码框 —— 用户填了只会收到 400，是“配了没用”的陷阱。 */}
            <p className="text-xs text-muted-foreground">
              只填环境变量名；密码真值写 backend/.env，本页不保存也不回显密钥。
            </p>
          </div>
        </div>
        <div className="space-y-2">
          <Label>数据库名</Label>
          <Input
            value={config.database || ''}
            onChange={(e) => onChange({ ...config, database: e.target.value })}
            placeholder="knowledge_db"
          />
        </div>
        <div className="space-y-2">
          <Label>集合/表名</Label>
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
            阿里云百炼（Model Studio）知识库：资料由企业在百炼侧维护，本平台只检索、不写入。
            其他云厂商本批未接入检索通道，绑给 AS-BOT 会被直接拒绝（不会“配了没反应”）。
          </p>
        </div>
        <div className="space-y-2">
          <Label>云厂商</Label>
          <Select
            value={config.provider || ''}
            onValueChange={(v) => onChange({ ...config, provider: v })}
          >
            <SelectTrigger>
              <SelectValue placeholder="选择云厂商" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="aliyun">阿里云百炼（已接入，只读检索）</SelectItem>
              <SelectItem value="tencent">腾讯云混元（本批未接入）</SelectItem>
              <SelectItem value="baidu">百度千帆（本批未接入）</SelectItem>
              <SelectItem value="zhipu">智谱 AI（本批未接入）</SelectItem>
              <SelectItem value="custom">自定义 API（本批未接入）</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {!vendorSupported && (
          <div className="p-3 rounded-lg bg-destructive/10 text-xs text-destructive">
            该厂商的检索通道本批未接入：保存后仍不能被 AS-BOT 检索使用。要么改选阿里云百炼，
            要么先不要把它绑进 AS-BOT。
          </div>
        )}
        {heldSecrets.length > 0 && (
          // 存量旧行可能仍有明文密钥（历史缺陷）：读取已被掩码，这里只告知“曾经存过”，不回显值。
          <div className="p-3 rounded-lg bg-destructive/10 text-xs text-destructive">
            检测到历史配置里存有凭据字段（{heldSecrets.join('、')}）。平台不再保存也不回显密钥：
            请把真值写入 backend/.env 的 DASHSCOPE_API_KEY，并清理该字段。
          </div>
        )}
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label>知识库 ID（index_id）</Label>
            <Input
              value={config.index_id || ''}
              onChange={(e) => onChange({ ...config, index_id: e.target.value })}
              placeholder="百炼控制台的知识库 ID"
            />
            {/* 外部系统标识：本系统没有可枚举的清单，按例外允许手输（原因必须写在这里，
                否则用户不知道该填库名还是 ID）；接入 ListIndices 后升级为可搜索选择框。 */}
            <p className="text-xs text-muted-foreground">
              百炼侧标识，本平台无法枚举候选，需从百炼控制台复制。
            </p>
          </div>
          <div className="space-y-2">
            <Label>业务空间 ID（workspace_id）</Label>
            <Input
              value={config.workspace_id || ''}
              onChange={(e) => onChange({ ...config, workspace_id: e.target.value })}
              placeholder="可选，百炼业务空间 ID"
            />
            <p className="text-xs text-muted-foreground">同样是外部标识，按需在百炼控制台查看。</p>
          </div>
        </div>
        <div className="space-y-2">
          <Label>凭据引用名（credential_ref）</Label>
          <Input
            value={config.credential_ref || ''}
            onChange={(e) => onChange({ ...config, credential_ref: e.target.value })}
            placeholder="留空则使用 DASHSCOPE_API_KEY"
          />
          <p className="text-xs text-muted-foreground">
            只填环境变量名（如 BAILIAN_KB_A_KEY），不要在这里填密钥；密钥只从 backend/.env 读取。
          </p>
        </div>
        <div className="space-y-2">
          <Label>检索数量 (Top K)</Label>
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
      toast.success('知识库更新成功');
      onSaved();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '更新失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={true} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>编辑知识库</DialogTitle>
          <DialogDescription>
            修改 "{knowledgeBase.name}" 的配置
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label>名称</Label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label>描述</Label>
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
            取消
          </Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? '保存中...' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
