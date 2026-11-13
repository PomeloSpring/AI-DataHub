/**
 * 交付文档对象 API — 渲染机（render_html/render_report/render_chart）产出的受管正文。
 *
 * 为什么要单独取：交付正文不落消息 content（会话存储的容量闸门不覆盖 content，
 * 整份 HTML 塞进消息会把会话表体量改坏），消息里只留 `{"doc_id": ...}` 引用，
 * 渲染时按引用取回 —— 与 ```design 块只带 design_id、面板实时拉取同一形态。
 */
import client from './client';

/** 交付正文形态：html=自包含文档，spec=结构化章节（平台渲染），chart-data=单图数据 */
export type RenderBodyKind = 'html' | 'spec' | 'chart-data';

export interface RenderDocument {
  id: string;
  conversation_id: number;
  kind: 'doc' | 'chart';
  body_kind: RenderBodyKind | string;
  title: string;
  theme: string | null;
  template_key: string | null;
  digest: string;
  size_chars: number;
  /** 体检/硬门的非阻断提示：必须可见（不得让用户以为"已按全部规范生成"） */
  warnings: string[];
  row_counts: Record<string, number> | null;
  updated_at: string;
  body: string;
}

/** 取回交付文档对象。三态错误码分开（前端要给用户不同文案，不能一律"加载失败"）。 */
export async function getRenderDocument(docId: string): Promise<RenderDocument> {
  const res = await client.get(`/chat/render-document/${encodeURIComponent(docId)}`);
  return res.data as RenderDocument;
}

/** 引用不可用时的可读原因：区分"被清理"与"无权访问"，排查方向完全不同。 */
export function renderDocumentErrorText(status?: number): string {
  if (status === 404) return '该交付内容已被清理（会话删除或保留策略裁剪），无法再打开';
  if (status === 403) return '无权访问该交付内容（只有产出它的会话属主可以打开）';
  if (status === 401) return '登录状态已失效，请重新登录后再打开该交付内容';
  return '交付内容暂时打不开，请重试';
}

/** `chart-data` 正文的形状（由服务端 render_chart 写入，列名/行数/图型都以它为准）。 */
export interface ChartDataPayload {
  chartType: string;
  title: string;
  columns: string[];
  rows: Record<string, any>[];
  rowCount: number;
  sql?: string;
}

/**
 * 解析图表数据对象正文 → 图表卡所需形状；形状不合就抛错。
 *
 * 不得宽容到底（缺列/缺行也回一个空对象）：缺数据的图表会被渲染成空白卡片，
 * 用户看到的是"没图"而不是"图的数据取不到"（no-silent-degradation）。
 * 抛出的文案要能区分成因，交给卡片显式展示。
 */
export function parseChartDataPayload(doc: RenderDocument): ChartDataPayload {
  let body: any = null;
  try {
    body = JSON.parse(doc.body || '{}');
  } catch {
    throw new Error('图表数据正文不是合法 JSON（交付对象已损坏），请让助手重新出图');
  }
  const columns: string[] = Array.isArray(body?.columns) ? body.columns.map(String) : [];
  const rawRows: any[] = Array.isArray(body?.rows) ? body.rows : [];
  const rows = rawRows.map((r: any) => {
    if (Array.isArray(r)) {
      const obj: Record<string, any> = {};
      columns.forEach((c, i) => { obj[c] = r[i]; });
      return obj;
    }
    return (r && typeof r === 'object') ? r : {};
  });
  if (!columns.length || !rows.length) {
    throw new Error('图表数据对象里没有可用的行列，请让助手重新出图');
  }
  return {
    chartType: typeof body.chart_type === 'string' ? body.chart_type : '',
    title: typeof body.title === 'string' ? body.title : '',
    columns,
    rows,
    rowCount: Number.isFinite(Number(body?.row_count)) ? Number(body.row_count) : rows.length,
    sql: typeof body?.sql === 'string' && body.sql.trim() ? body.sql.trim() : undefined,
  };
}
