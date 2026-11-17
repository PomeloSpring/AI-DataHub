/**
 * 字模库表单双语输入位（plan §P6）。
 * 判据：English 输入位存在 + 编辑写 i18n；中文事实源读 name_zh（英文界面下的回落口）。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = readFileSync(resolve(__dirname, '../VisLibrary.tsx'), 'utf-8');

describe('字模库 · 双语输入位（源码级护栏）', () => {
  it('nameEn / descriptionEn state 与 English 输入位存在', () => {
    expect(SRC).toMatch(/useState\(''\);\s*\n\s*\/\/ 双语[^\n]*\n\s*const \[nameEn, setNameEn\]/);
    expect(SRC).toMatch(/data-testid="vis-name-en"/);
    expect(SRC).toMatch(/data-testid="vis-desc-en"/);
  });
  it('openEdit 优先读 *_zh（英文界面保存不得污染中文事实源列）', () => {
    expect(SRC).toMatch(/name_zh\s*\?\?\s*c\.name/);
    expect(SRC).toMatch(/description_zh\s*\?\?\s*c\.description/);
  });
  it('payload 组 i18n blob，非空才写入（空英文 = 清该字段的 en-US，不是把空串塞进去）', () => {
    expect(SRC).toMatch(/if \(nameEn\.trim\(\)\)\s*i18n\.name\s*=\s*\{\s*'en-US':\s*nameEn\.trim\(\)/);
    expect(SRC).toMatch(/if \(descriptionEn\.trim\(\)\)\s*i18n\.description/);
  });
});
