/**
 * 系统监控页面 — 服务器性能指标 + 按架构分层的服务健康监控
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'sonner';
import {
  Activity, RefreshCw, CheckCircle2, XCircle, Gauge, Server, Timer,
  Cpu, MemoryStick, HardDrive, Layers,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Spinner } from '@/components/ui/spinner';
import {
  fetchServicesHealth, fetchSystemMetrics, formatBytes, formatDuration,
  MonitoringResult, MonitoredService, SystemMetrics, NodeMetrics,
} from '../../api/monitoring';

const AUTO_REFRESH_INTERVAL = 30000;

function StatCard({ icon: Icon, label, value, accent }: {
  icon: any; label: string; value: string | number; accent?: string;
}) {
  return (
    <Card>
      <CardContent className="p-4 flex items-center gap-3">
        <div className={`h-10 w-10 rounded-lg flex items-center justify-center ${accent || 'bg-primary/10'}`}>
          <Icon className="h-5 w-5 text-primary" />
        </div>
        <div>
          <div className="text-2xl font-bold leading-tight">{value}</div>
          <div className="text-xs text-muted-foreground">{label}</div>
        </div>
      </CardContent>
    </Card>
  );
}

/** Usage bar with color thresholds: green < 60%, amber < 85%, red >= 85% */
function UsageBar({ percent }: { percent: number }) {
  const color = percent >= 85 ? 'bg-destructive' : percent >= 60 ? 'bg-amber-500' : 'bg-green-500';
  return (
    <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
      <div className={`h-full rounded-full transition-all duration-500 ${color}`}
        style={{ width: `${Math.min(100, percent)}%` }} />
    </div>
  );
}

function MetricBlock({ icon: Icon, title, percent, detail }: {
  icon: any; title: string; percent: number | null; detail: string;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-sm">
        <span className="flex items-center gap-2 font-medium">
          <Icon className="h-4 w-4 text-muted-foreground" />{title}
        </span>
        <span className="font-semibold tabular-nums">{percent !== null ? `${percent}%` : '-'}</span>
      </div>
      {percent !== null ? <UsageBar percent={percent} /> : <div className="h-2 rounded-full bg-muted" />}
      <p className="text-xs text-muted-foreground">{detail}</p>
    </div>
  );
}

