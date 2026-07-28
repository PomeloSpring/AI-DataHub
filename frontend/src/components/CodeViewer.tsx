import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Play, X, Copy, Check, Package, Code2 } from 'lucide-react';

interface CodeViewerProps {
  code: string;
  description: string;
  requirements?: string[];
  onExecute: () => void;
  onCancel: () => void;
}

export default function CodeViewer({ code, description, requirements, onExecute, onCancel }: CodeViewerProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="rounded-lg border bg-card overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-muted/50 border-b">
        <div className="flex items-center gap-2">
          <Code2 className="h-4 w-4 text-primary" />
          <span className="text-sm font-medium">Python 代码</span>
          {description && (
            <Badge variant="secondary" className="text-xs font-normal">
              {description}
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={handleCopy} className="h-7 px-2">
            {copied ? <Check className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5" />}
          </Button>
        </div>
      </div>

      {/* Code */}
      <div className="relative">
        <pre className="p-4 overflow-x-auto text-sm leading-relaxed bg-[#1e1e1e] text-[#d4d4d4] max-h-[400px] overflow-y-auto">
          <code>{code}</code>
        </pre>
      </div>

      {/* Requirements */}
      {requirements && requirements.length > 0 && (
        <div className="px-4 py-2 border-t bg-muted/30 flex items-center gap-2">
          <Package className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-xs text-muted-foreground">依赖:</span>
          {requirements.map((pkg, i) => (
            <Badge key={i} variant="outline" className="text-xs">{pkg}</Badge>
          ))}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center justify-end gap-2 px-4 py-3 border-t bg-muted/30">
        <Button variant="outline" size="sm" onClick={onCancel}>
          <X className="h-4 w-4 mr-1" />
          取消
        </Button>
        <Button size="sm" onClick={onExecute}>
          <Play className="h-4 w-4 mr-1" />
          确认执行
        </Button>
      </div>
    </div>
  );
}
