import '@testing-library/jest-dom/vitest'

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
