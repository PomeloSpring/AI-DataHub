import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Play } from 'lucide-react';

function StartNode({ data }: NodeProps) {
  return (
    <div className="px-4 py-3 rounded-lg border-2 border-green-500/30 bg-green-500/10 shadow-md min-w-[140px]">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-full bg-green-500/20 flex items-center justify-center">
          <Play className="h-4 w-4 text-green-500" />
        </div>
        <div>
          <div className="font-medium text-sm">{(data as any).label || '开始'}</div>
          <div className="text-xs text-muted-foreground">工作流入口</div>
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-green-500 !w-3 !h-3" />
    </div>
  );
}

export default memo(StartNode);
