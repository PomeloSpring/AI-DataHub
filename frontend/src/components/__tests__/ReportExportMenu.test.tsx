// 导出接线契约：屏幕上决定观感的四件套必须一份不落传进 standaloneHtml。
//
// 这四件各管一件事，缺哪件就退化哪块，而且都是"看起来导出了、其实不是屏幕上那份"：
// - `css`（collectAppCss）：配色/间距/栅格全靠它命中类名，缺了只剩语义结构；
// - `rootClass`：CSS 变量按根主题类取值，缺了深色主题导出成浅色；
// - `contentClass`：正文容器自己的类（卡片内边距/prose），innerHTML 不含它；
// - `shellClass`：版心宽度，必须与屏幕同源（lib/pageWidth 一处裁决），
//   导出侧另写一个 max-width 就是第二个口径 → 富版式被压回中间一列。
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ReportExportMenu from '@/components/ReportExportMenu';
import { READING, WIDE } from '@/lib/pageWidth';
import { downloadTextFile, renderedHtml, standaloneHtml } from '@/lib/reportExport';

vi.mock('@/lib/reportExport', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('@/lib/reportExport');
  return {
    ...actual,
    // 拍像素、读样式表都是浏览器能力，jsdom 给不了：打桩后只验参数确实被算出来并交下去
    renderedHtml: vi.fn(async () => '<p>正文</p>'),
    collectAppCss: vi.fn(() => '.tech{--primary:200 80% 50%}'),
    standaloneHtml: vi.fn(() => '<!DOCTYPE html>DOC'),
    downloadTextFile: vi.fn(),
  };
});

const mountContainer = () => {
  const host = document.createElement('div');
  host.className = 'mt-5 rounded-xl border bg-card p-5 sm:p-6';
  host.innerHTML = '<p>正文</p>';
  document.body.appendChild(host);
  return host;
};

async function pickHtmlFormat() {
  await userEvent.click(document.querySelector('[data-report-export]') as HTMLElement);
  // markdown 正文的清单是 [md, html, pdf]、html 正文是 [html, pdf]（不给 .md 是显式取舍）
  // → 按文案挑 HTML，不靠序号（序号随正文形态变，测的就不是同一条分支）
  const item = screen.getAllByRole('menuitem')
    .find(n => /HTML/i.test(n.textContent || '')) as HTMLElement;
  await userEvent.click(item);
}

beforeEach(() => {
  vi.clearAllMocks();
  document.body.innerHTML = '';
  document.documentElement.className = 'tech dark';
});

describe('导出 HTML 的装配', () => {
  it('html 正文：四件套齐送，版心走屏幕的宽版口径', async () => {
    const host = mountContainer();
    render(<ReportExportMenu title="入口点击分析" createdAt="2026-11-13T00:00:00Z"
      bodyFormat="html" body="<div>原始正文</div>" getContainer={() => host} />);
    await pickHtmlFormat();

    await waitFor(() => expect(standaloneHtml).toHaveBeenCalled());
    expect(standaloneHtml).toHaveBeenCalledWith(expect.objectContaining({
      title: '入口点击分析',
      innerHtml: '<p>正文</p>',                       // 取的是渲染后的 DOM，不是原始正文
      css: '.tech{--primary:200 80% 50%}',
      rootClass: 'tech dark',
      contentClass: 'mt-5 rounded-xl border bg-card p-5 sm:p-6',
      shellClass: WIDE,
    }));
  });

  it('markdown 长文：版心换成窄栏口径（与屏幕同一份判定，不是导出侧另配）', async () => {
    const host = mountContainer();
    render(<ReportExportMenu title="白皮书" createdAt="2026-11-13T00:00:00Z"
      bodyFormat="markdown" body="## 正文" getContainer={() => host} />);
    await pickHtmlFormat();

    await waitFor(() => expect(standaloneHtml).toHaveBeenCalled());
    expect(vi.mocked(standaloneHtml).mock.calls[0][0]).toMatchObject({ shellClass: READING });
  });

  it('落盘的是算出来的文档，不是原始正文', async () => {
    const host = mountContainer();
    render(<ReportExportMenu title="入口点击分析" createdAt="2026-11-13T00:00:00Z"
      bodyFormat="html" body="<div>原始正文</div>" getContainer={() => host} />);
    await pickHtmlFormat();

    await waitFor(() => expect(downloadTextFile).toHaveBeenCalled());
    expect(vi.mocked(downloadTextFile).mock.calls[0][1]).toBe('<!DOCTYPE html>DOC');
    expect(vi.mocked(renderedHtml)).toHaveBeenCalledWith(host);
  });
});
