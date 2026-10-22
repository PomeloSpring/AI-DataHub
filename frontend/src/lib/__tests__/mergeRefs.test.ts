import { describe, expect, it } from 'vitest';
import { useRef } from 'react';
import { renderHook } from '@testing-library/react';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { useMergedRefs } from '../mergeRefs';

describe('合并 ref', () => {
  it('把节点同时写进对象 ref 与回调 ref', () => {
    const { result } = renderHook(() => {
      const objectRef = useRef<HTMLDivElement>(null);
      const seen: Array<HTMLDivElement | null> = [];
      const callbackRef = (node: HTMLDivElement | null) => seen.push(node);
      return { objectRef, seen, merged: useMergedRefs<HTMLDivElement>(objectRef, callbackRef, undefined) };
    });
    const node = document.createElement('div');
    result.current.merged(node);
    expect(result.current.objectRef.current).toBe(node);
    expect(result.current.seen).toEqual([node]);
  });

  it('身份稳定：重渲染不得产生新函数（否则 React 会先用 null 调旧回调）', () => {
    const { result, rerender } = renderHook(() => {
      const a = useRef<HTMLDivElement>(null);
      const b = useRef<HTMLDivElement>(null);
      return useMergedRefs<HTMLDivElement>(a, b);
    });
    const first = result.current;
    rerender();
    rerender();
    expect(result.current).toBe(first);
  });

  it('静态护栏：编辑器不得在 JSX 里内联 mergeRefs(...)', () => {
    // 内联调用每次渲染都新建函数身份，dnd-kit 的 setNodeRef 会收到 null 而注销落点节点，
    // 表现为「图表拖到画布上缩回去、没有落地」。必须用 useMergedRefs。
    const root = path.resolve(__dirname, '../..');
    for (const file of ['pages/DashboardEditor.tsx', 'components/editor/PageLayoutEditor.tsx']) {
      const code = fs.readFileSync(path.join(root, file), 'utf8');
      expect(code).not.toMatch(/ref=\{mergeRefs\(/);
    }
  });
});
