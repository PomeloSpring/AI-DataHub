import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { GitBranch } from 'lucide-react';

function ConditionNode({ data, selected }: NodeProps) {
  const d = data as any;

  return (
    <div
      className={`relative px-4 py-3 rounded-lg border-2 border-yellow-500/30 bg-yellow-500/10 shadow-md min-w-[160px] ${
        selected ? 'ring-2 ring-primary ring-offset-2' : ''
      }`}
    >
      <Handle type="target" position={Position.Top} className="!bg-yellow-500 !w-3 !h-3" />
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-lg bg-yellow-500/20 flex items-center justify-center">
          <GitBranch className="h-4 w-4 text-yellow-500" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-medium text-sm truncate">{d.label || '条件判断'}</div>
          <div className="text-xs text-muted-foreground truncate">
            {d.condition_expr || '设置条件'}
          </div>
        </div>
      </div>
      <div className="flex justify-between mt-2">
        <Handle
          type="source"
          position={Position.Bottom}
          id="true"
          className="!bg-green-500 !w-3 !h-3"
          style={{ left: '30%' }}
        />
        <Handle
          type="source"
          position={Position.Bottom}
          id="false"
          className="!bg-red-500 !w-3 !h-3"
          style={{ left: '70%' }}
        />
      </div>
      <div className="flex justify-between text-xs text-muted-foreground mt-1">
        <span className="text-green-500">是</span>
        <span className="text-red-500">否</span>
      </div>
    </div>
  );
}

export default memo(ConditionNode);
