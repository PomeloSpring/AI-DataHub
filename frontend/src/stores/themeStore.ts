import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type ThemeId = 'dark' | 'light' | 'tech' | 'finance' | 'bento' | 'glass' | 'ainative' | 'medical';

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
      theme: 'dark',
      isDark: true,
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
  root.classList.remove('light', 'tech', 'finance', 'bento', 'glass', 'ainative', 'medical');
  if (theme !== 'dark') {
    root.classList.add(theme);
  }
}
