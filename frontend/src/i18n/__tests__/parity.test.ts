/**
 * zh-CN / en-US 两套语言包 key 集合必须完全一致。
 *
 * 上一轮审计发现 4 个 key 缺英文（chat:reportDraft.charCount / common:label.selected /
 * common:label.total / workspace:delete.itemCount），本次已在 en-US 补齐。这里加静态
 * 护栏：以后再出现「加了中文忘了加英文」直接测试红，不允许再靠人工审计兜。
 *
 * 只比 key 结构不比内容（内容靠 review）；容忍 i18next 复数后缀 `_one`/`_other`
 * 与语义等价的 `_many` 变体（复数资源视为同一逻辑 key）。
 */
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = resolve(__dirname, '..', 'locales');
const NAMESPACES = readdirSync(resolve(ROOT, 'zh-CN'))
  .filter((f) => f.endsWith('.json'))
  .map((f) => f.replace(/\.json$/, ''));

/** 摊平嵌套对象为 `a.b.c` 路径；数组按索引展开；复数后缀归一。 */
function flatten(obj: unknown, prefix = ''): string[] {
  if (obj === null || typeof obj !== 'object') return prefix ? [prefix] : [];
  if (Array.isArray(obj)) {
    return obj.flatMap((v, i) => flatten(v, `${prefix}[${i}]`));
  }
  return Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) =>
    flatten(v, prefix ? `${prefix}.${k}` : k),
  );
}

/**
 * i18next v4 plural 规则：中文只有一份（无后缀），英文有 `_one` / `_other`。
 * 归一到不带后缀的 base，避免把复数当成 key 差异。
 */
function normalize(key: string): string {
  return key.replace(/_(one|other|many|two|few|zero)$/, '');
}

function loadLocale(ns: string, locale: 'zh-CN' | 'en-US'): string[] {
  const path = resolve(ROOT, locale, `${ns}.json`);
  const raw = readFileSync(path, 'utf-8');
  return flatten(JSON.parse(raw)).map(normalize).sort();
}

describe('locale key parity', () => {
  it('语言包文件存在且非空', () => {
    expect(NAMESPACES.length).toBeGreaterThan(0);
    for (const ns of NAMESPACES) {
      expect(NAMESPACES).toContain(ns);
    }
  });

  for (const ns of NAMESPACES) {
    it(`${ns}: zh-CN 与 en-US 的 key 集合完全一致`, () => {
      const zh = loadLocale(ns, 'zh-CN');
      const en = loadLocale(ns, 'en-US');
      const missingInEn = zh.filter((k) => !en.includes(k));
      const missingInZh = en.filter((k) => !zh.includes(k));
      expect(missingInEn, `en-US/${ns}.json 缺以下 key: ${missingInEn.join(', ')}`).toEqual([]);
      expect(missingInZh, `zh-CN/${ns}.json 缺以下 key: ${missingInZh.join(', ')}`).toEqual([]);
    });
  }

  it('审计出的 4 个补齐 key 现在都在 en-US 里', () => {
    // 上一轮审计发现这 4 个中文有、英文无；本次必须留痕（防止有人回滚补丁）
    const chat = loadLocale('chat', 'en-US');
    expect(chat).toContain('reportDraft.charCount');
    const common = loadLocale('common', 'en-US');
    expect(common).toContain('label.selected');
    expect(common).toContain('label.total');
    const ws = loadLocale('workspace', 'en-US');
    expect(ws).toContain('delete.itemCount');
  });
});
