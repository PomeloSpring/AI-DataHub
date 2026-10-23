import { describe, expect, it } from 'vitest';
import { dragPointer, libraryDragId, LIBRARY_DROP_ZONE } from '../editorDrag';

describe('dragPointer：拖拽实时指针', () => {
  it('必须叠加 delta —— activatorEvent 只是起点，不能当落点', () => {
    // 历史缺陷：拖拽起点在字模库里(clientX 10)，拖到画布上(clientX 600)后松手，
    // 直接读 activatorEvent 会得到 10 —— 落点判定恒在画布外，图表永远放不下。
    const start = { clientX: 10, clientY: 200 };
    expect(dragPointer(start, { x: 590, y: -150 })).toEqual({ x: 600, y: 50 });
  });

  it('delta 为 0 时等于起点（没动就是没动）', () => {
    expect(dragPointer({ clientX: 10, clientY: 20 }, { x: 0, y: 0 })).toEqual({ x: 10, y: 20 });
  });

  it('delta 缺失或非数值时按 0 处理，不产生 NaN', () => {
    expect(dragPointer({ clientX: 5, clientY: 6 }, undefined)).toEqual({ x: 5, y: 6 });
    expect(dragPointer({ clientX: 5, clientY: 6 }, null)).toEqual({ x: 5, y: 6 });
    expect(dragPointer({ clientX: 5, clientY: 6 }, { x: undefined, y: Number.NaN }))
      .toEqual({ x: 5, y: 6 });
  });

  it('clientY 缺失时按 0 起算，不崩', () => {
    expect(dragPointer({ clientX: 5 }, { x: 1, y: 2 })).toEqual({ x: 6, y: 2 });
  });

  it('非 pointer 事件（键盘拖拽无坐标）返回 null，由调用方回落到 over 判定', () => {
    expect(dragPointer(undefined, { x: 1, y: 1 })).toBeNull();
    expect(dragPointer({}, { x: 1, y: 1 })).toBeNull();
    expect(dragPointer({ clientX: 'x' }, { x: 1, y: 1 })).toBeNull();
    expect(dragPointer(null, { x: 1, y: 1 })).toBeNull();
  });
});

describe('editorDrag 契约', () => {
  it('画布是唯一落点 id，两种布局模式共用', () => {
    expect(LIBRARY_DROP_ZONE).toBe('canvas');
  });

  it('拖拽 id 稳定且按来源区分，避免图表/字模撞 id', () => {
    expect(libraryDragId('chart', 1)).toBe('chart:1');
    expect(libraryDragId('vis', 'bar')).toBe('vis:bar');
    expect(libraryDragId('chart', 1)).not.toBe(libraryDragId('vis', 1));
  });
});
