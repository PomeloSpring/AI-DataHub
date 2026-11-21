/**
 * 分析场景表单双语输入位（plan §P6）。
 *
 * 2026-12-05：表单已抽成共享组件 `components/ScenarioEditorDialog.tsx`（进化中心
 * 的「追加到已有场景」复用同一份表单与校验），页里不再有输入位。护栏改为扫拥有
 * 表单的那个文件 —— 护栏指错文件比红着更坑：后人会去一个已无表单的页面里加输入位。
 * P6 本身仍未实现（titleEn/scenarioEn 缺失），所以本文件依旧红；这是已知缺口，
 * 不属于「知识更新闭环」范围。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = readFileSync(resolve(__dirname, '../../../components/ScenarioEditorDialog.tsx'), 'utf-8');

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
