/**
 * 会话侧栏派生（会话产物 + 关键知识）——纯函数，唯一一份口径。
 *
 * 数据来源只有**已加载的会话消息**（`adh_conversations.messages` 里那一份 JSON）：
 * 提问、文档、报告草稿、工具调用参数与结果都已经在会话记录里，侧栏只是把它们换个位置
 * 呈现 —— 不请求新接口、不调模型、不新增存储，因此不存在"额外消耗 token"或"另写一份事实源"。
 *
 * 字段一律**白名单提取**（只取需要的键），不是"整份 dump 再打码"：
 * `get_glossary` 的返回里带 `target_table`/`target_column`，`knowledge_search` 的片段正文
 * 带物理表名（如 `t_user_customer`），这些属护栏 §7 不得对客户端渲染的内容。
 * 侧栏因此只呈现业务名与口径文字，引用只到"出处标题"为止（原文仍可在执行过程里看，
 * 那份文本本就随会话响应下发给本人，这里不新增信息面）。
 */
import type { ChatMessage, ChatReportDraft } from '../stores/chatStore';

// ── 会话产物 ─────────────────────────────────────────────────────

export interface DocArtifact {
  idx: number;
  title: string;
  updatedAt: string;
  revisions: number;
}

export interface ReportArtifact {
  idx: number;
  title: string;
  updatedAt: string;
  revisions: number;
  /** 已归档进报告中心（非空 = 不再给重复归档入口） */
  reportId: number;
  archivedAt: string;
  /** 草稿正文长度（侧栏只显示规模，正文本身在编辑面板里看） */
  size: number;
}

export interface SessionArtifacts {
  docs: DocArtifact[];
  reports: ReportArtifact[];
}

const draftOf = (v: unknown): ChatReportDraft | null =>
  (v && typeof v === 'object' ? (v as ChatReportDraft) : null);

/** 会话产物里的「文档」与「报告草稿」：都挂在产生它们的那条回答上，按消息顺序返回。 */
export function collectArtifacts(messages: ChatMessage[]): SessionArtifacts {
  const docs: DocArtifact[] = [];
  const reports: ReportArtifact[] = [];
  (messages || []).forEach((m, idx) => {
    if (!m || m.role !== 'assistant') return;
    const doc = (m as any).doc;
    if (doc && typeof doc === 'object') {
      docs.push({
        idx,
        title: String(doc.title || '').trim() || '无标题',
        updatedAt: String(doc.updated_at || doc.created_at || ''),
        revisions: Number(doc.revisions || 0),
      });
    }
    const draft = draftOf((m as any).reportDraft);
    if (draft) {
      const archived = draftOf((draft as any).archived) as any;
      reports.push({
        idx,
        title: String(draft.title || '').trim() || '未命名报告草稿',
        updatedAt: String(draft.updated_at || draft.created_at || ''),
        revisions: Number(draft.revisions || 0),
        reportId: Number(archived?.report_id || 0),
        archivedAt: String(archived?.archived_at || ''),
        size: String(draft.markdown || '').length,
      });
    }
  });
  return { docs, reports };
}

// ── 关键知识 ─────────────────────────────────────────────────────

export interface ScopeCard {
  idx: number;
  object: string;
  metrics: string[];
  dimensions: string[];
  timeWindow: string;
  site: string;
  filters: string[];
}

export interface DictEntry {
  name: string;
  kind: 'metric' | 'dimension';
  unit: string;
  description: string;
  enumLabels: string[];
}

export interface CitationCard {
  idx: number;
  question: string;
  /** 出处标题清单（只到标题，不含片段正文） */
  sources: string[];
  chunks: number;
}

export interface KnowledgeInsights {
  scopes: ScopeCard[];
  citations: CitationCard[];
  /** 结果过长被截断、无法结构化的工具调用条数（必须显式暴露，不能当成"没有知识"） */
  unparsed: number;
}

/** `mcp__datahub_semantic__run_semantic_query` → `run_semantic_query`（取最后一段工具名） */
function toolSuffix(name: unknown): string {
  const raw = String(name || '').trim();
  if (!raw) return '';
  const parts = raw.split('__');
  return parts[parts.length - 1] || '';
}

