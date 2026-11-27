// 会话侧栏派生契约（零 token：只从已落库的会话消息里摘）
// ① 产物 = 消息上的 doc / reportDraft（含归档回执）；
// ② 关键知识 = 本次口径（run_semantic_query 的 intent）+ 命中的字典口径 + 知识引用出处；
// ③ 字段白名单：术语的 target_table/target_column、目录检索的物理表名/数据源名、SQL 产物
//    一律不得进入侧栏数据（护栏 §7）；知识片段正文是用户点名要看的内容（排查知识质量/学习），
//    只落在 sources[].excerpt 里且必须显式标注截断，与执行过程同一份文本（不新增信息面）；
// ④ 结果被截断时必须数出来（unparsed），不能静默当成"没有知识"；
// ⑤ 会话技能 = 回合 meta 的三条通道（常驻/附带/取回），计"回合数"不等同"条目数"，
//    字段整份缺失是历史会话（未记录），绝不等于"没加载"。
import { describe, expect, it } from 'vitest';
import { collectArtifacts, collectKnowledge, collectSkills } from '../conversationInsights';

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
      timeWindow: '12M', sites: ['中国'],
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

  it('知识引用带正文：标题 + 知识库 + 摘录，供排查知识质量与学习', () => {
    expect(got.citations).toHaveLength(1);
    const cit = got.citations[0];
    expect(cit.question).toContain('联耀是什么客户');
    expect(cit.sources.map(s => s.title)).toContain('业务对象 · User');
    expect(cit.chunks).toBe(1);
    const src = cit.sources[0];
    expect(src.kb).toBe('');           // 该片段没带库名就不编造
    expect(src.excerpt).toContain('平台注册用户');
    expect(src.clipped).toBe(false);
    expect(cit.contentMissing).toBe(false);
  });

  it('result 被截断时回退服务端摘要：新形态带正文，旧会话只存标题也要认', () => {
    const truncated = assistant({
      tool_calls: [{
        tool: 'mcp__datahub_semantic__knowledge_search',
        result: '{"results": [{"question": "截断',
        citation_summary: [{
          question: '联耀是什么客户',
          sources: [{
            title: '业务对象 · User', knowledge_base: '平台数据字典',
            excerpt: '别名: User, 用户', excerpt_truncated: true,
          }],
        }],
      }],
    });
    const fresh = collectKnowledge([truncated] as any);
    expect(fresh.citations).toHaveLength(1);
    expect(fresh.citations[0].sources[0]).toMatchObject({
      title: '业务对象 · User', kb: '平台数据字典', excerpt: '别名: User, 用户', clipped: true,
    });
    expect(fresh.citations[0].contentMissing).toBe(false);
    // 摘要兜住了正文，就不能再报“未解析”
    expect(fresh.unparsed).toBe(0);

    // 旧会话：摘要的 sources 还是纯标题字符串
    const legacy = collectKnowledge([assistant({
      tool_calls: [{
        tool: 'mcp__datahub_semantic__knowledge_search',
        result: '{"results": [{"question": "截断',
        citation_summary: [{ question: '联耀是什么客户', sources: ['业务对象 · User'] }],
      }],
    })] as any);
    expect(legacy.citations[0].sources[0]).toMatchObject({ title: '业务对象 · User', excerpt: '' });
    // 正文没留存必须显式说出来，不能摆个点不开的展开键装作有内容
    expect(legacy.citations[0].contentMissing).toBe(true);
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

  it('跨站查询除站点外口径相同 → 合并成一张卡，站点按序列出（不按站点刷屏）', () => {
    const sites = ['中国', '新加坡', '欧洲', '英国', '日本'];
    const intent = (site: string) => ({
      object: 'app_usage_event', metrics: ['应用使用事件数'],
      filters: [{ dim: '事件形态', op: 'eq', value: 'click' }],
      time_window: '30d', site,
    });
    const got = collectKnowledge([assistant({
      tool_calls: [{
        tool: 'mcp__datahub_semantic__run_semantic_query',
        arguments: { intents_json: JSON.stringify(sites.map(intent)) },
      }],
    })] as any);
    expect(got.scopes).toHaveLength(1);
    expect(got.scopes[0].sites).toEqual(sites);
    // 跳转锚点取首次出现的那一轮（合并不改变定位口径）
    expect(got.scopes[0].idx).toBe(0);
  });

  it('只有站点差异才合并：维度/时间窗不同是不同口径，仍分两张卡', () => {
    const base = {
      object: 'app_usage_event', metrics: ['应用使用事件数'],
      filters: [{ dim: '事件形态', op: 'eq', value: 'click' }], time_window: '30d', site: '中国',
    };
    const got = collectKnowledge([assistant({
      tool_calls: [{
        tool: 'mcp__datahub_semantic__run_semantic_query',
        arguments: { intents_json: JSON.stringify([
          base,
          { ...base, dimensions: ['操作目标'] },   // 多一个分组维度 = 不同口径
          { ...base, time_window: '7d' },          // 不同窗口 = 不同口径
        ]) },
      }],
    })] as any);
    expect(got.scopes).toHaveLength(3);
    expect(got.scopes.map(s => s.sites)).toEqual([['中国'], ['中国'], ['中国']]);
  });

  it('完全相同的 intent 重复出现（同站点）只算一条，站点不重复登记', () => {
    const intent = {
      object: 'case', dimensions: ['渠道'],
      filters: [{ dim: '渠道', op: 'eq', value: 'asweb' }], site: '中国',
    };
    const got = collectKnowledge([assistant({
      tool_calls: [{
        tool: 'mcp__datahub_semantic__run_semantic_query',
        arguments: { intents_json: JSON.stringify([intent, intent, { ...intent }]) },
      }],
    })] as any);
    expect(got.scopes).toHaveLength(1);
    expect(got.scopes[0].sites).toEqual(['中国']);
  });

  it('指标/过滤顺序不同不代表口径不同（排序归一，不拆成两张卡）', () => {
    const got = collectKnowledge([assistant({
      tool_calls: [{
        tool: 'mcp__datahub_semantic__run_semantic_query',
        arguments: { intents_json: JSON.stringify([
          { object: 'case', metrics: ['工单量', '结案率'], filters: [
            { dim: '渠道', op: 'eq', value: 'asweb' }, { dim: '状态', op: 'eq', value: 'open' },
          ] },
          { object: 'case', metrics: ['结案率', '工单量'], filters: [
            { dim: '状态', op: 'eq', value: 'open' }, { dim: '渠道', op: 'eq', value: 'asweb' },
          ] },
        ]) },
      }],
    })] as any);
    expect(got.scopes).toHaveLength(1);
    expect(got.scopes[0].metrics).toEqual(['工单量', '结案率']);
    expect(got.scopes[0].filters).toEqual(['渠道 = asweb', '状态 = open']);
  });

  it('没有站点的 intent 不占站点位（站点清单只列真带站点的）', () => {
    const got = collectKnowledge([assistant({
      tool_calls: [{
        tool: 'mcp__datahub_semantic__run_semantic_query',
        arguments: { intent_json: JSON.stringify({ object: 'case', dimensions: ['渠道'] }) },
      }],
    })] as any);
    expect(got.scopes[0].sites).toEqual([]);
  });

  it('截断的结果被数出来（unparsed），不静默当成没有知识', () => {
    expect(got.unparsed).toBe(1);
  });

  it('物理标识与 SQL 一律不出现在侧栏数据里（护栏 §7）', () => {
    // 知识正文是用户点名要看的内容：只允许落在 sources[].excerpt（与执行过程同一份文本），
    // 其余字段（口径卡、标题、库名、问题）仍不得带内部标识与查询产物。
    const withoutExcerpt = JSON.stringify({
      ...got,
      citations: got.citations.map(c => ({
        ...c,
        sources: c.sources.map(s => ({ ...s, excerpt: '' })),
      })),
    });
    for (const forbidden of [
      'pc_isv_info', 'isv_code', 'target_table', 'target_column',
      't_user_customer', 'observability_rum', 'SELECT', 'prd-sls-cn',
    ]) {
      expect(withoutExcerpt, `侧栏数据（正文摘录除外）不应包含 ${forbidden}`).not.toContain(forbidden);
    }
    // 正文本身允许出现（用户展开即看，不新增信息面）
    expect(got.citations[0].sources[0].excerpt).toContain('t_user_customer');
  });
});

