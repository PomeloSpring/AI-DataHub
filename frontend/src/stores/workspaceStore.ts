import { create } from 'zustand';
import client from '../api/client';

export interface Workspace {
  id: number;
  name: string;
  description: string;
  icon: string;
  color: string;
  owner_id: number;
  owner_name?: string;
  is_default: boolean;
  user_default: boolean;
  role: string;
  created_at: string;
}

interface WorkspaceState {
  workspaces: Workspace[];
  currentWorkspaceId: number;
  loading: boolean;
  loaded: boolean;
  error: string | null;
  reset: () => void;

  loadWorkspaces: () => Promise<void>;
  setWorkspace: (id: number) => void;
  getDefaultWorkspaceId: () => number;
}

let requestVersion = 0;

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  workspaces: [],
  currentWorkspaceId: 0,
  loading: false,
  loaded: false,
  error: null,
  reset: () => { ++requestVersion; set({ workspaces: [], currentWorkspaceId: 0, loading: false, loaded: false, error: null }); },

  loadWorkspaces: async () => {
    const version = ++requestVersion;
    set({ loading: true, error: null });
    try {
      const { data } = await client.get('/workspaces');
      if (version !== requestVersion) return;
      if (!Array.isArray(data)) throw new Error('工作空间接口格式错误');
      const workspaces: Workspace[] = data;
      const current = get().currentWorkspaceId;
      set({ workspaces, loaded: true });
      if (current && !workspaces.some(w => w.id === current)) {
        set({ currentWorkspaceId: 0, error: '当前工作空间已不可访问，请重新选择' });
        return;
      }

      // If no current workspace selected, pick from localStorage or default
      if (!get().currentWorkspaceId && workspaces.length > 0) {
        const savedId = localStorage.getItem('currentWorkspace');
        const saved = savedId ? workspaces.find((w: Workspace) => w.id === Number(savedId)) : null;
        const ws = saved || workspaces.find((w: Workspace) => w.user_default) || workspaces[0];
        set({ currentWorkspaceId: ws.id });
        localStorage.setItem('currentWorkspace', String(ws.id));
      }
    } catch (error) {
      console.error('工作空间加载失败', error);
      if (version === requestVersion) set({ loaded: true, error: '工作空间加载失败，请重试' });
    } finally {
      if (version === requestVersion) set({ loading: false });
    }
  },

  setWorkspace: (id: number) => {
    set({ currentWorkspaceId: id, error: null });
    localStorage.setItem('currentWorkspace', String(id));
  },

  getDefaultWorkspaceId: () => {
    const { workspaces, currentWorkspaceId } = get();
    if (workspaces.some(w => w.id === currentWorkspaceId)) return currentWorkspaceId;
    const savedId = Number(localStorage.getItem('currentWorkspace'));
    if (workspaces.some(w => w.id === savedId)) return savedId;
    if (workspaces.length > 0) {
      const ws = workspaces.find((w) => w.user_default) || workspaces[0];
      return ws.id;
    }
    return 0;
  },
}));
