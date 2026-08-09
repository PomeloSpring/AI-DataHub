/** MCP 安装进度 Modal — 终端风格实时日志显示 */

import { useEffect, useRef, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Loader2, CheckCircle2, XCircle, Terminal } from 'lucide-react';
import client from '@/api/client';

interface McpInstallProgressProps {
  open: boolean;
  onClose: () => void;
  registryId: number;
  /** 安装请求 payload */
  payload: {
    name?: string;
    env_vars?: Record<string, string>;
    description?: string;
    ssh_config?: Record<string, any>;
  };
  /** 安装成功回调 */
  onInstalled: () => void;
}

type InstallStatus = 'building' | 'success' | 'error' | 'idle';

export function McpInstallProgress({
  open, onClose, registryId, payload, onInstalled,
}: McpInstallProgressProps) {
  const [logs, setLogs] = useState<string[]>([]);
  const [status, setStatus] = useState<InstallStatus>('idle');
  const [errorMsg, setErrorMsg] = useState('');
  const terminalRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 自动滚动到底部
  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
    }
  }, [logs]);

  // 开始安装
  useEffect(() => {
    if (!open || status !== 'idle') return;

    const abort = new AbortController();
    abortRef.current = abort;
    setStatus('building');
    setLogs([]);
    setErrorMsg('');

    const doInstall = async () => {
      try {
        const response = await fetch(
          `${client.defaults.baseURL}/mcp-market/${registryId}/install-stream`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${localStorage.getItem('token') || ''}`,
            },
            body: JSON.stringify(payload),
            signal: abort.signal,
          },
        );

        if (!response.ok) {
          const text = await response.text();
          throw new Error(text || `HTTP ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error('无法读取响应流');

        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          let currentEvent = '';
          for (const line of lines) {
            if (line.startsWith('event: ')) {
              currentEvent = line.slice(7).trim();
            } else if (line.startsWith('data: ')) {
              const dataStr = line.slice(6);
              try {
                const data = JSON.parse(dataStr);

                if (currentEvent === 'log') {
                  const msg = data.message || '';
                  // 检查特殊标记
                  if (msg.startsWith('__DONE__:')) {
                    const doneData = JSON.parse(msg.slice(9));
                    setStatus('success');
                    setLogs(prev => [...prev, `✅ ${doneData.message || '安装成功'}`]);
                    onInstalled();
                  } else if (msg.startsWith('__ERROR__:')) {
                    setStatus('error');
                    setErrorMsg(msg.slice(10));
                    setLogs(prev => [...prev, `❌ ${msg.slice(10)}`]);
                  } else {
                    setLogs(prev => [...prev, msg]);
                  }
                } else if (currentEvent === 'error') {
                  setStatus('error');
                  setErrorMsg(data.message || '安装失败');
                  setLogs(prev => [...prev, `❌ ${data.message || '安装失败'}`]);
                } else if (currentEvent === 'done') {
                  setStatus('success');
                  setLogs(prev => [...prev, `✅ ${data.message || '安装成功'}`]);
                  onInstalled();
                }
              } catch {
                // 非 JSON data，直接显示
                if (currentEvent === 'log') {
                  setLogs(prev => [...prev, dataStr]);
                }
              }
            }
          }
        }

        // 流结束但没有收到 done/error 事件
        if (status === 'building') {
          setStatus('success');
          onInstalled();
        }
      } catch (err: any) {
        if (err.name === 'AbortError') return;
        setStatus('error');
        setErrorMsg(err.message || '安装失败');
        setLogs(prev => [...prev, `❌ ${err.message || '安装失败'}`]);
      }
    };

    doInstall();

    return () => { abort.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const handleClose = () => {
    if (status === 'building') {
      abortRef.current?.abort();
    }
    onClose();
  };

  return (
    <Dialog open={open} onOpenChange={v => !v && handleClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Terminal className="h-5 w-5" />
            MCP 服务安装
            {status === 'building' && (
              <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
            )}
            {status === 'success' && (
              <CheckCircle2 className="h-4 w-4 text-green-500" />
            )}
            {status === 'error' && (
              <XCircle className="h-4 w-4 text-red-500" />
            )}
          </DialogTitle>
        </DialogHeader>

        {/* 终端日志区域 */}
        <div
          ref={terminalRef}
          className="bg-gray-900 text-green-400 font-mono text-xs p-4 rounded-lg h-80 overflow-y-auto whitespace-pre-wrap break-all"
        >
          {logs.length === 0 && status === 'building' && (
            <span className="text-gray-500">等待输出...</span>
          )}
          {logs.map((line, i) => (
            <div key={i} className={
              line.startsWith('✅') ? 'text-green-400' :
              line.startsWith('❌') ? 'text-red-400' :
              line.startsWith('📦') || line.startsWith('🔨') ? 'text-yellow-300' :
              line.startsWith('📝') ? 'text-blue-300' :
              line.startsWith('🔧') ? 'text-purple-300' :
              'text-gray-300'
            }>
              {line}
            </div>
          ))}
        </div>

        {/* 状态栏 */}
        <div className="flex items-center justify-between">
          <div className="text-sm text-muted-foreground">
            {status === 'building' && '正在构建 Docker 镜像，请稍候...'}
            {status === 'success' && '安装完成！'}
            {status === 'error' && (
              <span className="text-destructive">{errorMsg || '安装失败'}</span>
            )}
          </div>
          <Button
            variant={status === 'building' ? 'ghost' : 'default'}
            onClick={handleClose}
          >
            {status === 'building' ? '取消' : '关闭'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
