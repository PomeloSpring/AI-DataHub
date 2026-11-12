// 排队与模型繁忙提示的契约：位置/预计等待要看得见、排队可取消、重试有倒计时、
// 耗尽给两个出口，且绝不把内部报错原文或 id 摊到屏幕上。
import { render, screen, fireEvent, act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CapacityNotice } from '../CapacityNotice';
import type { CapacityState } from '@/stores/chatStore';

describe('CapacityNotice — 排队', () => {
  afterEach(() => vi.useRealTimers());
  it('显示排队位置与预计等待（服务端给的估计，不是猜的倒计时）', () => {
    render(<CapacityNotice state={{ phase: 'queued', position: 3, limit: 6, estimated_wait_s: 42 }} />);
    const notice = screen.getByRole('status');
    expect(notice).toHaveTextContent('第 3 位');
    expect(notice).toHaveTextContent('预计约 42 秒');
  });

  it('还没有样本可估时如实说"暂无平均时长可估"，不编一个秒数', () => {
    render(<CapacityNotice state={{ phase: 'queued', position: 1, limit: 6, estimated_wait_s: null }} />);
    expect(screen.getByRole('status')).toHaveTextContent('暂无平均时长可估');
  });

  it('「取消排队」可点，把取消动作交回调用方', () => {
    const onCancel = vi.fn();
    render(<CapacityNotice state={{ phase: 'queued', position: 2, limit: 6, estimated_wait_s: 10 }} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole('button', { name: '取消排队' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('提示里不出现内部 id / 原始报错文本', () => {
    const { container } = render(
      <CapacityNotice state={{ phase: 'queued', position: 2, limit: 6, estimated_wait_s: 10 } as CapacityState} />,
    );
    expect(container.textContent).not.toMatch(/session|conversation_id|sdk|Traceback/);
  });
});

describe('CapacityNotice — 自动重试与耗尽', () => {
  it('重试中显示第几次与剩余秒数，并随时间递减', () => {
    // 只在倒计时这一条用例里冻结时钟（其余用例走真时钟，不干扰 vitest 自己的超时）
    vi.useFakeTimers();
    try {
      render(<CapacityNotice state={{ phase: 'retrying', attempt: 2, max_attempts: 3, wait_s: 8, reason: '模型服务繁忙' }} />);
      expect(screen.getByRole('status')).toHaveTextContent('第 2/3 次');
      expect(screen.getByRole('status')).toHaveTextContent('8 秒后继续');
      act(() => { vi.advanceTimersByTime(3000); });
      expect(screen.getByRole('status')).toHaveTextContent('5 秒后继续');
    } finally {
      vi.useRealTimers();
    }
  });

  it('重试耗尽后给「重试本轮」与「切换模型」两个出口', () => {
    const onRetry = vi.fn();
    const onSwitchModel = vi.fn();
    render(
      <CapacityNotice
        state={{ phase: 'exhausted', reason: '模型服务当前繁忙，本轮未能完成' }}
        onRetry={onRetry}
        onSwitchModel={onSwitchModel}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('已停止自动重试');
    fireEvent.click(screen.getByRole('button', { name: /重试本轮/ }));
    fireEvent.click(screen.getByRole('button', { name: '切换模型' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onSwitchModel).toHaveBeenCalledTimes(1);
  });

  it('idle / started 不占据屏幕（本轮没排队就别提示）', () => {
    const { rerender, container } = render(<CapacityNotice state={{ phase: 'idle' }} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<CapacityNotice state={{ phase: 'started', waited_ms: 41800 }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
