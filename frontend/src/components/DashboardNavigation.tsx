import { useEffect, useMemo, useState } from 'react';
import { ListTree, Search, ChartNoAxesCombined } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import client from '@/api/client';
import type { Dashboard } from '@/stores/dashboardStore';

type MenuNode = { id: number; name: string; page_id?: number; children?: MenuNode[] };
export function useDashboardNavigation(workspaceId: number, dashboards: Dashboard[], currentId?: number, onSelect?: (id: number) => void) {
  const [tree, setTree] = useState<MenuNode[]>([]);
  const [error, setError] = useState('');
  const [ready, setReady] = useState(false);
  const [retry, setRetry] = useState(0);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  useEffect(() => { setOpen(false); setSearch(''); }, [workspaceId]);
  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    setTree([]); setError(''); setReady(false);
    client.get(`/admin/menu-tree?workspace_id=${workspaceId}`).then(({ data }) => {
      if (!Array.isArray(data)) throw new Error('目录格式错误');
      if (!cancelled) setTree(data);
    }).catch(() => { if (!cancelled) setError('看板目录加载失败，请重试'); })
      .finally(() => { if (!cancelled) setReady(true); });
    return () => { cancelled = true; };
  }, [workspaceId, retry]);
  const groups = useMemo(() => {
    const result: { name: string; dashboards: Dashboard[] }[] = [];
    const seen = new Set<number>();
    const walk = (nodes: MenuNode[], name: string) => {
      const leaves: Dashboard[] = [];
      for (const node of nodes) {
        const dashboard = dashboards.find(d => d.id === Number(node.page_id));
        if (dashboard && !seen.has(dashboard.id)) { leaves.push(dashboard); seen.add(dashboard.id); }
        if (node.children?.length) walk(node.children, name ? `${name} / ${node.name}` : node.name);
      }
      if (leaves.length) result.push({ name: name || '数据看板', dashboards: leaves });
    };
    walk(tree, '');
    const ungrouped = dashboards.filter(d => !seen.has(d.id));
    if (ungrouped.length) result.push({ name: tree.length ? '其他看板' : '数据看板', dashboards: ungrouped });
    return result;
  }, [tree, dashboards]);
  const select = (id: number) => { setOpen(false); onSelect?.(id); };
  const siblings = groups.find(group => group.dashboards.some(d => d.id === currentId))?.dashboards || [];
  const directory = <Dialog open={open} onOpenChange={setOpen}>
    <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" aria-label="打开看板目录" title="看板目录" onClick={() => setOpen(true)}><ListTree className="h-4 w-4" /></Button>
    <DialogContent className="left-0 top-0 h-[100dvh] w-[min(360px,90vw)] max-w-none translate-x-0 translate-y-0 rounded-none flex flex-col">
      <DialogHeader><DialogTitle>数据看板</DialogTitle><DialogDescription>浏览当前工作空间已启用的看板。</DialogDescription></DialogHeader>
      <div className="relative"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input aria-label="搜索看板" placeholder="搜索当前工作空间看板" value={search} onChange={e => setSearch(e.target.value)} className="pl-9" /></div>
      <nav className="min-h-0 flex-1 space-y-5 overflow-auto" aria-label="看板目录">
        {!ready ? <p className="text-sm text-muted-foreground">正在加载目录…</p> : error ? <div role="alert">{error}<Button variant="link" onClick={() => setRetry(v => v + 1)}>重试</Button></div> : groups.map(group => {
          const matches = group.dashboards.filter(d => d.name.toLowerCase().includes(search.toLowerCase()));
          return matches.length ? <div key={group.name}><p className="mb-2 px-2 text-xs font-medium text-muted-foreground">{group.name}</p>{matches.map(d =>
            <button key={d.id} aria-current={d.id === currentId ? 'page' : undefined} className={`flex w-full items-center gap-2 rounded-md px-3 py-2.5 text-left text-sm ${d.id === currentId ? 'bg-primary/10 text-primary' : 'hover:bg-muted'}`} onClick={() => select(d.id)}><ChartNoAxesCombined className="h-4 w-4 shrink-0" /><span className="truncate">{d.name}</span></button>)}</div> : null;
        })}
        {ready && !error && !groups.some(g => g.dashboards.some(d => d.name.toLowerCase().includes(search.toLowerCase()))) && <p className="p-3 text-sm text-muted-foreground">没有匹配的可用看板</p>}
      </nav>
    </DialogContent>
  </Dialog>;
  const tabs = !error && ready && siblings.length > 1 ? <nav aria-label="同组看板" className="flex shrink-0 gap-4 overflow-x-auto border-b px-4 sm:px-6">
    {siblings.map(d => <button key={d.id} aria-current={d.id === currentId ? 'page' : undefined} onClick={() => select(d.id)} className={`shrink-0 border-b-2 py-2 text-sm ${d.id === currentId ? 'border-primary font-medium text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>{d.name}</button>)}
  </nav> : null;
  return { directory, tabs, error };
}
