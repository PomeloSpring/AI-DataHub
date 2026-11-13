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
  /** 渲染机（render_chart）产出的图表引用：数据在受管文档对象里，不在消息里 */
  | { kind: 'chart-ref'; chartRef: string; title?: string }
  | { kind: 'mermaid'; code: string; title?: string }
  | { kind: 'artifact'; type: 'excel' | 'pdf' | 'html' | 'md' | 'png' | 'jpg' | 'gif' | 'webp' | 'csv'; filename: string; content?: string; path?: string; theme?: string; description?: string }
  | { kind: 'sql'; code: string }
  | { kind: 'graph'; title?: string; nodes: { id: string; label?: string; type?: string }[]; edges: { source: string; target: string; label?: string }[] }
  | { kind: 'design'; designId: string }
  /** 交付形态渲染机产出的整份文档引用（正文在受管文档对象里，块只携带 ref） */
  | { kind: 'report'; docId: string; bodyKind: string; title?: string }
  /** 流式期间 ` ```report ` 围栏尚未闭合：渲染进度占位，**不得**把半途 JSON 当代码块念给用户 */
  | { kind: 'report-pending'; bodyKind?: string }
  | { kind: 'raw'; text: string }; // 解析失败 → 原样代码文本

// 匹配 ```chart ... ``` 围栏(语言标注为 chart,大小写不敏感)
const CHART_FENCE = /```chart[ \t]*\r?\n([\s\S]*?)```/gi;
// 匹配 ```mermaid ... ``` 围栏
const MERMAID_FENCE = /```mermaid[ \t]*\r?\n([\s\S]*?)```/gi;
// 匹配 ```artifact ... ``` 围栏
const ARTIFACT_FENCE = /```artifact[ \t]*\r?\n([\s\S]*?)```/gi;
// 匹配 ```sql ... ``` 围栏(SQL 卡片:复制/在 Playground 打开)
const SQL_FENCE = /```sql[ \t]*\r?\n([\s\S]*?)```/gi;
// 匹配 ```graph ... ``` 围栏(关系图谱 JSON: {title?, nodes:[{id,label?,type?}], edges:[{source,target,label?}]})
const GRAPH_FENCE = /```graph[ \t]*\r?\n([\s\S]*?)```/gi;
// 匹配 ```design ... ``` 围栏(仪表盘设计卡片引用 JSON: {"design_id":"..."},由 LLM 需要用户打开面板时内嵌)
const DESIGN_FENCE = /```design[ \t]*\r?\n([\s\S]*?)```/gi;
// 匹配 ```report ... ``` 围栏(交付文档引用 JSON: {"doc_id":"...","body_kind":"html|spec","title":"..."})
// 正文不在消息里：渲染机把整份文档落成受管对象，此处只携引用，组件按 doc_id 取回后渲染
const REPORT_FENCE = /```report[ \t]*\r?\n([\s\S]*?)```/gi;

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

/** 解析单个 chart 块 body(JSON)→ chart / chart-ref 段或 raw 段(降级)。
 *  两种形态：
 *  - `chart_ref`：渲染机产出的引用（数据由服务端取并落文档对象，数字不经模型转抄）；
 *  - 内联 `columns + rows`：历史契约，保留给存量消息回放。
 * 引用形态单独成一个 kind 而不是把 data 做成可选：消费方拿到 `data` 缺失的 chart 段
 * 只会渲染出空白卡片（正是本次要消除的那类静默）。 */
