import { useCallback, useEffect, useState } from 'react';
import { toast } from '@/lib/toast';
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
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

/**
 * 站点管理（多站点部署拓扑清单）。
 *
 * 站点是「数据源归属」与「业务本体适用站点」的取值域；本页只维护清单本身，
 * 数据源归属在「数据源管理」里按站点选择框配置，本体适用站点在本体建模里配置。
 * 可查站点由角色数据源授权**派生**，此处不做授权（避免第二套裁决）。
 */
export default function SiteManagement() {
  const { t } = useTranslation('ui');
  const [sites, setSites] = useState<Site[]>([]);
  const [dsBySite, setDsBySite] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Site | null>(null);
  const [form, setForm] = useState<Partial<Site>>({});
  // 字段取值映射的行态（编辑比直接改 Record 好控：空行不入库、重复键要报错）
  const [fvRows, setFvRows] = useState<{ field: string; value: string }[]>([]);

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
      setError(e?.response?.data?.detail || t('k_e1edaf7b96'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const openCreate = () => {
    setEditing(null);
    setForm({ site_code: '', display_name: '', is_aggregate: 0, sort_order: (sites.length + 1) * 10, is_active: 1 });
    setFvRows([]);
    setModalOpen(true);
  };

  const openEdit = (site: Site) => {
    setEditing(site);
    setForm({ ...site });
    setFvRows(Object.entries(site.field_values || {}).map(([field, value]) => ({ field, value: String(value) })));
    setModalOpen(true);
  };

  const handleSave = async () => {
    const code = String(form.site_code || '').trim();
    const name = String(form.display_name || '').trim();
    if (!code || !name) {
      toast.error(t('k_1b8b3d046c'));
      return;
    }
    const field_values: Record<string, string> = {};
    for (const row of fvRows) {
      const key = String(row.field || '').trim().toLowerCase();
      const value = String(row.value || '').trim();
      if (!key && !value) continue;              // 全空行当作没填
      if (!key) {
        toast.error(t('k_ea05abb525', { p0: value }));
        return;
      }
      if (!value) {
        toast.error(t('k_7f1b813476', { p0: key }));
        return;
      }
      if (field_values[key]) {
        toast.error(t('k_581fc2b45d', { p0: key }));
        return;
      }
      field_values[key] = value;
    }
    try {
      await saveSite({ ...form, site_code: code, display_name: name, field_values });
      toast.success(editing ? t('k_780bf7a555') : t('k_e4608a244c'));
      setModalOpen(false);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_6de920b4e4'));
    }
  };

  const handleDelete = async (site: Site) => {
    try {
      await removeSite(site.site_code);
      toast.success(t('k_313aa495d5'));
      load();
    } catch (e: any) {
      // 被数据源/本体引用时后端返回 409 + 占用清单，原样转述（不静默成功）
      toast.error(e?.response?.data?.detail || t('k_acf0664a54'));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">{t("k_f40b79d76f")}</h3>
          <p className="text-sm text-muted-foreground">
            {t('k_2bb7ff0f78')}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            {t('k_38108eaa1d')}
          </Button>
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4 mr-2" />
            {t('k_504c3874b1')}
          </Button>
        </div>
      </div>

      {loading && <div className="flex justify-center py-10"><Spinner size={28} /></div>}
      {!loading && error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {error}
          <Button variant="link" size="sm" className="ml-2 h-auto p-0" onClick={load}>{t("k_e2d53a6d3a")}</Button>
        </div>
      )}
      {!loading && !error && sites.length === 0 && (
        <p className="text-sm text-muted-foreground py-8 text-center">{t("k_e711f9c93e")}</p>
      )}

      {!loading && !!sites.length && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {sites.map((s) => {
            const bound = dsBySite[s.site_code] || [];
            const fv = s.field_values || {};
            const fvText = Object.entries(fv).map(([k, v]) => `${k}=${v}`).join(' · ');
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
                        {s.is_aggregate ? <Badge variant="secondary" className="text-xs">{t("k_b3f3d7c8bf")}</Badge> : null}
                        {!s.is_active ? <Badge variant="outline" className="text-xs">{t("k_6c7dcbb73a")}</Badge> : null}
                      </div>
                    </div>
                  </div>
                  <div className="flex gap-1 shrink-0">
                    <Button size="sm" variant="ghost" className="h-7 w-7 p-0" title={t("k_a7f814c0a4")} onClick={() => openEdit(s)}>
                      <Edit className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="sm" variant="ghost" className="h-7 w-7 p-0" title={t("k_3755f56f2f")} onClick={() => handleDelete(s)}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
                <div className="text-xs text-muted-foreground space-y-1">
                  <div>{t('k_9c4738dc9d', { p0: s.sort_order })}</div>
                  <div title={t('k_ad1a747632', { p0: s.site_code })}>
                    {tr('k_c356ac4747')}{bound.length ? bound.join('、') : (s.is_aggregate ? t('k_e208a2e9c4') : t('k_f61f4cf6d0'))}
                  </div>
                  {/* 字段取值映射是看板筛选能不能命中数的配置，不能只能编辑时看得到 */}
                  <div>
                    {tr('k_4822c4aca0')}{s.is_aggregate
                      ? t('k_0533b7779e')
                      : (fvText || t('k_dbed17fc6a'))}
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
            <DialogTitle>{editing ? t('k_c45a1e3d66') : t('k_504c3874b1')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>{t('k_530728a11c')}</Label>
              <Input
                value={form.site_code || ''}
                disabled={!!editing}
                onChange={(e) => setForm({ ...form, site_code: e.target.value })}
                placeholder={t("k_4e79e58b04")}
              />
              {editing && (
                <p className="text-xs text-muted-foreground">{t("k_c87f87255f")}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label>{t('k_f6e17ae8e3')}</Label>
              <Input
                value={form.display_name || ''}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                placeholder={t("k_e199ba2a7f")}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t("k_dc35af8d69")}</Label>
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
                <Label>{t("k_d4e9ca3dd4")}</Label>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={!!form.is_aggregate}
                onCheckedChange={(v) => setForm({ ...form, is_aggregate: v ? 1 : 0 })}
              />
              <Label>{t("k_b7887d30b9")}</Label>
            </div>
            <div className="space-y-2">
              <Label>{t("k_02095fbcee")}</Label>
              <p className="text-xs text-muted-foreground">
                {t('k_a8fc3eb492')}
              </p>
              {fvRows.map((row, i) => (
                <div key={i} className="flex items-center gap-2">
                  <Input
                    className="flex-1"
                    placeholder={t("k_bd86378ec9")}
                    value={row.field}
                    onChange={(e) => setFvRows(fvRows.map((r, j) => (j === i ? { ...r, field: e.target.value } : r)))}
                  />
                  <Input
                    className="flex-1"
                    placeholder={t("k_07aad17194")}
                    value={row.value}
                    onChange={(e) => setFvRows(fvRows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))}
                  />
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-8 w-8 p-0 shrink-0"
                    title={t("k_699f4b779c")}
                    onClick={() => setFvRows(fvRows.filter((_, j) => j !== i))}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))}
              <Button size="sm" variant="outline" onClick={() => setFvRows([...fvRows, { field: '', value: '' }])}>
                <Plus className="h-3.5 w-3.5 mr-1" />
                {t('k_4484fa04e7')}
              </Button>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSave}>{editing ? t('k_d9db02d07a') : t('k_fcbd093292')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
