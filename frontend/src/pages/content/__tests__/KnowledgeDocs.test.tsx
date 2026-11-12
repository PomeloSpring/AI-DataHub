import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import client from '@/api/client';
import { usePermissionStore } from '@/stores/permissionStore';
import KnowledgeDocs from '../KnowledgeDocs';
import KnowledgeDocDetail from '../KnowledgeDocDetail';
import type { KbBase, KbDoc, KbTree } from '@/api/kbDocs';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
// 预览渲染组件若被引用就会画出「正文渲染」—— 保留桩，用来证明正文区不再走渲染预览
vi.mock('@/components/MarkdownWithCharts', () => ({ default: () => <div>正文渲染</div> }));

const base = (over: Partial<KbBase> = {}): KbBase => ({
  name: '云端库', description: '系统自身能力文档', kb_type: 'qmind', status: 'active',
  supports_docs: true, can_edit: true, reason: '', ...over,
});

const doc = (over: Partial<KbDoc> = {}): KbDoc => ({
  source_id: 's1', title: '00-说明.md', is_dir: false, source_type: 'markdown', status: 'ready',
  content: '# 站点全景\n\n默认分站呈现。', content_sha256: 'sha-base',
  derived: false, read_only_reason: '', updated_at: '2026-11-04 10:00:00', ...over,
});

const tree: KbTree = {
  base: { name: '云端库', description: '系统自身能力文档', kb_type: 'qmind', status: 'active' },
  nodes: [
    { source_id: 'd1', title: '合规', is_dir: true, parent_id: '', path: '', source_type: '',
      status: 'ready', error_message: '', updated_at: '', children: [
        { source_id: 's2', title: '01-条款.md', is_dir: false, parent_id: 'd1', path: '',
          source_type: 'markdown', status: 'ready', error_message: '', updated_at: '', children: [] },
      ] },
    { source_id: 's1', title: '00-说明.md', is_dir: false, parent_id: '', path: '',
      source_type: 'markdown', status: 'pending', error_message: '', updated_at: '', children: [] },
  ],
  doc_count: 2, folder_count: 1, flat: false,
};

/** 按 URL 前缀分派；未登记即抛错（用例与契约脱节必须炸出来） */
function route(handlers: Record<string, unknown>) {
  vi.mocked(client.get).mockImplementation(((url: string) => {
    const hit = Object.entries(handlers).find(([prefix]) => url.startsWith(prefix));
    if (!hit) throw new Error(`未登记的 GET ${url}`);
    return Promise.resolve({ data: hit[1] });
  }) as never);
}

// 请求 URL 里库名是 encodeURIComponent 后的形态, 前缀必须同形态才命中
const NB = encodeURIComponent('云端库');

const grant = (perms: string[]) =>
  usePermissionStore.setState({ loaded: true, unrestricted: false, perms, allowedMenus: [] });

const defaultHandlers = () => ({
  [`/content/knowledge-bases/${NB}/tree`]: tree,
  [`/content/knowledge-bases/${NB}/documents`]: doc(),
  '/content/knowledge-bases': { items: [base()] },
});

beforeEach(() => {
  vi.clearAllMocks();
  // 顺序即优先级: 具体前缀在前, 清单端点 (/content/knowledge-bases) 兜底在后
  route(defaultHandlers());
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  usePermissionStore.setState({ loaded: false, unrestricted: true, perms: null, allowedMenus: null });
});

