// 模板中心（报告模板 + 消息模板）的形态回归。
//
// 锁住四件历史反面：
// * 页面是表格，不是卡片网格 —— 版式/主题/被谁引用一列一件事；
// * 读写只走报告域 `/reports/templates*`：这页过去为了拿主题清单去调 /api/vis-library，
//   把「模板中心」的可见性绑到了 vis:* 权限上（权限漂移）；
// * 消息模板的可用变量来自服务端 `/message-templates/variables`：旧 UI 自己硬写 9 个变量，
//   其中 task_name/result_summary 属业务内容，按护栏根本发不出去；
// * 无约束/不完整/用不了变量的状态必须显式看得见，不静默丢弃。
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TemplateCenter from '../ReportTemplates';
import client from '@/api/client';
import { toast } from '@/lib/toast';
import { useAuthStore } from '@/stores/authStore';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
// 预览必须是报告中心那个渲染组件（与真实渲染同源），这里替身保留入参可断言
vi.mock('@/components/ReportContent', () => ({
  default: ({ content, format }: { content: string; format: string }) => (
    <div data-testid="preview" data-format={format}>{content}</div>
  ),
}));

const REPORT_ROWS = [{
  id: '149', code: 'rpt_1', name: '日报模板', format: 'markdown', theme: 'finance',
  description: '区域运营同款日报', is_builtin: true, is_active: 1,
  layout_registered: true, has_layout: true,
  headings: ['核心结论', '分省明细'],
  charts: [{ title: '各省 PV', chart_type: 'bar', columns: ['省份', 'PV'] }],
  content: '## 核心结论\n', content_preview: '## 核心结论', used_by_tasks: 3,
  updated_at: '2026-11-11 10:00:00',
}, {
  // 旧模板：只有正文可抽 → 摘要里要写明来源；正文与版式都没有 → 直说不会起作用
  id: '150', code: 'rpt_2', name: '旧周报模板', format: 'markdown', theme: '',
  description: '', is_builtin: false, is_active: 1,
  layout_registered: false, has_layout: true, headings: ['本周概览'], charts: [],
  content: '## 本周概览\n', content_preview: '', used_by_tasks: 0, updated_at: '',
}, {
  id: '151', code: 'rpt_3', name: '空模板', format: 'markdown', theme: '',
  description: '', is_builtin: false, is_active: 0,
  layout_registered: true, has_layout: false, headings: [], charts: [],
  content: '', content_preview: '', used_by_tasks: 0, updated_at: '',
}];

const MT_META = {
  variables: [
    { name: 'date', label: '执行日期', sample: '2026-11-11' },
    { name: 'time', label: '执行时间', sample: '09:30' },
    { name: 'status', label: '执行结果', sample: 'success' },
    { name: 'total', label: '分析项总数', sample: '3' },
    { name: 'succeeded', label: '成功数', sample: '2' },
    { name: 'failed', label: '失败数', sample: '1' },
    { name: 'report_link', label: '报告链接（需登录）', sample: 'https://x/reports/1' },
  ],
  kinds: [{ value: 'im', label: '群机器人 / Webhook' }],
  formats: { im: ['text', 'markdown'], email: ['text', 'markdown', 'html'], sms: ['text'] },
  sms_segment_chars: 70,
  excluded_note: '任务名、结果摘要不在可用变量里（安全护栏）。',
};

const MT_ROWS = [{
  id: 5, code: 'mt_channel_7', name: '运维群文案', template_kind: 'im', format: 'text',
  subject: '', content: '状态 {{status}}，摘要 {{result_summary}}', description: '',
  is_active: 1, is_builtin: false,
  variables: ['status'], unregistered_variables: ['result_summary'],
  used_by_channels: [{ id: 7, name: '运维群机器人' }], used_count: 1,
  updated_at: '2026-11-11 09:00:00',
}];

/** 按 URL 分流的假 axios：断言"走哪个域"本身就是本次的回归点 */
function route(url: string, config?: any) {
  if (url === '/reports/templates') return { data: REPORT_ROWS };
  if (url === '/reports/templates/theme-options') return { data: [{ code: 'tp_finance', theme: 'finance', name: '财务风' }] };
  if (url === '/message-templates/variables') return { data: MT_META };
  if (url === '/message-templates') {
    const kind = config?.params?.kind || '';
    return { data: kind === 'im' ? MT_ROWS : kind ? [] : MT_ROWS };
  }
  return { data: [] };
}

