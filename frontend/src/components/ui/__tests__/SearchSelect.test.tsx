/**
 * 可搜索选择框（`ui/search-select`）契约：
 *  - `keywords` 只参与匹配、不渲染（数据集入口要靠描述/字段名命中，但下拉里不该铺一堆噪声）；
 *  - 加载失败必须显示为失败 + 可重试，不得伪装成"暂无可选项"（no-silent-degradation）；
 *  - 超长清单截断要可见，不能静默少给候选。
 * 另锁一处接线：图表配置面板的数据集入口必须是可搜索选择，不得回退成盲选下拉。
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { SearchSelect, type SearchSelectOption } from '../search-select';

const options: SearchSelectOption[] = [
  { value: '8', label: 'ARMS·用户与会话趋势', description: 'arms-prd-cn · 5 字段', keywords: '独立访客 统计日期' },
  { value: '9', label: '待授权数据集', description: 'prd-eu · 3 字段' },
];

function openAndType(text: string) {
  const onValueChange = vi.fn();
  render(<SearchSelect value="" onValueChange={onValueChange} options={options} ariaLabel="数据集" />);
  fireEvent.click(screen.getByRole('combobox', { name: '数据集' }));
  fireEvent.change(screen.getByPlaceholderText('输入关键词过滤…'), { target: { value: text } });
  return onValueChange;
}

describe('SearchSelect', () => {
  it('keywords 参与匹配但不出现在选项文案里', () => {
    openAndType('独立访客');
    const hit = screen.getByRole('option', { name: /ARMS·用户与会话趋势/ });
    expect(hit.textContent).not.toContain('独立访客');           // 只匹配，不渲染
    expect(screen.queryAllByRole('option')).toHaveLength(1);      // 另一条未被命中
  });

  it('label / description 同样参与匹配，选中回传的是 value（内部 id 不外露为文本）', () => {
    const onValueChange = openAndType('prd-eu');
    fireEvent.click(screen.getByRole('option', { name: /待授权数据集/ }));
    expect(onValueChange).toHaveBeenCalledWith('9');
  });

  it('加载失败显示原因与重试，不伪装成"暂无可选项"', () => {
    const onRetry = vi.fn();
    render(<SearchSelect value="" onValueChange={vi.fn()} options={[]} loading={false}
      error="数据集清单加载失败，请重试" onRetry={onRetry} ariaLabel="数据集" />);
    fireEvent.click(screen.getByRole('combobox', { name: '数据集' }));
    expect(screen.getByText('数据集清单加载失败，请重试')).toBeTruthy();
    expect(screen.queryByText('暂无可选项')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /重试/ }));
    expect(onRetry).toHaveBeenCalled();
  });

  it('无候选与无匹配是两句话（都算业务结论，但成因不同）', () => {
    render(<SearchSelect value="" onValueChange={vi.fn()} options={[]} emptyText="当前角色清单内没有可选数据集" ariaLabel="数据集" />);
    fireEvent.click(screen.getByRole('combobox', { name: '数据集' }));
    expect(screen.getByText('当前角色清单内没有可选数据集')).toBeTruthy();
  });

  it('超出渲染上限时明示截断，不静默少给候选', () => {
    const many = Array.from({ length: 260 }, (_, i) => ({ value: String(i), label: `数据集 ${i}` }));
    render(<SearchSelect value="" onValueChange={vi.fn()} options={many} ariaLabel="数据集" />);
    fireEvent.click(screen.getByRole('combobox', { name: '数据集' }));
    expect(screen.getByText(/共 260 项，仅显示前 200 项/)).toBeTruthy();
  });

  it('已选值不在清单内时显式标注，不静默显示裸 id', () => {
    render(<SearchSelect value="999" onValueChange={vi.fn()} options={options} ariaLabel="数据集" />);
    expect(screen.getByRole('combobox', { name: '数据集' }).textContent).toContain('999（不在清单中）');
  });
});

describe('图表配置面板的数据集入口接线', () => {
  const src = readFileSync(resolve(process.cwd(), 'src/components/ChartConfigPanel.tsx'), 'utf8');

  it('用可搜索选择框，且关键词覆盖描述/字段业务名（清单 20+ 条时盲选不可用）', () => {
    expect(src).toContain('<SearchSelect');
    expect(src).toContain('datasetSearchText');                   // 命中范围与组件库同源
    expect(src).toContain('onRetry={loadDatasets}');              // 加载失败可重试
    expect(src).not.toMatch(/<Select[\s\S]{0,200}从数据集清单选择/); // 不回退成不可搜索的下拉
  });
});
