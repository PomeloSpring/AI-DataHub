/**
 * 引入数据集时的「参数装配」确认弹窗。
 *
 * 为什么是弹窗而不是静默自动补：补参数会往画布放控件、改看板清单，属于可见的结构改动，
 * 用户要能一眼看到"将要发生什么"再确认（本批拍板：弹窗列出将要补的东西，一键确认）。
 *
 * 组件是纯展示壳（对齐 editor 目录惯例）：计划由 `lib/dashboardParamAssembly` 算好后传入，
 * 勾选与显示名改动只在本地状态里收集，确认时一次性回传，不在这里碰 store。
 */
import { useEffect, useMemo, useState } from 'react';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { CONTROL_LABELS, pageParamTypeOf, VALUE_TYPE_LABELS, type AssemblyPlan } from '@/lib/dashboardParamAssembly';

interface Row {
  key: string;
  label: string;
  valueType: string;
  action: string;
  datasets: string[];
  /** 该行是否对应"要放一个控件"（决定确认后是否真的往画布放东西） */
  needsControl: boolean;
  createsParam: boolean;
}

export interface DatasetParamAssemblyDialogProps {
  open: boolean;
  plan: AssemblyPlan;
  datasetNames: string[];
  onCancel: () => void;
  /** selected = 用户确认要补的逻辑参数键；labels = 改过的显示名；withButtons = 要不要一起放「搜索/重置」 */
  onConfirm: (selected: string[], labels: Record<string, string>, withButtons: boolean) => void;
}

