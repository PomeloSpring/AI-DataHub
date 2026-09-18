import { Check, X, Loader2, AlertTriangle, ShieldCheck, FileText, Boxes } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import type { ApprovalRequest } from '../../stores/asBotStore';

interface ApprovalCardProps {
  approval: ApprovalRequest;
  status?: 'pending' | 'approved' | 'rejected' | 'executed' | 'failed';
  result?: any;
  onApprove: () => void;
  onReject: () => void;
}

const ACTION_ICONS: Record<string, any> = {
  'ontology.generate': Boxes,
  'ontology.save': FileText,
  'ontology.activate': ShieldCheck,
  'ontology.import_yaml': FileText,
  'metadata.sync': Loader2,
};

const STATUS_CONFIG: Record<string, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline'; icon: any; color: string }> = {
  pending: { label: '待审批', variant: 'outline', icon: AlertTriangle, color: 'text-yellow-500' },
  approved: { label: '已批准', variant: 'default', icon: Check, color: 'text-green-500' },
  rejected: { label: '已拒绝', variant: 'secondary', icon: X, color: 'text-muted-foreground' },
  executed: { label: '已执行', variant: 'default', icon: Check, color: 'text-green-500' },
  failed: { label: '执行失败', variant: 'destructive', icon: X, color: 'text-destructive' },
};

export default function ApprovalCard({ approval, status = 'pending', result, onApprove, onReject }: ApprovalCardProps) {
  const Icon = ACTION_ICONS[approval.action_key] || ShieldCheck;
  const statusCfg = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
  const StatusIcon = statusCfg.icon;

  return (
    <div className="border rounded-lg p-3 bg-card space-y-2">
      {/* Header */}
      <div className="flex items-center gap-2">
        <Icon className={`h-4 w-4 ${statusCfg.color}`} />
        <span className="text-sm font-medium flex-1">{approval.action_label}</span>
        <Badge variant={statusCfg.variant as any} className="text-xs">
          <StatusIcon className={`h-3 w-3 mr-1 ${statusCfg.color}`} />
          {statusCfg.label}
        </Badge>
      </div>

      {/* Description */}
      <p className="text-xs text-muted-foreground leading-relaxed">
        {approval.description}
      </p>

      {/* Payload preview (collapsed) */}
      {approval.payload && Object.keys(approval.payload).length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
            操作参数
          </summary>
          <pre className="mt-1 p-2 bg-muted rounded text-[10px] overflow-auto max-h-32 font-mono">
            {JSON.stringify(approval.payload, null, 2)}
          </pre>
        </details>
      )}

      {/* Result (after execution) */}
      {result && status !== 'pending' && status !== 'rejected' && (
        <div className={`text-xs p-2 rounded ${result.success ? 'bg-green-500/10 text-green-700 dark:text-green-400' : 'bg-destructive/10 text-destructive'}`}>
          {result.success
            ? `执行成功${result.model_id ? ` (模型 #${result.model_id})` : ''}`
            : `失败: ${result.error || '未知错误'}`}
        </div>
      )}

      {/* Action buttons (only when pending) */}
      {status === 'pending' && (
        <div className="flex gap-2 pt-1">
          <Button
            size="sm"
            variant="default"
            className="h-7 text-xs flex-1"
            onClick={onApprove}
          >
            <Check className="h-3 w-3 mr-1" />
            批准执行
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs flex-1"
            onClick={onReject}
          >
            <X className="h-3 w-3 mr-1" />
            拒绝
          </Button>
        </div>
      )}
    </div>
  );
}
