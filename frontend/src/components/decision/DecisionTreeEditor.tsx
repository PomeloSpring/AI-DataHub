import { useState } from 'react';
import { Plus, Trash2, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import DictTermSelect from '@/components/decision/DictTermSelect';
import type {
  DecisionConditionInput, DecisionAdviceRow, DecisionTreeDocInput, DecisionTreeNode,
} from '@/api/decisions';

/**
 * 决策树条件树编辑器（结构化表单形态）。
 *
 * 契约纪律在这里就落一遍，不靠后端报错兜：
 * - 只允许 branch(all/any) + leaf 两种节点，**没有自由表达式输入口**（表达式 = 绕过
 *   字典口径的第二套判断，plan §二 双门禁）；
 * - 指标/维度一律从字典选（`DictTermSelect`），建议动作从文案库选（同一文案不在多棵
 *   树里复制）；
 * - 阈值是常量或"另一指标"，二者互斥（后端契约同样 forbid）。
 *
 * P2 会在旁边并上 ReactFlow 图面视图（复用图可视化基准）；两种视图共用这一份
 * `tree_doc`，不做两套状态。
 */

const COMPARATORS = ['=', '!=', '>', '>=', '<', '<=', 'between', 'in', 'not_in'];

export function emptyLeaf(): DecisionTreeNode {
  return {
    type: 'leaf', label: '', advice_key: '', severity: '',
    when: { metric: '', comparator: '>', value: null },
  };
}

export function emptyBranch(): DecisionTreeNode {
  return { type: 'branch', label: '', op: 'all', children: [emptyLeaf()] };
}

export default function DecisionTreeEditor({
  value, onChange, adviceRows, readOnly,
}: {
  value: DecisionTreeDocInput;
  onChange: (next: DecisionTreeDocInput) => void;
  adviceRows: DecisionAdviceRow[];
  readOnly?: boolean;
}) {
  const root = value.root || emptyBranch();
  const patchRoot = (next: DecisionTreeNode) =>
    onChange({ ...value, root: next as any });

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Input
          aria-label="判定名称"
          value={root.label ?? ''}
          disabled={readOnly}
          placeholder="这棵树在判什么（如：精度风险判定）"
          onChange={(e) => patchRoot({ ...root, label: e.target.value } as any)}
          className="h-8 text-sm font-medium"
        />
      </div>
      <NodeEditor
        node={root}
        adviceRows={adviceRows}
        readOnly={readOnly}
        depth={0}
        onChange={patchRoot}
      />
    </div>
  );
}

