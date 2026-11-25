import { useCallback, useEffect, useRef } from 'react';
import { useReactFlow, useStore } from '@xyflow/react';

type Props = {
  padding?: number;
  minZoom?: number;
  maxZoom?: number;
};

/**
 * 图数据异步到达时的自适应视口（挂在 `<ReactFlow>` 内部使用）。
 *
 * 为什么不用 `fitView` / `useNodesInitialized`：
 * - `fitView` prop 与挂载即 `fitView()` 都跑在节点渲染前，拿不到尺寸；
 * - `useNodesInitialized()` 在本项目里恒为 false（实测 store 里每个节点
 *   `measured` 一直是 null，即使 DOM 上已是 99×39），导致适配永远不触发；
 * - ReactFlow 自带 fit 依赖这些尺寸，算出来是错的：15 节点网格被缩成 0.58、
 *   最下面一行裁在画布外，看着像“节点少了”。
 *
 * 因此这里**直接从 DOM 量**每个 `.react-flow__node` 的屏幕矩形，反算模型坐标
 * 后自己算缩放/平移（不依赖 store 的 measured）。另外画布高度会被同页内容改变
 * （运行详情节点表渲染后 380px → 330px），尺寸一变就重新适配。
 *
 * 节点数变化（换工作流 / 加节点 / 换 run）时由调用方换 `key` 重新适配：
 * `<AutoFitView key={nodes.length} />`。
 */
export default function AutoFitView({ padding = 0.15, minZoom = 0.2, maxZoom = 1 }: Props) {
  const { setViewport } = useReactFlow();
  const domNode = useStore((s) => s.domNode);
  const nodeCount = useStore((s) => s.nodes.length);
  const rafId = useRef(0);

  const fit = useCallback(() => {
    // 双帧：等节点挂到 DOM、样式落定后再量
    cancelAnimationFrame(rafId.current);
    rafId.current = requestAnimationFrame(() => {
      rafId.current = requestAnimationFrame(() => {
        const pane = domNode as HTMLElement | undefined;
        if (!pane) return;
        const nodes = Array.from(pane.querySelectorAll<HTMLElement>('.react-flow__node'));
        if (!nodes.length) return;
        // 当前视口（画在 viewport 上的 transform）
        const tf = pane.querySelector<HTMLElement>('.react-flow__viewport')?.style.transform || '';
        const m = /translate\(([-\d.]+)px,\s*([-\d.]+)px\)\s*scale\(([-\d.]+)\)/.exec(tf);
        const [tx, ty, zoom] = m ? [Number(m[1]), Number(m[2]), Number(m[3])] : [0, 0, 1];
        const box = pane.getBoundingClientRect();
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for (const el of nodes) {
          const r = el.getBoundingClientRect();
          if (!r.width || !r.height) continue;
          // 屏幕矩形 → 模型坐标（去掉当前平移与缩放）
          const nx0 = (r.left - box.left - tx) / zoom;
          const ny0 = (r.top - box.top - ty) / zoom;
          x0 = Math.min(x0, nx0); y0 = Math.min(y0, ny0);
          x1 = Math.max(x1, nx0 + r.width / zoom);
          y1 = Math.max(y1, ny0 + r.height / zoom);
        }
        if (!Number.isFinite(x0) || !Number.isFinite(y0)) return;
        const bw = Math.max(x1 - x0, 1);
        const bh = Math.max(y1 - y0, 1);
        const availW = box.width * (1 - padding * 2);
        const availH = box.height * (1 - padding * 2);
        const next = Math.min(Math.max(Math.min(availW / bw, availH / bh), minZoom), maxZoom);
        setViewport({
          x: (box.width - bw * next) / 2 - x0 * next,
          y: (box.height - bh * next) / 2 - y0 * next,
          zoom: next,
        });
      });
    });
  }, [domNode, setViewport, padding, minZoom, maxZoom]);

  useEffect(() => {
    if (nodeCount > 0) fit();
  }, [nodeCount, fit]);

  useEffect(() => {
    if (!domNode || !nodeCount) return;
    // 首帧回调是 observe 时的初始尺寸，不需要重算
    let skipFirst = true;
    const ro = new ResizeObserver(() => {
      if (skipFirst) { skipFirst = false; return; }
      fit();
    });
    ro.observe(domNode);
    return () => { ro.disconnect(); cancelAnimationFrame(rafId.current); };
  }, [domNode, nodeCount, fit]);

  return null;
}