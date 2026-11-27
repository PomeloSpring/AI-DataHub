// 会话右侧栏契约：默认收起（不渲染）；产物区分「未归档草稿」与「已归档报告」两种事实，
// 已归档不再给重复归档入口；会话技能只报回合 meta 的装载事实（三条通道）且显示业务名；
// 关键知识只显示业务名；侧栏只列引用，不展示「工具返回被截断」这类过程提示。
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ConversationSidePanel from '../ConversationSidePanel';
import type { ChatMessage } from '@/stores/chatStore';

// 文件清单自己打接口，本用例只验侧栏结构与动作接线
vi.mock('../SessionFilesPanel', () => ({ default: () => <div>会话文件区</div> }));

const draft = (over: Record<string, any> = {}) => ({
  title: '各应用独立用户数', markdown: '## 正文',
  created_at: '2026-11-06 15:00:00', updated_at: '2026-11-06 15:12:00', revisions: 1,
  ...over,
});

const knowledgeMessages = [
  { role: 'user', content: '查一下最近 12 个月各应用的独立用户数' } as ChatMessage,
  {
    role: 'assistant', content: '结论', reply: '结论',
    reportDraft: draft(),
    tool_calls: [
      {
        tool: 'mcp__datahub_semantic__run_semantic_query',
        arguments: {
          intents_json: JSON.stringify([{
            object: 'rum_event', metrics: ['独立用户数'], dimensions: ['应用环境'],
            filters: [{ dim: '应用环境', op: 'eq', value: 'prod' }], time_window: '12M', site: '中国',
          }]),
        },
        result: '{"results": []}',
      },
      {
        tool: 'mcp__datahub_semantic__get_metrics',
        result: JSON.stringify({
          objects: [{
            object: 'rum_event',
            metrics: [{ name: '独立用户数', unit: '人', description: '按用户去重' }],
            dimensions: [{ name: '应用环境', description: '部署环境', enum: { prod: '生产' } }],
          }],
        }),
      },
      // 半截 JSON：必须被数出来并提示，不能当成“没有知识”
      { tool: 'mcp__datahub_semantic__knowledge_search', result: '{"results": [{"question": "截断' },
    ],
  } as ChatMessage,
];

const renderPanel = (over: Record<string, any> = {}) => {
  const props = {
    open: true,
    onToggle: vi.fn(),
    workspaceId: 7,
    conversationId: 1,
    messages: knowledgeMessages,
    onJumpToMessage: vi.fn(),
    onOpenDoc: vi.fn(),
    onEditDraft: vi.fn(),
    onArchiveDraft: vi.fn(),
    archivingIdx: null,
    ...over,
  };
  render(<ConversationSidePanel {...(props as any)} />);
  return props;
};