/** 清单与详情是同一条菜单的两级路由，用例走真实跳转链路 */
function renderRoutes(at = '/content/kb-docs') {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <Routes>
        <Route path="/content/kb-docs" element={<KnowledgeDocs />} />
        <Route path="/content/kb-docs/:kb" element={<KnowledgeDocDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

const renderDetail = () => renderRoutes(`/content/kb-docs/${NB}`);

describe('知识库文档 — 清单页（卡片）', () => {
  it('卡片显示库级业务事实：名称、类型业务名、启用状态；不显示裸类型码', async () => {
    grant(['kbdoc:read', 'kbdoc:write']);
    renderRoutes();
    expect(await screen.findByText('云端库')).toBeInTheDocument();
    // 类型显示业务名（与系统配置「知识库」同口径），不是裸 qmind
    expect(screen.getByText('QMind 知识检索')).toBeInTheDocument();
    expect(screen.queryByText('qmind')).not.toBeInTheDocument();
    expect(screen.getByText('已启用')).toBeInTheDocument();
    expect(screen.getByText('系统自身能力文档')).toBeInTheDocument();
    expect(client.get).toHaveBeenCalledWith('/content/knowledge-bases');
    // 清单页不逐库拉树（那要每库一次云端 CLI）：篇数只在详情里出现
    expect(screen.queryByText('2 篇 / 1 个目录')).not.toBeInTheDocument();
    expect(client.get).not.toHaveBeenCalledWith(`/content/knowledge-bases/${NB}/tree`);
  });

  it('点卡片进入详情并在那里拉目录树', async () => {
    grant(['kbdoc:read', 'kbdoc:write']);
    renderRoutes();
    fireEvent.click(await screen.findByRole('link', { name: '进入知识库 云端库' }));
    expect(await screen.findByText('目录树')).toBeInTheDocument();
    expect(screen.getByText('合规')).toBeInTheDocument();
    await waitFor(() =>
      expect(client.get).toHaveBeenCalledWith(`/content/knowledge-bases/${NB}/tree`));
    // 返回入口给得明确（详情是子路由，不靠浏览器后退）
    expect(screen.getByRole('button', { name: /返回知识库清单/ })).toBeInTheDocument();
  });

  it('不支持文档树的库照样列出来：卡片不可点 + 服务端原因原文', async () => {
    grant(['kbdoc:read']);
    route({
      '/content/knowledge-bases': {
        items: [base({ name: '本地库', kb_type: 'local', supports_docs: false, can_edit: false,
                       reason: '文档树目前只支持云端知识库（qmind）' })],
      },
    });
    renderRoutes();
    const card = await screen.findByRole('link', { name: '进入知识库 本地库' });
    expect(card).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('本地数据目录')).toBeInTheDocument();
    // 原因用服务端原文（不另写一套文案），也不藏掉这个库
    expect(screen.getByText('文档树目前只支持云端知识库（qmind）')).toBeInTheDocument();
  });

  it('清单拉失败显式报错并可重试，不画空清单', async () => {
    grant(['kbdoc:read']);
    vi.mocked(client.get).mockRejectedValue({
      response: { status: 502, data: { detail: '知识库清单读取失败：timeout' } },
    } as never);
    renderRoutes();
    expect(await screen.findByText('知识库清单读取失败：timeout')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /进入知识库/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /刷新清单/ })).toBeInTheDocument();
  });
});

