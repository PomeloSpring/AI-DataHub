import { useMemo } from 'react';
import { usePermissionStore } from '@/stores/permissionStore';

/**
 * 模块级一级入口判定（单一口径）：该模块下有任一可见菜单才提供入口。
 * 非响应式上下文（store 动作等）也走这里，不另造第二套前缀匹配。
 */
export const prefixAllowedOf = (
  s: { unrestricted: boolean; allowedMenus: string[] | null },
  prefix: string,
) => {
  if (s.unrestricted) return true;
  return (s.allowedMenus ?? []).some(k => k.startsWith(prefix));
};

/**
 * 侧栏菜单权限的**订阅式**读取（三套 Layout 共用）。
 *
 * 不要用 `isMenuAllowed()` 裸判后再渲染菜单：它读的是 store 快照，且按
 * fail-open 设计在权限接口回来前一律放行 —— 首屏会把无权限的菜单（如「自动化」）
 * 先画出来，权限到位后再抽走，用户看到菜单闪一下消失。
 * 这里订阅 store 并暴露 `ready`：未就绪时调用方渲染骨架而不是全量菜单。
 */
export function useMenuAccess() {
  const loaded = usePermissionStore(s => s.loaded);
  const unrestricted = usePermissionStore(s => s.unrestricted);
  const allowedMenus = usePermissionStore(s => s.allowedMenus);

  return useMemo(() => ({
    /** 权限清单是否已就绪（未就绪 → 不渲染真实菜单，避免闪现） */
    ready: loaded,
    allowed: (menuKey: string) => {
      if (unrestricted) return true;
      return allowedMenus?.includes(menuKey) ?? true;
    },
    /** 模块级一级入口：该模块下有任一可见菜单才提供入口 */
    prefixAllowed: (prefix: string) => prefixAllowedOf({ unrestricted, allowedMenus }, prefix),
  }), [loaded, unrestricted, allowedMenus]);
}
