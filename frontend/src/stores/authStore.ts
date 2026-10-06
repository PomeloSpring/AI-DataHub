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
  /** 身份显示以服务端为准: 按当前 token 拉 /users/me 校正 user(防多 tab/多账号串号) */
  refreshUser: () => Promise<void>;
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
    // 身份显示以服务端为准: 登录后立即按新 token 校正(旧 localStorage user 串号不再残留)
    await useAuthStore.getState().refreshUser();
  },
  logout: () => {
    localStorage.removeItem('token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user');
    clearUserContext();
    set({ token: null, user: null });
  },
  refreshUser: async () => {
    try {
      const { data } = await client.get('/users/me');
      if (!data?.id) return;
      // 归一化身份形状: /users/me 返回 user_role, 前端各处消费 role(login 响应同为 role)
      const user = { ...data, role: data.role || data.user_role };
      localStorage.setItem('user', JSON.stringify(user));
      set({ user });
    } catch {
      // 显示层校正失败不阻断(身份鉴权处处以 token 由服务端裁决); 保留现有显示
    }
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

// 跨 tab 身份同步: 请求 token 每次取 localStorage(多 tab 共享),
// 其他 tab 登录/登出后本 tab 的显示身份必须跟随对齐, 否则
// "显示 zhangsan、请求是 admin"式漂移复发
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key === 'token' || e.key === 'user' || e.key === null) {
      const token = localStorage.getItem('token');
      const user = JSON.parse(localStorage.getItem('user') || 'null');
      useAuthStore.setState({ token, user });
      if (token) void useAuthStore.getState().refreshUser();
    }
  });
}
