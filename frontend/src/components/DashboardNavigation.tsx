import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import type { Dashboard } from '@/stores/dashboardStore';

/** 切换条只需轻量字段(组目录行即可, 无需完整 Dashboard) */
type DashboardNavItem = Pick<Dashboard, 'id' | 'name'> & Partial<Pick<Dashboard, 'status' | 'is_default'>>;

/** pill 距容器边缘至少留出的可视余量：不留余量时选中 pill 会正好贴着圆角被裁掉一半 */
const EDGE_PAD = 12;

/**
 * 同组看板切换条。
 * 展示范围 = 当前仪表盘组内、服务端按用户角色授权过滤后的可见集
 * (fail-closed, admin 全可见; 组只做编排不授予可见)。
 *
 * 产品决策(2026-10-27)：工作空间看板页**不再提供「看板目录」抽屉**。
 * 侧栏「数据看板」已是全量清单入口，页头切换条 + 组合下拉覆盖了同组/跨组跳转，
 * 抽屉与它们重复，还在页头多占一个图标位。删入口的同时删掉抽屉本体，不留死代码。
 *
 * 溢出处理：切换条横向溢出时滚动条是隐藏的（视觉护栏），没有别的提示就等于
 * “看不出还能往哪滚”，而且点后半段的看板时选中态会停在容器外——用户反馈“选到后面就看不见了”。
 * 因此补四件事（缺一都会退化成“后面的看板点不到”）：
 * ① 溢出时常驻左右翻页按钮（到头的那一侧置灰，不做“点了没反应”的死开关，
 *    也不做“到边就消失”导致条宽抖动）；
 * ② 切换/进入/布局变化时把选中 pill 用**最小位移**滚进可视区（不居中，避免每切一次都整条大幅滑动）；
 * ③ 滚轮纵向手势在溢出条上转成横向滚动（隐藏滚动条 + 只能横滚的容器，鼠标滚轮默认什么都不做，
 *    等于把后半段看板藏起来；到两端后**不再拦截**，放行给页面继续纵向滚，避免滚不动的死胡同）；
 * ④ 用 ResizeObserver 盯住容器与每颗 pill 重算溢出：只在 mount/scroll/window.resize 测一次是
 *    不够的——中文字体加载完、看板改名、页头左右簇宽窄变动都会晚于首帧才把 scrollWidth 撑开，
 *    测早了得出“没溢出”的结论，箭头就永远不出现（本条是用户反馈的直接根因）。
 */
