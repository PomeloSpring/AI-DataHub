import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog';
import { toast } from 'sonner';
import { Users, HardDrive, Trash2, RefreshCw, Settings2 } from 'lucide-react';
import client from '@/api/client';

// 管理员统管: 工作空间为用户个人工作站(随用户走, 无成员协作),
// 管理员在此按用户限制可建空间数与每空间磁盘配额, 并查看磁盘用量。

interface QuotaWorkspace {
  id: number;
  name: string;
  is_default: number;
  disk_usage_bytes: number;
}

interface QuotaUser {
  user_id: number;
  username: string;
  max_workspaces: number;
  disk_quota_bytes: number;
  workspaces: QuotaWorkspace[];
}

const fmtBytes = (n: number) => {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
};

function UsageBar({ used, quota }: { used: number; quota: number }) {
  const pct = quota > 0 ? Math.min(100, (used / quota) * 100) : 0;
  return (
    <div className="h-1.5 w-full rounded bg-muted overflow-hidden">
      <div
        className={`h-1.5 rounded ${pct >= 90 ? 'bg-destructive' : pct >= 70 ? 'bg-amber-500' : 'bg-primary'}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export default function WorkspaceManagerV2() {
  const [users, setUsers] = useState<QuotaUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [quotaTarget, setQuotaTarget] = useState<QuotaUser | null>(null);
  const [editMax, setEditMax] = useState('5');
  const [editDiskGb, setEditDiskGb] = useState('5');
  const [saving, setSaving] = useState(false);
  const [deleteWs, setDeleteWs] = useState<{ user: QuotaUser; ws: QuotaWorkspace } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/workspaces/quotas');
      setUsers(Array.isArray(data) ? data : []);
    } catch {
      toast.error('加载用户工作空间失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const openQuota = (u: QuotaUser) => {
    setQuotaTarget(u);
    setEditMax(String(u.max_workspaces));
    setEditDiskGb(String(Math.round((u.disk_quota_bytes / 1024 ** 3) * 10) / 10));
  };

  const saveQuota = async () => {
    if (!quotaTarget) return;
    const maxWs = Number(editMax);
    const diskBytes = Math.round(Number(editDiskGb) * 1024 ** 3);
    if (!Number.isFinite(maxWs) || maxWs < 1 || !Number.isFinite(diskBytes) || diskBytes < 1) {
      toast.error('配额必须为正数');
      return;
    }
    setSaving(true);
    try {
      await client.put(`/workspaces/quotas/${quotaTarget.user_id}`, {
        max_workspaces: maxWs,
        disk_quota_bytes: diskBytes,
      });
      toast.success('配额已保存');
      setQuotaTarget(null);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const removeWs = async () => {
    if (!deleteWs) return;
    try {
      await client.delete(`/workspaces/${deleteWs.ws.id}`);
      toast.success(`已删除工作空间 "${deleteWs.ws.name}"`);
      setDeleteWs(null);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  return (
    <div className="p-6 w-full space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">用户工作空间统管</h1>
          <p className="text-muted-foreground mt-1">
            工作空间是用户个人工作站（随用户走，用户自助创建）；在此限制每个用户的空间数量与磁盘配额。
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </Button>
      </div>

      {loading ? (
        <div className="text-center py-8 text-muted-foreground">加载中...</div>
      ) : users.length === 0 ? (
        <div className="text-center py-8 text-muted-foreground">暂无用户</div>
      ) : (
        <div className="space-y-4">
          {users.map(u => (
            <section key={u.user_id} className="border rounded-lg p-4 space-y-3">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <Users className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                  <span className="font-medium truncate">{u.username}</span>
                  <Badge variant="secondary">{u.workspaces.length}/{u.max_workspaces} 个空间</Badge>
                  <Badge variant="outline" className="text-xs">
                    <HardDrive className="h-3 w-3 mr-1" />
                    每空间 {fmtBytes(u.disk_quota_bytes)}
                  </Badge>
                </div>
                <Button variant="outline" size="sm" onClick={() => openQuota(u)}>
                  <Settings2 className="h-4 w-4 mr-1" />
                  配额设置
                </Button>
              </div>

              {u.workspaces.length === 0 ? (
                <p className="text-sm text-muted-foreground">该用户还没有工作空间（首次访问时自动创建默认工作站）</p>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
                  {u.workspaces.map(ws => {
                    const pct = u.disk_quota_bytes > 0
                      ? Math.round((ws.disk_usage_bytes / u.disk_quota_bytes) * 100) : 0;
                    return (
                      <div key={ws.id} className="border rounded p-3 space-y-2">
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="text-sm font-medium truncate">{ws.name}</span>
                            {ws.is_default ? <Badge variant="outline" className="text-[10px]">默认</Badge> : null}
                          </div>
                          <Button
                            variant="ghost" size="sm" className="text-destructive"
                            onClick={() => setDeleteWs({ user: u, ws })}
                            title="删除工作空间"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                        <div className="space-y-1">
                          <div className="flex justify-between text-xs text-muted-foreground">
                            <span>磁盘用量</span>
                            <span>{fmtBytes(ws.disk_usage_bytes)} / {fmtBytes(u.disk_quota_bytes)}{pct >= 90 ? '（接近上限）' : ''}</span>
                          </div>
                          <UsageBar used={ws.disk_usage_bytes} quota={u.disk_quota_bytes} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </section>
          ))}
        </div>
      )}

      {/* 配额设置 */}
      <Dialog open={!!quotaTarget} onOpenChange={open => { if (!open) setQuotaTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>配额设置 — {quotaTarget?.username}</DialogTitle>
            <DialogDescription>限制该用户可创建的工作空间数量与每个工作空间的磁盘上限。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>可建工作空间数（含默认工作站）</Label>
              <Input type="number" min={1} value={editMax} onChange={e => setEditMax(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label>每工作空间磁盘配额（GB）</Label>
              <Input type="number" min={1} step="0.5" value={editDiskGb} onChange={e => setEditDiskGb(e.target.value)} />
              <p className="text-xs text-muted-foreground">达到上限后将拒绝新建会话，用户需归档收藏产物或清理会话文件。</p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setQuotaTarget(null)}>取消</Button>
            <Button onClick={saveQuota} disabled={saving}>{saving ? '保存中...' : '保存'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <Dialog open={!!deleteWs} onOpenChange={open => { if (!open) setDeleteWs(null); }}>
        <DialogContent>
          <DialogHeader><DialogTitle>确认删除工作空间</DialogTitle></DialogHeader>
          <p className="text-sm">
            确定要删除 "{deleteWs?.ws.name}" 吗？该空间下的会话产物将一并清理（已归档资产不受影响）。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteWs(null)}>取消</Button>
            <Button variant="destructive" onClick={removeWs}>删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
