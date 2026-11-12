import { useEffect, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import type { PageParam } from '@/stores/dashboardStore';
import {
  decisionsApi, errorText, type DecisionParamBinding, type DecisionTreeRow,
} from '@/api/decisions';

/**
 * 看板决策控件的配置面（属性面板「控件配置」里的一段）。
 *
 * 只配两件事：**看哪棵树** + **哪个看板参数喂给哪个槽位**。
 * 这里不配阈值、不配判定表达式，也不拼 filters：条件口径属于决策树资产（在数据中台
 * 配置页改），筛选值到 intent 的翻译唯一发生在服务端 `semantics.param_plan`。
 */

const SLOTS: { value: DecisionParamBinding['slot']; label: string }[] = [
  { value: 'time_window', label: '时间区间（日期/相对天数）' },
  { value: 'dimension', label: '维度取值（要填维度业务名）' },
  { value: 'site', label: '站点' },
];

export default function DecisionWidgetConfig({
  chart, pageParams, onWidgetConfigChange,
}: {
  chart: any;
  pageParams: PageParam[];
  onWidgetConfigChange: (id: number, key: string, value: any) => void;
}) {
  const [trees, setTrees] = useState<DecisionTreeRow[]>([]);
  const [loadError, setLoadError] = useState('');
  const treeKey = String(chart.config?.treeKey || '');
  const bindings: DecisionParamBinding[] = Array.isArray(chart.config?.paramBindings)
    ? chart.config.paramBindings : [];

  useEffect(() => {
    let alive = true;
    decisionsApi.listTrees({ status: 'active' })
      .then(({ data }) => { if (alive) { setTrees(data.items || []); setLoadError(''); } })
      .catch((err: any) => { if (alive) setLoadError(errorText(err)); });
    return () => { alive = false; };
  }, []);

  const writeBindings = (next: DecisionParamBinding[]) =>
    onWidgetConfigChange(chart.id, 'paramBindings', next);

  const patch = (idx: number, part: Partial<DecisionParamBinding>) =>
    writeBindings(bindings.map((b, i) => (i === idx ? { ...b, ...part } : b)));

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="text-xs">决策树（只列已发布的）</Label>
        <select
          aria-label="决策树"
          className="h-7 w-full rounded-md border border-input bg-background px-2 text-xs"
          value={treeKey}
          onChange={e => onWidgetConfigChange(chart.id, 'treeKey', e.target.value)}
        >
          <option value="">请选择决策树</option>
          {trees.map(t => (
            <option key={t.tree_key} value={t.tree_key}>{t.name}（v{t.version}）</option>
          ))}
        </select>
        {!!loadError && (
          <p role="alert" className="text-[10px] text-destructive">
            决策树清单读不到：{loadError}
          </p>
        )}
        {!loadError && !trees.length && (
          <p className="text-[10px] text-muted-foreground">
            没有已发布的决策树（草案不参与评估），请先在「数据中台 → 决策建议」建好并发布。
          </p>
        )}
        {!!treeKey && !trees.some(t => t.tree_key === treeKey) && (
          <p role="alert" className="text-[10px] text-destructive">
            原选的决策树已下架或不存在，控件运行时会显示「未评估」而不是旧建议。
          </p>
        )}
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Label className="text-xs">看板筛选绑定</Label>
          <Button size="sm" variant="outline" className="ml-auto h-6 px-1.5 text-[10px]"
            aria-label="加一条筛选绑定"
            onClick={() => writeBindings([...bindings, { key: '', slot: 'time_window' }])}>
            <Plus className="h-3 w-3" />
          </Button>
        </div>
        {!bindings.length && (
          <p className="text-[10px] text-muted-foreground">
            未绑定：控件按决策树自身口径评估，看板上的日期/下拉不会影响建议。
          </p>
        )}
        {bindings.map((b, i) => (
          <div key={i} className="space-y-1 rounded-md border border-border/60 p-2">
            <div className="flex items-center gap-1">
              <select
                aria-label={`第 ${i + 1} 条绑定的参数`}
                className="h-6 min-w-0 flex-1 rounded-md border border-input bg-background px-1 text-[11px]"
                value={b.key}
                onChange={e => patch(i, { key: e.target.value })}
              >
                <option value="">选择看板参数</option>
                {pageParams.map(p => (
                  <option key={p.name} value={p.name}>{p.label || p.name}</option>
                ))}
              </select>
              <select
                aria-label={`第 ${i + 1} 条绑定的槽位`}
                className="h-6 min-w-0 flex-1 rounded-md border border-input bg-background px-1 text-[11px]"
                value={b.slot}
                onChange={e => patch(i, { slot: e.target.value as DecisionParamBinding['slot'] })}
              >
                {SLOTS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
              <Button size="sm" variant="ghost" className="h-6 w-6 p-0 text-red-600"
                aria-label={`删除第 ${i + 1} 条绑定`}
                onClick={() => writeBindings(bindings.filter((_, j) => j !== i))}>
                <Trash2 className="h-3 w-3" />
              </Button>
            </div>
            {b.slot === 'dimension' && (
              <Input
                aria-label={`第 ${i + 1} 条绑定的维度业务名`}
                className="h-6 text-[11px]"
                placeholder="维度业务名（指标与维度里登记的，不是列名）"
                value={b.dimension || ''}
                onChange={e => patch(i, { dimension: e.target.value })}
              />
            )}
          </div>
        ))}
        <p className="text-[10px] text-muted-foreground">
          只声明「哪个参数喂给哪类条件」；日期区间怎么落地由服务端与看板图表同一口径解析，
          没给值的绑定会在卡片上写明「未套用」。
        </p>
      </div>
    </div>
  );
}
