/**
 * ReportCard 渲染口径回归：交付文档必须"按引用取回 + 隔离渲染 + 失败可见"。
 *
 * 锁住四条历史缺陷同类风险：
 * 1. 整份 HTML 走 DOMPurify 白名单会被剥光 style/svg → 报告一片黑白（"存了不生效"同族）；
 * 2. 引用取不到时把 JSON 源码或空白当正文给用户；
 * 3. 未接线的形态静默不渲染；
 * 4. 把内部 doc_id 当标题显示（违反 ui-resource-display）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

const get = vi.fn();
vi.mock('@/api/client', () => ({ default: { get: (...a: any[]) => get(...a) } }));

import ReportCard from '../chat/ReportCard';

// ReportDoc 会拉起图表/表格/mermaid 的重依赖：本文件只锁“形态分派与外壳”，不重测逐章渲染
vi.mock('../chat/ReportDoc', () => ({
  default: () => <div data-testid="spec-doc">结构化章节</div>,
}));

const HTML_BODY = '<html><head><style>:root{--primary:#4f46e5}</style></head>'
  + '<body><h1>订单分析报告</h1><svg><path d="M0"/></svg></body></html>';

const doc = (over: Record<string, any> = {}) => ({
  id: 'doc123', conversation_id: 42, kind: 'doc', body_kind: 'html',
  title: '订单分析报告', theme: 'datafoundry', template_key: null,
  digest: 'abc', size_chars: HTML_BODY.length, warnings: [], row_counts: null,
  updated_at: '2026-11-13T00:00:00', body: HTML_BODY, ...over,
});

const fail = (status: number) => { throw { response: { status } }; };

beforeEach(() => { get.mockReset(); });

let clickSpy: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
});
afterEach(() => { clickSpy.mockRestore(); });

describe('ReportCard 整页文档渲染', () => {
  it('按引用取回后在隔离 iframe 里渲染，样式与内联 SVG 原样保留', async () => {
    get.mockResolvedValue({ data: doc() });
    render(<ReportCard docId="doc123" bodyKind="html" title="订单分析报告" />);
    const frame = await screen.findByTitle('订单分析报告');
    expect(frame.tagName.toLowerCase()).toBe('iframe');
    // 与 ReportContent/ArtifactCard 同一口径：allow-scripts、不给 same-origin
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
    const src = frame.getAttribute('srcDoc') || '';
    expect(src).toContain('<style>:root{--primary:#4f46e5}</style>');
    expect(src).toContain('<svg><path d="M0"/></svg>');
    expect(get).toHaveBeenCalledWith('/chat/render-document/doc123');
  });

  it('请求走文档标题，不把内部 doc_id 当标题显示', async () => {
    get.mockResolvedValue({ data: doc() });
    render(<ReportCard docId="doc123" bodyKind="html" />);
    await screen.findByText('订单分析报告');
    expect(screen.queryByText(/doc123/)).toBeNull();
  });

  it('体检的非阻断提示要可见（不得让用户以为完全按规范生成）', async () => {
    get.mockResolvedValue({ data: doc({ warnings: ['本轮含地图数据，HTML 通道已退为表格'] }) });
    render(<ReportCard docId="doc123" bodyKind="html" />);
    expect(await screen.findByText(/已退为表格/)).toBeInTheDocument();
  });

  it('不自动触发下载；点「下载」才落文件', async () => {
    get.mockResolvedValue({ data: doc() });
    render(<ReportCard docId="doc123" bodyKind="html" />);
    await screen.findByTitle('订单分析报告');
    expect(clickSpy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /下载/ }));
    expect(clickSpy).toHaveBeenCalledTimes(1);
  });

  it('全屏阅读可打开（长报告不受 520px 裁切）', async () => {
    get.mockResolvedValue({ data: doc() });
    render(<ReportCard docId="doc123" bodyKind="html" />);
    fireEvent.click(await screen.findByRole('button', { name: /全屏阅读/ }));
    const frames = await screen.findAllByTitle('订单分析报告');
    expect(frames.some((f: HTMLElement) => (f.className || '').includes('h-[80vh]'))).toBe(true);
  });

  it('真未接线的形态（图表数据对象）显式说明，而不是空白或源码', async () => {
    get.mockResolvedValue({ data: doc({ body_kind: 'chart-data', body: '{"columns":[],"rows":[]}' }) });
    render(<ReportCard docId="doc123" bodyKind="chart-data" />);
    expect(await screen.findByText(/尚未支持就地渲染/)).toBeInTheDocument();
    expect(document.querySelector('iframe')).toBeNull();
  });

  it('spec 形态走平台组件逐章渲染（不是 iframe），且不拿章节 JSON 当 .html 下载', async () => {
    get.mockResolvedValue({ data: doc({ body_kind: 'spec', body: JSON.stringify({ sections: [] }) }) });
    render(<ReportCard docId="doc123" bodyKind="spec" />);
    expect(await screen.findByTestId('spec-doc')).toBeInTheDocument();
    expect(document.querySelector('iframe')).toBeNull();
    expect(screen.queryByRole('button', { name: /下载/ })).toBeNull();
  });
});

describe('ReportCard 取回失败必须显式（区分成因）', () => {
  it('404 说清"已被清理"并给重试，不回退渲染源码', async () => {
    get.mockImplementation(() => fail(404));
    render(<ReportCard docId="doc123" bodyKind="html" title="订单分析报告" />);
    expect(await screen.findByText(/已被清理/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /重试/ })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('doc_id');
  });

  it('403 说清"无权访问"，与"被清理"文案不同（排查方向不同）', async () => {
    get.mockImplementation(() => fail(403));
    render(<ReportCard docId="doc123" bodyKind="html" />);
    expect(await screen.findByText(/无权访问/)).toBeInTheDocument();
  });

  it('其他状态码给可重试文案，不静默空白', async () => {
    get.mockImplementation(() => fail(500));
    render(<ReportCard docId="doc123" bodyKind="html" />);
    expect(await screen.findByText(/暂时打不开/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /重试/ })).toBeInTheDocument();
  });

  it('重试按钮重新拉取并成功渲染', async () => {
    get.mockImplementationOnce(() => fail(500));
    const { rerender } = render(<ReportCard docId="doc123" bodyKind="html" />);
    await screen.findByRole('button', { name: /重试/ });
    get.mockResolvedValueOnce({ data: doc() });
    fireEvent.click(screen.getByRole('button', { name: /重试/ }));
    await waitFor(() => expect(screen.getByTitle('订单分析报告')).toBeInTheDocument());
    void rerender;
  });
});

// 外壳的「存为报告草稿」入口：入口可以有两处（消息操作行与交付卡），流程只能有一份，
// 所以卡片只接外层传进来的动作；服务端未判 reportable 时（不传）不能给一个调不通的按钮。
describe('ReportCard 存报告草稿入口', () => {
  it('外层给了动作就有入口，点击走外层同一个 onSave', async () => {
    const onSave = vi.fn();
    get.mockResolvedValue({ data: doc() });
    render(<ReportCard docId="doc123" bodyKind="html" saveReport={{ onSave }} />);
    const btn = await screen.findByRole('button', { name: /存为报告草稿/ });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it('已有草稿时按外层给的文案（继续编辑，不是再存一份）', async () => {
    get.mockResolvedValue({ data: doc() });
    render(<ReportCard docId="doc123" bodyKind="html"
      saveReport={{ label: '继续编辑报告草稿', onSave: () => {} }} />);
    expect(await screen.findByRole('button', { name: /继续编辑报告草稿/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^存为报告草稿$/ })).toBeNull();
  });

  it('未传动作（本轮服务端未判为可报告）就不渲染该按钮，也不拿 busy 当默认态', async () => {
    get.mockResolvedValue({ data: doc() });
    render(<ReportCard docId="doc123" bodyKind="html" />);
    await screen.findByTitle('订单分析报告');
    expect(screen.queryByRole('button', { name: /报告草稿/ })).toBeNull();
  });

  it('保存进行中时按钮禁用（不让人连点出两份草稿）', async () => {
    get.mockResolvedValue({ data: doc() });
    render(<ReportCard docId="doc123" bodyKind="html"
      saveReport={{ busy: true, onSave: () => {} }} />);
    expect(await screen.findByRole('button', { name: /存为报告草稿/ })).toBeDisabled();
  });

  it('spec 形态同样给入口（存草稿不看形态，下载才看）', async () => {
    get.mockResolvedValue({ data: doc({ body_kind: 'spec', body: JSON.stringify({ sections: [] }) }) });
    render(<ReportCard docId="doc123" bodyKind="spec" saveReport={{ onSave: () => {} }} />);
    expect(await screen.findByRole('button', { name: /存为报告草稿/ })).toBeInTheDocument();
  });
});
