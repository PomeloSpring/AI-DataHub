import { useEffect, useMemo, useState } from 'react';
import { ListTree, Search, ChartNoAxesCombined } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import type { Dashboard } from '@/stores/dashboardStore';

/** 目录只需轻量字段(组目录行即可, 无需完整 Dashboard) */
type DashboardNavItem = Pick<Dashboard, 'id' | 'name'> & Partial<Pick<Dashboard, 'status' | 'is_default'>>;

/**
 * 看板目录与同组切换。
 * 展示范围 = 当前仪表盘组内 服务端按用户角色授权过滤后的可见集
 * (fail-closed, admin 全可见; 组只做编排不授予可见), 平铺展示。
 */
export function useDashboardNavigation(workspaceId: number, dashboards: DashboardNavItem[], currentId?: number, onSelect?: (id: number) => void) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  useEffect(() => { setOpen(false); setSearch(''); }, [workspaceId]);
  // 服务端已按角色可见集过滤并排好序（is_default/sort_order/updated_at），保持传入顺序。
  const visible = useMemo(() => dashboards, [dashboards]);
  const select = (id: number) => { setOpen(false); onSelect?.(id); };
  const directory = <Dialog open={open} onOpenChange={setOpen}>
    <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" aria-label="打开看板目录" title="看板目录" onClick={() => setOpen(true)}><ListTree className="h-4 w-4" /></Button>
    <DialogContent className="left-0 top-0 h-[100dvh] w-[min(360px,90vw)] max-w-none translate-x-0 translate-y-0 rounded-none flex flex-col">
      <DialogHeader><DialogTitle>数据看板</DialogTitle><DialogDescription>浏览当前工作空间你看得到的看板。</DialogDescription></DialogHeader>
      <div className="relative"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input aria-label="搜索看板" placeholder="搜索当前工作空间看板" value={search} onChange={e => setSearch(e.target.value)} className="pl-9" /></div>
      <nav className="min-h-0 flex-1 space-y-5 overflow-auto" aria-label="看板目录">
        <div><p className="mb-2 px-2 text-xs font-medium text-muted-foreground">数据看板</p>
          {visible.filter(d => d.name.toLowerCase().includes(search.toLowerCase())).map(d =>
            <button key={d.id} aria-current={d.id === currentId ? 'page' : undefined} className={`flex w-full items-center gap-2 rounded-md px-3 py-2.5 text-left text-sm ${d.id === currentId ? 'bg-primary/10 text-primary' : 'hover:bg-muted'}`} onClick={() => select(d.id)}><ChartNoAxesCombined className="h-4 w-4 shrink-0" /><span className="truncate">{d.name}</span></button>)}
        </div>
        {!visible.some(d => d.name.toLowerCase().includes(search.toLowerCase())) && <p className="p-3 text-sm text-muted-foreground">没有匹配的可用看板</p>}
      </nav>
    </DialogContent>
  </Dialog>;
  const tabs = visible.length > 1 ? <nav aria-label="同组看板" className="flex shrink-0 gap-4 overflow-x-auto border-b px-4 sm:px-6">
    {visible.map(d => <button key={d.id} aria-current={d.id === currentId ? 'page' : undefined} onClick={() => select(d.id)} className={`shrink-0 border-b-2 py-2 text-sm ${d.id === currentId ? 'border-primary font-medium text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>{d.name}</button>)}
  </nav> : null;
  return { directory, tabs, error: '' };
}
