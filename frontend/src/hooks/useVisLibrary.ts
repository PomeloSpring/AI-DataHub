import { useEffect } from 'react';
import { create } from 'zustand';
import { fetchVisComponents, type VisComponent } from '@/api/visLibrary';

interface LibraryState {
  items: VisComponent[]; loading: boolean; error: string | null; loaded: boolean;
  refresh: () => Promise<void>;
}
const useLibrary = create<LibraryState>((set, get) => ({
  items: [], loading: false, error: null, loaded: false,
  refresh: async () => {
    if (get().loading) return;
    set({ loading: true, error: null });
    try {
      set({ items: await fetchVisComponents(true), loaded: true });
    } catch (error) {
      set({ error: error instanceof Error && error.message.startsWith('字模接口')
        ? error.message : '字模库加载失败，请重试' });
    } finally { set({ loading: false }); }
  },
}));
export function useVisLibrary(includeInactive = false) {
  const state = useLibrary();
  useEffect(() => { if (!state.loaded && !state.error) void state.refresh(); }, [state.loaded, state.error, state.refresh]);
  return { ...state, items: includeInactive ? state.items : state.items.filter(c => c.is_active) };
}
