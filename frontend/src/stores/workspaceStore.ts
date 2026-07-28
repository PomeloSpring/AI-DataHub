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

  loadWorkspaces: () => Promise<void>;
  setWorkspace: (id: number) => void;
  getDefaultWorkspaceId: () => number;
}

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  workspaces: [],
  currentWorkspaceId: 0,
  loading: false,

  loadWorkspaces: async () => {
    set({ loading: true });
    try {
      const { data } = await client.get('/workspaces');
      const workspaces = data || [];
      set({ workspaces });

      // If no current workspace selected, pick from localStorage or default
      if (!get().currentWorkspaceId && workspaces.length > 0) {
        const savedId = localStorage.getItem('currentWorkspace');
        const saved = savedId ? workspaces.find((w: Workspace) => w.id === Number(savedId)) : null;
        const ws = saved || workspaces.find((w: Workspace) => w.user_default) || workspaces[0];
        set({ currentWorkspaceId: ws.id });
        localStorage.setItem('currentWorkspace', String(ws.id));
      }
    } catch (error) {
      console.error('Failed to load workspaces:', error);
    } finally {
      set({ loading: false });
    }
  },

  setWorkspace: (id: number) => {
    set({ currentWorkspaceId: id });
    localStorage.setItem('currentWorkspace', String(id));
  },

  getDefaultWorkspaceId: () => {
    const { workspaces, currentWorkspaceId } = get();
    if (currentWorkspaceId) return currentWorkspaceId;
    const savedId = localStorage.getItem('currentWorkspace');
    if (savedId) return Number(savedId);
    if (workspaces.length > 0) {
      const ws = workspaces.find((w) => w.user_default) || workspaces[0];
      return ws.id;
    }
    return 1; // fallback
  },
}));
