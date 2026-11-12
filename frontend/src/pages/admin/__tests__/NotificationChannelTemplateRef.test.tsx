// 通知渠道的「消息模板改为引用」回归。
//
// 反面形态（本次要修的）：渠道里一个 textarea 手填 config.message_template + 前端硬写 9 个变量，
// 而发送侧从来不读这个字段 —— 用户以为改了文案，实际发出去的是 executor 里硬编码那一句。
// 现在渠道只存引用（config.message_template_key），文案在模板中心维护；
// 引用的模板不可用、或发送走的其实是默认文案，都必须在这个页面上看得见（no-silent-degradation）。
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import NotificationChannels from '../NotificationChannels';
import client from '@/api/client';
import { toast } from '@/lib/toast';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const imTemplate = {
  id: 5, code: 'mt_im_a', name: '群通知文案', template_kind: 'im', format: 'markdown',
  subject: '', content: '状态：{{status}}', description: '', is_active: 1, is_builtin: false,
  variables: ['status'], unregistered_variables: [], used_by_channels: [], used_count: 0,
  updated_at: '2026-11-11 09:00:00',
};
const emailTemplate = {
  ...imTemplate, id: 6, code: 'mt_email_a', name: '邮件文案', template_kind: 'email',
  format: 'html', subject: '分析报告已生成（{{date}}）', content: '<h1>{{date}}</h1>',
};
const templates = [imTemplate, emailTemplate];

const channel = (over: Record<string, any> = {}) => ({
  id: 7, name: '运维群', channel_type: 'dingtalk',
  config: { webhook_url: 'https://oapi.dingtalk.com/robot/send?access_token=x', message_template: '📊 {{task_name}}' },
  is_active: true, workspace_id: 0, owner_id: 1, last_test_at: null, last_test_status: null,
  created_at: '', updated_at: '', ...over,
});

const openEditDialog = async (over: Record<string, any> = {}) => {
  (client.get as any).mockImplementation((url: string) => Promise.resolve(
    url === '/notification/channels' ? { data: [channel(over)] } : { data: templates }));
  render(<NotificationChannels />);
  await screen.findByText('运维群');
  fireEvent.click(await screen.findByRole('button', { name: '编辑' }));
  return screen.findByRole('dialog');
};

/** 弹窗里的第 2 个下拉就是消息模板（第 1 个是渠道类型） */
const templateSelect = (dialog: HTMLElement) => within(dialog).getAllByRole('combobox')[1];

beforeEach(() => {
  vi.clearAllMocks();
  (client.put as any).mockResolvedValue({ data: { success: true } });
  (client.post as any).mockResolvedValue({ data: { success: true, result: { status: 'rendered' } } });
});
afterEach(cleanup);

describe('通知渠道 · 消息模板只存引用', () => {
  it('编辑弹窗里消息模板是下拉选择，不再是手填 textarea，也不再广告发不出去的变量', async () => {
    const dialog = await openEditDialog();
    // 两个下拉：渠道类型 + 消息模板（旧形态里后者是 textarea）
    expect(within(dialog).getAllByRole('combobox')).toHaveLength(2);
    // 旧形态的判据：一个 id=msg-template 的 textarea + 一串硬编码变量 chips
    expect(within(dialog).queryByPlaceholderText(/查看完整报告: \{\{report_link\}/)).toBeNull();
    expect(within(dialog).queryByText('{{task_name}}')).toBeNull();
    expect(within(dialog).queryByText('{{result_summary}}')).toBeNull();
    // 文案正文只读展示在引用信息里（所见即所得），不可在渠道里编辑
    expect(within(dialog).queryByRole('textbox', { name: /消息模板/ })).toBeNull();
  });

  it('下拉只列当前渠道分类的模板（钉钉列 IM 文案，不列邮件文案）', async () => {
    const dialog = await openEditDialog();
    fireEvent.click(templateSelect(dialog));
    const options = await screen.findAllByRole('option');
    expect(options.map(o => o.textContent)).toEqual(['平台默认文案（不选模板）', '群通知文案']);
    expect(screen.queryByText('邮件文案')).toBeNull();
  });

  it('选中模板后保存写 message_template_key，并清掉已不再消费的内联 message_template', async () => {
    const dialog = await openEditDialog();
    // 旧内联字段是死字段：必须在页面上说清楚它不会发出去，而不是留着让人以为改了就有用
    expect(await within(dialog).findByText(/已不再消费/)).toBeInTheDocument();
    fireEvent.click(templateSelect(dialog));
    fireEvent.click(await screen.findByText('群通知文案'));
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    const [url, body] = (client.put as any).mock.calls[0];
    expect(url).toBe('/notification/channels/7');
    expect(body.config.message_template_key).toBe('mt_im_a');
    expect(body.config.message_template).toBeUndefined();
    expect(body.config.webhook_url).toContain('dingtalk');
  });

  it('引用的模板被下架/删除时显式提示“会退回默认文案”，不当成“没选”', async () => {
    const dialog = await openEditDialog({
      config: { webhook_url: 'https://x', message_template_key: 'mt_gone' },
    });
    expect(await within(dialog).findByText(/发送会退回平台默认文案/)).toBeInTheDocument();
  });

  it('测试发送按实际渲染结果说话：发的是默认文案时不能只报“发送成功”', async () => {
    (client.post as any).mockResolvedValueOnce({
      data: { success: true, result: { status: 'default', reasons: ['渠道未选择消息模板，按平台默认文案发送'] } },
    });
    render(<NotificationChannels />);
    fireEvent.click(await screen.findByRole('button', { name: /测试/ }));
    await waitFor(() => expect(toast.warning).toHaveBeenCalled());
    expect(String((toast.warning as any).mock.calls[0][0])).toContain('默认文案');
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('短信渠道列出运营商参数并说明本期是占位（不接服务商却显示能发 = 骗人）', async () => {
    const dialog = await openEditDialog();
    fireEvent.click(within(dialog).getAllByRole('combobox')[0]);
    fireEvent.click(await screen.findByRole('option', { name: /短信/ }));
    expect(await within(dialog).findByPlaceholderText('SMS_123456789')).toBeInTheDocument();
    expect(within(dialog).getByText(/服务商未配置/)).toBeInTheDocument();
    // 凭据不进渠道 config：页面上要说清只读 backend/.env
    expect(within(dialog).getByText(/只读 backend\/\.env/)).toBeInTheDocument();
  });
});
