import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { applyDocumentBrand } from '../lib/brand';

export type ThemeId = 'dark' | 'light' | 'tech' | 'finance' | 'bento' | 'glass' | 'ainative' | 'medical' | 'datafoundry';

/** Whether a theme uses a dark color scheme */
export const IS_DARK: Record<ThemeId, boolean> = {
  dark: true,
  light: false,
  tech: true,
  finance: true,
  bento: false,
  glass: true,
  ainative: true,
  medical: false,
  datafoundry: false,
};

interface ThemeState {
  theme: ThemeId;
  isDark: boolean;
  setTheme: (t: ThemeId) => void;
  toggle: () => void;
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      // 默认主题:亮色(仅影响未持久化过主题的用户,已保存的选择由 persist 恢复)
      theme: 'light',
      isDark: false,
      setTheme: (t) => set({ theme: t, isDark: IS_DARK[t] }),
      toggle: () => {
        const cur = get().theme;
        const next = cur === 'dark' ? 'light' : 'dark';
        set({ theme: next, isDark: IS_DARK[next] });
      },
    }),
    { name: 'chatbi-theme' }
  )
);

/** Apply theme class to <html>. Called from Layout. */
export function applyTheme(theme: ThemeId) {
  const root = document.documentElement;
  root.classList.remove('light', 'tech', 'finance', 'bento', 'glass', 'ainative', 'medical', 'datafoundry');
  if (theme !== 'dark') {
    root.classList.add(theme);
  }
  // 浏览器标签图标与页内品牌方块同源（都读 --primary）：主题类刚落盘就按新主色重算 favicon，
  // 否则换主题后侧栏跟着变、标签图标仍停在旧配色（实现唯一入口 lib/brand.applyDocumentBrand）。
  applyDocumentBrand();
}
