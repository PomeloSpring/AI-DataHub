// ASTTreeView — sqlglot dump(扁平父索引结构)的树形可视化
//
// 后端 /playground/ast 返回 tree.dump(): 扁平数组, 每项
//   { c: 类全名, i: 父节点下标, k: 父的 arg 键, a: 数组元素, v: 叶子值, m: {line,col} 源码位置 }
// 本组件重建层级并渲染为可折叠节点树, 按表达式类别着色, 不再直接展示 JSON。

import { useMemo, useState } from 'react';
import { ChevronRight, ChevronDown, FileCode2 } from 'lucide-react';

interface DumpNode {
  idx: number;
  cls: string;          // 短类名, 如 Select/Column; 叶子值节点为 ''
  argKey?: string;
  isArray?: boolean;
  value?: unknown;      // 叶子值
  pos?: { line: number; col: number };
  children: DumpNode[];
}

const shortClass = (full: string) => full.split('.').pop() || full;

/** 扁平 dump -> 树. 兼容异常输入: 返回 null 由调用方回退 JSON 视图。 */
export function buildAstTree(dump: unknown): DumpNode | null {
  if (!Array.isArray(dump) || dump.length === 0) return null;
  const nodes: DumpNode[] = dump.map((e: any, idx: number) => ({
    idx,
    cls: typeof e?.c === 'string' ? shortClass(e.c) : '',
    argKey: e?.k,
    isArray: !!e?.a,
    value: e?.v,
    pos: e?.m,
    children: [],
  }));
  let root: DumpNode | null = null;
  for (let i = 0; i < dump.length; i++) {
    const e = dump[i] as any;
    if (typeof e?.i === 'number' && nodes[e.i]) {
      nodes[e.i].children.push(nodes[i]);
    } else if (!root) {
      root = nodes[i]; // 无 i 的视为根(正常仅第 0 项)
    }
  }
  return root;
}

// 类名 -> 颜色 (chip 底色/边色), 未命中走默认
const CLASS_COLORS: Record<string, string> = {
  Select: 'bg-blue-100 text-blue-700 border-blue-300 dark:bg-blue-900/40 dark:text-blue-300 dark:border-blue-700',
  From: 'bg-indigo-100 text-indigo-700 border-indigo-300 dark:bg-indigo-900/40 dark:text-indigo-300 dark:border-indigo-700',
  Join: 'bg-indigo-100 text-indigo-700 border-indigo-300 dark:bg-indigo-900/40 dark:text-indigo-300 dark:border-indigo-700',
  Where: 'bg-amber-100 text-amber-700 border-amber-300 dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-700',
  Group: 'bg-amber-100 text-amber-700 border-amber-300 dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-700',
  Having: 'bg-amber-100 text-amber-700 border-amber-300 dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-700',
  Order: 'bg-amber-100 text-amber-700 border-amber-300 dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-700',
  Limit: 'bg-amber-100 text-amber-700 border-amber-300 dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-700',
  Table: 'bg-orange-100 text-orange-700 border-orange-300 dark:bg-orange-900/40 dark:text-orange-300 dark:border-orange-700',
  Column: 'bg-emerald-100 text-emerald-700 border-emerald-300 dark:bg-emerald-900/40 dark:text-emerald-300 dark:border-emerald-700',
  Identifier: 'bg-slate-100 text-slate-600 border-slate-300 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-600',
  Alias: 'bg-cyan-100 text-cyan-700 border-cyan-300 dark:bg-cyan-900/40 dark:text-cyan-300 dark:border-cyan-700',
  Subquery: 'bg-violet-100 text-violet-700 border-violet-300 dark:bg-violet-900/40 dark:text-violet-300 dark:border-violet-700',
  // 谓词/运算
  EQ: 'bg-rose-100 text-rose-700 border-rose-300 dark:bg-rose-900/40 dark:text-rose-300 dark:border-rose-700',
  NEQ: 'bg-rose-100 text-rose-700 border-rose-300 dark:bg-rose-900/40 dark:text-rose-300 dark:border-rose-700',
  GT: 'bg-rose-100 text-rose-700 border-rose-300 dark:bg-rose-900/40 dark:text-rose-300 dark:border-rose-700',
  GTE: 'bg-rose-100 text-rose-700 border-rose-300 dark:bg-rose-900/40 dark:text-rose-300 dark:border-rose-700',
  LT: 'bg-rose-100 text-rose-700 border-rose-300 dark:bg-rose-900/40 dark:text-rose-300 dark:border-rose-700',
  LTE: 'bg-rose-100 text-rose-700 border-rose-300 dark:bg-rose-900/40 dark:text-rose-300 dark:border-rose-700',
  Like: 'bg-rose-100 text-rose-700 border-rose-300 dark:bg-rose-900/40 dark:text-rose-300 dark:border-rose-700',
  In: 'bg-rose-100 text-rose-700 border-rose-300 dark:bg-rose-900/40 dark:text-rose-300 dark:border-rose-700',
  Between: 'bg-rose-100 text-rose-700 border-rose-300 dark:bg-rose-900/40 dark:text-rose-300 dark:border-rose-700',
  And: 'bg-pink-100 text-pink-700 border-pink-300 dark:bg-pink-900/40 dark:text-pink-300 dark:border-pink-700',
  Or: 'bg-pink-100 text-pink-700 border-pink-300 dark:bg-pink-900/40 dark:text-pink-300 dark:border-pink-700',
  Not: 'bg-pink-100 text-pink-700 border-pink-300 dark:bg-pink-900/40 dark:text-pink-300 dark:border-pink-700',
  // 字面量
  Literal: 'bg-green-50 text-green-700 border-green-300 dark:bg-green-900/30 dark:text-green-300 dark:border-green-700',
  Number: 'bg-green-50 text-green-700 border-green-300 dark:bg-green-900/30 dark:text-green-300 dark:border-green-700',
  String: 'bg-green-50 text-green-700 border-green-300 dark:bg-green-900/30 dark:text-green-300 dark:border-green-700',
};
const FUNC_SUFFIXES = ['Sum', 'Count', 'Avg', 'Min', 'Max', 'Func', 'Anonymous'];
const colorOf = (cls: string) => {
  if (CLASS_COLORS[cls]) return CLASS_COLORS[cls];
  if (FUNC_SUFFIXES.some((s) => cls.includes(s)))
    return 'bg-purple-100 text-purple-700 border-purple-300 dark:bg-purple-900/40 dark:text-purple-300 dark:border-purple-700';
  return 'bg-slate-100 text-slate-600 border-slate-300 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-600';
};

