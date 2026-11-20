import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import CanonicalDocPanel from '../CanonicalDocPanel';
import client from '@/api/client';
import { ontologyApi } from '@/api/ontology';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('@/api/ontology', () => ({ ontologyApi: { docView: vi.fn() } }));

/**
 * 本体 canonical 文档面板回归。
 *
 * 用户原话：「不要弹窗，用图中的方式」——面板必须是右侧整屏分栏（左侧留上下文），不是居中 Dialog；
 * 文档投影是服务端拼的（慢），服务端已做失效缓存，面板要如实显示「缓存」；
 * 第 4 档 SPARQL 打的是同一份事实源的 RDF 投影，本面板只读（写语句必须显式拦下，不能静默发出去）。
 */
const docView = ontologyApi.docView as unknown as ReturnType<typeof vi.fn>;
const post = (client as unknown as { post: ReturnType<typeof vi.fn> }).post;

const BASE = {
  open: true,
  onOpenChange: vi.fn(),
  modelId: 1789467214865307,
  modelName: '联耀业务本体',
  modelKind: 'business',
  liveVersion: '18',
  draftCount: 3,
  objectOptions: [{ key: 'case', name: '案例' }],
};

beforeEach(() => {
  docView.mockReset();
  post.mockReset();
  docView.mockResolvedValue({ data: { text: '{\n  "objects": []\n}', cached: false } });
});
afterEach(cleanup);

describe('CanonicalDocPanel（右侧分栏，不弹窗）', () => {
  it('渲染右侧面板：无 dialog 角色，头部事实源/版本/草案分段齐全', async () => {
    render(<CanonicalDocPanel {...BASE} />);
    // 不得是居中弹窗
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByText('canonical 文档')).toBeInTheDocument();
    expect(screen.getByText('只读')).toBeInTheDocument();
    expect(screen.getByText('生效版 v18')).toBeInTheDocument();
    expect(screen.getByText('草案 · 3 项')).toBeInTheDocument();
    await waitFor(() => expect(docView).toHaveBeenCalledWith(1789467214865307, 'json'));
  });

  it('切档只拉对应格式，命中服务端缓存时如实显示「缓存」', async () => {
    docView.mockImplementation(async (_id: number, as: string) => ({
      data: { text: 'a: 1', cached: as === 'yaml' },
    }));
    render(<CanonicalDocPanel {...BASE} />);
    await waitFor(() => expect(docView).toHaveBeenCalledWith(1789467214865307, 'json'));
    await userEvent.click(screen.getByRole('button', { name: 'YAML' }));
    await waitFor(() => expect(docView).toHaveBeenCalledWith(1789467214865307, 'yaml'));
    expect(await screen.findByText('缓存')).toBeInTheDocument();
  });

  it('文档取不到时显式报错并给重试（不显示空白正文假装空文档）', async () => {
    docView.mockRejectedValue({ response: { data: { detail: '模型不存在' } } });
    render(<CanonicalDocPanel {...BASE} />);
    expect(await screen.findByText(/模型不存在/)).toBeInTheDocument();
  });
});

describe('CanonicalDocPanel · SPARQL 档（同一事实源的图投影）', () => {
  it('只读校验拦住写语句：不发请求，显式说明去哪儿执行', async () => {
    render(<CanonicalDocPanel {...BASE} />);
    await userEvent.click(screen.getByRole('button', { name: 'SPARQL 查询' }));
    fireEvent.change(screen.getByPlaceholderText('输入SPARQL查询语句...'), {
      target: { value: 'DELETE WHERE { ?s ?p ?o }' },
    });
    await userEvent.click(screen.getByRole('button', { name: /执行查询/ }));
    expect(post).not.toHaveBeenCalled();
    expect(screen.getByText(/本面板只读/)).toBeInTheDocument();
  });

  it('示例查询打到模型所在命名图（business→ds:0）并渲染结果表', async () => {
    post.mockResolvedValue({ data: { records: [{ obj: 'obj:case', name: '案例' }], count: 1 } });
    render(<CanonicalDocPanel {...BASE} />);
    await userEvent.click(screen.getByRole('button', { name: 'SPARQL 查询' }));
    await userEvent.click(screen.getByRole('button', { name: '列出业务对象' }));
    await userEvent.click(screen.getByRole('button', { name: /执行查询/ }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/graph/query', {
      query: expect.stringContaining('GRAPH <http://ai-datahub.org/ontology/ds:0>'),
    }));
    expect(await screen.findByText('obj:case')).toBeInTheDocument();
    expect(screen.getByText('结果条数：1')).toBeInTheDocument();
  });

  it('查询失败显式回错（含服务端原因），不显示空结果表', async () => {
    post.mockRejectedValue({ response: { data: { detail: 'Oxigraph 不可达' } } });
    render(<CanonicalDocPanel {...BASE} />);
    await userEvent.click(screen.getByRole('button', { name: 'SPARQL 查询' }));
    fireEvent.change(screen.getByPlaceholderText('输入SPARQL查询语句...'), {
      target: { value: 'SELECT * WHERE { ?s ?p ?o } LIMIT 1' },
    });
    await userEvent.click(screen.getByRole('button', { name: /执行查询/ }));
    expect(await screen.findByText(/Oxigraph 不可达/)).toBeInTheDocument();
    expect(screen.queryByText(/结果条数/)).toBeNull();
  });
});
