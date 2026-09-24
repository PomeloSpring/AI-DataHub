/**
 * AssetPoolPanel — 未归属资产池（模型工作区左栏入口切换的独立视图）。
 *
 * 收留: bound_object_key 为空(unbound)/指向已失效对象(orphan)的指标与维度、
 * 未绑定任何表的黑话术语、解析失败回流的别名候选词(adh_alias_suggestions)。
 * 动作:
 *  - 指标/维度: "归属到…" 下拉选当前模型对象 → 唯一编辑器 API 写 bound_object_key(引用, 非锚点);
 *    或"删除"(字典行物理删除, 与指标中心同口径) —— 清掉永不归属的噪声资产才能清空资产池;
 *  - 术语: 引导去业务术语页绑定映射(删除也在该页);
 *  - 候选词: 通过 AS-BOT alias.approve/alias.reject 审批通道处理(不造第二写通道, 无直接删除)。
 */
import { useState, useEffect, useCallback } from 'react';
import { metricsApi } from '@/api/metrics';
import client from '@/api/client';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from 'sonner';
import { AlertTriangle, Archive, Bot, BookOpen, RefreshCw, Trash2 } from 'lucide-react';

interface PoolItem {
  id: number;
  name?: string;
  term_cn?: string;
  term?: string;
  bound_object_key?: string;
  pool_reason?: 'unbound' | 'orphan';
  unit?: string;
  agg_type?: string;
  category?: string;
  target_type?: string;
  target_ref?: string;
  hit_count?: number;
}

interface PoolData {
  metrics: PoolItem[];
  dimensions: PoolItem[];
  terms: PoolItem[];
  suggestions: PoolItem[];
  counts: Record<string, number>;
}

