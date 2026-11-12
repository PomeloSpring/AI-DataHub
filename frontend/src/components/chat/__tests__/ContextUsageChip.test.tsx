// 上下文占用小徽标的契约：收起态是右下角一条微进度条（不常驻百分比数字）、没测过就不渲染、
// "采集失败"绝不跟着一起藏掉也绝不画成 0% 的空条、明细与手动压缩都收在点击浮层里、
// 任务进行中不能压缩、压缩成功看得见对比、失败看得见原因。
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ContextUsageChip } from '../ContextUsageChip';
import type { CompactResult, ContextUsage } from '@/stores/chatStore';

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }));

import { toast } from 'sonner';

const usage = (over: Partial<ContextUsage> = {}): ContextUsage => ({
  available: true,
  reason: '',
  error: '',
  used_percentage: 42,
  threshold_percentage: 80,
  categories: [
    { type: 'system_prompt', label: '系统提示词', percentage: 12 },
    { type: 'messages', label: '对话消息', percentage: 24 },
    { type: 'free_space', label: '剩余空间', percentage: 58 },
  ],
  auto_compact_enabled: true,
  compact_count: 1,
  model: 'auto',
  busy: false,
  ...over,
});

const chip = () => screen.getByRole('button', { name: /^上下文(占用|压缩中)/ }) as HTMLElement;
const queryChip = () => screen.queryByRole('button', { name: /^上下文(占用|压缩中)/ });
/** 收起态微进度条的填充段（轨道 > 填充）；未测量时不存在填充段 */
const chipFill = () => chip().querySelector('span > span') as HTMLElement | null;
/** 收起态微进度条的轨道（采集失败时为红色虚线空槽） */
const chipTrack = () => chip().querySelector(':scope > span') as HTMLElement;
const measure = () => document.querySelector('[role="progressbar"]') as HTMLElement;
const fill = () => measure().querySelector('span') as HTMLElement;
const compactBtn = () => screen.getByRole('button', { name: '压缩上下文' }) as HTMLButtonElement;

const renderChip = (over: Partial<ComponentProps<typeof ContextUsageChip>> = {}) =>
  render(
    <ContextUsageChip
      usage={usage()}
      onCompact={vi.fn<() => Promise<CompactResult | null>>().mockResolvedValue(null)}
      {...over}
    />,
  );

beforeEach(() => {
  vi.clearAllMocks();
  document.body.innerHTML = '';
});

describe('ContextUsageChip — 收起态就是右下角一小条进度条', () => {
  it('默认不展开：只有微进度条、不常驻百分比数字，明细与压缩按钮都不在屏幕上', () => {
    renderChip();
    expect(chip().textContent).toBe('');
    expect(chipFill()).toHaveStyle({ width: '42%' });
    expect(chip()).toHaveAccessibleName('上下文占用 42%');
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByText('系统提示词')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '压缩上下文' })).not.toBeInTheDocument();
  });

  it('尚未测量（新会话）不渲染任何控件：没有占用可说，就别占一个没人看得懂的空位', () => {
    renderChip({ usage: usage({ available: false, used_percentage: null, reason: '尚未测量（发送一轮后开始测量）' }) });
    expect(queryChip()).not.toBeInTheDocument();
    expect(screen.queryByText('--%')).not.toBeInTheDocument();
  });

  it('采集失败必须留着：红色虚线空槽 + 原因，不跟着"空"一起藏掉，也不画成 0% 的实心轨道', () => {
    const err = '执行层未返回上下文占用百分比';
    renderChip({ usage: usage({ available: false, used_percentage: null, reason: err, error: err }) });
    expect(chip()).toHaveAccessibleName('上下文占用采集失败');
    expect(chip().title).toBe(err);
    expect(chipTrack()).toHaveClass('border-dashed');
    expect(chipFill()).toBeNull();
    fireEvent.click(chip());
    expect(screen.getByText(err)).toBeInTheDocument();
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
    expect(measure()).not.toHaveAttribute('aria-valuenow');
  });

  it('有旧值但本次采集失败：照旧渲染条，并明说"上方是上次成功的值"', () => {
    renderChip({ usage: usage({ error: '采集超时' }) });
    expect(chipFill()).toHaveStyle({ width: '42%' });
    fireEvent.click(chip());
    expect(screen.getByText(/最近一次采集失败：采集超时/)).toBeInTheDocument();
    expect(screen.getByText(/上次成功的值/)).toBeInTheDocument();
  });

  it('色阶随压力升级：常规 / 70%+ 提醒 / 85%+ 紧张（填充色为告警通道）', () => {
    const cases: [number, string][] = [[42, 'bg-primary/70'], [76, 'bg-amber-500'], [92, 'bg-red-500']];
    cases.forEach(([pct, fillCls]) => {
      document.body.innerHTML = '';
      renderChip({ usage: usage({ used_percentage: pct }) });
      expect(chipFill()).toHaveClass(fillCls);
    });
  });

  it('点徽标外面或按 Esc 收起（展开态是临时看一眼，不能常驻把卡片撑高）', () => {
    renderChip();
    fireEvent.click(chip());
    expect(screen.getByText('上下文窗口')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByText('上下文窗口')).not.toBeInTheDocument();
    fireEvent.click(chip());
    fireEvent.mouseDown(document.body);
    expect(screen.queryByText('上下文窗口')).not.toBeInTheDocument();
  });
});

