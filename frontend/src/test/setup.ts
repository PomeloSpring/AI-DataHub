import '@testing-library/jest-dom/vitest'
import { beforeEach } from 'vitest'
import i18n from '@/i18n'
import zhWorkspace from '@/i18n/locales/zh-CN/workspace.json'
import enWorkspace from '@/i18n/locales/en-US/workspace.json'
import zhChat from '@/i18n/locales/zh-CN/chat.json'
import enChat from '@/i18n/locales/en-US/chat.json'
import zhDashboard from '@/i18n/locales/zh-CN/dashboard.json'
import enDashboard from '@/i18n/locales/en-US/dashboard.json'
import zhData from '@/i18n/locales/zh-CN/data.json'
import enData from '@/i18n/locales/en-US/data.json'
import zhReport from '@/i18n/locales/zh-CN/report.json'
import enReport from '@/i18n/locales/en-US/report.json'

// i18n 测试口径：懒加载命名空间在测试里同步预载（避免异步加载竞态），
// 新增命名空间时在此登记；每个用例前复位 zh-CN，防语言切换用例串扰。
i18n.addResourceBundle('zh-CN', 'workspace', zhWorkspace, true, true)
i18n.addResourceBundle('en-US', 'workspace', enWorkspace, true, true)
i18n.addResourceBundle('zh-CN', 'chat', zhChat, true, true)
i18n.addResourceBundle('en-US', 'chat', enChat, true, true)
i18n.addResourceBundle('zh-CN', 'dashboard', zhDashboard, true, true)
i18n.addResourceBundle('en-US', 'dashboard', enDashboard, true, true)
i18n.addResourceBundle('zh-CN', 'data', zhData, true, true)
i18n.addResourceBundle('en-US', 'data', enData, true, true)
i18n.addResourceBundle('zh-CN', 'report', zhReport, true, true)
i18n.addResourceBundle('en-US', 'report', enReport, true, true)
beforeEach(async () => {
  await i18n.changeLanguage('zh-CN')
})

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = String(value); },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { store = {}; },
    get length() { return Object.keys(store).length; },
    key: (index: number) => Object.keys(store)[index] ?? null,
  };
})();
Object.defineProperty(window, 'localStorage', { value: localStorageMock });

// Mock matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// Mock ResizeObserver
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Object.defineProperty(window, 'ResizeObserver', {
  writable: true,
  value: ResizeObserverMock,
});

// Mock IntersectionObserver
class IntersectionObserverMock {
  constructor() {}
  observe() {}
  unobserve() {}
  disconnect() {}
}
Object.defineProperty(window, 'IntersectionObserver', {
  writable: true,
  value: IntersectionObserverMock,
});

// Mock window.scrollTo
Object.defineProperty(window, 'scrollTo', {
  writable: true,
  value: () => {},
});

// jsdom 未实现 Pointer Capture / scrollIntoView —— Radix Select/Popover 交互依赖它们，
// 缺失时 userEvent 点开下拉报 target.hasPointerCapture is not a function，选项永不渲染
Element.prototype.hasPointerCapture = () => false;
Element.prototype.setPointerCapture = () => {};
Element.prototype.releasePointerCapture = () => {};
Element.prototype.scrollIntoView = () => {};

// jsdom 同样未实现 Element.prototype.scrollBy/scrollTo（真实浏览器有）：横向切换条/滚动容器
// 靠它做最小位移滚动，缺失时挂载即抛 TypeError，会把“环境缺口”误报成组件缺陷。
// 要断言滚动量的用例自行覆盖成 spy 或注入几何值（见 DashboardNavOverflow.test.tsx）。
Element.prototype.scrollBy = () => {};
Element.prototype.scrollTo = () => {};

// Suppress console.error in tests (optional, comment out for debugging)
// const originalError = console.error;
// console.error = (...args: any[]) => {
//   if (typeof args[0] === 'string' && args[0].includes('Warning:')) return;
//   originalError(...args);
// };
