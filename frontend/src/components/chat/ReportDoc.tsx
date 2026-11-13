/**
 * 交付文档的结构化章节渲染面（body_kind = spec）— 报告正文在对话内逐章展开。
 *
 * 为什么 spec 不用 iframe：spec 的图位由平台组件渲染（`ChartPicker` → `DashboardChart`
 * 经 `useChartDesign`），于是三件事同时成立 —— 地理/富图型是真渲染（不是 HTML 单文件里
 * 退化的表格）、主题随查看者即时换装（改字模库存量报告跟着变）、图表可下钻。
 * 归档快照走另一条路（服务端把 spec 渲成自包含 HTML），因为报表要脱离会话独立存活。
 *
 * 口径：
 * 1. 数据来自文档对象本身（服务端逐章经语义层取），组件不再自己拼数字；
 * 2. 章节取数失败 = 显式「暂无数据 + 原因」，**不得**留空或用上一版数字（no-silent-degradation）；
 * 3. 服务端的非阻断提示（图型推断、结果截断）要可见。
 */
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AlertCircle } from 'lucide-react';
import ChartPicker from '@/components/ChartPicker';
import DataGrid from '@/components/DataGrid';
import MermaidBlock from '@/components/MermaidBlock';
import { Badge } from '@/components/ui/badge';
import type { RenderDocument } from '@/api/renderDocument';

interface SectionData {
  columns: string[];
  rows: Record<string, any>[];
  row_count?: number;
}

interface SpecSection {
  title?: string;
  kind: string;
  narrative?: string;
  chart_type?: string;
  code?: string;
  data?: SectionData;
  error?: string;
}

interface SpecDoc {
  title?: string;
  theme?: string;
  template_name?: string;
  sections: SpecSection[];
}

/** spec 正文解析：坏 JSON 或没有章节时回 null，由调用方显式报错（不静默渲染空报告）。 */
export function parseSpecDoc(doc: RenderDocument): SpecDoc | null {
  let body: any = null;
  try {
    body = JSON.parse(doc.body || '{}');
  } catch {
    return null;
  }
  const sections = Array.isArray(body?.sections) ? body.sections : null;
  if (!sections) return null;
  return {
    title: typeof body.title === 'string' ? body.title : undefined,
    theme: typeof body.theme === 'string' ? body.theme : undefined,
    template_name: typeof body.template_name === 'string' ? body.template_name : undefined,
    sections: sections.filter((s: any) => s && typeof s === 'object') as SpecSection[],
  };
}

function SectionBlock({ section, onDrill }: { section: SpecSection; onDrill?: (q: string) => void }) {
  const data = section.data;
  const columns = data?.columns || [];
  const rows = data?.rows || [];
  const truncated = !!data && Number.isFinite(data.row_count)
    && Number(data.row_count) > rows.length;

  // 取数失败：写明原因与出路，不留空白章（空白会被当成"这块没结论"）
  if (section.error) {
    return (
      <section className="my-3 rounded-md border border-dashed p-3">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          <AlertCircle className="h-4 w-4 text-muted-foreground" aria-hidden />
          {section.title || '本节'}：暂无数据
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {section.error}。平台不会沿用上一版数字顶替，需要时请重新发起该查询。
        </p>
      </section>
    );
  }

  return (
    <section className="my-3">
      {section.title && <h3 className="text-sm font-semibold mb-1.5">{section.title}</h3>}
      {section.narrative && (
        <div className="prose prose-sm max-w-none text-[13px] leading-relaxed">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{section.narrative}</ReactMarkdown>
        </div>
      )}
      {section.kind === 'mermaid' && (section.code
        ? <MermaidBlock code={section.code} />
        : <p className="text-xs text-muted-foreground">本节没有 mermaid 源码。</p>)}
      {(section.kind === 'chart' || section.kind === 'kpi' || section.kind === 'table') && (
        columns.length < 2 || !rows.length
          // 空数据章同样要说出来：渲染成空白 = 用户以为报告坏了，而不是"这章没取到数"
          ? <p className="text-xs text-muted-foreground">本节没有可展示的数据行。</p>
          : section.kind === 'chart'
            ? <ChartPicker data={{ columns, rows }} defaultType={section.chart_type} onDrill={onDrill} />
            : <DataGrid columns={columns} rows={rows} height={Math.min(360, 40 + rows.length * 32)}
                filename={section.title || 'report-section'} />
      )}
      {truncated && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          本节共 {data!.row_count} 行，图上只用了前 {rows.length} 行（不得据这些行做全量求和/计数）。
        </p>
      )}
    </section>
  );
}

export default function ReportDoc({ doc, onDrill }: { doc: RenderDocument; onDrill?: (q: string) => void }) {
  const spec = parseSpecDoc(doc);
  const warnings = (Array.isArray(doc.warnings) ? doc.warnings : []).filter(w => typeof w === 'string' && w);

  if (!spec) {
    // 引用有效但正文坏形：显式报错并给成因，不拿"空报告"糊过去，也不回退显示 JSON 源码
    return (
      <div role="alert" className="px-3 py-3 text-xs text-muted-foreground">
        这份交付文档的章节正文读不出来（结构不完整或已损坏），请让助手重新生成本报告。
      </div>
    );
  }

  return (
    <div className="px-3 pb-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 pt-2 text-[11px] text-muted-foreground">
        <span>{spec.sections.length} 个章节</span>
        {spec.theme && <span>· 主题 {spec.theme}</span>}
        {spec.template_name && <span>· 模板 {spec.template_name}</span>}
        <span>· 数据由服务端逐章经语义层取（章节内可下钻）</span>
      </div>
      {warnings.length > 0 && (
        <div className="flex flex-wrap gap-1 py-2">
          {warnings.map((w, i) => (
            <Badge key={i} variant="secondary" className="text-[11px] font-normal" title={w}>
              {w.length > 46 ? `${w.slice(0, 46)}…` : w}
            </Badge>
          ))}
        </div>
      )}
      {spec.sections.map((section, i) => <SectionBlock key={i} section={section} onDrill={onDrill} />)}
    </div>
  );
}
