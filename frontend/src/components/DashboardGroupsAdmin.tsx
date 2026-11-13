import { useState, useEffect, useCallback, useMemo, useRef, Fragment } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { toast } from '@/lib/toast';
import { TOGGLE_ACTIVE_CLASS } from '@/lib/toggleState';
import { Plus, Edit, Trash2, Layers, ArrowUp, ArrowDown, ArrowLeft, ArrowRight, Search, GripVertical } from 'lucide-react';
import client from '@/api/client';
import { useDashboardStore } from '@/stores/dashboardStore';
import { invalidateCatalogCache } from '@/lib/dashboardCatalog';

// 看板组合(仪表盘组)管理: 把若干看板编排为一个组合并按角色分配。
// 组只做目录/轮播编排, 不授予看板可见性(可见性仍由角色看板授权 fail-closed 裁决)。
// 呈现与同页看板卡片同构（卡片网格、只显名称不显裸 id）：组合也是被管理的对象，不是配置表行。
// 顺序即编排的一部分：组合卡上移/下移决定「数据看板」的组合次序，弹窗里「已选看板」的次序决定
// 点组合进哪块板 / 组内切换条左右 / 大屏轮播依次播放（后端按 adh_dashboard_group_items.sort 下发）。

interface Group {
  id: number;
  name: string;
  description: string;
  sort: number;
  dashboard_ids: number[];
  role_ids: number[];
}

