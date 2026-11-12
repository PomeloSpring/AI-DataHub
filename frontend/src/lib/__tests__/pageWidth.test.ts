import { describe, expect, it } from 'vitest';
import { READING, WIDE, containerForKind, widthModeForKind } from '../pageWidth';

/**
 * 阅读宽度的分流口径（公司介绍这类 HTML 版式与数据报告铺满页面，长文保持窄栏）。
 * 这里锁的是"规则本身"，页面侧只消费它，不允许各页再各写一套 max-w。
 */
describe('pageWidth — 正文容器宽度按内容形态分流', () => {
  it('html 版式走宽版，其余形态走窄栏', () => {
    expect(containerForKind('html')).toBe(WIDE);
    for (const kind of ['markdown', 'link', 'file', 'promoted', undefined, null]) {
      expect(containerForKind(kind)).toBe(READING);
    }
    expect(widthModeForKind('html')).toBe('wide');
    expect(widthModeForKind('markdown')).toBe('reading');
  });

  it('宽版是「百分比填满 + 像素上限兜底」，不是只写一个像素上限', () => {
    // 只给 max-w-[Npx]：N 大于当前视口时永不触发（改了等于没改）；
    // 只给百分比：超宽屏无限拉长。两者必须同时在。
    expect(WIDE).toMatch(/w-\[\d+%/);
    expect(WIDE).toMatch(/max-w-\[\d+px\]/);
    expect(WIDE).toContain('min-w-0');
    expect(WIDE).toContain('mx-auto');
    expect(READING).toMatch(/max-w-\[\d+px\]/);
  });

  it('宽版上限明显大于长文栏，二者不是同一个数', () => {
    const px = (s: string) => Number(s.match(/max-w-\[(\d+)px\]/)?.[1] ?? 0);
    expect(px(WIDE)).toBeGreaterThan(px(READING));
  });
});