export function useDashboardNavigation(dashboards: DashboardNavItem[], currentId?: number, onSelect?: (id: number) => void) {
  // 服务端已按角色可见集过滤并排好序（组内 = 管理端编排顺序，未分组 = is_default/sort_order），保持传入顺序。
  const visible = dashboards;
  const navRef = useRef<HTMLElement | null>(null);
  const [edge, setEdge] = useState({ overflow: false, left: false, right: false });

  // 选中 pill 用最小位移滚进可视区（nearest 语义）；已在视野内就一动不动，不做“每次切换都滑一下”。
  const scrollActiveIntoView = useCallback(() => {
    const el = navRef.current;
    if (!el) return;
    const active = el.querySelector('[aria-current="true"]') as HTMLElement | null;
    if (!active) return;
    const box = el.getBoundingClientRect(), pill = active.getBoundingClientRect();
    let delta = 0;
    if (pill.right > box.right - EDGE_PAD) delta = pill.right - (box.right - EDGE_PAD);
    else if (pill.left < box.left + EDGE_PAD) delta = pill.left - (box.left + EDGE_PAD);
    if (delta) el.scrollBy({ left: delta, behavior: 'smooth' });
  }, []);

  const syncEdge = useCallback(() => {
    const el = navRef.current;
    if (!el) return;
    const max = el.scrollWidth - el.clientWidth;
    // 溢出判定留 2px 容差：缩放/亚像素布局下 scrollWidth 常比 clientWidth 大 1px，
    // 不留容差会出现“两个永远不会生效的箭头”。
    setEdge(prev => {
      const next = { overflow: max > 2, left: el.scrollLeft > 2, right: el.scrollLeft < max - 2 };
      return prev.overflow === next.overflow && prev.left === next.left && prev.right === next.right
        ? prev : next;
    });
  }, []);

  // 布局变了先重算溢出，再把选中项带回可视区（②④ 的交汇点）：
  // 先测后滚的顺序不能颠倒——按旧几何算出的箭头可用性会与新位置不一致。
  const remeasure = useCallback(() => {
    syncEdge();
    scrollActiveIntoView();
  }, [syncEdge, scrollActiveIntoView]);

  useEffect(() => {
    const el = navRef.current;
    if (!el) return;
    syncEdge();
    el.addEventListener('scroll', syncEdge, { passive: true });
    window.addEventListener('resize', remeasure);
    // 无 ResizeObserver 的环境（jsdom / 老浏览器）退回上面两个事件：只是少一条重算时机，
    // 不改变功能语义，因此不需要在页面上给用户任何提示。
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => remeasure()) : null;
    ro?.observe(el);
    // 容器宽度不变但内容变宽（字体加载完、看板名变长）时 RO 只会为子元素回调，
    // 所以逐颗 pill 一起观察，不靠“猜个延时再测一次”。
    el.querySelectorAll('button').forEach(b => ro?.observe(b));
    return () => {
      el.removeEventListener('scroll', syncEdge);
      window.removeEventListener('resize', remeasure);
      ro?.disconnect();
    };
  }, [syncEdge, remeasure, visible.length]);

  // 切换/进入/看板清单变化后把选中项滚进可视区
  useEffect(() => {
    if (currentId == null) return;
    scrollActiveIntoView();
    syncEdge();
  }, [currentId, visible.length, scrollActiveIntoView, syncEdge]);

  // 纵向滚轮转横向滚动：只在**确实还能横滚**时接管，到两端就放行给页面（不做滚不动的死胡同）。
  // 必须用原生非 passive 监听：React 的 onWheel 在根容器上是 passive 的，preventDefault 无效。
  useEffect(() => {
    const el = navRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;  // 缩放手势/已有横向输入：交给浏览器
      const max = el.scrollWidth - el.clientWidth;
      if (max <= 2) return;
      // Firefox 的 line 模式与老版 page 模式：deltaY 不是像素，按单位换算再滚
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? el.clientWidth : 1;
      const next = Math.min(max, Math.max(0, el.scrollLeft + e.deltaY * unit));
      if (next === el.scrollLeft) return;
      e.preventDefault();
      el.scrollLeft = next;
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [visible.length]);

  const page = (dir: -1 | 1) => {
    const el = navRef.current;
    if (!el) return;
    el.scrollBy({ left: dir * Math.max(160, el.clientWidth * 0.6), behavior: 'smooth' });
  };

  const arrowCls = 'flex h-6 w-6 shrink-0 items-center justify-center rounded-full '
    + 'bg-muted/60 text-muted-foreground ring-1 ring-border/60 hover:bg-muted hover:text-foreground '
    + 'disabled:pointer-events-none disabled:opacity-30';

  // 同组看板切换：整条做成**分段控件**（凹槽容器 + 选中项浮起），不再让几颗 pill 漂在空白带上。
  // 凹槽**不拉 flex-1**：贴内容收窄，否则两个 tab 时凹槽占满整行、中间一大块空白（用户反馈“光秃秃”）；
  // tab 多时靠 min-w-0 + overflow-x-auto 压缩并横向滚动，不会顶宽页头。
  // 选中态仍走软高亮（容器底 + 主色字 + 细描边），不用 aria-current="page"：
  // 后者会被主题全局 button[aria-current="page"] 规则(tech/glass/ainative…)涂成厚重色块/发光。
  const tabs = visible.length > 1 ? (
    <div className="flex min-w-0 items-center gap-1">
      {edge.overflow && <button type="button" aria-label="向前翻看板" title="向前翻看板"
        className={arrowCls} disabled={!edge.left} onClick={() => page(-1)}>
        <ChevronLeft className="h-4 w-4" /></button>}
      <nav ref={navRef} aria-label="同组看板"
        className="flex min-w-0 items-center gap-1.5 overflow-x-auto rounded-full bg-muted/60 p-1.5 ring-1 ring-border/60 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {visible.map(d => <button key={d.id} type="button" title={d.name} aria-current={d.id === currentId ? 'true' : undefined}
          onClick={() => onSelect?.(d.id)}
          className={`shrink-0 whitespace-nowrap rounded-full px-4 py-1.5 text-[13px] transition-colors sm:text-sm ${
            d.id === currentId
              ? 'bg-background font-medium text-primary shadow-sm ring-1 ring-primary/25'
              : 'text-muted-foreground hover:bg-background/70 hover:text-foreground'}`}>{d.name}</button>)}
      </nav>
      {edge.overflow && <button type="button" aria-label="向后翻看板" title="向后翻看板"
        className={arrowCls} disabled={!edge.right} onClick={() => page(1)}>
        <ChevronRight className="h-4 w-4" /></button>}
    </div>
  ) : null;
  return { tabs };
}
