import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import client from '@/api/client';
import {
  ontologyApi,
  generateOntologyDraft,
  type OntologyModel,
  type OntologyModelSummary,
  type OntologyStatus,
} from '@/api/ontology';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { toast } from 'sonner';
import {
  Boxes, Sparkles, Save, CheckCircle2, Archive, RefreshCw, Loader2, Trash2,
} from 'lucide-react';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import { yaml } from '@codemirror/lang-yaml';
import { markdown } from '@codemirror/lang-markdown';

interface Datasource {
  id: number;
  name: string;
  db_type?: string;
}

const STATUS_META: Record<OntologyStatus, { label: string; cls: string }> = {
  draft: { label: '草案', cls: 'bg-amber-500/10 text-amber-500 border-amber-500/20' },
  active: { label: '激活', cls: 'bg-green-500/10 text-green-500 border-green-500/20' },
  archived: { label: '已归档', cls: 'bg-gray-500/10 text-gray-500 border-gray-500/20' },
};

/** 从 JSON 内容解析对象列表（预览用，容错） */
function parseObjects(jsonContent: string): any[] {
  try {
    const doc = JSON.parse(jsonContent);
    return Array.isArray(doc?.objects) ? doc.objects : [];
  } catch {
    return [];
  }
}

export default function OntologyModeling() {
  // ── 数据源 ──
  const [datasources, setDatasources] = useState<Datasource[]>([]);
  const [datasourceId, setDatasourceId] = useState<number>(0);

  // ── 模型列表 ──
  const [models, setModels] = useState<OntologyModelSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [model, setModel] = useState<OntologyModel | null>(null);

  // ── 编辑器 ──
  const [jsonDraft, setJsonDraft] = useState('');
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  // ── 生成 SSE ──
  const [generating, setGenerating] = useState(false);
  const [progressLogs, setProgressLogs] = useState<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  // ── 弹窗 ──
  const [activateOpen, setActivateOpen] = useState(false);
  const [activating, setActivating] = useState(false);

  const loadDatasources = useCallback(async () => {
    try {
      const { data } = await client.get('/datasources');
      setDatasources(Array.isArray(data) ? data : []);
    } catch {
      // ignore
    }
  }, []);

  const loadModels = useCallback(async (dsId?: number) => {
    try {
      const { data } = await ontologyApi.list(dsId);
      setModels(data.items || []);
    } catch {
      setModels([]);
    }
  }, []);

  useEffect(() => {
    loadDatasources();
    loadModels();
  }, [loadDatasources, loadModels]);

  useEffect(() => {
    if (datasourceId) loadModels(datasourceId);
  }, [datasourceId, loadModels]);

  const loadModel = useCallback(async (id: number) => {
    try {
      const { data } = await ontologyApi.get(id);
      setModel(data);
      setSelectedId(id);
      setJsonDraft(data.json_content || '');
      setDirty(false);
    } catch {
      toast.error('加载模型失败');
    }
  }, []);

  // ── 生成草案（SSE） ──
  const handleGenerate = () => {
    if (!datasourceId) {
      toast.warning('请先选择数据源');
      return;
    }
    setGenerating(true);
    setProgressLogs([]);
    abortRef.current = generateOntologyDraft(datasourceId, (event, data) => {
      if (event === 'progress') {
        setProgressLogs((prev) => [...prev, data.detail || data.stage]);
      } else if (event === 'done') {
        setGenerating(false);
        toast.success(`草案已生成：${data.object_count} 个业务对象`);
        loadModels(datasourceId);
        if (data.model_id) loadModel(data.model_id);
      } else {
        setGenerating(false);
        toast.error(data.message || '生成失败');
      }
    });
  };

  // ── 保存（仅 draft） ──
  const handleSave = async () => {
    if (!model) return;
    setSaving(true);
    try {
      const { data } = await ontologyApi.save(model.id, jsonDraft);
      setModel(data);
      setJsonDraft(data.json_content || '');
      setDirty(false);
      loadModels(datasourceId || undefined);
      toast.success('草案已保存，YAML/MD 已同步派生');
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  // ── 激活 ──
  const handleActivate = async () => {
    if (!model) return;
    setActivating(true);
    try {
      await ontologyApi.activate(model.id);
      toast.success('模型已激活，对象向量已重建');
      setActivateOpen(false);
      await loadModels(datasourceId || undefined);
      await loadModel(model.id);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '激活失败');
    } finally {
      setActivating(false);
    }
  };

  // ── 归档 / 删除 ──
  const handleArchive = async () => {
    if (!model) return;
    try {
      await ontologyApi.archive(model.id);
      toast.success('模型已归档');
      await loadModels(datasourceId || undefined);
      await loadModel(model.id);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '归档失败');
    }
  };

  const handleDelete = async () => {
    if (!model) return;
    try {
      await ontologyApi.remove(model.id);
      toast.success('模型已删除');
      setModel(null);
      setSelectedId(null);
      setJsonDraft('');
      loadModels(datasourceId || undefined);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  const objects = useMemo(() => parseObjects(model ? jsonDraft : ''), [model, jsonDraft]);
  const jsonInvalid = useMemo(() => {
    if (!dirty || !jsonDraft.trim()) return false;
    try {
      JSON.parse(jsonDraft);
      return false;
    } catch {
      return true;
    }
  }, [jsonDraft, dirty]);

  const editable = model?.status === 'draft';

  return (
    <div className="space-y-4">
      {/* 顶部工具栏 */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Boxes className="h-5 w-5 text-primary" />
          <h1 className="text-lg font-semibold">本体建模</h1>
        </div>
        <div className="flex-1" />
        <Select
          value={datasourceId ? String(datasourceId) : ''}
          onValueChange={(v) => setDatasourceId(Number(v))}
        >
          <SelectTrigger className="w-56">
            <SelectValue placeholder="选择数据源" />
          </SelectTrigger>
          <SelectContent>
            {datasources.map((ds) => (
              <SelectItem key={ds.id} value={String(ds.id)}>
                {ds.name}（{ds.db_type || '-'}）
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button onClick={handleGenerate} disabled={generating || !datasourceId}>
          {generating ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Sparkles className="h-4 w-4 mr-1" />}
          {generating ? '生成中…' : '生成本体模型'}
        </Button>
        <Button
          variant="outline"
          onClick={() => loadModels(datasourceId || undefined)}
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>

      {/* 生成进度 */}
      {generating && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">生成进度</CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-24">
              <div className="space-y-1">
                {progressLogs.map((line, i) => (
                  <div key={i} className="text-xs text-muted-foreground">• {line}</div>
                ))}
                {progressLogs.length === 0 && (
                  <div className="text-xs text-muted-foreground">正在启动…</div>
                )}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-4 gap-4">
        {/* 左侧：模型列表 */}
        <Card className="xl:col-span-1">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">模型列表</CardTitle>
          </CardHeader>
          <CardContent>
            {models.length === 0 ? (
              <div className="text-xs text-muted-foreground py-4 text-center">
                暂无模型，选择数据源后点击"生成本体模型"
              </div>
            ) : (
              <div className="space-y-1">
                {models.map((m) => {
                  const meta = STATUS_META[m.status] || STATUS_META.draft;
                  return (
                    <button
                      key={m.id}
                      onClick={() => loadModel(m.id)}
                      className={`w-full text-left rounded-lg border px-3 py-2 transition-colors
                        ${selectedId === m.id
                          ? 'border-primary bg-primary/5'
                          : 'border-border hover:bg-muted/50'}`}
                    >
                      <div className="flex items-center justify-between gap-1">
                        <span className="text-sm font-medium truncate">{m.name}</span>
                        <Badge variant="outline" className={`flex-shrink-0 ${meta.cls}`}>
                          {meta.label}
                        </Badge>
                      </div>
                      <div className="text-xs text-muted-foreground mt-0.5">
                        {m.object_count} 个对象 · 更新于 {m.updated_at?.slice(0, 16).replace('T', ' ')}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>

        {/* 中间：三格式编辑器 */}
        <Card className="xl:col-span-2">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <CardTitle className="text-sm">
                {model ? model.name : '编辑器'}
                {dirty && editable && <span className="ml-2 text-xs text-amber-500">未保存</span>}
                {jsonInvalid && <span className="ml-2 text-xs text-red-500">JSON 格式错误</span>}
              </CardTitle>
              {model && (
                <div className="flex items-center gap-2">
                  {editable && (
                    <Button size="sm" onClick={handleSave} disabled={saving || jsonInvalid || !dirty}>
                      {saving ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Save className="h-3.5 w-3.5 mr-1" />}
                      保存草案
                    </Button>
                  )}
                  {editable && (
                    <Button size="sm" variant="default" onClick={() => setActivateOpen(true)}>
                      <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
                      确认激活
                    </Button>
                  )}
                  {model.status === 'active' && (
                    <Button size="sm" variant="outline" onClick={handleArchive}>
                      <Archive className="h-3.5 w-3.5 mr-1" />
                      归档
                    </Button>
                  )}
                  {model.status !== 'active' && (
                    <Button size="sm" variant="ghost" className="text-red-500 hover:text-red-600" onClick={handleDelete}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </div>
              )}
            </div>
            {!editable && model && (
              <div className="text-xs text-muted-foreground">
                {model.status === 'active'
                  ? '激活模型只读；如需修改请重新生成草案后编辑并激活。'
                  : '已归档模型只读。'}
              </div>
            )}
          </CardHeader>
          <CardContent>
            {!model ? (
              <div className="text-sm text-muted-foreground py-12 text-center">
                从左侧选择模型，或先生成本体草案
              </div>
            ) : (
              <Tabs defaultValue="json">
                <TabsList>
                  <TabsTrigger value="json">JSON</TabsTrigger>
                  <TabsTrigger value="yaml">YAML</TabsTrigger>
                  <TabsTrigger value="md">MD 预览</TabsTrigger>
                </TabsList>
                <TabsContent value="json" className="mt-2">
                  <CodeMirror
                    value={jsonDraft}
                    height="60vh"
                    extensions={[json()]}
                    editable={editable}
                    onChange={(v) => {
                      setJsonDraft(v);
                      setDirty(true);
                    }}
                    className="border border-border rounded-md text-xs overflow-hidden"
                  />
                  <div className="text-xs text-muted-foreground mt-1">
                    JSON 为唯一事实源；保存后服务端自动重新派生 YAML 与 MD。
                  </div>
                </TabsContent>
                <TabsContent value="yaml" className="mt-2">
                  <CodeMirror
                    value={model.yaml_content || ''}
                    height="60vh"
                    extensions={[yaml()]}
                    editable={false}
                    className="border border-border rounded-md text-xs overflow-hidden"
                  />
                </TabsContent>
                <TabsContent value="md" className="mt-2">
                  <CodeMirror
                    value={model.md_content || ''}
                    height="60vh"
                    extensions={[markdown()]}
                    editable={false}
                    className="border border-border rounded-md text-xs overflow-hidden"
                  />
                  <div className="text-xs text-muted-foreground mt-1">
                    MD 按对象分节生成，激活时逐段向量化；直接修改 MD 不会回写结构。
                  </div>
                </TabsContent>
              </Tabs>
            )}
          </CardContent>
        </Card>

        {/* 右侧：对象预览 */}
        <Card className="xl:col-span-1">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">对象预览（{objects.length}）</CardTitle>
          </CardHeader>
          <CardContent>
            {objects.length === 0 ? (
              <div className="text-xs text-muted-foreground py-4 text-center">暂无对象</div>
            ) : (
              <ScrollArea className="h-[60vh]">
                <div className="space-y-2 pr-2">
                  {objects.map((obj, i) => (
                    <div key={obj.key || i} className="rounded-lg border border-border px-3 py-2">
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm font-medium">{obj.display_name || obj.key}</span>
                        <span className="text-xs text-muted-foreground">{obj.key}</span>
                      </div>
                      {(obj.aliases || []).length > 0 && (
                        <div className="text-xs text-muted-foreground mt-0.5 truncate">
                          别名: {(obj.aliases as string[]).join('、')}
                        </div>
                      )}
                      <div className="flex items-center gap-2 mt-1 text-[11px] text-muted-foreground">
                        <Badge variant="outline" className="font-normal">{(obj.properties || []).length} 属性</Badge>
                        <Badge variant="outline" className="font-normal">{(obj.links || []).length} 关系</Badge>
                        <Badge variant="outline" className="font-normal">{(obj.metrics || []).length} 指标</Badge>
                      </div>
                      {obj.primary_table && (
                        <div className="text-[11px] text-muted-foreground mt-1 truncate">
                          主表: {obj.primary_table}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </ScrollArea>
            )}
          </CardContent>
        </Card>
      </div>

      {/* 激活确认弹窗 */}
      <Dialog open={activateOpen} onOpenChange={setActivateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认激活本体模型？</DialogTitle>
          </DialogHeader>
          <div className="text-sm text-muted-foreground space-y-2">
            <p>激活将执行以下操作：</p>
            <ul className="list-disc pl-5 space-y-1">
              <li>该数据源原 active 模型将被归档</li>
              <li>逐对象 MD 段重新向量化写入向量库（{objects.length} 个对象）</li>
              <li>ontology_first 检索策略将使用新模型</li>
            </ul>
            <p>激活前请确认 JSON 内容已检查无误。</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setActivateOpen(false)} disabled={activating}>
              取消
            </Button>
            <Button onClick={handleActivate} disabled={activating}>
              {activating && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
              确认激活
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