export default function DatasetParamAssemblyDialog({
  open, plan, datasetNames, onCancel, onConfirm,
}: DatasetParamAssemblyDialogProps) {
  const rows = useMemo<Row[]>(() => buildRows(plan), [plan]);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [withButtons, setWithButtons] = useState(true);

  // 每次打开都回到"全都要"的默认态，避免上一次勾掉的项悄悄不生效
  useEffect(() => {
    if (!open) return;
    setChecked(Object.fromEntries(rows.map(r => [r.key, true])));
    setLabels(Object.fromEntries(rows.map(r => [r.key, r.label])));
    setWithButtons(true);
  }, [open, rows]);

  const picked = rows.filter(r => checked[r.key]);
  const newParams = picked.filter(r => r.createsParam).length;
  const newControls = picked.filter(r => r.needsControl).length;
  const newButtons = withButtons ? plan.addButtons.length : 0;
  // 参数已全部由现有控件驱动、只缺按钮时，确认仍要可用（否则弹窗只能取消，缺陷依旧）
  const canConfirm = picked.length > 0 || newButtons > 0;

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onCancel(); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>为看板补齐数据集的入参</DialogTitle>
          <DialogDescription>
            {datasetNames.join('、') || '本次引入的数据集'} 声明了参数化的取数口径。
            看板只做可视化装配，参数由数据集声明决定，这里列出**将要补上的东西**，确认后才写入草稿
            （保存前仍可撤销，不会改动数据集本身）。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 max-h-[55vh] overflow-y-auto pr-1">
          {!!rows.length && (
            <table className="w-full text-xs">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="w-8"></th>
                  <th className="text-left font-normal">参数</th>
                  <th className="text-left font-normal">显示名</th>
                  <th className="text-left font-normal">形状 / 控件</th>
                  <th className="text-left font-normal">来源数据集</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.key} className="border-t align-top">
                    <td className="pt-2">
                      <input type="checkbox" className="h-3.5 w-3.5" checked={!!checked[r.key]}
                        aria-label={`补齐参数 ${r.label}`}
                        onChange={e => setChecked(c => ({ ...c, [r.key]: e.target.checked }))} />
                    </td>
                    <td className="py-2">
                      <div>{r.label}</div>
                      {/* 键是实现细节，只作次要信息展示（参数键不是内部 id，可查排错） */}
                      <div className="font-mono text-[10px] text-muted-foreground">{r.key}</div>
                    </td>
                    <td className="py-1">
                      <Input className="h-6 text-xs" value={labels[r.key] ?? r.label}
                        aria-label={`显示名 ${r.key}`}
                        onChange={e => setLabels(l => ({ ...l, [r.key]: e.target.value }))} />
                    </td>
                    <td className="py-2 text-[11px]">
                      <div>{VALUE_TYPE_LABELS[r.valueType] ?? r.valueType}</div>
                      <div className="text-muted-foreground">{r.action}</div>
                    </td>
                    <td className="py-2 text-[11px] text-muted-foreground">
                      {r.datasets.join('、') || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {!!plan.addButtons.length && (
            <label className="flex items-start gap-2 rounded-md border border-border bg-muted/30 px-3 py-2 text-[11px]">
              <input type="checkbox" className="mt-0.5 h-3.5 w-3.5" checked={withButtons}
                aria-label="同时放置搜索与重置按钮"
                onChange={e => setWithButtons(e.target.checked)} />
              <span>
                同时放置「{plan.addButtons.map(b => b.label).join('」「')}」按钮
                <span className="block text-muted-foreground">
                  控件只负责改值，取数要点「搜索」才下发；「重置」把全部参数恢复默认值并重新查询。
                  没有这两个入口，刚补上的参数就只是能填但不会生效。
                </span>
              </span>
            </label>
          )}

          {!!plan.keptButtons.length && (
            <p className="text-[11px] text-muted-foreground">
              画布上已有「{plan.keptButtons.join('」「')}」按钮，本次不重复放置。
            </p>
          )}

          {!!plan.keptControls.length && (
            <p className="text-[11px] text-muted-foreground">
              画布上已有控件在驱动：{plan.keptControls.join('、')}，本次不重复放置。
            </p>
          )}

          {!!plan.pending.length && (
            <Section title={`${plan.pending.length} 项需要你先确认形态（本次不生成控件）`} tone="warn">
              {plan.pending.map(p => (
                <li key={p.key}>
                  「{p.label}」（{p.datasetNames.join('、')}）：{p.reason}
                </li>
              ))}
            </Section>
          )}

          {!!plan.constants.length && (
            <Section title="这些入参是数据集正文里的口径常数，不该做成看板控件" tone="muted">
              {plan.constants.map(c => (
                <li key={c.key}>「{c.label}」（{c.datasetNames.join('、')}）</li>
              ))}
            </Section>
          )}

          {!!plan.conflicts.length && (
            <Section title="口径分歧（不阻塞出数，但建议去数据集页收敛）" tone="warn">
              {plan.conflicts.map((c, i) => <li key={i}>{c}</li>)}
            </Section>
          )}

          {!!plan.notes.length && (
            <Section title="装配后的口径说明" tone="muted">
              {plan.notes.map((n, i) => <li key={i}>{n}</li>)}
            </Section>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={onCancel}>取消</Button>
          <Button size="sm" disabled={!canConfirm}
            onClick={() => onConfirm(picked.map(r => r.key),
                                    Object.fromEntries(picked.map(r => [r.key, (labels[r.key] || r.label).trim() || r.label])),
                                    withButtons)}>
            {!canConfirm
              ? '没有要补的内容'
              : picked.length
                ? `补齐 ${newParams} 个参数并放置 ${newControls} 个控件${newButtons ? ` + ${newButtons} 个按钮` : ''}`
                : `放置「${plan.addButtons.map(b => b.label).join('」「')}」按钮`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Section({ title, tone, children }: { title: string; tone: 'warn' | 'muted'; children: React.ReactNode }) {
  return (
    <div className={`rounded-md border px-3 py-2 text-[11px] ${tone === 'warn' ? 'border-amber-300 bg-amber-50/60 text-amber-900' : 'border-border bg-muted/30 text-muted-foreground'}`}>
      <div className="mb-1 font-medium">{title}</div>
      <ul className="list-disc space-y-0.5 pl-4">{children}</ul>
    </div>
  );
}

/** 行的动作文案：让用户看清"参数"和"控件"分别是什么状态，而不是一个笼统的"自动补齐" */
function buildRows(plan: AssemblyPlan): Row[] {
  const rows: Row[] = [];
  const controlByKey = new Map(plan.addControls.map(c => [c.key, c]));

  for (const c of plan.addControls) {
    const param = plan.addParams.find(p => p.name === c.key);
    const reused = plan.reuseParams.find(p => p.key === c.key);
    const mismatch = plan.mismatchParams.find(p => p.key === c.key);
    rows.push({
      key: c.key, label: c.label, valueType: c.value_type, datasets: c.datasetNames,
      needsControl: true, createsParam: !!param,
      action: `${param ? '新建参数' : reused ? '复用现有参数' : '保持现有参数'} + 放置「${CONTROL_LABELS[c.control] || c.control}」`
        + (c.options.length ? `（候选 ${c.options.length} 项）` : '')
        + (mismatch ? `；⚠ 现有类型 ${mismatch.current} 与需要的 ${mismatch.want} 不一致，确认后请在参数里改判` : ''),
    });
  }
  // 只复用不新放控件的参数也要出现在清单里，否则用户以为"补了参数"其实没补
  for (const p of plan.addParams) {
    if (controlByKey.has(p.name)) continue;
    rows.push({ key: p.name, label: p.label, valueType: p.type, datasets: [],
                needsControl: false, createsParam: true, action: '新建参数（不放置控件）' });
  }
  for (const m of plan.mismatchParams) {
    if (controlByKey.has(m.key)) continue;
    rows.push({ key: m.key, label: m.label, valueType: pageParamTypeOf(m.want), datasets: m.datasetNames,
                needsControl: false, createsParam: false,
                action: `现有参数类型是 ${m.current}，数据集要的是 ${m.want}` });
  }
  return rows;
}
