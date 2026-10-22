import { useEffect, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import client from '@/api/client';
import { metricsApi } from '@/api/metrics';
import type { PageParam } from '@/stores/dashboardStore';

/**
 * 参数名选择框（对齐 ui-resource-display.md：从资源清单中选，不手填内部标识）。
 *
 * paramKey / bind_param 过去是自由文本，要求用户手工和图表 SQL 的 {{param}}、
 * 看板参数三处对齐，拼错即静默失效；这里改为从看板 page_params 中选，
 * 并允许就地新建。引用了未定义的参数必须红标提醒，不得静默不生效。
 */
export function ParamNameSelect({ value, params, placeholder, label, onChange, onCreate }: {
  value: string; params: PageParam[]; placeholder?: string; label: string;
  onChange: (name: string) => void; onCreate: (param: PageParam) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<Omit<PageParam, 'label'> & { label: string }>({ name: '', type: 'string', default: '', label: '' });
  const [labelTouched, setLabelTouched] = useState(false);
  const defined = params.some(p => p.name === value);
  const invalid = !!value && !defined;
  // 未手改过显示名时跟参数名走，改过则保留用户输入
  const setName = (text: string) => setDraft(d => ({ ...d, name: text, label: labelTouched ? d.label : text }));
  return <div className="space-y-1">
    <Label className="text-xs">{label}</Label>
    <div className="flex items-center gap-1">
      <Select value={defined ? value : ''} onValueChange={v => { if (v === '__create__') { setCreating(true); return; } onChange(v); }}>
        <SelectTrigger className={`h-7 flex-1 text-xs ${invalid ? 'border-destructive' : ''}`}>
          <SelectValue placeholder={placeholder || '请选择页面参数'} />
        </SelectTrigger>
        <SelectContent>
          {params.map(p => <SelectItem key={p.name} value={p.name} className="text-xs">
            {/* 显示用业务名，参数名作为次要技术标识（value 仍用 name 是实现细节） */}
            <span className="flex items-center gap-2">
              <span>{p.label || p.name}</span>
              {p.label && <span className="font-mono text-[10px] text-muted-foreground">{p.name}</span>}
              <span className="text-[10px] text-muted-foreground">{PARAM_TYPE_LABELS[p.type] || p.type}</span>
            </span>
          </SelectItem>)}
          <SelectItem value="__create__" className="text-xs">＋ 新建参数…</SelectItem>
        </SelectContent>
      </Select>
      {/* 已填但未定义的参数保留原值并允许直接改，避免选择框把用户已有输入吞掉 */}
      {invalid && <Input className="h-7 w-24 text-xs font-mono" value={value} onChange={e => onChange(e.target.value)} aria-label={`${label}（未定义）`} />}
    </div>
    {invalid && <p role="alert" className="text-[10px] text-destructive">
      参数「{value}」未在看板参数中定义，控件值不会生效。请改选已定义参数或新建同名参数。
    </p>}
    {creating && <div className="space-y-1.5 rounded-md border bg-muted/30 p-2">
      <div className="space-y-1">
        <Label className="text-[10px]">参数名（字母/数字/下划线，不以数字开头）</Label>
        <Input className="h-6 text-xs font-mono" value={draft.name} placeholder="如 date_start"
          onChange={e => setName(e.target.value)} />
      </div>
      <div className="space-y-1">
        <Label className="text-[10px]">显示名</Label>
        <Input className="h-6 text-xs" value={draft.label} placeholder="如 开始日期"
          onChange={e => { setLabelTouched(true); setDraft(d => ({ ...d, label: e.target.value })); }} />
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        <div className="space-y-1">
          <Label className="text-[10px]">类型</Label>
          <Select value={draft.type} onValueChange={v => setDraft(d => ({ ...d, type: v as PageParam['type'] }))}>
            <SelectTrigger className="h-6 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              {Object.entries(PARAM_TYPE_LABELS).map(([k, l]) => <SelectItem key={k} value={k} className="text-xs">{l}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-[10px]">默认值</Label>
          <Input className="h-6 text-xs" value={draft.default} onChange={e => setDraft(d => ({ ...d, default: e.target.value }))} />
        </div>
      </div>
      <p className="text-[10px] text-muted-foreground">图表 SQL 中使用 {'{{参数名}}'} 引用该参数。</p>
      <div className="flex justify-end gap-1">
        <Button size="sm" variant="ghost" className="h-6 text-xs" onClick={() => setCreating(false)}>取消</Button>
        <Button size="sm" className="h-6 text-xs" disabled={!VALID_PARAM_NAME.test(draft.name) || !draft.label.trim() || params.some(p => p.name === draft.name)} onClick={() => {
          const created: PageParam = { name: draft.name, type: draft.type, default: draft.default, label: draft.label.trim() || draft.name };
          onCreate(created); onChange(created.name); setCreating(false); setLabelTouched(false);
          setDraft({ name: '', type: 'string', default: '', label: '' });
        }}>创建并选用</Button>
      </div>
      {params.some(p => p.name === draft.name) && draft.name && <p className="text-[10px] text-destructive">参数名已存在</p>}
    </div>}
  </div>;
}

const PARAM_TYPE_LABELS: Record<PageParam['type'], string> = {
  string: '文本', number: '数字', date: '日期', date_range: '日期范围',
};
const VALID_PARAM_NAME = /^[A-Za-z_]\w*$/;

/** 选项来源：手动录入 / 维度字典 value_labels / 数据集列去重值 */
type OptionsSource = 'manual' | 'dict' | 'dataset';
export interface WidgetOption { label: string; value: string }

/**
 * 控件选项编辑器。
 *
 * 过去是一行「逗号分隔」自由文本：无法表达 label≠value（码值场景全废）、
 * 含逗号的选项被 split 静默截断、也不能从字典/数据集取候选。
 */
export function WidgetOptionsEditor({ value, onChange }: { value: WidgetOption[]; onChange: (options: WidgetOption[]) => void }) {
  const [source, setSource] = useState<OptionsSource>(() => 'manual');
  const options = value || [];
  return <div className="space-y-2">
    <div className="flex items-center justify-between">
      <Label className="text-xs">选项</Label>
      <Select value={source} onValueChange={v => setSource(v as OptionsSource)}>
        <SelectTrigger className="h-6 w-32 text-[11px]" aria-label="选项来源"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="manual" className="text-xs">手动录入</SelectItem>
          <SelectItem value="dict" className="text-xs">维度字典</SelectItem>
          <SelectItem value="dataset" className="text-xs">数据集列</SelectItem>
        </SelectContent>
      </Select>
    </div>
    {source === 'manual' && <OptionRows options={options} onChange={onChange} />}
    {source === 'dict' && <DictOptionSource onSelect={onChange} />}
    {source === 'dataset' && <DatasetOptionSource onSelect={onChange} />}
    {options.length === 0 && source !== 'manual' && (
      <p className="text-[10px] text-muted-foreground">尚未选择来源，当前控件没有可选项。</p>
    )}
  </div>;
}

function OptionRows({ options, onChange }: { options: WidgetOption[]; onChange: (o: WidgetOption[]) => void }) {
  const update = (index: number, key: keyof WidgetOption, text: string) =>
    onChange(options.map((o, i) => i === index ? { ...o, [key]: text } : o));
  return <div className="space-y-1">
    {options.map((o, i) => <div key={i} className="flex items-center gap-1">
      <Input className="h-6 flex-1 text-xs" value={o.label} placeholder="显示名" aria-label={`选项 ${i + 1} 显示名`}
        onChange={e => update(i, 'label', e.target.value)} />
      <Input className="h-6 w-20 text-xs font-mono" value={o.value} placeholder="值" aria-label={`选项 ${i + 1} 取值`}
        onChange={e => update(i, 'value', e.target.value)} />
      <Button size="sm" variant="ghost" className="h-6 w-6 p-0" aria-label={`删除选项 ${i + 1}`} onClick={() => onChange(options.filter((_, j) => j !== i))}>
        <Trash2 className="h-3 w-3" />
      </Button>
    </div>)}
    <Button size="sm" variant="outline" className="h-6 w-full text-xs" onClick={() => onChange([...options, { label: '', value: '' }])}>
      <Plus className="h-3 w-3 mr-1" />添加选项
    </Button>
    <p className="text-[10px] text-muted-foreground">显示名给使用者看，取值下发给 SQL；两者可以不同。</p>
  </div>;
}

/** 维度字典 value_labels 是枚举码→业务标签的唯一事实源（对齐 ontology-modeling.md §3） */
function DictOptionSource({ onSelect }: { onSelect: (o: WidgetOption[]) => void }) {
  const [items, setItems] = useState<{ id: number; name: string; labels: Record<string, string> }[]>([]);
  const [picked, setPicked] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    metricsApi.listAllDimensions().then(({ data }) => {
      const rows = (Array.isArray(data) ? data : data.items || []) as { id: number; name: string; value_labels?: string | Record<string, string> }[];
      setItems(rows.filter(r => r.value_labels).map(r => ({
        id: r.id, name: r.name, labels: typeof r.value_labels === 'string' ? safeParse(r.value_labels) : (r.value_labels || {}),
      })).filter(r => Object.keys(r.labels).length));
    }).catch(() => setError('维度字典接口暂不可用'));
  }, []);
  if (error) return <p role="alert" className="text-[10px] text-destructive">{error}</p>;
  return <div className="space-y-1">
    <Select value={picked} onValueChange={name => {
      setPicked(name);
      const dim = items.find(i => i.name === name);
      if (dim) onSelect(Object.entries(dim.labels).map(([value, label]) => ({ label: label || value, value })));
    }}>
      <SelectTrigger className="h-6 text-xs" aria-label="选择维度字典"><SelectValue placeholder="选择维度字典" /></SelectTrigger>
      <SelectContent>{items.map(i => <SelectItem key={i.id} value={i.name} className="text-xs">{i.name}</SelectItem>)}</SelectContent>
    </Select>
    {picked && <p className="text-[10px] text-muted-foreground">已按字典「{picked}」的码值生成 {countLabels(items, picked)} 个选项。</p>}
  </div>;
}