export function parseChartBody(body: string): ChartSegment {
  const text = body.trim();
  try {
    const obj = JSON.parse(text);
    const chartRef = typeof obj?.chart_ref === 'string' ? obj.chart_ref.trim() : '';
    if (chartRef) {
      return {
        kind: 'chart-ref',
        chartRef,
        title: typeof obj.title === 'string' ? obj.title : undefined,
      };
    }
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

/** 解析 ```graph 块 body(JSON)→ graph 段或 raw 段(降级)。
 *  节点须有 id;边的端点不在节点集内时丢弃该边(宁缺勿悬空)。 */
export function parseGraphBody(body: string): ChartSegment {
  try {
    const obj = JSON.parse(body);
    const rawNodes = obj?.nodes;
    const rawEdges = obj?.edges;
    if (!Array.isArray(rawNodes) || !Array.isArray(rawEdges)) throw new Error('missing fields');
    const nodes = rawNodes
      .filter((n: any) => n && n.id !== undefined && n.id !== null && n.id !== '')
      .map((n: any) => ({
        id: String(n.id),
        label: typeof n.label === 'string' ? n.label : undefined,
        type: typeof n.type === 'string' ? n.type : undefined,
      }));
    const ids = new Set(nodes.map(n => n.id));
    const edges = rawEdges
      .filter((e: any) => e && ids.has(String(e?.source)) && ids.has(String(e?.target)))
      .map((e: any) => ({
        source: String(e.source),
        target: String(e.target),
        label: typeof e.label === 'string' ? e.label : undefined,
      }));
    return {
      kind: 'graph',
      title: typeof obj.title === 'string' ? obj.title : undefined,
      nodes,
      edges,
    };
  } catch {
    return { kind: 'raw', text: body };
  }
}

/** 解析单个 artifact 块 body(JSON)→ artifact 段或 raw 段(降级)。
 *  支持两种交付: 内联 content(base64/文本) 或 按引用 path(会话工作区文件)。 */
export function parseArtifactBody(body: string): ChartSegment {
  const text = body.trim();
  try {
    const obj = JSON.parse(text);
    const type = obj?.type;
    const filename = obj?.filename;
    const content = typeof obj?.content === 'string' ? obj.content : undefined;
    const path = typeof obj?.path === 'string' ? obj.path : undefined;
    if (!type || !filename || (!content && !path)) throw new Error('missing fields');
    return {
      kind: 'artifact',
      type,
      filename,
      content,
      path,
      theme: typeof obj.theme === 'string' ? obj.theme : undefined,
      description: typeof obj.description === 'string' ? obj.description : undefined,
    };
  } catch {
    return { kind: 'raw', text };
  }
}

/** 解析 ```design 块 body(JSON)→ design 段或 raw 段(降级)。
 *  仅携带 design_id(展示字段由前端经鉴权 REST 实时拉取,块内不落静态快照/SQL)。 */
export function parseDesignBody(body: string): ChartSegment {
  const text = body.trim();
  try {
    const obj = JSON.parse(text);
    const designId = obj?.design_id;
    if (typeof designId !== 'string' || !designId.trim()) throw new Error('missing design_id');
    return { kind: 'design', designId: designId.trim() };
  } catch {
    return { kind: 'raw', text };
  }
}

/** 解析 ```report 块 body(JSON)→ report 段或 raw 段(降级)。
 *  只携带 doc_id：正文在渲染机落的受管文档对象里，REST 实时取回（不在消息里存快照）。
 *  body_kind 缺失时按 html 处理：后端写入时总带，漏写属异常形状，**不得**当“没这个块”丢掉。 */
export function parseReportBody(body: string): ChartSegment {
  const text = body.trim();
  try {
    const obj = JSON.parse(text);
    const docId = typeof obj?.doc_id === 'string' ? obj.doc_id.trim() : '';
    if (!docId) throw new Error('missing doc_id');
    return {
      kind: 'report',
      docId,
      bodyKind: typeof obj?.body_kind === 'string' ? obj.body_kind.trim() : 'html',
      title: typeof obj?.title === 'string' ? obj.title : undefined,
    };
  } catch {
    return { kind: 'raw', text };
  }
}

/** 把整段回答文本切分为 markdown / chart / mermaid / artifact / sql / graph / design / report / raw 交替片段。
 *  同一回答内相同 design_id 只保留第一个 design 块(重复引用不重复弹卡片)。
 *
 *  `streaming=true` 时额外处理未闭合的 ` ```report ` 围栏：引用块虽然小（正文不在块里），
 *  但把半途 JSON 当代码块逐字滑给用户同样是在展示一个还不存在的东西 —— 改为进度占位。
 *  只对 report 做这件事：其余块（chart/mermaid）未闭合时保持既有行为，不拿本次改动改它们。 */
export function splitChartBlocks(text: string, streaming = false): ChartSegment[] {
  const segs: ChartSegment[] = [];
  if (!text) return segs;
  const src = text;
  let last = 0;

  // 合并所有围栏正则,按出现位置排序
  const fences: { regex: RegExp; kind: string }[] = [
    { regex: CHART_FENCE, kind: 'chart' },
    { regex: MERMAID_FENCE, kind: 'mermaid' },
    { regex: ARTIFACT_FENCE, kind: 'artifact' },
    { regex: SQL_FENCE, kind: 'sql' },
    { regex: GRAPH_FENCE, kind: 'graph' },
    { regex: DESIGN_FENCE, kind: 'design' },
    { regex: REPORT_FENCE, kind: 'report' },
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
  const seenDesignIds = new Set<string>();
  const seenDocIds = new Set<string>();
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
    } else if (match.kind === 'sql') {
      segs.push({ kind: 'sql', code: match.body.trim() });
    } else if (match.kind === 'graph') {
      segs.push(parseGraphBody(match.body));
    } else if (match.kind === 'design') {
      const seg = parseDesignBody(match.body);
      if (seg.kind === 'design') {
        // 同一回答内重复引用同一设计只渲染一次卡片;块本身仍消耗,不再回退代码文本
        if (!seenDesignIds.has(seg.designId)) {
          seenDesignIds.add(seg.designId);
          segs.push(seg);
        }
      } else {
        segs.push(seg);
      }
    } else if (match.kind === 'report') {
      const seg = parseReportBody(match.body);
      if (seg.kind === 'report') {
        // 一轮只一个主交付物：同一 doc 重复引用只渲一次，但块照样消耗（不回退成源码给用户看）
        if (!seenDocIds.has(seg.docId)) {
          seenDocIds.add(seg.docId);
          segs.push(seg);
        }
      } else {
        segs.push(seg);
      }
    }
    last = match.index + match.length;
  }

  if (last < src.length) {
    segs.push({ kind: 'markdown', text: src.slice(last) });
  }
  if (streaming) {
    return collapsePendingReportFence(segs);
  }
  return segs;
}

/** 流式收尾：末段 markdown 里挂着未闭合的 ` ```report ` 围栏时，拆成进度占位。 */
export function collapsePendingReportFence(segs: ChartSegment[]): ChartSegment[] {
  const tail = segs[segs.length - 1];
  if (!tail || tail.kind !== 'markdown') return segs;
  const open = tail.text.search(/(^|\n)```report[ \t]*\r?\n/i);
  if (open < 0) return segs;
  const before = tail.text.slice(0, open);
  const out = segs.slice(0, -1);
  if (before.trim()) out.push({ kind: 'markdown', text: before });
  out.push({ kind: 'report-pending' });
  return out;
}