describe('collectSkills', () => {
  it('三条通道各自计「回合数」，同一回合内重复写同一个技能名只算一次', () => {
    const got = collectSkills([
      { role: 'user', content: '做个大屏看上周转化' } as any,
      assistant({ skills_attached: ['screen-builder'], skills_loaded: [], skills_fetched: [] }),
      assistant({
        // 同一回合里同一通道写了两次：计数口径是回合数，不是条目数
        skills_attached: ['screen-builder', 'screen-builder'],
        skills_loaded: ['screen-builder'],
        skills_fetched: ['data-analysis'],
      }),
    ]);
    expect(got.turns).toBe(2);
    expect(got.recorded).toBe(true);
    expect(got.cards.map(c => c.name)).toEqual(['screen-builder', 'data-analysis']);
    expect(got.cards[0]).toMatchObject({ idx: 1, resident: 1, attached: 2, fetched: 0 });
    expect(got.cards[1]).toMatchObject({ idx: 2, resident: 0, attached: 0, fetched: 1 });
  });

  it('记录字段整份缺失 = 历史会话（recorded=false），不得当成「没加载」', () => {
    const got = collectSkills([assistant({ content: '旧回合' })]);
    expect(got.turns).toBe(1);
    expect(got.recorded).toBe(false);
    expect(got.cards).toEqual([]);
  });

  it('字段在但都是空数组 = 确实没加载（recorded=true）', () => {
    const got = collectSkills([assistant({ skills_loaded: [], skills_attached: [], skills_fetched: [] })]);
    expect(got.recorded).toBe(true);
    expect(got.cards).toEqual([]);
  });

  it('新会话（还没有回答回合）turns=0', () => {
    expect(collectSkills([{ role: 'user', content: '你好' } as any])).toMatchObject({ turns: 0, cards: [] });
  });
});
