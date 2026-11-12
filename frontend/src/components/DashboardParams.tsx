/**
 * 看板参数（遗留 `params` + 未被控件驱动的 `page_params`）在浏览页/大屏的渲染带。
 *
 * 2026-11-10 收口两件事：
 *  1. **同一参数只有一个输入口**。画布控件已经驱动的键（`paramKey` 与 `bind_param`
 *     拆出的变量名都算）不再在这里重复渲染。过去只给页面参数带做了去重，遗留
 *     `params` 带照样渲一份，而下发时 `{...paramValues, ...pageParamValues}` 会让
 *     顶栏那份被覆盖 —— 用户看到的是"能填但不起作用"的死输入口（空承诺）。
 *  2. **类型必须真的决定控件**。`date` 给日期选择器、`date_range` 给起止两个日期
 *     选择器、`select` 给选择框（没配候选值就显式说出来，不静默退成文本框）。
 *     历史缺陷：`type` 存了但渲染不生效（string 与 text 两套词汇并存，日期被当文本填）。
 */
import { Search, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useDashboardStore, type Dashboard, type DashboardParam, type PageParam } from '../stores/dashboardStore';
import { placeholderOfShape, shapeOfParam, uncontrolledParams } from '@/lib/dashboardParamAssembly';

type AnyParam = DashboardParam | PageParam;

/** 日期区间值归一：控件给对象、历史默认值给 "a,b" 文本，两处读同一份 */
export function rangeOf(value: any): { start: string; end: string } {
  if (value && typeof value === 'object') return { start: String(value.start ?? ''), end: String(value.end ?? '') };
  if (typeof value === 'string' && value.includes(',')) {
    const [start, end] = value.split(',');
    return { start: (start || '').trim(), end: (end || '').trim() };
  }
  return { start: '', end: '' };
}

/** 一个参数按声明形状渲染出的输入口（选择框/日期选择器/数字/文本），值仍以内部键存 */
export function ParamField({ param, value, onChange }: {
  param: AnyParam; value: any; onChange: (value: any) => void;
}) {
  const shape = shapeOfParam(param);
  // 占位文案与画布控件同源（“动作 + 业务名”），本参数显式配的 placeholder 优先
  const placeholder = (param as DashboardParam).placeholder || placeholderOfShape(shape, param.label || param.name);
  const options = (param as DashboardParam).options || (param as PageParam).options || [];

  if (shape === 'date_range') {
    const { start, end } = rangeOf(value);
    return <span className="flex items-center gap-1">
      <Input aria-label={`${param.label || param.name} 起始日期`} type="date" className="h-8 w-[132px] text-xs"
        value={start} onChange={e => onChange({ ...rangeOf(value), start: e.target.value })} />
      <span className="text-xs text-muted-foreground">~</span>
      <Input aria-label={`${param.label || param.name} 结束日期`} type="date" className="h-8 w-[132px] text-xs"
        value={end} onChange={e => onChange({ ...rangeOf(value), end: e.target.value })} />
    </span>;
  }
  if (shape === 'date') {
    return <Input aria-label={param.label || param.name} type="date" className="h-8 w-[140px] text-xs"
      value={String(value ?? '')} onChange={e => onChange(e.target.value)} />;
  }
  if (shape === 'number') {
    return <Input aria-label={param.label || param.name} type="number" className="h-8 w-[120px] text-xs"
      value={String(value ?? '')} placeholder={placeholder} onChange={e => onChange(e.target.value)} />;
  }
  if (shape === 'select') {
    // 没配候选值的选择框等于"看着能选、点开是空的"，必须说出来而不是退成文本框
    if (!options.length) return <span className="flex items-center gap-1">
      <Select value={String(value ?? '') || undefined} onValueChange={onChange}>
        <SelectTrigger aria-label={param.label || param.name} className="h-8 w-[140px] text-xs">
          <SelectValue placeholder="无候选值" />
        </SelectTrigger>
        <SelectContent />
      </Select>
      <span className="text-[10px] text-amber-600 dark:text-amber-500">未配置候选值，请在「看板参数」里填写</span>
    </span>;
    return <Select value={String(value ?? '') || undefined} onValueChange={onChange}>
      <SelectTrigger aria-label={param.label || param.name} className="h-8 w-[140px] text-xs">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {options.map(opt => <SelectItem key={opt} value={opt} className="text-xs">{opt}</SelectItem>)}
      </SelectContent>
    </Select>;
  }
  return <Input aria-label={param.label || param.name} type="text" className="h-8 w-[140px] text-xs"
    value={String(value ?? '')} placeholder={placeholder} onChange={e => onChange(e.target.value)} />;
}

/**
 * 运行期参数带：只渲染没有被画布控件驱动的参数。
 * 两套命名空间合并成一行（一行两带是历史遗留，用户无法判断以哪个为准）。
 */
export function DashboardRuntimeParams({ dashboard }: { dashboard: Dashboard }) {
  const { paramValues, pageParamValues, setParamValue, setPageParamValue, currentId } = useDashboardStore();
  const resetParams = useDashboardStore(s => s.resetParams);
  const refreshCharts = useDashboardStore(s => s.refreshCharts);
  const charts = useDashboardStore(s => s.dashboards.find(d => d.id === dashboard.id)?.charts);
  if (currentId !== dashboard.id) return null;

  // 同一个参数只有一个输入口：画布控件（paramKey / bind_param）已驱动的键不再重复渲染，
  // 两套命名空间合并成一行（一行两带是历史遗留，用户无法判断以哪个为准）
  const rows = uncontrolledParams(dashboard, charts || []);
  if (!rows.length) return null;
  return <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b bg-muted/20 px-4 py-2">
    {rows.map(({ param, via }) => {
      const value = via === 'param'
        ? (paramValues[param.name] ?? param.default ?? '')
        : (pageParamValues[param.name] ?? param.default ?? '');
      return <label key={`${via}:${param.name}`} className="flex items-center gap-2 text-xs">
        <span className="whitespace-nowrap text-muted-foreground">{param.label || param.name}</span>
        <ParamField param={param} value={value}
          onChange={v => (via === 'param' ? setParamValue : setPageParamValue)(param.name, v)} />
      </label>;
    })}
    {/* 这一带的值不会自动触发取数（与画布控件同口径），所以必须给出可见的生效入口 */}
    <span className="ml-auto flex items-center gap-2">
      <Button size="sm" variant="outline" className="h-8 text-xs" onClick={() => void refreshCharts(dashboard.id)}>
        <Search className="mr-1 h-3.5 w-3.5" />搜索
      </Button>
      <Button size="sm" variant="ghost" className="h-8 text-xs" title="把全部参数恢复为默认值并重新查询"
        onClick={() => void resetParams(dashboard.id)}>
        <RotateCcw className="mr-1 h-3.5 w-3.5" />重置
      </Button>
    </span>
  </div>;
}
