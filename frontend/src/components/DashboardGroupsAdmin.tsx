import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import { Plus, Edit, Trash2, Layers } from 'lucide-react';
import client from '@/api/client';

// 看板组合(仪表盘组)管理: 把若干看板编排为一个组合并按角色分配。
// 组只做目录/轮播编排, 不授予看板可见性(可见性仍由角色看板授权 fail-closed 裁决)。

interface Group {
  id: number;
  name: string;
  description: string;
  sort: number;
  dashboard_ids: number[];
  role_ids: number[];
}

export default function DashboardGroupsAdmin() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [boards, setBoards] = useState<Array<{ id: number; name: string; status?: string }>>([]);
  const [roles, setRoles] = useState<Array<{ id: number; display_name: string }>>([]);
  const [edit, setEdit] = useState<Group | 'new' | null>(null);
  const [form, setForm] = useState({ name: '', description: '', dashboard_ids: [] as number[], role_ids: [] as number[] });
  const [deleteTarget, setDeleteTarget] = useState<Group | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const [g, d, r] = await Promise.all([
        client.get('/dashboard/groups'),
        client.get('/dashboard/'),
        client.get('/roles/'),
      ]);
      setGroups(g.data || []);
      setBoards((d.data || []).map((x: any) => ({ id: x.id, name: x.name, status: x.status })));
      setRoles((r.data || []).map((x: any) => ({ id: x.id, display_name: x.display_name || x.name })));
    } catch {
      toast.error('加载看板组合失败');
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const openEdit = (g: Group | 'new') => {
    setEdit(g);
    setForm(g === 'new'
      ? { name: '', description: '', dashboard_ids: [], role_ids: [] }
      : { name: g.name, description: g.description || '', dashboard_ids: [...g.dashboard_ids], role_ids: [...g.role_ids] });
  };

  const toggle = (key: 'dashboard_ids' | 'role_ids', id: number) => {
    setForm(f => ({
      ...f,
      [key]: f[key].includes(id) ? f[key].filter(x => x !== id) : [...f[key], id],
    }));
  };

  const save = async () => {
    if (!form.name.trim()) { toast.error('请输入组合名称'); return; }
    setSaving(true);
    try {
      const payload = { name: form.name.trim(), description: form.description, sort: 0 };
      const gid = edit === 'new'
        ? (await client.post('/dashboard/groups', payload)).data.id
        : (edit as Group).id;
      if (edit !== 'new') await client.put(`/dashboard/groups/${gid}`, payload);
      await Promise.all([
        client.put(`/dashboard/groups/${gid}/items`, { dashboard_ids: form.dashboard_ids }),
        client.put(`/dashboard/groups/${gid}/roles`, { role_ids: form.role_ids }),
      ]);
      toast.success('看板组合已保存');
      setEdit(null);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!deleteTarget) return;
    try {
      await client.delete(`/dashboard/groups/${deleteTarget.id}`);
      toast.success('已删除');
      setDeleteTarget(null);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  return (
    <section className="border rounded-lg p-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="font-semibold flex items-center gap-2"><Layers className="h-4 w-4" />看板组合</h2>
          <p className="text-xs text-muted-foreground">
            把若干看板编排为一个组合并按角色分配；用户在「数据看板」按组合切换浏览/轮播（组内看板仍受角色可见性裁决）。
          </p>
        </div>
        <Button size="sm" onClick={() => openEdit('new')}><Plus className="h-4 w-4 mr-1" />新建组合</Button>
      </div>

      {groups.length === 0 ? (
        <p className="text-sm text-muted-foreground">暂无组合</p>
      ) : (
        <div className="space-y-2">
          {groups.map(g => (
            <div key={g.id} className="flex items-center justify-between gap-2 border rounded px-3 py-2">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium truncate">{g.name}</span>
                  <Badge variant="secondary" className="text-xs">{(g.dashboard_ids || []).length} 个看板</Badge>
                  {(g.role_ids || []).length === 0 ? (
                    <Badge variant="destructive" className="text-xs">未绑角色，用户看不到</Badge>
                  ) : (
                    <Badge variant="outline" className="text-xs">{(g.role_ids || []).length} 个角色</Badge>
                  )}
                </div>
                {g.description && <p className="text-xs text-muted-foreground truncate">{g.description}</p>}
              </div>
              <div className="flex gap-1">
                <Button variant="outline" size="sm" onClick={() => openEdit(g)}><Edit className="h-3.5 w-3.5" /></Button>
                <Button variant="ghost" size="sm" className="text-destructive" onClick={() => setDeleteTarget(g)}><Trash2 className="h-3.5 w-3.5" /></Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 新建/编辑 */}
      <Dialog open={!!edit} onOpenChange={open => { if (!open) setEdit(null); }}>
        <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{edit === 'new' ? '新建看板组合' : '编辑看板组合'}</DialogTitle>
            <DialogDescription>选择组合内的看板与可看到该组合的角色。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>名称 *</Label>
              <Input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="如: 经营日报" />
            </div>
            <div className="space-y-2">
              <Label>说明</Label>
              <Textarea rows={2} value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
            </div>
            <div className="space-y-2">
              <Label>组合内看板</Label>
              <p className="text-xs text-muted-foreground">浏览/轮播仅展示状态为「启用」的看板；设计中/已关闭的看板入组后不会出现在数据看板页。</p>
              <div className="max-h-40 overflow-y-auto border rounded p-2 space-y-1">
                {boards.map(b => (
                  <label key={b.id} className="flex items-center gap-2 text-sm cursor-pointer">
                    <input
                      type="checkbox" className="h-3.5 w-3.5"
                      checked={form.dashboard_ids.includes(b.id)}
                      onChange={() => toggle('dashboard_ids', b.id)}
                    />
                    {b.name}
                    {b.status && b.status !== 'enabled' && (
                      <Badge variant="outline" className="text-[10px]">{b.status === 'designing' ? '设计中' : b.status === 'closed' ? '已关闭' : b.status}</Badge>
                    )}
                  </label>
                ))}
                {boards.length === 0 && <p className="text-xs text-muted-foreground">暂无看板</p>}
              </div>
            </div>
            <div className="space-y-2">
              <Label>可看到该组合的角色</Label>
              <div className="max-h-32 overflow-y-auto border rounded p-2 space-y-1">
                {roles.map(r => (
                  <label key={r.id} className="flex items-center gap-2 text-sm cursor-pointer">
                    <input
                      type="checkbox" className="h-3.5 w-3.5"
                      checked={form.role_ids.includes(r.id)}
                      onChange={() => toggle('role_ids', r.id)}
                    />
                    {r.display_name}
                  </label>
                ))}
                {roles.length === 0 && <p className="text-xs text-muted-foreground">暂无角色</p>}
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEdit(null)}>取消</Button>
            <Button onClick={save} disabled={saving}>{saving ? '保存中...' : '保存'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <Dialog open={!!deleteTarget} onOpenChange={open => { if (!open) setDeleteTarget(null); }}>
        <DialogContent>
          <DialogHeader><DialogTitle>确认删除组合</DialogTitle></DialogHeader>
          <p className="text-sm">删除 "{deleteTarget?.name}" 不会删除其中的看板。</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={remove}>删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
