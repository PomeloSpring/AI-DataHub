import { create } from 'zustand';
import client from '../api/client';

/**
 * 权限码驱动的前端权限 store。
 * 数据源: GET /roles/current/permissions → { permissions, menus, unrestricted }
 * - permissions: 当前用户角色的权限码集合(如 ontology:generate), admin 为 ['*']
 * - menus: 后端由「权限点注册表」反查出的可访问菜单 key(菜单可见性直接用它)
 * - unrestricted: true = 不做限制(全部放行)
 */
interface PermissionState {
  perms: string[] | null;
  allowedMenus: string[] | null;
  unrestricted: boolean;
  loaded: boolean;
  loadPermissions: () => Promise<void>;
  clear: () => void;
}

export const usePermissionStore = create<PermissionState>((set) => ({
  perms: null,
  allowedMenus: null,
  unrestricted: true,
  loaded: false,

  loadPermissions: async () => {
    try {
      const { data } = await client.get('/roles/current/permissions');
      set({
        perms: data.permissions || [],
        allowedMenus: data.menus || [],
        unrestricted: !!data.unrestricted,
        loaded: true,
      });
    } catch {
      // 加载失败时不限制(安全降级, API 层仍有后端中间件兜底)
      set({ perms: null, allowedMenus: null, unrestricted: true, loaded: true });
    }
  },

  clear: () => set({ perms: null, allowedMenus: null, unrestricted: true, loaded: false }),
}));

/** 权限码匹配(支持 * 与 module:* 通配) */
function matchPerm(perms: string[], permCode: string): boolean {
  if (perms.includes('*') || perms.includes(permCode)) return true;
  const module = permCode.split(':', 1)[0];
  return perms.includes(`${module}:*`);
}

/**
 * 判断菜单是否对当前用户可见。
 * - 未加载完成 / unrestricted → 全部可见
 * - menuKey 在 allowedMenus 中 → 可见
 */
export function isMenuAllowed(menuKey: string): boolean {
  const { allowedMenus, unrestricted, loaded } = usePermissionStore.getState();
  if (!loaded || unrestricted) return true;
  return allowedMenus?.includes(menuKey) ?? true;
}

/**
 * 判断当前用户是否拥有某权限码(按钮/操作显隐用)。
 * - 未加载完成 / unrestricted → 放行
 */
export function hasPerm(permCode: string): boolean {
  const { perms, unrestricted, loaded } = usePermissionStore.getState();
  if (!loaded || unrestricted) return true;
  return matchPerm(perms ?? [], permCode);
}
