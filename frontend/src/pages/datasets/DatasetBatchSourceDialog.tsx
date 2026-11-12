import { useEffect, useMemo, useState } from 'react';
import { Database, Layers } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

/**
 * 批量修改数据集的数据源配置：默认数据源（执行源）与可用数据源范围。
 *
 * 口径：
 *  - 两项各自有「不修改」态，只有显式改成「修改」的字段才提交（后端逐条只 patch 带的字段）；
 *  - 选择一律业务名/从数据源清单选（ui-resource-display §2），执行源用 id（实现细节，选项显示 name）；
 *  - 可用范围存业务名数组，执行源隐式属于范围（无需重复勾选），与数据集详情页同口径。
 *  - ES 型数据集不挂在数据源上：由调用方在提交前排除，或在结果里逐条回报「本次不处理」。
 */

interface DsOption { id: number; name: string; site_code?: string; db_type?: string }

interface Props {
  open: boolean;
  count: number;
  datasources: DsOption[];
  busy: boolean;
  onClose: () => void;
  onSubmit: (patch: { datasource_id?: number; available_datasources?: string[] }) => Promise<void>;
}

export default function DatasetBatchSourceDialog({ open, count, datasources, busy, onClose, onSubmit }: Props) {
  const [execMode, setExecMode] = useState<'keep' | 'set'>('keep');
  const [execId, setExecId] = useState<string>('0');
  const [availMode, setAvailMode] = useState<'keep' | 'set'>('keep');
  const [availSelected, setAvailSelected] = useState<string[]>([]);

  useEffect(() => {
    if (open) { setExecMode('keep'); setExecId('0'); setAvailMode('keep'); setAvailSelected([]); }
  }, [open]);

  // 按站点分组展示（组名用 site_code，无站点归「未分组」）：与数据集详情页可用范围选择器同构
  const groups = useMemo(() => {
    const map = new Map<string, DsOption[]>();
    datasources.forEach(d => {
      const key = d.site_code || '未分组';
      map.set(key, [...(map.get(key) || []), d]);
    });
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0], 'zh-CN'));
  }, [datasources]);

  const toggleAvail = (name: string) => setAvailSelected(prev =>
    prev.includes(name) ? prev.filter(x => x !== name) : [...prev, name]);

  const nothingToChange = execMode === 'keep' && availMode === 'keep';

  const submit = async () => {
    if (nothingToChange) return;
    const patch: { datasource_id?: number; available_datasources?: string[] } = {};
    if (execMode === 'set') patch.datasource_id = Number(execId) || 0;
    if (availMode === 'set') patch.available_datasources = availSelected;
    await onSubmit(patch);
  };

  return (
    <Dialog open={open} onOpenChange={v => { if (!v) onClose(); }}>
      <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>批量修改数据源</DialogTitle>
          <DialogDescription>
            对已选中的 <b>{count}</b> 个数据集生效。只想改一项就把另一项保持「不修改」；
            ES 型数据集不挂在数据源上，会在结果里逐条回报「本次不处理」，不会被静默改动。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* 默认数据源（执行源） */}
          <div className="space-y-2 rounded-md border p-3">
            <div className="flex items-center justify-between">
              <Label className="flex items-center gap-1.5 text-sm"><Database className="h-4 w-4" />默认数据源（执行源）</Label>
              <div className="flex gap-1">
                <Button size="sm" variant={execMode === 'keep' ? 'secondary' : 'ghost'} className="h-7 text-xs" onClick={() => setExecMode('keep')}>不修改</Button>
                <Button size="sm" variant={execMode === 'set' ? 'secondary' : 'ghost'} className="h-7 text-xs" onClick={() => setExecMode('set')}>修改</Button>
              </div>
            </div>
            {execMode === 'set' ? (
              <Select value={execId} onValueChange={setExecId}>
                <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="0">默认引擎（平台元数据库）</SelectItem>
                  {datasources.map(d => <SelectItem key={d.id} value={String(d.id)}>{d.name}</SelectItem>)}
                </SelectContent>
              </Select>
            ) : <p className="text-xs text-muted-foreground">保持各数据集当前执行源不变。</p>}
          </div>

          {/* 可用数据源范围 */}
          <div className="space-y-2 rounded-md border p-3">
            <div className="flex items-center justify-between">
              <Label className="flex items-center gap-1.5 text-sm"><Layers className="h-4 w-4" />可用数据源范围</Label>
              <div className="flex gap-1">
                <Button size="sm" variant={availMode === 'keep' ? 'secondary' : 'ghost'} className="h-7 text-xs" onClick={() => setAvailMode('keep')}>不修改</Button>
                <Button size="sm" variant={availMode === 'set' ? 'secondary' : 'ghost'} className="h-7 text-xs" onClick={() => setAvailMode('set')}>修改</Button>
              </div>
            </div>
            {availMode === 'set' && (
              <div className="space-y-2">
                <p className="text-[11px] text-muted-foreground">
                  勾选后这些数据集即可在所选的额外数据源上执行（执行源本身隐式可用，无需重复勾选）。不勾任何项 = 只允许执行源单源运行。
                </p>
                <div className="max-h-48 overflow-y-auto border rounded p-2 space-y-1">
                  {groups.map(([site, opts]) => (
                    <div key={site} className="space-y-1">
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{site}</div>
                      {opts.map(d => (
                        <label key={d.id} className="flex items-center gap-2 text-sm cursor-pointer">
                          <input type="checkbox" className="h-3.5 w-3.5"
                            checked={availSelected.includes(d.name)}
                            onChange={() => toggleAvail(d.name)} />
                          <span className="truncate">{d.name}</span>
                          {d.db_type && <span className="text-[10px] text-muted-foreground">{d.db_type}</span>}
                        </label>
                      ))}
                    </div>
                  ))}
                  {groups.length === 0 && <p className="text-xs text-muted-foreground">暂无可选数据源</p>}
                </div>
              </div>
            )}
            {availMode === 'keep' && <p className="text-xs text-muted-foreground">保持各数据集当前可用范围不变。</p>}
          </div>

          {nothingToChange && (
            <p className="text-xs text-amber-600">至少把一项切到「修改」，否则没有可提交的变更。</p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={submit} disabled={busy || nothingToChange}>{busy ? '处理中…' : `应用到 ${count} 个数据集`}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
