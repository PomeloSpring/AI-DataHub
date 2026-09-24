import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import {
  ArrowLeft, Boxes, Code2, Play, Save, Plus, Trash2, Loader2, Users, Shield,
} from 'lucide-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import client from '../../api/client';
import { hasPerm } from '../../stores/permissionStore';

interface DatasetField {
  field: string;
  label?: string;
  role: 'dimension' | 'measure';
  is_time?: boolean;
  aliases?: string[];
  enum?: Record<string, string>;
  unit?: string;
  calculation?: string;
  description?: string;
}

interface Scope {
  id?: number;
  subject_type: 'user' | 'role';
  subject_id: number;
  filters: Array<{ field: string; op: string; value: any }>;
}

const OPS = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'in', 'like'];

export default function DatasetDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const datasetId = Number(id);
  const [ds, setDs] = useState<any>(null);
  const [fields, setFields] = useState<DatasetField[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const canManage = hasPerm('dataset:manage');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get(`/datasets/${datasetId}`);
      setDs(data);
      setFields((data.fields || []).map((f: any) => ({ ...f })));
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '加载数据集失败');
      navigate('/data/datasets');
    } finally {
      setLoading(false);
    }
  }, [datasetId, navigate]);

  useEffect(() => { load(); }, [load]);

  const saveBasic = async () => {
    setSaving(true);
    try {
      const { data } = await client.put(`/datasets/${datasetId}`, {
        description: ds.description, visibility: ds.visibility,
        ...(ds.source_type === 'sql' ? { datasource_id: Number(ds.datasource_id) || 0 } : {}),
        ...(ds.source_type === 'sql' && ds.sql_query ? { sql_query: ds.sql_query } : {}),
      });
      setDs(data);
      toast.success('已保存');
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="flex justify-center py-16 text-muted-foreground"><Loader2 className="h-6 w-6 animate-spin" /></div>;
  }
  if (!ds) return null;

  return (
    <div className="h-full overflow-auto p-1">
      <div className="flex items-center gap-3 mb-1">
        <Button variant="ghost" size="sm" onClick={() => navigate('/data/datasets')}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <h1 className="text-2xl font-bold flex items-center gap-2">{ds.name}</h1>
        <Badge variant="outline" className="flex items-center gap-1">
          {ds.source_type === 'semantic' ? <><Boxes className="h-3 w-3" /> 语义对象</> : <><Code2 className="h-3 w-3" /> SQL</>}
        </Badge>
      </div>
      {ds.source_type === 'semantic' && (
        <p className="text-sm text-muted-foreground ml-11 mb-4">
          绑定本体对象 <code className="px-1 rounded bg-muted">{ds.object_key}</code>，字段随语义层/本体更新自动演进
        </p>
      )}

      <Tabs defaultValue="fields" className="mt-2">
        <TabsList>
          <TabsTrigger value="fields">字段定义 ({fields.length})</TabsTrigger>
          <TabsTrigger value="preview">数据预览</TabsTrigger>
          <TabsTrigger value="basic">基本信息</TabsTrigger>
          <TabsTrigger value="scopes">行级范围</TabsTrigger>
          <TabsTrigger value="refs">引用</TabsTrigger>
        </TabsList>

        {/* ── 字段定义 ── */}
        <TabsContent value="fields" className="pt-3">
          <div className="border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs text-muted-foreground">
                <tr>
                  <th className="text-left px-3 py-2 font-medium">字段</th>
                  <th className="text-left px-3 py-2 font-medium">显示名</th>
                  <th className="text-left px-3 py-2 font-medium w-28">角色</th>
                  <th className="text-left px-3 py-2 font-medium">说明</th>
                </tr>
              </thead>
              <tbody>
                {fields.map((f, i) => (
                  <tr key={f.field} className="border-t">
                    <td className="px-3 py-2"><code className="text-xs">{f.field}</code>
                      {f.is_time && <Badge variant="secondary" className="ml-2 text-[10px]">时间</Badge>}
                    </td>
                    <td className="px-3 py-2">
                      {ds.source_type === 'sql' && canManage ? (
                        <Input className="h-7 text-sm" value={f.label || ''}
                          onChange={e => setFields(prev => prev.map((p, pi) => pi === i ? { ...p, label: e.target.value } : p))} />
                      ) : (f.label || f.field)}
                    </td>
                    <td className="px-3 py-2">
                      {ds.source_type === 'sql' && canManage ? (
                        <Select value={f.role} onValueChange={v => setFields(prev => prev.map((p, pi) => pi === i ? { ...p, role: v as any } : p))}>
                          <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="dimension">维度</SelectItem>
                            <SelectItem value="measure">度量</SelectItem>
                          </SelectContent>
                        </Select>
                      ) : (
                        <Badge variant={f.role === 'measure' ? 'default' : 'secondary'} className="text-[10px]">
                          {f.role === 'measure' ? '度量' : '维度'}
                        </Badge>
                      )}
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {f.description || (f.aliases?.length ? `别名: ${f.aliases.join(', ')}` : '')}
                      {f.calculation ? ` · ${f.calculation}` : ''}
                    </td>
                  </tr>
                ))}
                {fields.length === 0 && (
                  <tr><td colSpan={4} className="text-center py-6 text-muted-foreground text-sm">暂无字段</td></tr>
                )}
              </tbody>
            </table>
          </div>
          {ds.source_type === 'sql' && canManage && (
            <Button size="sm" className="mt-3" onClick={async () => {
              try {
                await client.put(`/datasets/${datasetId}`, { field_config: fields });
                toast.success('字段配置已保存');
              } catch (e: any) { toast.error(e.response?.data?.detail || '保存失败'); }
            }}>
              <Save className="h-3.5 w-3.5 mr-1" /> 保存字段配置
            </Button>
          )}
        </TabsContent>

        {/* ── 数据预览 ── */}
        <TabsContent value="preview" className="pt-3">
          <PreviewPanel datasetId={datasetId} fields={fields} />
        </TabsContent>

        {/* ── 基本信息 ── */}
        <TabsContent value="basic" className="pt-3 space-y-3 max-w-xl">
          <div className="space-y-1.5">
            <Label>描述</Label>
            <Textarea rows={3} value={ds.description || ''} disabled={!canManage}
              onChange={e => setDs({ ...ds, description: e.target.value })} />
          </div>
          <div className="space-y-1.5">
            <Label>可见性</Label>
            <Select value={ds.visibility || 'workspace'} disabled={!canManage}
              onValueChange={v => setDs({ ...ds, visibility: v })}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="private">私有(仅自己)</SelectItem>
                <SelectItem value="workspace">工作空间</SelectItem>
                <SelectItem value="public">公开</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {ds.source_type === 'sql' && (
            <>
              <div className="space-y-1.5">
                <Label>执行数据源</Label>
                <SqlDatasourceSelect
                  value={ds.datasource_id || 0}
                  disabled={!canManage}
                  onChange={v => setDs({ ...ds, datasource_id: v })}
                />
              </div>
              <div className="space-y-1.5">
                <Label>SQL</Label>
                <Textarea rows={6} className="font-mono text-xs" value={ds.sql_query || ''} disabled={!canManage}
                  onChange={e => setDs({ ...ds, sql_query: e.target.value })} />
              </div>
            </>
          )}
          {canManage && (
            <Button size="sm" onClick={saveBasic} disabled={saving}>
              {saving ? '保存中…' : '保存'}
            </Button>
          )}
        </TabsContent>

        {/* ── 行级范围 ── */}
        <TabsContent value="scopes" className="pt-3">
          <ScopesPanel datasetId={datasetId} fields={fields} canManage={canManage} />
        </TabsContent>

        {/* ── 引用 ── */}
        <TabsContent value="refs" className="pt-3">
          <div className="border rounded-lg divide-y">
            {(ds.references || []).map((r: any) => (
              <div key={r.chart_id} className="flex items-center justify-between px-3 py-2 text-sm">
                <span>{r.chart_name}</span>
                <Link className="text-xs text-primary hover:underline" to={`/dashboard/editor/${r.dashboard_id}`}>
                  看板: {r.dashboard_name}
                </Link>
              </div>
            ))}
            {(ds.references || []).length === 0 && (
              <div className="text-center py-6 text-muted-foreground text-sm">暂未被看板引用</div>
            )}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ── SQL 源执行数据源选择 ────────────────────────────────────────────────

function SqlDatasourceSelect({ value, disabled, onChange }: {
  value: number; disabled: boolean; onChange: (v: number) => void;
}) {
  const [list, setList] = useState<any[]>([]);
  useEffect(() => {
    client.get('/datasources/').then(({ data }) => setList(Array.isArray(data) ? data : (data?.items || []))).catch(() => {});
  }, []);
  return (
    <Select value={String(value)} onValueChange={v => onChange(Number(v))} disabled={disabled}>
      <SelectTrigger><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectItem value="0">默认引擎</SelectItem>
        {list.map((d: any) => <SelectItem key={d.id} value={String(d.id)}>{d.name}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}

// ── 数据预览(治理取数, 服务端施加权限/RLS/scopes) ─────────────────────

function PreviewPanel({ datasetId, fields }: { datasetId: number; fields: DatasetField[] }) {
  const [result, setResult] = useState<any>(null);
  const [running, setRunning] = useState(false);
  const dims = fields.filter(f => f.role === 'dimension');
  const measures = fields.filter(f => f.role === 'measure');
  const [dimensions, setDimensions] = useState<string[]>(dims.slice(0, 1).map(f => f.field));
  const [measuresSel, setMeasuresSel] = useState<string[]>(measures.slice(0, 2).map(f => f.field));

  const run = async () => {
    setRunning(true);
    try {
      const { data } = await client.post(`/datasets/${datasetId}/preview`, {
        dimensions, measures: measuresSel, limit: 100,
      });
      setResult(data);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '预览失败');
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 items-end">
        <div className="min-w-40">
          <Label className="text-xs">维度</Label>
          <div className="flex flex-wrap gap-1 mt-1">
            {dims.map(f => (
              <Badge key={f.field}
                variant={dimensions.includes(f.field) ? 'default' : 'outline'}
                className="cursor-pointer text-[11px]"
                onClick={() => setDimensions(p => p.includes(f.field) ? p.filter(x => x !== f.field) : [...p, f.field])}>
                {f.label || f.field}
              </Badge>
            ))}
          </div>
        </div>
        <div className="min-w-40">
          <Label className="text-xs">度量</Label>
          <div className="flex flex-wrap gap-1 mt-1">
            {measures.map(f => (
              <Badge key={f.field}
                variant={measuresSel.includes(f.field) ? 'default' : 'outline'}
                className="cursor-pointer text-[11px]"
                onClick={() => setMeasuresSel(p => p.includes(f.field) ? p.filter(x => x !== f.field) : [...p, f.field])}>
                {f.label || f.field}
              </Badge>
            ))}
          </div>
        </div>
        <Button size="sm" onClick={run} disabled={running}>
          {running ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <Play className="h-3.5 w-3.5 mr-1" />}
          预览取数
        </Button>
      </div>

      {result && (
        <>
          <div className="text-xs text-muted-foreground flex gap-3 flex-wrap">
            <span>{result.row_count} 行</span>
            {result.elapsed_ms != null && <span>{Math.round(result.elapsed_ms)}ms</span>}
            {result.applied_rls ? <span className="text-amber-600">已应用行级权限</span> : <span>无 RLS 规则</span>}
            {!!result.masked_columns?.length && <span className="text-destructive">脱敏列: {result.masked_columns.join(', ')}</span>}
          </div>
          <div className="border rounded-lg overflow-auto max-h-96">
            <table className="w-full text-xs">
              <thead className="bg-muted/50 sticky top-0">
                <tr>{(result.columns || []).map((c: string) => <th key={c} className="text-left px-2.5 py-1.5 font-medium whitespace-nowrap">{c}</th>)}</tr>
              </thead>
              <tbody>
                {(result.rows || []).slice(0, 100).map((row: any, ri: number) => (
                  <tr key={ri} className="border-t">
                    {(result.columns || []).map((c: string) => (
                      <td key={c} className="px-2.5 py-1 whitespace-nowrap">{row[c] == null ? '—' : String(row[c])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {!result && !running && (
        <div className="text-center py-8 text-muted-foreground text-sm border rounded-lg">
          点击「预览取数」经统一治理入口查询(自动施加你的权限/RLS/敏感脱敏/行级范围)
        </div>
      )}
    </div>
  );
}

// ── 行级范围配置 ──────────────────────────────────────────────────────

function ScopesPanel({ datasetId, fields, canManage }: {
  datasetId: number; fields: DatasetField[]; canManage: boolean;
}) {
  const [scopes, setScopes] = useState<Scope[]>([]);
  const [users, setUsers] = useState<any[]>([]);
  const [roles, setRoles] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    client.get(`/datasets/${datasetId}/scopes`).then(({ data }) => setScopes(data.scopes || [])).catch(() => {});
    if (canManage) {
      client.get('/users/', { params: { size: 200 } }).then(({ data }) => setUsers(data.items || data || [])).catch(() => {});
      client.get('/roles/').then(({ data }) => setRoles(Array.isArray(data) ? data : [])).catch(() => {});
    }
  }, [datasetId, canManage]);

  const update = (i: number, patch: Partial<Scope>) =>
    setScopes(prev => prev.map((s, si) => si === i ? { ...s, ...patch } : s));

  const updateFilter = (i: number, fi: number, patch: any) =>
    setScopes(prev => prev.map((s, si) => si === i ? {
      ...s, filters: s.filters.map((f, fli) => fli === fi ? { ...f, ...patch } : f),
    } : s));

  const save = async () => {
    setSaving(true);
    try {
      await client.put(`/datasets/${datasetId}/scopes`, { scopes });
      const { data } = await client.get(`/datasets/${datasetId}/scopes`);
      setScopes(data.scopes || []);
      toast.success('行级范围已保存');
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        为指定用户/角色追加行过滤条件（在 RLS 与敏感基线之上叠加，只收紧不放宽）。
      </p>
      {scopes.map((s, i) => {
        const subjectName = s.subject_type === 'user'
          ? users.find(u => u.id === s.subject_id)?.username || `用户#${s.subject_id}`
          : roles.find(r => r.id === s.subject_id)?.display_name || `角色#${s.subject_id}`;
        return (
          <div key={i} className="border rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-2">
              {s.subject_type === 'user' ? <Users className="h-4 w-4 text-muted-foreground" /> : <Shield className="h-4 w-4 text-muted-foreground" />}
              <span className="text-sm font-medium">{subjectName}</span>
              {canManage && (
                <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setScopes(p => p.filter((_, x) => x !== i))}>
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>
            {s.filters.map((f, fi) => (
              <div key={fi} className="flex gap-2 items-center flex-wrap">
                <Select value={f.field} onValueChange={v => updateFilter(i, fi, { field: v })} disabled={!canManage}>
                  <SelectTrigger className="h-8 w-40 text-xs"><SelectValue placeholder="字段" /></SelectTrigger>
                  <SelectContent>
                    {fields.map(fd => <SelectItem key={fd.field} value={fd.field}>{fd.label || fd.field}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Select value={f.op} onValueChange={v => updateFilter(i, fi, { op: v })} disabled={!canManage}>
                  <SelectTrigger className="h-8 w-24 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>{OPS.map(o => <SelectItem key={o} value={o}>{o}</SelectItem>)}</SelectContent>
                </Select>
                <Input className="h-8 w-44 text-xs" value={Array.isArray(f.value) ? f.value.join(',') : String(f.value ?? '')}
                  disabled={!canManage} placeholder="值(in 用逗号分隔)"
                  onChange={e => updateFilter(i, fi, {
                    value: f.op === 'in' ? e.target.value.split(',').map(x => x.trim()).filter(Boolean) : e.target.value,
                  })} />
              </div>
            ))}
            {canManage && (
              <Button variant="outline" size="sm" onClick={() => update(i, {
                filters: [...s.filters, { field: fields[0]?.field || '', op: 'eq', value: '' }],
              })}>
                <Plus className="h-3 w-3 mr-1" /> 条件
              </Button>
            )}
          </div>
        );
      })}
      {canManage && (
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => setScopes(p => [...p, {
            subject_type: 'user', subject_id: users[0]?.id || 0,
            filters: [{ field: fields[0]?.field || '', op: 'eq', value: '' }],
          }])}>
            <Plus className="h-3.5 w-3.5 mr-1" /> 添加范围
          </Button>
          <Button size="sm" onClick={save} disabled={saving || scopes.some(s => !s.subject_id)}>
            {saving ? '保存中…' : '保存行级范围'}
          </Button>
        </div>
      )}
      {scopes.length === 0 && !canManage && (
        <div className="text-center py-6 text-muted-foreground text-sm border rounded-lg">未配置行级范围</div>
      )}
    </div>
  );
}
