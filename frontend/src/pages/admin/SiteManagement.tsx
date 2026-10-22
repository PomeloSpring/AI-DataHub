import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Globe, Plus, Trash2, Edit } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Spinner } from '@/components/ui/spinner';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import client from '../../api/client';
import { listSites, saveSite, removeSite, type Site } from '../../api/sites';

/**
 * 站点管理（多站点部署拓扑清单）。
 *
 * 站点是「数据源归属」与「业务本体适用站点」的取值域；本页只维护清单本身，
 * 数据源归属在「数据源管理」里按站点选择框配置，本体适用站点在本体建模里配置。
 * 可查站点由角色数据源授权**派生**，此处不做授权（避免第二套裁决）。
 */
export default function SiteManagement() {
  const [sites, setSites] = useState<Site[]>([]);
  const [dsBySite, setDsBySite] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Site | null>(null);
  const [form, setForm] = useState<Partial<Site>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [items, dsRes] = await Promise.all([
        listSites({ includeInactive: true }),
        client.get('/datasources/'),
      ]);
      const grouped: Record<string, string[]> = {};
      (Array.isArray(dsRes.data) ? dsRes.data : []).forEach((d: any) => {
        const code = String(d.site_code || '');
        if (!code) return;
        (grouped[code] = grouped[code] || []).push(String(d.name || ''));
      });
      setSites(items);
      setDsBySite(grouped);
    } catch (e: any) {
      // 加载失败显式报错并可重试，不静默渲成空清单（no-silent-degradation）
      setError(e?.response?.data?.detail || '站点清单加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const openCreate = () => {
    setEditing(null);
    setForm({ site_code: '', display_name: '', is_aggregate: 0, sort_order: (sites.length + 1) * 10, is_active: 1 });
    setModalOpen(true);
  };

  const openEdit = (site: Site) => {
    setEditing(site);
    setForm({ ...site });
    setModalOpen(true);
  };

  const handleSave = async () => {
    const code = String(form.site_code || '').trim();
    const name = String(form.display_name || '').trim();
    if (!code || !name) {
      toast.error('站点代码与名称都不能为空');
      return;
    }
    try {
      await saveSite({ ...form, site_code: code, display_name: name });
      toast.success(editing ? '已更新站点' : '已新增站点');
      setModalOpen(false);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败');
    }
  };

  const handleDelete = async (site: Site) => {
    try {
      await removeSite(site.site_code);
      toast.success('已删除站点');
      load();
    } catch (e: any) {
      // 被数据源/本体引用时后端返回 409 + 占用清单，原样转述（不静默成功）
      toast.error(e?.response?.data?.detail || '删除失败');
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">站点管理</h3>
          <p className="text-sm text-muted-foreground">
            多站点部署拓扑的取值域：数据源在此声明的站点上归属，业务本体按站点声明适用范围。
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            刷新
          </Button>
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4 mr-2" />
            新增站点
          </Button>
        </div>
      </div>

      {loading && <div className="flex justify-center py-10"><Spinner size={28} /></div>}
      {!loading && error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {error}
          <Button variant="link" size="sm" className="ml-2 h-auto p-0" onClick={load}>重试</Button>
        </div>
      )}
      {!loading && !error && sites.length === 0 && (
        <p className="text-sm text-muted-foreground py-8 text-center">暂无站点，请先新增</p>
      )}

      {!loading && !!sites.length && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {sites.map((s) => {
            const bound = dsBySite[s.site_code] || [];
            return (
              <div key={s.site_code} className="border rounded-lg p-4">
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <div className="w-9 h-9 rounded-lg bg-muted flex items-center justify-center shrink-0">
                      <Globe className="h-4 w-4 text-muted-foreground" />
                    </div>
                    <div className="min-w-0">
                      <h4 className="font-medium truncate">{s.display_name}</h4>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {s.is_aggregate ? <Badge variant="secondary" className="text-xs">聚合态</Badge> : null}
                        {!s.is_active ? <Badge variant="outline" className="text-xs">已停用</Badge> : null}
                      </div>
                    </div>
                  </div>
                  <div className="flex gap-1 shrink-0">
                    <Button size="sm" variant="ghost" className="h-7 w-7 p-0" title="编辑" onClick={() => openEdit(s)}>
                      <Edit className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="sm" variant="ghost" className="h-7 w-7 p-0" title="删除" onClick={() => handleDelete(s)}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
                <div className="text-xs text-muted-foreground space-y-1">
                  <div>排序：{s.sort_order}</div>
                  <div title={`站点代码 ${s.site_code}`}>
                    归属数据源：{bound.length ? bound.join('、') : (s.is_aggregate ? '（聚合态不挂数据源）' : '暂无')}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-[460px]">
          <DialogHeader>
            <DialogTitle>{editing ? '编辑站点' : '新增站点'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>站点代码 *</Label>
              <Input
                value={form.site_code || ''}
                disabled={!!editing}
                onChange={(e) => setForm({ ...form, site_code: e.target.value })}
                placeholder="如：cn / eu / jp"
              />
              {editing && (
                <p className="text-xs text-muted-foreground">代码已被数据源与本体引用，不可改名。</p>
              )}
            </div>
            <div className="space-y-2">
              <Label>站点名称 *</Label>
              <Input
                value={form.display_name || ''}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                placeholder="如：中国站点"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>排序</Label>
                <Input
                  type="number"
                  value={form.sort_order ?? 0}
                  onChange={(e) => setForm({ ...form, sort_order: parseInt(e.target.value, 10) || 0 })}
                />
              </div>
              <div className="flex items-center gap-2 pt-6">
                <Switch
                  checked={!!form.is_active}
                  onCheckedChange={(v) => setForm({ ...form, is_active: v ? 1 : 0 })}
                />
                <Label>启用</Label>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={!!form.is_aggregate}
                onCheckedChange={(v) => setForm({ ...form, is_aggregate: v ? 1 : 0 })}
              />
              <Label>聚合态（如「全球」：跨站汇总，不挂具体数据源）</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>取消</Button>
            <Button onClick={handleSave}>{editing ? '更新' : '创建'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
