import { readFileSync } from 'node:fs';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import WaitingIndicator from '../chat/WaitingIndicator';

/**
 * 等待文案的纪律：只说当前确实成立的事，不预测这次在干什么。
 * 这组用例锁两件事 —— ① 默认文案是一句通用等待；② 不许回到"轮换文案池 + 定时器"：
 * 轮换出来的每句话都会被读者当成对本次处理的事实描述（知识问答被写成"正在获取数据"就是误导）。
 */

// 源码级断言：行为用例管不住“以后有人把轮换池加回来”。路径按 vitest 的 cwd（frontend）解析，
// 不用 import.meta.url —— 它在 vite 变换后不是 file: 协议，readFileSync 会直接报错
const source = readFileSync('src/components/chat/WaitingIndicator.tsx', 'utf8');

describe('WaitingIndicator — 等待期文案不做无依据的预测', () => {
  it('没有阶段信号时只说「正在等待模型响应」', () => {
    render(<WaitingIndicator />);
    expect(screen.getByRole('status')).toHaveTextContent('正在等待模型响应');
  });

  it('组件不轮换文案：没有定时器，也没有轮换池', () => {
    expect(source).not.toContain('setInterval');
    expect(source).not.toMatch(/IDLE_LABELS/);
  });

  it('阶段文案不预设结果形态：调用工具不等于在获取数据', () => {
    const { container } = render(<WaitingIndicator stage="tools" />);
    expect(screen.getByRole('status').textContent).toBe('正在调用工具');
    expect(screen.getByRole('status').textContent).not.toContain('数据');
    container.remove();
    render(<WaitingIndicator stage="thinking" />);
    expect(screen.getByRole('status')).toHaveTextContent('正在思考');
  });

  it('调用方给的显式文案优先（后端进度消息、停止中等等原样显示）', () => {
    render(<WaitingIndicator stage="composing" label="正在切换工作空间…" />);
    expect(screen.getByRole('status')).toHaveTextContent('正在切换工作空间…');
  });

  it('三点动画保留并标 aria-hidden：表达“还在处理”而不是文字承诺', () => {
    const { container } = render(<WaitingIndicator />);
    expect(container.querySelectorAll('.typing-dot')).toHaveLength(3);
    expect(container.querySelector('span[aria-hidden]')).not.toBeNull();
  });
});
