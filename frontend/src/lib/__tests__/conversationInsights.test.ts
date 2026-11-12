// 会话侧栏派生契约（零 token：只从已落库的会话消息里摘）
// ① 产物 = 消息上的 doc / reportDraft（含归档回执）；
// ② 关键知识 = 本次口径（run_semantic_query 的 intent）+ 命中的字典口径 + 知识引用出处；
// ③ 字段白名单：术语的 target_table/target_column、目录检索的物理表名、知识库片段正文
//    一律不得进入侧栏数据（护栏 §7：物理标识不对客户端渲染）；
// ④ 结果被截断时必须数出来（unparsed），不能静默当成"没有知识"。
import { describe, expect, it } from 'vitest';
import { collectArtifacts, collectKnowledge } from '../conversationInsights';

const assistant = (over: Record<string, any> = {}) => ({
  role: 'assistant', content: '结论正文', reply: '结论正文', ...over,
} as any);

const METRICS_RESULT = JSON.stringify({
  objects: [{
    object: 'rum_event',
    metrics: [{ name: '独立用户数', unit: '人', description: '按用户去重后的数量' }],
    dimensions: [
      { name: '应用名称', description: '应用名称（观测维度）' },
      { name: '本轮没用到的维度', description: '不该被列进侧栏' },
      { name: '应用环境', description: '部署环境', enum: { prod: '生产', test: '测试' } },
    ],
  }],
  total: 1,
});

const GLOSSARY_RESULT = JSON.stringify({
  items: [{
    term_cn: '联耀', term_en: 'Alliedstar', term_aliases: ['联耀科技'],
    description: '开放平台客户',
    // 涉密字段：物理映射不得进侧栏
    target_table: 'pc_isv_info', target_column: 'isv_code', calculation: 'SELECT 1 FROM x',
  }],
  total: 1,
});

// 真实形态：云端片段没有 title，正文是内部 md（含物理表名 t_user_customer）
const KNOWLEDGE_RESULT = [
  '{"results": [{"question": "联耀是什么客户",',
  '"chunks": [{"title": "", "content": "# 本体模型: Alliedstar Cloud Platform\\n',
  '## 业务对象: User (user)\\n\\n别名:User,用户,t_user_customer 描述:平台注册用户。',
  '###属性(业务名)- 主键 ID(int)"}]}]}',
].join('');

const messages = [
  { role: 'user', content: '查一下最近 12 个月各应用的独立用户数' } as any,
  assistant({
    doc: { title: '分析结论', html: '<p>正文</p>', created_at: '2026-11-06T10:00:00', updated_at: '2026-11-06T11:00:00', revisions: 3 },
    reportDraft: {
      title: '各应用独立用户数', markdown: '## 各应用独立用户数\n\n正文',
      created_at: '2026-11-06 15:00:00', updated_at: '2026-11-06 15:12:00', revisions: 1,
      archived: { report_id: 9, title: '各应用独立用户数', archived_at: '2026-11-06 15:20:00' },
    },
    tool_calls: [
      {
        tool: 'mcp__datahub_semantic__run_semantic_query',
        arguments: {
          intents_json: JSON.stringify([{
            object: 'rum_event', metrics: ['独立用户数'], dimensions: ['应用名称', '应用环境'],
            filters: [{ dim: '应用环境', op: 'eq', value: 'prod' }],
            time_window: '12M', site: '中国',
          }]),
        },
        result: '{"results": [{"index": 0, "object": "rum_event"}]}',
      },
      { tool: 'mcp__datahub_semantic__get_metrics', result: METRICS_RESULT },
      { tool: 'mcp__datahub_semantic__get_glossary', result: GLOSSARY_RESULT },
      { tool: 'mcp__datahub_semantic__knowledge_search', result: KNOWLEDGE_RESULT },
      // 目录检索给的是物理表名 + 数据源名：整类不进关键知识
      { tool: 'mcp__datahub_semantic__search_metadata', result: JSON.stringify({ tables: [{ table_name: 'observability_rum', datasource: 'prd-sls-cn' }] }) },
      // 被展示限长截断的半截 JSON：必须数出来，不能当成"没有知识"
      { tool: 'mcp__datahub_semantic__knowledge_search', result: '{"results": [{"question": "截断的' },
    ],
  }),
];

describe('collectArtifacts', () => {
  it('文档与报告草稿都挂在产生它们的那条回答上', () => {
    const got = collectArtifacts(messages);
    expect(got.docs).toEqual([
      { idx: 1, title: '分析结论', updatedAt: '2026-11-06T11:00:00', revisions: 3 },
    ]);
    expect(got.reports).toHaveLength(1);
    expect(got.reports[0]).toMatchObject({
      idx: 1, title: '各应用独立用户数', revisions: 1, reportId: 9,
    });
    expect(got.reports[0].updatedAt).toBe('2026-11-06 15:12:00');
  });

  it('没有草稿/文档的会话返回空清单（不造空条目）', () => {
    const got = collectArtifacts([{ role: 'user', content: '你好' } as any]);
    expect(got).toEqual({ docs: [], reports: [] });
  });
});

describe('collectKnowledge', () => {
  const got = collectKnowledge(messages);

  it('本次口径取自 intent：对象/指标/维度/时间窗/站点/过滤都是业务名', () => {
    expect(got.scopes).toHaveLength(1);
    expect(got.scopes[0]).toMatchObject({
      idx: 1, object: 'rum_event',
      metrics: ['独立用户数'],
      dimensions: ['应用名称', '应用环境'],
      timeWindow: '12M', site: '中国',
    });
    // 过滤值必须解成字典枚举的业务标签（字典行在 intent 之后才出现也要能解到），
    // 算子也不能把内部码 `eq` 直接面用户
    expect(got.scopes[0].filters).toEqual(['应用环境 = 生产']);
  });

  it('字典口径不再展示（get_metrics 只用于解枚举标签）', () => {
    // 枚举标签仍然生效（供「本次口径」过滤值解业务名）
    expect(got.scopes[0].filters).toEqual(['应用环境 = 生产']);
    // 不再有 dicts 字段
    expect((got as any).dicts).toBeUndefined();
  });

  it('字典里没登记枚举时原样给码值（不编造标签）', () => {
    const bare = collectKnowledge([assistant({
      tool_calls: [{
        tool: 'mcp__datahub_semantic__run_semantic_query',
        arguments: { intent_json: JSON.stringify({ object: 'case', dimensions: ['渠道'], filters: [{ dim: '渠道', op: 'eq', value: 'asweb' }] }) },
      }],
    })] as any);
    expect(bare.scopes[0].filters).toEqual(['渠道 = asweb']);
  });

  it('知识引用只到出处标题，正文与物理表名不进侧栏数据', () => {
    expect(got.citations).toHaveLength(1);
    expect(got.citations[0].question).toContain('联耀是什么客户');
    expect(got.citations[0].sources).toContain('业务对象 · User');
    expect(got.citations[0].chunks).toBe(1);
  });

  it('截断的结果被数出来（unparsed），不静默当成没有知识', () => {
    expect(got.unparsed).toBe(1);
  });

  it('物理标识与 SQL 一律不出现在侧栏数据里（护栏 §7）', () => {
    const blob = JSON.stringify(got);
    for (const forbidden of [
      'pc_isv_info', 'isv_code', 'target_table', 'target_column',
      't_user_customer', 'observability_rum', 'SELECT', 'prd-sls-cn',
    ]) {
      expect(blob, `侧栏数据不应包含 ${forbidden}`).not.toContain(forbidden);
    }
  });
});