function NodeEditor({
  node, adviceRows, readOnly, depth, onChange,
}: {
  node: DecisionTreeNode;
  adviceRows: DecisionAdviceRow[];
  readOnly?: boolean;
  depth: number;
  onChange: (next: DecisionTreeNode) => void;
}) {
  const [open, setOpen] = useState(true);

  if (node.type === 'leaf') {
    return <LeafEditor leaf={node} adviceRows={adviceRows} readOnly={readOnly} onChange={onChange} />;
  }

  const branch = node;
  const patchChild = (idx: number, next: DecisionTreeNode) => {
    const children = branch.children.map((c, i) => (i === idx ? next : c));
    onChange({ ...branch, children } as DecisionTreeNode);
  };
  const removeChild = (idx: number) => {
    const children = branch.children.filter((_, i) => i !== idx);
    onChange({ ...branch, children: children.length ? children : [emptyLeaf()] } as DecisionTreeNode);
  };
  const addChild = (kind: 'leaf' | 'branch') => {
    onChange({
      ...branch,
      children: [...branch.children, kind === 'leaf' ? emptyLeaf() : emptyBranch()],
    } as DecisionTreeNode);
  };

  return (
    <div className="rounded-lg border border-border/70 bg-muted/20 p-2">
      <div className="flex items-center gap-2">
        <button type="button" onClick={() => setOpen(o => !o)} aria-label="展开/收起"
          className="text-muted-foreground">
          <ChevronRight className={`h-4 w-4 transition-transform ${open ? 'rotate-90' : ''}`} />
        </button>
        <Input
          aria-label="分支名称"
          value={branch.label}
          disabled={readOnly}
          placeholder="分支名称（如：先看校准，再看精度）"
          onChange={(e) => onChange({ ...branch, label: e.target.value } as DecisionTreeNode)}
          className="h-8 flex-1 text-sm"
        />
        <select
          aria-label="组合方式"
          value={branch.op}
          disabled={readOnly}
          onChange={(e) => onChange({ ...branch, op: e.target.value as 'all' | 'any' } as DecisionTreeNode)}
          className="h-8 rounded-md border border-border bg-background px-2 text-xs">
          <option value="all">全部满足（且）</option>
          <option value="any">任一满足（或）</option>
        </select>
      </div>

      {open && (
        <div className="mt-2 space-y-2 pl-4">
          {branch.children.map((child, i) => (
            <div key={i} className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <NodeEditor node={child} adviceRows={adviceRows} readOnly={readOnly}
                  depth={depth + 1} onChange={(next) => patchChild(i, next)} />
              </div>
              {!readOnly && (
                <Button size="sm" variant="ghost" aria-label={`删除第 ${i + 1} 项`}
                  onClick={() => removeChild(i)}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              )}
            </div>
          ))}
          {!readOnly && (
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => addChild('leaf')}>
                <Plus className="h-4 w-4" /><span className="ml-1">加判定条件</span>
              </Button>
              <Button size="sm" variant="outline" onClick={() => addChild('branch')}>
                <Plus className="h-4 w-4" /><span className="ml-1">加分支</span>
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** 叶子明细表单：表单视图与图面详情侧栏共用这一份（两套视图一份契约，不各写一遍）。 */
export function LeafEditor({
  leaf, adviceRows, readOnly, onChange,
}: {
  leaf: Extract<DecisionTreeNode, { type: 'leaf' }>;
  adviceRows: DecisionAdviceRow[];
  readOnly?: boolean;
  onChange: (next: DecisionTreeNode) => void;
}) {
  const cond = leaf.when;
  const patchCond = (next: Partial<DecisionConditionInput>) =>
    onChange({ ...leaf, when: { ...cond, ...next } } as DecisionTreeNode);
  const useRefMode = !!cond.threshold_ref;

  return (
    <div className="rounded-lg border border-border bg-card p-3 space-y-2" aria-label="判定条件">
      <Input
        aria-label="情形描述"
        value={leaf.label}
        disabled={readOnly}
        placeholder="这条判定说的是什么情形（如：校准超差）"
        onChange={(e) => onChange({ ...leaf, label: e.target.value } as DecisionTreeNode)}
        className="h-8 text-sm"
      />
      <div className="grid gap-2 md:grid-cols-[1.4fr_1.4fr_0.7fr_1fr]">
        <div>
          <label className="text-xs text-muted-foreground">指标</label>
          <DictTermSelect kind="metric" value={cond.metric} disabled={readOnly}
            onChange={(v) => patchCond({ metric: v })} />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">切分维度（可留空）</label>
          <DictTermSelect kind="dimension" value={cond.dimension || ''} disabled={readOnly}
            onChange={(v) => patchCond({ dimension: v })} />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">比较</label>
          <select aria-label="比较符" value={cond.comparator} disabled={readOnly}
            onChange={(e) => patchCond({ comparator: e.target.value })}
            className="mt-1 h-8 w-full rounded-md border border-border bg-background px-2 text-sm">
            {COMPARATORS.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div>
          <label className="text-xs text-muted-foreground">时间窗</label>
          <Input aria-label="相对时间窗" value={cond.time_window || ''} disabled={readOnly}
            placeholder="7d / 24h / 2w / 1M"
            onChange={(e) => patchCond({ time_window: e.target.value || null })}
            className="mt-1 h-8 text-sm" />
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-[180px] flex-1">
          <label className="text-xs text-muted-foreground">
            {useRefMode ? '阈值指标（引用另一指标）' : '阈值常量'}
          </label>
          {useRefMode ? (
            <DictTermSelect kind="metric" value={cond.threshold_ref || ''} disabled={readOnly}
              onChange={(v) => patchCond({ threshold_ref: v })} />
          ) : (
            <Input aria-label="阈值" value={formatThreshold(cond.value)} disabled={readOnly}
              placeholder={cond.comparator === 'between' ? '下界,上界' : cond.comparator === 'in' ? '值1,值2' : '0.05'}
              onChange={(e) => patchCond({ value: parseThreshold(e.target.value, cond.comparator) })}
              className="mt-1 h-8 text-sm" />
          )}
        </div>
        <label className="flex items-center gap-1.5 pb-2 text-xs text-muted-foreground">
          <input type="checkbox" checked={useRefMode} disabled={readOnly}
            onChange={(e) => patchCond(e.target.checked
              ? { threshold_ref: cond.metric, value: null }
              : { threshold_ref: '' })} />
          引用另一指标当阈值
        </label>
        <div className="w-24">
          <label className="text-xs text-muted-foreground">单位</label>
          <Input aria-label="展示单位" value={cond.unit || ''} disabled={readOnly} placeholder="mm"
            onChange={(e) => patchCond({ unit: e.target.value })} className="mt-1 h-8 text-sm" />
        </div>
      </div>

      <div className="grid gap-2 md:grid-cols-[1.6fr_0.8fr]">
        <div>
          <label className="text-xs text-muted-foreground">建议动作（从文案库选）</label>
          <select aria-label="建议动作（从文案库选）" value={leaf.advice_key} disabled={readOnly}
            onChange={(e) => onChange({ ...leaf, advice_key: e.target.value } as DecisionTreeNode)}
            className="mt-1 h-8 w-full rounded-md border border-border bg-background px-2 text-sm">
            <option value="">请选择建议文案</option>
            {adviceRows.map(a => (
              <option key={a.advice_key} value={a.advice_key}>{a.action}（{a.advice_key}）</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs text-muted-foreground">严重级（空=沿用库值）</label>
          <select aria-label="严重级" value={leaf.severity || ''} disabled={readOnly}
            onChange={(e) => onChange({ ...leaf, severity: e.target.value as any } as DecisionTreeNode)}
            className="mt-1 h-8 w-full rounded-md border border-border bg-background px-2 text-sm">
            <option value="">沿用文案库</option>
            {['low', 'medium', 'high', 'critical'].map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      </div>
      <Input aria-label="依据说明" value={leaf.evidence_note || ''} disabled={readOnly}
        placeholder="给人看的依据说明（会出现在建议卡上）"
        onChange={(e) => onChange({ ...leaf, evidence_note: e.target.value } as DecisionTreeNode)}
        className="h-8 text-sm" />
    </div>
  );
}

function formatThreshold(v: any): string {
  if (v === null || v === undefined) return '';
  return Array.isArray(v) ? v.join(',') : String(v);
}

/**
 * 阈值解析：比较符决定形状。解析不出数字时**保留原文**而不是丢成 null ——
 * 让后端契约去拒（宁拒勿静默改写，否则人写的 0.05 被吃掉后树还在但条件已变）。
 */
function parseThreshold(raw: string, comparator: string): any {
  const text = raw.trim();
  if (!text) return null;
  if (comparator === 'between' || comparator === 'in' || comparator === 'not_in') {
    const parts = text.split(',').map(s => s.trim()).filter(Boolean);
    return parts.map(toNumber);
  }
  return toNumber(text);
}

function toNumber(text: string): any {
  const n = Number(text);
  return Number.isNaN(n) ? text : n;
}
