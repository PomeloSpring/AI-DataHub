// 「用最新数据重新生成」的在线直播：过程要看得见，离开就要终止（用户实拍三点：
// 只有“生成中”、看不到进度、关掉页面它照跑）。
// 口径：事件由服务端一处产出（stage/capacity/data/tool_start/tool_result/text/done），
// 本页只翻译成人话；结局（ready/degraded/cancelled/failed）一律用 done 事件里的字，不自己判。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ReportsCenter from '../ReportsCenter';

const TITLE = '全国页面访问统计';
const BODY = '## 核心发现\n\n近 7 天 PV 1,234,567';

const v1 = {
  id: 843, task_id: null, log_id: null, title: TITLE, format: 'markdown',
  access_mode: 'private', view_count: 3, created_at: '2026-11-10T16:49:19',
  generation_status: 'ready', source_type: 'conversation', workspace_id: 3,
  series_key: 'report:843',
  origin: { channel: 'chat', question: '查访问 ip 归属地分布' },
  refresh: { available: true, channel: 'report', action_label: '用最新数据重新生成', hint: '' },
};
const v2 = { ...v1, id: 844, source_type: 'analysis', view_count: 0,
  created_at: '2026-11-11T09:00:00', refresh: { ...v1.refresh } };

// 桩：事件由用例给；发完后停在 gate 上，让用例能看到“进行中”的页面（不被收尾一次刷掉）
const h = vi.hoisted(() => {
  const state: any = { events: [], calls: [], list: [] as any[], aborted: [] as any[] };
  state.gate = new Promise<void>(resolve => { state.release = resolve; });
  return state;
});

vi.mock('@/api/report', () => ({
  listReports: vi.fn(() => Promise.resolve(h.list)),
  getReportDetail: vi.fn((id: number) => Promise.resolve(
    { ...(id === v2.id ? v2 : v1), content_url: `/c/${id}`, content_size: BODY.length })),
  fetchReportContent: vi.fn(() => Promise.resolve(BODY)),
  updateReport: vi.fn(), deleteReport: vi.fn(), saveReportAsTemplate: vi.fn(),
  createTaskFromReport: vi.fn(),
  refreshReportStream: vi.fn(async (id: number, handlers: any) => {
    h.calls.push([id, handlers]);
    handlers.signal.addEventListener('abort', () => h.aborted.push(id));
    for (const event of h.events) {
      handlers.onEvent(event);
      await new Promise(resolve => setTimeout(resolve, 0));
    }
    await h.gate;
  }),
  isEditableReport: (r: any) => ['manual', 'conversation'].includes(r.source_type),
  isDeletableReport: (r: any) => ['manual', 'conversation', 'legacy', 'analysis'].includes(r.source_type),
}));
vi.mock('@/api/scheduledTask', () => ({ triggerScheduledTask: vi.fn() }));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
vi.mock('@/components/ReportContent', () => ({
  default: ({ content }: { content: string }) => <div data-testid="body">{content}</div>,
}));
vi.mock('@/components/ReportTaskDialog', () => ({ default: () => null }));

import { refreshReportStream, listReports, getReportDetail } from '@/api/report';
import { toast } from '@/lib/toast';

const PROGRESS = [
  { type: 'stage', step: 'prepared', report_id: 844,
    message: '已在本报告下建好新版本（沿用原版式），正在排队…' },
  { type: 'capacity', event: 'queued', position: 2, limit: 6, estimated_wait_s: 8.5 },
  { type: 'tool_start', tool_call_id: 't1', call_index: 1,
    tool: 'mcp__datahub_x__run_semantic_query' },
  { type: 'data', query_count: 1, row_count: 12, truncated: false, source: 'run_semantic_query' },
  { type: 'tool_result', tool_call_id: 't1', call_index: 1, ok: true, elapsed: 2.34,
    tool: 'mcp__datahub_x__run_semantic_query' },
  { type: 'text', chars: 512 },
];

const startRefresh = async () => {
  fireEvent.click(await screen.findByRole('button', { name: /用最新数据重新生成/ }));
  return waitFor(() => expect(refreshReportStream).toHaveBeenCalled());
};

beforeEach(() => {
  vi.clearAllMocks();
  h.events = [];
  h.calls = [];
  h.list = [v1];
  h.aborted = [];
  h.gate = new Promise<void>(resolve => { h.release = resolve; });
});
afterEach(() => { cleanup(); h.release(); });

