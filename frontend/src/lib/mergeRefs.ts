import { useCallback } from 'react';
import type { Ref } from 'react';

/**
 * 合并多个 ref（React 18 没有 useMergeRefs）。
 *
 * 画布视口同时需要「交互 hook 的 ref」与「dnd-kit droppable 的 setNodeRef」，
 * 二者必须挂在同一个节点上才能既算得出坐标又收得到落点事件。
 */
export function mergeRefs<T>(...refs: (Ref<T> | undefined | null)[]): (node: T | null) => void {
  return node => {
    for (const ref of refs) {
      if (typeof ref === 'function') ref(node);
      else if (ref) (ref as { current: T | null }).current = node;
    }
  };
}

/**
 * 稳定身份的合并 ref。
 *
 * 必须用这个而不是在 JSX 里内联 mergeRefs(...)：内联调用每次渲染都产生新函数，
 * React 会先用 null 调旧回调、再用节点调新回调；dnd-kit 的 setNodeRef 收到 null
 * 会注销落点节点，导致拖拽过程中（本身就会因预览重渲染）e.over 丢失、落点被静默丢弃。
 */
export function useMergedRefs<T>(...refs: (Ref<T> | undefined | null)[]): (node: T | null) => void {
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useCallback(mergeRefs<T>(...refs), refs);
}
