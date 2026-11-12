import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ReadArticle from '../ReadArticle';
import { TooltipProvider } from '@/components/ui/tooltip';
import { useAuthStore } from '@/stores/authStore';
import { usePermissionStore } from '@/stores/permissionStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { DEFAULT_MOTION, type MotionSpec } from '@/lib/motion';
import { useMotionPrefStore } from '@/stores/motionPrefStore';
import type { VisComponent } from '@/api/visLibrary';
import client from '@/api/client';

/**
 * 内容详情的正文出现效果（P3）。
 *
 * 三条口径：
 * - **默认零变化**：`reveal.effect='none'` 时一个 class 都不加、也不为挂 class 多插一层 div；
 * - **分段错峰有上限**：长文按标题分段渐入，尾部段落不能等好几秒；
 * - **安全边界不动**：HTML 版式只作用外层容器，绝不往正文注入 `<style>`/animation
 *   （sanitize 白名单禁 `style` 标签，动效不能成为第一个绕开它的理由）。
 */

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const library = vi.hoisted(() => ({ items: [] as VisComponent[], error: null as string | null, loaded: true, refresh: vi.fn() }));
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: () => library }));

const STAGGER: MotionSpec = {
  ...DEFAULT_MOTION,
  enter: { ...DEFAULT_MOTION.enter, durationMs: 700 },
  reveal: { effect: 'sectionStagger', staggerMs: 120 },
};
const FADE: MotionSpec = { ...DEFAULT_MOTION, reveal: { effect: 'fadeIn', staggerMs: 90 } };
const comp = (code: string, name: string, style: MotionSpec, over: Partial<VisComponent> = {}): VisComponent => ({
  id: 1, code, name, category: 'motion_preset', chart_type: null, style_config: style,
  source: 'system', is_builtin: 1, is_active: 1, is_enabled: 1, sort_order: 85, description: '', ...over,
} as VisComponent);

const detail = (over: Record<string, unknown> = {}) => ({
  id: 1, slug: 'monthly', title: '五月运行月报', summary: '', body: '',
  category_id: 2, category_label: '月报', category_icon: '', category_color: '#0891b2',
  tags: [], kind: 'markdown', kind_label: 'Markdown', link_url: '', link_label: '',
  refs: [], pinned: 0, featured: 0, status: 'published', is_live: true, has_pending_change: false,
  review_by: '', published_at: new Date().toISOString(), archived_at: '', view_count: 3,
  author: '平台内容组', updated_by: '', updated_at: '', version: 1, files: [], kb_sync: null,
  ...over,
});

function renderRead(body: Record<string, unknown> = {}) {
  const table: Record<string, unknown> = {
    '/content/categories': { items: [{ id: 2, key: 'monthly', label: '月报', icon: '', color: '#0891b2', sort: 1, is_active: 1 }] },
    '/content/tags': { items: [] },
    '/content/articles/monthly': detail({ body: '## 一、总体\n第一段\n\n## 二、明细\n第二段\n\n## 三、结论\n第三段', ...body }),
    '/content/articles/monthly/related': { items: [] },
    '/content/articles/monthly/releases': { items: [], history_hidden: false },
  };
  vi.mocked(client.get).mockImplementation(((url: string) => {
    if (!(url in table)) return Promise.resolve({ data: {} });
    return Promise.resolve({ data: table[url] });
  }) as never);
  return render(<TooltipProvider><MemoryRouter initialEntries={['/read/monthly']}><Routes>
    <Route path="/read/:slug" element={<ReadArticle />} />
  </Routes></MemoryRouter></TooltipProvider>);
}
/** 正文块 = 打了 reveal class 的那些 div（默认档下应当一个都没有） */
const blocks = (container: HTMLElement) => Array.from(container.querySelectorAll<HTMLElement>('.motion-reveal'));

beforeEach(() => {
  vi.clearAllMocks();
  library.items = [
    comp('mp_default', '默认（现网）', DEFAULT_MOTION, { id: 2, sort_order: 80 }),
    comp('mp_reveal', '分段渐入', STAGGER),
    comp('mp_fade', '整块淡入', FADE),
  ];
  library.error = null;
  useMotionPrefStore.setState({ presetCode: null, override: null, loaded: true, error: '', fetching: false });
  useAuthStore.setState({ user: { id: 1, username: 'carol', role: 'viewer' } } as any);
  usePermissionStore.setState({ loaded: true, unrestricted: false, perms: ['article:read'], allowedMenus: ['home:feed'] });
  useWorkspaceStore.setState({ workspaces: [{ id: 301 } as never], loaded: true, loading: false });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  usePermissionStore.setState({ loaded: false, unrestricted: true, perms: null, allowedMenus: null });
});