const openCategory = async (name: RegExp) => {
  render(<TemplateCenter />);
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  await user.click(await screen.findByRole('tab', { name }));
};

beforeEach(() => {
  vi.clearAllMocks();
  (client.get as any).mockImplementation((url: string, config?: any) => Promise.resolve(route(url, config)));
  (client.post as any).mockImplementation((url: string) => Promise.resolve(
    url === '/reports/templates/analyze'
      ? { data: { format: 'markdown', has_layout: true, layout: { format: 'markdown', headings: ['报告标题', '核心结论', '分省明细'], charts: [{ title: '各省 PV', chart_type: 'bar', columns: ['省份', 'PV'] }] }, skeleton: '# 报告标题\n\n```chart\n{"rows":[["示例甲",1]]}\n```' } }
      : url === '/message-templates/preview'
        ? { data: { subject: '', content: '状态 success，摘要 {{result_summary}}', unknown_variables: ['result_summary'], variables: ['status'], char_count: 24, note: '预览取示例值' } }
        : { data: { id: 77 } },
  ));
  (client.put as any).mockResolvedValue({ data: { success: true, warnings: [] } });
  (client.delete as any).mockResolvedValue({ data: { success: true } });
  useAuthStore.setState({ user: { id: 1, username: 'admin', role: 'admin' } as any });
});
afterEach(() => { cleanup(); useAuthStore.getState().logout?.(); });

