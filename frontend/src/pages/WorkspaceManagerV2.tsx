import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Spinner } from '@/components/ui/spinner';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog';
import { toast } from '@/lib/toast';
import { Search, Trash2, RefreshCw, Settings2 } from 'lucide-react';
import client from '@/api/client';
import { PaginationBar, DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { WorkspaceDeleteDialog } from '@/components/WorkspaceDeleteDialog';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

// 管理员统管: 工作空间为用户个人空间(随用户走, 无成员协作)，用户侧自建入口已关，
// 管理员在此按用户设可建空间数上限与每空间磁盘配额，并查看磁盘用量。
//
// 列表按**用户**服务端真分页（总数来自 COUNT，翻页重新请求，不在前端切片）；
// 配额列的用量取该用户**单个空间的最大值**（与"每空间配额"同口径才可比），
// 且只扫当前页用户的空间；逐空间明细在详情里算 —— 递归扫盘成本不随全库增长。

interface QuotaUserRow {
  user_id: number;
  username: string;
  max_workspaces: number;
  disk_quota_bytes: number;
  /** 该用户单个空间的最大用量（与"每空间配额"同口径才可比） */
  disk_usage_bytes: number;
  /** 后端下发的"生效配额"是否为系统缺省（管理员未显式配置过）。
   *  显示值必须与实际拦截门同源，缺省时也要看得见，避免"页面显示有配额、实际不拦"的双口径。 */
  quota_is_default?: boolean;
}

interface QuotaWorkspace {
  id: number;
  name: string;
  is_default: number;
  disk_usage_bytes: number;
}

interface QuotaDetail extends QuotaUserRow {
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

/** 每空间磁盘配额的口径文字：0 是管理员显式登记的"不设限"逃生口，不是缺省值 */
const quotaText = (bytes: number, isDefault?: boolean) => (
  bytes > 0
    ? `${tr('k_d57eabbdd2')} ${fmtBytes(bytes)}${isDefault ? tr('k_9f611db98f') : ''}`
    : tr('k_c6961d6f86')
);

/** 配额列的用量数字：进度条给比例，文字给绝对值（无上限时只报已用） */
const usageText = (used: number, quota: number) => (
  quota > 0
    ? `${fmtBytes(used)} / ${fmtBytes(quota)}`
    : `${fmtBytes(used)} / ${tr('k_c6961d6f86')}`
);

/** 用量进度条：只画「已用 / 每空间配额」这一个比例。
 *  - 用量再小也要看得见：6.8 MB / 500 MB 只有 1.4%，轨道宽不到 2px 会被读成"没用过"，故给最小可见宽度；
 *  - 配额 0 = 管理员显式登记的"不设限"，没有比例可画 —— 不渲空条冒充没用过，只留文字读数。 */
function UsageBar({ used, quota }: { used: number; quota: number }) {
  if (quota <= 0) return null;
  const pct = Math.min(100, (used / quota) * 100);
  return (
    <div className="h-1.5 w-full rounded bg-muted overflow-hidden">
      <div
        className={`h-1.5 rounded ${pct >= 90 ? 'bg-destructive' : pct >= 70 ? 'bg-amber-500' : 'bg-primary'}`}
        style={{ width: `${pct}%`, minWidth: used > 0 ? 6 : 0 }}
      />
    </div>
  );
}

export default function WorkspaceManagerV2() {
  const { t } = useTranslation('ui');
  const [rows, setRows] = useState<QuotaUserRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [keyword, setKeyword] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [loading, setLoading] = useState(false);

  const [quotaTarget, setQuotaTarget] = useState<QuotaUserRow | null>(null);
  const [editMax, setEditMax] = useState('5');
  const [editDiskGb, setEditDiskGb] = useState('5');
  const [saving, setSaving] = useState(false);

  const [detailTarget, setDetailTarget] = useState<QuotaUserRow | null>(null);
  const [detail, setDetail] = useState<QuotaDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');

  const [deleteWs, setDeleteWs] = useState<{ user: QuotaUserRow; ws: QuotaWorkspace } | null>(null);

  // 搜索防抖 + 改条件即回第 1 页（停在旧页码 + 新关键词会拿到错位的空清单）
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQ(keyword.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [keyword]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/workspaces/quotas', {
        params: { page, page_size: pageSize, q: debouncedQ },
      });
      setRows(Array.isArray(data?.items) ? data.items : []);
      setTotal(Number(data?.total) || 0);
    } catch {
      toast.error(tr('k_26b4ea3cd1'));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, debouncedQ]);

  useEffect(() => { load(); }, [load]);

  const loadDetail = useCallback(async (userId: number) => {
    setDetailLoading(true);
    setDetailError('');
    try {
      const { data } = await client.get(`/workspaces/quotas/${userId}`);
      setDetail(data);
    } catch (e: any) {
      // 详情失败必须显式（可重试），不得静默渲成"该用户没有空间"
      setDetail(null);
      setDetailError(e?.response?.data?.detail || tr('k_8929cd2c27'));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const openDetail = (u: QuotaUserRow) => {
    setDetailTarget(u);
    setDetail(null);
    loadDetail(u.user_id);
  };

  const openQuota = (u: QuotaUserRow) => {
    setQuotaTarget(u);
    setEditMax(String(u.max_workspaces));
    setEditDiskGb(String(Math.round((u.disk_quota_bytes / 1024 ** 3) * 10) / 10));
  };

  const saveQuota = async () => {
    if (!quotaTarget) return;
    const maxWs = Number(editMax);
    const diskBytes = Math.round(Number(editDiskGb) * 1024 ** 3);
    if (!Number.isFinite(maxWs) || maxWs < 1 || !Number.isFinite(diskBytes) || diskBytes < 1) {
      toast.error(tr('k_d655915298'));
      return;
    }
    setSaving(true);
    try {
      await client.put(`/workspaces/quotas/${quotaTarget.user_id}`, {
        max_workspaces: maxWs,
        disk_quota_bytes: diskBytes,
      });
      toast.success(tr('k_d233c83ffd'));
      const targetId = quotaTarget.user_id;
      setQuotaTarget(null);
      await load();
      if (detailTarget?.user_id === targetId) loadDetail(targetId);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || tr('k_6de920b4e4'));
    } finally {
      setSaving(false);
    }
  };

  const removeWs = () => {
    if (!deleteWs) return;
    const userId = deleteWs.user.user_id;
    setDeleteWs(null);
    load();
    loadDetail(userId);
  };

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex items-center justify-between gap-3 shrink-0 flex-wrap">
        <div>
          <h1 className="text-lg font-semibold">{t("k_c8b912ff8c")}</h1>
          <p className="text-muted-foreground text-xs mt-1">{t('k_47e27a3014')}</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="h-4 w-4 absolute left-2.5 top-2.5 text-muted-foreground" />
            <Input
              className="w-[220px] pl-8" value={keyword}
              onChange={e => setKeyword(e.target.value)}
              placeholder={t('k_801b0a1216')}
            />
          </div>
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
            {t('k_38108eaa1d')}
          </Button>
        </div>
      </div>

      {/* 仅此区滚动（页头/分页固定，参考表&字段） */}
      <div className="flex-1 min-h-0 border rounded-lg overflow-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-muted/60 text-xs text-muted-foreground">
            <tr className="text-left">
              <th className="px-4 py-2 font-medium w-[180px]">{t('k_a1aaf352cb')}</th>
              <th className="px-4 py-2 font-medium">{t('k_2d92e66133')}</th>
              <th className="px-4 py-2 font-medium w-[180px] text-right">{t('k_f3ea6d345e')}</th>
            </tr>
          </thead>
          <tbody>
            {loading && rows.length === 0 ? (
              <tr><td colSpan={3} className="px-4 py-8 text-center text-muted-foreground">{t('k_26b5bd4947')}</td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={3} className="px-4 py-8 text-center text-muted-foreground">{t("k_3d268bdd39")}</td></tr>
            ) : rows.map(u => (
              <tr
                key={u.user_id}
                className="border-t hover:bg-muted/40 cursor-pointer"
                onClick={() => openDetail(u)}
              >
                <td className="px-4 py-2 font-medium truncate" title={u.username}>{u.username}</td>
                {/* 配额来源不再单列（没人在「配额设置」里登记过时，整列都是同一个"系统缺省"）；
                    缺省与显式配置的区分收进这里：缺省带后缀，来源细节放 title。 */}
                <td className="px-4 py-2">
                  <div
                    className="flex items-center gap-2"
                    title={u.quota_is_default ? t('k_2684e4ee00') : t('k_dbd61ece7f')}
                  >
                    <UsageBar used={u.disk_usage_bytes} quota={u.disk_quota_bytes} />
                    <span className="text-xs tabular-nums whitespace-nowrap text-muted-foreground">
                      <span>{usageText(u.disk_usage_bytes, u.disk_quota_bytes)}</span>
                      {u.quota_is_default ? <span>{t('k_9f611db98f')}</span> : null}
                    </span>
                  </div>
                </td>
                <td className="px-4 py-2">
                  <div className="flex items-center justify-end gap-2" onClick={e => e.stopPropagation()}>
                    <Button variant="ghost" size="sm" onClick={() => openDetail(u)}>{t('k_4f55ee1e68')}</Button>
                    <Button variant="outline" size="sm" onClick={() => openQuota(u)}>
                      <Settings2 className="h-4 w-4 mr-1" />
                      {t('k_9ed63f5074')}
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 分页条在滚动区之外：总数来自服务端 COUNT，翻页重新请求 */}
      <PaginationBar
        page={page} pageSize={pageSize} total={total} busy={loading}
        onChange={setPage}
        onPageSizeChange={n => { setPageSize(n); setPage(1); }}
      />

      {/* 详情: 该用户的空间清单与磁盘用量 */}
      <Dialog open={!!detailTarget} onOpenChange={open => { if (!open) setDetailTarget(null); }}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t('k_ce5d513dc7')} {detailTarget?.username}</DialogTitle>
            <DialogDescription>
              {quotaText(detailTarget?.disk_quota_bytes ?? 0, detailTarget?.quota_is_default)}
            </DialogDescription>
          </DialogHeader>

          {detailLoading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-muted-foreground">
              <Spinner className="h-4 w-4" />{t('k_26b5bd4947')}
            </div>
          ) : detailError ? (
            <div className="space-y-3 py-4">
              <p className="text-sm text-destructive">{detailError}</p>
              <Button variant="outline" size="sm" onClick={() => detailTarget && loadDetail(detailTarget.user_id)}>
                <RefreshCw className="h-4 w-4 mr-1" />{t('k_38108eaa1d')}
              </Button>
            </div>
          ) : detail?.workspaces.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4">{t("k_7a5f8a85b4")}</p>
          ) : (
            <div className="divide-y">
              {(detail?.workspaces || []).map(ws => {
                const quota = detail?.disk_quota_bytes ?? 0;
                const pct = quota > 0 ? Math.round((ws.disk_usage_bytes / quota) * 100) : 0;
                return (
                  <div key={ws.id} className="flex items-center gap-3 py-3">
                    <div className="flex items-center gap-2 min-w-0 w-[180px]">
                      <span className="text-sm font-medium truncate">{ws.name}</span>
                      {ws.is_default ? (
                        <Badge variant="outline" className="text-[10px] shrink-0"
                          title={t("k_3d776cb51b")}>{t("k_c8d09cf955")}</Badge>
                      ) : null}
                    </div>
                    <div className="flex-1 space-y-1">
                      <div className="flex justify-between text-xs text-muted-foreground">
                        <span>{t("k_240cb16114")}</span>
                        <span className="tabular-nums">
                          {fmtBytes(ws.disk_usage_bytes)} / {quota > 0 ? fmtBytes(quota) : t('k_c6961d6f86')}
                          {pct >= 90 ? t('k_8ddbcf2691') : ''}
                        </span>
                      </div>
                      <UsageBar used={ws.disk_usage_bytes} quota={quota} />
                    </div>
                    {/* 默认工作空间不给删(与用户侧下拉、后端 409 同一口径) */}
                    {!ws.is_default && (
                      <Button
                        variant="ghost" size="sm" className="text-destructive shrink-0"
                        onClick={() => detailTarget && setDeleteWs({ user: detailTarget, ws })}
                        title={t("k_0717b53396")}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <DialogFooter>
            {detailTarget && (
              <Button variant="outline" onClick={() => openQuota(detailTarget)}>
                <Settings2 className="h-4 w-4 mr-1" />{t('k_9ed63f5074')}
              </Button>
            )}
            <Button onClick={() => setDetailTarget(null)}>{t("k_4d0b4688c7")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 配额设置 */}
      <Dialog open={!!quotaTarget} onOpenChange={open => { if (!open) setQuotaTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('k_6c147a1911')}{' '}{quotaTarget?.username}</DialogTitle>
            <DialogDescription>{t("k_560f6d5cc0")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>{t("k_fc5cdea640")}</Label>
              <Input type="number" min={1} value={editMax} onChange={e => setEditMax(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label>{t("k_788216c066")}</Label>
              <Input type="number" min={0.1} step={0.1} value={editDiskGb} onChange={e => setEditDiskGb(e.target.value)} />
              <p className="text-xs text-muted-foreground">
                {t('k_55f7c50f1f')}{quotaTarget?.quota_is_default
                  ? t('k_6327866d41')
                  : t('k_d28da7a760')}
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setQuotaTarget(null)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={saveQuota} disabled={saving}>{saving ? t('k_2a33020e5b') : t('k_fadf24dbc5')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认（与用户侧下拉共用：先展示影响清单再确认） */}
      <WorkspaceDeleteDialog
        workspace={deleteWs ? deleteWs.ws : null} open={!!deleteWs}
        onOpenChange={open => { if (!open) setDeleteWs(null); }}
        onDeleted={removeWs}
      />
    </div>
  );
}
