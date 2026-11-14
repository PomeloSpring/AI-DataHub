// 多语言验收用例（架构设计 §八）：
// 1. 语言切换渲染断言：已迁移页面切 en-US 即时重渲染，切回 zh-CN 恢复；
// 2. 文案常量函数化回归：label 函数/本地化摘要随语言解析，双轨中文口径同源；
// 3. menu.json 工作空间 key 与侧栏消费一一对应（静态护栏）；
// 4. setLocale/getStoredLocale 契约：持久化、非法值显式拒绝、非法存储回默认。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import i18n, { setLocale, getStoredLocale, menuI18nKey, LOCALE_STORAGE_KEY } from '@/i18n';
import zhMenu from '@/i18n/locales/zh-CN/menu.json';
import enMenu from '@/i18n/locales/en-US/menu.json';
import enWorkspace from '@/i18n/locales/en-US/workspace.json';
import Profile from '@/pages/Profile';
import MemorySettings from '@/pages/MemorySettings';
import LanguageMenu from '@/components/LanguageMenu';
import { TooltipProvider } from '@/components/ui/tooltip';
import client from '@/api/client';
import { verdictLabel, severityLabel, treeStatusLabel, triggerLabel } from '@/api/decisions';
import { motionSummary, motionSummaryLocalized, DEFAULT_MOTION } from '@/lib/motion';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));

const mockClient = () => {
  vi.mocked(client.get).mockImplementation(((url: string) => {
    if (url === '/users/me') return Promise.resolve({ data: { id: 1, username: 'tester', status: 'active', role_name: '管理员' } });
    if (url === '/users/me/preferences') return Promise.resolve({ data: { cloud_sync_enabled: true } });
    if (url === '/memory') return Promise.resolve({ data: { fixed_slots: [], floating_entries: [], fixed_slot_defs: [], floating_defs: [], floating_limit: 5, content_max: 300 } });
    return Promise.resolve({ data: [] });
  }) as never);
};

const renderProfile = () => render(
  <MemoryRouter initialEntries={['/ws/7/profile']}>
    <Routes><Route path="/ws/:workspaceId/profile" element={<Profile />} /></Routes>
  </MemoryRouter>,
);

const renderMemory = () => render(
  <MemoryRouter initialEntries={['/ws/7/memory']}>
    <Routes><Route path="/ws/:workspaceId/memory" element={<MemorySettings />} /></Routes>
  </MemoryRouter>,
);

