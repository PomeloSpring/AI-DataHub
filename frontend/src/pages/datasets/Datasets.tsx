import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Plus, Layers, Code2, Trash2, Eye, Loader2, Check,
  RefreshCw, BarChart3, Square, CheckSquare, Database, AlertTriangle,
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
  Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import client from '../../api/client';
import { hasPerm } from '../../stores/permissionStore';
import EsSourcePicker from '@/components/EsSourcePicker';
import { CHART_TYPES } from '../../components/DashboardChart';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { useClientPagination } from '@/hooks/useClientPagination';
import DatasetBatchSourceDialog from './DatasetBatchSourceDialog';

interface Dataset {
  id: number;
  name: string;
  description: string;
  source_type: 'semantic' | 'sql' | 'both' | 'es';
  object_key: string;
  datasource_id: number;
  datasource_name?: string;
  /** ES 型数据集：已选索引范围（data view 模式名） */
  es_pattern?: string;
  /** 可用数据源范围(业务名)：执行源 ∪ available_datasources，经后端按授权交集过滤后返回 */
  available_source_names?: string[];
  /** 当前用户在范围内真正有权限执行的源(业务名) */
  usable_now?: string[];
  chart_type: string;
  owner_id: number;
  field_count: number;
  references: number;
  updated_at: string;
}

// 来源类型显示业务名（不得把内部枚举 es/semantic/both 直接当名称展示，ui-resource-display §1）
const SOURCE_LABELS: Record<string, string> = {
  sql: 'SQL',
  semantic: '语义对象',
  both: '语义 + SQL',
  es: 'ES 索引',
};
const CHART_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  CHART_TYPES.map(t => [t.value, t.label]),
);
// 图表预设只支持真实图表(排除参数控件)
const PRESET_CHART_TYPES = CHART_TYPES.filter(t => t.category !== 'widget');

/**
 * 可承载数据集取数的内置 MCP 通道（`GET /datasets/channels`）。
 *
 * 筛选选项一律来自后端（按 MCP 服务分维度）：前端不得再硬编码“ES 那一项”的文案与 value，
 * 否则新增一个 MCP 通道就要改前端，且两处文案会漂移（ui-resource-display §2）。
 */
interface McpChannelOption {
  value: string;
  label: string;
  group: string;
  ready: boolean;
  state: string;
  status_message: string;
  open_view_count: number;
}

/** 通道类筛选值：`mcp:<服务>`（后端下发的选项）或旧写法 `es`，都不占用 datasource_id */
const isChannelFilter = (v: string) => v.startsWith('mcp:') || v === 'es';