describe('ConversationSidePanel', () => {
  it('收起时不渲染（默认态不占正文宽度）', () => {
    renderPanel({ open: false });
    expect(screen.queryByLabelText('会话侧栏')).toBeNull();
  });

  it('未归档草稿给编辑与归档入口，并说明归档前只属于本会话', () => {
    const props = renderPanel();
    expect(screen.getByText('各应用独立用户数')).toBeInTheDocument();
    expect(screen.queryByText(/已归档进报告中心/)).toBeNull();
    expect(screen.getByRole('button', { name: '编辑草稿' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '归档' })).toBeInTheDocument();
    expect(screen.getByText('会话文件区')).toBeInTheDocument();
    expect(props.conversationId).toBe(1);
  });

  it('已归档的草稿不再给重复归档入口，改为去报告中心查看', () => {
    renderPanel({
      messages: [{
        role: 'assistant', content: '结论',
        reportDraft: draft({ archived: { report_id: 9, title: '各应用独立用户数', archived_at: '2026-11-06 15:20:00' } }),
      } as ChatMessage],
    });
    expect(screen.getByText('已归档进报告中心')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '归档' })).toBeNull();
    expect(screen.getByRole('link', { name: '去查看' })).toHaveAttribute('href', '/report/9');
  });

  it('关键知识显示业务名：口径卡与过滤值的枚举标签', () => {
    renderPanel();
    expect(screen.getByText('rum_event')).toBeInTheDocument();
    // 指标在「本次口径」中显示业务名
    expect(screen.getAllByText('独立用户数').length).toBeGreaterThan(0);
    // 码值 prod 不面用户，显示字典里的「生产」
    expect(screen.getByText(/应用环境 = 生产/)).toBeInTheDocument();
    expect(screen.queryByText(/prod/)).toBeNull();
  });

  it('知识引用可展开查看正文（排查知识质量/学习），库名与截断都标出来', async () => {
    renderPanel({
      messages: [{
        role: 'assistant', content: '结论',
        tool_calls: [{
          tool: 'mcp__datahub_semantic__knowledge_search',
          result: JSON.stringify({
            question: '联耀是什么客户',
            chunks: [{
              title: '业务对象 · User', knowledge_base: '平台数据字典',
              content: '别名: User, 用户\n描述: 平台注册用户。',
            }],
          }),
        }],
      } as ChatMessage],
    });
    expect(screen.getByText('业务对象 · User')).toBeInTheDocument();
    // 默认收起：正文不平铺（侧栏只放关系主干，点开才看明细）
    expect(screen.queryByText(/平台注册用户/)).toBeNull();
    await userEvent.click(screen.getByTitle('查看「业务对象 · User」的命中内容'));
    expect(screen.getByText(/平台注册用户/)).toBeInTheDocument();
    expect(screen.getByText('来自 平台数据字典')).toBeInTheDocument();
    await userEvent.click(screen.getByTitle('收起「业务对象 · User」的命中内容'));
    expect(screen.queryByText(/平台注册用户/)).toBeNull();
  });

  it('旧会话只存了标题：正文未留存要显式说明，不给点不开的展开键', () => {
    renderPanel({
      messages: [{
        role: 'assistant', content: '结论',
        tool_calls: [{
          tool: 'mcp__datahub_semantic__knowledge_search',
          result: '{"results": [{"question": "截断',
          citation_summary: [{ question: '联耀是什么客户', sources: ['业务对象 · User'] }],
        }],
      } as ChatMessage],
    });
    expect(screen.getByText('正文未随会话留存（该轮检索结果超长被截断）· 原文见该轮执行过程')).toBeInTheDocument();
    expect(screen.queryByTitle(/查看「/)).toBeNull();
  });

  it('跨站 fan-out 的同款口径只摆一张卡，站点并列（超 3 站收成「等共 N 站」）', () => {
    const sites = ['中国', '新加坡', '欧洲', '英国', '日本'];
    renderPanel({
      messages: [{
        role: 'assistant', content: '结论',
        tool_calls: [{
          tool: 'mcp__datahub_semantic__run_semantic_query',
          arguments: { intents_json: JSON.stringify(sites.map(site => ({
            object: 'app_usage_event', metrics: ['应用使用事件数'],
            filters: [{ dim: '事件形态', op: 'eq', value: 'click' }],
            time_window: '30d', site,
          }))) },
        }],
      } as ChatMessage],
    });
    // 5 个站点 = 5 条 intent，但口径只有一份 → 不再 5 张同款卡刷屏
    expect(screen.getAllByText('app_usage_event')).toHaveLength(1);
    expect(screen.getByText(/窗口 30d · 站点 中国\/新加坡\/欧洲 等共 5 站/)).toBeInTheDocument();
    // 完整站点清单不丢（title），对象名不被 meta 挤掉
    expect(screen.getByTitle('窗口 30d · 站点 中国/新加坡/欧洲/英国/日本')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /^定位到/ })).toHaveLength(1);
  });

  it('工具返回被截断时侧栏不出提示（只列引用，详情看该轮执行过程）', () => {
    renderPanel();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByText(/工具返回过长被截断/)).toBeNull();
    // 引用与口径照常呈现
    expect(screen.getByText('rum_event')).toBeInTheDocument();
  });

  it('点条目上的定位按钮 → 交给页面同一个跳转口径（不自己滚）', async () => {
    const props = renderPanel();
    await userEvent.click(screen.getAllByRole('button', { name: /^定位到/ })[0]);
    await waitFor(() => expect(props.onJumpToMessage).toHaveBeenCalledWith(1));
  });

  it('会话技能：业务名 + 三条通道回合数 + 定位到首次加载的那一轮', async () => {
    const props = renderPanel({
      messages: [
        { role: 'user', content: '做个大屏看上周转化' } as ChatMessage,
        { role: 'assistant', content: '好', skills_attached: ['screen-builder'] } as ChatMessage,
        {
          role: 'assistant', content: '再补一版',
          skills_loaded: ['screen-builder'], skills_fetched: ['data-analysis'],
        } as ChatMessage,
      ],
      skillDisplayNames: { 'screen-builder': '数据大屏搭建' },
    });
    expect(screen.getByText('数据大屏搭建')).toBeInTheDocument();
    // 技术名不面用户（候选清单没给业务名的才退回技术名，不编造）
    expect(screen.queryByText('screen-builder')).toBeNull();
    expect(screen.getByText('常驻 1 · 附带 1 · 取回 0')).toBeInTheDocument();
    expect(screen.getByText('2 轮')).toBeInTheDocument();
    expect(screen.getByText('data-analysis')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '定位到首次加载「数据大屏搭建」的一轮' }));
    await waitFor(() => expect(props.onJumpToMessage).toHaveBeenCalledWith(1));
  });

  it('历史会话（整份缺装载字段）说「未记录」，不说「没加载」', () => {
    renderPanel({ messages: [{ role: 'assistant', content: '旧回答' } as ChatMessage] });
    expect(screen.getByText(/早于「技能加载记录」上线/)).toBeInTheDocument();
    expect(screen.queryByText(/还没有加载过技能细则/)).toBeNull();
  });

  it('有记录但一条技能都没加载 → 明确说没加载，不与历史会话混说', () => {
    renderPanel({
      messages: [{
        role: 'assistant', content: '答',
        skills_loaded: [], skills_attached: [], skills_fetched: [],
      } as ChatMessage],
    });
    expect(screen.getByText(/还没有加载过技能细则/)).toBeInTheDocument();
  });

  it('没有产物也没有知识时给引导文案，而不是空白块', () => {
    renderPanel({ messages: [{ role: 'user', content: '你好' } as ChatMessage] });
    expect(screen.getByText(/还没有报告草稿/)).toBeInTheDocument();
    expect(screen.getByText(/还没有可摘取的知识/)).toBeInTheDocument();
    // 还没有回答回合（新会话）：技能小节给“发送后就会列出”，不报“未记录”
    expect(screen.getByText(/发送消息后，这里会列出/)).toBeInTheDocument();
  });
});