beforeEach(() => { vi.clearAllMocks(); mockClient(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('语言切换渲染断言（消费面即时重渲染）', () => {
  it('Profile：zh→en→zh 标题/卡片文案随语言切换，<html lang> 同步', async () => {
    renderProfile();
    expect(await screen.findByText('个人设置')).toBeInTheDocument();
    expect(screen.getByText('账号信息')).toBeInTheDocument();

    await i18n.changeLanguage('en-US');
    expect(await screen.findByText('Profile')).toBeInTheDocument();
    expect(screen.getByText('Account')).toBeInTheDocument();
    expect(screen.queryByText('个人设置')).not.toBeInTheDocument();
    expect(document.documentElement.lang).toBe('en-US');

    await i18n.changeLanguage('zh-CN');
    expect(await screen.findByText('个人设置')).toBeInTheDocument();
    expect(document.documentElement.lang).toBe('zh-CN');
  });

  it('MemorySettings：标题与固定偏好卡随语言切换', async () => {
    renderMemory();
    expect(await screen.findByText('我的记忆', { selector: 'h1' })).toBeInTheDocument();
    expect(screen.getByText('固定偏好')).toBeInTheDocument();

    await i18n.changeLanguage('en-US');
    expect(await screen.findByText('My memory', { selector: 'h1' })).toBeInTheDocument();
    expect(screen.getByText('Fixed preferences')).toBeInTheDocument();
  });
});

describe('文案常量函数化回归（渲染期/调用期解析，不缓存语言）', () => {
  it('决策 label 函数随语言解析，en 口径与语言包一致', async () => {
    expect(verdictLabel('hit')).toBe('命中');
    expect(severityLabel('critical')).toBe('紧急');
    expect(treeStatusLabel('active')).toBe('已发布');
    expect(triggerLabel('chat_turn')).toBe('智能问数');

    await i18n.changeLanguage('en-US');
    expect(verdictLabel('hit')).toBe(enWorkspace.decision.verdict.hit);
    expect(severityLabel('critical')).toBe(enWorkspace.decision.severity.critical);
    expect(treeStatusLabel('active')).toBe(enWorkspace.decision.treeStatus.active);
    expect(triggerLabel('chat_turn')).toBe(enWorkspace.decision.trigger.chat_turn);
    // 未登记的枚举值回退原值（不静默翻译成别的词）
    expect(verdictLabel('unknown_verdict')).toBe('unknown_verdict');
  });

  it('motionSummaryLocalized 与中文 motionSummary 双轨同源（zh 逐字一致，en 走语言包）', async () => {
    const zh = motionSummary(DEFAULT_MOTION);
    const localizedZh = motionSummaryLocalized(DEFAULT_MOTION);
    expect(localizedZh).toEqual(zh);

    await i18n.changeLanguage('en-US');
    const localizedEn = motionSummaryLocalized(DEFAULT_MOTION);
    expect(localizedEn.map(r => r.label)).toEqual([
      enWorkspace.motion.summary.enter, enWorkspace.motion.summary.chart,
      enWorkspace.motion.summary.transition, enWorkspace.motion.summary.reveal,
    ]);
    expect(localizedEn[1].text).toBe(enWorkspace.motion.summary.chartDefault);
    expect(localizedEn[2].text).toBe(enWorkspace.motion.summary.transitionNone);
    // 编辑器消费的中文口径不受切换影响（未迁移面保持中文，显式可知）
    expect(motionSummary(DEFAULT_MOTION)).toEqual(zh);
  });
});

describe('menu.json 工作空间 key 与侧栏一一对应（静态护栏）', () => {
  it('侧栏消费的 workspace.* key 集合 = 语言包登记集合（zh/en 同构）', () => {
    const src = readFileSync(resolve(__dirname, '../../components/WorkspaceLayout.tsx'), 'utf-8');
    const used = new Set([...src.matchAll(/tMenu\('workspace\.(\w+)'\)/g)].map(m => m[1]));
    expect([...used].sort()).toEqual(Object.keys(zhMenu.workspace).sort());
    expect(Object.keys(enMenu.workspace).sort()).toEqual(Object.keys(zhMenu.workspace).sort());
  });

  it('menu_key 冒号落点号只转显示层（显隐反查口径不动）', () => {
    expect(menuI18nKey('workspace:dashboards')).toBe('workspace.dashboards');
    expect(menuI18nKey('system:users')).toBe('system.users');
  });
});

describe('顶栏语言切换器（2026-11-14 从用户下拉移入顶栏）', () => {
  it('图标按钮开下拉切换语言；aria 随语言、选项原生名不变', async () => {
    render(<TooltipProvider><LanguageMenu /></TooltipProvider>);
    const trigger = screen.getByRole('button', { name: '语言' });
    await userEvent.click(trigger);
    // 原生语言名不随当前语言翻译（切到英文后仍能找到「简体中文」）
    await userEvent.click(await screen.findByText('English'));
    await vi.waitFor(() => expect(i18n.language).toBe('en-US'));
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en-US');
    expect(screen.getByRole('button', { name: 'Language' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Language' }));
    expect(await screen.findByText('简体中文')).toBeInTheDocument();
    await userEvent.click(screen.getByText('简体中文'));
    await vi.waitFor(() => expect(i18n.language).toBe('zh-CN'));
  });
});

describe('setLocale / getStoredLocale 契约', () => {
  beforeEach(() => { localStorage.clear(); });

  it('切换即持久化；非法值显式拒绝（不静默忽略）', async () => {
    setLocale('en-US');
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en-US');
    await vi.waitFor(() => expect(i18n.language).toBe('en-US'));
    expect(() => setLocale('fr-FR' as never)).toThrow(/不支持的语言/);
  });

  it('存储缺失/非法一律回默认 zh-CN（不猜浏览器语言）', () => {
    expect(getStoredLocale()).toBe('zh-CN');
    localStorage.setItem(LOCALE_STORAGE_KEY, 'klingon');
    expect(getStoredLocale()).toBe('zh-CN');
    localStorage.setItem(LOCALE_STORAGE_KEY, 'en-US');
    expect(getStoredLocale()).toBe('en-US');
  });
});