describe('报告重跑的在线直播', () => {
  it('过程面板把取数、工具步、写正文逐条显示出来（不再是“生成中”三个字）', async () => {
    h.events = PROGRESS;
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
    await startRefresh();

    // 事件是一拍一条发上来的：等到最后一条到位再逐项断言，不然只看得见前几行
    const panel = await waitFor(() => {
      const el = screen.getByTestId('refresh-panel');
      expect(el).toHaveTextContent('模型正在写正文（已 512 字）');
      return el;
    });
    expect(panel).toHaveTextContent('已在本报告下建好新版本');
    expect(panel).toHaveTextContent('第 2 位');           // 排队位置由服务端给
    expect(panel).toHaveTextContent('取数 #1');
    expect(panel).toHaveTextContent('12 行');
    expect(panel).toHaveTextContent('语义取数 完成 · 2.3s');   // 工具名人话 + 耗时
    // 直播期间旧正文照读（新版本只在跑完那一瞬才切过去）
    expect(screen.getByTestId('body')).toHaveTextContent('1,234,567');
    // 进行中不许再点一次（并发跑两轮会把这一版正文写成半截）
    expect(screen.getByRole('button', { name: /重新生成中/ })).toBeDisabled();
    h.release();
  });

  it('完成后按服务端给的结局收尾：切到新版本，成功文案只看 done 里的字', async () => {
    h.events = [...PROGRESS, { type: 'done', generation_status: 'ready',
      layout_result: { status: 'preserved', format: 'markdown', reasons: [] } }];
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
    await startRefresh();
    // 新版本只有跑完才进清单（服务端 append 一行后重拉），此时才轮到它被选中
    h.list = [v2, v1];
    h.release();

    await waitFor(() => expect(screen.queryByTestId('refresh-panel')).toBeNull());
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('新版本'));
    expect(toast.error).not.toHaveBeenCalled();
    await waitFor(() => expect(
      (screen.getByTitle('版本管理') as HTMLSelectElement).value).toBe('844'));
  });

  it('证据不全那一版不得说成成功：结局照 done 里那句原因说', async () => {
    h.events = [{ type: 'stage', step: 'prepared', report_id: 844, message: '已建好新版本' },
      { type: 'done', generation_status: 'degraded',
        reason: '本轮没有已登记的受治理取数结果' }];
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
    await startRefresh();
    h.release();

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining('本轮没有已登记的受治理取数结果')));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('点「停止」就断开：本轮中止并记为已取消，不冒充成平台故障', async () => {
    h.events = [PROGRESS[0], PROGRESS[3]];
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
    await startRefresh();
    await waitFor(() => expect(screen.getByTestId('refresh-panel')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /停止/ }));
    await waitFor(() => expect(h.aborted).toEqual([843]));
    await waitFor(() => expect(screen.queryByTestId('refresh-panel')).toBeNull());
    expect(toast.info).toHaveBeenCalledWith(expect.stringContaining('已停止本次重新生成'));
    // 用户自己停的不算失败：不得再补一句“重新生成失败”
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('切到别的版本 = 离开这一轮：同样断开，面板不留残影', async () => {
    h.events = [PROGRESS[0]];
    h.list = [v2, v1];            // 真的有两个版本才能“切到别的版本”（单版本不给版本下拉）
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
    await startRefresh();
    await waitFor(() => expect(screen.getByTestId('refresh-panel')).toBeInTheDocument());

    const picker = (await waitFor(() => screen.getByTitle('版本管理'))) as HTMLSelectElement;
    fireEvent.change(picker, { target: { value: String(v1.id) } });
    await waitFor(() => expect(h.aborted).toEqual([844]));
    await waitFor(() => expect(screen.queryByTestId('refresh-panel')).toBeNull());
  });

  it('流断了但没收到结局：如实说“连接中断”并重拉，不猜一个成功', async () => {
    h.events = [PROGRESS[0]];              // 没有 done 就收尾
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
    await startRefresh();
    h.release();

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining('连接中断')));
    expect(toast.success).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByTestId('refresh-panel')).toBeNull());
  });

  it('服务端说不可重跑（没登记可复跑问题）：按钮置灰并给原因，不会发起流', async () => {
    const blocked = {
      ...v1,
      refresh: { available: false, channel: '', action_label: '',
                 hint: '该报告未登记可复跑的原始问题，请在智能问数里重新提问后另存为报告' },
    };
    vi.mocked(listReports).mockResolvedValue([blocked] as any);
    // 重跑可用性跟着详情走（服务端一处判，前端不吃清单里那份副本）
    vi.mocked(getReportDetail).mockResolvedValue(
      { ...blocked, content_url: '/c/843', content_size: BODY.length } as any);
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);

    const btn = await waitFor(() => screen.getByRole('button', { name: /用最新数据重新生成/ }));
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('title', expect.stringContaining('未登记可复跑的原始问题'));
    fireEvent.click(btn);            // 置灰就是点不动（旧写法看着能点，点了才弹一句错误）
    expect(refreshReportStream).not.toHaveBeenCalled();
  });
});
