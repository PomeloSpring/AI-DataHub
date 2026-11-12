// 「选中态看得出来」这条口径的护栏：一处定义、多处引用，且必须靠多层线索而不是单一底色。
//
// 起因（用户反馈）：看板「仅收藏」选中与未选中在页面上分不出来 —— 旧写法只把 variant
// 从 ghost 换成 secondary，而亮色主题下 --secondary = 218 73% 97%（近乎白的蓝），落在
// 白底卡片上肉眼不可辨。这类"选中态等于没换"的缺陷靠肉眼回归很容易漏，所以钉成用例。
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { TOGGLE_ACTIVE_CLASS } from '../toggleState';

const read = (...parts: string[]) =>
  fs.readFileSync(path.resolve(__dirname, '..', '..', ...parts), 'utf8');

describe('可切换按钮的选中态口径', () => {
  it('四层线索齐备：底色 + 文字色 + 字重 + 描边（不用单一色相传达状态）', () => {
    expect(TOGGLE_ACTIVE_CLASS).toMatch(/bg-primary\/\d+/);
    expect(TOGGLE_ACTIVE_CLASS).toMatch(/(^| )text-primary( |$)/);
    expect(TOGGLE_ACTIVE_CLASS).toContain('font-semibold');
    expect(TOGGLE_ACTIVE_CLASS).toMatch(/ring-1 ring-primary\/\d+/);
  });

  it('hover 留在同一色系（否则 ghost 变体的 hover:bg-accent 会把选中态刷成灰底）', () => {
    expect(TOGGLE_ACTIVE_CLASS).toMatch(/hover:bg-primary\/\d+/);
    expect(TOGGLE_ACTIVE_CLASS).not.toContain('hover:bg-accent');
  });

  it('看板「仅收藏」不再靠 variant 区分选中，并给出 aria-pressed 与实心星形', () => {
    const src = read('pages', 'Dashboard.tsx');
    const line = src.split('\n').find(l => l.includes('仅收藏')) ?? '';
    expect(line).toContain('TOGGLE_ACTIVE_CLASS');
    expect(line).toContain('aria-pressed={favoritesOnly}');
    expect(line).toContain('fill-current');
    // 旧口径不得回潮：只换 variant 在这套主题下等于没换
    expect(src).not.toMatch(/variant=\{favoritesOnly \? 'secondary' : 'ghost'\}/);
  });

});
