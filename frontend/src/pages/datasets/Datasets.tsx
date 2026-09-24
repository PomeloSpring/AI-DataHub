import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Plus, Layers, Database, Code2, Trash2, Eye, Boxes, Loader2, Check,
} from 'lucide-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import client from '../../api/client';
import { hasPerm } from '../../stores/permissionStore';

interface Dataset {
  id: number;
  name: string;
  description: string;
  source_type: 'semantic' | 'sql';
  object_key: string;
  datasource_id: number;
  visibility: 'private' | 'workspace' | 'public';
  owner_id: number;
  field_count: number;
  references: number;
  updated_at: string;
}

const VIS_LABELS: Record<string, string> = { private: '私有', workspace: '工作空间', public: '公开' };

export default function Datasets() {
  const navigate = useNavigate();
  const [items, setItems] = useState<Dataset[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const canManage = hasPerm('dataset:manage');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/datasets/', { params: { keyword } });
      setItems(data.items || []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [keyword]);

  useEffect(() => { load(); }, [load]);

  const handleDelete = async (ds: Dataset) => {
    if (!confirm(`确定删除数据集 "${ds.name}"？`)) return;
    try {
      await client.delete(`/datasets/${ds.id}`);
      toast.success('已删除');
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  return (
    <div className="h-full overflow-auto p-1">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Layers className="h-6 w-6" /> 数据集
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            BI 治理建模层：语义对象 / SQL 双来源，看板、报表与 Chat 共用同一口径
          </p>
        </div>
        {canManage && (
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4 mr-1" /> 新建数据集
          </Button>
        )}
      </div>

      <div className="flex gap-2 mb-4">
        <Input
          placeholder="搜索数据集名称 / 描述 / 对象…"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          className="max-w-xs"
        />
      </div>

      {loading ? (
        <div className="flex justify-center py-12 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground border rounded-lg">
          暂无数据集{canManage ? '，点击右上角新建' : ''}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {items.map(ds => (
            <div key={ds.id} className="border rounded-lg p-4 hover:bg-muted/30 transition-colors">
              <div className="flex items-start justify-between gap-2">
                <button className="text-left min-w-0" onClick={() => navigate(`/data/datasets/${ds.id}`)}>
                  <div className="font-medium truncate">{ds.name}</div>
                  {ds.description && (
                    <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{ds.description}</p>
                  )}
                </button>
                <Badge variant="outline" className="shrink-0 flex items-center gap-1 text-[10px]">
                  {ds.source_type === 'semantic'
                    ? <><Boxes className="h-3 w-3" /> 语义对象</>
                    : <><Code2 className="h-3 w-3" /> SQL</>}
                </Badge>
              </div>
              <div className="flex items-center gap-2 mt-3 text-xs text-muted-foreground flex-wrap">
                {ds.source_type === 'semantic' && (
                  <span className="flex items-center gap-1"><Database className="h-3 w-3" />{ds.object_key}</span>
                )}
                <span>{ds.field_count} 字段</span>
                <span>被 {ds.references} 图表引用</span>
                <Badge variant="secondary" className="text-[10px]">{VIS_LABELS[ds.visibility] || ds.visibility}</Badge>
              </div>
              <div className="flex justify-end gap-1 mt-2">
                <Button variant="ghost" size="sm" onClick={() => navigate(`/data/datasets/${ds.id}`)}>
                  <Eye className="h-3.5 w-3.5" />
                </Button>
                {canManage && (
                  <Button variant="ghost" size="sm" onClick={() => handleDelete(ds)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <CreateDatasetDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(id) => { setCreateOpen(false); navigate(`/data/datasets/${id}`); }}
      />
    </div>
  );
}

// ── Create Dialog ─────────────────────────────────────────────────────

function CreateDatasetDialog({
  open, onClose, onCreated,
}: { open: boolean; onClose: () => void; onCreated: (id: number) => void }) {
  const [form, setForm] = useState({
    name: '', description: '', source_type: 'semantic',
    object_key: '', datasource_id: 0, sql_query: '', visibility: 'workspace',
  });
  const [datasources, setDatasources] = useState<any[]>([]);
  const [objects, setObjects] = useState<any[]>([]);
  const [modelId, setModelId] = useState('');
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [inferredFields, setInferredFields] = useState<any[]>([]);

  useEffect(() => {
    if (!open) return;
    client.get('/datasources/').then(({ data }) => {
      setDatasources(Array.isArray(data) ? data : (data?.items || []));
    }).catch(() => setDatasources([]));
  }, [open]);

  // 选数据源 → 自动取其 active 本体模型的 objects 供选择
  useEffect(() => {
    if (!form.datasource_id) { setObjects([]); setModelId(''); return; }
    client.get('/catalog/ontology/models', { params: { datasource_id: form.datasource_id } })
      .then(({ data }) => {
        const ms = (data.items || []).filter((m: any) => m.status === 'active');
        setModelId(ms.length ? String(ms[0].id) : '');
      })
      .catch(() => { setObjects([]); setModelId(''); });
  }, [form.datasource_id]);

  useEffect(() => {
    if (!modelId) { setObjects([]); return; }
    client.get(`/catalog/ontology/models/${modelId}`)
      .then(({ data }) => {
        try {
          const doc = JSON.parse(data.json_content || '{}');
          setObjects((doc.objects || []).map((o: any) => ({
            key: o.key, display_name: o.display_name || o.key,
          })));
        } catch { setObjects([]); }
      })
      .catch(() => setObjects([]));
  }, [modelId]);

  const validateSql = async () => {
    if (!form.sql_query.trim()) { toast.error('请先输入 SQL'); return; }
    setValidating(true);
    try {
      const { data } = await client.post('/datasets/validate-sql', {
        sql: form.sql_query, datasource_id: Number(form.datasource_id) || 0,
      });
      setInferredFields(data.fields || []);
      toast.success(`校验通过，推断 ${data.fields?.length || 0} 个字段`);
    } catch (e: any) {
      setInferredFields([]);
      toast.error(e.response?.data?.detail || 'SQL 校验失败');
    } finally {
      setValidating(false);
    }
  };

  const handleSave = async () => {
    if (!form.name.trim()) { toast.error('名称必填'); return; }
    if (form.source_type === 'semantic' && !form.object_key) { toast.error('请选择本体对象'); return; }
    if (form.source_type === 'sql' && (!form.sql_query.trim() || inferredFields.length === 0)) {
      toast.error('请先「校验并推断字段」'); return;
    }
    setSaving(true);
    try {
      const { data } = await client.post('/datasets/', {
        ...form,
        datasource_id: Number(form.datasource_id) || 0,
        field_config: form.source_type === 'sql' ? inferredFields : undefined,
      });
      toast.success('数据集已创建');
      onCreated(data.id);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '创建失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>新建数据集</DialogTitle>
          <DialogDescription>语义对象数据集继承本体口径；SQL 数据集经统一治理入口校验与取数</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>名称 *</Label>
              <Input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="如：案件分析数据集" />
            </div>
            <div className="space-y-1.5">
              <Label>可见性</Label>
              <Select value={form.visibility} onValueChange={v => setForm({ ...form, visibility: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="private">私有(仅自己)</SelectItem>
                  <SelectItem value="workspace">工作空间</SelectItem>
                  <SelectItem value="public">公开</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>描述</Label>
            <Textarea rows={2} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} />
          </div>

          <Tabs value={form.source_type} onValueChange={v => setForm({ ...form, source_type: v })}>
            <TabsList className="grid grid-cols-2 w-64">
              <TabsTrigger value="semantic"><Boxes className="h-3.5 w-3.5 mr-1" /> 语义对象</TabsTrigger>
              <TabsTrigger value="sql"><Code2 className="h-3.5 w-3.5 mr-1" /> SQL</TabsTrigger>
            </TabsList>

            <TabsContent value="semantic" className="space-y-3 pt-3">
              <div className="space-y-1.5">
                <Label>数据源 *</Label>
                <Select
                  value={form.datasource_id ? String(form.datasource_id) : ''}
                  onValueChange={v => setForm({ ...form, datasource_id: Number(v), object_key: '' })}
                >
                  <SelectTrigger><SelectValue placeholder="选择数据源" /></SelectTrigger>
                  <SelectContent>
                    {datasources.map((d: any) => <SelectItem key={d.id} value={String(d.id)}>{d.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>本体对象 *</Label>
                <Select
                  value={form.object_key}
                  onValueChange={v => setForm({ ...form, object_key: v })}
                  disabled={!form.datasource_id || objects.length === 0}
                >
                  <SelectTrigger><SelectValue placeholder={objects.length ? '选择对象' : '该数据源暂无 active 本体模型'} /></SelectTrigger>
                  <SelectContent>
                    {objects.map(o => <SelectItem key={o.key} value={o.key}>{o.display_name} ({o.key})</SelectItem>)}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">字段(指标/维度)自动继承该对象在语义层的定义，本体更新后同步</p>
              </div>
            </TabsContent>

            <TabsContent value="sql" className="space-y-3 pt-3">
              <div className="space-y-1.5">
                <Label>执行数据源</Label>
                <Select
                  value={form.datasource_id ? String(form.datasource_id) : '0'}
                  onValueChange={v => setForm({ ...form, datasource_id: Number(v) })}
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="0">默认引擎</SelectItem>
                    {datasources.map((d: any) => <SelectItem key={d.id} value={String(d.id)}>{d.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <Label>SQL (仅 SELECT/WITH) *</Label>
                  <Button size="sm" variant="outline" onClick={validateSql} disabled={validating}>
                    {validating ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <Check className="h-3.5 w-3.5 mr-1" />}
                    校验并推断字段
                  </Button>
                </div>
                <Textarea
                  rows={6} className="font-mono text-xs"
                  value={form.sql_query}
                  onChange={e => { setForm({ ...form, sql_query: e.target.value }); setInferredFields([]); }}
                  placeholder="SELECT region, status, COUNT(*) AS cnt FROM t_case_records GROUP BY region, status"
                />
              </div>
              {inferredFields.length > 0 && (
                <div className="border rounded-lg p-2.5 text-xs space-y-1">
                  <div className="font-medium mb-1">推断字段（详情页可调整角色/别名）</div>
                  <div className="flex flex-wrap gap-1.5">
                    {inferredFields.map((f: any) => (
                      <Badge key={f.field} variant="outline" className="text-[10px]">
                        {f.field} · {f.role === 'measure' ? '度量' : '维度'}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </TabsContent>
          </Tabs>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={handleSave} disabled={saving}>{saving ? '创建中…' : '创建'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
