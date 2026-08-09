import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { CheckCircle, XCircle, Clock, Play, Copy, Check, ChevronDown, ChevronUp, Terminal } from 'lucide-react';
import type { ExecutionResult } from '@/stores/chatStore';

interface ExecutionResultCardProps {
  result: ExecutionResult;
  onRerun?: (code: string, requirements?: string[]) => void;
}

export default function ExecutionResultCard({ result, onRerun }: ExecutionResultCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(result.code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const hasOutput = result.stdout || result.stderr || result.error;

  return (
    <div className="rounded-lg border bg-card overflow-hidden mt-2">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-muted/50 border-b">
        <div className="flex items-center gap-2">
          <Terminal className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm font-medium">代码执行</span>
          {result.description && (
            <Badge variant="secondary" className="text-xs">{result.description}</Badge>
          )}
          {result.success ? (
            <Badge variant="default" className="text-xs bg-green-500 hover:bg-green-600">
              <CheckCircle className="h-3 w-3 mr-1" /> 成功
            </Badge>
          ) : (
            <Badge variant="destructive" className="text-xs">
              <XCircle className="h-3 w-3 mr-1" /> 失败
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-1">
          <span className="text-xs text-muted-foreground">{result.sandbox_name}</span>
          {result.elapsed_ms && (
            <span className="text-xs text-muted-foreground">
              <Clock className="h-3 w-3 inline mr-0.5" />
              {result.elapsed_ms}ms
            </span>
          )}
          <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={handleCopy}>
            {copied ? <Check className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
          </Button>
          {onRerun && (
            <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => onRerun(result.code, result.requirements)} title="重新执行">
              <Play className="h-3 w-3" />
            </Button>
          )}
          <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => setExpanded(!expanded)}>
            {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </Button>
        </div>
      </div>

      {/* Code preview (collapsed) */}
      {!expanded && (
        <div className="px-3 py-2">
          <pre className="text-xs text-muted-foreground bg-muted/30 rounded p-2 overflow-x-auto max-h-20">
            {result.code.slice(0, 200)}{result.code.length > 200 ? '...' : ''}
          </pre>
        </div>
      )}

      {/* Expanded view */}
      {expanded && (
        <>
          {/* Code */}
          <div className="px-3 py-2 border-b">
            <pre className="text-xs bg-[#1e1e1e] text-[#d4d4d4] rounded p-3 overflow-x-auto max-h-[300px] overflow-y-auto">
              <code>{result.code}</code>
            </pre>
          </div>

          {/* Requirements */}
          {result.requirements && result.requirements.length > 0 && (
            <div className="px-3 py-1.5 border-b bg-muted/30 flex items-center gap-2">
              <span className="text-xs text-muted-foreground">依赖:</span>
              {result.requirements.map((pkg, i) => (
                <Badge key={i} variant="outline" className="text-xs">{pkg}</Badge>
              ))}
            </div>
          )}

          {/* Output */}
          {hasOutput && (
            <div className="px-3 py-2 space-y-2">
              {result.stdout && (
                <div>
                  <div className="text-xs text-muted-foreground mb-1">stdout:</div>
                  <pre className="text-xs bg-black/10 rounded p-2 overflow-x-auto max-h-[200px] overflow-y-auto whitespace-pre-wrap">
                    {result.stdout}
                  </pre>
                </div>
              )}
              {result.stderr && (
                <div>
                  <div className="text-xs text-red-500 mb-1">stderr:</div>
                  <pre className="text-xs bg-red-500/10 rounded p-2 overflow-x-auto max-h-[200px] overflow-y-auto whitespace-pre-wrap text-red-400">
                    {result.stderr}
                  </pre>
                </div>
              )}
              {result.error && (
                <div>
                  <div className="text-xs text-red-500 mb-1">错误:</div>
                  <pre className="text-xs bg-red-500/10 rounded p-2 overflow-x-auto max-h-[200px] overflow-y-auto whitespace-pre-wrap text-red-400">
                    {result.error}
                  </pre>
                </div>
              )}
              {result.result && (
                <div>
                  <div className="text-xs text-muted-foreground mb-1">返回值:</div>
                  <pre className="text-xs bg-muted/30 rounded p-2 overflow-x-auto whitespace-pre-wrap">
                    {result.result}
                  </pre>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
