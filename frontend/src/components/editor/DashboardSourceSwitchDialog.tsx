import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Check, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import client from '@/api/client';

/**
 * 看板级「批量切换执行数据源」。
 *
 * 背景：数据集可声明「可用数据源范围」（一份定义 × N 个源，同构本体 deploy_sites），
 * 看板图表此前只能由服务端自动定源、无法在看板编辑里切换。本对话框把选择抬到整板维度：
 * 选一个目标数据源，一次性钉到所有「其数据集支持该源」的图表上；不支持的图表显式跳过并说明
 * 原因（不静默改挂、不越权换源）。
 *
 * 口径纪律（与后端一致，不开第二套判断）：
 *  - 候选源 = 各数据集 `usable_now`（后端 = 可用范围 ∩ 当前角色授权源）的并集，只给业务名；
 *    内部 datasource_id 属黑盒（护栏 §7），前端不接触、不提交。
 *  - 是否支持某源，唯一依据是该数据集的 `usable_now` 是否含它（与挂图闸门 assert_chart_data_source
 *    同一裁决）；数据集不在当前清单内（已撤权/停用）的图表无法判定 → 跳过并标注，不猜。
 *  - 应用只写草稿（config.datasource），保存时由后端逐图校验范围，越界即拒。
 */

interface ChartRow {
  id: number;
  name: string;
  datasetId: number;
  datasetName: string;
  usable: string[];
  current: string;
}

interface Props {
  open: boolean;
  /** 看板草稿中的全部图表（含未保存的新图） */
  charts: any[];
  onCancel: () => void;
  /**
   * 应用切换：target 为数据源业务名；chartIds 为支持该源、将被钉选的图表 id。
   * target='' 表示「清除全部钉选，改回自动定源」（此时 chartIds 为当前已钉选的图表）。
   */
  onApply: (target: string, chartIds: number[]) => void;
}

