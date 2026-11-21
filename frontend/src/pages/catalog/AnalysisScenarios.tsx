import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/lib/toast';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { Plus, Edit, Trash2, Workflow, Search, BookX } from 'lucide-react';
import client from '@/api/client';
import { useTranslation } from 'react-i18next';
import ScenarioEditorDialog, {
  splitTriggers, useScenarioFormOptions, type ScenarioItem,
} from '@/components/ScenarioEditorDialog';

/**
 * 分析场景管理页（清单 + 分诊）。
 *
 * 清单口径：分页表格、列只放"能扫读"的关键信息 —— 触发词与查询步骤正文**不进清单**
 * （它们是一整段配置详情，平铺两三条就把清单撑成整屏，反而看不清有哪些场景）。
 *
 * 编辑表单已抽到 `ScenarioEditorDialog`：主体授权、报告模板候选、三条必填校验都在那里，
 * 进化中心的「知识更新」也要用同一套，复制一份必然两处口径漂移。
 */

export default function AnalysisScenarios() {
  const { t } = useTranslation('ui');
  const [items, setItems] = useState<ScenarioItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [searchText, setSearchText] = useState('');
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editItem, setEditItem] = useState<ScenarioItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ScenarioItem | null>(null);
  const [PAGE_SIZE, setPageSize] = useState(DEFAULT_PAGE_SIZE);

  // 主体与报告模板候选：与权限判定同一数据源，加载失败显式报错重试（不静默成空下拉）
  const options = useScenarioFormOptions();

  // ── Load items ───────────────────────────────────────────────────

  const loadItems = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, any> = {
        workspace_id: 0,
        knowledge_type: 'quick_path',
        page,
        size: PAGE_SIZE,
      };
      if (searchText) params.search = searchText;
      const { data } = await client.get('/admin/knowledge', { params });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch (e: any) {
      // 404 = 暂无数据，不弹 toast
      if (e?.response?.status && e.response.status >= 500) {
        toast.error(t('k_866b795eae'));
      }
    } finally {
      setLoading(false);
    }
  }, [page, PAGE_SIZE, searchText]);

  useEffect(() => { loadItems(); }, [loadItems]);

  // ── Form handling ────────────────────────────────────────────────

  const openCreate = () => {
    setEditItem(null);
    setFormOpen(true);
  };

  const openEdit = (item: ScenarioItem) => {
    setEditItem(item);
    setFormOpen(true);
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await client.delete(`/admin/knowledge/${deleteTarget.id}`);
      toast.success(t('k_5cc232620c'));
      setDeleteTarget(null);
      loadItems();
    } catch {
      toast.error(t('k_acf0664a54'));
    }
  };

  const handleToggle = async (item: ScenarioItem) => {
    try {
      await client.patch(`/admin/knowledge/${item.id}/toggle`);
      loadItems();
    } catch {
      toast.error(t('k_c2312c491e'));
    }
  };

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between mb-6 shrink-0">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Workflow className="h-6 w-6" />
            {t('k_b2be89d785')}
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            {t('k_faf550d2a6')}
          </p>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4 mr-1" />{t('k_5bd56c5159')}
        </Button>
      </div>

      {/* Search */}
      <div className="mb-4 shrink-0">
        <div className="relative">
          <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder={t('k_e0cba2043d')}
            value={searchText}
            onChange={e => { setSearchText(e.target.value); setPage(1); }}
            className="pl-8 w-64"
          />
        </div>
      </div>

      {/* 清单：分页表格（不用卡片式）。列只留“能扫读”的关键信息，
          触发词与查询步骤正文不进列表——它们是一整段配置详情，平铺在清单里
          会把两三条场景撑成整屏，反而看不清有哪些场景（去编辑弹窗里看）。 */}
      <div className="flex-1 min-h-0 overflow-auto">
      {loading ? (
        <div className="text-center py-8 text-muted-foreground">{t('k_26b5bd4947')}</div>
      ) : items.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground">
          <BookX className="h-10 w-10 mx-auto mb-3 opacity-30" />
          <p className="text-sm">{t("k_dc1a608775")}</p>
          <p className="text-xs mt-1">{t("k_0697d54ecb")}</p>
        </div>
      ) : (
        <div className="rounded-lg border bg-card">
          <table className="w-full">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">{t("k_7b345ebc76")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground">{t("k_4731a17e52")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground w-[90px]">{t("k_7cabe771eb")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground w-[80px]">{t("k_d4e9ca3dd4")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground w-[70px]">{t("k_7a130d7fde")}</th>
                <th className="h-10 px-4 text-left text-sm font-medium text-muted-foreground w-[160px]">{t("k_093dea88c9")}</th>
                <th className="h-10 px-4 text-right text-sm font-medium text-muted-foreground w-[90px]">{t("k_f3ea6d345e")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map(item => {
                const noTrigger = splitTriggers((item.metadata?.triggers || []).join(',')).length === 0;
                const subjectText = item.subject_all ? t('k_5216e74601')
                  : (item.subject_names || []).join(t('k_b505660a97')) || t('k_737f192a0d');
                return (
                  <tr key={item.id} className="border-b last:border-b-0 hover:bg-muted/30">
                    <td className="px-4 py-2 min-w-0">
                      <div className="flex items-center gap-2">
                        <Workflow className="h-3.5 w-3.5 shrink-0 text-primary" />
                        <span className="text-sm font-medium truncate" title={item.title}>{item.title}</span>
                      </div>
                      {noTrigger && (
                        <p className="mt-0.5 pl-5 text-xs text-amber-600">{t("k_acf7bde256")}</p>
                      )}
                      {/* 绑了哪个报告模板属于配置事实，要能在清单里扫到（不然只能逐条开弹窗看） */}
                      {item.metadata?.report_template && (
                        <p className="mt-0.5 pl-5 text-xs text-muted-foreground"
                          title={t("k_3aeb0a262d")}>
                          {t('k_08ff53ef7d', { p0: item.metadata.report_template })}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-2 min-w-0">
                      <span className="text-sm" title={t('k_65e7ca7c61')}>{subjectText}</span>
                      {/* 不可见原因属于配置事实，必须当场看见（而不是到问答里发现“怎么没生效”） */}
                      {item.visible_to_me === false && item.visibility_note && (
                        <p className="mt-0.5 text-xs text-amber-600">{item.visibility_note}</p>
                      )}
                    </td>
                    <td className="px-4 py-2 text-sm tabular-nums">{item.priority}</td>
                    <td className="px-4 py-2">
                      <Switch checked={!!item.is_active} onCheckedChange={() => handleToggle(item)} />
                    </td>
                    <td className="px-4 py-2 text-sm tabular-nums text-muted-foreground" title={t("k_728e7f3434")}>
                      {item.usage_count}
                    </td>
                    <td className="px-4 py-2 text-xs text-muted-foreground">{item.updated_at}</td>
                    <td className="px-4 py-2">
                      <div className="flex items-center justify-end gap-1">
                        <Button variant="ghost" size="sm" title={t("k_1958a21d26")} onClick={() => openEdit(item)}>
                          <Edit className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="sm" title={t("k_aa8aa282e6")} onClick={() => setDeleteTarget(item)}>
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      </div>

      {/* Pagination —— 统一分页条：固定底栏（仅清单区滚动，页头/分页不随页面滚走） */}
      <PaginationBar
        page={page}
        pageSize={PAGE_SIZE}
        total={total}
        busy={loading}
        onChange={(p: number) => setPage(p)}
        onPageSizeChange={(n: number) => { setPageSize(n); setPage(1); }}
        hideWhenSingle
        className="shrink-0 mt-2"
      />

      {/* ── Create/Edit Dialog（共享组件）───────────────────────────── */}
      <ScenarioEditorDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        options={options}
        item={editItem}
        onSaved={() => loadItems()}
      />

      {/* ── Delete Confirmation ─────────────────────────────────── */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("k_3c06abe116")}</DialogTitle>
            <DialogDescription>
              {t('k_5851dad150', { p0: deleteTarget?.title })}</DialogDescription>
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