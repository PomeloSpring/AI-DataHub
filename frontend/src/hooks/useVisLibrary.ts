import { useEffect, useMemo } from 'react';
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
      // 清单一次取全（含下架与禁用），可见性在各消费口按口径过滤：
      // 管理页要能把禁用的字模列出来再启用，渲染/选择面则只认在用字模
      set({ items: await fetchVisComponents(true, true), loaded: true });
    } catch (error) {
      set({ error: error instanceof Error && error.message.startsWith('字模接口')
        ? error.message : '字模库加载失败，请重试' });
    } finally { set({ loading: false }); }
  },
}));
export function useVisLibrary(includeInactive = false, includeDisabled = false) {
  const state = useLibrary();
  useEffect(() => { if (!state.loaded && !state.error) void state.refresh(); }, [state.loaded, state.error, state.refresh]);
  // 默认口径 = 在用字模（未下架且未禁用）：禁用后不得被任何选用面拿到（ui-vis-library-style §1）
  const items = useMemo(() => state.items.filter(c =>
    (includeInactive || !!c.is_active) && (includeDisabled || c.is_enabled !== 0)),
  [includeInactive, includeDisabled, state.items]);
  return { ...state, items };
}
