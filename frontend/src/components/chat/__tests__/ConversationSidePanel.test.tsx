// 会话右侧栏契约：默认收起（不渲染）；产物区分「未归档草稿」与「已归档报告」两种事实，
// 已归档不再给重复归档入口；关键知识只显示业务名；结果截断必须显式提示而不是显示空小节。
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

  it('结果被截断时显式提示条数，不静默当成没有知识', () => {
    renderPanel();
    const warn = screen.getByRole('alert');
    expect(warn.textContent).toContain('1 段工具返回过长被截断');
  });

  it('点条目上的定位按钮 → 交给页面同一个跳转口径（不自己滚）', async () => {
    const props = renderPanel();
    await userEvent.click(screen.getAllByRole('button', { name: /^定位到/ })[0]);
    await waitFor(() => expect(props.onJumpToMessage).toHaveBeenCalledWith(1));
  });

  it('没有产物也没有知识时给引导文案，而不是空白块', () => {
    renderPanel({ messages: [{ role: 'user', content: '你好' } as ChatMessage] });
    expect(screen.getByText(/还没有报告草稿/)).toBeInTheDocument();
    expect(screen.getByText(/还没有可摘取的知识/)).toBeInTheDocument();
  });
});