export default function DashboardGroupsAdmin() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [roles, setRoles] = useState<Array<{ id: number; display_name: string }>>([]);
  const [edit, setEdit] = useState<Group | 'new' | null>(null);
  const [form, setForm] = useState({ name: '', description: '', dashboard_ids: [] as number[], role_ids: [] as number[] });
  const [deleteTarget, setDeleteTarget] = useState<Group | null>(null);
  const [saving, setSaving] = useState(false);
  // “组合内看板”必须与同页看板列表同一事实源：直接复用看板管理页按当前工作空间加载好的 store，
  // 不再自己发一次 GET /dashboard/（不带 workspace_id 会跨工作空间全量返回，导致清单与看板列表对不上）。
  const dashboards = useDashboardStore(s => s.dashboards);
  const boards = useMemo(
    () => dashboards.map(d => ({ id: d.id, name: d.name, status: d.status })),
    [dashboards]);

  const load = useCallback(async () => {
    try {
      const [g, r] = await Promise.all([
        client.get('/dashboard/groups'),
        client.get('/roles/'),
      ]);
      setGroups(g.data || []);
      setRoles((r.data || []).map((x: any) => ({ id: x.id, display_name: x.display_name || x.name })));
    } catch {
      toast.error('加载看板组合失败');
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  // 写操作统一走 lock + 错因透出：排序失败不能假装已生效（顺序错了用户第一屏就看错板）
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const run = async (action: () => Promise<unknown>) => {
    if (lock.current) return;
    lock.current = true; setBusy(true);
    try { await action(); } catch (e: any) { toast.error(e?.response?.data?.detail || e?.message || '操作失败，请重试'); }
    finally { lock.current = false; setBusy(false); }
  };

  // 组合卡上移/下移: 全量回写顺序(间隔 10)，只发被交换的两条在存量 sort 全为 0 时会排不开。
  const moveGroup = (id: number, delta: number) => run(async () => {
    const ordered = [...groups];
    const index = ordered.findIndex(g => g.id === id);
    const next = index + delta;
    if (index < 0 || next < 0 || next >= ordered.length) return;
    [ordered[index], ordered[next]] = [ordered[next], ordered[index]];
    await client.post('/dashboard/groups/reorder', {
      orders: ordered.map((g, i) => ({ id: g.id, sort: (i + 1) * 10 })),
    });
    invalidateCatalogCache();
    await load();
  });

  // 卡片上只呈现业务名称（id 仅作为关联键）；资源已删除时显式标注，不回退裸 id
  const boardName = useCallback((id: number) => boards.find(b => b.id === id)?.name || '看板已删除', [boards]);
  const roleName = useCallback((id: number) => roles.find(r => r.id === id)?.display_name || '角色已删除', [roles]);

  const openEdit = (g: Group | 'new') => {
    setEdit(g);
    setForm(g === 'new'
      ? { name: '', description: '', dashboard_ids: [], role_ids: [] }
      : { name: g.name, description: g.description || '', dashboard_ids: [...g.dashboard_ids], role_ids: [...g.role_ids] });
  };

  // 角色仍是一览式多选(无顺序语义)；看板改走左右穿梭框(有顺序语义), 见 BoardShuttle
  const toggle = (key: 'role_ids', id: number) => {
    setForm(f => ({
      ...f,
      [key]: f[key].includes(id) ? f[key].filter(x => x !== id) : [...f[key], id],
    }));
  };

  const save = async () => {
    if (!form.name.trim()) { toast.error('请输入组合名称'); return; }
    setSaving(true);
    try {
      // 不随编辑回传 sort: 名称/说明的保存不得把已排好的顺序抬回首位(服务端未传时保留原值)
      const payload = { name: form.name.trim(), description: form.description };
      const gid = edit === 'new'
        ? (await client.post('/dashboard/groups', payload)).data.id
        : (edit as Group).id;
      if (edit !== 'new') await client.put(`/dashboard/groups/${gid}`, payload);
      await Promise.all([
        client.put(`/dashboard/groups/${gid}/items`, { dashboard_ids: form.dashboard_ids }),
        client.put(`/dashboard/groups/${gid}/roles`, { role_ids: form.role_ids }),
      ]);
      toast.success('看板组合已保存');
      setEdit(null);
      invalidateCatalogCache();
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!deleteTarget) return;
    try {
      await client.delete(`/dashboard/groups/${deleteTarget.id}`);
      toast.success('已删除');
      setDeleteTarget(null);
      invalidateCatalogCache();
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="font-semibold flex items-center gap-2"><Layers className="h-4 w-4" />看板组合</h2>
          <p className="text-xs text-muted-foreground">
            把若干看板编排为一个组合并按角色分配；用户在「数据看板」按组合切换浏览/轮播（组内看板仍受角色可见性裁决）。
            卡片顺序与组内看板顺序都是编排结果：上移/下移立即决定用户看到的次序与轮播次序。
          </p>
        </div>
        <Button size="sm" onClick={() => openEdit('new')}><Plus className="h-4 w-4 mr-1" />新建组合</Button>
      </div>

      {groups.length === 0 ? (
        <div className="rounded-xl border border-dashed py-12 text-center text-sm text-muted-foreground">
          暂无组合，新建组合后把看板编排进去并按角色分配，用户才能在「数据看板」看到它们。
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {groups.map((g, gi) => {
            const items = g.dashboard_ids || [];
            const owners = g.role_ids || [];
            // 带序号展示编排顺序：管理员得能在卡上看清“用户点进来先看到哪块板”，而不是只看到一堆名字
            const boardText = items.map((id, i) => `${i + 1}. ${boardName(id)}`).join(' → ');
            const roleText = owners.map(roleName).join('、');
            const isFirst = gi === 0, isLast = gi === groups.length - 1;
            return (
              <article key={g.id} className="flex flex-col overflow-hidden rounded-xl border bg-card transition-shadow hover:shadow-md">
                <div className="flex-1 space-y-3 p-4">
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <h3 className="truncate font-semibold" title={g.name}>{g.name}</h3>
                      <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{g.description || '暂无描述'}</p>
                    </div>
                    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-muted/40 text-muted-foreground"><Layers className="h-4 w-4" /></span>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <Badge variant="outline" title="在「数据看板」里的展示次序">第 {gi + 1} 位</Badge>
                    <Badge variant="secondary">{items.length} 个看板</Badge>
                    {owners.length === 0 ? (
                      <Badge variant="destructive" className="text-xs">未绑角色，用户看不到</Badge>
                    ) : (
                      <Badge variant="outline">{owners.length} 个角色</Badge>
                    )}
                  </div>
                  <p className="line-clamp-2 text-xs text-muted-foreground" title={`按编排顺序（用户点组合先看第 1 块）：${boardText}`}>
                    看板：{boardText || '尚未编排看板'}
                  </p>
                  <p className="line-clamp-1 text-xs text-muted-foreground" title={roleText}>
                    角色：{roleText || '未分配'}
                  </p>
                </div>
                <div className="flex gap-2 border-t p-4 pt-3">
                  <Button size="sm" variant="outline" className="flex-1" aria-label={`编辑组合 ${g.name}`} onClick={() => openEdit(g)}>
                    <Edit className="mr-1 h-3.5 w-3.5" />编辑
                  </Button>
                  {/* 上移/下移 = 组合次序唯一写入口（存量 sort 全为 0，不拖不排就永远只能按 id） */}
                  <Button size="sm" variant="ghost" disabled={busy || isFirst} title={isFirst ? '已排在首位' : '上移（用户在「数据看板」先看它）'}
                    aria-label={`上移组合 ${g.name}`} onClick={() => moveGroup(g.id, -1)}>
                    <ArrowUp className="h-3.5 w-3.5" />
                  </Button>
                  <Button size="sm" variant="ghost" disabled={busy || isLast} title={isLast ? '已排在末位' : '下移'}
                    aria-label={`下移组合 ${g.name}`} onClick={() => moveGroup(g.id, 1)}>
                    <ArrowDown className="h-3.5 w-3.5" />
                  </Button>
                  <Button size="sm" variant="ghost" className="text-destructive" aria-label={`删除组合 ${g.name}`}
                    onClick={() => setDeleteTarget(g)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {/* 新建/编辑 */}
      <Dialog open={!!edit} onOpenChange={open => { if (!open) setEdit(null); }}>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{edit === 'new' ? '新建看板组合' : '编辑看板组合'}</DialogTitle>
            <DialogDescription>选择组合内的看板（并给它们排序）与可看到该组合的角色。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>名称 *</Label>
              <Input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="如: 经营日报" />
            </div>
            <div className="space-y-2">
              <Label>说明</Label>
              <Textarea rows={2} value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
            </div>
            <div className="space-y-2">
              <Label>组合内看板</Label>
              <p className="text-xs text-muted-foreground">
                左列选中后用 <ArrowRight className="inline h-3 w-3" /> 加入右列（可多选、Shift 连选、双击直接加入），右列拖动或 ↑↓ 调位；
                排在第 1 位的那块 = 用户点组合卡先进入的看板，同时也是大屏轮播的首块板。
              </p>
              <BoardShuttle
                key={edit === null ? 'closed' : edit === 'new' ? 'new' : edit.id}
                boards={boards}
                selected={form.dashboard_ids}
                onChange={dashboard_ids => setForm(f => ({ ...f, dashboard_ids }))}
              />
              <p className="text-xs text-muted-foreground">浏览/轮播仅展示状态为「启用」的看板；设计中/已关闭的看板入组后不会出现在数据看板页。</p>
            </div>
            <div className="space-y-2">
              <Label>可看到该组合的角色</Label>
              <div className="max-h-32 overflow-y-auto border rounded p-2 space-y-1">
                {roles.map(r => (
                  <label key={r.id} className="flex items-center gap-2 text-sm cursor-pointer">
                    <input
                      type="checkbox" className="h-3.5 w-3.5"
                      checked={form.role_ids.includes(r.id)}
                      onChange={() => toggle('role_ids', r.id)}
                    />
                    {r.display_name}
                  </label>
                ))}
                {roles.length === 0 && <p className="text-xs text-muted-foreground">暂无角色</p>}
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEdit(null)}>取消</Button>
            <Button onClick={save} disabled={saving}>{saving ? '保存中...' : '保存'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <Dialog open={!!deleteTarget} onOpenChange={open => { if (!open) setDeleteTarget(null); }}>
        <DialogContent>
          <DialogHeader><DialogTitle>确认删除组合</DialogTitle></DialogHeader>
          <p className="text-sm">删除 "{deleteTarget?.name}" 不会删除其中的看板。</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={remove}>删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

interface ShuttleBoard { id: number; name: string; status?: string }

const PANE_CLS = "flex min-h-0 flex-1 flex-col rounded border bg-background";
const PANE_BODY_CLS = "min-h-[13rem] flex-1 space-y-1 overflow-y-auto p-1.5";
const ROW_CLS = "flex items-center gap-1 rounded px-1 py-1 text-sm";
const ICON_BTN_CLS = "shrink-0 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40";
// 选中态走 lib/toggleState 那一份口径（底色 tint + 文字色 + 字重 + 描边，hover 同色系）：
// 穿梭框里“选中了哪几行”直接决定下一次 <> 搬走谁，看不清就是误操作。
const pickCls = (on: boolean) =>
  `min-w-0 flex-1 truncate rounded px-1 py-0.5 text-left text-sm ${on ? TOGGLE_ACTIVE_CLASS : 'hover:bg-muted'}`;

/**
 * 组合内看板的左右穿梭框：左列 = 可选，右列 = 已选且行序就是展示/轮播次序。
 *
 * 为什么不再用一排 checkbox：勾选的“先后”对用户不可见，排完也看不出结果，
 * 而组内顺序是编排事实（决定点组合先看哪块、轮播第几块），必须给一个显式的有序清单。
 * 拖拽之外保留 ↑↓：触屏/键盘用户同一条路可走，拖拽不是唯一通路。
 */
function BoardShuttle({ boards, selected, onChange }: {
  boards: ShuttleBoard[]; selected: number[]; onChange: (next: number[]) => void;
}) {
  const [query, setQuery] = useState('');
  const [leftSel, setLeftSel] = useState<number[]>([]);
  const [rightSel, setRightSel] = useState<number[]>([]);
  const [drag, setDrag] = useState<{ from: number; over: number } | null>(null);
  const anchor = useRef<number | null>(null);

  const chosen = useMemo(() => new Set(selected), [selected]);
  const available = useMemo(() => {
    const q = query.trim().toLowerCase();
    return boards.filter(b => !chosen.has(b.id) && (!q || b.name.toLowerCase().includes(q)));
  }, [boards, chosen, query]);
  const info = useCallback((id: number) => boards.find(b => b.id === id), [boards]);
  // 已选但看板库里查不到的 id 不得静默丢（保存等于删成员）：仍列在右列并标出已删除
  const label = useCallback((id: number) => info(id)?.name || '看板已删除', [info]);

  /**
   * 多选：普通点击在当前已选集合里切换一项，Shift+点击在可见清单里连选。
   * visibleIds 与 current 必须分开传：前者只管“范围”（行序），后者管“已选”（成员）。
   * 拿 visibleIds 当已选集合会把“本列有哪几行”误当成“选中了哪几行”（点第2行却点亮第1行）。
   */
  const toggleSel = (
    id: number, shift: boolean, visibleIds: number[], current: number[], setter: (next: number[]) => void,
  ) => {
    if (shift && anchor.current !== null) {
      const a = visibleIds.indexOf(anchor.current), b = visibleIds.indexOf(id);
      if (a >= 0 && b >= 0) {
        setter(visibleIds.slice(Math.min(a, b), Math.max(a, b) + 1));
        return;
      }
    }
    anchor.current = id;
    setter(current.includes(id) ? current.filter(x => x !== id) : [...current, id]);
  };

  // 加入右列按左列可见顺序追加（而不是点击乱序）：新成员排在末尾, 位置再由拖拽/↑↓ 定
  const addPicked = () => {
    const ids = available.filter(b => leftSel.includes(b.id)).map(b => b.id);
    if (!ids.length) return;
    onChange([...selected, ...ids]);
    setLeftSel([]);
  };
  const removePicked = () => {
    if (!rightSel.length) return;
    onChange(selected.filter(id => !rightSel.includes(id)));
    setRightSel([]);
  };
  const move = (index: number, delta: number) => {
    const next = [...selected];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };
  // 拖拽落点：先抽出再插入（下标向下移动一位时目标要减 1，否则会把自插到自己后面）
  const dropAt = (target: number) => {
    if (!drag) return;
    const next = [...selected];
    const [moved] = next.splice(drag.from, 1);
    next.splice(target > drag.from ? target - 1 : target, 0, moved);
    onChange(next);
    setDrag(null);
  };

  return <div className="flex flex-col gap-2 sm:flex-row sm:items-stretch">
    <div className={PANE_CLS}>
      <div className="shrink-0 space-y-1.5 border-b p-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-2 h-3.5 w-3.5 text-muted-foreground" />
          {/* 改筛法时清掉左列选中：“选了又摘掉过滤条件”看不见的高亮会让 → 搬出意外内容 */}
          <Input aria-label="搜索可选看板" placeholder="搜索看板名称…" className="h-8 pl-7 text-xs"
            value={query} onChange={e => { setQuery(e.target.value); setLeftSel([]); }} />
        </div>
        <p className="text-[11px] text-muted-foreground">可选 {available.length} 个·已选 {selected.length} 个</p>
      </div>
      <ul role="group" aria-label="可选看板" className={PANE_BODY_CLS}>
        {available.map(b => <li key={b.id} className={ROW_CLS} onDoubleClick={() => onChange([...selected, b.id])}>
          <button type="button" className={pickCls(leftSel.includes(b.id))} title={b.name}
            aria-pressed={leftSel.includes(b.id)} aria-label={`选择看板 ${b.name}`}
            onClick={e => toggleSel(b.id, e.shiftKey, available.map(x => x.id), leftSel, setLeftSel)}>
            <span className="block truncate">{b.name}</span>
          </button>
          {b.status && b.status !== 'enabled' && <Badge variant="outline" className="shrink-0 text-[10px]">{b.status === 'designing' ? '设计中' : b.status === 'closed' ? '已关闭' : b.status}</Badge>}
        </li>)}
        {!available.length && <li className="px-2 py-6 text-center text-xs text-muted-foreground">
          {query ? '没有匹配的看板，可换个关键词' : '全部看板都已在右侧'}
        </li>}
      </ul>
    </div>

    <div className="flex shrink-0 flex-row items-center justify-center gap-2 sm:flex-col">
      <Button type="button" size="sm" variant="outline" className="h-8 w-9 p-0" disabled={!leftSel.length}
        title={leftSel.length ? `加入右列（已选 ${leftSel.length} 个）` : '先在左列选中看板'}
        aria-label="把左列选中的看板加入组合" onClick={addPicked}><ArrowRight className="h-4 w-4" /></Button>
      <Button type="button" size="sm" variant="outline" className="h-8 w-9 p-0" disabled={!rightSel.length}
        title={rightSel.length ? `移出组合（已选 ${rightSel.length} 个）` : '先在右列选中看板'}
        aria-label="把右列选中的看板移出组合" onClick={removePicked}><ArrowLeft className="h-4 w-4" /></Button>
    </div>

    <div className={PANE_CLS}>
      <div className="shrink-0 border-b p-2">
        <p className="text-xs font-medium">已选顺序（拖动或用 ↑↓ 调位）</p>
        <p className="text-[11px] text-muted-foreground">第 1 位 = 用户点组合卡先进入的看板</p>
      </div>
      {/* 容器兼作“落到末尾”的接盘区（行上的 dragover 会 stopPropagation, 漏到容器就是“拖到列表下方”） */}
      <ul role="group" aria-label="组合内看板顺序" className={PANE_BODY_CLS}
        onDragOver={e => { if (drag) { e.preventDefault(); setDrag(d => (d ? { ...d, over: selected.length } : d)); } }}
        onDrop={e => { if (drag) { e.preventDefault(); dropAt(selected.length); } }}>
        {selected.map((id, i) => <Fragment key={id}>
          {drag?.over === i && <li aria-hidden className="h-0.5 rounded bg-primary" />}
          <li className={`${ROW_CLS} ${drag?.from === i ? 'opacity-50' : ''}`}
            onDragOver={e => { if (!drag) return; e.preventDefault(); e.stopPropagation(); setDrag(d => (d ? { ...d, over: i } : d)); }}
            onDrop={e => { e.preventDefault(); e.stopPropagation(); dropAt(i); }}
            onDoubleClick={() => onChange([...selected.filter(x => x !== id), id])}>
            <span draggable className="shrink-0 cursor-grab active:cursor-grabbing" title="拖动调位"
              onDragStart={e => { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(i)); setDrag({ from: i, over: i }); }}
              onDragEnd={() => setDrag(null)}>
              <GripVertical className="h-4 w-4 text-muted-foreground" />
            </span>
            <button type="button" className={pickCls(rightSel.includes(id))} title={label(id)}
              aria-pressed={rightSel.includes(id)} aria-label={`选择看板 ${label(id)}`}
              onClick={e => toggleSel(id, e.shiftKey, selected, rightSel, setRightSel)}>
              <span className="text-xs text-muted-foreground">{i + 1}.</span> {label(id)}
            </button>
            {i === 0 && <Badge variant="outline" className="shrink-0 text-[10px]">先看</Badge>}
            {info(id)?.status && info(id)!.status !== 'enabled' && <Badge variant="outline" className="shrink-0 text-[10px]">{info(id)!.status === 'designing' ? '设计中' : '已关闭'}</Badge>}
            <button type="button" className={ICON_BTN_CLS} title="上移" disabled={i === 0}
              aria-label={`上移看板 ${label(id)}`} onClick={() => move(i, -1)}><ArrowUp className="h-3.5 w-3.5" /></button>
            <button type="button" className={ICON_BTN_CLS} title="下移" disabled={i === selected.length - 1}
              aria-label={`下移看板 ${label(id)}`} onClick={() => move(i, 1)}><ArrowDown className="h-3.5 w-3.5" /></button>
          </li>
        </Fragment>)}
        {drag?.over === selected.length && selected.length > 0 && <li aria-hidden className="h-0.5 rounded bg-primary" />}
        {!selected.length && <li className="px-2 py-6 text-center text-xs text-muted-foreground">
          还没有选看板：左列挑好后点 →
        </li>}
      </ul>
    </div>
  </div>;
}
