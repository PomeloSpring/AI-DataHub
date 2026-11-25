import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
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
import { Plus, Edit, Trash2, RefreshCw } from 'lucide-react';
import { governanceApi, type DataStandard } from '@/api/governance';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useTranslation } from 'react-i18next';
import { t as i18nTr , t as tr } from '@/i18n/runtime';

const TYPE_MAP: Record<string, { label: string; color: string }> = {
  naming: { label: i18nTr('k_4d443c5242'), color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  encoding: { label: i18nTr('k_954c95280e'), color: 'bg-purple-500/10 text-purple-500 border-purple-500/20' },
  measurement: { label: i18nTr('k_d6e94ede0c'), color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
  format: { label: i18nTr('k_b5720ccbe8'), color: 'bg-green-500/10 text-green-500 border-green-500/20' },
};

interface StandardForm {
  name: string;
  standard_type: string;
  description: string;
  rule_config: string;
}

const EMPTY_FORM: StandardForm = {
  name: '',
  standard_type: '',
  description: '',
  rule_config: '{}',
};

export default function Standards() {
  const { t } = useTranslation('ui');
  const [standards, setStandards] = useState<DataStandard[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editStandard, setEditStandard] = useState<DataStandard | null>(null);
  const [form, setForm] = useState<StandardForm>(EMPTY_FORM);
  const [deleteTarget, setDeleteTarget] = useState<DataStandard | null>(null);
  const [jsonError, setJsonError] = useState('');
  const { getDefaultWorkspaceId } = useWorkspaceStore();

  const loadStandards = useCallback(async () => {
    setLoading(true);
    try {
      const workspaceId = getDefaultWorkspaceId();
      const res = await governanceApi.listStandards({
        workspace_id: workspaceId,
        page,
        page_size: pageSize,
      });
      // 分页信封：total 取服务端 COUNT（不是当前页长度）
      setStandards(res.data?.items || []);
      setTotal(res.data?.total || 0);
    } catch {
      toast.error(t('k_0d165565bf'));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, getDefaultWorkspaceId]);

  useEffect(() => { loadStandards(); }, [loadStandards]);

  const openCreate = () => {
    setEditStandard(null);
    setForm(EMPTY_FORM);
    setJsonError('');
    setFormOpen(true);
  };

  const openEdit = (standard: DataStandard) => {
    setEditStandard(standard);
    setForm({
      name: standard.name,
      standard_type: standard.standard_type,
      description: standard.description || '',
      rule_config: JSON.stringify(standard.rule_config || {}, null, 2),
    });
    setJsonError('');
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.name.trim()) { toast.error(t('k_09ee176868')); return; }
    if (!form.standard_type) { toast.error(t('k_e8d3750196')); return; }
    let parsedConfig: Record<string, any>;
    try {
      parsedConfig = JSON.parse(form.rule_config);
      setJsonError('');
    } catch {
      setJsonError(tr('k_c5a7652815'));
      return;
    }
    const payload = {
      name: form.name,
      standard_type: form.standard_type,
      description: form.description,
      rule_config: parsedConfig,
      workspace_id: getDefaultWorkspaceId(),
    };
    try {
      if (editStandard) {
        await governanceApi.updateStandard(editStandard.id, payload);
        toast.success(t('k_1b92092d63'));
      } else {
        await governanceApi.createStandard(payload);
        toast.success(t('k_2d4c5dcc74'));
      }
      setFormOpen(false);
      // 新建/编辑后回到第 1 页（按 created_at DESC，最新标准在首页）
      if (page === 1) loadStandards(); else setPage(1);
    } catch {
      toast.error(t('k_6de920b4e4'));
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await governanceApi.deleteStandard(deleteTarget.id);
      toast.success(t('k_5cc232620c'));
      setDeleteTarget(null);
      // 末页删空时收敛到前一个有效页，不停在空白页
      if (standards.length === 1 && page > 1) setPage(page - 1);
      else loadStandards();
    } catch {
      toast.error(t('k_acf0664a54'));
    }
  };

  const handleToggle = async (standard: DataStandard) => {
    try {
      // Use update to toggle is_active
      await governanceApi.updateStandard(standard.id, { is_active: !standard.is_active });
      toast.success(standard.is_active ? t('k_0fe5a98e9f') : t('k_53ace4305f'));
      loadStandards();
    } catch {
      toast.error(t('k_5fa802bef5'));
    }
  };

  return (
    <div className="flex flex-col h-full gap-4">
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-2xl font-bold">{t("k_32f7d476fd")}</h1>
          <p className="text-muted-foreground text-sm mt-1">{t("k_1fd2fe33ef")}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadStandards}>
            <RefreshCw className="w-4 h-4 mr-1" />
            {t('k_38108eaa1d')}
          </Button>
          <Button size="sm" onClick={openCreate}>
            <Plus className="w-4 h-4 mr-1" />
            {t('k_555f2a12b8')}
          </Button>
        </div>
      </div>

      {/* Standards Table：仅此区滚动（页头/分页固定，参考表&字段） */}
      <div className="flex-1 min-h-0 overflow-auto border rounded-lg">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">{t("k_a888194c58")}</th>
              <th className="text-left p-3 font-medium">{t("k_e4e46c7235")}</th>
              <th className="text-left p-3 font-medium">{t("k_412f54dc38")}</th>
              <th className="text-center p-3 font-medium">{t("k_d4e9ca3dd4")}</th>
              <th className="text-left p-3 font-medium">{t("k_84e3802f60")}</th>
              <th className="text-right p-3 font-medium">{t("k_f3ea6d345e")}</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={6} className="p-8 text-center text-muted-foreground">
                  {t('k_26b5bd4947')}
                </td>
              </tr>
            ) : standards.length === 0 ? (
              <tr>
                <td colSpan={6} className="p-8 text-center text-muted-foreground">
                  {t('k_42bc07f873')}
                </td>
              </tr>
            ) : (
              standards.map(standard => (
                <tr key={standard.id} className="border-t hover:bg-muted/30">
                  <td className="p-3 font-medium">{standard.name}</td>
                  <td className="p-3">
                    <Badge variant="outline" className={TYPE_MAP[standard.standard_type]?.color}>
                      {TYPE_MAP[standard.standard_type]?.label || standard.standard_type}
                    </Badge>
                  </td>
                  <td className="p-3 text-xs text-muted-foreground truncate max-w-[300px]">
                    {standard.description || '-'}
                  </td>
                  <td className="p-3 text-center">
                    <Switch
                      checked={standard.is_active}
                      onCheckedChange={() => handleToggle(standard)}
                    />
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">
                    {new Date(standard.created_at).toLocaleString()}
                  </td>
                  <td className="p-3">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openEdit(standard)}
                        title={t("k_a7f814c0a4")}
                      >
                        <Edit className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleteTarget(standard)}
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

      {/* Pagination —— 统一分页条：固定底栏；单页也保留「共 N 条」与档位 */}
      {total > 0 && (
        <PaginationBar
          page={page}
          pageSize={pageSize}
          total={total}
          busy={loading}
          onChange={(p: number) => setPage(p)}
          onPageSizeChange={(n: number) => { setPageSize(n); setPage(1); }}
          className="shrink-0"
        />
      )}

      {/* Create/Edit Dialog */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent
          className="max-w-xl max-h-[90vh] overflow-y-auto"
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>{editStandard ? t('k_832f332943') : t('k_d6f71ba848')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium">{t('k_62f403bc0b')}</label>
              <Input
                className="mt-1"
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder={t("k_2c9348c596")}
              />
            </div>
            <div>
              <label className="text-sm font-medium">{t('k_8ef276b9cf')}</label>
              <Select
                value={form.standard_type}
                onValueChange={v => setForm(f => ({ ...f, standard_type: v }))}
              >
                <SelectTrigger className="mt-1">
                  <SelectValue placeholder={t("k_3941d2e565")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="naming">{t("k_4d443c5242")}</SelectItem>
                  <SelectItem value="encoding">{t("k_954c95280e")}</SelectItem>
                  <SelectItem value="measurement">{t("k_d6e94ede0c")}</SelectItem>
                  <SelectItem value="format">{t("k_b5720ccbe8")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-sm font-medium">{t("k_412f54dc38")}</label>
              <Input
                className="mt-1"
                value={form.description}
                onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                placeholder={t("k_ff09c92e48")}
              />
            </div>
            <div>
              <label className="text-sm font-medium">{t('k_f9ae4186e1')}</label>
              <textarea
                className="mt-1 w-full min-h-[120px] rounded-md border border-input bg-background px-3 py-2 text-sm font-mono"
                value={form.rule_config}
                onChange={e => setForm(f => ({ ...f, rule_config: e.target.value }))}
                placeholder='{"pattern": "^[a-z][a-z0-9_]*$", "max_length": 64}'
              />
              {jsonError && (
                <p className="text-sm text-red-500 mt-1">{jsonError}</p>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSave}>{editStandard ? t('k_d9db02d07a') : t('k_fcbd093292')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("k_3c06abe116")}</DialogTitle>
            <DialogDescription>
              {t('k_790f88abab', { p0: deleteTarget?.name })}</DialogDescription>
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