function safeParse(text: unknown): any | null {
  const raw = typeof text === 'string' ? text : '';
  if (!raw.trim()) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

const strList = (v: unknown): string[] => Array.isArray(v)
  ? v.map(x => String(x || '').trim()).filter(Boolean)
  : [];

/** 描述截断：口径文字够用即可，长文本收进执行过程看原文。 */
function clip(text: unknown, max = 160): string {
  const one = String(text || '').replace(/\s+/g, ' ').trim();
  return one.length > max ? `${one.slice(0, max)}…` : one;
}

/** 过滤算子的可读形式（内部码值不能直接面用户，ui-resource-display §1）。 */
const OP_LABELS: Record<string, string> = {
  eq: '=', ne: '≠', gt: '>', gte: '≥', lt: '<', lte: '≤',
  in: '属于', not_in: '不属于', like: '包含', between: '区间',
};

interface RawFilter { dim: string; op: string; values: string[] }

/** intent 里的过滤条件：先取业务名与码值，标签在第二趟按字典枚举解析。 */
function rawFilters(filters: unknown): RawFilter[] {
  if (!Array.isArray(filters)) return [];
  const out: RawFilter[] = [];
  for (const f of filters) {
    if (!f || typeof f !== 'object') continue;
    const dim = String((f as any).dim || (f as any).dimension || '').trim();
    if (!dim) continue;
    const rawOp = String((f as any).op || '').trim() || 'eq';
    const values = Array.isArray((f as any).values)
      ? strList((f as any).values)
      : (String((f as any).value ?? '').trim() ? [String((f as any).value).trim()] : []);
    out.push({ dim, op: OP_LABELS[rawOp] || rawOp, values });
  }
  return out;
}

/** 从一次 run_semantic_query 调用里取 intent 清单（参数优先，结果兜底）。 */
function intentsOf(tc: any): any[] {
  const args = tc?.arguments;
  const fromArg = (v: unknown): any[] => {
    if (!v) return [];
    if (typeof v === 'string') {
      const parsed = safeParse(v);
      return Array.isArray(parsed) ? parsed : (parsed ? [parsed] : []);
    }
    return typeof v === 'object' ? [v] : [];
  };
  let list = [
    ...fromArg(args?.intent_json), ...fromArg(args?.intent),
    ...fromArg(args?.intents_json), ...fromArg(args?.intents),
  ];
  if (!list.length) {
    const result = safeParse(tc?.result);
    const rows = Array.isArray(result?.results) ? result.results : [];
    list = rows.map((r: any) => r?.intent).filter((r: any) => r && typeof r === 'object');
  }
  return list.filter(i => i && typeof i === 'object');
}

/** 出处标题：chunk.title 优先；本体同步文档没有标题时从 markdown 小标题取"名字"（不取正文）。 */
const HEADING_OBJECT = /^##\s*业务对象[:：]\s*([^(（\n]+)/m;
const HEADING_MODEL = /^#\s*本体模型[:：]\s*(.+)$/m;
const HEADING_ANY = /^#{1,3}\s*(.+)$/m;

function sourceTitleOf(chunk: any): string {
  const titled = clip(chunk?.title, 60);
  if (titled) return titled;
  const content = String(chunk?.content || '');
  const obj = content.match(HEADING_OBJECT);
  if (obj?.[1]) return `业务对象 · ${String(obj[1]).trim()}`;
  const model = content.match(HEADING_MODEL);
  if (model?.[1]) return `本体模型 · ${clip(model[1], 60)}`;
  const any = content.match(HEADING_ANY);
  if (any?.[1]) return clip(any[1], 60);
  const first = content.split('\n').map(s => s.trim()).find(Boolean);
  return first ? clip(first, 40) : '知识库片段';
}

/** 字典条目的码值→业务标签表（供过滤值显示成「生产」而不是「prod」）。 */
function enumLabelsOf(payload: any): Record<string, Record<string, string>> {
  const out: Record<string, Record<string, string>> = {};
  for (const bucket of (Array.isArray(payload?.objects) ? payload.objects : [])) {
    for (const kind of ['metrics', 'dimensions'] as const) {
      for (const row of (Array.isArray(bucket?.[kind]) ? bucket[kind] : [])) {
        const name = String(row?.name || '').trim();
        const enumMap = row?.enum && typeof row.enum === 'object' ? row.enum : {};
        if (!name || !Object.keys(enumMap).length) continue;
        const labels: Record<string, string> = { ...(out[name] || {}) };
        for (const [code, label] of Object.entries(enumMap)) {
          const l = String(label || '').trim();
          if (l) labels[String(code)] = l;
        }
        out[name] = labels;
      }
    }
  }
  return out;
}

/** 从会话消息派生「关键知识」：本次口径 + 知识引用出处。
 *
 * 两趟：第一趟只读原始事实（intent / 枚举标签 / 检索），第二趟才给过滤值解枚举标签 ——
 * `get_metrics` 常常出现在 `run_semantic_query` 之后，单趟按消息顺序解会漏（同一个维度
 * 的码值标签在后一趟才到手）。过滤条件面用户必须显示业务标签而不是 `prod` 这类码值。 */
export function collectKnowledge(messages: ChatMessage[]): KnowledgeInsights {
  interface RawScope { idx: number; object: string; metrics: string[]; dimensions: string[]; timeWindow: string; site: string; filters: RawFilter[] }
  const rawScopes: RawScope[] = [];
  const citations: CitationCard[] = [];
  const enums: Record<string, Record<string, string>> = {};
  const seenScope = new Set<string>();
  const seenCitation = new Set<string>();
  let unparsed = 0;

  (messages || []).forEach((m, idx) => {
    if (!m || m.role !== 'assistant') return;
    for (const tc of (m.tool_calls || [])) {
      const tool = toolSuffix(tc?.tool);
      if (!tool) continue;

      if (tool === 'run_semantic_query') {
        for (const intent of intentsOf(tc)) {
          const metrics = strList(intent.metrics);
          const dimensions = strList(intent.dimensions);
          const objectName = String(intent.object || '').trim();
          const timeWindow = String(intent.time_window || intent.timeWindow || '').trim();
          const site = String(intent.site || '').trim();
          const filters = rawFilters(intent.filters);
          if (!objectName && !metrics.length && !dimensions.length) continue;
          const dedupe = [objectName, site, timeWindow, metrics.join(','),
            dimensions.join(','), filters.map(f => `${f.dim}${f.op}${f.values.join('/')}`).join(',')].join('|');
          if (seenScope.has(dedupe)) continue;
          seenScope.add(dedupe);
          rawScopes.push({ idx, object: objectName, metrics, dimensions, timeWindow, site, filters });
        }
        continue;
      }

      if (tool === 'get_metrics' || tool === 'get_dimensions') {
        const payload = safeParse(tc?.result);
        if (!payload) {
          if (String(tc?.result || '').trim()) unparsed += 1;
          continue;
        }
        // 只提取枚举标签（供「本次口径」过滤值解业务名），不再展示「命中的字典口径」
        for (const [name, byCode] of Object.entries(enumLabelsOf(payload))) {
          enums[name] = { ...(enums[name] || {}), ...byCode };
        }
        continue;
      }

      if (tool === 'knowledge_search' || tool === 'search_business_knowledge') {
        const payload = safeParse(tc?.result);
        if (payload) {
          const groups: any[] = Array.isArray(payload?.results) && payload.results.length
            ? payload.results
            : (payload ? [payload] : []);
          for (const g of groups) {
            const chunks = Array.isArray(g?.chunks) ? g.chunks : [];
            const question = clip(g?.question, 80);
            const sources: string[] = [];
            for (const chunk of chunks) {
              const title = sourceTitleOf(chunk);
              if (title && !sources.includes(title)) sources.push(title);
            }
            const dedupe = `${question}|${sources.join(',')}`;
            if (!sources.length || seenCitation.has(dedupe)) continue;
            seenCitation.add(dedupe);
            citations.push({ idx, question, sources, chunks: chunks.length });
          }
        } else {
          // 结果被截断时回退服务端轻量引用摘要（不受落库截断影响）
          const summary = Array.isArray(tc?.citation_summary) ? tc.citation_summary : [];
          if (summary.length) {
            for (const s of summary) {
              const question = clip(s?.question, 80);
              const sources = Array.isArray(s?.sources) ? s.sources.filter(Boolean) : [];
              const dedupe = `${question}|${sources.join(',')}`;
              if (!sources.length || seenCitation.has(dedupe)) continue;
              seenCitation.add(dedupe);
              citations.push({ idx, question, sources, chunks: sources.length });
            }
          } else if (String(tc?.result || '').trim()) {
            unparsed += 1;
          }
        }
        continue;
      }
      // 其余工具（search_metadata / get_table_schema / execute_sql / check_sql / get_glossary）
      // 给的是物理表列与 SQL 或字典口径，不属"关键知识"卡片，不进侧栏（护栏 §7）。
    }
  });

  const scopes: ScopeCard[] = rawScopes.map(s => ({
    idx: s.idx, object: s.object, metrics: s.metrics, dimensions: s.dimensions,
    timeWindow: s.timeWindow, site: s.site,
    filters: s.filters.map(f => {
      const byCode = enums[f.dim] || {};
      const value = f.values.map(v => byCode[v] || v).join('/');
      return value ? `${f.dim} ${f.op} ${value}` : `${f.dim} ${f.op}`;
    }),
  }));

  return { scopes, citations, unparsed };
}

/** 会话里是否出现过任何可展示的知识条目（决定侧栏要不要摆空小节）。 */
export function hasKnowledge(ins: KnowledgeInsights): boolean {
  return !!(ins.scopes.length || ins.citations.length || ins.unparsed);
}