describe('ContextUsageChip — 展开浮层', () => {
  it('浮层给占用、阈值刻度与中文分类名，不把内部 type 裸给用户', () => {
    renderChip();
    fireEvent.click(chip());
    expect(screen.getByText('上下文窗口')).toBeInTheDocument();
    expect(measure()).toHaveAttribute('aria-valuenow', '42');
    expect(fill()).toHaveStyle({ width: '42%' });
    // 阈值刻度线画在 80% 处（Qoder 同构：看得见"什么时候会自动压缩"）：[0]=填充条 [1]=刻度
    expect(measure().querySelectorAll('span')[1]).toHaveStyle({ left: '80%' });
    expect(screen.getByText('系统提示词')).toBeInTheDocument();
    expect(screen.getByText('对话消息')).toBeInTheDocument();
    expect(screen.getByText(/自动压缩：已开启/)).toBeInTheDocument();
    expect(screen.queryByText('system_prompt')).not.toBeInTheDocument();
  });

  it('CLI 自动压缩进行中：收起态切成不确定态脉动条并禁用手动压缩', () => {
    renderChip({ autoCompacting: true });
    expect(chip()).toHaveAccessibleName('上下文压缩中');
    expect(chipFill()).toHaveClass('animate-pulse');
    fireEvent.click(chip());
    expect(compactBtn()).toBeDisabled();
    expect(compactBtn()).toHaveAttribute('title', '模型正在自动压缩上下文，请稍候…');
  });

  it('压缩请求中（尚未测出值也要看得见）：不确定态脉动条', () => {
    renderChip({ usage: usage({ available: false, used_percentage: null }), compacting: true });
    expect(chip()).toBeInTheDocument();
    expect(chipFill()).toHaveClass('animate-pulse');
  });

  it('任务进行中不能压缩，并把原因写在按钮 title 上', () => {
    renderChip({ blocked: true, blockedReason: '本轮任务进行中，等它结束或先停止后再压缩' });
    fireEvent.click(chip());
    expect(compactBtn()).toBeDisabled();
    expect(compactBtn()).toHaveAttribute('title', '本轮任务进行中，等它结束或先停止后再压缩');
  });

  it('压缩成功后立刻显示前后对比与累计次数', async () => {
    const onCompact = vi.fn<() => Promise<CompactResult>>().mockResolvedValue({
      before_percentage: 84, after_percentage: 23, saved_percentage: 61, compact_count: 2,
    });
    renderChip({ onCompact, usage: usage({ used_percentage: 84 }) });
    fireEvent.click(chip());
    fireEvent.click(compactBtn());
    expect(onCompact).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByText(/已压缩，占用从 84% 降到 23%/)).toBeInTheDocument());
  });

  it('压缩失败必须把服务端原因 toast 出来，不假装成功', async () => {
    const onCompact = vi.fn().mockRejectedValue({
      response: { data: { detail: '本轮任务进行中，等它结束或先停止后再压缩' } },
    });
    renderChip({ onCompact });
    fireEvent.click(chip());
    fireEvent.click(compactBtn());
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    // @/lib/toast 会给 error 补上分档时长，断言取第一段文案（那才是给用户看的那句）
    expect(vi.mocked(toast.error).mock.calls[0][0])
      .toContain('本轮任务进行中，等它结束或先停止后再压缩');
    expect(screen.queryByText(/已压缩/)).not.toBeInTheDocument();
  });
});
