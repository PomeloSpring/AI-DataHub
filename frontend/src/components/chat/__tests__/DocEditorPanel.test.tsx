// 文档编辑面板契约（转文档编辑）：初稿与气泡渲染同源、正文以 DOM 为事实源、
// 保存失败必须拦住关闭并给重试入口（静默丢弃用户改动比报错严重得多）。
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import DocEditorPanel from '../DocEditorPanel';
import type { ChatDoc } from '@/stores/chatStore';

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }));
import { toast } from 'sonner';

const toastTexts = (mock: any): string[] =>
  ((mock?.mock?.calls ?? []) as unknown[][]).map(c => String(c[0]));

const doc = (over: Partial<ChatDoc> = {}): ChatDoc => ({
  title: '', html: '', created_at: '2026-11-06T10:51:00',
  updated_at: '2026-11-06T10:51:00', revisions: 0, ...over,
});

const body = () => document.querySelector('[aria-label="文档正文"]') as HTMLElement;
const titleInput = () => screen.getByLabelText('文档标题') as HTMLInputElement;
const closeBtn = () => screen.getByRole('button', { name: '关闭' }) as HTMLButtonElement;

const writeText = vi.fn().mockResolvedValue(undefined);
const anchorClick = vi.fn();

const setup = (over: Partial<ChatDoc> = {}, onSave = vi.fn().mockResolvedValue(true)) => {
  const props = {
    doc: doc(over),
    sourceMarkdown: '## 两种模式\n\n1. **快速模式**：响应快\n\n```chart\n{"title":"趋势","columns":["a"],"rows":[[1]]}\n```',
    onSave,
    onClose: vi.fn(),
  };
  render(<DocEditorPanel {...props} />);
  return { ...props, onSave };
};

describe('DocEditorPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    writeText.mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    URL.createObjectURL = vi.fn(() => 'blob:doc');
    URL.revokeObjectURL = vi.fn();
    window.HTMLAnchorElement.prototype.click = anchorClick;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('还没有正文时用回答 markdown 初稿化，并落一次库（编辑 1 次）', async () => {
    const { onSave } = setup();
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = (onSave.mock.calls[0] as any[])[0] as ChatDoc;
    expect(saved.html).toContain('<h2>');
    expect(saved.html).toContain('快速模式');
    // 特殊块不进文档正文的裸 JSON，而是可读占位（与复制口径同源）
    expect(saved.html).toContain('【图表：趋势】');
    expect(saved.html).not.toContain('"columns"');
    expect(saved.revisions).toBe(1);
    expect(body().innerHTML).toContain('<h2>');
    await waitFor(() => expect(screen.getByText(/已自动保存（编辑 1 次）/)).toBeInTheDocument());
  });

  it('已有正文直接展示，挂载时不重复保存', async () => {
    const { onSave } = setup({ title: '销售分析', html: '<p>已有正文</p>', revisions: 3 });
    expect(body().innerHTML).toBe('<p>已有正文</p>');
    expect((titleInput().value)).toBe('销售分析');
    expect(screen.getByText('编辑 3 次')).toBeInTheDocument();
    await new Promise(r => setTimeout(r, 50));
    expect(onSave).not.toHaveBeenCalled();
  });

  it('改标题触发防抖自动保存（带新标题，正文原样）', async () => {
    const { onSave } = setup({ title: '旧标题', html: '<p>正文</p>', revisions: 1 });
    fireEvent.change(titleInput(), { target: { value: '月度销售复盘' } });
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1), { timeout: 3000 });
    const saved = (onSave.mock.calls[0] as any[])[0] as ChatDoc;
    expect(saved.title).toBe('月度销售复盘');
    expect(saved.html).toBe('<p>正文</p>');
    expect(saved.revisions).toBe(2);
    expect(saved.created_at).toBe('2026-11-06T10:51:00');
  });

  it('保存失败时拦住关闭并给重试入口（不静默丢弃改动）', async () => {
    const onSave = vi.fn().mockResolvedValue(false);
    setup({ title: '旧标题', html: '<p>正文</p>', revisions: 1 }, onSave);
    fireEvent.change(titleInput(), { target: { value: '改了但没存上' } });
    fireEvent.click(closeBtn());
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(screen.getByText('保存失败，改动只在当前页面，关掉就没了')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试保存' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: '关闭' })).toBeInTheDocument());
    // 重试成功后才放行关闭
    onSave.mockResolvedValueOnce(true);
    fireEvent.click(screen.getByRole('button', { name: '重试保存' }));
    await waitFor(() => expect(screen.queryByText(/保存失败/)).not.toBeInTheDocument());
    fireEvent.click(closeBtn());
    await waitFor(() => expect(screen.queryByText(/保存失败/)).not.toBeInTheDocument());
  });

  it('复制优先富文本，浏览器不支持富文本剪贴板时如实说明只给了纯文本', async () => {
    setup({ html: '<p>正文 <strong>粗体</strong></p>', title: 'T', revisions: 1 });
    fireEvent.click(screen.getByRole('button', { name: '复制' }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const copied = (writeText.mock.calls[0] as any[])[0] as string;
    expect(copied).toContain('正文');
    expect(copied).not.toContain('<strong>');
    expect(toastTexts(toast.success)).toContain('已复制为纯文本（当前浏览器不支持富文本剪贴板）');
  });

  it('下载产出独立 HTML 文件（标题 + 正文原样，不做二次格式转换）', () => {
    // jsdom 的 Blob/Response 读不回内容，直接拦截 Blob 入参看写进文件的内容
    const parts: string[] = [];
    vi.spyOn(globalThis, 'Blob').mockImplementation(function (this: void, init?: any, opts?: any) {
      parts.push(String(init?.[0] ?? ''));
      return { type: opts?.type ?? '', size: 0 } as unknown as Blob;
    } as any);
    setup({ html: '<p>正文</p>', title: '销售复盘', revisions: 1 });
    fireEvent.click(screen.getByRole('button', { name: '下载' }));
    expect(anchorClick).toHaveBeenCalledTimes(1);
    expect(toastTexts(toast.success).some(t => t.startsWith('已开始下载'))).toBe(true);
    expect(parts[0]).toContain('<title>销售复盘</title>');
    expect(parts[0]).toContain('<p>正文</p>');
  });

  it('浏览器不支持富文本命令时显式提示，而不是假装改好了', async () => {
    setup({ html: '<p>正文</p>', title: 'T', revisions: 1 });
    expect(screen.queryByText(/不支持富文本工具栏/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '加粗' }));
    await waitFor(() => expect(screen.getByText(/当前浏览器不支持富文本工具栏/)).toBeInTheDocument());
  });
});
