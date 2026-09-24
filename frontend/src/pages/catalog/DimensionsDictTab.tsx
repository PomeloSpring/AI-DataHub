/**
 * 维度字典 Tab — 全局维度(adh_dimensions)的唯一个人工编辑入口。
 *
 * 与本体回写链路(sync_enums_to_dimensions)协同: value_labels/aliases 人工值优先,
 * 本体激活时只补空白不覆盖此处修改。名称与数据源归属不开放(是字典键与血缘锚点)。
 */
import { useState, useEffect, useCallback } from 'react';
import { Search, Edit, RefreshCw, CheckCircle2 } from 'lucide-react';
import { metricsApi } from '@/api/metrics';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from 'sonner';

interface DictDimension {
  id: number;
  name: string;
  name_en: string;
  category: string;
  level: number;
  hierarchy: string;
  target_table: string;
  target_column: string;
  bound_object_key: string;
  aliases: string | string[] | null;
  value_labels: string | Record<string, string> | null;
  certified: number;
  datasource_id: number;
  description: string;
}

function parseJson<T>(v: unknown, fallback: T): T {
  if (v == null) return fallback;
  if (typeof v === 'string') {
    try { return JSON.parse(v) as T; } catch { return fallback; }
  }
  return v as T;
}

const CATEGORIES = [
  { value: '基础属性', label: '基础属性' },
  { value: '时间', label: '时间' },
  { value: '地理', label: '地理' },
  { value: '状态枚举', label: '状态枚举' },
  { value: '业务分类', label: '业务分类' },
  { value: '组织人员', label: '组织人员' },
];

