import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { IntentGateBadge } from '../chat/IntentGateBadge';

/**
 * 前置意图漏斗在聊天界面的呈现契约：**只留逃生门，不打内部术语标记**。
 *
 * 用户不需要在每条回答下面看到"未调用模型·前置意图层直答"——那是实现细节；
 * 但"这条被前置层拦了、判错了怎么救"的动作必须在。断言把两端都钉住：
 * 文案不出现，按钮可点且把 force_agent 那条路走通（由调用方实现）。
 */
describe('IntentGateBadge', () => {
  it('渲染「转深度分析」按钮，不渲染内部术语标记', () => {
    const onDeepAnalysis = vi.fn();
    render(<IntentGateBadge gate={{ mode: 'enforce', layer: 'L0', label: 'social_ack', reason: '整句由寒暄/应答词构成' }}
                            onDeepAnalysis={onDeepAnalysis} />);
    const btn = screen.getByRole('button', { name: '转深度分析' });
    expect(btn).toBeInTheDocument();
    fireEvent.click(btn);
    expect(onDeepAnalysis).toHaveBeenCalledTimes(1);

    const body = document.body.textContent || '';
    for (const banned of ['未调用模型', '前置意图层', 'social_ack', 'enforce']) {
      expect(body).not.toContain(banned);
    }
  });

  it('没有回调时不占位（走模型的回合不该出现空容器）', () => {
    const { container } = render(<IntentGateBadge gate={{ mode: 'enforce' }} />);
    expect(container.firstChild).toBeNull();
  });
});