describe('内容详情正文出现效果', () => {
  it('默认档：正文没有任何 reveal class，也没多插包裹层（与今天逐字节相同）', async () => {
    const { container } = renderRead();
    await screen.findByRole('heading', { name: '一、总体' });
    expect(blocks(container)).toHaveLength(0);
    expect(container.querySelector('[style*="--motion-reveal-dur"]')).toBeNull();
  });

  it('sectionStagger：按标题分段错峰，第一段不等、后面依次递增', async () => {
    useMotionPrefStore.setState({ presetCode: 'mp_reveal' });
    const { container } = renderRead();
    await screen.findByRole('heading', { name: '一、总体' });
    const found = blocks(container);
    expect(found.length).toBeGreaterThanOrEqual(3);
    expect(found[0].style.animationDelay).toBe('');
    expect(found[1].style.animationDelay).toBe('120ms');
    expect(found[2].style.animationDelay).toBe('240ms');
    // 时长跟着预设走：写死在 :root 的 450ms 不该盖掉这里的 700ms
    expect(found[0].getAttribute('style')).toContain('--motion-reveal-dur: 700ms');
    // 切段只改出现节奏，不得改内容：三个标题与三段正文都得在
    const text = container.textContent || '';
    for (const piece of ['一、总体', '第一段', '二、明细', '第二段', '三、结论', '第三段']) {
      expect(text).toContain(piece);
    }
  });

  it('错峰受 enter.maxStaggerMs 封顶：长文尾部段落不必等好几秒', async () => {
    useMotionPrefStore.setState({ presetCode: 'mp_reveal' });
    const many = Array.from({ length: 12 }, (_, i) => `## 第 ${i + 1} 节\n内容 ${i}`).join('\n\n');
    const { container } = renderRead({ body: many });
    await screen.findByRole('heading', { name: '第 12 节' });
    const delays = blocks(container).map(b => Number.parseInt(b.style.animationDelay || '0', 10));
    expect(Math.max(...delays)).toBeLessThanOrEqual(DEFAULT_MOTION.enter.maxStaggerMs);
    expect(delays[delays.length - 1]).toBe(DEFAULT_MOTION.enter.maxStaggerMs);
  });

  it('fadeIn：整块一起渐显（分段错峰不该混进来）', async () => {
    useMotionPrefStore.setState({ presetCode: 'mp_fade' });
    const { container } = renderRead();
    await screen.findByRole('heading', { name: '一、总体' });
    const found = blocks(container);
    expect(found.length).toBeGreaterThan(0);
    for (const block of found) expect(block.style.animationDelay).toBe('');
  });

  it('HTML 版式只作用外层容器，正文里不出现注入的 <style>', async () => {
    useMotionPrefStore.setState({ presetCode: 'mp_fade' });
    const { container } = renderRead({
      kind: 'html', kind_label: 'HTML 版式',
      body: '<style>body{background:#000}</style><h2>标题</h2><p onclick="alert(1)">正文</p>',
    });
    await screen.findByRole('heading', { name: '标题' });
    const wrapper = container.querySelector<HTMLElement>('.motion-reveal');
    expect(wrapper).not.toBeNull();
    expect(wrapper!.querySelector('style')).toBeNull();
    expect(wrapper!.querySelector('[onclick]')).toBeNull();
    expect(wrapper!.getAttribute('style')).toContain('--motion-reveal-dur');
  });

  it('系统减少动态效果：正文一次性出现（不是"配了没反应"，原因写在偏好卡上）', async () => {
    vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
      matches: /reduced-motion/.test(query), media: query, onchange: null,
      addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
    }) as any);
    useMotionPrefStore.setState({ presetCode: 'mp_reveal' });
    const { container } = renderRead();
    await screen.findByRole('heading', { name: '一、总体' });
    expect(blocks(container)).toHaveLength(0);
  });
});
