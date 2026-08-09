import { useState, useEffect } from 'react';
import {
  Plus, Edit2, Trash2, Database, Server, Cloud, Folder, Search,
  RefreshCw, Settings, X, Check, Link, Unlink,
} from 'lucide-react';
import { toast } from 'sonner';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import client from '../../api/client';

// ── Types ──────────────────────────────────────────────────────────

interface KnowledgeBase {
  id: number;
  name: string;
  description: string;
  kb_type: 'local' | 'vector_db' | 'cloud_rag';
  source_config: Record<string, any>;
  status: 'active' | 'inactive' | 'error';
  document_count: number;
  chunk_count: number;
  last_sync_at: string | null;
  workspace_ids: number[];
  created_at: string;
  updated_at: string;
}

// ── KB Type Config ─────────────────────────────────────────────────

const KB_TYPES = [
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
  const [managingKB, setManagingKB] = useState<KnowledgeBase | null>(null);

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

  return (
    <div className="p-6 w-full">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">知识库管理</h1>
          <p className="text-muted-foreground mt-1">
            管理多个知识库，支持本地目录、向量数据库、云厂商 RAG 等多种数据源
          </p>
        </div>
        <Button onClick={() => setShowCreateDialog(true)}>
          <Plus className="h-4 w-4 mr-2" />
          新建知识库
        </Button>
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
          <table className="w-full">
            <thead className="bg-muted/50">
              <tr>
                <th className="text-left p-3 font-medium">名称</th>
                <th className="text-left p-3 font-medium">类型</th>
                <th className="text-left p-3 font-medium">状态</th>
                <th className="text-left p-3 font-medium">文档数</th>
                <th className="text-left p-3 font-medium">关联工作空间</th>
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
                    </td>
                    <td className="p-3">
                      <Badge variant={kb.status === 'active' ? 'default' : kb.status === 'error' ? 'destructive' : 'secondary'}>
                        {kb.status === 'active' ? '正常' : kb.status === 'error' ? '异常' : '停用'}
                      </Badge>
                    </td>
                    <td className="p-3 text-muted-foreground">
                      {kb.document_count.toLocaleString()}
                    </td>
                    <td className="p-3">
                      {kb.workspace_ids.length > 0 ? (
                        <Badge variant="outline">{kb.workspace_ids.length} 个工作空间</Badge>
                      ) : (
                        <span className="text-muted-foreground text-sm">未关联</span>
                      )}
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
                          onClick={() => handleSync(kb)}
                          title="同步"
                        >
                          <RefreshCw className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setManagingKB(kb)}
                          title="关联工作空间"
                        >
                          <Link className="h-4 w-4" />
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

      {/* Manage Workspace Dialog */}
      {managingKB && (
        <ManageWorkspaceDialog
          knowledgeBase={managingKB}
          onClose={() => setManagingKB(null)}
          onSaved={() => {
            setManagingKB(null);
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
  if (kbType === 'local') {
    return (
      <div className="space-y-4">
        <div className="p-4 bg-blue-50 rounded-lg">
          <p className="text-sm text-blue-800">
            从服务器本地目录读取文档文件，支持 .md, .txt, .pdf, .docx 等格式
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
            连接向量数据库，支持 Doris、Milvus、Pinecone、Qdrant 等
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
            <Label>密码</Label>
            <Input
              type="password"
              value={config.password || ''}
              onChange={(e) => onChange({ ...config, password: e.target.value })}
              placeholder="••••••"
            />
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
    return (
      <div className="space-y-4">
        <div className="p-4 bg-purple-50 rounded-lg">
          <p className="text-sm text-purple-800">
            接入云厂商 RAG 服务，支持阿里云、腾讯云、百度云等
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
              <SelectItem value="aliyun">阿里云百炼</SelectItem>
              <SelectItem value="tencent">腾讯云混元</SelectItem>
              <SelectItem value="baidu">百度千帆</SelectItem>
              <SelectItem value="zhipu">智谱 AI</SelectItem>
              <SelectItem value="custom">自定义 API</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label>API Key</Label>
          <Input
            type="password"
            value={config.api_key || ''}
            onChange={(e) => onChange({ ...config, api_key: e.target.value })}
            placeholder="sk-..."
          />
        </div>
        <div className="space-y-2">
          <Label>App ID / 知识库 ID</Label>
          <Input
            value={config.app_id || ''}
            onChange={(e) => onChange({ ...config, app_id: e.target.value })}
            placeholder="输入应用 ID 或知识库 ID"
          />
        </div>
        {config.provider === 'custom' && (
          <div className="space-y-2">
            <Label>API 端点</Label>
            <Input
              value={config.api_endpoint || ''}
              onChange={(e) => onChange({ ...config, api_endpoint: e.target.value })}
              placeholder="https://api.example.com/v1"
            />
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

// ── Manage Workspace Dialog ────────────────────────────────────────

function ManageWorkspaceDialog({
  knowledgeBase,
  onClose,
  onSaved,
}: {
  knowledgeBase: KnowledgeBase;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [workspaces, setWorkspaces] = useState<any[]>([]);
  const [selectedIds, setSelectedIds] = useState<number[]>(knowledgeBase.workspace_ids || []);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setLoading(true);
    client.get('/workspaces')
      .then(({ data }) => setWorkspaces(data || []))
      .catch(() => toast.error('加载工作空间失败'))
      .finally(() => setLoading(false));
  }, []);

  const toggleWorkspace = (id: number) => {
    setSelectedIds(prev =>
      prev.includes(id) ? prev.filter(v => v !== id) : [...prev, id]
    );
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await client.put(`/knowledge-bases/${knowledgeBase.id}`, {
        workspace_ids: selectedIds,
      });
      toast.success('工作空间关联已更新');
      onSaved();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '更新失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={true} onOpenChange={onClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>关联工作空间</DialogTitle>
          <DialogDescription>
            选择哪些工作空间可以使用 "{knowledgeBase.name}" 知识库
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex justify-center py-8">
            <Spinner size={24} />
          </div>
        ) : (
          <div className="space-y-2 py-4 max-h-[400px] overflow-y-auto">
            {workspaces.map((ws) => (
              <div
                key={ws.id}
                onClick={() => toggleWorkspace(ws.id)}
                className={`flex items-center justify-between p-3 border rounded-lg cursor-pointer transition-colors ${
                  selectedIds.includes(ws.id)
                    ? 'border-primary bg-primary/5'
                    : 'hover:bg-muted/50'
                }`}
              >
                <div className="flex items-center gap-2">
                  <span>{ws.icon}</span>
                  <span className="font-medium">{ws.name}</span>
                </div>
                {selectedIds.includes(ws.id) && (
                  <Check className="h-4 w-4 text-primary" />
                )}
              </div>
            ))}
            {workspaces.length === 0 && (
              <div className="text-center text-muted-foreground py-4">
                暂无工作空间
              </div>
            )}
          </div>
        )}

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
