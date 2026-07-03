import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { StopCircle } from 'lucide-react';

function EndNode({ data }: NodeProps) {
  return (
    <div className="px-4 py-3 rounded-lg border-2 border-red-500/30 bg-red-500/10 shadow-md min-w-[140px]">
      <Handle type="target" position={Position.Top} className="!bg-red-500 !w-3 !h-3" />
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-full bg-red-500/20 flex items-center justify-center">
          <StopCircle className="h-4 w-4 text-red-500" />
        </div>
        <div>
          <div className="font-medium text-sm">{(data as any).label || '结束'}</div>
          <div className="text-xs text-muted-foreground">工作流出口</div>
        </div>
      </div>
    </div>
  );
}

export default memo(EndNode);
