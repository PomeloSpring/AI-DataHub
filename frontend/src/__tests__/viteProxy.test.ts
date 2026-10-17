import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * dev 代理配置护栏（防"SPA 兜底"这一类静默失败回潮）。
 *
 * 历史缺陷：vite 代理曾是 60+ 条 `proxy['/api/xxx']` **显式白名单**，且所有 target
 * 早已收敛到同一个后端 8000。漏登记一个前缀时，vite 不会 404，而是用 SPA 的
 * index.html 兜底返回 **200 + text/html**：axios 不报错，页面渲染成"空列表/未设置"。
 * 已因此踩过 eval / evolution / assets，实测又抓到 memory、ontology、user-assets
 * ——「我的记忆」页因此永远显示未设置，而记忆其实早已落库。
 *
 * 本测试锁两件事：① 代理必须是 `/api` 单条通配（退回白名单即红）；
 * ② 前端代码里真实出现的每个 API 前缀都必须能被代理规则匹配。
 */

const ROOT = path.resolve(__dirname, '../..');
const CONFIG_SRC = readFileSync(path.join(ROOT, 'vite.config.ts'), 'utf-8');

/** 从 vite.config.ts 里取出所有代理路径前缀（先剔掉注释，避免文档里的示例字串被当成配置）。 */
function proxyPrefixes(): string[] {
  const code = CONFIG_SRC
    .split('\n')
    .filter((l) => !l.trim().startsWith('//') && !l.trim().startsWith('*') && !l.trim().startsWith('/*'))
    .join('\n');
  const keys = new Set<string>();
  for (const m of code.matchAll(/proxy\[\s*['"]([^'"]+)['"]\s*\]/g)) keys.add(m[1]);
  for (const m of code.matchAll(/^\s{4,}['"](\/api[^'"]*)['"]\s*:/gm)) keys.add(m[1]);
  return [...keys];
}

/** 扫描 src 下所有 axios client 调用里出现的 API 首段前缀。 */
function usedApiPrefixes(): string[] {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== '__tests__' && entry.name !== 'node_modules') walk(full);
      } else if (/\.(ts|tsx)$/.test(entry.name)) files.push(full);
    }
  };
  walk(path.join(ROOT, 'src'));
  const prefixes = new Set<string>();
  const re = /client\.(?:get|post|put|delete|patch)\(\s*[`'"]\/([a-zA-Z0-9_-]+)/g;
  for (const f of files) {
    const src = readFileSync(f, 'utf-8');
    for (const m of src.matchAll(re)) prefixes.add(`/api/${m[1]}`);
  }
  return [...prefixes].sort();
}

describe('vite dev 代理必须覆盖全部 /api 前缀', () => {
  it('代理是 /api 单条通配（不得退回逐前缀白名单）', () => {
    const prefixes = proxyPrefixes();
    expect(prefixes).toEqual(['/api']);
    // 通配的意义就是"新增端点零登记"；再出现第二条具体前缀说明白名单回潮
    expect(prefixes.filter((p) => p !== '/api')).toHaveLength(0);
  });

  it('前端用到的每个 API 前缀都能被代理规则匹配', () => {
    const prefixes = proxyPrefixes();
    const unmatched = usedApiPrefixes().filter(
      (used) => !prefixes.some((p) => used === p || used.startsWith(`${p}/`)),
    );
    // 断言消息里点名：漏配的前缀会被 SPA 兜底成 index.html(200+HTML)，页面只显示空数据
    expect(unmatched, `以下前缀未被 dev 代理覆盖，会被兜底成 HTML: ${unmatched.join(', ')}`).toEqual([]);
  });

  it('扫描确实覆盖到了已知端点（防测试自身失效）', () => {
    const used = usedApiPrefixes();
    for (const must of ['/api/memory', '/api/ontology', '/api/user-assets', '/api/chat', '/api/users']) {
      expect(used, `扫描器应能发现 ${must}`).toContain(must);
    }
    expect(used.length).toBeGreaterThan(30);
  });
});
