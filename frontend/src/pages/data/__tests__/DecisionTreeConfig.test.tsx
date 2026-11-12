// 数据中台「决策建议」配置页（/data/decisions ↔ data:decisions）的四条硬口径：
//  1. 两段式：保存只落草案，发布是**独立权限码** decision:publish；没有该码时按钮
//     必须禁用并点名缺哪条码（"看起来能点其实 403"就是死开关，menu-permission-sync §3）；
//  2. 路径前缀与权限码一一对应：写动作用 /config*，发布用 /publish*，读用 /query*。
//     混挂会让 read 码顺带放行发布（fnmatch 做不了减法）；
//  3. 条件里的指标/维度只能从字典选（ui-resource-display §2），界面不提供自由输入
//     物理名的口子；建议动作从文案库选，不在树里复制正文；
//  4. 校验的阻断项必须逐条显示，不能只弹一个"保存失败"。
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DecisionTreeConfig from '../DecisionTreeConfig';
import client from '@/api/client';
import { usePermissionStore } from '@/stores/permissionStore';
import { toast } from '@/lib/toast';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }));

const TREE = {
  id: 1, tree_key: 'accuracy-risk', name: '设备精度风险判定', description: '', workspace_id: 0,
  status: 'draft', version: 1, is_active: 1,
  tree_doc: {
    root: {
      type: 'branch', label: '精度风险判定', op: 'all', children: [
        { type: 'leaf', label: '校准超差', advice_key: 'adv_recalibrate',
          when: { metric: '扫描精度偏差均值', comparator: '>=', value: 0.05, unit: 'mm', time_window: '7d' } },
      ],
    },
  },
  apply_sites: [], required_metrics: ['扫描精度偏差均值'], bound_object_keys: ['equipment'],
};
const ADVICE = {
  id: 5, advice_key: 'adv_recalibrate', action: '安排现场校准', rationale: '连续 7 日超差',
  severity: 'high', kb_refs: ['calibration-sop'], workspace_id: 0, is_active: 1,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.get).mockImplementation(async (url: string) => {
    if (url.endsWith('/query/trees')) return { data: { items: [TREE], total: 1 } };
    if (url.endsWith('/query/advice')) return { data: { items: [ADVICE], total: 1 } };
    if (url.endsWith('/query/dictionary')) {
      return { data: { items: [{ name: '扫描精度偏差均值', name_en: 'accuracy_dev', aliases: ['精度偏差'], has_object: true }], total: 1 } };
    }
    if (url.endsWith('/datasources/sites')) return { data: { items: [{ site_code: 'cn', display_name: '中国', is_aggregate: 0, sort_order: 1 }] } };
    if (url.endsWith('/notification/channels')) return { data: [{ id: 3, name: '运维群', channel_type: 'wecom', is_active: true }] };
    return { data: { items: [], total: 0 } };
  });
  vi.mocked(client.post).mockResolvedValue({ data: { saved: true, status: 'draft', validation_errors: [], validation_warnings: [] } } as any);
  usePermissionStore.setState({ loaded: true, unrestricted: true, perms: null, allowedMenus: null });
});
afterEach(() => cleanup());

async function openTree() {
  render(<DecisionTreeConfig />);
  await screen.findByText('设备精度风险判定');
  fireEvent.click(screen.getByRole('button', { name: /设备精度风险判定/ }));
}

/** 默认视图是图面（P2）：要验表单里的叶子明细就先切到表单 */
async function openTreeAsForm() {
  await openTree();
  fireEvent.click(screen.getByRole('button', { name: '表单' }));
  return await screen.findByLabelText('选择指标');
}

