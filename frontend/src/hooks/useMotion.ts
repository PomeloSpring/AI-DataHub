import { useEffect, useMemo, useState } from 'react';
import { useVisLibrary } from './useVisLibrary';
import { useMotionPrefStore } from '@/stores/motionPrefStore';
import {
  enterClass, motionCssVars, motionPresets, resolveMotion,
  type MotionBoardRef, type MotionResolution,
} from '@/lib/motion';

/**
 * 「减少动态效果」偏好（prefers-reduced-motion）的唯一读取口。
 *
 * CSS 里那条 @media 只压住动画时长，**不改变解析结果**：配置界面要能说明白
 * 「当前动效为什么没生效」，所以这一段事实必须由 JS 拿到并回传 reason
 * （.qoder/rules/no-silent-degradation.md：不是静默关掉就算合规）。
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches,
  );
  useEffect(() => {
    if (typeof matchMedia !== 'function') return;
    const mq = matchMedia('(prefers-reduced-motion: reduce)');
    const onChange = () => setReduced(mq.matches);
    onChange();
    // addEventListener 是新接口，旧浏览器只有 addListener：两个都要接，否则系统设置改了不跟随
    if (mq.addEventListener) {
      mq.addEventListener('change', onChange);
      return () => mq.removeEventListener('change', onChange);
    }
    mq.addListener(onChange);
    return () => mq.removeListener(onChange);
  }, []);
  return reduced;
}

export interface UseMotionResult extends MotionResolution {
  /** 挂在容器上的 CSS 变量（时长/缓动/位移，globals.css 只读变量不写死数值） */
  cssVars: Record<string, string>;
  /** 可选预设清单（在用口径，显示用 name） */
  presets: Array<{ code: string; name: string; description?: string }>;
  /** 偏好是否已从服务端读回（false 时界面必须显示「未加载」而不是默认档） */
  prefLoaded: boolean;
  prefError: string;
  /** 重新拉偏好（Profile 的重试入口） */
  reloadPref: () => Promise<void>;
}

/**
 * 动效解析的唯一消费入口（与 useChartDesign 在样式层的地位对称）。
 *
 * 三面消费一律经此，禁止页面自写 @keyframes：
 * - `useMotion()`：Chat 图表、内容详情 —— 用户偏好 > 内置默认；
 * - `useMotion({ board })`：看板/大屏播放 —— 板上挂载 > 用户偏好 > 内置默认（分面裁决）。
 *
 * 优先级、禁用回退与差量合并规则全部在 lib/motion.ts 一处实现，这里只做数据接线。
 */
export function useMotion(opts?: { board?: MotionBoardRef | null }): UseMotionResult {
  const { items, error: libraryError } = useVisLibrary();
  const presetCode = useMotionPrefStore(s => s.presetCode);
  const override = useMotionPrefStore(s => s.override);
  const prefLoaded = useMotionPrefStore(s => s.loaded);
  const prefErr = useMotionPrefStore(s => s.error);
  const fetchPref = useMotionPrefStore(s => s.fetch);
  const reducedMotion = usePrefersReducedMotion();

  useEffect(() => { void fetchPref(); }, [fetchPref]);

  const board = opts?.board;
  // 依赖只取标量（ref / 快照 code+revision / 差量序列化）：调用方传进来的 board 常是渲染时新对象，
  // 直接把对象放进 deps 会让每张图每帧重算一次解析结果
  const snapshot = board?.snapshot;
  const overrideKey = JSON.stringify(board?.override ?? null);
  const resolution = useMemo(() => resolveMotion({
    items,
    userPref: { presetCode, override },
    board,
    reducedMotion,
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [items, presetCode, override, board?.ref, snapshot?.code, snapshot?.revision, overrideKey, reducedMotion]);

  const presets = useMemo(() => motionPresets(items)
    .map(c => ({ code: c.code, name: c.name, description: c.description })), [items]);

  return {
    ...resolution,
    cssVars: motionCssVars(resolution.motion),
    presets,
    prefLoaded,
    // 字模库读失败也要说清（预设清单是空的，下拉不能装作「没有可选项」）
    prefError: libraryError || prefErr,
    reloadPref: () => fetchPref(true),
  };
}

/**
 * 只要入场 class 的简版（chat 各阶段卡/过程卡共用）。
 * 效果名与时长依旧由 lib/motion 一处决定，这里不夹任何默认值 ——
 * 否则每个组件都会变成一份“自己的动效口径”（就是本规范要消除的重复）。
 */
export function useEnterClass(): string {
  const { motion } = useMotion();
  return enterClass(motion.enter);
}

/**
 * 看板 filters → 板上挂载的动效引用（**唯一取法**）。
 * Screen/大屏播放、阅读页、编辑器、设计面板一律经这里读，不得各读一套字段 ——
 * 出现第二套取法就等于“存了一处、生效一处”，与 ui-vis-library-style §1 同理。
 * `design.motionOverride` = 这块板自己的时长/错峰微调（只叠在挂载预设上）。
 */
export function boardMotionRef(filters: any): MotionBoardRef | null {
  const ref = filters?.components?.motion ?? null;
  const snapshot = filters?.design?.componentSnapshots?.motion ?? null;
  const override = filters?.design?.motionOverride ?? null;
  return ref || snapshot || override ? { ref, snapshot, override } : null;
}
