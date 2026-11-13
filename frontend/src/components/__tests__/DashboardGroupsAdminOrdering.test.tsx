/**
 * 看板组合排序（管理端唯一写入口）。
 *
 * 锁定的契约：
 *  - 组合卡按服务端顺序呈现并显式标出「第 N 位」，上移/下移全量回写 sort（存量 sort 全为 0，
 *    只发被交换的两条排不开）；首位上移、末位下移必须置灰，不做点了没反应的死按钮；
 *  - 组内看板有独立的「已选顺序」清单可上移/下移，保存按该顺序回传 dashboard_ids
 *    （后端按数组下标写 items.sort，浏览端据此决定点组合进哪块板与轮播次序）；
 *  - 编辑名称/说明 **不回传 sort**：服务端未传时保留原值，回传 0 会把已排好的组合抬回首位。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import DashboardGroupsAdmin from '../DashboardGroupsAdmin';
import { useDashboardStore, type Dashboard } from '@/stores/dashboardStore';
import { invalidateCatalogCache, fetchVisibleGroups } from '@/lib/dashboardCatalog';
import { TOGGLE_ACTIVE_CLASS } from '@/lib/toggleState';
import { toast } from '@/lib/toast';
import client from '@/api/client';

const board = (id: number, name: string, status: Dashboard['status'] = 'enabled'): any =>
  ({ id, name, status, description: '', charts: [], workspace_id: 301, owner_id: 1 });

// 服务端已按 sort 出序: 经营日报在前、设备大盘在后（与 id 序相反, 用来证明前端没有自己按 id 重排）
const GROUPS = [
  { id: 11, name: '经营日报', description: '', sort: 10, dashboard_ids: [1, 2], role_ids: [1] },
  { id: 9, name: '设备大盘', description: '', sort: 20, dashboard_ids: [3], role_ids: [1] },
  { id: 12, name: '质量周报', description: '', sort: 30, dashboard_ids: [], role_ids: [1] },
];

let posts: Array<[string, any]>;
let puts: Array<[string, any]>;
let getCalls: string[];

const visibleFetches = () => getCalls.filter(u => u === '/dashboard/groups/visible').length;

beforeEach(() => {
  invalidateCatalogCache();
  posts = []; puts = []; getCalls = [];
  useDashboardStore.setState({
    dashboards: [board(1, '官网用户操作看板'), board(2, '版本统计'), board(3, '设备扫描精度')],
  } as any);
  vi.spyOn(client, 'get').mockImplementation(((url: string) => {
    getCalls.push(url);
    if (url === '/dashboard/groups') return Promise.resolve({ data: GROUPS });
    if (url === '/dashboard/groups/visible') return Promise.resolve({ data: [] });
    if (url === '/roles/') return Promise.resolve({ data: [{ id: 1, display_name: '管理员' }] });
    return Promise.resolve({ data: [] });
  }) as any);
  vi.spyOn(client, 'post').mockImplementation(((url: string, body?: any) => {
    posts.push([url, body]);
    return Promise.resolve({ data: { success: true, id: 99 } });
  }) as any);
  vi.spyOn(client, 'put').mockImplementation(((url: string, body?: any) => {
    puts.push([url, body]);
    return Promise.resolve({ data: { success: true } });
  }) as any);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('组合之间的顺序', () => {
  it('卡面按服务端顺序显示位次，首/末位的越界按钮置灰', async () => {
    render(<DashboardGroupsAdmin />);
    await screen.findByText('经营日报');
    const cards = [...document.querySelectorAll('article')];
    expect(cards.map(c => c.querySelector('h3')!.textContent)).toEqual(['经营日报', '设备大盘', '质量周报']);
    expect(cards[0].textContent).toContain('第 1 位');
    expect(cards[2].textContent).toContain('第 3 位');
    expect((screen.getByRole('button', { name: '上移组合 经营日报' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '下移组合 质量周报' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '上移组合 设备大盘' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('上移第 2 个组合 = 全量回写新顺序，并让浏览端目录回源', async () => {
    render(<DashboardGroupsAdmin />);
    await screen.findByText('经营日报');
    fireEvent.click(screen.getByRole('button', { name: '上移组合 设备大盘' }));

    await waitFor(() => expect(posts.some(([u]) => u === '/dashboard/groups/reorder')).toBe(true));
    const [, body] = posts.find(([u]) => u === '/dashboard/groups/reorder')!;
    expect(body.orders).toEqual([
      { id: 9, sort: 10 }, { id: 11, sort: 20 }, { id: 12, sort: 30 },
    ]);
    // 排序写库后必须重拉目录：本地乐观改序会让「刷新又跳回去」这种不一致没人发现
    await waitFor(() => expect(vi.mocked(client.get).mock.calls.filter(([u]) => u === '/dashboard/groups').length).toBeGreaterThan(1));
  });

  it('服务端拒绝排序时透出原因，不当成已排好', async () => {
    const err = vi.spyOn(toast, 'error').mockReturnValue(0 as any);
    vi.mocked(client.post).mockRejectedValueOnce({ response: { data: { detail: '1 个仪表盘组已不存在，排序未保存，请刷新后重试' } } });
    render(<DashboardGroupsAdmin />);
    await screen.findByText('经营日报');
    fireEvent.click(screen.getByRole('button', { name: '下移组合 经营日报' }));

    await waitFor(() => expect(err).toHaveBeenCalledWith('1 个仪表盘组已不存在，排序未保存，请刷新后重试'));
    // 失败后不重拉列表、也不把卡面改成已交换的样子（避免“看着排好了”的假成功）
    expect(err.mock.calls.length).toBe(1);
  });
});

describe('组合内看板的顺序（左右穿梭框）', () => {
  const openEditor = async (name: string) => {
    render(<DashboardGroupsAdmin />);
    await screen.findByText(name);
    fireEvent.click(screen.getByRole('button', { name: `编辑组合 ${name}` }));
    await screen.findByLabelText('组合内看板顺序');
  };
  const namesOf = (label: string) => within(screen.getByLabelText(label))
    .getAllByRole('button', { name: /^选择看板 / })
    .map(b => (b.getAttribute('aria-label') || '').replace('选择看板 ', ''));
  const pickedNames = () => namesOf('组合内看板顺序');
  const availableNames = () => namesOf('可选看板');

  it('已选看板按编排顺序单列，首位标「先看」', async () => {
    await openEditor('经营日报');
    expect(pickedNames()).toEqual(['官网用户操作看板', '版本统计']);
    expect(within(screen.getByLabelText('组合内看板顺序')).getByText('先看')).toBeInTheDocument();
    // 未入组的在左列，不混在已选清单里
    expect(availableNames()).toEqual(['设备扫描精度']);
    // 组合卡上的清单也带序号，管理员不打开弹窗也看得出顺序
    expect(screen.getByTitle(/按编排顺序（用户点组合先看第 1 块）：1\. 官网用户操作看板 → 2\. 版本统计/)).toBeInTheDocument();
  });

  it('多选：普通点击累加、Shift 连选、再点取消，点 > 按左列顺序一次入组', async () => {
    const clsOf = (name: string) => screen.getByRole('button', { name }).className;
    await openEditor('质量周报');          // 空组：三块板全部在左列
    expect(availableNames()).toEqual(['官网用户操作看板', '版本统计', '设备扫描精度']);

    fireEvent.click(screen.getByRole('button', { name: '选择看板 官网用户操作看板' }));
    fireEvent.click(screen.getByRole('button', { name: '选择看板 设备扫描精度' }), { shiftKey: true });
    // 按钮 title 把“本次会搬几个”说出来，不让人点完才知道
    expect(screen.getByRole('button', { name: '把左列选中的看板加入组合' }))
      .toHaveAttribute('title', '加入右列（已选 3 个）');
    // 选中态必须可见（与全站 toggle 同一份口径）：否则“下一步 <> 会搬走谁”只能靠猜
    expect(clsOf('选择看板 官网用户操作看板')).toContain(TOGGLE_ACTIVE_CLASS);

    fireEvent.click(screen.getByRole('button', { name: '选择看板 版本统计' }));   // 再点 = 只取消这一行
    expect(clsOf('选择看板 版本统计')).not.toContain(TOGGLE_ACTIVE_CLASS);
    expect(clsOf('选择看板 设备扫描精度')).toContain(TOGGLE_ACTIVE_CLASS);
    fireEvent.click(screen.getByRole('button', { name: '把左列选中的看板加入组合' }));
    // 入组按左列可见顺序而不是点击乱序，也不会把“本列有哪几行”当成已选集合
    expect(pickedNames()).toEqual(['官网用户操作看板', '设备扫描精度']);
    expect(availableNames()).toEqual(['版本统计']);
  });

  it('未选中时 <> 都置灰（不做点了没反应的死开关）', async () => {
    await openEditor('经营日报');
    expect((screen.getByRole('button', { name: '把左列选中的看板加入组合' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '把右列选中的看板移出组合' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('点 < 把右列选中项退回左列（移出与入组共用一份数据）', async () => {
    await openEditor('经营日报');
    fireEvent.click(screen.getByRole('button', { name: '选择看板 版本统计' }));
    fireEvent.click(screen.getByRole('button', { name: '把右列选中的看板移出组合' }));

    expect(pickedNames()).toEqual(['官网用户操作看板']);
    expect(availableNames()).toEqual(['版本统计', '设备扫描精度']);
  });

  it('双击左列行 = 直接加入末尾（无需先选中再点 >）', async () => {
    await openEditor('经营日报');
    fireEvent.doubleClick(screen.getByRole('button', { name: '选择看板 设备扫描精度' }));
    expect(pickedNames()).toEqual(['官网用户操作看板', '版本统计', '设备扫描精度']);
  });

  it('拖动已选行到首位：右列行序即时刷新（保存时就是新的 dashboard_ids 序）', async () => {
    await openEditor('经营日报');
    const list = screen.getByLabelText('组合内看板顺序');
    const handle = within(list).getAllByTitle('拖动调位')[1];          // 版本统计
    const rowOf = (name: string) => within(list)
      .getByRole('button', { name: `选择看板 ${name}` }).closest('li')!;

    fireEvent.dragStart(handle, { dataTransfer: { setData: vi.fn(), effectAllowed: '' } });
    fireEvent.dragOver(rowOf('官网用户操作看板'));
    fireEvent.drop(rowOf('官网用户操作看板'));
    fireEvent.dragEnd(handle);

    expect(pickedNames()).toEqual(['版本统计', '官网用户操作看板']);

    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(puts.some(([u]) => u === '/dashboard/groups/11/items')).toBe(true));
    expect(puts.find(([u]) => u === '/dashboard/groups/11/items')![1].dashboard_ids).toEqual([2, 1]);
  });

  it('上移第 2 块板后保存：dashboard_ids 按新顺序回传，且不回传 sort', async () => {
    await openEditor('经营日报');
    fireEvent.click(await screen.findByRole('button', { name: '上移看板 版本统计' }));
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(puts.some(([u]) => u === '/dashboard/groups/11/items')).toBe(true));
    expect(puts.find(([u]) => u === '/dashboard/groups/11/items')![1].dashboard_ids).toEqual([2, 1]);
    const groupPut = puts.find(([u]) => u === '/dashboard/groups/11')!;
    expect(groupPut[1]).not.toHaveProperty('sort');   // 回传缺省 0 会把组合抬回首位
  });

  it('看板被删了也要列在右列并标出（保存不得静默丢成员）', async () => {
    vi.mocked(client.get).mockImplementation(((url: string) => {
      if (url === '/dashboard/groups') {
        return Promise.resolve({ data: [{ id: 11, name: '经营日报', description: '', sort: 10, dashboard_ids: [1, 555], role_ids: [1] }] });
      }
      if (url === '/roles/') return Promise.resolve({ data: [{ id: 1, display_name: '管理员' }] });
      return Promise.resolve({ data: [] });
    }) as any);
    await openEditor('经营日报');

    expect(pickedNames()).toEqual(['官网用户操作看板', '看板已删除']);
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(puts.some(([u]) => u === '/dashboard/groups/11/items')).toBe(true));
    expect(puts.find(([u]) => u === '/dashboard/groups/11/items')![1].dashboard_ids).toEqual([1, 555]);
  });
});

describe('排完立即对用户生效', () => {
  it('组合上移写库后让浏览端目录缓存失效（不必等 30s TTL）', async () => {
    await fetchVisibleGroups();
    expect(visibleFetches()).toBe(1);            // 已填缓存

    render(<DashboardGroupsAdmin />);
    await screen.findByText('经营日报');
    fireEvent.click(screen.getByRole('button', { name: '上移组合 设备大盘' }));
    await waitFor(() => expect(posts.some(([u]) => u === '/dashboard/groups/reorder')).toBe(true));

    await fetchVisibleGroups();
    expect(visibleFetches()).toBe(2);            // 排序写库后回源, 管理员立刻能在数据看板看到新序
  });

  it('首位组合点不动就不发写请求（越界按钮置灰，也不误清目录缓存）', async () => {
    await fetchVisibleGroups();
    render(<DashboardGroupsAdmin />);
    await screen.findByText('经营日报');
    fireEvent.click(screen.getByRole('button', { name: '上移组合 经营日报' }));   // 首位置灰, 点不动
    await new Promise(r => setTimeout(r, 0));
    expect(posts).toHaveLength(0);
    await fetchVisibleGroups();
    expect(visibleFetches()).toBe(1);
  });
});