/** 该节点值得内联展示的名字: this 子节点是叶子时取其值 (Identifier/Literal 等)。 */
const inlineName = (n: DumpNode): string | null => {
  const t = n.children.find((c) => c.argKey === 'this' && !c.cls && c.value != null);
  if (t && typeof t.value === 'string') return t.value;
  return null;
};

function NodeRow({ node, depth, defaultOpen, expandSignal }: {
  node: DumpNode; depth: number; defaultOpen: boolean;
  expandSignal: { open: boolean | null; seq: number };
}) {
  const [open, setOpen] = useState(defaultOpen);
  // 工具栏 展开全部/收起全部: seq 变化时按信号覆盖
  const [lastSeq, setLastSeq] = useState(0);
  if (expandSignal.seq !== lastSeq) {
    setLastSeq(expandSignal.seq);
    if (expandSignal.open !== null) setOpen(expandSignal.open);
  }
  const branchKids = node.children.filter((c) => c.cls);
  const leafKids = node.children.filter((c) => !c.cls);
  const name = inlineName(node);
  const hasKids = branchKids.length > 0;

  return (
    <div style={{ marginLeft: depth === 0 ? 0 : 14 }} className={depth > 0 ? 'border-l border-border/60 pl-1.5' : ''}>
      <div
        className={`flex items-center gap-1.5 py-[3px] pr-2 rounded hover:bg-muted/60 ${hasKids ? 'cursor-pointer' : ''}`}
        onClick={() => hasKids && setOpen((v) => !v)}
        title={node.pos ? `源码位置 L${node.pos.line}:${node.pos.col}` : undefined}
      >
        {hasKids ? (open ? <ChevronDown size={13} className="shrink-0 text-muted-foreground" /> : <ChevronRight size={13} className="shrink-0 text-muted-foreground" />)
          : <span className="w-[13px] shrink-0" />}
        {node.argKey && (
          <span className="text-[10px] text-muted-foreground font-mono shrink-0">
            {node.argKey}{node.isArray ? '[]' : ''}
          </span>
        )}
        <span className={`text-[11px] font-medium px-1.5 py-px rounded border leading-4 shrink-0 ${colorOf(node.cls)}`}>
          {node.cls}
        </span>
        {name && <span className="text-xs font-mono text-foreground/80 truncate max-w-[220px]">{name}</span>}
        {/* 叶子属性内联成 key=value chips */}
        <span className="flex flex-wrap gap-1">
          {leafKids.filter((c) => c.argKey !== 'this').map((c) => (
            <span key={c.idx} className="text-[10px] font-mono text-muted-foreground bg-muted/70 rounded px-1 leading-4">
              {c.argKey}={typeof c.value === 'string' ? c.value : String(c.value)}
            </span>
          ))}
        </span>
        {node.pos && <span className="text-[10px] text-muted-foreground/70 font-mono ml-auto shrink-0">L{node.pos.line}:{node.pos.col}</span>}
      </div>
      {open && branchKids.map((c) => (
        <NodeRow key={c.idx} node={c} depth={depth + 1} defaultOpen={depth + 1 < 3} expandSignal={expandSignal} />
      ))}
    </div>
  );
}

export default function ASTTreeView({ dump }: { dump: unknown }) {
  const root = useMemo(() => buildAstTree(dump), [dump]);
  const [expandSignal, setExpandSignal] = useState<{ open: boolean | null; seq: number }>({ open: null, seq: 0 });
  if (!root) {
    return <p className="text-xs text-muted-foreground">AST 数据为空或格式不支持树视图。</p>;
  }
  const nodeCount = Array.isArray(dump) ? dump.length : 0;
  return (
    <div className="text-sm">
      <div className="flex items-center gap-2 mb-1.5 text-xs text-muted-foreground">
        <FileCode2 size={13} />
        <span>{nodeCount} 个节点</span>
        <button type="button" className="ml-auto underline-offset-2 hover:underline"
          onClick={() => setExpandSignal((s) => ({ open: true, seq: s.seq + 1 }))}>展开全部</button>
        <button type="button" className="underline-offset-2 hover:underline"
          onClick={() => setExpandSignal((s) => ({ open: false, seq: s.seq + 1 }))}>收起全部</button>
      </div>
      <NodeRow node={root} depth={0} defaultOpen expandSignal={expandSignal} />
    </div>
  );
}