describe('知识库文档 — 详情页：目录树与只提交修改部分', () => {
  it('树按目录优先渲染，文档带云端状态与篇数', async () => {
    grant(['kbdoc:read', 'kbdoc:write']);
    renderDetail();
    expect(await screen.findByText('合规')).toBeInTheDocument();
    expect(screen.getByText('00-说明.md')).toBeInTheDocument();
    expect(screen.getByText('2 篇 / 1 个目录')).toBeInTheDocument();
    // 非 ready 的文档要显式标出来（"检索暂时命中不到"不是失败，但必须看得见）
    expect(screen.getByText('pending')).toBeInTheDocument();
  });

  it('点开文档直接进编辑区：没有预览这一层，未改动不给提交', async () => {
    grant(['kbdoc:read', 'kbdoc:write']);
    renderDetail();
    fireEvent.click(await screen.findByLabelText('打开 00-说明.md'));
    await waitFor(() => expect(screen.getByLabelText('编辑文档正文')).toBeInTheDocument());
    expect(client.get).toHaveBeenCalledWith(`/content/knowledge-bases/${NB}/documents/s1`);
    // 预览已移除：既没有按钮，也不许再把正文交给渲染组件（用户 2026-11-05 反馈）
    expect(screen.queryByRole('button', { name: '预览' })).not.toBeInTheDocument();
    expect(screen.queryByText('正文渲染')).not.toBeInTheDocument();
    expect(screen.getByDisplayValue(/站点全景/)).toBeInTheDocument();

    const submit = screen.getByRole('button', { name: /只提交这一篇/ });
    expect(submit).toBeDisabled();
    expect(screen.getByText(/内容与云端一致，无需提交/)).toBeInTheDocument();
  });

  it('改动后提交只 PUT 当前这一篇，并带打开时的基线哈希', async () => {
    grant(['kbdoc:read', 'kbdoc:write']);
    vi.mocked(client.put).mockResolvedValue({
      data: { source_id: 's9', previous_source_id: 's1', title: '00-说明.md',
              content_sha256: 'sha-new', message: '已提交这一篇' },
    } as never);
    renderDetail();
    fireEvent.click(await screen.findByLabelText('打开 00-说明.md'));
    await waitFor(() => expect(screen.getByLabelText('编辑文档正文')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('编辑文档正文'), { target: { value: '# 改过了' } });
    await waitFor(() => expect(screen.getByText('有未提交改动')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /只提交这一篇/ }));
    await waitFor(() => expect(client.put).toHaveBeenCalledTimes(1));
    const [url, body] = vi.mocked(client.put).mock.calls[0] as [string, Record<string, string>];
    expect(url).toBe(`/content/knowledge-bases/${NB}/documents/s1`);
    expect(body).toMatchObject({ content: '# 改过了', expected_sha256: 'sha-base' });
    expect(await screen.findByText('已提交这一篇')).toBeInTheDocument();
    // 别的文档不许被顺手动过
    expect(client.post).not.toHaveBeenCalled();
  });

  it('派生文档只读：说明去处、编辑区不可写、提交不给', async () => {
    grant(['kbdoc:read', 'kbdoc:write']);
    route({
      [`/content/knowledge-bases/${NB}/tree`]: tree,
      [`/content/knowledge-bases/${NB}/documents`]: doc({
        derived: true, read_only_reason: '这是本体建模自动上云的派生文档，请到数据中台本体建模修改',
      }),
    });
    renderDetail();
    fireEvent.click(await screen.findByLabelText('打开 00-说明.md'));
    expect(await screen.findByText(/本体建模自动上云的派生文档/)).toBeInTheDocument();
    expect(screen.getByLabelText('编辑文档正文')).toHaveAttribute('readonly');
    expect(screen.getByRole('button', { name: /只提交这一篇/ })).toBeDisabled();
  });

  it('只有查看权时不出现新建与提交，并说明缺哪个权限', async () => {
    grant(['kbdoc:read']);
    renderDetail();
    fireEvent.click(await screen.findByLabelText('打开 00-说明.md'));
    await waitFor(() => expect(screen.getByLabelText('编辑文档正文')).toBeInTheDocument());
    expect(screen.getByText(/只有查看权（kbdoc:read）/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /根下新建文档/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText('编辑文档正文')).toHaveAttribute('readonly');
    expect(screen.getByRole('button', { name: /只提交这一篇/ })).toBeDisabled();
  });

  it('云端目录读取失败必须显式报错，不画一棵空树', async () => {
    grant(['kbdoc:read', 'kbdoc:write']);
    vi.mocked(client.get).mockImplementation(((url: string) =>
      url.includes('/tree')
        ? Promise.reject({ response: { status: 502, data: { detail: '读取云端目录失败：timeout' } } })
        : Promise.resolve({ data: doc() })
    ) as never);
    renderDetail();
    expect(await screen.findByText('读取云端目录失败：timeout')).toBeInTheDocument();
    expect(screen.queryByText('00-说明.md')).not.toBeInTheDocument();
  });
});