export default function AssetPoolPanel({
  modelId,
  objects,
}: {
  modelId?: number;
  objects?: { key: string; display_name: string }[];
}) {
  const [data, setData] = useState<PoolData | null>(null);
  const [loading, setLoading] = useState(false);
  const [assigning, setAssigning] = useState<string>('');  // "m:id" / "d:id"
  const [deleteTarget, setDeleteTarget] = useState<{ kind: 'metric' | 'dimension'; item: PoolItem } | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data: d } = await metricsApi.assetPool();
      setData(d);
    } catch {
      setData({ metrics: [], dimensions: [], terms: [], suggestions: [], counts: {} });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const assign = async (kind: 'metric' | 'dimension', item: PoolItem, objectKey: string) => {
    const gid = `${kind}:${item.id}`;
    setAssigning(gid);
    try {
      if (kind === 'metric') await metricsApi.update(item.id, { bound_object_key: objectKey });
      else await metricsApi.updateDimension(item.id, { bound_object_key: objectKey });
      toast.success(`「${item.name}」已归属到对象 ${objectKey}`);
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '归属失败');
    } finally {
      setAssigning('');
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      if (deleteTarget.kind === 'metric') await metricsApi.delete(deleteTarget.item.id);
      else await metricsApi.deleteDimension(deleteTarget.item.id);
      toast.success(`「${deleteTarget.item.name}」已删除`);
      setDeleteTarget(null);
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '删除失败');
    } finally {
      setDeleting(false);
    }
  };

  const proposeAlias = async (sug: PoolItem, action: 'approve' | 'reject') => {
    try {
      const { data } = await client.post('/as-bot/approvals/create', {
        action_key: `alias.${action}`,
        payload: {
          suggestion_id: sug.id,
          term: sug.term,
          target_type: sug.target_type || 'dimension',
          target_ref: sug.target_ref || '',
        },
      });
      if (data?.success) toast.success('已提交 AS-BOT 审批，请在审批面板确认');
      else toast.error(data?.error || '创建审批失败（检查动作权限/参数）');
    } catch {
      toast.error('创建审批请求失败');
    }
  };

  const AssignSelect = ({ kind, item }: { kind: 'metric' | 'dimension'; item: PoolItem }) => {
    const gid = `${kind}:${item.id}`;
    if (!objects?.length) return null;
    return (
      <Select
        disabled={assigning === gid}
        onValueChange={(v) => assign(kind, item, v)}
      >
        <SelectTrigger className="w-36 h-7 text-xs">
          <SelectValue placeholder={assigning === gid ? '归属中…' : '归属到本模型对象…'} />
        </SelectTrigger>
        <SelectContent>
          {objects.map((o) => (
            <SelectItem key={o.key} value={o.key}>{o.display_name || o.key}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  };

  const DeleteButton = ({ kind, item }: { kind: 'metric' | 'dimension'; item: PoolItem }) => (
    <Button size="sm" variant="ghost" className="h-6 w-6 p-0 text-muted-foreground hover:text-red-500"
      title={`删除该${kind === 'metric' ? '指标' : '维度'}字典行`}
      onClick={() => setDeleteTarget({ kind, item })}>
      <Trash2 className="h-3.5 w-3.5" />
    </Button>
  );

  const Section = ({ title, icon, items, render }: any) => (
    <div className="border rounded-lg">
      <div className="px-3 py-2 border-b text-sm font-medium flex items-center gap-2">
        {icon} {title} <Badge variant="outline" className="text-[11px]">{items.length}</Badge>
      </div>
      <div>
        {items.length === 0 ? (
          <div className="text-xs text-muted-foreground p-4 text-center">空 — 该类资产均已归属</div>
        ) : items.map((it: PoolItem, i: number) => (
          <div key={it.id ?? i} className="flex items-center gap-2 px-3 py-2 border-b last:border-0 text-sm">
            {render ? render(it) : <span>{it.name || it.term_cn || it.term}</span>}
            <span className="ml-auto flex items-center gap-2 shrink-0">
              {it.pool_reason === 'orphan' ? (
                <Badge variant="outline" className="text-[11px] text-red-500 border-red-500/40">
                  <AlertTriangle className="h-3 w-3 mr-0.5" />孤儿引用 {it.bound_object_key && `→ ${it.bound_object_key}`}
                </Badge>
              ) : (
                <Badge variant="outline" className="text-[11px] text-amber-500 border-amber-500/40">未绑定</Badge>
              )}
            </span>
          </div>
        ))}
      </div>
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">
          未归属资产池 = 本体覆盖缺口的待办清单；目标：清空。当前共 {data?.counts?.total ?? 0} 项。
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />刷新
        </Button>
      </div>

      {!modelId && (
        <div className="text-xs text-amber-500">提示：先在左侧选择一个本体模型，指标/维度才能"归属到本模型对象"。</div>
      )}

      <Section
        title="指标" icon={<AlertTriangle className="h-4 w-4 text-amber-500" />}
        items={data?.metrics || []}
        render={(it: PoolItem) => (
          <span className="flex items-center gap-2 min-w-0">
            <span className="truncate">{it.name}</span>
            {it.agg_type && <span className="text-xs font-mono text-muted-foreground">{it.agg_type}</span>}
            {modelId && <AssignSelect kind="metric" item={it} />}
            <DeleteButton kind="metric" item={it} />
          </span>
        )}
      />
      <Section
        title="维度" icon={<AlertTriangle className="h-4 w-4 text-amber-500" />}
        items={data?.dimensions || []}
        render={(it: PoolItem) => (
          <span className="flex items-center gap-2 min-w-0">
            <span className="truncate">{it.name}</span>
            {it.category && <Badge variant="outline" className="text-[10px] px-1 h-4">{it.category}</Badge>}
            {modelId && <AssignSelect kind="dimension" item={it} />}
            <DeleteButton kind="dimension" item={it} />
          </span>
        )}
      />
      <Section
        title="未绑定术语（黑话缓冲区）" icon={<BookOpen className="h-4 w-4 text-muted-foreground" />}
        items={data?.terms || []}
        render={(it: PoolItem) => (
          <span className="flex items-center gap-2">
            <span>{it.term_cn}</span>
            <span className="text-[11px] text-muted-foreground">在「业务术语」页设置映射或收编为别名</span>
          </span>
        )}
      />
      <Section
        title="解析失败回流候选词（alias_suggestions）" icon={<Bot className="h-4 w-4 text-muted-foreground" />}
        items={data?.suggestions || []}
        render={(it: PoolItem) => (
          <span className="flex items-center gap-2 min-w-0">
            <span className="truncate font-medium">{it.term}</span>
            {it.target_ref && <span className="text-xs text-muted-foreground">≈ {it.target_ref}</span>}
            <Badge variant="outline" className="text-[10px] px-1 h-4">命中 {it.hit_count ?? 1}</Badge>
            <Button size="sm" variant="outline" className="h-6 text-[11px]"
              onClick={() => proposeAlias(it, 'approve')}>
              收编(提审批)
            </Button>
            <Button size="sm" variant="ghost" className="h-6 text-[11px]"
              onClick={() => proposeAlias(it, 'reject')}>
              <Archive className="h-3 w-3 mr-0.5" />驳回
            </Button>
          </span>
        )}
      />

      {/* 删除确认: 与指标中心同口径的物理删除交互 */}
      <Dialog open={!!deleteTarget} onOpenChange={(o) => { if (!o) setDeleteTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            确定要删除{deleteTarget?.kind === 'metric' ? '指标' : '维度'}
            <strong> {deleteTarget?.item?.name} </strong>
            吗？字典行物理删除不可恢复；若它仍被对象使用，请改用「归属到…」而非删除。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={confirmDelete} disabled={deleting}>
              {deleting ? '删除中…' : '删除'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
