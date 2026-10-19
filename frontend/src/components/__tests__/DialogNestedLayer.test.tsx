import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import fs from 'node:fs';
import path from 'node:path';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Dialog, DialogContent, isNestedLayerTarget } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

// 历史缺陷：弹窗里的下拉「点两次」（第一次展开、第二次选值）会把弹窗关掉，
// 详情页直接退回列表（数据源详情的 DB 类型下拉就是现场，全站带下拉/菜单的弹窗同病）。
// 根因：Select 浮层经 Portal 挂在 body 上、DOM 上不在弹窗节点内；而 Dialog 开了
// deferPointerDownOutside，"点外判定"延迟到 click 才派发 —— 那时 Select 已关闭卸载，
// 于是被判成"点在弹窗外"→ onDismiss → 弹窗关闭。
// 修复：外点目标落在嵌套浮层（popper/listbox/option/menu/dialog）上时 preventDefault。
afterEach(() => cleanup());

function Demo({ onOpenChange }: { onOpenChange: (open: boolean) => void }) {
  const [v, setV] = useState('mysql');
  return <Dialog open onOpenChange={onOpenChange}>
    <DialogContent>
      <Select value={v} onValueChange={setV}>
        <SelectTrigger aria-label="数据库类型"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="mysql">MySQL</SelectItem>
          <SelectItem value="postgres">PostgreSQL</SelectItem>
        </SelectContent>
      </Select>
    </DialogContent>
  </Dialog>;
}

describe('弹窗内嵌套浮层不触发弹窗关闭', () => {
  it('isNestedLayerTarget 识别下拉/菜单浮层（含脱浮后的节点）', () => {
    const wrapper = document.createElement('div');
    wrapper.setAttribute('data-radix-popper-content-wrapper', '');
    const option = document.createElement('div');
    option.setAttribute('role', 'option');
    wrapper.appendChild(option);
    document.body.appendChild(wrapper);
    expect(isNestedLayerTarget(option)).toBe(true);
    expect(isNestedLayerTarget(wrapper)).toBe(true);
    // 浮层卸载后节点仍要能被识别（关闭顺序：先卸浮层、后派发外点）
    wrapper.remove();
    expect(isNestedLayerTarget(option)).toBe(true);
    const plain = document.createElement('button');
    document.body.appendChild(plain);
    expect(isNestedLayerTarget(plain)).toBe(false);
    expect(isNestedLayerTarget(null)).toBe(false);
    wrapper.remove(); plain.remove();
  });

  it('在下拉里选值不关闭弹窗（点两次不退出详情）', async () => {
    // 注意：jsdom 无法完全复刻浏览器“延迟外点”的派发时序，本例是意图 smoke；
    // 真正的回归硬锁是下面的“守卫接线”用例。
    const onOpenChange = vi.fn();
    render(<Demo onOpenChange={onOpenChange} />);
    const trigger = screen.getByLabelText('数据库类型');
    fireEvent.pointerDown(trigger, { button: 0 });
    fireEvent.click(trigger);
    const option = await screen.findByText('PostgreSQL');
    // 第一次点击展开，第二次点击选值 —— 修复后弹窗不得被关
    fireEvent.pointerDown(option, { button: 0 });
    fireEvent.click(option);
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it('修复不误伤：真正的关闭路径仍生效（Esc 关闭 / 外点目标非浮层不拦截）', async () => {
    const onOpenChange = vi.fn();
    render(<Demo onOpenChange={onOpenChange} />);
    // 遮罩/空白区不属于嵌套浮层，不会被修复拦截（已由 isNestedLayerTarget 用例锁定）
    const overlay = screen.getByRole('dialog').previousElementSibling!;
    expect(isNestedLayerTarget(overlay)).toBe(false);
    // Esc 仍是正常关闭路径
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('守卫必须接线：DialogContent 的外点事件都过 keepNestedLayers', () => {
    // 源码级护栏：删掉守卫必须立刻变红（浏览器里不接守卫时，弹窗会被关、详情退回列表）
    const src = fs.readFileSync(
      path.resolve(__dirname, '..', '..', 'components', 'ui', 'dialog.tsx'), 'utf8');
    expect(src).toMatch(/onInteractOutside=\{\(e\) => keepNestedLayers\(e, onInteractOutside\)\}/);
    expect(src).toMatch(/onPointerDownOutside=\{\(e\) => keepNestedLayers\(e, onPointerDownOutside\)\}/);
    // 判定必须覆盖 Radix 浮层（Select/菜单/气泡的 popper 包裹层与 listbox/option）
    expect(src).toContain('data-radix-popper-content-wrapper');
    expect(src).toContain('role="listbox"');
  });
});
