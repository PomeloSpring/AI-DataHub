import type { Dashboard } from '@/stores/dashboardStore';

/** 切换条只需轻量字段(组目录行即可, 无需完整 Dashboard) */
type DashboardNavItem = Pick<Dashboard, 'id' | 'name'> & Partial<Pick<Dashboard, 'status' | 'is_default'>>;

/**
 * 同组看板切换条。
 * 展示范围 = 当前仪表盘组内、服务端按用户角色授权过滤后的可见集
 * (fail-closed, admin 全可见; 组只做编排不授予可见)。
 *
 * 产品决策(2026-10-27)：工作空间看板页**不再提供「看板目录」抽屉**。
 * 侧栏「数据看板」已是全量清单入口，页头切换条 + 组合下拉覆盖了同组/跨组跳转，
 * 抽屉与它们重复，还在页头多占一个图标位。删入口的同时删掉抽屉本体，不留死代码。
 */
export function useDashboardNavigation(dashboards: DashboardNavItem[], currentId?: number, onSelect?: (id: number) => void) {
  // 服务端已按角色可见集过滤并排好序（is_default/sort_order/updated_at），保持传入顺序。
  const visible = dashboards;
  // 同组看板切换：整条做成**分段控件**（凹槽容器 + 选中项浮起），不再让几颗 pill 漂在空白带上。
  // 选中态仍走软高亮（容器底 + 主色字 + 细描边），不用 aria-current="page"：
  // 后者会被主题全局 button[aria-current="page"] 规则(tech/glass/ainative…)涂成厚重色块/发光。
  const tabs = visible.length > 1 ? <nav aria-label="同组看板" className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto rounded-full bg-muted/60 p-1.5 ring-1 ring-border/60 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
    {visible.map(d => <button key={d.id} type="button" title={d.name} aria-current={d.id === currentId ? 'true' : undefined}
      onClick={() => onSelect?.(d.id)}
      className={`shrink-0 whitespace-nowrap rounded-full px-4 py-1.5 text-[13px] transition-colors sm:text-sm ${
        d.id === currentId
          ? 'bg-background font-medium text-primary shadow-sm ring-1 ring-primary/25'
          : 'text-muted-foreground hover:bg-background/70 hover:text-foreground'}`}>{d.name}</button>)}
  </nav> : null;
  return { tabs };
}
