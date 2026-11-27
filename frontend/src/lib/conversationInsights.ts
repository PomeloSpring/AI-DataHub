/**
 * 会话侧栏派生（会话产物 + 关键知识）——纯函数，唯一一份口径。
 *
 * 数据来源只有**已加载的会话消息**（`adh_conversations.messages` 里那一份 JSON）：
 * 提问、文档、报告草稿、工具调用参数与结果都已经在会话记录里，侧栏只是把它们换个位置
 * 呈现 —— 不请求新接口、不调模型、不新增存储，因此不存在"额外消耗 token"或"另写一份事实源"。
 *
 * 字段一律**白名单提取**（只取需要的键），不是"整份 dump 再打码"：
 * `get_glossary` 的 `target_table`/`target_column`、`search_metadata` 的物理表与数据源名
 * 属内部映射/目录产物，不进侧栏（护栏 §7）。
 * `knowledge_search` 的片段正文则是**用户点名要看的内容**（排查知识是否不全/错误、或直接学习）：
 * 它本就随会话响应下发给本人、同一份文本在执行过程里也原样渲染，侧栏只是换个位置呈现，
 * 不新增信息面；正文同样截断并显式标注（`clipped` / `contentMissing`），
 * 完整原文仍在该轮执行过程里。
 */
import type { ChatMessage, ChatReportDraft } from '../stores/chatStore';
import i18n from '../i18n';

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
        title: String(doc.title || '').trim() || i18n.t('chat:docCard.untitled'),
        updatedAt: String(doc.updated_at || doc.created_at || ''),
        revisions: Number(doc.revisions || 0),
      });
    }
    const draft = draftOf((m as any).reportDraft);
    if (draft) {
      const archived = draftOf((draft as any).archived) as any;
      reports.push({
        idx,
        title: String(draft.title || '').trim() || i18n.t('chat:sidePanel.untitledDraft'),
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

// ── 会话技能 ─────────────────────────────────────────────────────

export interface SkillUsageCard {
  name: string;
  /** 首次加载它的那一轮（与产物/知识同一个锚点口径，供定位跳转） */
  idx: number;
  /** 以下三个是**回合数**（不是技能名出现次数），与 list 接口的 skill_usage 同口径 */
  resident: number;
  attached: number;
  fetched: number;
}

export interface SkillInsights {
  cards: SkillUsageCard[];
  /** 本会话的回答回合数（判“还没开始”与“早于记录上线”是两件事，不能混说） */
  turns: number;
  /** 会话里出现过装载事实字段（哪怕数组为空）：整份都缺 = 该字段上线前的历史会话 */
  recorded: boolean;
}

/** 三条通道与消息字段一一对应（顺序即「怎么到达模型」的优先级）。 */
const SKILL_FLAGS = ['skills_loaded', 'skills_attached', 'skills_fetched'] as const;

/** 从会话消息派生「会话技能」：本会话每轮真正读到过哪几份作业细则。
 *
 * 事实源是回合 meta（`skills_loaded/attached/fetched`，由执行层写入），不是模型自述：
 * 用户排查“答得不按口径”时，这里就是“细则到底加载了没有”的唯一依据。
 * 与产物/知识同一个口径：只读已加载的消息，不请求新接口、不另存一份。
 */
export function collectSkills(messages: ChatMessage[]): SkillInsights {
  const acc = new Map<string, SkillUsageCard>();
  let turns = 0;
  let recorded = false;

  (messages || []).forEach((m, idx) => {
    if (!m || m.role !== 'assistant') return;
    turns += 1;
    const lists = SKILL_FLAGS.map(key => m[key]);
    if (lists.some(Array.isArray)) recorded = true;
    lists.forEach((list, flagIdx) => {
      if (!Array.isArray(list)) return;
      // 同一条消息里重复写同一个技能名只算一个回合（计数口径是“回合数”，不是条目数）
      const names = new Set(list.map(x => String(x || '').trim()).filter(Boolean));
      for (const name of names) {
        const card = acc.get(name) || { name, idx, resident: 0, attached: 0, fetched: 0 };
        if (flagIdx === 0) card.resident += 1;
        else if (flagIdx === 1) card.attached += 1;
        else card.fetched += 1;
        acc.set(name, card);
      }
    });
  });

  const cards = [...acc.values()].sort((a, b) =>
    (b.resident + b.attached + b.fetched) - (a.resident + a.attached + a.fetched)
    || a.name.localeCompare(b.name));
  return { cards, turns, recorded };
}

// ── 关键知识 ─────────────────────────────────────────────────────

export interface ScopeCard {
  idx: number;
  object: string;
  metrics: string[];
  dimensions: string[];
  timeWindow: string;
  /** 生效站点：跨站查询会一站发一条 intent（其余字段完全相同），合并成一张卡按序列出，
   *  不再按站点拆卡刷屏；跳转锚点取首次出现的那一轮。 */
  sites: string[];
  filters: string[];
}

export interface DictEntry {
  name: string;
  kind: 'metric' | 'dimension';
  unit: string;
  description: string;
  enumLabels: string[];
}

export interface CitationSource {
  /** 出处标题（只到标题，不出内部标识） */
  title: string;
  /** 命中知识库的业务名（服务端没给则空串，不编造） */
  kb: string;
  /** 命中正文：result 可解析时取原文，否则取服务端 citation_summary 的 excerpt（同一份文本） */
  excerpt: string;
  /** 正文被裁（服务端预算/落库截断）：侧栏只给一段，完整原文在该轮执行过程里 */
  clipped: boolean;
}

export interface CitationCard {
  idx: number;
  question: string;
  /** 出处清单（标题 + 正文摘录，正文可展开查看） */
  sources: CitationSource[];
  chunks: number;
  /** 一段正文都没有（旧会话的摘要只存了标题）：必须显式提示，不能摆个展开键装作有内容 */
  contentMissing: boolean;
}

export interface KnowledgeInsights {
  scopes: ScopeCard[];
  citations: CitationCard[];
  /** 结果过长被截断、无法结构化的工具调用条数（诊断口径；侧栏只列引用，不渲染该计数） */
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

/** 过滤算子的可读形式（内部码值不能直接面用户，ui-resource-display §1）。
 * 符号算子语言无关；词形算子只存 key，调用期经 i18n 解析（不冻结语言）。 */
const OP_LABELS: Record<string, string> = {
  eq: '=', ne: '≠', gt: '>', gte: '≥', lt: '<', lte: '≤',
};
const OP_LABEL_KEYS: Record<string, 'sidePanel.op.in' | 'sidePanel.op.not_in' | 'sidePanel.op.like' | 'sidePanel.op.between'> = {
  in: 'sidePanel.op.in', not_in: 'sidePanel.op.not_in', like: 'sidePanel.op.like', between: 'sidePanel.op.between',
};

interface RawFilter { dim: string; op: string; values: string[] }

/** 口径去重键：**不含 site** —— 跨站查询一站发一条 intent，除站点外字段完全相同，
 *  按站点拆卡就是侧栏「同款口径刷屏」的来源，合并后站点并列列出（信息不丢）。
 *  指标/维度/过滤值做排序归一：顺序不同不代表口径不同，别让它拆成两张卡。 */
function scopeKey(s: {
  object: string; timeWindow: string; metrics: string[]; dimensions: string[]; filters: RawFilter[];
}): string {
  const filters = s.filters
    .map(f => `${f.dim}${f.op}${[...f.values].sort().join('/')}`)
    .sort()
    .join(',');
  return [s.object, s.timeWindow, [...s.metrics].sort().join(','),
    [...s.dimensions].sort().join(','), filters].join('|');
}

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
    out.push({ dim, op: OP_LABELS[rawOp] || (OP_LABEL_KEYS[rawOp] ? i18n.t(`chat:${OP_LABEL_KEYS[rawOp]}` as 'chat:sidePanel.op.in') : rawOp), values });
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

/** 出处标题：chunk.title 优先；本体同步文档没有标题时从 markdown 小标题取"名字"（不取正文）。
 * 两个小标题正则是后端上云文档的固定契约（功能性解析，不随界面语言变）。 */
const HEADING_OBJECT = /^##\s*\u4e1a\u52a1\u5bf9\u8c61[:：]\s*([^(（\n]+)/m;
const HEADING_MODEL = /^#\s*\u672c\u4f53\u6a21\u578b[:：]\s*(.+)$/m;
const HEADING_ANY = /^#{1,3}\s*(.+)$/m;

function sourceTitleOf(chunk: any): string {
  const titled = clip(chunk?.title, 60);
  if (titled) return titled;
  const content = String(chunk?.content || '');
  const obj = content.match(HEADING_OBJECT);
  if (obj?.[1]) return i18n.t('chat:sidePanel.citationObject', { name: String(obj[1]).trim() });
  const model = content.match(HEADING_MODEL);
  if (model?.[1]) return i18n.t('chat:sidePanel.citationModel', { name: clip(model[1], 60) });
  const any = content.match(HEADING_ANY);
  if (any?.[1]) return clip(any[1], 60);
  const first = content.split('\n').map(s => s.trim()).find(Boolean);
  return first ? clip(first, 40) : i18n.t('chat:sidePanel.citationFragment');
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

/** 侧栏正文上限：给一段够读的文字（排查知识质量/学习），完整原文留在该轮执行过程里。 */
const EXCERPT_CHARS = 400;

function excerptOf(content: unknown, serverClipped = false): { text: string; clipped: boolean } {
  const text = String(content || '').trim();
  if (text.length <= EXCERPT_CHARS) return { text, clipped: serverClipped };
  return { text: text.slice(0, EXCERPT_CHARS), clipped: true };
}

/** 一条命中出处：标题 + 知识库名 + 正文摘录（正文空则该出处不可展开）。 */
function sourceOf(chunk: any): CitationSource {
  const { text, clipped } = excerptOf(chunk?.content, !!chunk?.content_truncated);
  return {
    title: sourceTitleOf(chunk),
    kb: clip(chunk?.knowledge_base, 60),
    excerpt: text,
    clipped,
  };
}

/** 摘要里的出处：新形态是带正文的对象，旧会话是纯标题字符串 —— 两种都要认。 */
function summarySourceOf(item: any): CitationSource | null {
  if (typeof item === 'string') {
    const title = item.trim();
    return title ? { title, kb: '', excerpt: '', clipped: false } : null;
  }
  if (!item || typeof item !== 'object') return null;
  const title = String(item.title || '').trim();
  if (!title) return null;
  return {
    title,
    kb: clip(item.knowledge_base, 60),
    excerpt: String(item.excerpt || '').trim(),
    clipped: !!item.excerpt_truncated,
  };
}

/** 从会话消息派生「关键知识」：本次口径 + 知识引用出处。
 *
 * 两趟：第一趟只读原始事实（intent / 枚举标签 / 检索），第二趟才给过滤值解枚举标签 ——
 * `get_metrics` 常常出现在 `run_semantic_query` 之后，单趟按消息顺序解会漏（同一个维度
 * 的码值标签在后一趟才到手）。过滤条件面用户必须显示业务标签而不是 `prod` 这类码值。
 * 口径卡按 `scopeKey` 合并：**仅站点不同**的 intent（跨站 fan-out）合成一张卡列站点，
 * 指标/维度/窗口/过滤任一不同都是不同口径，照旧分卡。 */
export function collectKnowledge(messages: ChatMessage[]): KnowledgeInsights {
  interface RawScope { idx: number; object: string; metrics: string[]; dimensions: string[]; timeWindow: string; sites: string[]; filters: RawFilter[] }
  const scopesByKey = new Map<string, RawScope>();
  const citations: CitationCard[] = [];
  const enums: Record<string, Record<string, string>> = {};
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
          const key = scopeKey({ object: objectName, timeWindow, metrics, dimensions, filters });
          const found = scopesByKey.get(key);
          if (found) {
            // 同款口径再来一条（跨站 fan-out 或重复提问）：只补站点，不另起一张卡
            if (site && !found.sites.includes(site)) found.sites.push(site);
            continue;
          }
          scopesByKey.set(key, {
            idx, object: objectName, metrics, dimensions, timeWindow,
            sites: site ? [site] : [], filters,
          });
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
            const sources: CitationSource[] = [];
            const seenTitles = new Set<string>();
            for (const chunk of chunks) {
              const src = sourceOf(chunk);
              if (seenTitles.has(src.title)) continue;
              seenTitles.add(src.title);
              sources.push(src);
            }
            const dedupe = `${question}|${sources.map(s => s.title).join(',')}`;
            if (!sources.length || seenCitation.has(dedupe)) continue;
            seenCitation.add(dedupe);
            citations.push({
              idx, question, sources, chunks: chunks.length,
              contentMissing: sources.every(s => !s.excerpt),
            });
          }
        } else {
          // 结果被截断时回退服务端轻量引用摘要（在原始结果上抽取，不受落库截断影响）
          const summary = Array.isArray(tc?.citation_summary) ? tc.citation_summary : [];
          if (summary.length) {
            for (const s of summary) {
              const question = clip(s?.question, 80);
              const sources: CitationSource[] = [];
              for (const item of (Array.isArray(s?.sources) ? s.sources : [])) {
                const src = summarySourceOf(item);
                if (!src || sources.some(x => x.title === src.title)) continue;
                sources.push(src);
              }
              const dedupe = `${question}|${sources.map(x => x.title).join(',')}`;
              if (!sources.length || seenCitation.has(dedupe)) continue;
              seenCitation.add(dedupe);
              citations.push({
                idx, question, sources, chunks: sources.length,
                contentMissing: sources.every(x => !x.excerpt),
              });
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

  const rawScopes = [...scopesByKey.values()];
  const scopes: ScopeCard[] = rawScopes.map(s => ({
    idx: s.idx, object: s.object, metrics: s.metrics, dimensions: s.dimensions,
    timeWindow: s.timeWindow, sites: s.sites,
    filters: s.filters.map(f => {
      const byCode = enums[f.dim] || {};
      const value = f.values.map(v => byCode[v] || v).join('/');
      return value ? `${f.dim} ${f.op} ${value}` : `${f.dim} ${f.op}`;
    }),
  }));

  return { scopes, citations, unparsed };
}

/** 会话里是否出现过任何可展示的知识条目（决定侧栏要不要摆空小节）。
 *  只算真正会被渲染的口径卡与引用卡 —— 截断计数不再是可展示内容。 */
export function hasKnowledge(ins: KnowledgeInsights): boolean {
  return !!(ins.scopes.length || ins.citations.length);
}
