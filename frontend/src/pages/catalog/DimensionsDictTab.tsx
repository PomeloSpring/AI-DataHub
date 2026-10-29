/**
 * 维度字典 Tab — 全局维度(adh_dimensions)的唯一个人工编辑入口。
 *
 * 与本体回写链路(sync_enums_to_dimensions)协同: value_labels/aliases 人工值优先,
 * 本体激活时只补空白不覆盖此处修改。名称与数据源归属不开放(是字典键与血缘锚点)。
 *
 * 筛选口径: 列表接口不分页(一次全量返回)，因此筛选在**全量集**上前端完成 ——
 * 计数精确且无需重拉; 指标 Tab 因服务端分页(size=20)必须把过滤下推 SQL，
 * 两个 Tab 的口径差异源于分页形状而非两套规则(后端 metrics_service.list_all_dimensions 注释同口径)。
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import { Search, Edit, RefreshCw, CheckCircle2, Filter } from 'lucide-react';
import { metricsApi } from '@/api/metrics';
import type { DictFilters } from '@/api/metrics';
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
import { SearchSelect, type SearchSelectOption } from '@/components/ui/search-select';
import { toast } from '@/lib/toast';
import {
  DICT_UNSET, dimensionCategoryLabel, objectLabel, withCount,
} from '@/lib/dictLabels';

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
  /** 口径状态（1=启用参与取数, 0=已停用/未发布） */
  is_active: number;
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
  facets,
  facetsError,
  onRetryFacets,
}: {
  /** 模型工作区嵌入时传入: 只列该模型对象挂接的维度(scope='model')或待归属(scope='unbound') */
  modelId?: number;
  scope?: 'model' | 'unbound';
  /** 筛选词表(指标中心统一拉取): 只用于拿归属对象的**业务名**; 选项值域仍从已加载的全量行里取 */
  facets?: DictFilters | null;
  /** 词表加载失败原因: 必须显式提示(降级要可见), 对象列此时回退显示 key */
  facetsError?: string | null;
  onRetryFacets?: () => void;
} = {}) {
  const [items, setItems] = useState<DictDimension[]>([]);
  const [loading, setLoading] = useState(false);
  // 加载失败与“确实没有数据”必须可区分(与指标字典 Tab 同口径)
  const [loadError, setLoadError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [fCategory, setFCategory] = useState('all');
  const [fObject, setFObject] = useState('');
  const [fCertified, setFCertified] = useState('all');
  const [fStatus, setFStatus] = useState('all');
  const [fEnum, setFEnum] = useState('all');
  const [fBind, setFBind] = useState('all');
  const [editTarget, setEditTarget] = useState<DictDimension | null>(null);
  // 新建入口：此前本页只有「编辑」，维度根本建不了（列表列有归属对象/物理列，表单却无从填写）
  const [createOpen, setCreateOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      // is_active='all': 字典维护页必须看得见停用/未发布行（接口缺省只给生效行，
      // 那是给 Chat/看板参数枚举等取数向消费方的口径）——否则 AS-BOT 未发布的维度
      // 在页面上既看不到也恢复不了，只能改库。
      const base = modelId && scope === 'model' ? { model_id: modelId, scope }
        : (scope ? { scope } : {});
      const { data } = await metricsApi.listAllDimensions({ ...base, is_active: 'all' });
      setItems(data.items || []);
    } catch (e: any) {
      // 失败即暴露: 不把接口错误吞成“暂无维度数据”
      setItems([]);
      const msg = e?.response?.data?.detail || e?.message || '未知错误';
      setLoadError(String(msg));
      toast.error(`维度字典加载失败: ${msg}`);
    } finally {
      setLoading(false);
    }
  }, [modelId, scope]);

  useEffect(() => { load(); }, [load]);

  const objectNames = useMemo(
    () => Object.fromEntries((facets?.objects || []).map((o) => [o.key, o.display_name] as const)),
    [facets]);

  /** 枚举码值个数(value_labels 可能是 JSON 字符串或对象) */
  const enumCount = (d: DictDimension) =>
    Object.keys(parseJson<Record<string, string>>(d.value_labels, {})).length;
  const hasColumn = (d: DictDimension) => !!(d.target_table && d.target_column);

  // 选项与计数均从已加载的全量行派生(列表不分页 ⇒ 精确), 不依赖也不写死枚举
  const categoryOptions = useMemo(() => {
    const m = new Map<string, number>();
    for (const d of items) {
      const k = (d.category || '').trim() || DICT_UNSET;
      m.set(k, (m.get(k) || 0) + 1);
    }
    return [...m.entries()]
      .map(([value, count]) => ({ value, count }))
      .sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
  }, [items]);
  const objectOptions = useMemo<SearchSelectOption[]>(() => {
    const m = new Map<string, number>();
    for (const d of items) {
      const k = (d.bound_object_key || '').trim() || DICT_UNSET;
      m.set(k, (m.get(k) || 0) + 1);
    }
    return [...m.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([k, count]) => ({
        value: k,
        label: k === DICT_UNSET ? '未归属' : objectLabel(objectNames, k),
        description: `${k === DICT_UNSET ? '待归属维度' : k} ${count} 条`,
      }));
  }, [items, objectNames]);
  const counts = useMemo(() => ({
    certified: items.filter((d) => !!d.certified).length,
    active: items.filter((d) => d.is_active !== 0).length,
    withEnum: items.filter((d) => enumCount(d) > 0).length,
    bound: items.filter((d) => hasColumn(d)).length,
  }), [items]);

  const filtered = useMemo(() => items.filter((d) => {
    if (search.trim()) {
      const kw = search.trim().toLowerCase();
      const aliases = parseJson<string[]>(d.aliases, []);
      const labels = Object.values(parseJson<Record<string, string>>(d.value_labels, {}));
      const hit = [d.name, d.name_en, d.bound_object_key, d.target_table, d.target_column,
                   d.description, objectLabel(objectNames, d.bound_object_key),
                   ...aliases, ...labels]
        .some((x) => String(x || '').toLowerCase().includes(kw));
      if (!hit) return false;
    }
    if (fCategory !== 'all') {
      const cat = (d.category || '').trim();
      if (fCategory === DICT_UNSET ? cat : cat !== fCategory) return false;
    }
    if (fObject) {
      const obj = (d.bound_object_key || '').trim();
      if (fObject === DICT_UNSET ? obj : obj !== fObject) return false;
    }
    if (fCertified !== 'all' && (!!d.certified !== (fCertified === '1'))) return false;
    if (fStatus !== 'all' && (d.is_active !== 0 ? 1 : 0) !== Number(fStatus)) return false;
    if (fEnum !== 'all' && (enumCount(d) > 0) !== (fEnum === 'has')) return false;
    if (fBind !== 'all' && hasColumn(d) !== (fBind === 'bound')) return false;
    return true;
  }), [items, search, fCategory, fObject, fCertified, fStatus, fEnum, fBind, objectNames]);

  const hasFilters = !!(search || fCategory !== 'all' || fObject
    || fCertified !== 'all' || fStatus !== 'all' || fEnum !== 'all' || fBind !== 'all');
  // 编辑弹窗的分类候选: 字典里已在用的分类在前(按条数) + 历史预设兼顾，
  // 否则存量分类(属性/状态/渠道…)在下拉里渲染成空白，像“分类被清空了”
  const categoryChoices = useMemo(() => {
    const list: string[] = [];
    for (const f of categoryOptions) if (f.value !== DICT_UNSET) list.push(f.value);
    for (const c of CATEGORIES) if (!list.includes(c.value)) list.push(c.value);
    return list;
  }, [categoryOptions]);
  const resetFilters = () => {
    setSearch('');
    setFCategory('all');
    setFObject('');
    setFCertified('all');
    setFStatus('all');
    setFEnum('all');
    setFBind('all');
  };

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        全局维度字典（adh_dimensions）：run_semantic_query 解析维度名/别名/枚举标签的权威来源。
        名称与数据源归属由同步与本体链路维护，此处编辑别名、枚举标签与属性 ——
        <span className="text-foreground font-medium">人工值优先，本体激活只补空白不覆盖</span>。
      </p>

      {/* 筛选栏: 选项与计数都从已加载的全量行派生(列表不分页 ⇒ 精确)，不得写死枚举 ——
          历史上表单那套 CATEGORIES(基础属性/状态枚举/…) 与库内真实分类(属性/状态/地理/…)
          并不同名，拿它当筛选项会做出永远查不出结果的死选项。 */}
      <div className="flex flex-wrap items-center gap-3 p-3 border rounded-lg bg-muted/30">
        <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Filter className="w-4 h-4" /> 筛选
        </div>
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="搜索名称/英文名/别名/枚举/对象..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 h-8 w-60"
          />
        </div>
        <Button size="sm" className="h-8" onClick={() => setCreateOpen(true)}>新建维度</Button>
        <Select value={fCategory} onValueChange={setFCategory}>
          <SelectTrigger className="h-8 w-36" aria-label="维度分类"><SelectValue placeholder="维度分类" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部分类</SelectItem>
            {categoryOptions.map((f) => (
              <SelectItem key={f.value} value={f.value}>
                {withCount(dimensionCategoryLabel(f.value), f.count)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <SearchSelect
          value={fObject}
          onValueChange={setFObject}
          options={objectOptions}
          placeholder="归属对象"
          searchPlaceholder="搜索对象名称..."
          emptyText="暂无已挂接对象"
          className="h-8 w-44 text-xs"
          ariaLabel="归属对象"
        />
        <Select value={fCertified} onValueChange={setFCertified}>
          <SelectTrigger className="h-8 w-32" aria-label="认证状态"><SelectValue placeholder="认证状态" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部认证状态</SelectItem>
            <SelectItem value="1">{withCount('已认证', counts.certified)}</SelectItem>
            <SelectItem value="0">{withCount('草稿', items.length - counts.certified)}</SelectItem>
          </SelectContent>
        </Select>
        {/* 口径状态与指标字典同义：停用/未发布的行也要可见可恢复 */}
        <Select value={fStatus} onValueChange={setFStatus}>
          <SelectTrigger className="h-8 w-32" aria-label="口径状态"><SelectValue placeholder="口径状态" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部口径状态</SelectItem>
            <SelectItem value="1">{withCount('启用', counts.active)}</SelectItem>
            <SelectItem value="0">{withCount('已停用', items.length - counts.active)}</SelectItem>
          </SelectContent>
        </Select>
        <Select value={fEnum} onValueChange={setFEnum}>
          <SelectTrigger className="h-8 w-36" aria-label="枚举标签"><SelectValue placeholder="枚举标签" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部枚举标签</SelectItem>
            <SelectItem value="has">{withCount('已有码值', counts.withEnum)}</SelectItem>
            <SelectItem value="missing">{withCount('缺失码值', items.length - counts.withEnum)}</SelectItem>
          </SelectContent>
        </Select>
        <Select value={fBind} onValueChange={setFBind}>
          <SelectTrigger className="h-8 w-36" aria-label="物理列绑定"><SelectValue placeholder="物理列绑定" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部物理列</SelectItem>
            <SelectItem value="bound">{withCount('已绑定', counts.bound)}</SelectItem>
            <SelectItem value="unbound">{withCount('未绑定', items.length - counts.bound)}</SelectItem>
          </SelectContent>
        </Select>
        {hasFilters && (
          <Button variant="ghost" size="sm" className="h-8 text-xs" onClick={resetFilters}>
            清除筛选
          </Button>
        )}
        <Button variant="outline" size="sm" className="h-8 ml-auto" onClick={load}>
          <RefreshCw className="w-4 h-4 mr-1" /> 刷新
        </Button>
        <span className="text-xs text-muted-foreground">共 {filtered.length} / {items.length} 条</span>
      </div>
      {facetsError && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-destructive">
          <span>对象业务名词表加载失败：{facetsError}（归属对象暂以 key 显示，名称/列/分类筛选仍可用）</span>
          {onRetryFacets && (
            <Button variant="outline" size="sm" className="h-7 text-xs" onClick={onRetryFacets}>
              <RefreshCw className="w-3 h-3 mr-1" />重试
            </Button>
          )}
        </div>
      )}

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
            ) : loadError ? (
              <tr><td colSpan={8} className="p-8 text-center text-destructive">维度字典加载失败：{loadError}</td></tr>
            ) : filtered.length === 0 ? (
              <tr><td colSpan={8} className="p-8 text-center text-muted-foreground">
                {hasFilters ? (
                  <span>无符合筛选条件的维度 · <button
                    className="underline underline-offset-2 hover:text-foreground" onClick={resetFilters}>清除筛选</button></span>
                ) : '暂无维度数据'}
              </td></tr>
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
                        {d.is_active === 0 && (
                          <Badge variant="outline" className="text-[11px] font-normal text-muted-foreground">已停用</Badge>
                        )}
                      </div>
                    </td>
                    <td className="p-3 text-muted-foreground">{d.name_en || '-'}</td>
                    <td className="p-3">
                      <Badge variant={d.category === '时间' ? 'default' : 'outline'} className="text-xs">
                        {dimensionCategoryLabel(d.category)}
                      </Badge>
                    </td>
                    <td className="p-3 text-muted-foreground" title={d.bound_object_key || ''}>
                      {d.bound_object_key ? objectLabel(objectNames, d.bound_object_key) : '未归属'}
                    </td>
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

      {createOpen && (
        <CreateDimensionDialog
          categories={categoryChoices}
          objectNames={objectNames}
          onClose={() => setCreateOpen(false)}
          onSaved={() => { setCreateOpen(false); load(); }}
        />
      )}
      {editTarget && (
        <EditDimensionDialog
          dimension={editTarget}
          categories={categoryChoices}
          onClose={() => setEditTarget(null)}
          onSaved={() => { setEditTarget(null); load(); }}
        />
      )}
    </div>
  );
}

// ── 新建弹窗 ─────────────────────────────────────────────────────────
// 字典行是跨数据源口径：**不提供“目标源/数据源”字段**（服务层恒为 0），
// 物理归属由「归属对象」+「目标列」决定（见 ui-resource-display §2：选不手填）。
function CreateDimensionDialog({
  categories = [], objectNames = {}, onClose, onSaved,
}: {
  categories?: string[];
  objectNames?: Record<string, string>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState({
    display_name: '', name_en: '', category: '属性', bound_object_key: '',
    target_table: '', target_column: '', aliases: '', value_labels: '', description: '',
  });
  const [saving, setSaving] = useState(false);
  const categoryChoices = useMemo(() => {
    const list = [...categories];
    if (form.category && !list.includes(form.category)) list.unshift(form.category);
    return list;
  }, [categories, form.category]);
  const objectEntries = useMemo(
    () => Object.entries(objectNames).sort((a, b) => (a[1] || a[0]).localeCompare(b[1] || b[0])),
    [objectNames]);

  const handleSave = async () => {
    if (!form.display_name.trim()) { toast.error('请填写维度名称（业务名）'); return; }
    if (!form.bound_object_key) { toast.error('请选择归属对象（维度必须挂在业务对象上）'); return; }
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
      await metricsApi.createDimension({
        display_name: form.display_name.trim(),
        name_en: form.name_en.trim(),
        category: form.category,
        bound_object_key: form.bound_object_key,
        target_table: form.target_table.trim(),
        target_column: form.target_column.trim(),
        aliases: form.aliases.split(/[,，]/).map(x => x.trim()).filter(Boolean),
        value_labels: Object.keys(labels).length ? labels : null,
        description: form.description.trim(),
      });
      toast.success('维度已创建');
      onSaved();
    } catch (e: any) {
      // 透传服务端具体错因（如归属对象引用校验失败），不换成通用文案
      toast.error(e.response?.data?.detail || '创建失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-xl">
        <DialogHeader><DialogTitle>新建维度</DialogTitle></DialogHeader>
        <div className="space-y-4 py-2">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>维度名称（业务名）*</Label>
              <Input value={form.display_name} placeholder="如: 注册日期"
                onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>英文名</Label>
              <Input value={form.name_en} placeholder="如: register_date"
                onChange={(e) => setForm({ ...form, name_en: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>分类</Label>
              <Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {categoryChoices.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>归属对象 *</Label>
              <Select value={form.bound_object_key || undefined}
                onValueChange={(v) => setForm({ ...form, bound_object_key: v })}>
                <SelectTrigger><SelectValue placeholder={objectEntries.length ? '选择业务对象' : '对象清单为空'} /></SelectTrigger>
                <SelectContent>
                  {objectEntries.map(([key, label]) => (
                    <SelectItem key={key} value={key}>{label || key} ({key})</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>目标表</Label>
              <Input value={form.target_table} placeholder="数据来源表名"
                onChange={(e) => setForm({ ...form, target_table: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>目标列（物理列）</Label>
              <Input value={form.target_column} placeholder="数据来源列名"
                onChange={(e) => setForm({ ...form, target_column: e.target.value })} />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>别名（逗号分隔，取数时自动解析）</Label>
            <Input value={form.aliases} placeholder="如: 下单渠道, 渠道来源"
              onChange={(e) => setForm({ ...form, aliases: e.target.value })} />
          </div>
          <div className="space-y-1.5">
            <Label>枚举标签（每行一条，码值=业务名；查询结果自动翻译）</Label>
            <Textarea rows={4} className="font-mono text-xs" value={form.value_labels}
              placeholder={'0=否\n1=是\n2=已上传'}
              onChange={(e) => setForm({ ...form, value_labels: e.target.value })} />
          </div>
          <div className="space-y-1.5">
            <Label>描述</Label>
            <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={handleSave} disabled={saving}>{saving ? '创建中…' : '创建'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── 编辑弹窗 ─────────────────────────────────────────────────────────

function EditDimensionDialog({
  dimension, categories, onClose, onSaved,
}: {
  dimension: DictDimension;
  /** 分类候选(字典已在用的值优先): 从清单选，不手填 */
  categories?: string[];
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
    is_active: dimension.is_active === 0 ? 0 : 1,
  });
  const [saving, setSaving] = useState(false);
  // 当前值必须可渲染(不在候选里时补上)，避免编辑时把已有分类隐形成“选择空白”
  const categoryChoices = [...(categories?.length ? categories : CATEGORIES.map((c) => c.value))];
  if (form.category && !categoryChoices.includes(form.category)) categoryChoices.unshift(form.category);

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
        is_active: form.is_active,
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
                  {categoryChoices.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
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
          {/* 口径状态（is_active）：停用与 AS-BOT 未发布同义 —— 行保留但不参与检索/取数。
              没这个开关时，停用行在页面上看得见却恢复不了（只能改库）。 */}
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="is_active"
              checked={!!form.is_active}
              onChange={(e) => setForm({ ...form, is_active: e.target.checked ? 1 : 0 })}
              className="h-4 w-4 rounded border-input"
            />
            <Label htmlFor="is_active" className="text-sm font-normal cursor-pointer">
              启用（参与语义检索与取数）
            </Label>
          </div>
          {!form.is_active && (
            <p className="text-xs text-amber-600">当前为停用态：该维度不会被语义检索命中，也不会出现在看板参数/查询候选里。</p>
          )}
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
