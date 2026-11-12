import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import DecisionWidget from '../DecisionWidget';
import client from '@/api/client';
import type { DecisionOutcomeView } from '@/api/decisions';

/**
 * 看板决策控件（计划 §四出口 4）。
 *
 * 钉四件事：结论只来自服务端；筛选只送「原值 + 绑定清单」（不自己拼 filters）；
 * 无权限/未选树都要说清（不能空着一块让人以为"没有风险"）；参数变了要重算。
 */

const chrome = {
  background: '#fff', color: '#111', borderColor: '#eee', borderWidth: 1,
  borderStyle: 'solid', borderRadius: 6, padding: '8px', fontSize: 13,
};

const hitOutcome: DecisionOutcomeView = {
  tree_key: 'accuracy-risk', tree_name: '设备精度风险判定', tree_version: 3,
  verdict: 'hit', hit_path: ['精度风险判定', '校准超差'],
  advice: [{ advice_key: 'adv_recalibrate', action: '安排一次现场校准', severity: 'high' }],
  evidence: [{ metric: '扫描精度偏差均值', comparator: '>=', threshold: 0.05, measured: 0.08, unit: 'mm' }],
  scope_notes: ['参数「统计区间」没有取值，本次按决策树自身口径评估（未套用该筛选）'],
};

const skippedOutcome: DecisionOutcomeView = {
  tree_key: 'accuracy-risk', tree_name: '设备精度风险判定', tree_version: 3,
  verdict: 'skipped', reason: '决策树「设备精度风险判定」当前状态是 draft，只有已发布的树参与评估',
  hit_path: [], advice: [], evidence: [],
};

let postCalls: any[] = [];

beforeEach(() => {
  postCalls = [];
  vi.spyOn(client, 'get').mockResolvedValue({ data: { items: [] } } as any);
  vi.spyOn(client, 'post').mockImplementation(async (url: string, body: any) => {
    postCalls.push({ url, body });
    return { data: hitOutcome } as any;
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

const renderWidget = (props: Partial<React.ComponentProps<typeof DecisionWidget>> = {}) => render(
  <MemoryRouter>
    <DecisionWidget
      chrome={chrome}
      treeKey="accuracy-risk"
      paramBindings={[
        { key: 'd', slot: 'time_window', label: '统计区间' },
        { key: 'region', slot: 'dimension', dimension: '区域' },
      ]}
      params={{ d: '30d', region: '华东', unrelated: '别图参数' }}
      workspaceId={7}
      {...props}
    />
  </MemoryRouter>,
);

/** 同一个控件实例（用 rerender 换筛选值）：看板上的真实形态就是同一张卡跟着参数变 */
const renderLive = (params: Record<string, any>) => render(
  <MemoryRouter>
    <DecisionWidget
      chrome={chrome} treeKey="accuracy-risk"
      paramBindings={[{ key: 'd', slot: 'time_window', label: '统计区间' }]}
      params={params} workspaceId={7} />
  </MemoryRouter>,
);

describe('看板决策控件', () => {
  it('把看板参数原值与绑定清单交给服务端，且只送绑定里出现的键', async () => {
    renderWidget();
    const call = await screen.findByText('安排一次现场校准');
    expect(call).toBeTruthy();
    const post = postCalls.find(p => String(p.url).includes('/decisions/query/trees/accuracy-risk/evaluate'));
    expect(post).toBeTruthy();
    expect(post.body.params).toEqual({ d: '30d', region: '华东' });
    expect(post.body.param_bindings.map((b: any) => b.slot)).toEqual(['time_window', 'dimension']);
    expect(post.body.workspace_id).toBe(7);
    // 前端不拼 filters：翻译只在服务端 param_plan 发生
    expect(JSON.stringify(post.body)).not.toContain('filters');
  });

  it('结论按服务端给的渲染：命中路径、依据表与口径说明都在卡上', async () => {
    renderWidget();
    await screen.findByText('安排一次现场校准');
    expect(screen.getByLabelText('命中路径')).toBeTruthy();
    expect(screen.getByLabelText('判定依据')).toBeTruthy();
    expect(screen.getByText('设备精度风险判定')).toBeTruthy();
    // hit 也要显式说明口径没套用上（否则会把全量结论当筛选后的结论读）
    expect(screen.getByText(/没有取值/)).toBeTruthy();
  });

  it('看板筛选变化会重新求值（控件不是贴死的静态卡）', async () => {
    const { rerender } = renderLive({ d: '30d' });
    await screen.findByText('安排一次现场校准');
    const before = postCalls.length;

    rerender(
      <MemoryRouter>
        <DecisionWidget
          chrome={chrome} treeKey="accuracy-risk"
          paramBindings={[{ key: 'd', slot: 'time_window', label: '统计区间' }]}
          params={{ d: '2026-11-01,2026-11-06' }} workspaceId={7} />
      </MemoryRouter>,
    );
    await waitFor(() => expect(postCalls.length).toBeGreaterThan(before), { timeout: 2000 });
    expect(postCalls[postCalls.length - 1].body.params).toEqual({ d: '2026-11-01,2026-11-06' });
  });

  it('403 说清是权限问题，不是"没有建议"', async () => {
    vi.spyOn(client, 'post').mockImplementation(async () => {
      throw { response: { status: 403, data: { detail: '无权访问该资源' } } };
    });
    renderWidget();
    expect(await screen.findByRole('alert')).toHaveTextContent('当前角色不可见决策建议');
  });

  it('未选择决策树时不取数，并说明去哪里配', async () => {
    renderWidget({ treeKey: '' });
    expect(await screen.findByText(/未选择决策树/)).toBeTruthy();
    expect(postCalls.filter(p => String(p.url).includes('evaluate'))).toHaveLength(0);
  });

  it('服务端回 skipped 时如实显示原因（不把"没评估"画成"没风险"）', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: skippedOutcome } as any);
    renderWidget();
    expect(await screen.findByText(/只有已发布/)).toBeTruthy();
  });

  it('样式来自传入的 chrome，不在控件里硬编码色值', async () => {
    const { container } = renderWidget();
    await screen.findByText('安排一次现场校准');
    const box = container.firstElementChild as HTMLElement;
    expect(box.style.background).toBe('rgb(255, 255, 255)');
    expect(box.style.borderRadius).toBe('6px');
    expect(box.style.fontSize).toBe('13px');
  });

  it('请求失败也给出可读原因，不静默留空白', async () => {
    vi.spyOn(client, 'post').mockRejectedValue({ message: '网络中断' });
    renderWidget();
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('评估未完成');
    expect(alert.textContent).toContain('网络中断');
  });
});
