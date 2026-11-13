/**
 * 图表引用卡 — ` ```chart {"chart_ref": "..."}` 按引用取回数据，再交给 ChartPicker 渲染。
 *
 * 为什么不是把数据抄在块里（旧 ` ```chart ` 形态）：数字由服务端经语义层取，
 * `row_count` 与取数同源；模型抄一遍就多一处"抄错一位也对不上账"的风险，还会把
 * 大结果集塞进会话消息。引用形态与 report/design 块同构：块只带 id，渲染时实时取回，
 * 图表/明细/SQL 三视图与图上下钻都还是 ChartPicker 那一套（不另造第二条渲染管线）。
 *
 * 三条口径与 ReportCard 一致：
 * 1. 引用取不到 = 显式错误（三态文案 + 重试），**不得**渲染空白卡片或回退显示源码；
 * 2. 形态不符（不是 chart-data）同样显式说明，不"当成柱状图画一下";
 * 3. 服务端的非阻断提示（warnings：图型推断、结果截断）必须可见。
 */
import { useCallback, useEffect, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import ChartPicker from '@/components/ChartPicker';
import {
  getRenderDocument, parseChartDataPayload, renderDocumentErrorText,
  type ChartDataPayload, type RenderDocument,
} from '@/api/renderDocument';

interface Props {
  /** ` ```chart ` 块携带的图表数据对象引用（render_chart 回执的 chart_id） */
  chartRef: string;
  /** 块内声明的标题；以文档标题为准，声明值只作首屏占位 */
  title?: string;
  /** 图上下钻（与内联图表卡同一入口，不另写一份） */
  onDrill?: (question: string) => void;
}

export default function ChartRefCard({ chartRef, title, onDrill }: Props) {
  const [doc, setDoc] = useState<RenderDocument | null>(null);
  const [chart, setChart] = useState<ChartDataPayload | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      const loaded = await getRenderDocument(chartRef);
      setDoc(loaded);
      if (loaded.body_kind !== 'chart-data') {
        // 形态不符不能"将就着画"：拿 spec/html 正文当图表数据只会画出空图
        setChart(null);
        setError(`该引用指向的是 ${loaded.body_kind} 形态的交付对象，不是图表数据。请让助手用 render_chart 重新出图`);
        return;
      }
      setChart(parseChartDataPayload(loaded));
    } catch (e: any) {
      setDoc(null);
      setChart(null);
      setError(e?.response?.status ? renderDocumentErrorText(e.response.status)
                                   : (e?.message || '图表数据暂时取不到，请重试'));
    } finally {
      setBusy(false);
    }
  }, [chartRef]);

  useEffect(() => { void reload(); }, [reload]);

  const warnings = (doc?.warnings || []).filter(w => typeof w === 'string' && w);

  return (
    <div className="my-3 rounded-lg border p-3 bg-card" title={chartRef}>
      <div className="flex items-center gap-2 mb-1">
        {(chart?.title || doc?.title || title) && (
          <div className="text-sm font-semibold">{chart?.title || doc?.title || title}</div>
        )}
        {chart && (
          <span className="text-[11px] text-muted-foreground">
            图型 {chart.chartType || '自动'} · 共 {chart.rowCount} 行
            {chart.rows.length < chart.rowCount ? `（图上用前 ${chart.rows.length} 行）` : ''}
          </span>
        )}
      </div>

      {error ? (
        <div role="alert" className="text-xs text-muted-foreground space-y-2">
          <p>{error}</p>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => { void reload(); }}>
            {busy ? '正在重试…' : '重试'}
          </Button>
        </div>
      ) : !chart ? (
        <div role="status" className="py-6 text-xs text-muted-foreground">正在加载图表数据…</div>
      ) : (
        <>
          {warnings.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-2">
              {warnings.map((w, i) => (
                <Badge key={i} variant="secondary" className="text-[11px] font-normal" title={w}>
                  {w.length > 46 ? `${w.slice(0, 46)}…` : w}
                </Badge>
              ))}
            </div>
          )}
          <ChartPicker
            data={{ columns: chart.columns, rows: chart.rows }}
            defaultType={chart.chartType || undefined}
            sql={chart.sql}
            onDrill={onDrill}
          />
        </>
      )}
    </div>
  );
}
