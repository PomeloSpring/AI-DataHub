/**
 * 模板选择弹窗回归：模板定位是图表样式展示，选的时候就要看清“内置示例数据、不取数”。
 *
 * 锁定：卡片带真实栅格预览 + 适用场景 + 内置示例数据说明；搜索按名称/场景/标签命中；
 * 模板定义校验未通过时显式报警并禁止导入（不静默渲染残缺模板 —— no-silent-degradation）。
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import DashboardTemplates from '@/components/DashboardTemplates';
import { toast } from '@/lib/toast';
import { DASHBOARD_TEMPLATES, validateTemplates } from '@/lib/dashboardTemplates';

vi.mock('@/lib/dashboardTemplates', async importOriginal => {
  const real = await importOriginal<typeof import('@/lib/dashboardTemplates')>();
  return { ...real, validateTemplates: vi.fn(real.validateTemplates) };
});

const open = () => render(<DashboardTemplates open onClose={vi.fn()} onApply={vi.fn()} />);

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.mocked(validateTemplates).mockImplementation(() => []); });

describe('仪表盘模板弹窗', () => {
  it('渲染全部模板：栅格预览、适用场景与「内置示例数据」说明', () => {
    open();
    for (const template of DASHBOARD_TEMPLATES) {
      const card = screen.getByTestId(`template-card-${template.id}`);
      expect(within(card).getByText(template.name)).toBeTruthy();
      expect(within(card).getByText(template.scenario)).toBeTruthy();
      expect(within(card).getByTestId('template-preview')).toBeTruthy();
      if (template.charts.length) {
        expect(within(card).getByText(/内置示例数据/)).toBeTruthy();
        // 预览按真实栅格逐张卡片绘制（数量与模板图表一致、带像素位置），而不是写死几根假柱子
        const tiles = Array.from(within(card).getAllByTestId('template-preview')[0].querySelectorAll('div.absolute'));
        expect(tiles.length).toBe(template.charts.length);
        expect(tiles.every(t => /px/.test(String((t as HTMLElement).style.left)) && /px/.test(String((t as HTMLElement).style.width)))).toBe(true);
      }
    }
  });

  it('按名称/场景/标签搜索都能命中，不匹配时给空态', () => {
    open();
    const search = screen.getByPlaceholderText(/搜索模板/);
    fireEvent.change(search, { target: { value: '成本' } });
    expect(screen.getAllByRole('button', { name: /成本/ }).length).toBeGreaterThan(0);
    fireEvent.change(search, { target: { value: 'Token' } });
    expect(screen.queryByText('网站流量分析')).toBeNull();
    expect(screen.getByText(/AI 应用用量分析/)).toBeTruthy();
    fireEvent.change(search, { target: { value: '不存在的关键字' } });
    expect(screen.getByText('未找到匹配的模板')).toBeTruthy();
  });

  it('选中后显示图表数与示例数据说明，确认后回传模板并提示后续动作', () => {
    const onApply = vi.fn();
    const onClose = vi.fn();
    render(<DashboardTemplates open onClose={onClose} onApply={onApply} />);
    const info = vi.spyOn(toast, 'info').mockReturnValue(0 as any);
    fireEvent.click(screen.getByTestId('template-card-sales_overview'));
    expect(screen.getByText(/将创建 \d+ 个图卡，以内置示例数据展示样式/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '使用此模板' }));
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ id: 'sales_overview' }));
    expect(onClose).toHaveBeenCalled();
    expect(info).toHaveBeenCalledWith(expect.stringContaining('仅用于展示样式'), expect.anything());
  });

  it('预览底色/卡片走主题包解析（与看板渲染同源，不另创画风）', () => {
    // themePackFor 会命中 tp_datafoundry（无主题时的兼底包）
    const pack = { id: 1, code: 'tp_datafoundry', category: 'theme_pack', name: '默认包',
      style_config: { mode: 'dark', palette: ['#22d3ee', '#3b82f6'],
        background: { backgroundColor: '#0a1220' }, card: { cardBg: '#112233' } } } as any;
    render(<DashboardTemplates open onClose={vi.fn()} onApply={vi.fn()} items={[pack]} />);
    const preview = screen.getAllByTestId('template-preview')[0] as HTMLElement;
    expect(preview.style.backgroundColor).toBe('rgb(10, 18, 32)');
    const tile = preview.querySelector('div.absolute') as HTMLElement;
    expect(String(tile?.style.background || tile?.style.backgroundColor)).toContain('rgb(17, 34, 51)');
  });

  it('空白模板不谎称内置示例数据', () => {
    open();
    const card = screen.getByTestId('template-card-empty');
    expect(within(card).getAllByText('空白画布').length).toBeGreaterThan(0);
    expect(within(card).queryByText(/内置示例数据/)).toBeNull();
  });

  it('模板定义校验未通过：显式报错、禁用导入按钮，点了也不回传', () => {
    const onApply = vi.fn();
    vi.mocked(validateTemplates).mockReturnValue(['模板「销售经营概览」 ·「销售趋势」 缺少示例数据，导入后看不到效果']);
    const err = vi.spyOn(toast, 'error').mockReturnValue(0 as any);
    open();
    expect(String(screen.getByRole('alert').textContent)).toContain('模板定义校验未通过');
    fireEvent.click(screen.getByTestId('template-card-sales_overview'));
    const apply = screen.getByRole('button', { name: '使用此模板' });
    expect(apply.hasAttribute('disabled')).toBe(true);
    fireEvent.click(apply);
    expect(onApply).not.toHaveBeenCalled();
    expect(err).not.toHaveBeenCalled();
  });
});