describe('模板中心 · 报告模板表格', () => {
  it('表格列出版式摘要、主题业务名与被任务引用数（不再用卡片、不再为拿主题读字模库）', async () => {
    render(<TemplateCenter />);
    const row = (await screen.findByRole('row', { name: /日报模板/ })) as HTMLTableRowElement;
    expect(within(row).getByText('2 个章节 · 1 个图表')).toBeInTheDocument();
    expect(within(row).getByText('财务风')).toBeInTheDocument();
    // 主题内部名 finance 只作数据，主面必须是业务名
    expect(within(row).queryByText('finance')).toBeNull();
    expect(within(row).getByText('3 个任务')).toBeInTheDocument();

    const legacy = within(await screen.findByRole('row', { name: /旧周报模板/ }))
      .getByText('1 个章节（从正文抽取）');
    expect(legacy).toBeInTheDocument();
  });

  it('既没版式也没正文 → 显式标「不会起作用」，不靠用户点开才发现是空模板', async () => {
    render(<TemplateCenter />);
    const row = await screen.findByRole('row', { name: /空模板/ });
    expect(within(row as HTMLElement).getByText('未登记版式（不会起作用）')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('已下架')).toBeInTheDocument();
  });

  it('保存走报告域（/reports/templates），payload 是版式契约', async () => {
    render(<TemplateCenter />);
    fireEvent.click(await screen.findByRole('button', { name: /新建模板/ }));
    fireEvent.change(screen.getByPlaceholderText('如：周报版式模板'), { target: { value: '周报版式' } });
    fireEvent.click(screen.getByRole('button', { name: /从正文抽版式/ }));
    fireEvent.click(screen.getByRole('button', { name: '创建' }));
    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/reports/templates', expect.anything()));
    const body = (client.post as any).mock.calls
      .find((c: any[]) => c[0] === '/reports/templates')[1];
    expect(body.layout.headings).toEqual(['报告标题', '核心结论', '分省明细']);
    expect(body.layout.charts[0]).toEqual({ title: '各省 PV', chart_type: 'bar', columns: ['省份', 'PV'] });
    expect(body.is_builtin).toBe(false);
    // 关键漂移点：这页不再读 /vis-library（菜单勾了模板中心就该配得完）
    expect((client.get as any).mock.calls.some((c: any[]) => String(c[0]).startsWith('/vis-library'))).toBe(false);
  });

  it('图表规格不完整时挡住保存并说明原因（不得静默丢条目）', async () => {
    render(<TemplateCenter />);
    fireEvent.click(await screen.findByRole('button', { name: /新建模板/ }));
    fireEvent.change(screen.getByPlaceholderText('如：周报版式模板'), { target: { value: '半成品' } });
    fireEvent.click(screen.getByRole('button', { name: /加图表/ }));
    fireEvent.click(screen.getByRole('button', { name: '创建' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(String((toast.error as any).mock.calls.at(-1)[0])).toContain('不完整');
    expect(client.post).not.toHaveBeenCalledWith('/reports/templates', expect.anything());
  });

  it('被任务引用的模板下架被服务端拒时，把原因原样端给用户', async () => {
    (client.delete as any).mockRejectedValueOnce({ response: { data: { detail: '该模板正被 3 个定时任务引用' } } });
    render(<TemplateCenter />);
    const row = await screen.findByRole('row', { name: /日报模板/ });
    fireEvent.click(within(row as HTMLElement).getByRole('button', { name: '下架' }));
    fireEvent.click(await within(screen.getByRole('dialog')).findByRole('button', { name: '确认下架' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('该模板正被 3 个定时任务引用'));
  });

  it('预览把当前/锁定主题带给服务端（HTML 骨架在隔离 iframe 里渲染，不带主题就没样式）', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    render(<TemplateCenter />);
    fireEvent.click(await screen.findByRole('button', { name: /新建模板/ }));
    await user.click(screen.getByRole('tab', { name: /预览/ }));
    const preview = await waitFor(() => screen.getByTestId('preview'));
    expect(preview.textContent).toContain('示例甲');
    const payload = (client.post as any).mock.calls
      .find((c: any[]) => c[0] === '/reports/templates/analyze')[1];
    expect(typeof payload.theme).toBe('string');
    expect(payload.theme.length).toBeGreaterThan(0);
  });
});

describe('模板中心 · 消息模板', () => {
  it('变量 chips 来自服务端清单，广告过的业务内容变量不再出现', async () => {
    await openCategory(/群机器人/);
    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/message-templates/variables'));
    fireEvent.click(await screen.findByRole('button', { name: /新建/ }));
    const chips = await screen.findAllByText(/\{\{status\}\}/);
    expect(chips.length).toBeGreaterThan(0);
    expect(screen.queryByText(/\{\{task_name\}\}/)).toBeNull();
    expect(screen.queryByText(/\{\{result_summary\}\}/)).toBeNull();
  });

  it('表格列出用到变量、发不出去的变量与被渠道引用数', async () => {
    await openCategory(/群机器人/);
    const row = await screen.findByRole('row', { name: /运维群文案/ });
    expect(within(row as HTMLElement).getByText('1 个用不了')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('1 个渠道')).toBeInTheDocument();
  });

  it('保存按当前分类落 template_kind，预览走服务端同一渲染函数并报出用不了的变量', async () => {
    await openCategory(/群机器人/);
    fireEvent.click(await screen.findByRole('button', { name: /新建/ }));
    fireEvent.change(screen.getByPlaceholderText('如：任务完成通知（运维群）'), { target: { value: '完成通知' } });
    fireEvent.change(screen.getByPlaceholderText(/📅 \{\{date\}\} \{\{time\}\}/), { target: { value: '状态：{{status}}' } });
    fireEvent.click(screen.getByRole('button', { name: /按示例值预览/ }));
    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/message-templates/preview', expect.anything()));
    expect(await screen.findByText(/发不出去，收件人会看到字面量/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '创建' }));
    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/message-templates', expect.anything()));
    const body = (client.post as any).mock.calls
      .find((c: any[]) => c[0] === '/message-templates')[1];
    expect(body.template_kind).toBe('im');
    expect(body.name).toBe('完成通知');
  });

  it('短信分类只给纯文本格式（越级格式在选项里就不出现）', async () => {
    await openCategory(/短信/);
    fireEvent.click(await screen.findByRole('button', { name: /新建/ }));
    const dialog = await screen.findByRole('dialog');
    // 格式下拉在弹窗里：选项由服务端 formats.sms 白名单给出，只有纯文本
    // （Radix Select 的列表 portal 到 body，所以选项用 screen 而不是 within(dialog)）
    const trigger = within(dialog).getAllByRole('combobox')[0];
    fireEvent.click(trigger);
    const options = await screen.findAllByRole('option');
    expect(options.map(o => o.textContent)).toEqual(['纯文本']);
  });
});
