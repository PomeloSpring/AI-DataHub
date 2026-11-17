/**
 * 栏目/标签管理双语输入位（plan §P6）。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = readFileSync(resolve(__dirname, '../ContentTaxonomy.tsx'), 'utf-8');

describe('栏目 / 标签 · 双语输入位（源码级护栏）', () => {
  it('栏目 dialog：打开时把中文事实源回填（label_zh ?? label，避免英文界面保存污染中文列）', () => {
    expect(SRC).toMatch(/label:\s*d\.label_zh\s*\?\?\s*d\.label/);
    expect(SRC).toMatch(/description:\s*d\.description_zh\s*\?\?\s*d\.description/);
  });
  it('栏目 submit 组 i18n blob 上送', () => {
    expect(SRC).toMatch(/if \(labelEn\) i18n\.label = \{\s*'en-US':\s*labelEn\s*\}/);
    expect(SRC).toMatch(/if \(descEn\) i18n\.description/);
  });
  it('新标签 English 输入位 + createTag 带 i18n', () => {
    expect(SRC).toMatch(/data-testid="tag-new-en"/);
    expect(SRC).toMatch(/if \(newTagEn\.trim\(\)\) i18n\.tag = \{\s*'en-US':\s*newTagEn\.trim\(\)/);
  });
  it('UI 上给栏目 English 输入挂 data-testid，供 e2e 定位', () => {
    expect(SRC).toMatch(/data-testid="category-label-en"/);
    expect(SRC).toMatch(/data-testid="category-desc-en"/);
  });
});
