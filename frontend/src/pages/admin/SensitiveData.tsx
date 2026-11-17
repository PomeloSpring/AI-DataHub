import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { toast } from '@/lib/toast';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { Plus, Trash2, RefreshCw, ScanSearch, Edit, Shield } from 'lucide-react';
import { governanceApi, type SensitiveField } from '@/api/governance';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import client from '@/api/client';
import { useTranslation } from 'react-i18next';
import { t as i18nTr , t as tr } from '@/i18n/runtime';

const LEVEL_MAP: Record<string, { label: string; color: string }> = {
  low: { label: i18nTr('k_b9ee259b7f'), color: 'bg-green-500/10 text-green-500 border-green-500/20' },
  medium: { label: i18nTr('k_0869071c92'), color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  high: { label: i18nTr('k_b096b3f5ac'), color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
  critical: { label: i18nTr('k_c34687da2b'), color: 'bg-red-500/10 text-red-500 border-red-500/20' },
};

// mask_type 与后端 adh_sensitive_fields 枚举一致: full|partial|hash|none|block
// block = 不能查询出来(合规屏蔽基线), 对所有身份含管理员生效
const MASK_TYPE_OPTIONS = [
  { value: 'block', label: i18nTr('k_35ee02d9c2'), danger: true },
  { value: 'full', label: i18nTr('k_60a4f347fc') },
  { value: 'partial', label: i18nTr('k_b25959d39d') },
  { value: 'hash', label: i18nTr('k_29646d0eeb') },
  { value: 'none', label: i18nTr('k_b90319af3c') },
];

const maskTypeLabel = (v: string) =>
  MASK_TYPE_OPTIONS.find(m => m.value === v)?.label || v || '-';

interface FieldForm {
  datasource_id: string;
  table_name: string;
  column_name: string;
  sensitivity_level: string;
  mask_type: string;
}

const EMPTY_FORM: FieldForm = {
  datasource_id: '',
  table_name: '',
  column_name: '',
  sensitivity_level: '',
  mask_type: '',
};

export default function SensitiveData() {
  const { t } = useTranslation('ui');
  const [fields, setFields] = useState<SensitiveField[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editField, setEditField] = useState<SensitiveField | null>(null);
  const [form, setForm] = useState<FieldForm>(EMPTY_FORM);
  const [deleteTarget, setDeleteTarget] = useState<SensitiveField | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanDatasourceId, setScanDatasourceId] = useState('');
  const [datasources, setDatasources] = useState<Array<{ id: number; name: string; db_type: string }>>([]);
  const { getDefaultWorkspaceId } = useWorkspaceStore();

  // 加载数据源列表
  useEffect(() => {
    client.get('/datasources/').then(({ data }) => {
      setDatasources(Array.isArray(data) ? data : []);
    }).catch(() => {});
  }, []);

  const loadFields = useCallback(async () => {
    setLoading(true);
    try {
      const workspaceId = getDefaultWorkspaceId();
      const res = await governanceApi.getSensitiveFields({ workspace_id: workspaceId, page, size: pageSize });
      // 服务端分页：{items, total}（历史缺陷：接口只回数组无 total，分页条算不出页数等于没做）
      const payload: any = res.data;
      setFields(Array.isArray(payload) ? payload : (payload?.items || []));
      setTotal(Array.isArray(payload) ? payload.length : (payload?.total ?? 0));
    } catch {
      toast.error(t('k_9dbf4795ad'));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, getDefaultWorkspaceId]);

  useEffect(() => { loadFields(); }, [loadFields]);

  const openCreate = () => {
    setEditField(null);
    setForm(EMPTY_FORM);
    setFormOpen(true);
  };

  const openEdit = (field: SensitiveField) => {
    setEditField(field);
    setForm({
      datasource_id: field.datasource_id === 0 ? 'global' : String(field.datasource_id),
      table_name: field.table_name,
      column_name: field.column_name,
      sensitivity_level: field.sensitivity_level,
      mask_type: field.mask_type,
    });
    setFormOpen(true);
  };

  const handleSave = async () => {
    // 数据源 ID: 'global' = 0 (全局), 否则为具体数据源 ID
    const dsId = form.datasource_id === 'global' ? 0 : Number(form.datasource_id);
    if (!form.column_name.trim()) { toast.error(t('k_5755fa7071')); return; }
    if (!form.sensitivity_level) { toast.error(t('k_1af61f81c0')); return; }
    if (!form.mask_type) { toast.error(t('k_a226c14dd0')); return; }
    const payload = {
      datasource_id: dsId,
      table_name: form.table_name.trim(),
      column_name: form.column_name.trim(),
      sensitivity_level: form.sensitivity_level,
      mask_type: form.mask_type,
      workspace_id: getDefaultWorkspaceId(),
    };
    try {
      if (editField) {
        await governanceApi.updateSensitiveField(editField.id, payload);
        toast.success(t('k_1b92092d63'));
      } else {
        await governanceApi.createSensitiveField(payload);
        toast.success(t('k_2d4c5dcc74'));
      }
      setFormOpen(false);
      loadFields();
    } catch {
      toast.error(t('k_6de920b4e4'));
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await governanceApi.deleteSensitiveField(deleteTarget.id);
      toast.success(t('k_5cc232620c'));
      setDeleteTarget(null);
      loadFields();
    } catch {
      toast.error(t('k_acf0664a54'));
    }
  };

  const handleScan = async () => {
    if (!scanDatasourceId) {
      toast.error(t('k_9acb966d94'));
      return;
    }
    setScanning(true);
    try {
      const workspaceId = getDefaultWorkspaceId();
      const dsId = scanDatasourceId === 'global' ? 0 : Number(scanDatasourceId);
      const res = await governanceApi.scanSensitiveFields(dsId, workspaceId);
      const count = res.data?.found ?? res.data?.count ?? 0;
      toast.success(t('k_c7e7f5a90c', { p0: count }));
      loadFields();
    } catch {
      toast.error(t('k_5ec14542d7'));
    } finally {
      setScanning(false);
    }
  };

  return (
    <div className="flex flex-col h-full gap-4">
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-lg font-semibold">{t("k_980954e90b")}</h1>
          <p className="text-muted-foreground text-xs mt-1">{t("k_21e42c9cdf")}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadFields}>
            <RefreshCw className="w-4 h-4 mr-1" />
            {t('k_38108eaa1d')}
          </Button>
          <Button size="sm" onClick={openCreate}>
            <Plus className="w-4 h-4 mr-1" />
            {t('k_4484fa04e7')}
          </Button>
        </div>
      </div>

      {/* Scan Bar */}
      <div className="flex items-center gap-3 p-4 border rounded-lg bg-muted/20 shrink-0">
        <Shield className="w-5 h-5 text-muted-foreground" />
        <span className="text-sm font-medium">{t("k_c4f0cf5ff7")}</span>
        <Select value={scanDatasourceId} onValueChange={setScanDatasourceId}>
          <SelectTrigger className="w-[200px]">
            <SelectValue placeholder={t("k_422b9e314a")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="global">{t("k_f92855e6dd")}</SelectItem>
            {datasources.map(ds => (
              <SelectItem key={ds.id} value={String(ds.id)}>
                {ds.name} ({ds.db_type})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button size="sm" variant="outline" onClick={handleScan} disabled={scanning}>
          <ScanSearch className="w-4 h-4 mr-1" />
          {scanning ? t('k_e6c2d5d31a') : t('k_d0e34b8577')}
        </Button>
        <span className="text-xs text-muted-foreground">
          {t('k_1d0ad3d3c5')}
        </span>
      </div>

      {/* Fields Table：固定表头 + 有界滚动（顶部不随页面滚动），底栏作为卡片尾栏 */}
      <div className="flex-1 min-h-0 flex flex-col border rounded-lg overflow-hidden">
        <div className="flex-1 overflow-auto">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="sticky top-0 z-10 bg-muted text-left p-3 font-medium">{t("k_584c112b78")}</th>
              <th className="sticky top-0 z-10 bg-muted text-left p-3 font-medium">{t("k_074a5eecfc")}</th>
              <th className="sticky top-0 z-10 bg-muted text-left p-3 font-medium">{t("k_db09fe2f4c")}</th>
              <th className="sticky top-0 z-10 bg-muted text-left p-3 font-medium">{t("k_d520a7a44a")}</th>
              <th className="sticky top-0 z-10 bg-muted text-left p-3 font-medium">{t("k_a3ccf702c5")}</th>
              <th className="sticky top-0 z-10 bg-muted text-left p-3 font-medium">{t("k_84e3802f60")}</th>
              <th className="sticky top-0 z-10 bg-muted text-right p-3 font-medium">{t("k_f3ea6d345e")}</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} className="p-8 text-center text-muted-foreground">
                  {t('k_26b5bd4947')}
                </td>
              </tr>
            ) : fields.length === 0 ? (
              <tr>
                <td colSpan={7} className="p-8 text-center text-muted-foreground">
                  {t('k_1c388efdd6')}
                </td>
              </tr>
            ) : (
              fields.map(field => (
                <tr key={field.id} className="border-t hover:bg-muted/30">
                  <td className="p-3">
                    {field.table_name ? (
                      <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{field.table_name}</code>
                    ) : (
                      <Badge variant="outline" className="text-xs border-blue-500/30 text-blue-500">{t("k_7b006fedc8")}</Badge>
                    )}
                  </td>
                  <td className="p-3">
                    <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{field.column_name}</code>
                  </td>
                  <td className="p-3">
                    <Badge variant="outline" className={LEVEL_MAP[field.sensitivity_level]?.color}>
                      {LEVEL_MAP[field.sensitivity_level]?.label || field.sensitivity_level}
                    </Badge>
                  </td>
                  <td className="p-3 text-xs">
                    {field.mask_type === 'block' ? (
                      <Badge variant="outline" className="bg-red-500/10 text-red-500 border-red-500/30 font-medium">
                        {maskTypeLabel(field.mask_type)}
                      </Badge>
                    ) : (
                      maskTypeLabel(field.mask_type)
                    )}
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">
                    {field.datasource_id === 0
                      ? t('k_2be75b1044')
                      : (datasources.find(d => d.id === field.datasource_id)?.name || t('k_32cc05509f'))}
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">
                    {new Date(field.created_at).toLocaleString()}
                  </td>
                  <td className="p-3">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openEdit(field)}
                        title={t("k_a7f814c0a4")}
                      >
                        <Edit className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleteTarget(field)}
                        title={t("k_3755f56f2f")}
                      >
                        <Trash2 className="w-4 h-4 text-destructive" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        </div>

        {/* Pagination —— 统一分页条：卡片尾栏（不悬浮，不随表体滚动） */}
        <PaginationBar
          page={page}
          pageSize={pageSize}
          total={total}
          onChange={(p: number) => setPage(p)}
          onPageSizeChange={(n: number) => { setPageSize(n); setPage(1); }}
          hideWhenSingle
          className="shrink-0"
        />
      </div>

      {/* Create/Edit Dialog */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent
          className="max-w-lg"
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>{editField ? t('k_064b0cd408') : t('k_3dd6572dee')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium">{t('k_0e3c853488')}</label>
              <Select
                value={form.datasource_id || 'global'}
                onValueChange={v => setForm(f => ({ ...f, datasource_id: v }))}
                disabled={!!editField}
              >
                <SelectTrigger className="mt-1">
                  <SelectValue placeholder={t("k_422b9e314a")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="global">{t("k_f92855e6dd")}</SelectItem>
                  {datasources.map(ds => (
                    <SelectItem key={ds.id} value={String(ds.id)}>
                      {ds.name} ({ds.db_type})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {!editField && (
                <p className="text-xs text-muted-foreground mt-1">{t("k_baf2c0c25e")}</p>
              )}
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">{t("k_584c112b78")}</label>
                <Input
                  className="mt-1"
                  value={form.table_name}
                  onChange={e => setForm(f => ({ ...f, table_name: e.target.value }))}
                  placeholder={t('k_24858e2d28')}
                  disabled={!!editField}
                />
                {!editField && (
                  <p className="text-xs text-muted-foreground mt-1">{t("k_695606f679")}</p>
                )}
              </div>
              <div>
                <label className="text-sm font-medium">{t('k_970457073d')}</label>
                <Input
                  className="mt-1"
                  value={form.column_name}
                  onChange={e => setForm(f => ({ ...f, column_name: e.target.value }))}
                  placeholder={t('k_e532fca59c')}
                  disabled={!!editField}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">{t('k_c7eb07ad03')}</label>
                <Select
                  value={form.sensitivity_level}
                  onValueChange={v => setForm(f => ({ ...f, sensitivity_level: v }))}
                >
                  <SelectTrigger className="mt-1">
                    <SelectValue placeholder={t("k_18f438b908")} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="low">{t("k_b9ee259b7f")}</SelectItem>
                    <SelectItem value="medium">{t("k_0869071c92")}</SelectItem>
                    <SelectItem value="high">{t("k_b096b3f5ac")}</SelectItem>
                    <SelectItem value="critical">{t("k_c34687da2b")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-sm font-medium">{t('k_e2dfe5d6e9')}</label>
                <Select
                  value={form.mask_type}
                  onValueChange={v => setForm(f => ({ ...f, mask_type: v }))}
                >
                  <SelectTrigger className="mt-1">
                    <SelectValue placeholder={t("k_23ddbfc460")} />
                  </SelectTrigger>
                  <SelectContent>
                    {MASK_TYPE_OPTIONS.map(opt => (
                      <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSave}>{editField ? t('k_d9db02d07a') : t('k_94191ce210')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("k_3c06abe116")}</DialogTitle>
            <DialogDescription>
              {tr('k_cf36331024')}{deleteTarget?.table_name ? deleteTarget.table_name + '.' : t('k_b698d2f0d6')}{deleteTarget?.column_name}{tr('k_be20fe7f99')}{' '}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>{t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={handleDelete}>{t("k_3755f56f2f")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