export default function Datasets() {
  const navigate = useNavigate();
  const [items, setItems] = useState<Dataset[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  // 筛选项：'0'=全部 | '<id>'=具体数据源 | 'mcp:<服务>'=内置 MCP 取数通道
  //（ES 型数据集不挂在数据源上，只能走 channel 筛，否则清单里看得到、下拉里选不出）
  const [sourceFilter, setSourceFilter] = useState('0');
  const [datasources, setDatasources] = useState<any[]>([]);
  // 内置 MCP 通道清单与就绪态（事实源 GET /datasets/channels，与 MCP 服务页同源）：
  // 下拉里该出现哪几项、能不能选、不可用的原因，都由它定，不拿前端假设凑。
  const [mcpChannels, setMcpChannels] = useState<McpChannelOption[]>([]);
  const [channelState, setChannelState] = useState<{ loaded: boolean; failed: boolean; message: string }>(
    { loaded: false, failed: false, message: '' });
  const [createOpen, setCreateOpen] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<any>(null);
  // 批量操作：开关式批量模式（默认卡片不挂勾选框，点「批量操作」才进入）+ 多选 + 逐条回报结果
  const [batchMode, setBatchMode] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [batchSourceOpen, setBatchSourceOpen] = useState(false);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchResult, setBatchResult] = useState<any>(null);
  const canManage = hasPerm('dataset:manage');

  // 数据集清单一次性拉全量 → 客户端分页（全站统一默认每页 20 条，候选 20/50/100）
  const paged = useClientPagination(items, DEFAULT_PAGE_SIZE);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/datasets/', {
        params: {
          keyword,
          // MCP 通道不是数据源：选它时 datasource_id 传 0，改由 channel=<选项 value> 筛（后端两者互斥）
          datasource_id: isChannelFilter(sourceFilter) ? 0 : (Number(sourceFilter) || 0),
          channel: isChannelFilter(sourceFilter) ? sourceFilter : '',
        },
      });
      setItems(data.items || []);
    } catch (e: any) {
      // 清单拉失败不得静默渲成「暂无数据集」（会被误读为数据丢了），显式提醒
      setItems([]);
      toast.error(e?.response?.data?.detail || '数据集清单加载失败');
    } finally {
      setLoading(false);
    }
  }, [keyword, sourceFilter]);

  useEffect(() => { load(); }, [load]);

  // 筛选条件变化重置到第 1 页（避免停在越界空页）
  useEffect(() => { paged.reset(); }, [keyword, sourceFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  // 数据源筛选器选项（显示 name、value=id，符合“显示用 name、选择用选择框”规范）
  useEffect(() => {
    client.get('/datasources/')
      .then(({ data }) => setDatasources(Array.isArray(data) ? data : (data?.items || [])))
      .catch(() => setDatasources([]));
  }, []);

  // 通道清单拉失败/未就绪都要可见：不能给一个“选得出、永远 0 结果”的假筛选项，
  // 也不能因为拉失败就悄悄少一项（那会被读成“这个部署没有 ES 通道”）。
  const loadChannels = useCallback(() => {
    client.get('/datasets/channels')
      .then(({ data }) => {
        const items: McpChannelOption[] = Array.isArray(data?.items) ? data.items : [];
        setMcpChannels(items);
        setChannelState({ loaded: true, failed: false, message: '' });
      })
      .catch((e: any) => {
        setMcpChannels([]);
        setChannelState({
          loaded: true, failed: true,
          message: e?.response?.data?.detail || 'ES 通道状态获取失败',
        });
      });
  }, []);
  useEffect(() => { loadChannels(); }, [loadChannels]);

  // 按后端下发的 group 分组（组名与项都不得写死）：日后多一个能承载数据集的 MCP 服务，
  // 下拉自己长出一项，不需要改前端。
  const mcpGroups = useMemo(() => {
    const map = new Map<string, McpChannelOption[]>();
    mcpChannels.forEach(c => {
      const key = c.group || 'MCP 服务（内置）';
      map.set(key, [...(map.get(key) || []), c]);
    });
    return [...map.entries()];
  }, [mcpChannels]);

  const handleDelete = async (ds: Dataset) => {
    if (!confirm(`确定删除数据集 "${ds.name}"？`)) return;
    try {
      await client.delete(`/datasets/${ds.id}`);
      toast.success('已删除');
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  // ── 批量操作：多选 + 逐条复用单条接口 + 逐条回报（失败项保持勾选可重试）──
  const toggleSelect = (id: number) =>
    setSelected(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  const clearSelection = () => setSelected([]);
  const exitBatchMode = () => { setBatchMode(false); setSelected([]); };
  const pageIds = () => paged.slice.map(d => d.id);
  const allPageSelected = paged.slice.length > 0 && paged.slice.every(d => selected.includes(d.id));
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
    if (!confirm(`确定删除选中的 ${selected.length} 个数据集？被看板引用的会逐条跳过并列出原因。`)) return;
    setBatchBusy(true);
    try {
      const { data } = await client.post('/datasets/batch-delete', { ids: selected });
      setBatchResult({ kind: 'delete', ...data });
      setSelected((data.failed || []).map((f: any) => f.id));  // 失败项保持勾选
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '批量删除失败');
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
      toast.error(e.response?.data?.detail || '批量修改失败');
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
      toast.success(`业务本体已同步：${data.object_count ?? 0} 个对象`);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '同步业务本体失败');
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between mb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Layers className="h-6 w-6" /> 数据集
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            BI 治理建模层：执行 SQL 取数，看板、报告与 Chat 共用同一口径
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canManage && (
            <Button size="sm" variant={batchMode ? 'secondary' : 'outline'}
              aria-pressed={batchMode} onClick={() => (batchMode ? exitBatchMode() : setBatchMode(true))}>
              <CheckSquare className="h-4 w-4 mr-1" /> {batchMode ? '退出批量' : '批量操作'}
            </Button>
          )}
          {canManage && (
            <Button size="sm" variant="outline" onClick={handleSync} disabled={syncing}>
              {syncing
                ? <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                : <RefreshCw className="h-4 w-4 mr-1" />}
              同步到业务本体
            </Button>
          )}
          {canManage && (
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4 mr-1" /> 新建数据集
            </Button>
          )}
        </div>
      </div>

      <div className="flex gap-2 mb-4 flex-wrap shrink-0">
        <Input
          placeholder="搜索数据集名称 / 描述"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          className="max-w-xs"
        />
        <Select
          value={sourceFilter}
          onValueChange={setSourceFilter}
        >
          <SelectTrigger className="w-56 h-9" aria-label="按数据源或 MCP 通道筛选数据集">
            <SelectValue placeholder="全部来源" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="0">全部来源</SelectItem>
            {/* 按取数通道分维度：内置 MCP 服务一组（选项来自 /datasets/channels），
                数据源一组（选项来自 /datasources/）。ES 型数据集不挂在数据源上，
                不单列这一组就永远筛不到它。 */}
            {mcpGroups.map(([groupName, opts]) => (
              <SelectGroup key={groupName}>
                <SelectLabel>{groupName}</SelectLabel>
                {opts.map(c => (
                  <SelectItem key={c.value} value={c.value} disabled={!c.ready}
                              title={c.ready ? '' : (c.status_message || c.state)}>
                    {c.label}
                  </SelectItem>
                ))}
              </SelectGroup>
            ))}
            <SelectGroup>
              <SelectLabel>数据源</SelectLabel>
              {datasources.map((d: any) => (
                <SelectItem key={d.id} value={String(d.id)}>{d.name}</SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
        {/* 不可选的原因必须可见：不能留一个“选得出、永远 0 结果”的假筛选项，也不能拿默认引擎凑数 */}
        {!channelState.loaded && (
          <p className="w-full text-[11px] text-muted-foreground">正在确认可用取数通道…</p>
        )}
        {channelState.loaded && channelState.failed && (
          <p role="alert" className="w-full text-[11px] text-destructive">
            {channelState.message}
            <button type="button" className="ml-1 underline" onClick={loadChannels}>重试</button>
          </p>
        )}
        {mcpChannels.filter(c => !c.ready).map(c => (
          <p role="alert" key={c.value} className="w-full text-[11px] text-destructive">
            {c.label}当前不可用：{c.status_message || c.state || '未配置'}（筛选项已禁用；配置由部署侧托管，请联系管理员）
          </p>
        ))}
      </div>

      {/* 批量操作条：进入批量模式即出现（含全选，未选时也可先全选）；未选时动作按钮置灰 */}
      {canManage && batchMode && (
        <div className="flex items-center gap-2 mb-4 shrink-0 rounded-md border bg-muted/30 px-3 py-2 flex-wrap">
          <Button size="sm" variant="outline" className="h-8 text-xs" onClick={toggleSelectPage}>
            {allPageSelected ? <CheckSquare className="h-4 w-4 mr-1" /> : <Square className="h-4 w-4 mr-1" />}
            本页全选
          </Button>
          {selected.length > 0 && (
            <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={clearSelection}>清空选择</Button>
          )}
          <span className="text-sm text-muted-foreground">已选 <b className="text-foreground">{selected.length}</b> 个</span>
          <div className="flex-1" />
          <Button size="sm" variant="outline" onClick={() => setBatchSourceOpen(true)} disabled={batchBusy || selected.length === 0}>
            <Database className="h-4 w-4 mr-1" /> 批量改数据源
          </Button>
          <Button size="sm" variant="destructive" onClick={handleBatchDelete} disabled={batchBusy || selected.length === 0}>
            {batchBusy ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Trash2 className="h-4 w-4 mr-1" />}
            批量删除
          </Button>
        </div>
      )}

      {/* 清单区：仅此区域滚动（页头与分页固定，参考表&字段） */}
      <div className="flex-1 min-h-0 overflow-auto">
      {loading ? (
        <div className="flex justify-center py-12 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground border rounded-lg">
          暂无数据集{canManage ? '，点击右上角新建' : ''}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {paged.slice.map(ds => (
            <div key={ds.id} className="border rounded-lg p-4 hover:bg-muted/30 transition-colors">
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-start gap-2 min-w-0 flex-1">
                  {canManage && batchMode && (
                    <button type="button" aria-label={`选择 ${ds.name}`} onClick={() => toggleSelect(ds.id)}
                      className="mt-0.5 shrink-0 text-muted-foreground hover:text-foreground">
                      {selected.includes(ds.id)
                        ? <CheckSquare className="h-4 w-4 text-primary" />
                        : <Square className="h-4 w-4" />}
                    </button>
                  )}
                  <button className="text-left min-w-0" onClick={() => navigate(`/data/datasets/${ds.id}`)}>
                    <div className="font-medium truncate">{ds.name}</div>
                    {ds.description && (
                      <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{ds.description}</p>
                    )}
                  </button>
                </div>
                <Badge variant="outline" className="shrink-0 flex items-center gap-1 text-[10px]">
                  <Code2 className="h-3 w-3" />
                  {SOURCE_LABELS[ds.source_type] || ds.source_type || 'SQL'}
                </Badge>
              </div>
              <div className="flex items-center gap-2 mt-3 text-xs text-muted-foreground flex-wrap">
                <span>{ds.field_count} 字段</span>
                <span>被 {ds.references} 图表引用</span>
                {/* 数据源显示业务名（后端返回，不靠前端拼 ID 兜底）；多源时标注可用范围 */}
                <Badge variant="outline" className="text-[10px]"
                  title={ds.source_type === 'es'
                    ? `内置 Elasticsearch 通道，索引范围: ${ds.es_pattern || '-'}`
                    : `执行数据源: ${ds.datasource_name || '数据源'}`}>
                  {ds.datasource_name || '数据源'}
                </Badge>
                {ds.source_type === 'es' && ds.es_pattern && (
                  <Badge variant="secondary" className="text-[10px] max-w-[160px] truncate"
                    title={`已选索引范围（只读通道，不套行列级策略）: ${ds.es_pattern}`}>
                    {ds.es_pattern}
                  </Badge>
                )}
                {(ds.available_source_names || []).length > 1 && (
                  <Badge variant="secondary" className="text-[10px]"
                    title={`可用数据源: ${(ds.available_source_names || []).join('、')}`}>
                    可用 {(ds.usable_now || []).length}/{(ds.available_source_names || []).length} 源
                  </Badge>
                )}
                {ds.chart_type && (
                  <Badge variant="secondary" className="text-[10px] flex items-center gap-1">
                    <BarChart3 className="h-3 w-3" />
                    {CHART_TYPE_LABELS[ds.chart_type] || ds.chart_type}
                  </Badge>
                )}
              </div>
              <div className="flex justify-end gap-1 mt-2">
                <Button variant="ghost" size="sm" onClick={() => navigate(`/data/datasets/${ds.id}`)}>
                  <Eye className="h-3.5 w-3.5" />
                </Button>
                {canManage && (
                  <Button variant="ghost" size="sm" onClick={() => handleDelete(ds)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      </div>

      {/* 统一分页条：固定底栏，始终可见（不随页面滚走，也不悬浮在卡片上） */}
      <PaginationBar
        page={paged.page}
        pageSize={paged.pageSize}
        total={paged.total}
        onChange={(p: number) => paged.setPage(p)}
        onPageSizeChange={paged.setPageSize}
        hideWhenSingle
        className="shrink-0 mt-2"
      />

      <CreateDatasetDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(id) => { setCreateOpen(false); navigate(`/data/datasets/${id}`); }}
      />

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
            <DialogTitle>批量{batchResult?.kind === 'delete' ? '删除' : '修改数据源'}结果</DialogTitle>
            <DialogDescription>{batchResult?.message}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            {(batchResult?.succeeded || []).length > 0 && (
              <div className="space-y-1">
                <div className="text-xs font-medium text-green-700 dark:text-green-400">成功 {batchResult.succeeded_count} 个</div>
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
                <div className="text-xs font-medium text-destructive">失败/本次不处理 {batchResult.failed_count} 个（已保留勾选，可修正后重试）</div>
                {(batchResult.failed || []).map((f: any) => (
                  <div key={f.id} className="text-[11px] text-muted-foreground">
                    · <b className="text-foreground">{f.name || `#${f.id}`}</b>：{f.reason}
                  </div>
                ))}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button onClick={() => setBatchResult(null)}>知道了</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 同步结果弹窗: object_count 与 notes 显式展示 */}
      <Dialog open={!!syncResult} onOpenChange={() => setSyncResult(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>业务本体同步结果</DialogTitle>
            <DialogDescription>
              数据集已作为业务本体路由节点一并生成（save_draft/activate 级联）
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 text-sm">
            <div>生成对象数：<b>{syncResult?.object_count ?? 0}</b></div>
            {(syncResult?.notes || []).length > 0 && (
              <div className="border rounded-md p-2.5 bg-amber-50 dark:bg-amber-950/30 space-y-1">
                <div className="text-xs font-medium text-amber-700 dark:text-amber-400">告警（治理数据未对齐项）</div>
                {(syncResult.notes || []).map((n: string, i: number) => (
                  <div key={i} className="text-xs text-amber-700 dark:text-amber-400">· {n}</div>
                ))}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button onClick={() => setSyncResult(null)}>知道了</Button>
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
  // 来源类型：sql=数据源上的预制 SQL（经治理入口）；es=内置 Elasticsearch 通道（不进治理域）
  const [kind, setKind] = useState<'sql' | 'es'>('sql');
  // ES 配置面单独一个权限码：无该码就不渲染 ES 选项，而不是给人一个点了必 403 的按钮
  const canUseEs = hasPerm('dataset:es');
  const [form, setForm] = useState({
    name: '', description: '', datasource_id: 0, sql_query: '',
    chart_type: '', es_pattern: '',
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
    if (!form.sql_query.trim()) { toast.error('请先输入 SQL'); return; }
    if (kind === 'es' && !form.es_pattern) { toast.error('请先选择索引范围'); return; }
    setValidating(true);
    try {
      const { data } = kind === 'es'
        ? await client.post('/datasets/validate-es-sql', {
          sql: form.sql_query, es_pattern: form.es_pattern,
        })
        : await client.post('/datasets/validate-sql', {
          sql: form.sql_query, datasource_id: Number(form.datasource_id) || 0,
        });
      setInferredFields(data.fields || []);
      toast.success(`校验通过，推断 ${data.fields?.length || 0} 个字段`);
    } catch (e: any) {
      setInferredFields([]);
      toast.error(e.response?.data?.detail || 'SQL 校验失败');
    } finally {
      setValidating(false);
    }
  };

  const handleSave = async () => {
    if (!form.name.trim()) { toast.error('名称必填'); return; }
    if (!form.sql_query.trim()) {
      toast.error(kind === 'es' ? '请输入 ES 查询正文' : '请输入执行 SQL'); return;
    }
    if (kind === 'es' && !form.es_pattern) { toast.error('请选择索引范围'); return; }
    if (form.sql_query.trim() && inferredFields.length === 0) {
      toast.error('请先「校验并推断字段」'); return;
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
        // ES 型不挂在数据源上：datasource_id 传 0（后端会显式校核并拒掉非 0）
        datasource_id: kind === 'es' ? 0 : (Number(form.datasource_id) || 0),
        source_type: kind,
        es_pattern: kind === 'es' ? form.es_pattern : '',
        sql_query: form.sql_query,
        chart_type: form.chart_type,
        chart_preset: chartPreset,
        field_config: inferredFields,
      });
      toast.success('数据集已创建');
      onCreated(data.id);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '创建失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>新建数据集</DialogTitle>
          <DialogDescription>
            {kind === 'es'
              ? '内置 Elasticsearch 通道取数：只读、按索引范围查询，不进列级/行级数据权限治理域'
              : '执行 SQL 负责取数，经统一治理入口；看板、报告与 Chat 共用同一口径'}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {canUseEs && (
            <div className="space-y-1.5">
              <Label>数据来源</Label>
              <div className="flex gap-2">
                {([
                  { value: 'sql' as const, label: '数据源 SQL', desc: '已登记数据源上的预制 SQL，套敏感/RLS/审计' },
                  { value: 'es' as const, label: 'Elasticsearch 索引', desc: '平台内置只读通道，按索引范围取数，不套行列级策略' },
                ]).map(o => (
                  <button key={o.value} type="button" title={o.desc}
                    onClick={() => { setKind(o.value); setInferredFields([]); }}
                    className={`flex-1 rounded-lg border px-3 py-2 text-left text-xs transition-colors ${
                      kind === o.value
                        ? 'border-primary bg-primary/5 font-medium'
                        : 'border-border hover:bg-muted/40'}`}>
                    {o.label}
                    <span className="mt-0.5 block text-[10px] text-muted-foreground">{o.desc}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>名称 *</Label>
              <Input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="如：案件分析数据集" />
            </div>
            {kind === 'es' ? (
              <EsSourcePicker required
                value={form.es_pattern}
                onChange={v => { setForm({ ...form, es_pattern: v }); setInferredFields([]); }}
              />
            ) : (
              <div className="space-y-1.5">
                <Label>数据源</Label>
                <Select
                  value={form.datasource_id ? String(form.datasource_id) : '0'}
                  onValueChange={v => setForm({ ...form, datasource_id: Number(v) })}
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="0">默认引擎</SelectItem>
                    {datasources.map((d: any) => <SelectItem key={d.id} value={String(d.id)}>{d.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
          <div className="space-y-1.5">
            <Label>描述</Label>
            <Textarea rows={2} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} />
          </div>

          {/* ── 执行正文（治理域 SQL | ES SQL）── */}
          <div className="border rounded-lg p-3 space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-sm font-medium">
                <Code2 className="h-4 w-4" /> {kind === 'es' ? 'ES 查询正文' : '执行 SQL'}
                <Badge variant="outline" className="text-[10px]">
                  {kind === 'es' ? '单表 SELECT，必须 LIMIT' : '仅 SELECT/WITH'}
                </Badge>
              </div>
              <Button size="sm" variant="outline" onClick={validateSql} disabled={validating}>
                {validating ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <Check className="h-3.5 w-3.5 mr-1" />}
                校验并推断字段
              </Button>
            </div>
            <Textarea
              rows={6} className="font-mono text-xs"
              value={form.sql_query}
              onChange={e => { setForm({ ...form, sql_query: e.target.value }); setInferredFields([]); }}
              placeholder={kind === 'es'
                ? 'SELECT DATE_TRUNC(\'DAY\', "@timestamp") AS d, COUNT(*) AS c\nFROM "logs-*" GROUP BY DATE_TRUNC(\'DAY\', "@timestamp") ORDER BY d ASC LIMIT 60'
                : 'SELECT region, status, COUNT(*) AS cnt FROM t_case_records GROUP BY region, status'}
            />
            {inferredFields.length > 0 && (
              <div className="border rounded-lg p-2.5 text-xs space-y-1">
                <div className="font-medium mb-1">推断字段（详情页可调整角色/别名）</div>
                <div className="flex flex-wrap gap-1.5">
                  {inferredFields.map((f: any) => (
                    <Badge key={f.field} variant="outline" className="text-[10px]">
                      {f.field} · {f.role === 'measure' ? '度量' : '维度'}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* ── 图表预设(可选) ── */}
          <div className="border rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-1.5 text-sm font-medium">
              <BarChart3 className="h-4 w-4" /> 图表预设
              <Badge variant="outline" className="text-[10px]">可选 · 引入看板时一键生成</Badge>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>图表类型</Label>
                <Select value={form.chart_type} onValueChange={v => setForm({ ...form, chart_type: v })}>
                  <SelectTrigger><SelectValue placeholder="不预设" /></SelectTrigger>
                  <SelectContent>
                    {PRESET_CHART_TYPES.map(t => (
                      <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>X 轴列</Label>
                <Select value={preset.xCol} onValueChange={v => setPreset({ ...preset, xCol: v })}>
                  <SelectTrigger><SelectValue placeholder="不预设" /></SelectTrigger>
                  <SelectContent>
                    {presetColumns.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Y 轴列</Label>
                <Select value={preset.yCol} onValueChange={v => setPreset({ ...preset, yCol: v })}>
                  <SelectTrigger><SelectValue placeholder="不预设" /></SelectTrigger>
                  <SelectContent>
                    {presetColumns.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>分组列</Label>
                <Select value={preset.groupCol} onValueChange={v => setPreset({ ...preset, groupCol: v })}>
                  <SelectTrigger><SelectValue placeholder="不预设" /></SelectTrigger>
                  <SelectContent>
                    {presetColumns.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              预设列引用查询结果列名；看板编辑器「从数据集引入」时直接搬入图表配置
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={handleSave} disabled={saving}>{saving ? '创建中…' : '创建'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
