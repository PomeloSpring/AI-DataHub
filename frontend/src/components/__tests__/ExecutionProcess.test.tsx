/**
 * ExecutionProcess 状态口径回归（两轮用户报障）：
 * ① 「结果都输出了，思考中还一直转圈」——
 *    历史缺陷: inline 窗口写死 timeline(true)；且转圈跟随 loading(=未收到 done)，
 *    而后端 done 要等 CLI 收尾核验才发。修复: 思考块转圈由 thinkingActive 驱动
 *    （开始出正文即 false）。
 * ② 「任务在运行中，但看不出在运行中」——
 *    历史缺陷: 运行中状态与思考转圈共用一个信号，Chat 侧用 !msg.content 兜底,
 *    agent loop 中模型输出中间文本后还会续调工具，结果执行卡被误渲成「已完成」绿勾。
 *    修复: 卡头 isStreaming 只跟随 done（loading），文本出现不改变运行中状态。
 *
 * 本文件锁定组件层契约：两个信号各自驱动各自的状态，互不越界。
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import ExecutionProcess from '../ExecutionProcess';

const thinkingOnly = {
  process: [{ kind: 'thinking' as const, text: 'The user is asking something...' }],
  thinking: 'The user is asking something...',
};

const spinnerCount = (container: HTMLElement) =>
  container.querySelectorAll('.animate-spin').length;

describe('ExecutionProcess 思考转圈跟随 thinkingActive', () => {
  it('思考阶段进行中（isStreaming 缺省跟随）显示「思考中...」', () => {
    render(<ExecutionProcess {...thinkingOnly} isStreaming />);
    expect(screen.getByText('思考中...')).toBeInTheDocument();
  });

  it('流式结束后即使手动展开过程，也不再显示「思考中...」', () => {
    const { container } = render(<ExecutionProcess {...thinkingOnly} isStreaming={false} />);
    // 收起态先点开 inline 过程窗口（旧实现这里写死 streaming=true → 转圈复现）
    expect(screen.getAllByText(/The user is asking/).length).toBeGreaterThan(0);
    expect(screen.queryByText('思考中...')).not.toBeInTheDocument();
    expect(spinnerCount(container)).toBe(0);
  });

  it('回合仍在跑但已出正文（中间文本）：思考块停转，卡头保持运行中', () => {
    const { container } = render(
      <ExecutionProcess {...thinkingOnly} isStreaming thinkingActive={false} />,
    );
    // ① 思考阶段已结束 → 不再「思考中...」
    expect(screen.queryByText('思考中...')).not.toBeInTheDocument();
    // ② 回合未结束 → 卡头不得出现「已完成」，保持运行中文案 + spinner
    expect(screen.queryByText(/已完成/)).not.toBeInTheDocument();
    expect(screen.getByText('正在思考与调用工具…')).toBeInTheDocument();
    expect(spinnerCount(container)).toBeGreaterThan(0);
  });
});

describe('ExecutionProcess 运行中状态跟随 done（isStreaming），不受文本影响', () => {
  it('done 未达（isStreaming=true）：过程窗口保持展开，供实时观察', () => {
    render(<ExecutionProcess {...thinkingOnly} isStreaming thinkingActive={false} />);
    expect(screen.getAllByText(/The user is asking/).length).toBeGreaterThan(0);
  });

  it('done 已达（isStreaming=false）：卡头渲成「已完成」，无 spinner', () => {
    const { container } = render(<ExecutionProcess {...thinkingOnly} isStreaming={false} />);
    expect(screen.getByText('已完成，点击滑动展开过程')).toBeInTheDocument();
    expect(screen.queryByText('正在思考与调用工具…')).not.toBeInTheDocument();
    expect(spinnerCount(container)).toBe(0);
  });
});