function NodePanel({ node }: { node: NodeMetrics }) {
  const { cpu, memory, disk } = node;
  return (
    <Card>
      <CardContent className="p-5 space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <Server className="h-4 w-4 text-primary" />
            {node.hostname}
            <span className="text-xs font-normal text-muted-foreground">
              采集自 {node.source_service} @ {node.source_host}
            </span>
          </h2>
          <div className="flex items-center gap-4 text-xs text-muted-foreground">
            {node.uptime_seconds !== null && <span>运行 {formatDuration(node.uptime_seconds)}</span>}
            {node.process_count !== null && <span>{node.process_count} 个进程</span>}
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <MetricBlock
            icon={Cpu} title="CPU" percent={cpu.percent}
            detail={cpu.load
              ? `${cpu.cores ?? '-'} 核 · 负载 ${cpu.load.load1} / ${cpu.load.load5} / ${cpu.load.load15}`
              : `${cpu.cores ?? '-'} 核`}
          />
          <MetricBlock
            icon={MemoryStick} title="内存" percent={memory?.percent ?? null}
            detail={memory ? `已用 ${formatBytes(memory.used)} / 共 ${formatBytes(memory.total)}` : '-'}
          />
          <MetricBlock
            icon={HardDrive} title={`磁盘 (${disk?.path ?? '/'})`} percent={disk?.percent ?? null}
            detail={disk ? `已用 ${formatBytes(disk.used)} / 共 ${formatBytes(disk.total)}` : '-'}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function SystemPanel({ metrics }: { metrics: SystemMetrics | null }) {
  if (!metrics || metrics.nodes.length === 0) return null;
  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <Cpu className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-sm font-semibold">服务器性能</h2>
        <span className="text-xs text-muted-foreground">共 {metrics.node_count} 个节点</span>
      </div>
      {metrics.nodes.map(node => <NodePanel key={node.hostname} node={node} />)}
    </section>
  );
}

function ServiceCard({ svc }: { svc: MonitoredService }) {
  const healthy = svc.status === 'healthy';
  return (
    <Card className={healthy ? '' : 'border-destructive/50'}>
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-2 mb-2">
          <div className="flex items-center gap-2 min-w-0">
            {healthy
              ? <CheckCircle2 className="h-4 w-4 text-green-500 flex-shrink-0" />
              : <XCircle className="h-4 w-4 text-destructive flex-shrink-0" />}
            <span className="font-medium truncate">{svc.name}</span>
          </div>
          <Badge variant={healthy ? 'default' : 'destructive'}
            className={healthy ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 hover:bg-green-100' : ''}>
            {healthy ? '运行中' : '已停止'}
          </Badge>
        </div>
        <p className="text-xs text-muted-foreground mb-3">{svc.desc}</p>
        <div className="flex items-center gap-4 text-xs text-muted-foreground">
          <span>端口 {svc.port}</span>
          {svc.host && svc.host !== '127.0.0.1' && <span>{svc.host}</span>}
          {healthy && svc.latency_ms !== null && (
            <span className="flex items-center gap-1">
              <Timer className="h-3 w-3" />{svc.latency_ms} ms
            </span>
          )}
          {svc.version && <span>v{svc.version}</span>}
        </div>
        {!healthy && (
          <p className="mt-2 text-xs text-destructive/80 truncate" title={svc.message}>
            {svc.message}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export default function Monitoring() {
  const [data, setData] = useState<MonitoringResult | null>(null);
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (silent = false) => {
    if (!silent) setRefreshing(true);
    try {
      const [result, sys] = await Promise.all([fetchServicesHealth(), fetchSystemMetrics()]);
      setData(result);
      setMetrics(sys);
    } catch (e: any) {
      if (!silent) toast.error('获取监控数据失败');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (autoRefresh) {
      timerRef.current = setInterval(() => load(true), AUTO_REFRESH_INTERVAL);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      timerRef.current = null;
    };
  }, [autoRefresh, load]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full min-h-[400px]">
        <Spinner className="h-6 w-6 text-primary" />
      </div>
    );
  }

  const summary = data?.summary;

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2">
            <Activity className="h-5 w-5 text-primary" />
            系统监控
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            服务器性能与服务健康状态监控
            {data?.checked_at && <span className="ml-2">· 上次检查 {data.checked_at}</span>}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
            <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} />
            自动刷新 (30s)
          </label>
          <Button variant="outline" size="sm" onClick={() => load()} disabled={refreshing}>
            <RefreshCw className={`h-4 w-4 mr-1.5 ${refreshing ? 'animate-spin' : ''}`} />
            刷新
          </Button>
        </div>
      </div>

      {/* Server performance */}
      <SystemPanel metrics={metrics} />

      {/* Summary */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard icon={Server} label="服务总数" value={summary.total} />
          <StatCard icon={CheckCircle2} label="运行中" value={summary.healthy}
            accent="bg-green-500/10" />
          <StatCard icon={XCircle} label="已停止" value={summary.down}
            accent={summary.down > 0 ? 'bg-destructive/10' : 'bg-muted'} />
          <StatCard icon={Gauge} label="平均延迟"
            value={summary.avg_latency_ms !== null ? `${summary.avg_latency_ms} ms` : '-'} />
        </div>
      )}

      {/* Services grouped by architecture layer */}
      {data?.layers.map(layer => (
        <section key={layer.key} className="space-y-3">
          <div className="flex items-center gap-2">
            <Layers className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-sm font-semibold">{layer.name}</h2>
            <span className="text-xs text-muted-foreground">{layer.desc}</span>
            <Badge variant="outline" className="ml-auto">
              {layer.down > 0
                ? `${layer.healthy}/${layer.total} 正常`
                : `${layer.total} 全部正常`}
            </Badge>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {layer.services.map(svc => <ServiceCard key={svc.key} svc={svc} />)}
          </div>
        </section>
      ))}
    </div>
  );
}
