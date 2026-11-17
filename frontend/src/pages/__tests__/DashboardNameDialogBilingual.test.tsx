/**
 * 看板属性面板（nameDialog）双语输入位（plan §P6）。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = readFileSync(resolve(__dirname, '../Dashboard.tsx'), 'utf-8');

describe('看板属性 · 双语输入位（源码级护栏）', () => {
  it('nameDialog state 扩：nameEn + description + descriptionEn', () => {
    expect(SRC).toMatch(/nameEn:\s*string;\s*description:\s*string;\s*descriptionEn:\s*string/);
  });
  it('重命名入口回填读 *_zh（英文界面下的回落口）', () => {
    expect(SRC).toMatch(/name:\s*db\.name_zh\s*\?\?\s*db\.name/);
    expect(SRC).toMatch(/description:\s*db\.description_zh\s*\?\?\s*db\.description/);
  });
  it('提交时把非空英文写进 i18n blob；空则不带上', () => {
    expect(SRC).toMatch(/if \(nameDialog\.nameEn\.trim\(\)\)\s*i18n\.name = \{\s*'en-US':\s*nameDialog\.nameEn\.trim\(\)/);
    expect(SRC).toMatch(/if \(nameDialog\.descriptionEn\.trim\(\)\)\s*i18n\.description/);
  });
  it('English 输入位有 data-testid 供 e2e 定位', () => {
    expect(SRC).toMatch(/data-testid="dashboard-name-en"/);
    expect(SRC).toMatch(/data-testid="dashboard-desc-en"/);
  });
});
