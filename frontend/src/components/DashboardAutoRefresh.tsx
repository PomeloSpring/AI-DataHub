import { useState, useEffect, useCallback, useRef } from 'react';
import { RefreshCw, Pause, Play, Clock } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Badge } from '@/components/ui/badge';

interface DashboardAutoRefreshProps {
  onRefresh: () => void;
  loading?: boolean;
}

const REFRESH_INTERVALS = [
  { value: '0', label: '关闭' },
  { value: '5', label: '5秒' },
  { value: '10', label: '10秒' },
  { value: '30', label: '30秒' },
  { value: '60', label: '1分钟' },
  { value: '300', label: '5分钟' },
  { value: '600', label: '10分钟' },
  { value: '1800', label: '30分钟' },
];

export default function DashboardAutoRefresh({ onRefresh, loading }: DashboardAutoRefreshProps) {
  const [interval, setInterval] = useState('0');
  const [isPaused, setIsPaused] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [countdown, setCountdown] = useState(0);
  const timerRef = useRef<number | null>(null);
  const countdownRef = useRef<number | null>(null);

  const intervalNum = parseInt(interval);

  const clearTimers = useCallback(() => {
    if (timerRef.current) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (countdownRef.current) {
      window.clearInterval(countdownRef.current);
      countdownRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => clearTimers();
  }, [clearTimers]);

  const startTimer = useCallback(() => {
    clearTimers();
    if (intervalNum <= 0 || isPaused) return;

    setCountdown(intervalNum);

    const countdownInterval = window.setInterval(() => {
      setCountdown((prev: number) => {
        if (prev <= 1) return intervalNum;
        return prev - 1;
      });
    }, 1000);
    countdownRef.current = countdownInterval;

    const refreshInterval = window.setInterval(() => {
      onRefresh();
      setLastRefresh(new Date());
    }, intervalNum * 1000);
    timerRef.current = refreshInterval;
  }, [intervalNum, isPaused, onRefresh, clearTimers]);

  useEffect(() => {
    startTimer();
    return clearTimers;
  }, [startTimer, clearTimers]);

  const handleIntervalChange = useCallback((value: string) => {
    setInterval(value);
    setIsPaused(false);
    if (parseInt(value) > 0) {
      setLastRefresh(new Date());
    }
  }, []);

  const togglePause = useCallback(() => {
    setIsPaused(prev => !prev);
  }, []);

  const handleManualRefresh = useCallback(() => {
    onRefresh();
    setLastRefresh(new Date());
    if (intervalNum > 0) {
      setCountdown(intervalNum);
    }
  }, [onRefresh, intervalNum]);

  const formatTime = useCallback((date: Date) => {
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }, []);

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-muted/30 border-b flex-shrink-0">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={handleManualRefresh}
            disabled={loading}
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </TooltipTrigger>
        <TooltipContent>手动刷新</TooltipContent>
      </Tooltip>

      <Select value={interval} onValueChange={handleIntervalChange}>
        <SelectTrigger className="w-[100px] h-8">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {REFRESH_INTERVALS.map((item) => (
            <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      {intervalNum > 0 && (
        <>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="h-8 w-8 p-0"
                onClick={togglePause}
              >
                {isPaused ? (
                  <Play className="h-4 w-4 text-green-500" />
                ) : (
                  <Pause className="h-4 w-4 text-yellow-500" />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent>{isPaused ? '继续自动刷新' : '暂停自动刷新'}</TooltipContent>
          </Tooltip>

          <div className="flex items-center gap-1 px-2 py-1 bg-background rounded border">
            <Clock className="h-3 w-3 text-muted-foreground" />
            <span className="text-xs text-muted-foreground min-w-[20px] text-center">{countdown}</span>
            <span className="text-xs text-muted-foreground">秒</span>
          </div>

          {isPaused && (
            <Badge variant="outline" className="text-xs text-yellow-500">已暂停</Badge>
          )}
        </>
      )}

      {lastRefresh && (
        <span className="text-xs text-muted-foreground ml-auto">
          上次刷新: {formatTime(lastRefresh)}
        </span>
      )}
    </div>
  );
}
