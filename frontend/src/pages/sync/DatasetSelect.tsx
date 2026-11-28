import { useEffect, useState } from 'react';
import { t as tr } from '@/i18n/runtime';
import { ExternalLink } from 'lucide-react';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Button } from '@/components/ui/button';
import client from '@/api/client';

// 物化数据集选择（UI 资源规范：显示 name，选择用选择框，禁止手输内部 ID）。
// 只列「可物化」的数据集：SQL 型（含 both）——语义型数据集的行数上限由语义层编译决定，
// 物化执行器会显式拒绝（materialize_runner: DATASET_NOT_SQL），这里不放进来避免做选项即失败。
export interface DatasetParam {
  key: string;
  label?: string;
  value_type?: string;
}

export interface DatasetOption {
  // id 用于「跳数据集详情 / 拉详情正文」（详情接口只收 id）；清单行自带 d.id
  id?: number;
  name: string;
  status?: string;
  source_type?: string;
  params: DatasetParam[];
}

function materializable(row: any): boolean {
  const type = String(row?.source_type || '');
  // 'sql' = 只有 SQL；'both' = 语义对象 + SQL 双定义（执行仍按 SQL 正文）
  return type === 'sql' || type === 'both';
}

export function useMaterializableDatasets(): DatasetOption[] {
  const [options, setOptions] = useState<DatasetOption[]>([]);
  useEffect(() => {
    // 不传 page_size = 选择面板档（全量 {items}），与 list-pagination §5 的两档契约一致
    client.get('/datasets/').then(({ data }) => {
      const items = Array.isArray(data) ? data : data?.items || [];
      setOptions(items.filter(materializable).map((d: any) => ({
        id: typeof d.id === 'number' ? d.id : undefined,
        name: d.name,
        status: d.status,
        source_type: d.source_type,
        params: (d.param_summary || [])
          .filter((g: any) => g?.key)
          .map((g: any) => ({ key: String(g.key), label: g.label, value_type: g.value_type })),
      })));
    }).catch(() => setOptions([]));
  }, []);
  return options;
}

export function DatasetSelect({
  value, onChange, datasets, placeholder = tr('k_f4bf86975d'),
}: {
  value?: string; onChange: (v: string) => void;
  datasets: DatasetOption[]; placeholder?: string;
}) {
  return (
    <Select value={value || ''} onValueChange={onChange}>
      <SelectTrigger><SelectValue placeholder={placeholder} /></SelectTrigger>
      <SelectContent>
        {datasets.length === 0 && (
          <SelectItem value="__empty__" disabled>{tr('k_75c9abe4cf')}</SelectItem>
        )}
        {datasets.map(opt => (
          <SelectItem key={opt.name} value={opt.name}>{opt.name}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

// ── 数据集 SQL 预览 + 详情跳转（物化配置面就地看正文，免切页核对） ─────
// 正文只在**详情**接口（清单档按 list-pagination §5 已剥掉 sql_query），
// 故选中后按 id 懒加载。失败/正文缺失/数据集不在清单一律显式提示 + 可重试，
// 不静默留空（no-silent-degradation）；详情用 <a target="_blank"> 新开——
// DAG 编辑器画布有未保存改动，同页跳转会把编排丢掉。
export function DatasetSqlPreview({ datasetName, datasets }: {
  datasetName?: string; datasets: DatasetOption[];
}) {
  const opt = datasetName ? datasets.find(d => d.name === datasetName) : undefined;
  const id = opt?.id;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [blocks, setBlocks] = useState<{ label?: string; sql: string }[]>([]);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!id) { setBlocks([]); setError(null); setLoading(false); return; }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setBlocks([]);
    client.get(`/datasets/${id}`).then(({ data }) => {
      if (cancelled) return;
      const out: { label?: string; sql: string }[] = [];
      const main = String(data?.sql_query || '').trim();
      if (main) out.push({ sql: main });
      // 多方言正文（{引擎|方言族: 正文}，JSON 文本）：逐键展示，不替执行器猜该用哪段
      const raw = data?.sql_bodies;
      if (raw) {
        let bodies: Record<string, unknown> | null = null;
        try { bodies = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch { bodies = null; }
        if (bodies && typeof bodies === 'object') {
          Object.entries(bodies).forEach(([key, value]) => {
            const sql = String(value || '').trim();
            if (sql) out.push({ label: key, sql });
          });
        } else {
          // 正文损坏 = 该数据集当前取不出数，必须喊出来而不是只显示 sql_query
          setError(tr('k_35008187a5'));
        }
      }
      setBlocks(out);
    }).catch((e: any) => {
      if (cancelled) return;
      setError(e?.response?.data?.detail || tr('k_6002474108'));
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [id, reload]);

  if (!datasetName) return null;
  // 清单还没到（或清单为空时由选择器那条空态文案负责）：这里不抢发「不在清单」的结论
  if (datasets.length === 0) return null;

  return (
    <div className="mt-2 border rounded-lg p-2 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium">{tr('k_d0419eca3f')}</span>
        {id && (
          <a href={`/data/datasets/${id}`} target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline">
            {tr('k_1f35df7aae')}<ExternalLink className="w-3 h-3" />
          </a>
        )}
      </div>
      {!id && <p className="text-[10px] text-muted-foreground">{tr('k_4aa822bcae')}</p>}
      {id && loading && (
        <p className="text-[10px] text-muted-foreground">{tr('k_300ee3dee4')}</p>
      )}
      {id && !loading && error && (
        <div className="flex items-center gap-2">
          <p className="text-[10px] text-destructive min-w-0 break-all">{error}</p>
          <Button variant="outline" size="sm" className="h-5 px-1.5 text-[10px] shrink-0"
            onClick={() => setReload(r => r + 1)}>
            {tr('k_e2d53a6d3a')}
          </Button>
        </div>
      )}
      {id && !loading && !error && blocks.length === 0 && (
        <p className="text-[10px] text-muted-foreground">{tr('k_4b6631b72d')}</p>
      )}
      {blocks.map((b, i) => (
        <div key={`${b.label || 'main'}-${i}`}>
          {b.label && (
            <div className="text-[10px] font-mono text-muted-foreground">{b.label}</div>
          )}
          <pre className="text-[10px] font-mono bg-muted/40 rounded p-1.5 max-h-36 overflow-auto
            whitespace-pre-wrap break-all leading-relaxed">{b.sql}</pre>
        </div>
      ))}
    </div>
  );
}
