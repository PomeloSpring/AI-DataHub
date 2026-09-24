import { create } from 'zustand';
import client from '../api/client';
import { useWorkspaceStore } from './workspaceStore';
import { useDashboardStore } from './dashboardStore';
import { useChatStore } from './chatStore';
import { usePermissionStore } from './permissionStore';

function clearUserContext() {
  useWorkspaceStore.getState().reset();
  useDashboardStore.getState().reset();
  useChatStore.getState().reset();
  usePermissionStore.getState().clear();
  localStorage.removeItem('currentWorkspace');
}

interface User {
  id: number;
  username: string;
  role: string;
}

interface AuthState {
  token: string | null;
  user: User | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  updateUser: (updates: Partial<User>) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem('token'),
  user: JSON.parse(localStorage.getItem('user') || 'null'),
  login: async (username, password) => {
    const { data } = await client.post('/auth/login', { username, password });
    localStorage.setItem('token', data.access_token);
    if (data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token);
    localStorage.setItem('user', JSON.stringify(data.user));
    clearUserContext();
    set({ token: data.access_token, user: data.user });
  },
  logout: () => {
    localStorage.removeItem('token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user');
    clearUserContext();
    set({ token: null, user: null });
  },
  updateUser: (updates) => {
    set((state) => {
      if (!state.user) return state;
      const updated = { ...state.user, ...updates };
      localStorage.setItem('user', JSON.stringify(updated));
      return { user: updated };
    });
  },
}));