describe('决策树配置页', () => {
  it('指标条件用字典选择器，界面没有自由输入物理名的口子', async () => {
    await openTreeAsForm();
    expect(screen.getByLabelText('选择维度')).toBeInTheDocument();
    // 选择框显示的是字典业务名，不是让人敲 equipment_metrics.xxx
    expect(screen.getByText(/扫描精度偏差均值/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/物理列|字段名/)).not.toBeInTheDocument();
  });

  it('有 decision:publish 时发布可点，请求走 /publish* 而不是 /config* 或 /query*', async () => {
    await openTree();
    const btn = await screen.findByRole('button', { name: '发布' });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    await waitFor(() => expect(client.post).toHaveBeenCalled());
    const urls = vi.mocked(client.post).mock.calls.map(c => String(c[0]));
    expect(urls.some(u => u.startsWith('/decisions/publish/'))).toBe(true);
    expect(urls.some(u => u.includes('/query/'))).toBe(false);
  });

  it('缺 decision:publish 时发布按钮禁用且点名缺哪条码（不做死开关）', async () => {
    usePermissionStore.setState({
      loaded: true, unrestricted: false, perms: ['decision:manage'], allowedMenus: ['data:decisions'],
    });
    render(<DecisionTreeConfig />);
    const btn = await screen.findByRole('button', { name: '发布' });
    expect(btn).toBeDisabled();
    expect(await screen.findByText(/decision:publish/)).toBeInTheDocument();
  });

  it('保存草案走 /config/trees，并回显服务端的阻断项与告警', async () => {
    const resp = {
      data: {
        saved: true, status: 'draft',
        validation_errors: [{ where: '精度风险判定/校准超差', term: '校准记录数', type: 'metric',
                              message: '「校准记录数」无法在指标字典中确认', candidates: ['近7天校准记录数'] }],
        validation_warnings: [{ where: '精度风险判定/校准超差', term: '扫描精度偏差均值', type: 'window',
                                message: '条件没有相对时间窗' }],
      },
    };
    vi.mocked(client.post).mockResolvedValue(resp as any);
    vi.mocked(client.put).mockResolvedValue(resp as any);
    await openTree();
    fireEvent.click(screen.getByRole('button', { name: '保存草案' }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    expect(String(vi.mocked(client.put).mock.calls[0][0])).toBe('/decisions/config/trees/accuracy-risk');
    expect(await screen.findByText(/【阻断】.*无法在指标字典中确认/)).toBeInTheDocument();
    expect(screen.getByText(/【告警】.*没有相对时间窗/)).toBeInTheDocument();
  });

  it('建议文案库是独立清单：叶子只引用 advice_key，不复制正文', async () => {
    await openTreeAsForm();
    fireEvent.click(screen.getByRole('button', { name: '建议文案库' }));
    expect(await screen.findByText('安排现场校准')).toBeInTheDocument();
    expect(screen.getByText('adv_recalibrate')).toBeInTheDocument();
    // 条件树里的建议入口是一个"从文案库选"的选择框，不是正文输入框
    fireEvent.click(screen.getByRole('button', { name: '决策树' }));
    fireEvent.click(screen.getByRole('button', { name: '表单' }));
    expect(await screen.findByLabelText('建议动作（从文案库选）')).toBeInTheDocument();
  });

  it('字典加载失败显式提示，不伪装成"没有可选项"', async () => {
    vi.mocked(client.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/query/dictionary')) {
        const err: any = new Error('boom');
        err.response = { status: 500, data: { detail: '指标字典读取失败' } };
        throw err;
      }
      if (url.endsWith('/query/trees')) return { data: { items: [TREE], total: 1 } };
      if (url.endsWith('/query/advice')) return { data: { items: [ADVICE], total: 1 } };
      if (url.endsWith('/datasources/sites')) return { data: { items: [] } };
      if (url.endsWith('/notification/channels')) return { data: [] };
      return { data: { items: [], total: 0 } };
    });
    await openTreeAsForm();
    fireEvent.click(screen.getByLabelText('选择指标'));
    expect(await screen.findByText('指标字典读取失败')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    expect(vi.mocked(toast.error)).not.toHaveBeenCalled();
  });

  it('条件树默认给图面，可切表单，且切回来还是同一份数据', async () => {
    await openTree();
    // 默认图面：有视图切换与图面自己的详情侧栏（明细不平铺在画布上）
    expect(screen.getByRole('button', { name: '图面' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByLabelText('节点详情')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '表单' }));
    expect(await screen.findByLabelText('判定名称')).toBeInTheDocument();

    // 切回图面：根判定名仍是文档里那一个（两种视图共用一份 tree_doc）
    fireEvent.click(screen.getByRole('button', { name: '图面' }));
    expect(await screen.findByLabelText('图面分支名称')).toHaveValue('精度风险判定');
  });

  it('图面里加的判定条件切到表单也在，保存时仍是同一份 tree_doc', async () => {
    await openTree();
    fireEvent.click(screen.getByLabelText('图面加判定条件'));
    fireEvent.click(screen.getByRole('button', { name: '表单' }));
    const inputs = screen.getAllByLabelText('情形描述');
    expect(inputs).toHaveLength(2);   // 原有那条 + 图面新加的那条（同一份文档）
    fireEvent.click(screen.getByRole('button', { name: '保存草案' }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    const body = vi.mocked(client.put).mock.calls[0][1] as any;
    expect(body.tree_doc.root.children).toHaveLength(2);
  });
});
