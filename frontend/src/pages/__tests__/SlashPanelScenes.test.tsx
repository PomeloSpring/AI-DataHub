// `/` 面板「场景」栏的三个历史缺陷（用户 2026-11-06 报障：来回切换记录会重复、Esc 无效）。
//
// 根因是契约错配：服务端把候选标识下发成 `id`，前端读的是 `s.key` → 每一项的
// React key 都变成 `scene:undefined` → 切换类别时 reconciliation 认不出节点，
// 屏幕上多出重复行且 Esc 关不掉（那些是多出来的孤儿 DOM，不是面板没关）。
// 连带还有一个静默故障：选中时 item.scene=undefined，chip 挂的是空标识、
// 请求体里 selected_scenes 变成空数组 —— 用户以为选了，实际等于没选。
//
// 本文件同时锁住两侧：正常契约下行数/关闭/上送都对；契约再坏也不得出现重复行，
// 且必须显式报错而不是假装选中。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))
// 必须 mock @/lib/toast 本身（Chat 用的是它，不是直接用 sonner），
// 否则拿到的是真包装函数，没有 mock API 可断言。
vi.mock('@/lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))
const { mockClient } = vi.hoisted(() => ({
  mockClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))
vi.mock('../../api/client', () => ({ default: mockClient }))
vi.mock('../../components/chat/Model3DViewer', () => ({ default: () => null }))
vi.mock('../../components/chat/SessionFilesPanel', () => ({ default: () => null }))
vi.mock('../../components/MarkdownWithCharts', () => ({ default: () => null }))
vi.mock('../../components/ChartPicker', () => ({ default: () => null }))
vi.mock('../../components/DashboardChart', () => ({ CHART_TYPES: [], default: () => null }))

import Chat from '../Chat'
import { useChatStore } from '../../stores/chatStore'
import { toast } from '@/lib/toast'

const SCENE1 = '官网 Web 端体验与流量分析';
const SCENE2 = '按页面 URL 或 IP 关联用户操作路径';

/** 下发 scenes 的形状由参数决定（要分别验正常契约与坏契约） */
function mockOptions(scenes: any[]) {
  mockClient.get.mockImplementation((url: string) => (
    url === '/chat/slash-options'
      ? Promise.resolve({ data: {
        skills: [{ name: 'data-analysis', display_name: '数据分析（语义层）', description: 'd' }],
        tasks: [], commands: [], modes: [], scenes, scenes_hidden: 0,
      } })
      : Promise.resolve({ data: [] })
  ));
}

async function openPanel() {
  const input = screen.getByPlaceholderText(/输入你的数据查询问题/) as HTMLInputElement;
  fireEvent.change(input, { target: { value: '/' } });
  input.selectionStart = 1;
  await screen.findByText(SCENE2);
  return input;
}

beforeEach(() => {
  vi.clearAllMocks();
  useChatStore.setState({ messages: [], currentConvId: null, loading: false, selectedWorkspaceId: 301 });
});

afterEach(() => cleanup());

describe('/ 面板场景栏', () => {
  it('来回切换类别不产生重复行（每项只出现一次）', async () => {
    mockOptions([
      { key: '1001', name: SCENE1, description: '官网流量' },
      { key: '1002', name: SCENE2, description: '操作路径' },
    ]);
    render(<MemoryRouter><Chat /></MemoryRouter>);
    const input = await openPanel();
    expect(screen.getAllByText(SCENE1)).toHaveLength(1);

    for (let i = 0; i < 4; i++) {
      fireEvent.keyDown(input, { key: 'ArrowRight' });
      fireEvent.keyDown(input, { key: 'ArrowLeft' });
    }
    expect(screen.getAllByText(SCENE1)).toHaveLength(1);
    expect(screen.getAllByText(SCENE2)).toHaveLength(1);
  });

  it('Esc 收起面板并摘掉 `/` 片段（残留会把面板再顶回来）', async () => {
    mockOptions([
      { key: '1001', name: SCENE1, description: '官网流量' },
      { key: '1002', name: SCENE2, description: '操作路径' },
    ]);
    render(<MemoryRouter><Chat /></MemoryRouter>);
    const input = await openPanel();
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByText(SCENE2)).not.toBeInTheDocument();
    expect((screen.getByPlaceholderText(/输入你的数据查询问题/) as HTMLInputElement).value).toBe('');
  });

  it('选中场景后 chip 显示业务名，发送时带 selected_scenes=[key]', async () => {
    mockOptions([
      { key: '1001', name: SCENE1, description: '官网流量' },
      { key: '1002', name: SCENE2, description: '操作路径' },
    ]);
    const sendSpy = vi.fn().mockResolvedValue(undefined);
    useChatStore.setState({ sendMessage: sendSpy } as any);
    render(<MemoryRouter><Chat /></MemoryRouter>);
    const input = await openPanel();

    fireEvent.keyDown(input, { key: 'Enter' });   // 高亮在首条 → 添加该场景
    expect(await screen.findByText(SCENE1)).toBeInTheDocument();   // 面板收起后 chip 里仍在

    fireEvent.change(input, { target: { value: '近7天怎么样' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(sendSpy).toHaveBeenCalled());
    expect(sendSpy.mock.calls[0][3]).toEqual(expect.objectContaining({ scenes: ['1001'] }));
  });

  it('本轮选了哪个场景要留在提问气泡里（否则“注入了方案但模型没按它做”无从核对）', async () => {
    mockOptions([
      { key: '1001', name: SCENE1, description: '官网流量' },
      { key: '1002', name: SCENE2, description: '操作路径' },
    ]);
    // 场景方案只注入提示词、不进答案；不在轮次上记一笔，回放时就只能对着空问句猜
    useChatStore.setState({
      messages: [
        { role: 'user', content: '查询近3月的', scenes: ['1002'] },
        { role: 'assistant', content: '…' },
      ],
    } as any);
    render(<MemoryRouter><Chat /></MemoryRouter>);
    // 显示业务名（不裸显 id，也不回退成“分析场景”占位）
    expect(await screen.findByText(SCENE2)).toBeInTheDocument();
    expect(screen.queryByText('1002')).not.toBeInTheDocument();
  });

  it('契约再坏（下发项没有 key）也不得出现重复行，且选中被显式拒而不是假装选上', async () => {
    // 这正是历史缺陷的现场：服务端字段叫 id，前端读 s.key=undefined
    mockOptions([
      { id: '1001', name: SCENE1, description: '官网流量' },
      { id: '1002', name: SCENE2, description: '操作路径' },
    ]);
    render(<MemoryRouter><Chat /></MemoryRouter>);
    const input = await openPanel();
    for (let i = 0; i < 4; i++) {
      fireEvent.keyDown(input, { key: 'ArrowRight' });
      fireEvent.keyDown(input, { key: 'ArrowLeft' });
    }
    expect(screen.getAllByText(SCENE1)).toHaveLength(1);

    vi.mocked(toast.error).mockClear();
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('缺少场景标识')));
  });
});
