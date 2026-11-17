/**
 * 分析场景表单双语输入位（plan §P6）。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = readFileSync(resolve(__dirname, '../AnalysisScenarios.tsx'), 'utf-8');

describe('分析场景 · 双语输入位（源码级护栏）', () => {
  it('FormData 声明 titleEn / scenarioEn（英文输入位落这里）', () => {
    expect(SRC).toMatch(/titleEn:\s*string;/);
    expect(SRC).toMatch(/scenarioEn:\s*string;/);
  });
  it('payload 里带 i18n（title/scenario 覆盖层，仅非空才写）', () => {
    expect(SRC).toMatch(/if \(form\.titleEn\?\.trim\(\)\) i\.title = \{\s*'en-US':\s*form\.titleEn\.trim\(\)/);
    expect(SRC).toMatch(/if \(form\.scenarioEn\?\.trim\(\)\) i\.scenario/);
  });
  it('UI 挂 data-testid="scenario-title-en" / "scenario-desc-en"（浏览器可寻）', () => {
    expect(SRC).toMatch(/data-testid="scenario-title-en"/);
    expect(SRC).toMatch(/data-testid="scenario-desc-en"/);
  });
});
