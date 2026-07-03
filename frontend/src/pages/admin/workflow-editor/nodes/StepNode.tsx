import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Box, Cog, Search, Code, PlayCircle, BarChart3, Brain, Wrench, GitBranch, GitMerge, Zap } from 'lucide-react';

const ICON_MAP: Record<string, any> = {
  metadata_retrieval: Search,
  llm_analysis: Brain,
  metadata_supplement: Cog,
  sql_generation: Code,
  sql_execution: PlayCircle,
  result_analysis: BarChart3,
  agent_call: GitBranch,
  mcp_tool: Wrench,
  llm_call: Zap,
  transform: Cog,
  condition: GitBranch,
  parallel: GitMerge,
  merge: GitMerge,
  default: Box,
};

const COLOR_MAP: Record<string, { color: string; bg: string; border: string }> = {
  metadata_retrieval: { color: 'text-blue-500', bg: 'bg-blue-500/10', border: 'border-blue-500/30' },
  llm_analysis: { color: 'text-purple-500', bg: 'bg-purple-500/10', border: 'border-purple-500/30' },
  metadata_supplement: { color: 'text-cyan-500', bg: 'bg-cyan-500/10', border: 'border-cyan-500/30' },
  sql_generation: { color: 'text-green-500', bg: 'bg-green-500/10', border: 'border-green-500/30' },
  sql_execution: { color: 'text-yellow-500', bg: 'bg-yellow-500/10', border: 'border-yellow-500/30' },
  result_analysis: { color: 'text-pink-500', bg: 'bg-pink-500/10', border: 'border-pink-500/30' },
  agent_call: { color: 'text-violet-500', bg: 'bg-violet-500/10', border: 'border-violet-500/30' },
  mcp_tool: { color: 'text-orange-500', bg: 'bg-orange-500/10', border: 'border-orange-500/30' },
  default: { color: 'text-gray-500', bg: 'bg-gray-500/10', border: 'border-gray-500/30' },
};

function StepNode({ data, selected }: NodeProps) {
  const d = data as any;
  const stepType = d.step_type || 'default';
  const Icon = ICON_MAP[stepType] || ICON_MAP.default;
  const colors = COLOR_MAP[stepType] || COLOR_MAP.default;
  const status = d.status || 'idle';

  const statusIndicator: Record<string, JSX.Element | null> = {
    idle: null,
    running: <div className="absolute -top-1 -right-1 w-3 h-3 rounded-full bg-blue-500 animate-pulse" />,
    success: <div className="absolute -top-1 -right-1 w-3 h-3 rounded-full bg-green-500" />,
    error: <div className="absolute -top-1 -right-1 w-3 h-3 rounded-full bg-red-500" />,
  };

  return (
    <div
      className={`relative px-4 py-3 rounded-lg border-2 ${colors.border} ${colors.bg} shadow-md min-w-[160px] transition-all ${
        selected ? 'ring-2 ring-primary ring-offset-2' : ''
      } ${!d.is_enabled ? 'opacity-50' : ''}`}
    >
      <Handle type="target" position={Position.Top} className="!w-3 !h-3" />
      <div className="flex items-center gap-2">
        <div className={`w-8 h-8 rounded-lg ${colors.bg} flex items-center justify-center`}>
          <Icon className={`h-4 w-4 ${colors.color}`} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-medium text-sm truncate">{d.label || d.step_name}</div>
          <div className="text-xs text-muted-foreground truncate">
            {stepType === 'default' ? '处理步骤' : stepType}
          </div>
        </div>
      </div>
      {d.max_rounds && d.max_rounds > 1 && (
        <div className="mt-1 text-xs text-muted-foreground">最大轮次: {d.max_rounds}</div>
      )}
      <Handle type="source" position={Position.Bottom} className="!w-3 !h-3" />
      {statusIndicator[status]}
    </div>
  );
}

export default memo(StepNode);