export default function DimensionsDictTab({
  modelId,
  scope,
}: {
  /** 模型工作区嵌入时传入: 只列该模型对象挂接的维度(scope='model')或待归属(scope='unbound') */
  modelId?: number;
  scope?: 'model' | 'unbound';
} = {}) {
  const [items, setItems] = useState<DictDimension[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [editTarget, setEditTarget] = useState<DictDimension | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await metricsApi.listAllDimensions(
        modelId && scope === 'model' ? { model_id: modelId, scope } : (scope ? { scope } : undefined));
      setItems(data.items || []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [modelId, scope]);

  useEffect(() => { load(); }, [load]);

  const filtered = items.filter((d) => {
    if (!search.trim()) return true;
    const kw = search.trim().toLowerCase();
    const aliases = parseJson<string[]>(d.aliases, []);
    return [d.name, d.name_en, d.bound_object_key, d.target_table, d.target_column, ...aliases]
      .some((x) => String(x || '').toLowerCase().includes(kw));
  });

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        全局维度字典（adh_dimensions）：run_semantic_query 解析维度名/别名/枚举标签的权威来源。
        名称与数据源归属由同步与本体链路维护，此处编辑别名、枚举标签与属性 ——
        <span className="text-foreground font-medium">人工值优先，本体激活只补空白不覆盖</span>。
      </p>

      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="搜索维度名称/别名/绑定列..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw className="w-4 h-4 mr-1" /> 刷新
        </Button>
        <span className="text-xs text-muted-foreground ml-auto">共 {filtered.length} 条</span>
      </div>

      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">维度名称</th>
              <th className="text-left p-3 font-medium">英文名</th>
              <th className="text-left p-3 font-medium">分类</th>
              <th className="text-left p-3 font-medium">绑定对象</th>
              <th className="text-left p-3 font-medium">物理列</th>
              <th className="text-left p-3 font-medium">别名</th>
              <th className="text-left p-3 font-medium">枚举标签</th>
              <th className="text-right p-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={8} className="p-8 text-center text-muted-foreground">加载中...</td></tr>
            ) : filtered.length === 0 ? (
              <tr><td colSpan={8} className="p-8 text-center text-muted-foreground">暂无维度数据</td></tr>
            ) : (
              filtered.map((d) => {
                const aliases = parseJson<string[]>(d.aliases, []);
                const labels = parseJson<Record<string, string>>(d.value_labels, {});
                const labelCount = Object.keys(labels).length;
                return (
                  <tr key={d.id} className="border-t hover:bg-muted/30">
                    <td className="p-3">
                      <div className="flex items-center gap-1.5">
                        <span className="font-medium">{d.name}</span>
                        {!!d.certified && <span title="已认证"><CheckCircle2 className="w-3.5 h-3.5 text-green-500" /></span>}
                      </div>
                    </td>
                    <td className="p-3 text-muted-foreground">{d.name_en || '-'}</td>
                    <td className="p-3">
                      <Badge variant={d.category === '时间' ? 'default' : 'outline'} className="text-xs">
                        {d.category || '未分类'}
                      </Badge>
                    </td>
                    <td className="p-3 text-muted-foreground">{d.bound_object_key || '-'}</td>
                    <td className="p-3 font-mono text-xs text-muted-foreground">
                      {d.target_table ? `${d.target_table}.${d.target_column}` : '-'}
                    </td>
                    <td className="p-3 text-xs text-muted-foreground max-w-40 truncate" title={aliases.join(', ')}>
                      {aliases.length ? aliases.join(', ') : '-'}
                    </td>
                    <td className="p-3 text-xs">
                      {labelCount
                        ? <Badge variant="secondary" className="text-xs">{labelCount} 个码值</Badge>
                        : <span className="text-muted-foreground">-</span>}
                    </td>
                    <td className="p-3 text-right">
                      <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => setEditTarget(d)}>
                        <Edit className="w-4 h-4" />
                      </Button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {editTarget && (
        <EditDimensionDialog
          dimension={editTarget}
          onClose={() => setEditTarget(null)}
          onSaved={() => { setEditTarget(null); load(); }}
        />
      )}
    </div>
  );
}

// ── 编辑弹窗 ─────────────────────────────────────────────────────────

function EditDimensionDialog({
  dimension, onClose, onSaved,
}: {
  dimension: DictDimension;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState({
    name_en: dimension.name_en || '',
    category: dimension.category || '基础属性',
    description: dimension.description || '',
    aliases: parseJson<string[]>(dimension.aliases, []).join(', '),
    value_labels: Object.entries(parseJson<Record<string, string>>(dimension.value_labels, {}))
      .map(([k, v]) => `${k}=${v}`).join('\n'),
    certified: dimension.certified ? 1 : 0,
  });
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    // 解析 "code=label" 行 → 对象; 非法行直接拒绝定位
    const labels: Record<string, string> = {};
    for (const line of form.value_labels.split('\n')) {
      const s = line.trim();
      if (!s) continue;
      const i = s.indexOf('=');
      if (i <= 0) { toast.error(`枚举标签格式应为 码值=业务名: ${s}`); return; }
      labels[s.slice(0, i).trim()] = s.slice(i + 1).trim();
    }
    setSaving(true);
    try {
      await metricsApi.updateDimension(dimension.id, {
        name_en: form.name_en,
        category: form.category,
        description: form.description,
        certified: form.certified,
        aliases: form.aliases.split(/[,，]/).map((x) => x.trim()).filter(Boolean),
        value_labels: labels,
      });
      toast.success('维度已更新');
      onSaved();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '更新失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>编辑维度 — {dimension.name}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 py-2">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>英文名</Label>
              <Input value={form.name_en} onChange={(e) => setForm({ ...form, name_en: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>分类</Label>
              <Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {CATEGORIES.map((c) => <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>别名（逗号分隔，取数时自动解析）</Label>
            <Input
              value={form.aliases}
              onChange={(e) => setForm({ ...form, aliases: e.target.value })}
              placeholder="如: 下单渠道, 渠道来源"
            />
          </div>
          <div className="space-y-1.5">
            <Label>枚举标签（每行一条，码值=业务名；查询结果自动翻译）</Label>
            <Textarea
              rows={5}
              className="font-mono text-xs"
              value={form.value_labels}
              onChange={(e) => setForm({ ...form, value_labels: e.target.value })}
              placeholder={'0=否\n1=是\n2=已上传'}
            />
          </div>
          <div className="space-y-1.5">
            <Label>描述</Label>
            <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </div>
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="certified"
              checked={!!form.certified}
              onChange={(e) => setForm({ ...form, certified: e.target.checked ? 1 : 0 })}
              className="h-4 w-4 rounded border-input"
            />
            <Label htmlFor="certified" className="text-sm font-normal cursor-pointer">
              认证维度（口径已确认，UI 展示绿色对勾）
            </Label>
          </div>
          <p className="text-xs text-muted-foreground">
            物理绑定 {dimension.target_table || '-'}.{dimension.target_column || '-'}
            {dimension.bound_object_key ? ` · 本体对象 ${dimension.bound_object_key}` : ''}
            （由同步与本体链路维护，不在此编辑）
          </p>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={handleSave} disabled={saving}>{saving ? '保存中...' : '保存'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
