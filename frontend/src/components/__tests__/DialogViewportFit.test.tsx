import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import fs from 'node:fs';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';
import MetricsCenter from '../../pages/catalog/MetricsCenter';
import { metricsApi } from '../../api/metrics';

/**
 * 弹窗高度护栏 —— 历史缺陷：指标/维度编辑弹窗（十几行表单 + 三元组预览）
 * 把 DialogContent 撑得比视口还高，而基准类只有 fixed 居中、没有 max-h/overflow：
 * 结果标题与关闭按钮跑到视口上方、保存/取消跑到视口下方，页面既滚不动也点不到
 * （用户视角＝"弹窗占满页面无法操作"）。
 *
 * 钉住三件事：
 * 1. 基准 DialogContent 自带 max-h + 纵向滚动（全站弹窗默认不再溢出视口）；
 * 2. 页面自定义的 max-h-[85vh] 之类仍能覆盖基准（twMerge 后写优先）；
 * 3. 指标编辑弹窗实际渲染出的节点带护栏（不是只改了组件、页面又用别的容器绕开）。
 */

const dialogSrc = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'components', 'ui', 'dialog.tsx'), 'utf8');

afterEach(() => cleanup());

describe('DialogContent 基准高度护栏', () => {
  it('基准类含 max-h-[90vh] + overflow-y-auto（长表单不再顶出视口）', () => {
    expect(dialogSrc).toMatch(/max-h-\[90vh\]/);
    expect(dialogSrc).toMatch(/overflow-y-auto/);
  });

  it('渲染出的弹窗节点带护栏类', () => {
    render(
      <Dialog open>
        <DialogContent>
          <DialogHeader><DialogTitle>标题</DialogTitle></DialogHeader>
          <div className="h-[2000px]">超长内容</div>
          <DialogFooter><button>保存</button></DialogFooter>
        </DialogContent>
      </Dialog>,
    );
    const cls = screen.getByRole('dialog').className;
    expect(cls).toContain('max-h-[90vh]');
    expect(cls).toContain('overflow-y-auto');
  });

  it('页面自定义 max-h 仍优先生效（基准不抢各页既有约定）', () => {
    render(
      <Dialog open>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader><DialogTitle>标题</DialogTitle></DialogHeader>
        </DialogContent>
      </Dialog>,
    );
    const cls = screen.getByRole('dialog').className;
    expect(cls).toContain('max-h-[85vh]');
    expect(cls).not.toContain('max-h-[90vh]');
  });

  it('占满整高的抽屉（看板属性面板 narrow）自行覆盖基准 max-h', () => {
    const src = fs.readFileSync(
      path.resolve(__dirname, '..', 'editor', 'PropertyPanel.tsx'), 'utf8');
    const drawer = src.match(/h-\[100dvh\][^"]*"/)?.[0] ?? '';
    expect(drawer).toContain('max-h-[100dvh]');
  });
});

// ── 现场：指标中心「编辑指标」弹窗 ────────────────────────────────────
vi.mock('@/api/metrics', () => ({
  metricsApi: {
    list: vi.fn(), filters: vi.fn(), listAllDimensions: vi.fn(),
    getDimensions: vi.fn(), addDimension: vi.fn(), updateDimension: vi.fn(),
    create: vi.fn(), update: vi.fn(), delete: vi.fn(), deleteDimension: vi.fn(),
    assetPool: vi.fn(),
  },
}));

const FACETS = {
  metrics: {
    total: 1, active: 1, inactive: 0, certified: 0, draft: 1, unbound_objects: 0,
    with_target_table: 1, without_target_table: 0, with_target_column: 0,
    categories: [{ value: '可观测', count: 1 }],
    agg_types: [{ value: 'COUNT', count: 1 }],
    tags: [{ value: '基础', count: 1 }],
  },
  dimensions: {
    total: 0, active: 0, inactive: 0, certified: 0, draft: 0, unbound_objects: 0,
    with_target_table: 0, without_target_table: 0, with_target_column: 0,
    with_value_labels: 0, without_value_labels: 0, categories: [],
  },
  objects: [{ key: 'rum_event', display_name: 'RUM观测事件', metric_count: 1, dimension_count: 0 }],
};

beforeEach(() => {
  vi.mocked(metricsApi.list).mockResolvedValue({ data: { total: 1, items: [{
    id: 1, name: '事件总数', display_name: '事件总数', description: '', category: '可观测',
    tags: ['基础'], calculation_type: 'COUNT', expression: 'COUNT(1)', unit: '个',
    target_table: 'rum_event', target_column: '', granularity: '', owner: '',
    bound_object_key: 'rum_event', aliases: '[]', certified: 0, is_active: 1,
    created_at: '', updated_at: '',
  }] } } as never);
  vi.mocked(metricsApi.filters).mockResolvedValue({ data: FACETS } as never);
  vi.mocked(metricsApi.listAllDimensions).mockResolvedValue({ data: { total: 0, items: [] } } as never);
  vi.mocked(metricsApi.getDimensions).mockResolvedValue({ data: [] } as never);
});

it('指标编辑弹窗：表单节点带高度护栏，取消/保存吸底可见', async () => {
  render(<MetricsCenter embedded={false} />);
  await screen.findByText('共 1 条');
  const row = (await screen.findAllByText('事件总数'))[0].closest('tr') as HTMLElement;
  fireEvent.click(row.querySelectorAll('button')[0]); // 行内首个图标钮 = 编辑
  const dlg = await screen.findByRole('dialog');
  expect(dlg.className).toContain('max-h-');
  expect(dlg.className).toMatch(/overflow-y-auto/);
  // 操作条不得在滚动容器里漂走（sticky 底）
  const footer = dlg.querySelector('div.sticky.bottom-0');
  expect(footer).not.toBeNull();
  expect(footer?.textContent).toContain('保存');
  // 三元组预览限高内部滚动，不把表单撑出视口
  const pre = dlg.querySelector('pre');
  expect(pre?.className).toContain('max-h-40');
});
