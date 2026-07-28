import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Merge } from 'lucide-react';

function MergeNode({ data, selected }: NodeProps) {
  const d = data as any;
  const inputCount = d.config?.input_count || 3;

  return (
    <div
      className={`relative px-4 py-3 rounded-lg border-2 border-cyan-500/30 bg-cyan-500/10 shadow-md min-w-[160px] ${
        selected ? 'ring-2 ring-primary ring-offset-2' : ''
      }`}
    >
      <div className="flex justify-around mb-1">
        {Array.from({ length: inputCount }, (_, i) => (
          <Handle
            key={i}
            type="target"
            position={Position.Top}
            id={`input_${i + 1}`}
            className="!bg-cyan-500 !w-3 !h-3"
            style={{ left: `${((i + 1) / (inputCount + 1)) * 100}%` }}
          />
        ))}
      </div>
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-lg bg-cyan-500/20 flex items-center justify-center">
          <Merge className="h-4 w-4 text-cyan-500" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-medium text-sm truncate">{d.label || '合并'}</div>
          <div className="text-xs text-muted-foreground">合并 {inputCount} 路结果</div>
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-cyan-500 !w-3 !h-3" />
    </div>
  );
}

export default memo(MergeNode);
