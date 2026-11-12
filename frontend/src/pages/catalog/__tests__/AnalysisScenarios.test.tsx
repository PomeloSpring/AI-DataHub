// 分析场景管理页的三条硬口径（用户 2026-11-06 拍板）：
//  1. 不用卡片式，改分页表格；**触发词与查询步骤正文不进清单**（两三条场景的正文平铺
//     就把清单撑成整屏，反而看不清有哪些场景）；
//  2. 场景必须显式声明主体范围：要么选主体，要么勾「不限主体」——没声明的场景对所有
//     角色都不会命中，所以保存前先拦一层，并把服务端的不可见原因当场显示出来；
//  3. 主体从清单选（显示业务名），不让人手输 object_key（ui-resource-display §2）。
// 历史缺陷：旧版把三条写死的示例问句当"场景"候选铺在首页，且清单把正文全铺出来。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'

// mock 句柄必须走 vi.hoisted：vi.mock 工厂会被提到文件顶部，直接引用
// 下面声明的 `const mockClient` 会报 "Cannot access before initialization"。
const { mockClient } = vi.hoisted(() => ({
  mockClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
vi.mock('@/api/client', () => ({ default: mockClient }))
vi.mock('@/lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

import AnalysisScenarios from '../AnalysisScenarios'
import { toast } from '@/lib/toast'

const CONTENT = '## 适用问题（官网 / H5 的 Web 端体验与流量）\n这段正文只该在编辑弹窗里看到。';

const ROW = {
  id: 1001, workspace_id: 0, datasource_id: 0, knowledge_type: 'quick_path',
  title: '官网 Web 端体验与流量分析', content: CONTENT,
  metadata: { triggers: ['官网', 'UV'], scenario: '官网流量', subjects: ['rum_event'], subject_all: false },
  related_tables: 'rum_event', priority: 60, usage_count: 3, positive_count: 0, negative_count: 0,
  is_active: 1, created_at: '2026-11-06 10:00:00', updated_at: '2026-11-06 10:54:33',
  subject_keys: ['rum_event'], subject_names: ['Web 端 RUM 观测事件（官网/H5）'],
  subject_all: false, visible_to_me: true, visibility_note: '',
};

const SUBJECTS = { items: [
  { key: 'rum_event', name: 'Web 端 RUM 观测事件（官网/H5）', model_name: 'ARMS RUM 观测本体', kind: 'business' },
  { key: 'case', name: 'Case', model_name: '联耀业务本体', kind: 'business' },
] };

beforeEach(() => {
  vi.clearAllMocks();
  mockClient.get.mockImplementation((url: string) => {
    if (String(url).endsWith('/knowledge/subjects')) return Promise.resolve({ data: SUBJECTS });
    return Promise.resolve({ data: { items: [ROW], total: 1, page: 1, size: 20 } });
  });
});

afterEach(() => cleanup());

describe('分析场景清单（表格化 + 主体授权）', () => {
  it('清单是表格：有「主体（可见范围）」列，且不铺正文与触发词清单', async () => {
    render(<AnalysisScenarios />);
    expect(await screen.findByText('官网 Web 端体验与流量分析')).toBeInTheDocument();
    expect(screen.getByText('主体（可见范围）')).toBeInTheDocument();
    expect(screen.getByText('Web 端 RUM 观测事件（官网/H5）')).toBeInTheDocument();
    // 正文属于配置详情，只在编辑弹窗里出现，不进清单
    expect(screen.queryByText(/这段正文只该在编辑弹窗里看到/)).not.toBeInTheDocument();
    expect(screen.queryByText('触发词:')).not.toBeInTheDocument();
  });

  it('主体从服务端清单选（显示业务名，不让人手输 object_key）', async () => {
    render(<AnalysisScenarios />);
    await screen.findByText('官网 Web 端体验与流量分析');
    expect(mockClient.get).toHaveBeenCalledWith('/admin/knowledge/subjects');

    fireEvent.click(screen.getByRole('button', { name: /新增分析场景/ }));
    expect(await screen.findByText('主体范围 *')).toBeInTheDocument();
    // 选项是业务名；内部 key 只进 title 供排查，不当主展示
    // （清单行里也出现了同一个主体名，所以用 getAllByText 不区分具体位置）
    expect(screen.getAllByText('Web 端 RUM 观测事件（官网/H5）').length).toBeGreaterThan(0);
    expect(screen.getByText('Case')).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/输入 object_key/)).not.toBeInTheDocument();
  });

  it('未声明主体就保存会被拦下，且不发请求（配了没生效不如当场报错）', async () => {
    render(<AnalysisScenarios />);
    await screen.findByText('官网 Web 端体验与流量分析');
    fireEvent.click(screen.getByRole('button', { name: /新增分析场景/ }));
    // 只填名称/触发词/正文，不选主体也不勾「不限主体」
    fireEvent.change(screen.getByPlaceholderText('如: 按月统计销售额'), { target: { value: '新场景' } });
    fireEvent.change(screen.getByPlaceholderText('如: 销售额, GMV, 成交金额'), { target: { value: '销售额' } });
    fireEvent.change(screen.getByPlaceholderText(/告诉 AI 该如何查询/), { target: { value: '步骤正文' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining('请至少选择一个主体')));
    expect(mockClient.post).not.toHaveBeenCalled();
  });

  it('勾了「不限主体」就合法，且提交体带上 subject_all（显式扩权而不是漏配）', async () => {
    render(<AnalysisScenarios />);
    await screen.findByText('官网 Web 端体验与流量分析');
    fireEvent.click(screen.getByRole('button', { name: /新增分析场景/ }));
    fireEvent.change(screen.getByPlaceholderText('如: 按月统计销售额'), { target: { value: '新场景' } });
    fireEvent.change(screen.getByPlaceholderText('如: 销售额, GMV, 成交金额'), { target: { value: '销售额' } });
    fireEvent.change(screen.getByPlaceholderText(/告诉 AI 该如何查询/), { target: { value: '步骤正文' } });
    // 点「不限主体」开关本身（它在 Label 前面，两者同属一个 flex 容器）
    const note = screen.getAllByText('不限主体（所有角色都能命中该场景）')[0];
    const sw = note.parentElement!.querySelector('button[role="switch"]')!;
    fireEvent.click(sw);
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(mockClient.post).toHaveBeenCalled());
    const payload = mockClient.post.mock.calls[0][1] as any;
    expect(payload.metadata.subject_all).toBe(true);
    expect(payload.metadata.subjects).toEqual([]);
  });

  it('主体无权限而不可见的场景，原因当场写在清单里（不让人到问答里才发现没生效）', async () => {
    mockClient.get.mockImplementation((url: string) => (
      String(url).endsWith('/knowledge/subjects')
        ? Promise.resolve({ data: SUBJECTS })
        : Promise.resolve({ data: { items: [{ ...ROW, visible_to_me: false,
            visibility_note: '所绑主体当前身份无权限（主体：rum_event）' }], total: 1, page: 1, size: 20 } })));
    render(<AnalysisScenarios />);
    expect(await screen.findByText(/所绑主体当前身份无权限/)).toBeInTheDocument();
  });
});
