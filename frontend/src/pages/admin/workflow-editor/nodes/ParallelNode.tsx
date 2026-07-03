import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { GitMerge } from 'lucide-react';

function ParallelNode({ data, selected }: NodeProps) {
  const d = data as any;
  const branchCount = d.config?.parallel_count || 3;

  return (
    <div
      className={`relative px-4 py-3 rounded-lg border-2 border-purple-500/30 bg-purple-500/10 shadow-md min-w-[180px] ${
        selected ? 'ring-2 ring-primary ring-offset-2' : ''
      }`}
    >
      <Handle type="target" position={Position.Top} className="!bg-purple-500 !w-3 !h-3" />
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-lg bg-purple-500/20 flex items-center justify-center">
          <GitMerge className="h-4 w-4 text-purple-500" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-medium text-sm truncate">{d.label || '并行执行'}</div>
          <div className="text-xs text-muted-foreground">{branchCount} 路并行</div>
        </div>
      </div>
      <div className="flex justify-around mt-2">
        {Array.from({ length: branchCount }, (_, i) => (
          <div key={i} className="flex flex-col items-center">
            <Handle
              type="source"
              position={Position.Bottom}
              id={`branch_${i + 1}`}
              className="!bg-purple-500 !w-3 !h-3"
              style={{ left: `${((i + 1) / (branchCount + 1)) * 100}%` }}
            />
            <span className="text-xs text-muted-foreground mt-1">{i + 1}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default memo(ParallelNode);