const countLabels = (items: { name: string; labels: Record<string, string> }[], name: string) =>
  Object.keys(items.find(i => i.name === name)?.labels || {}).length;

function DatasetOptionSource({ onSelect }: { onSelect: (o: WidgetOption[]) => void }) {
  const [datasets, setDatasets] = useState<{ id: number; name: string }[]>([]);
  const [datasetId, setDatasetId] = useState('');
  const [columns, setColumns] = useState<string[]>([]);
  const [column, setColumn] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    client.get('/datasets/')
      .then(({ data }) => setDatasets((data.items || []).map((d: any) => ({ id: d.id, name: d.name }))))
      .catch(() => setError('数据集列表暂不可用'));
  }, []);
  useEffect(() => {
    if (!datasetId) { setColumns([]); return; }
    client.get(`/datasets/${datasetId}`).then(({ data }) => {
      const fields = (data.field_config?.fields || data.fields || []) as { field?: string; name?: string; role?: string }[];
      setColumns(fields.filter(f => f.role !== 'measure').map(f => f.field || f.name).filter(Boolean) as string[]);
    }).catch(() => setError('字段读取失败'));
  }, [datasetId]);
  const load = async () => {
    setLoading(true); setError('');
    try {
      const { data } = await client.post(`/datasets/${datasetId}/preview`, { dimensions: [column], measures: [], limit: 500 });
      const rows = (data.rows || []) as unknown[];
      const values = Array.from(new Set(rows.map(r => Array.isArray(r) ? r[0] : (r as any)?.[column]).filter(v => v !== null && v !== '') as (string | number)[]));
      if (!values.length) setError('该列没有可枚举值，请换一列或改用手动录入');
      else onSelect(values.map(v => ({ label: String(v), value: String(v) })));
    } catch (e: any) {
      setError(e.response?.data?.detail || '去重值读取失败，请改用手动录入');
    } finally { setLoading(false); }
  };
  return <div className="space-y-1">
    <div className="grid grid-cols-2 gap-1">
      <Select value={datasetId} onValueChange={v => { setDatasetId(v); setColumn(''); }}>
        <SelectTrigger className="h-6 text-xs"><SelectValue placeholder="数据集" /></SelectTrigger>
        <SelectContent>{datasets.map(d => <SelectItem key={d.id} value={String(d.id)} className="text-xs">{d.name}</SelectItem>)}</SelectContent>
      </Select>
      <Select value={column} onValueChange={setColumn} disabled={!datasetId}>
        <SelectTrigger className="h-6 text-xs"><SelectValue placeholder="列" /></SelectTrigger>
        <SelectContent>{columns.map(c => <SelectItem key={c} value={c} className="text-xs">{c}</SelectItem>)}</SelectContent>
      </Select>
    </div>
    <Button size="sm" variant="outline" className="h-6 w-full text-xs" disabled={!datasetId || !column || loading} onClick={load}>
      {loading ? '读取中…' : '取该列去重值'}
    </Button>
    {error && <p role="alert" className="text-[10px] text-destructive">{error}</p>}
  </div>;
}

function safeParse(text: string): Record<string, string> {
  try { const v = JSON.parse(text); return v && typeof v === 'object' ? v as Record<string, string> : {}; } catch { return {}; }
}
