/**
 * 图表块解析 — 从 Qoder 回答文本中提取 ```chart 围栏代码块。
 *
 * 契约(与后端 services/datamind/execution/chart_contract.py 对齐):
 *   ```chart
 *   {"title":"...","chart_type":"line","sql":"SELECT ...","columns":["月","额"],"rows":[["2026-01",120],...]}
 *   ```
 * rows 允许是「数组的数组」(按 columns 顺序)或「对象的数组」(键=列名);
 * 统一归一化为 ChartPicker 所需的对象数组;sql 为可选的查询语句(卡片内 SQL 视图)。
 * 解析失败的块降级为普通代码文本。
 */

export interface ChartData {
  columns: string[];
  rows: Record<string, any>[];
}

export type ChartSegment =
  | { kind: 'markdown'; text: string }
  | { kind: 'chart'; title?: string; chartType?: string; sql?: string; data: ChartData }
  | { kind: 'mermaid'; code: string; title?: string }
  | { kind: 'artifact'; type: 'excel' | 'pdf' | 'html' | 'md' | 'png' | 'jpg' | 'csv'; filename: string; content: string; description?: string }
  | { kind: 'raw'; text: string }; // 解析失败 → 原样代码文本

// 匹配 ```chart ... ``` 围栏(语言标注为 chart,大小写不敏感)
const CHART_FENCE = /```chart[ \t]*\r?\n([\s\S]*?)```/gi;
// 匹配 ```mermaid ... ``` 围栏
const MERMAID_FENCE = /```mermaid[ \t]*\r?\n([\s\S]*?)```/gi;
// 匹配 ```artifact ... ``` 围栏
const ARTIFACT_FENCE = /```artifact[ \t]*\r?\n([\s\S]*?)```/gi;

/** 把 rows(数组的数组 或 对象的数组)归一化为对象数组。 */
export function normalizeRows(columns: string[], rawRows: any[]): Record<string, any>[] {
  return rawRows.map((r) => {
    if (Array.isArray(r)) {
      const obj: Record<string, any> = {};
      columns.forEach((c, i) => {
        obj[c] = r[i];
      });
      return obj;
    }
    return r as Record<string, any>;
  });
}

/** 解析单个 chart 块 body(JSON)→ chart 段或 raw 段(降级)。 */
export function parseChartBody(body: string): ChartSegment {
  const text = body.trim();
  try {
    const obj = JSON.parse(text);
    const columns: string[] = Array.isArray(obj?.columns) ? obj.columns.map(String) : [];
    const rawRows: any[] = Array.isArray(obj?.rows) ? obj.rows : [];
    if (!columns.length || !rawRows.length) throw new Error('empty chart');
    return {
      kind: 'chart',
      title: typeof obj.title === 'string' ? obj.title : undefined,
      chartType: typeof obj.chart_type === 'string' ? obj.chart_type : undefined,
      sql: typeof obj.sql === 'string' && obj.sql.trim() ? obj.sql.trim() : undefined,
      data: { columns, rows: normalizeRows(columns, rawRows) },
    };
  } catch {
    return { kind: 'raw', text };
  }
}

/** 解析单个 artifact 块 body(JSON)→ artifact 段或 raw 段(降级)。 */
export function parseArtifactBody(body: string): ChartSegment {
  const text = body.trim();
  try {
    const obj = JSON.parse(text);
    const type = obj?.type;
    const filename = obj?.filename;
    const content = obj?.content;
    if (!type || !filename || !content) throw new Error('missing fields');
    return {
      kind: 'artifact',
      type,
      filename,
      content,
      description: typeof obj.description === 'string' ? obj.description : undefined,
    };
  } catch {
    return { kind: 'raw', text };
  }
}

/** 把整段回答文本切分为 markdown / chart / mermaid / artifact / raw 交替片段。 */
export function splitChartBlocks(text: string): ChartSegment[] {
  const segs: ChartSegment[] = [];
  if (!text) return segs;
  const src = text;
  let last = 0;

  // 合并所有围栏正则,按出现位置排序
  const fences: { regex: RegExp; kind: string }[] = [
    { regex: CHART_FENCE, kind: 'chart' },
    { regex: MERMAID_FENCE, kind: 'mermaid' },
    { regex: ARTIFACT_FENCE, kind: 'artifact' },
  ];

  // 收集所有匹配
  const matches: { index: number; length: number; kind: string; body: string }[] = [];
  for (const { regex, kind } of fences) {
    regex.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = regex.exec(src)) !== null) {
      matches.push({ index: m.index, length: m[0].length, kind, body: m[1] || '' });
    }
  }

  // 按位置排序
  matches.sort((a, b) => a.index - b.index);

  // 构建片段
  for (const match of matches) {
    if (match.index > last) {
      segs.push({ kind: 'markdown', text: src.slice(last, match.index) });
    }
    if (match.kind === 'chart') {
      segs.push(parseChartBody(match.body));
    } else if (match.kind === 'mermaid') {
      segs.push({ kind: 'mermaid', code: match.body.trim() });
    } else if (match.kind === 'artifact') {
      segs.push(parseArtifactBody(match.body));
    }
    last = match.index + match.length;
  }

  if (last < src.length) {
    segs.push({ kind: 'markdown', text: src.slice(last) });
  }
  return segs;
}
