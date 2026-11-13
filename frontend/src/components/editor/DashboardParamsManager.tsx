/**
 * 「看板参数」管理区：把两套参数命名空间摆到同一处可见、可改、可删。
 *
 * 收口前的状态（2026-11-10 用户反馈"看板编辑功能中也不呈现，无法操作"）：
 *  - 遗留 `params` 只能在**看板列表页**的「状态与参数设置」弹窗里改，编辑器里看不见；
 *  - 它的类型词汇（text/number/date/select）与 `page_params`（string/number/date/date_range）
 *    是两套，同一形状在两套里写法不同，渲染时按 `type === 'date'` 判 → 存成 `string`
 *    的日期参数只能渲染成文本框；
 *  - 画布控件已经在驱动同一批键时，这一份定义既没被标注也没被清理，页面上就长出第二个输入口。
 *
 * 因此这里给一份实现（编辑器与列表页弹窗共用），形状用 `PARAM_SHAPES` 统一词汇，
 * 并显式标出"画布控件已驱动"的键，让"要不要留这一条"成为看得见的决定。
 */
import { Plus, Trash2, Wand2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import type { DashboardParam, PageParam } from '@/stores/dashboardStore';
import {
  boundParamKeys, CONTROL_BY_PARAM_SHAPE, PARAM_SHAPES, PARAM_SHAPE_LABELS, type ParamShape,
} from '@/lib/dashboardParamAssembly';

const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

export interface ParamRow {
  /** 属于哪套命名空间：`param` = 遗留查询参数，`page` = 看板参数 */
  via: 'param' | 'page';
  index: number;
  name: string;
  label: string;
  shape: ParamShape;
  defaultValue: string;
  options: string[];
  /** 看板级常数（仅对 `page` 成立）：值照下发，页面上不开输入口 */
  readonly: boolean;
}

/** 两套定义 → 同一份行模型（编辑期只操作行模型，落库时再拆回两套，避免两处各写一份表单） */
export function toParamRows(params: DashboardParam[] = [], pageParams: PageParam[] = []): ParamRow[] {
  return [
    ...params.map((p, index) => ({ via: 'param' as const, index, name: p.name, label: p.label || '',
      shape: (p.type === 'text' ? 'string' : (p.type as ParamShape)) || 'string',
      defaultValue: p.default ?? '', options: p.options || [], readonly: false })),
    ...pageParams.map((p, index) => ({ via: 'page' as const, index, name: p.name, label: p.label || '',
      shape: ((p.type as ParamShape) || 'string'), defaultValue: p.default ?? '',
      options: p.options || [], readonly: !!p.readonly })),
  ];
}

/** 行模型 → 两套定义（保留各自的原字段，只改可编辑的那几项） */
export function fromParamRows(rows: ParamRow[], params: DashboardParam[] = [], pageParams: PageParam[] = []) {
  const build = (row: ParamRow): DashboardParam | PageParam => ({
    name: row.name.trim(), label: row.label.trim() || row.name.trim(), type: row.shape,
    default: row.defaultValue, ...(row.shape === 'select' ? { options: row.options } : {}),
    // readonly 要显式写回（包括 false）：下方 mergeOld 会先把存量行铺回来，
    // 不把“刚取消勾选”这件事写进去，取消只读会被旧值盖掉（存了不生效）。
    ...(row.via === 'page' ? { readonly: row.readonly } : {}),
  }) as DashboardParam | PageParam;
  const nextParams = rows.filter(r => r.via === 'param').map(build);
  const nextPage = rows.filter(r => r.via === 'page').map(build);
  // 遗留 params 的历史字段（placeholder 等）在重建时不丢：按名字回捞原行合并
  const mergeOld = (list: DashboardParam[] | PageParam[], next: any[]) => next.map(item => {
    const old = (list || []).find(p => p.name === item.name);
    return old ? { ...old, ...item } : item;
  });
  return { params: mergeOld(params, nextParams) as DashboardParam[],
    page_params: mergeOld(pageParams, nextPage) as PageParam[] };
}

export interface Props {
  params?: DashboardParam[];
  pageParams?: PageParam[];
  /** 画布图表：用于标出"这个键已经有控件在驱动"（编辑器传，列表页没有画布就不标） */
  charts?: { chart_type?: string; config?: any }[];
  onChange: (next: { params: DashboardParam[]; page_params: PageParam[] }) => void;
  /** 把遗留参数搬到看板参数并放一个控件到画布（只有编辑器有画布） */
  onConvertToControl?: (param: DashboardParam, shape: ParamShape) => void;
}

export default function DashboardParamsManager({ params = [], pageParams = [], charts, onChange, onConvertToControl }: Props) {
  const rows = toParamRows(params, pageParams);
  const driven = charts ? boundParamKeys(charts) : [];
  const dup = rows.some(r => !NAME_RE.test(r.name.trim())) ||
    new Set(rows.map(r => r.name.trim())).size !== rows.length;

  const patch = (via: ParamRow['via'], index: number, next: Partial<ParamRow>) => {
    const updated = rows.map(r => (r.via === via && r.index === index ? { ...r, ...next } : r));
    onChange(fromParamRows(updated, params, pageParams));
  };
  const remove = (via: ParamRow['via'], index: number) =>
    onChange(fromParamRows(rows.filter(r => !(r.via === via && r.index === index)), params, pageParams));
  const add = (via: ParamRow['via']) => onChange(fromParamRows(
    [...rows, { via, index: rows.filter(r => r.via === via).length, name: '', label: '',
      shape: 'string', defaultValue: '', options: [], readonly: false }], params, pageParams));

  const section = (via: ParamRow['via'], title: string, hint: string) => {
    const items = rows.filter(r => r.via === via);
    return <section className="space-y-2" aria-label={title}>
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium">{title}</h3>
        <span className="text-xs text-muted-foreground">{hint}</span>
      </div>
      {items.length === 0 && <p className="rounded border border-dashed px-3 py-2 text-xs text-muted-foreground">
        暂无{title}。参数只在没有画布控件驱动它时，才在页面顶部出现输入口。
      </p>}
      {items.map(row => {
        const isDriven = driven.includes(row.name.trim());
        return <div key={`${row.via}:${row.index}`} className="space-y-2 rounded border p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">显示名</span>
            <Input aria-label={`显示名 ${row.name || '新参数'}`} className="h-8 w-40 text-xs" value={row.label}
              placeholder="如 统计时间窗口" onChange={e => patch(row.via, row.index, { label: e.target.value })} />
            <span className="text-xs text-muted-foreground">参数名</span>
            <Input aria-label={`参数名 ${row.label || row.name || '新参数'}`} className="h-8 w-40 text-xs" value={row.name}
              placeholder="start_time" onChange={e => patch(row.via, row.index, { name: e.target.value })} />
            <span className="text-xs text-muted-foreground">形状</span>
            <Select value={row.shape} onValueChange={v => patch(row.via, row.index, { shape: v as ParamShape })}>
              <SelectTrigger aria-label={`形状 ${row.name || '新参数'}`} className="h-8 w-[110px] text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PARAM_SHAPES.map(s => <SelectItem key={s} value={s} className="text-xs">{PARAM_SHAPE_LABELS[s]}</SelectItem>)}
              </SelectContent>
            </Select>
            {isDriven && <Badge variant="secondary" title="画布上的控件已在驱动这个键，顶部不会再重复出现输入口">
              画布控件已驱动</Badge>}
            {row.via === 'param' && onConvertToControl && (
              <Button size="sm" variant="outline" className="h-8 text-xs" title={`在画布放置「${PARAM_SHAPE_LABELS[row.shape]}」控件并改由控件录入`}
                disabled={!row.name.trim() || !NAME_RE.test(row.name.trim())}
                onClick={() => onConvertToControl({ name: row.name.trim(), label: row.label.trim() || row.name.trim(),
                  type: row.shape, default: row.defaultValue, options: row.options } as DashboardParam, row.shape)}>
                <Wand2 className="mr-1 h-3.5 w-3.5" />转成控件
              </Button>)}
            <Button size="icon" variant="ghost" className="h-8 w-8" aria-label={`删除参数 ${row.label || row.name || '新参数'}`}
              onClick={() => remove(row.via, row.index)}><Trash2 className="h-4 w-4" /></Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">默认值</span>
            <Input aria-label={`默认值 ${row.name || '新参数'}`} className="h-8 w-52 text-xs" value={row.defaultValue}
              placeholder={row.shape === 'date_range' ? '留空由用户选择' : ''}
              onChange={e => patch(row.via, row.index, { defaultValue: e.target.value })} />
            {row.shape === 'select' && <>
              <span className="text-xs text-muted-foreground">候选值（逗号分隔）</span>
              <Input aria-label={`候选值 ${row.name || '新参数'}`} className="h-8 w-64 text-xs" value={row.options.join(',')}
                placeholder="华东,华北" onChange={e => patch(row.via, row.index,
                  { options: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })} />
              {!row.options.length && <span className="text-[10px] text-amber-600 dark:text-amber-500">
                未配置候选值，页面上会是空下拉</span>}
            </>}
            {/* 看板级常数只对「看板参数」开：遗留 params 不参与本页顶部渲染带之外的口径固定 */}
            {row.via === 'page' && <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <input type="checkbox" className="h-3.5 w-3.5 accent-primary"
                aria-label={`固定为看板级常数 ${row.name || '新参数'}`} checked={row.readonly}
                onChange={e => patch(row.via, row.index, { readonly: e.target.checked })} />
              只读（看板级常数）
            </label>}
            {row.via === 'page' && row.readonly && <span className="text-[10px] text-muted-foreground">
              值仍随取数下发，但页面上不给改（适合单 App 看板的分析对象）</span>}
          </div>
        </div>;
      })}
      <Button size="sm" variant="outline" className="h-8 text-xs" onClick={() => add(via)}>
        <Plus className="mr-1 h-3.5 w-3.5" />添加{title}
      </Button>
    </section>;
  };

  return <div className="space-y-4">
    <p className="text-xs text-muted-foreground">
      参数口径的权威在数据集声明里：从数据集引入图表时会自动补齐参数与控件，这里用于改名、改形状、删除与人工归一。
      同一个键只应有一个输入口 —— 画布控件驱动的键不会在页面顶部重复出现。
    </p>
    {section('page', '看板参数', '与画布控件同一套（逻辑参数落这里）')}
    {section('param', '遗留查询参数', '历史字段，建议逐个「转成控件」后清空')}
    {dup && <p role="alert" className="text-xs text-destructive">
      参数名需唯一且只能由字母/数字/下划线组成（不能以数字开头），当前填写不合法，未写入。
    </p>}
  </div>;
}

/** 遗留参数转控件时的控件类型（与 CONTROL_LABELS 同源，编辑器据此放卡片） */
export const controlOfShape = (shape: ParamShape) => CONTROL_BY_PARAM_SHAPE[shape];
