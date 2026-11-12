/**
 * AS-BOT 配置页的「引用知识库」选择器：未接入检索通道的类型必须**灰显且不可点**，
 * 并把服务端原因原文显示出来。
 *
 * 为什么这条值得单测：本批把绑定校验改成 fail-loud（绑了 local/vector_db 直接拒），
 * 如果前端还做成「能选上、保存后检索永远问不到」的开关，就是"配了没用"的空承诺
 * （menu-permission-sync 记录过的同一类三处漂移）。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AsBotManager from '../AsBotManager';
import client from '@/api/client';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

const AS_BOT = {
  id: 3, as_bot_key: 'analyst', name: 'analyst', display_name: '数据分析师',
  description: '', category: 'custom', system_prompt: '', persona: {},
  tools: { groups: ['semantic'], standard: [], mcp: {}, external: {}, functions: { disabled: [] } },
  mcp_server_ids: [], knowledge_base_ids: [], skills: [], models: [],
  chart_enabled: true, execution_layer_id: 0, permission_mode: 'inherit',
  is_active: true, is_builtin: 0,
};

const KBS = [
  // 平台同步语料：可检索、平台可写
  { id: 1, name: '运营知识库', description: '平台同步', kb_type: 'qmind', status: 'active',
    source_config: {},
    retrieval: { readable: true, provider: 'QMind 云端知识库', corpus_origin: 'platform_synced', reason: '' } },
  // 外部只读语料：可检索，平台不写
  { id: 2, name: '产品手册', description: '百炼', kb_type: 'cloud_rag', status: 'active',
    source_config: {},
    retrieval: { readable: true, provider: '百炼知识库（外部只读语料）', corpus_origin: 'external', reason: '' } },
  // 本批未接入检索通道：原因原文来自服务端，前端不自己编措辞
  { id: 3, name: '本地目录库', description: '', kb_type: 'local', status: 'active',
    source_config: {},
    retrieval: { readable: false, provider: 'local', corpus_origin: 'external',
                 reason: '「local」类型的检索通道本批未接入' } },
];

const mockKbList = (rows: any[]) =>
  vi.mocked(client.get).mockImplementation(async (url: string) => {
    if (url === '/admin/as-bots/') return { data: [AS_BOT] } as any;
    if (url === '/knowledge-bases') return { data: rows } as any;
    if (url === '/admin/as-bots/tools/catalog') {
      return { data: { groups: [], standard: [], functions: { items: [] } } } as any;
    }
    return { data: [] } as any;
  });

/** 打开某个 AS-BOT 的编辑弹窗：行尾的编辑按钮只有图标、没有可访问名，按行内最后一个按钮取。 */
async function openEditForm() {
  render(<AsBotManager />);
  const label = await screen.findByText('数据分析师');
  const row = label.closest('div.p-3') as HTMLElement;
  const buttons = row.querySelectorAll('button');
  fireEvent.click(buttons[buttons.length - 1]);
  await screen.findByText('引用知识库');
}

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('AS-BOT 知识库选择器 — 未接入类型灰显', () => {
  it('未接入的库灰显 + 服务端原因原文，点击不进绑定', async () => {
    mockKbList(KBS);
    await openEditForm();

    const blocked = await screen.findByText('本地目录库（不可检索）');
    expect(blocked.getAttribute('title')).toContain('本批未接入');
    expect(blocked.className).toContain('cursor-not-allowed');

    const ok = screen.getByText('运营知识库');
    expect(ok.className).toContain('cursor-pointer');

    fireEvent.click(ok);                       // 正常库可勾选（选中态 = badge default 变体）
    await waitFor(() => expect(ok.className).toContain('bg-primary'));

    fireEvent.click(blocked);                  // 灰显库点了也不生效（不得做成可选但不生效的开关）
    expect(blocked.className).not.toContain('bg-primary');
    expect(ok.className).toContain('bg-primary');
  });

  it('外部只读语料照常可选（可检索 ≠ 可写入，两件事不得混为一谈）', async () => {
    mockKbList(KBS);
    await openEditForm();

    const external = await screen.findByText('产品手册');
    expect(external.className).toContain('cursor-pointer');
    fireEvent.click(external);
    await waitFor(() => expect(external.className).toContain('bg-primary'));
  });

  it('retrieval 字段缺失时不得把正常库锁死（旧载荷兼容）', async () => {
    mockKbList([{ ...KBS[2], retrieval: undefined }]);
    await openEditForm();

    const kb = await screen.findByText('本地目录库');
    expect(kb.textContent).toBe('本地目录库');        // 不带「不可检索」后缀
    expect(kb.className).toContain('cursor-pointer');
    fireEvent.click(kb);
    await waitFor(() => expect(kb.className).toContain('bg-primary'));
  });
});
