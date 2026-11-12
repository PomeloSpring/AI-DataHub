// 消息操作行契约（参照千问）：复制给纯文本、复制为Markdown 给原文、转文档编辑只给助手回答；
// 写剪贴板失败/无纯文本可复制时必须显式提示，不得"点了没反应"或假装已复制。
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import MessageActions from '../MessageActions';
import type { ChatDoc } from '@/stores/chatStore';

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }));
import { toast } from 'sonner';

const writeText = vi.fn().mockResolvedValue(undefined);

/** lib/toast 包装器会给每条 toast 追加分档时长，断言只看文案本身 */
const toastTexts = (mock: any): string[] =>
  ((mock?.mock?.calls ?? []) as unknown[][]).map(c => String(c[0]));

const doc = (over: Partial<ChatDoc> = {}): ChatDoc => ({
  title: '无标题', html: '<p>正文</p>', created_at: '2026-11-06T10:51:00',
  updated_at: '2026-11-06T10:51:00', revisions: 1, ...over,
});

const copyBtn = () => screen.getByRole('button', { name: '复制回答文本' }) as HTMLButtonElement;
const menuBtn = () => screen.getByRole('button', { name: '复制方式' }) as HTMLButtonElement;
const moreBtn = () => screen.queryByRole('button', { name: '更多操作' });

const openItem = async (trigger: HTMLElement, name: string) => {
  await userEvent.click(trigger);
  const item = await screen.findByRole('menuitem', { name });
  await userEvent.click(item);
};

describe('MessageActions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    writeText.mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  });

  it('点复制图标写入纯文本（去掉 markdown 标记）', async () => {
    render(<MessageActions markdown={'## 标题\n\n**加粗**正文'} />);
    await userEvent.click(copyBtn());
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('标题\n\n加粗正文'));
    expect(toastTexts(toast.success)).toContain('已复制');
  });

  it('下拉里「复制为Markdown」写入原文，「复制」写入纯文本', async () => {
    render(<MessageActions markdown={'## 标题\n\n- 条目'} />);
    await openItem(menuBtn(), '复制为Markdown');
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith('## 标题\n\n- 条目'));
    expect(toastTexts(toast.success)).toContain('已复制为 Markdown');
    await openItem(menuBtn(), '复制');
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith('标题\n\n• 条目'));
  });

  it('助手回答给转文档编辑；已有文档时变成继续编辑（不重复建）', async () => {
    const onCreateDoc = vi.fn();
    const onOpenDoc = vi.fn();
    const { rerender } = render(
      <MessageActions markdown="正文" allowDoc onCreateDoc={onCreateDoc} onOpenDoc={onOpenDoc} />,
    );
    await openItem(moreBtn()!, '转文档编辑');
    expect(onCreateDoc).toHaveBeenCalledTimes(1);

    rerender(
      <MessageActions markdown="正文" allowDoc doc={doc()} onCreateDoc={onCreateDoc} onOpenDoc={onOpenDoc} />,
    );
    await openItem(moreBtn()!, '继续编辑文档');
    expect(onOpenDoc).toHaveBeenCalledTimes(1);
    expect(onCreateDoc).toHaveBeenCalledTimes(1);
  });

  it('用户消息只给复制，不给转文档编辑入口', () => {
    render(<MessageActions markdown="我提问的内容" />);
    expect(moreBtn()).toBeNull();
  });

  it('赞踩与复制同一行（不分两端）；已反馈后只留标注', async () => {
    const onUp = vi.fn();
    const onDown = vi.fn();
    const { rerender } = render(
      <MessageActions markdown="正文" feedback={{ onUp, onDown }} />,
    );
    const row = copyBtn().closest('div')!.parentElement as HTMLElement;
    expect(screen.getAllByRole('button').every(b => row.contains(b))).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: '结果准确？满意' }));
    expect(onUp).toHaveBeenCalledTimes(1);

    rerender(<MessageActions markdown="正文" feedback={{ value: 'up', onUp, onDown }} />);
    expect(screen.getByText('👍 已反馈')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '结果准确？满意' })).toBeNull();
  });

  it('存为报告草稿不是每轮都有：只在服务端判 reportable 时收进「更多」菜单', async () => {
    const onSave = vi.fn();
    const { rerender } = render(<MessageActions markdown="正文" allowDoc />);
    await openItem(moreBtn()!, '转文档编辑');
    expect(screen.queryByRole('menuitem', { name: /存为报告草稿/ })).toBeNull();

    rerender(<MessageActions markdown="正文" allowDoc report={{ onSave }} />);
    await openItem(moreBtn()!, '存为报告草稿');
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it('已有草稿时菜单文案改成「继续编辑报告草稿」（一条回答只一份，不重复建）', async () => {
    const onSave = vi.fn();
    render(
      <MessageActions markdown="正文" report={{ onSave, label: '继续编辑报告草稿' }} />,
    );
    await openItem(moreBtn()!, '继续编辑报告草稿');
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('menuitem', { name: '存为报告草稿' })).toBeNull();
  });

  it('保存报告进行中菜单项置灰，不重复提交', async () => {
    const onSave = vi.fn();
    render(<MessageActions markdown="正文" report={{ busy: true, onSave }} />);
    await userEvent.click(moreBtn()!);
    const item = await screen.findByRole('menuitem', { name: /存为报告草稿/ });
    expect(item.getAttribute('data-disabled')).not.toBeNull();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('流式进行中禁用：正文未定稿不给复制', () => {
    render(<MessageActions markdown="写到一半" disabled />);
    expect(copyBtn().disabled).toBe(true);
    expect(menuBtn().disabled).toBe(true);
  });

  it('没有可复制文本时提示原因，而不是假装已复制', async () => {
    render(<MessageActions markdown={'---'} />);
    await userEvent.click(copyBtn());
    await waitFor(() => expect(toastTexts(toast.info))
      .toContain('这条回答没有可复制的文本内容'));
    expect(writeText).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('剪贴板写入失败要报错（两条路都失败才算失败）', async () => {
    writeText.mockRejectedValueOnce(new Error('denied'));
    render(<MessageActions markdown="正文" />);
    await userEvent.click(copyBtn());
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.success).not.toHaveBeenCalled();
  });
});
