import client from './client';

export interface MonitoredService {
  key: string;
  name: string;
  desc: string;
  port: number;
  path: string;
  layer: string;
  host?: string;
  status: 'healthy' | 'down';
  latency_ms: number | null;
  version: string | null;
  message: string;
}

export interface MonitoringLayer {
  key: string;
  name: string;
  desc: string;
  total: number;
  healthy: number;
  down: number;
  services: MonitoredService[];
}

export interface MonitoringSummary {
  total: number;
  healthy: number;
  down: number;
  avg_latency_ms: number | null;
}

export interface MonitoringResult {
  checked_at: string;
  summary: MonitoringSummary;
  layers: MonitoringLayer[];
}

export interface NodeMetrics {
  collected_at: string;
  hostname: string;
  source_service: string;
  source_host: string;
  cpu: {
    percent: number | null;
    cores: number | null;
    load: { load1: number; load5: number; load15: number } | null;
  };
  memory: { total: number; used: number; available: number; percent: number } | null;
  disk: { path: string; total: number; used: number; free: number; percent: number } | null;
  uptime_seconds: number | null;
  process_count: number | null;
}

export interface SystemMetrics {
  collected_at: string;
  node_count: number;
  nodes: NodeMetrics[];
}

export async function fetchServicesHealth(): Promise<MonitoringResult> {
  const { data } = await client.get<MonitoringResult>('/monitoring/services');
  return data;
}

export async function fetchSystemMetrics(): Promise<SystemMetrics> {
  const { data } = await client.get<SystemMetrics>('/monitoring/system');
  return data;
}

/** Format bytes as human-readable string */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '-';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let v = bytes;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

/** Format seconds as compact duration */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '-';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d} 天 ${h} 小时`;
  if (h > 0) return `${h} 小时 ${m} 分`;
  return `${m} 分钟`;
}
