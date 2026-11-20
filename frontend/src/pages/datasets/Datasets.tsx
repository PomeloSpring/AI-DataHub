import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { t , t as tr } from '@/i18n/runtime';
import {
  Plus, Layers, Code2, Trash2, Eye, Loader2, Check,
  RefreshCw, BarChart3, Square, CheckSquare, Database, AlertTriangle,
  Share2, ShieldCheck, ShieldOff,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import client from '../../api/client';
import { hasPerm } from '../../stores/permissionStore';
import { CHART_TYPES } from '../../components/DashboardChart';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { formatDateTime } from '@/lib/format';
import DatasetBatchSourceDialog from './DatasetBatchSourceDialog';
import ShareDatasetDialog from './ShareDatasetDialog';
import { useTranslation } from 'react-i18next';

interface Dataset {
  id: number;
  name: string;
  description: string;
  source_type: 'semantic' | 'sql' | 'both';
  object_key: string;
  datasource_id: number;
  datasource_name?: string;
  /** 执行通道位（服务端 `execution_is_es`下发）：正文是否跑在 ES 型数据源上 */
  is_es?: boolean;
  /** 可用数据源范围(业务名)：执行源 ∪ available_datasources，经后端按授权交集过滤后返回 */
  available_source_names?: string[];
  /** 当前用户在范围内真正有权限执行的源(业务名) */
  usable_now?: string[];
  chart_type: string;
  owner_id: number;
  /** 归属（服务端 JOIN adh_users 下发，页面显示业务名而非裸 id） */
  owner_name?: string;
  /** 可见范围：private=仅创建者 / workspace=按角色共享 / public=保留未启用 */
  visibility?: 'private' | 'workspace' | 'public';
  visibility_label?: string;
  /** 当前用户是否可编辑/共享（admin 或属主） */
  editable?: boolean;
  is_owner?: boolean;
  /** 当前身份是否真有可执行源（分页档显式下发，false=标“不可用”，不静默藏行） */
  executable?: boolean;
  field_count: number;
  references: number;
  updated_at: string;
}

// 来源类型显示业务名（不得把内部枚举 semantic/both 直接当名称展示，ui-resource-display §1）
// “es” 这个取值已随内置通道退役（库里 0 行、写入被后端显式拒），ES 数据集的 source_type 就是 sql，
// ES 身份由服务端的 `is_es` 下发（判据与取数分派同源），不靠枚举名反推。
const SOURCE_LABELS: Record<string, string> = {
  sql: 'SQL',
  semantic: tr('k_668cea6a49'),
  both: tr('k_764b4b7d97'),
};
// 图表预设只支持真实图表(排除参数控件)
const PRESET_CHART_TYPES = CHART_TYPES.filter(t => t.category !== 'widget');

/** 筛选只有「数据源」一个维度 —— “ES 取数通道”这一项已于 2026-11-13 撤掉。
 *
 * 它原本是内置 MCP 通道时代的“按通道筛”，通道退役后被改写成“正文执行在 ES 型数据源上”
 * （`GET /datasets/?channel=es`）。但现网只有 1 个 ES 源，选它与直接选 `cloud-platform-es`
 * 结果逐字相同，而“取数通道”这个词会让人以为**还有一条不走数据源的取数路**（实测：用户就
 * 这么误读了）。按 `ui-resource-display.md`“不留看得见但语义含混/重复的选项”，前端不再提供；
 * 后端 `channel` 参数暂留（脚本/将来多 ES 源场景可用，随阶段三第 2 步一并拆除）。
 */

export default function Datasets() {
  const { t } = useTranslation('ui');
  const navigate = useNavigate();
  const [items, setItems] = useState<Dataset[]>([]);
  const [loading, setLoading] = useState(false);
  // 搜索框输入与生效关键词分开：停手 300ms 再发请求（list-pagination §3）
  const [kwInput, setKwInput] = useState('');
  const [keyword, setKeyword] = useState('');
  // 筛选项：'0'=全部 | '<id>'=具体数据源（ES 型数据集也挂在 ES 数据源上，按源筛自然涵盖）
  const [sourceFilter, setSourceFilter] = useState('0');
  // 筛选：可见范围 'all' | 'private' | 'workspace'
  const [visibilityFilter, setVisibilityFilter] = useState('all');
  const [datasources, setDatasources] = useState<any[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<any>(null);
  // 批量操作：开关式批量模式（默认卡片不挂勾选框，点「批量操作」才进入）+ 多选 + 逐条回报结果
  const [batchMode, setBatchMode] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [batchSourceOpen, setBatchSourceOpen] = useState(false);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchResult, setBatchResult] = useState<any>(null);
  // 共享对话框（private ⇄ workspace）：只对「管理权限 ∧ 该行可编辑」的行开放
  const [shareTarget, setShareTarget] = useState<Dataset | null>(null);
  const canManage = hasPerm('dataset:manage');

  // 服务端真分页：total 来自服务端 COUNT（不在前端切片，不用 useClientPagination）
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [total, setTotal] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/datasets/', {
        params: {
          keyword,
          datasource_id: Number(sourceFilter) || 0,
          visibility: visibilityFilter === 'all' ? '' : visibilityFilter,
          page,
          page_size: pageSize,
        },
      });
      const rows: Dataset[] = data.items || [];
      setItems(rows);
      setTotal(Number(data.total ?? rows.length));
      // 末页被删空时收敛到最后一个有效页（不停在空白页）
      if (rows.length === 0 && page > 1) setPage(p => Math.max(1, p - 1));
    } catch (e: any) {
      // 清单拉失败不得静默渲成「暂无数据集」（会被误读为数据丢了），显式提醒
      setItems([]);
      setTotal(0);
      toast.error(e?.response?.data?.detail || tr('k_65aaefc869'));
    } finally {
      setLoading(false);
    }
  }, [keyword, sourceFilter, visibilityFilter, page, pageSize]);

  useEffect(() => { load(); }, [load]);

  // 搜索防抖（停手再发请求）
  useEffect(() => {
    const id = setTimeout(() => setKeyword(kwInput.trim()), 300);
    return () => clearTimeout(id);
  }, [kwInput]);

  // 筛选/搜索/每页条数变化 → 回到第 1 页（停在旧页码会拿到错位空清单）
  useEffect(() => { setPage(1); }, [keyword, sourceFilter, visibilityFilter, pageSize]);

  // 数据源筛选器选项（显示 name、value=id，符合“显示用 name、选择用选择框”规范）
  useEffect(() => {
    client.get('/datasources/')
      .then(({ data }) => setDatasources(Array.isArray(data) ? data : (data?.items || [])))
      .catch(() => setDatasources([]));
  }, []);

  const handleDelete = async (ds: Dataset) => {
    if (!confirm(tr('k_b60c1886cc', { p0: ds.name }))) return;
    try {
      await client.delete(`/datasets/${ds.id}`);
      toast.success(tr('k_5cc232620c'));
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || tr('k_acf0664a54'));
    }
  };

  // ── 批量操作：多选 + 逐条复用单条接口 + 逐条回报（失败项保持勾选可重试）──
  const toggleSelect = (id: number) =>
    setSelected(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  const clearSelection = () => setSelected([]);
  const exitBatchMode = () => { setBatchMode(false); setSelected([]); };
  const pageIds = () => items.map(d => d.id);
  const allPageSelected = items.length > 0 && items.every(d => selected.includes(d.id));
  const toggleSelectPage = () => setSelected(prev => {
    const ids = pageIds();
    return allPageSelected ? prev.filter(x => !ids.includes(x)) : [...new Set([...prev, ...ids])];
  });

  // 清单刷新后丢掉已不存在（如已删）的勾选项，避免拿脏 id 提交
  useEffect(() => {
    setSelected(prev => {
      const alive = new Set(items.map(i => i.id));
      const next = prev.filter(id => alive.has(id));
      return next.length === prev.length ? prev : next;
    });
  }, [items]);

  const handleBatchDelete = async () => {
    if (!selected.length) return;
    if (!confirm(tr('k_3e3a4989e3', { p0: selected.length }))) return;
    setBatchBusy(true);
    try {
      const { data } = await client.post('/datasets/batch-delete', { ids: selected });
      setBatchResult({ kind: 'delete', ...data });
      setSelected((data.failed || []).map((f: any) => f.id));  // 失败项保持勾选
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || tr('k_1bac376d51'));
    } finally {
      setBatchBusy(false);
    }
  };

  const handleBatchSourceSubmit = async (patch: { datasource_id?: number; available_datasources?: string[] }) => {
    if (!selected.length) return;
    setBatchBusy(true);
    try {
      const { data } = await client.post('/datasets/batch-update-datasource', { ids: selected, ...patch });
      setBatchResult({ kind: 'source', ...data });
      setSelected((data.failed || []).map((f: any) => f.id));
      setBatchSourceOpen(false);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || tr('k_24182b2345'));
    } finally {
      setBatchBusy(false);
    }
  };

  // 手动同步数据集到业务本体(走 generate-business-assets 的 save_draft/activate 级联)
  const handleSync = async () => {
    setSyncing(true);
    try {
      const { data } = await client.post('/catalog/ontology/generate-business-assets');
      setSyncResult(data);
      (data.notes || []).forEach((n: string) => toast.warning(n)); // 告警显式暴露, 不静默
      toast.success(tr('k_8ec9e239aa', { p0: data.object_count ?? 0 }));
    } catch (e: any) {
      toast.error(e.response?.data?.detail || tr('k_6e55927480'));
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between mb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Layers className="h-6 w-6" /> {tr('k_fc2b4735c3')}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {tr('k_5f4b0a1d5b')}</p>
        </div>
        <div className="flex items-center gap-2">
          {canManage && (
            <Button size="sm" variant={batchMode ? 'secondary' : 'outline'}
              aria-pressed={batchMode} onClick={() => (batchMode ? exitBatchMode() : setBatchMode(true))}>
              <CheckSquare className="h-4 w-4 mr-1" /> {batchMode ? tr('k_07c2859e08') : tr('k_7f7c624a84')}
            </Button>
          )}
          {canManage && (
            <Button size="sm" variant="outline" onClick={handleSync} disabled={syncing}>
              {syncing
                ? <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                : <RefreshCw className="h-4 w-4 mr-1" />}
              {tr('k_7d8d985efc')}</Button>
          )}
          {canManage && (
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4 mr-1" /> {tr('k_e78bd5fabb')}</Button>
          )}
        </div>
      </div>

      {/* 筛选项：关键字（防抖）/ 数据源 / 可见范围；筛选全部下推服务端（不在前端再 filter） */}
      <div className="flex gap-2 mb-4 flex-wrap shrink-0">
        <Input
          placeholder={t("k_b05ef3f713")}
          value={kwInput}
          onChange={(e) => setKwInput(e.target.value)}
          className="max-w-xs"
        />
        <Select
          value={sourceFilter}
          onValueChange={setSourceFilter}
        >
          <SelectTrigger className="w-56 h-9" aria-label={t("k_3a38ce0110")}>
            <SelectValue placeholder={t("k_f8d22fd22e")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="0">{t("k_f8d22fd22e")}</SelectItem>
            {/* 只留「数据源」维度：“ES 取数通道”那组已撤（与按 ES 源筛重复且词容易误读，
                理由见文首 ES 通道注释）；数据源清单回源 `/datasources/`，不写死。 */}
            {datasources.map((d: any) => (
              <SelectItem key={d.id} value={String(d.id)}>{d.name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={visibilityFilter} onValueChange={setVisibilityFilter}>
          <SelectTrigger className="w-44 h-9" aria-label={t("k_7b52e18fa3")}>
            <SelectValue placeholder={t("k_778fc8f994")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{tr('k_778fc8f994')}</SelectItem>
            <SelectItem value="private">{tr('k_9e40c7b158')}</SelectItem>
            <SelectItem value="workspace">{tr('k_2f81d5ae64')}</SelectItem>
          </SelectContent>
        </Select>
        {(kwInput || sourceFilter !== '0' || visibilityFilter !== 'all') && (
          <Button size="sm" variant="ghost" className="h-9"
            onClick={() => { setKwInput(''); setSourceFilter('0'); setVisibilityFilter('all'); }}>
            {tr('k_3d81345303')}</Button>
        )}
      </div>

      {/* 批量操作条：进入批量模式即出现（含全选，未选时也可先全选）；未选时动作按钮置灰 */}
      {canManage && batchMode && (
        <div className="flex items-center gap-2 mb-4 shrink-0 rounded-md border bg-muted/30 px-3 py-2 flex-wrap">
          <Button size="sm" variant="outline" className="h-8 text-xs" onClick={toggleSelectPage}>
            {allPageSelected ? <CheckSquare className="h-4 w-4 mr-1" /> : <Square className="h-4 w-4 mr-1" />}
            {tr('k_c8121d9d63')}</Button>
          {selected.length > 0 && (
            <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={clearSelection}>{t("k_a6d3de0e86")}</Button>
          )}
          <span className="text-sm text-muted-foreground">{t("k_f24ddc2bef")} <b className="text-foreground">{selected.length}</b> {t("k_f7b2a6ee68")}</span>
          <div className="flex-1" />
          <Button size="sm" variant="outline" onClick={() => setBatchSourceOpen(true)} disabled={batchBusy || selected.length === 0}>
            <Database className="h-4 w-4 mr-1" /> {tr('k_0b9837b627')}</Button>
          <Button size="sm" variant="destructive" onClick={handleBatchDelete} disabled={batchBusy || selected.length === 0}>
            {batchBusy ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Trash2 className="h-4 w-4 mr-1" />}
            {tr('k_7fb62b3011')}</Button>
        </div>
      )}

      {/* 清单区：表格式列表（表头固定 + 仅此区域滚动），分页由服务端驱动 */}
      <div className="flex-1 min-h-0 overflow-auto border rounded-lg">
      {loading ? (
        <div className="flex justify-center py-12 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground">
          {tr('k_c46e035dbe')}{canManage ? tr('k_1e2ac39e19') : ''}
        </div>
      ) : (
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-background">
            <TableRow>
              {/* 批量模式下多一列勾选框；全选按钮只在顶部批量操作条上（避免同名按钮两处） */}
              {canManage && batchMode && <TableHead className="w-10" />}
              <TableHead className="min-w-[220px]">{tr('k_1be7ae4fc2')}</TableHead>
              <TableHead>{tr('k_c63f79e636')}</TableHead>
              <TableHead>{tr('k_a3ccf702c5')}</TableHead>
              <TableHead className="text-right">{tr('k_77a49f2c38')}</TableHead>
              <TableHead className="text-right">{tr('k_23c0e102a0')}</TableHead>
              <TableHead>{tr('k_3a91f0c4d7')}</TableHead>
              <TableHead>{tr('k_7b52e18fa3')}</TableHead>
              <TableHead>{tr('k_093dea88c9')}</TableHead>
              <TableHead className="text-right">{tr('k_f3ea6d345e')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map(ds => (
              <TableRow key={ds.id} className="hover:bg-muted/40">
                {canManage && batchMode && (
                  <TableCell className="w-10">
                    <button type="button" aria-label={tr('k_f7c57733fd', { p0: ds.name })} onClick={() => toggleSelect(ds.id)}
                      className="text-muted-foreground hover:text-foreground">
                      {selected.includes(ds.id)
                        ? <CheckSquare className="h-4 w-4 text-primary" />
                        : <Square className="h-4 w-4" />}
                    </button>
                  </TableCell>
                )}
                <TableCell className="py-2">
                  <button className="text-left min-w-0" onClick={() => navigate(`/data/datasets/${ds.id}`)}>
                    <div className="font-medium truncate">{ds.name}</div>
                    {ds.description && (
                      <p className="text-xs text-muted-foreground mt-0.5 truncate max-w-[380px]"
                        title={ds.description}>{ds.description}</p>
                    )}
                  </button>
                </TableCell>
                <TableCell className="py-2">
                  <div className="flex items-center gap-1 flex-wrap">
                    <Badge variant="outline" className="text-[10px] flex items-center gap-1">
                      <Code2 className="h-3 w-3" />
                      {SOURCE_LABELS[ds.source_type] || ds.source_type || 'SQL'}
                    </Badge>
                    {/* 执行通道位只从服务端 `is_es` 读：这一位与其他源同一个治理口 */}
                    {ds.is_es && (
                      <Badge variant="secondary" className="text-[10px]" title={t("k_e7f2685f92")}>
                        {tr('k_645f449115')}</Badge>
                    )}
                  </div>
                </TableCell>
                <TableCell className="py-2">
                  <div className="flex items-center gap-1 flex-wrap">
                    <Badge variant="outline" className="text-[10px]"
                      title={tr('k_8b470f2735', { p0: ds.datasource_name || tr('k_a3ccf702c5') })}>
                      {ds.datasource_name || tr('k_a3ccf702c5')}
                    </Badge>
                    {(ds.available_source_names || []).length > 1 && (
                      <Badge variant="secondary" className="text-[10px]"
                        title={tr('k_950ed5943e', { p0: (ds.available_source_names || []).join('、') })}>
                        {tr('k_e91365cf9e')}{' '}{(ds.usable_now || []).length}/{(ds.available_source_names || []).length} {tr('k_66f5e225b7')}</Badge>
                    )}
                    {/* 无可用源的行不静默藏起来（服务端 total 与本页行数要一致），显式标不可用 */}
                    {ds.executable === false && (
                      <Badge variant="destructive" className="text-[10px]"
                        title={tr('k_950ed5943e', { p0: (ds.available_source_names || []).join('、') || tr('k_a3ccf702c5') })}>
                        <ShieldOff className="h-3 w-3 mr-1" />{tr('k_beff4a1cd1')}</Badge>
                    )}
                  </div>
                </TableCell>
                <TableCell className="py-2 text-right tabular-nums">{ds.field_count}</TableCell>
                <TableCell className="py-2 text-right tabular-nums">{ds.references}</TableCell>
                <TableCell className="py-2 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1" title={tr('k_3a91f0c4d7')}>
                    {ds.owner_name || tr('k_a3ccf702c5')}
                  </span>
                </TableCell>
                <TableCell className="py-2">
                  <Badge
                    variant={ds.visibility === 'private' ? 'secondary' : 'outline'}
                    className="text-[10px] flex items-center gap-1 w-fit"
                    title={ds.visibility_label || ''}>
                    {ds.visibility === 'private'
                      ? <ShieldOff className="h-3 w-3" />
                      : <ShieldCheck className="h-3 w-3" />}
                    {ds.visibility_label
                      || (ds.visibility === 'private' ? tr('k_9e40c7b158') : tr('k_2f81d5ae64'))}
                  </Badge>
                </TableCell>
                <TableCell className="py-2 text-xs text-muted-foreground whitespace-nowrap">
                  {ds.updated_at ? formatDateTime(ds.updated_at) : '—'}
                </TableCell>
                <TableCell className="py-2">
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" size="sm" title={tr('k_f7acefd2d4')}
                      aria-label={tr('k_f7acefd2d4')} onClick={() => navigate(`/data/datasets/${ds.id}`)}>
                      <Eye className="h-3.5 w-3.5" />
                    </Button>
                    {/* 共享是显式动作：admin 在管理面可代为治理他人私有数据集（editable 由服务端裁决） */}
                    {canManage && ds.editable !== false && (
                      <Button variant="ghost" size="sm" title={tr('k_c1d6a4b902')}
                        aria-label={tr('k_c1d6a4b902')} onClick={() => setShareTarget(ds)}>
                        <Share2 className="h-3.5 w-3.5" />
                      </Button>
                    )}
                    {canManage && ds.editable !== false && (
                      <Button variant="ghost" size="sm" title={tr('k_5cc232620c')}
                        aria-label={tr('k_5cc232620c')} onClick={() => handleDelete(ds)}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      </div>

      {/* 统一分页条：固定底栏，total 来自服务端 COUNT（翻页/换页大小均重新请求） */}
      <PaginationBar
        page={page}
        pageSize={pageSize}
        total={total}
        busy={loading}
        onChange={setPage}
        onPageSizeChange={setPageSize}
        className="shrink-0 mt-2"
      />

      <CreateDatasetDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(id) => { setCreateOpen(false); navigate(`/data/datasets/${id}`); }}
      />

      {shareTarget && (
        <ShareDatasetDialog
          open
          datasetId={shareTarget.id}
          datasetName={shareTarget.name}
          onClose={() => setShareTarget(null)}
          onSaved={load}
        />
      )}

      <DatasetBatchSourceDialog
        open={batchSourceOpen}
        count={selected.length}
        datasources={datasources}
        busy={batchBusy}
        onClose={() => setBatchSourceOpen(false)}
        onSubmit={handleBatchSourceSubmit}
      />

      {/* 批量结果回报：逐条点名成功/失败，失败项已保留勾选可重试（不笼统报“部分失败”） */}
      <Dialog open={!!batchResult} onOpenChange={() => setBatchResult(null)}>
        <DialogContent className="max-w-md max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{batchResult?.kind === 'delete' ? t('k_a72199c91f') : t('k_b7981d9704')}</DialogTitle>
            <DialogDescription>{batchResult?.message}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            {(batchResult?.succeeded || []).length > 0 && (
              <div className="space-y-1">
                <div className="text-xs font-medium text-green-700 dark:text-green-400">{tr('k_51991a5d11')}{' '}{batchResult.succeeded_count} {tr('k_f7b2a6ee68')}</div>
                {(batchResult.succeeded || []).filter((s: any) => s.warnings?.length).map((s: any) => (
                  <div key={s.id} role="alert" className="flex items-start gap-1.5 text-[11px] text-amber-700 dark:text-amber-400">
                    <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                    <span>#{s.id}：{(s.warnings || []).join('；')}</span>
                  </div>
                ))}
              </div>
            )}
            {(batchResult?.failed || []).length > 0 && (
              <div className="space-y-1">
                <div className="text-xs font-medium text-destructive">{tr('k_21212666d5')}{' '}{batchResult.failed_count} {tr('k_7d19caa31b')}</div>
                {(batchResult.failed || []).map((f: any) => (
                  <div key={f.id} className="text-[11px] text-muted-foreground">
                    · <b className="text-foreground">{f.name || `#${f.id}`}</b>：{f.reason}
                  </div>
                ))}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button onClick={() => setBatchResult(null)}>{t("k_cb63c62e50")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 同步结果弹窗: object_count 与 notes 显式展示 */}
      <Dialog open={!!syncResult} onOpenChange={() => setSyncResult(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t("k_890dc420e5")}</DialogTitle>
            <DialogDescription>
              {tr('k_8e527be941')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-2 text-sm">
            <div>{t("k_fc9650fcc3")}<b>{syncResult?.object_count ?? 0}</b></div>
            {(syncResult?.notes || []).length > 0 && (
              <div className="border rounded-md p-2.5 bg-amber-50 dark:bg-amber-950/30 space-y-1">
                <div className="text-xs font-medium text-amber-700 dark:text-amber-400">{t("k_32f6a5a468")}</div>
                {(syncResult.notes || []).map((n: string, i: number) => (
                  <div key={i} className="text-xs text-amber-700 dark:text-amber-400">· {n}</div>
                ))}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button onClick={() => setSyncResult(null)}>{t("k_cb63c62e50")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Create Dialog：执行 SQL + 图表预设 ──────────────────────────────────────

function CreateDatasetDialog({
  open, onClose, onCreated,
}: { open: boolean; onClose: () => void; onCreated: (id: number) => void }) {
  // 新建只有一条通道：数据源上的预制 SQL（经治理入口）。内置 Elasticsearch 通道已于
  // 2026-11-13 退役（ES 现在是正式数据源，取数走角色源授权 + RLS/列级 + 审计），
  // 所以这里不再给「ES 索引」入口 —— 留着就是一个点了必报错的死选项（后端显式拒）。
  const [form, setForm] = useState({
    name: '', description: '', datasource_id: 0, sql_query: '',
    chart_type: '',
  });
  const [preset, setPreset] = useState({ xCol: '', yCol: '', groupCol: '' });
  const [datasources, setDatasources] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [inferredFields, setInferredFields] = useState<any[]>([]);

  useEffect(() => {
    if (!open) return;
    client.get('/datasources/').then(({ data }) => {
      setDatasources(Array.isArray(data) ? data : (data?.items || []));
    }).catch(() => setDatasources([]));
  }, [open]);

  // 图表预设可选列: 按 SQL 结果列
  const presetColumns = inferredFields.length
    ? inferredFields.map((f: any) => f.field)
    : [];

  const validateSql = async () => {
    if (!form.sql_query.trim()) { toast.error(tr('k_722cfefb5e')); return; }
    setValidating(true);
    try {
      const { data } = await client.post('/datasets/validate-sql', {
        sql: form.sql_query, datasource_id: Number(form.datasource_id) || 0,
      });
      setInferredFields(data.fields || []);
      toast.success(tr('k_335923e9dc', { p0: data.fields?.length || 0 }));
    } catch (e: any) {
      setInferredFields([]);
      toast.error(e.response?.data?.detail || tr('k_ff77a1eb5f'));
    } finally {
      setValidating(false);
    }
  };

  const handleSave = async () => {
    if (!form.name.trim()) { toast.error(tr('k_88cfbe5b6b')); return; }
    if (!form.sql_query.trim()) {
      toast.error(tr('k_c13269d0c6')); return;
    }
    if (form.sql_query.trim() && inferredFields.length === 0) {
      toast.error(tr('k_731cba14d1')); return;
    }
    setSaving(true);
    try {
      const chartPreset: Record<string, any> = {};
      if (preset.xCol) chartPreset.xCol = preset.xCol;
      if (preset.yCol) chartPreset.yCol = preset.yCol;
      if (preset.groupCol) chartPreset.groupCol = preset.groupCol;
      const { data } = await client.post('/datasets/', {
        name: form.name.trim(),
        description: form.description,
        // 预制 SQL 一律挂在已登记数据源上（0=默认引擎），由服务端按 db_type 分派执行口
        datasource_id: Number(form.datasource_id) || 0,
        source_type: 'sql',
        sql_query: form.sql_query,
        chart_type: form.chart_type,
        chart_preset: chartPreset,
        field_config: inferredFields,
      });
      toast.success(tr('k_2215b20d59'));
      onCreated(data.id);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || tr('k_a889286a51'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("k_e78bd5fabb")}</DialogTitle>
          <DialogDescription>
            {tr('k_7326cc98b3')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>{tr('k_456f3ac6b4')}</Label>
              <Input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder={t("k_ab0883f179")} />
            </div>
            <div className="space-y-1.5">
              <Label>{t("k_a3ccf702c5")}</Label>
              <Select
                value={form.datasource_id ? String(form.datasource_id) : '0'}
                onValueChange={v => setForm({ ...form, datasource_id: Number(v) })}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="0">{t("k_4c3c25802e")}</SelectItem>
                  {datasources.map((d: any) => <SelectItem key={d.id} value={String(d.id)}>{d.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>{t("k_412f54dc38")}</Label>
            <Textarea rows={2} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} />
          </div>

          {/* ── 执行正文（治理域 SQL）── */}
          <div className="border rounded-lg p-3 space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-sm font-medium">
                <Code2 className="h-4 w-4" /> {tr('k_a8c191126e')}<Badge variant="outline" className="text-[10px]">
                  {tr('k_96b5deb12f')}</Badge>
              </div>
              <Button size="sm" variant="outline" onClick={validateSql} disabled={validating}>
                {validating ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <Check className="h-3.5 w-3.5 mr-1" />}
                {tr('k_042ac83126')}</Button>
            </div>
            <Textarea
              rows={6} className="font-mono text-xs"
              value={form.sql_query}
              onChange={e => { setForm({ ...form, sql_query: e.target.value }); setInferredFields([]); }}
              placeholder={'SELECT region, status, COUNT(*) AS cnt FROM t_case_records GROUP BY region, status'}
            />
            {inferredFields.length > 0 && (
              <div className="border rounded-lg p-2.5 text-xs space-y-1">
                <div className="font-medium mb-1">{t("k_aa8f49841f")}</div>
                <div className="flex flex-wrap gap-1.5">
                  {inferredFields.map((f: any) => (
                    <Badge key={f.field} variant="outline" className="text-[10px]">
                      {f.field} · {f.role === 'measure' ? tr('k_a285e2aa8c') : tr('k_72fa9ecdc4')}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* ── 图表预设(可选) ── */}
          <div className="border rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-1.5 text-sm font-medium">
              <BarChart3 className="h-4 w-4" /> {tr('k_099c0176b1')}<Badge variant="outline" className="text-[10px]">{t("k_c026ada3ad")}</Badge>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>{t("k_e891b46dbb")}</Label>
                <Select value={form.chart_type} onValueChange={v => setForm({ ...form, chart_type: v })}>
                  <SelectTrigger><SelectValue placeholder={t("k_0c4c3bfaba")} /></SelectTrigger>
                  <SelectContent>
                    {PRESET_CHART_TYPES.map(t => (
                      <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>{t("k_6bd074681b")}</Label>
                <Select value={preset.xCol} onValueChange={v => setPreset({ ...preset, xCol: v })}>
                  <SelectTrigger><SelectValue placeholder={t("k_0c4c3bfaba")} /></SelectTrigger>
                  <SelectContent>
                    {presetColumns.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>{t("k_55f83922dd")}</Label>
                <Select value={preset.yCol} onValueChange={v => setPreset({ ...preset, yCol: v })}>
                  <SelectTrigger><SelectValue placeholder={t("k_0c4c3bfaba")} /></SelectTrigger>
                  <SelectContent>
                    {presetColumns.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>{t("k_8b9d1d4bf1")}</Label>
                <Select value={preset.groupCol} onValueChange={v => setPreset({ ...preset, groupCol: v })}>
                  <SelectTrigger><SelectValue placeholder={t("k_0c4c3bfaba")} /></SelectTrigger>
                  <SelectContent>
                    {presetColumns.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              {tr('k_272c3db340')}</p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t("k_4d0b4688c7")}</Button>
          <Button onClick={handleSave} disabled={saving}>{saving ? tr('k_d156b373ad') : tr('k_fcbd093292')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