export default function DashboardSourceSwitchDialog({ open, charts, onCancel, onApply }: Props) {
  const [datasets, setDatasets] = useState<Record<number, any>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [target, setTarget] = useState('');
  // 每次打开 remount 一次非受控 Select：避免受控/非受控切换告警，并清掉上次的选项
  const [resetKey, setResetKey] = useState(0);

  const load = () => {
    setLoading(true);
    setError('');
    client.get('/datasets/').then(({ data }) => {
      const items = Array.isArray(data?.items) ? data.items : [];
      setDatasets(Object.fromEntries(items.map((d: any) => [Number(d.id), d])));
    }).catch(() => {
      // 清单拉不到不能退成"没有可切的源"（那会被读成业务结论）：显式报错 + 可重试
      setDatasets({});
      setError('数据集清单加载失败，无法判定各图表支持的数据源范围，请重试。');
    }).finally(() => setLoading(false));
  };

  useEffect(() => { if (open) { setTarget(''); setResetKey(k => k + 1); load(); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // 数据集图表（排除控件、未绑数据集的占位图）→ 带其数据集支持范围与当前钉选
  const rows: ChartRow[] = useMemo(() => (charts || [])
    .filter(c => c && !String(c.chart_type || '').startsWith('widget_')
      && String(c.source_type) === 'dataset' && c.source_id)
    .map(c => {
      const ds = datasets[Number(c.source_id)];
      return {
        id: c.id,
        name: c.name || `图表 ${c.id}`,
        datasetId: Number(c.source_id),
        datasetName: ds?.name || '数据集不在当前清单内（可能已撤权/停用）',
        usable: (ds?.usable_now || []).filter(Boolean),
        current: String(c.config?.datasource || ''),
      };
    }), [charts, datasets]);

  // 候选目标源：所有可判定图表支持范围的并集（业务名，按中文排序）
  const candidates = useMemo(() => {
    const set = new Set<string>();
    rows.forEach(r => r.usable.forEach(n => set.add(n)));
    return [...set].sort((a, b) => a.localeCompare(b, 'zh-CN'));
  }, [rows]);

  const matched = target ? rows.filter(r => r.usable.includes(target)) : [];
  const skipped = target ? rows.filter(r => !r.usable.includes(target)) : [];
  const pinnedCount = rows.filter(r => r.current).length;

  const apply = () => {
    if (!target) return;
    onApply(target, matched.map(r => r.id));
  };
  const clearAll = () => onApply('', rows.filter(r => r.current).map(r => r.id));

  return (
    <Dialog open={open} onOpenChange={v => { if (!v) onCancel(); }}>
      <DialogContent className="max-w-[560px] max-h-[82vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><RefreshCw className="h-5 w-5" />批量切换执行数据源</DialogTitle>
          <DialogDescription>
            把所选数据源一次性钉到「其数据集支持该源」的图表上；不支持的图表保持原样并列出原因。
            只改草稿，保存后生效；跨查看者按其角色权限裁决。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-1 min-h-0 overflow-auto pr-1">
          {error && (
            <div role="alert" className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-xs text-destructive">
              <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" />
              <span className="flex-1">{error}</span>
              <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={load}>重试</Button>
            </div>
          )}

          {rows.length === 0 && !loading && !error && (
            <p className="text-xs text-muted-foreground">本看板还没有绑定数据集的图表（控件与占位图不参与切换）。</p>
          )}

          {rows.length > 0 && (
            <>
              <div className="space-y-1.5">
                <Label className="text-xs">目标数据源</Label>
                <Select key={resetKey} onValueChange={setTarget} disabled={loading || !candidates.length}>
                  <SelectTrigger className="h-8 text-xs"><SelectValue placeholder={loading ? '正在加载数据集清单…' : (candidates.length ? '选择要切换到的数据源' : '当前图表都没有可切换的多源数据集')} /></SelectTrigger>
                  <SelectContent>
                    {candidates.map(n => <SelectItem key={n} value={n}>{n}</SelectItem>)}
                  </SelectContent>
                </Select>
                {!loading && !candidates.length && (
                  <p className="text-[11px] text-muted-foreground">
                    可切换的前提是数据集声明了「可用数据源范围」（多于一个源）。当前图表的数据集都只有单一可执行源，没有可切的选项——如需多源请到「数据集」页配置可用范围。
                  </p>
                )}
              </div>

              {target && (
                <div className="space-y-2 rounded-md border bg-muted/20 p-2 text-xs">
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary" className="text-[10px]">将切换 {matched.length}</Badge>
                    {skipped.length > 0 && <Badge variant="outline" className="text-[10px]">跳过 {skipped.length}</Badge>}
                  </div>
                  <ul className="space-y-1">
                    {matched.map(r => (
                      <li key={r.id} className="flex items-center gap-1.5">
                        <Check className="h-3.5 w-3.5 text-green-600 flex-shrink-0" />
                        <span className="truncate">{r.name}</span>
                        <span className="text-muted-foreground">· {r.datasetName}</span>
                        {r.current && r.current !== target && <span className="text-muted-foreground">（原：{r.current}）</span>}
                      </li>
                    ))}
                    {skipped.map(r => (
                      <li key={r.id} className="flex items-center gap-1.5 text-muted-foreground">
                        <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" />
                        <span className="truncate">{r.name}</span>
                        <span>· {r.usable.length ? `数据集不支持「${target}」（可执行：${r.usable.join('、')}）` : r.datasetName}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {pinnedCount > 0 && !target && (
                <p className="text-[11px] text-muted-foreground">
                  当前有 {pinnedCount} 个图表已钉选数据源。
                  <Button variant="link" size="sm" className="h-5 px-1 text-[11px]" onClick={clearAll}>全部改回自动定源</Button>
                </p>
              )}
            </>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>取消</Button>
          <Button onClick={apply} disabled={loading || !!error || !target || matched.length === 0}>
            {target && matched.length ? `切换到 ${matched.length} 个图表` : '应用'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
