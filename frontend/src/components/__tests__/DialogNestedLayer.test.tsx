import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import fs from 'node:fs';
import path from 'node:path';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Dialog, DialogContent, isNestedLayerTarget, hasOpenNestedLayer } from '@/components/ui/dialog';
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
    expect(src).toMatch(/onInteractOutside=\{\(e\) => keepNestedLayers\(e, onInteractOutside, nestedOpenAtPointerDown\(\)\)\}/);
    expect(src).toMatch(/onPointerDownOutside=\{\(e\) => keepNestedLayers\(e, onPointerDownOutside, nestedOpenAtPointerDown\(\)\)\}/);
    // 判定必须覆盖 Radix 浮层（Select/菜单/气泡的 popper 包裹层与 listbox/option）
    expect(src).toContain('data-radix-popper-content-wrapper');
    expect(src).toContain('role="listbox"');
    // 并且必须带“浮层展开”这条（Radix #2450/#2961 的社区可用解法）与 capture 快照
    expect(src).toMatch(/isNestedLayerTarget\(e\.target\) \|\| nestedOpenAtPointerDown \|\| hasOpenNestedLayer\(\)/);
    expect(src).toContain("document.addEventListener('pointerdown', onPointerDown, true)");
  });
});

// ── 第二次历史缺陷：下拉展开期间点别处，整个弹窗被一起关掉 ────────────────
// Radix 长期缺陷（primitives#2450 / #2961，官方 closed as not planned，2026-03 仍有人
// 确认复现）：Select 内容层硬编码 disableOutsidePointerEvents:true，展开时把弹窗层压成
// pointer-events:none，这一下命中测试落空到 <html> → 弹窗被判外点。
// 上一轮按“落空 + 坐标在弹窗矩形内”修，实机反馈无效：Radix 开了 deferPointerDownOutside，
// 外点判定推迟到 click 才派发，而下拉在 pointerdown 阶段已卸载 —— 到派发时再判永远慢一步。
// 现按社区被验证的解法：“嵌套浮层展开期间，弹窗一律不响应外点”，并在 capture 阶段拍快照。
describe('嵌套浮层展开期间，弹窗不响应外点', () => {
  // 只清自己造的行，不能动 body.innerHTML（会把 React/Radix 的 portal 节点先拆掉，
  // 随后的 cleanup 就报 NotFoundError）
  const created: HTMLElement[] = [];
  function layer(role: string, state = 'open') {
    const el = document.createElement('div');
    el.setAttribute('role', role);
    el.setAttribute('data-state', state);
    document.body.appendChild(el);
    created.push(el);
    return el;
  }
  const clearLayers = () => { created.splice(0).forEach(el => el.remove()); };
  afterEach(clearLayers);

  it('hasOpenNestedLayer：只认正展开的 Select/菜单，不认 tooltip 与已收起的', () => {
    expect(hasOpenNestedLayer()).toBe(false);
    const listbox = layer('listbox');
    expect(hasOpenNestedLayer()).toBe(true);
    // 退出动画期间 Radix 把 data-state 改成 closed，不能还当成“开着”挡住关窗
    listbox.setAttribute('data-state', 'closed');
    expect(hasOpenNestedLayer()).toBe(false);
    layer('menu');
    expect(hasOpenNestedLayer()).toBe(true);
    clearLayers();
    // Tooltip 的 data-state 是 delayed-open/instant-open：hover 不该挡住关窗
    layer('tooltip', 'delayed-open');
    expect(hasOpenNestedLayer()).toBe(false);
  });

  async function openSelect(onOpenChange: (v: boolean) => void) {
    render(<Demo onOpenChange={onOpenChange} />);
    // 遮罩要在下拉展开前拿：Radix 的模态浮层会把弹窗标 aria-hidden，之后 getByRole 找不到
    const overlay = screen.getByRole('dialog').previousElementSibling!;
    const trigger = screen.getByLabelText('数据库类型');
    fireEvent.pointerDown(trigger, { button: 0 });
    fireEvent.click(trigger);
    await screen.findByText('PostgreSQL');   // 浮层已展开
    return overlay;
  }

  it('下拉展开时点遮罩：只收下拉，弹窗保持（defer 派发后状态已丢也能判）', async () => {
    const onOpenChange = vi.fn();
    const overlay = await openSelect(onOpenChange);
    fireEvent.pointerDown(overlay, { button: 0 });
    fireEvent.click(overlay);
    // defer 的外点在下一个宏任务派发；那时浮层已卸载，靠的正是 pointerdown 快照
    await new Promise(r => setTimeout(r, 0));
    expect(hasOpenNestedLayer()).toBe(false);
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it('不误伤：下拉收起后点遮罩 → 照常关窗', async () => {
    const onOpenChange = vi.fn();
    const overlay = await openSelect(onOpenChange);
    const option = screen.getByText('PostgreSQL');
    fireEvent.pointerDown(option, { button: 0 });
    fireEvent.click(option);              // 选值 → 浮层收起
    await new Promise(r => setTimeout(r, 0));
    expect(hasOpenNestedLayer()).toBe(false);

    fireEvent.pointerDown(overlay, { button: 0 });
    fireEvent.click(overlay);
    await new Promise(r => setTimeout(r, 0));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

