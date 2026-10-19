/**
 * 侧栏菜单骨架：权限清单还没回来时的占位。
 *
 * 侧栏菜单按权限码裁决，但权限接口是异步的。未就绪时若照常渲染，会先把
 * 用户**没有**权限的菜单画出来（fail-open），接口回来后整组抽走 —— 表现为
 * 「自动化」等分组闪一下消失。骨架保持与真实菜单同高的行，避免布局跳动。
 */
export function SidebarMenuSkeleton({ rows = 5, collapsed = false }: { rows?: number; collapsed?: boolean }) {
  return (
    <div aria-busy="true" aria-label="菜单加载中" className="space-y-1">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className={`h-9 rounded-lg bg-sidebar-accent/60 animate-pulse ${collapsed ? 'w-10 mx-auto' : 'w-full'}`}
        />
      ))}
    </div>
  );
}
