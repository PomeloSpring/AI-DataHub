import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SyncBadges } from '../ModelAssetTabs';
import client from '@/api/client';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const mockGet = vi.mocked(client.get);

/**
 * 同步状态徽章的「说人话」契约。
 *
 * 背景：用户看到「图谱 滞后 1/4」不知道是数据错还是没重建，也看不出
 * 「同步知识库」到底推到了哪个库。徽章的文案就是用户唯一的诊断入口，
 * 因此把三件事钉成用例：滞后方向 + 处置指引、目标库名、源本体按设计不同步。
 */
async function renderBadges(payload: unknown, kind?: string) {
  mockGet.mockResolvedValue({ data: payload } as never);
  render(<SyncBadges modelId={1} kind={kind} />);
  // 轮询首帧是异步 setState，等它落地
  expect(await screen.findByText(/图谱/)).toBeTruthy();
}

afterEach(() => { cleanup(); mockGet.mockReset(); });

it('滞后写清方向与处置：图中几个 / 模型几个 + 需重建', async () => {
  await renderBadges({
    graph: { state: 'lagging', classes: 1, objects: 4, graph_datasource_id: 1791117518309 },
    kb: { state: 'no_kb', targets: 0, target_names: [] },
  }, 'source');
  expect(screen.getByText(/滞后（图中 1\/模型 4）/)).toBeTruthy();
  expect(screen.getByText('· 需重建')).toBeTruthy();
});

it('知识库徽章直接带目标库名，不露裸 id', async () => {
  await renderBadges({
    graph: { state: 'synced', classes: 17, objects: 17, graph_datasource_id: 0 },
    kb: { state: 'success', targets: 1, target_names: ['AI-DataHub 系统知识库'], synced_at: '2026-10-12 14:53:43' },
  }, 'business');
  expect(screen.getByText(/知识库 已同步 → AI-DataHub 系统知识库/)).toBeTruthy();
});

it('多个目标库显示首个 + 计数', async () => {
  await renderBadges({
    graph: { state: 'synced', classes: 1, objects: 1, graph_datasource_id: 0 },
    kb: { state: 'success', targets: 3, target_names: ['库A', '库B', '库C'] },
  }, 'business');
  expect(screen.getByText(/库A 等 3 个/)).toBeTruthy();
});

it('源本体不入知识库按设计呈现，不当成"未开启同步"告警', async () => {
  await renderBadges({
    graph: { state: 'synced', classes: 4, objects: 4, graph_datasource_id: 9 },
    kb: { state: 'no_kb', targets: 0, target_names: [] },
  }, 'source');
  expect(screen.getByText(/不适用（源本体）/)).toBeTruthy();
  expect(screen.queryByText(/未开启同步/)).toBeNull();
});

it('业务本体没有目标库时仍如实说未开启（不借源本体文案遮）', async () => {
  await renderBadges({
    graph: { state: 'synced', classes: 1, objects: 1, graph_datasource_id: 0 },
    kb: { state: 'no_kb', targets: 0, target_names: [] },
  }, 'business');
  expect(screen.getByText(/未开启同步/)).toBeTruthy();
});
