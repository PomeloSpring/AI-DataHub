import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/tabs';

/**
 * 护栏：Radix 隐藏「非激活 TabsContent」靠的是 HTML `hidden` 属性，而 Tailwind preflight 的
 * `[hidden]:where(:not([hidden="until-found"])){display:none}` 特异性只有 (0,1,0)，与 utilities
 * 同权重且排在前面 —— 页面只要给 TabsContent 传 `flex`/`grid` 等显示类（指标中心、安全策略页的
 * `flex-1 min-h-0 flex flex-col` 全高布局），hidden 就会被覆盖，空面板继续以 flex-1 参与布局，
 * 把真正的 Tab 内容挤到下半屏（症状：页面中间一大片空白）。
 * 修法是在基准组件里用 `data-[state=inactive]:hidden`（特异性 (0,2,0)）显式收口，此处锁定它不被删。
 */
function renderTwoTabs() {
  return render(
    <Tabs defaultValue="a" className="flex flex-col">
      <TabsList>
        <TabsTrigger value="a">甲</TabsTrigger>
        <TabsTrigger value="b">乙</TabsTrigger>
      </TabsList>
      {/* 与真实调用方同口径：非激活面板也带 flex 显示类 */}
      <TabsContent value="a" className="flex-1 min-h-0 flex flex-col gap-4">A 内容</TabsContent>
      <TabsContent value="b" className="flex-1 min-h-0 flex flex-col gap-4">B 内容</TabsContent>
    </Tabs>,
  );
}

describe('TabsContent 非激活面板必须真正退出布局', () => {
  /** 非激活面板带 hidden 属性, 会被 getByRole 的可访问性过滤掉 —— 这里按 DOM 取两个面板 */
  function panels(): HTMLElement[] {
    const { container } = renderTwoTabs();
    return Array.from(container.querySelectorAll<HTMLElement>('[role="tabpanel"]'));
  }

  it('基准组件带 data-[state=inactive]:hidden 护栏类', () => {
    const all = panels();
    expect(all.length).toBe(2);
    all.forEach((p) => {
      expect(p.className).toContain('data-[state=inactive]:hidden');
    });
  });

  it('非激活面板同时带 hidden 属性与护栏类（页面无需各自补）', () => {
    const [active, inactive] = panels();
    expect(active.getAttribute('data-state')).toBe('active');
    expect(active.hasAttribute('hidden')).toBe(false);
    expect(inactive.getAttribute('data-state')).toBe('inactive');
    expect(inactive.hasAttribute('hidden')).toBe(true);
    expect(inactive.className).toContain('data-[state=inactive]:hidden');
  });
});
