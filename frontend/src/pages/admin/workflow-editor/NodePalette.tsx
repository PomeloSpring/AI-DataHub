import { Play, StopCircle, Box, GitBranch, GitMerge, Merge, Bot, Wrench } from 'lucide-react';

const NODE_PALETTE_ITEMS = [
  { type: 'start', label: '开始', icon: Play, color: 'text-green-500', bg: 'bg-green-500/10', border: 'border-green-500/30' },
  { type: 'end', label: '结束', icon: StopCircle, color: 'text-red-500', bg: 'bg-red-500/10', border: 'border-red-500/30' },
  { type: 'step', label: '处理步骤', icon: Box, color: 'text-blue-500', bg: 'bg-blue-500/10', border: 'border-blue-500/30' },
  { type: 'condition', label: '条件判断', icon: GitBranch, color: 'text-yellow-500', bg: 'bg-yellow-500/10', border: 'border-yellow-500/30' },
  { type: 'parallel', label: '并行执行', icon: GitMerge, color: 'text-purple-500', bg: 'bg-purple-500/10', border: 'border-purple-500/30' },
  { type: 'merge', label: '合并', icon: Merge, color: 'text-cyan-500', bg: 'bg-cyan-500/10', border: 'border-cyan-500/30' },
  { type: 'agent', label: 'Agent', icon: Bot, color: 'text-violet-500', bg: 'bg-violet-500/10', border: 'border-violet-500/30' },
  { type: 'mcp_tool', label: 'MCP工具', icon: Wrench, color: 'text-orange-500', bg: 'bg-orange-500/10', border: 'border-orange-500/30' },
];

interface NodePaletteProps {
  onDragStart: (event: React.DragEvent, nodeType: string) => void;
}

export default function NodePalette({ onDragStart }: NodePaletteProps) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium text-muted-foreground px-1">节点面板</h3>
      <div className="grid grid-cols-2 gap-2">
        {NODE_PALETTE_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <div
              key={item.type}
              className={`flex flex-col items-center gap-1.5 p-3 rounded-lg border ${item.border} ${item.bg} cursor-grab hover:shadow-md transition-shadow active:cursor-grabbing`}
              draggable
              onDragStart={(e) => onDragStart(e, item.type)}
            >
              <Icon className={`h-5 w-5 ${item.color}`} />
              <span className="text-xs font-medium">{item.label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
