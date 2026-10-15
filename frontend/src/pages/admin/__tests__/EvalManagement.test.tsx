/**
 * 评测中心页面回归（P2 重构的门禁呈现口径）。
 *
 * 这些断言对应三类曾经真实错的呈现，不是风格检查：
 *  1. 只按"相对未回退"报绿 —— 35.7% 的层当时显示"可以发布"；
 *  2. 「无运行」被当作通过（precheck 旧实现里 has_run=false 时 baseline_ok=true）；
 *  3. L1 跑的是 mock 目录，但页面把它的 100% 当成平台能力汇报。
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import EvalManagement from '../EvalManagement';
import client from '@/api/client';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('react-router-dom');
  return { ...actual, Link: ({ children }: any) => <span>{children}</span> };
});

const SUITES = [
  { suite: 'compile', label: 'L1 编译规则（mock 目录）', expected_keys: ['ok', 'unbound'],
    scope_note: '只走离线确定性编译，语义目录被打桩：测的是规则回归，不反映真实本体' },
  { suite: 'semantic', label: 'L2 真实语义（intent→治理改写）', expected_keys: ['allowed'],
    scope_note: '不打桩：真实 binding + 真实敏感/RLS 基线' },
  { suite: 'retrieval', label: 'L3 真实检索', expected_keys: ['hit'], scope_note: '真跑检索策略链' },
  { suite: 'llm', label: 'L4 端到端行为', expected_keys: ['no_leak'], scope_note: 'trace 是自录回放' },
];
const CAPS = [
  { key: 'resolution', label: '口径解析' },
  { key: 'governance', label: '权限治理' },
  { key: 'honesty', label: '诚实性' },
];

const PRECHECK = {
  release_ok: false,
  baseline_ok: true,
  verdict: '发布前检查不通过：有层低于绝对下限、用例数不足或未配门禁',
  suites: {
    // 相对未回退为真，但正确率低于下限 —— 旧口径会在这里显示"可以发布"
    retrieval: {
      has_run: true, run_id: 54, baseline_ok: true, floor_ok: false, release_ok: false,
      gate_verdict: 'below_floor', accuracy: 0.8333, passed: 5, total: 6,
      run_accuracy_snapshot: 0.3571, run_total_snapshot: 14,
      retired_count: 8, known_defect_count: 1,
      breaches: [{
        suite: 'retrieval', capability: '', capability_label: '整体',
        accuracy: 0.8333, passed: 5, total: 6, floor_accuracy: 0.9, min_cases: 10,
        reason: '活跃用例 6 条，低于覆盖度下界 10 条（删/退役难用例会在此被拦住）',
      }],
    },
    compile: {
      has_run: true, run_id: 53, baseline_ok: true, floor_ok: true, release_ok: true,
      gate_verdict: 'ok', accuracy: 1, passed: 72, total: 72,
      run_accuracy_snapshot: 1, run_total_snapshot: 72, retired_count: 0, known_defect_count: 0,
      breaches: [],
    },
    semantic: {
      has_run: true, run_id: 60, baseline_ok: true, floor_ok: true, release_ok: true,
      gate_verdict: 'ok', accuracy: 1, passed: 24, total: 24,
      run_accuracy_snapshot: 1, run_total_snapshot: 24, retired_count: 0, known_defect_count: 0,
      breaches: [],
    },
    // llm 无运行：旧实现这里给 baseline_ok=true，整页仍报绿
    llm: {
      has_run: false, baseline_ok: false, floor_ok: false, release_ok: false,
      gate_verdict: 'no_run', retired_count: 0, known_defect_count: 0,
    },
  },
};

const COVERAGE = {
  cells: [
    { suite: 'compile', capability: 'resolution', total: 18, scored: 18, retired: 0,
      known_defect: 0, accuracy: 1, run_id: 53, capability_label: '口径解析', suite_label: 'L1' },
    // 退役/已知缺陷的条数必须从覆盖矩阵带上来（两者语义不可混：
    // 一个仍拖累下限、一个不计分母但条数常驻）
    { suite: 'retrieval', capability: 'routing', total: 5, scored: 1, retired: 8,
      known_defect: 1, accuracy: 1, run_id: 54, capability_label: '跨源路由', suite_label: 'L3' },
    { suite: 'retrieval', capability: 'governance', total: 0, scored: 0, retired: 0,
      known_defect: 0, accuracy: null, run_id: null, capability_label: '权限治理', suite_label: 'L3' },
  ],
};

function mockOk() {
  vi.mocked(client.get).mockImplementation(async (url: string) => {
    if (url === '/eval/suites') return { data: { suites: SUITES, capabilities: CAPS, case_statuses: ['active', 'known_defect', 'retired'] } } as any;
    if (url === '/eval/precheck') return { data: PRECHECK } as any;
    if (url === '/eval/coverage') return { data: COVERAGE } as any;
    if (url === '/eval/gate') return { data: { items: [
      { id: 3, suite: 'retrieval', capability: '', floor_accuracy: 0.9, min_cases: 10, enabled: 1, updated_by: '' },
    ], total: 1 } } as any;
    if (url === '/eval/runs') return { data: { items: [], total: 0 } } as any;
    if (url === '/eval/cases') return { data: { items: [], total: 0 } } as any;
    return { data: [] } as any;
  });
}

beforeEach(() => { vi.clearAllMocks(); mockOk(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('发布前检查 — 双口径不得被单一口径冒充', () => {
  it('低于绝对下限时显式红字，且不出现"可以发布"', async () => {
    render(<EvalManagement />);
    await screen.findByText(/发布前检查（双口径）/);

    // 整页结论：红字 + 说明是绝对下限不过，而不是"未低于基线"
    const verdict = await screen.findByText(PRECHECK.verdict);
    expect(verdict).toHaveClass('text-destructive');
    expect(screen.queryByText(/可以发布/)).not.toBeInTheDocument();
    expect(screen.queryByText(/各评测集均未低于基线/)).not.toBeInTheDocument();

    // L3 那一行必须显示"低于下限"，且把 breach 原因摊开（不能只给个颜色）
    expect(screen.getAllByText('低于下限').length).toBeGreaterThan(0);
    expect(await screen.findByText(/低于覆盖度下界 10 条/)).toBeInTheDocument();
  });

  it('无运行按不通过呈现，不把未知当通过', async () => {
    render(<EvalManagement />);
    await screen.findByText(/发布前检查（双口径）/);
    expect(screen.getByText('无运行')).toBeInTheDocument();
    expect(screen.getByText('未知≠通过')).toBeInTheDocument();
  });

  it('判定分母与当时快照并列展示，两个数字必须互相解释得了', async () => {
    render(<EvalManagement />);
    // 门禁按退役后的 6 条分母判；当时那条运行是 14 条全量口径
    const snap = await screen.findByText(/（当时 14 条 35\.7%）/);
    expect(snap).toBeInTheDocument();
    expect(snap.getAttribute('title')).toContain('退役用例后判定分母变小');
  });

  it('L1 的 mock 边界必须写在页面上，避免规则回归被当成平台能力', async () => {
    render(<EvalManagement />);
    const note = await screen.findByText(/不反映真实本体/);
    expect(note).toBeInTheDocument();
  });
});

describe('覆盖矩阵 — 缺口要可见可点', () => {
  it('无活跃用例的格标为缺口并可点击进入新增', async () => {
    render(<EvalManagement />);
    const gaps = await screen.findAllByText('缺口');
    expect(gaps.length).toBeGreaterThan(0);
    expect(screen.getByText(/格无活跃用例/)).toBeInTheDocument();

    fireEvent.click(gaps[0]);
    // 跳转后应出现新增用例表单，且能力契约组已带上
    expect(await screen.findByText('新增评测用例')).toBeInTheDocument();
  });

  it('已知缺陷与退役条数分开呈现（两者语义不可混）', async () => {
    render(<EvalManagement />);
    await screen.findByText(/已知缺陷 1 条（计入分母）/);
    await screen.findByText(/已退役 8 条（不计分母/);
  });
});

describe('用例表单 — 契约必填、状态变更必须带原因', () => {
  it('未选能力契约组时拒绝保存并说明原因', async () => {
    const { toast } = await import('sonner');
    const user = userEvent.setup();
    render(<EvalManagement />);
    // 从概览矩阵点「缺口」会**预填**契约组（那是该入口的设计意图），
    // 要测"契约缺失就拦住"得走不预填的普通新增入口。
    await user.click(await screen.findByRole('tab', { name: /用例/ }));
    fireEvent.click(await screen.findByRole('button', { name: /新增用例/ }));
    await screen.findByText('新增评测用例');

    fireEvent.change(screen.getByLabelText(/用例标识/), { target: { value: 't_new_case' } });
    fireEvent.change(screen.getByLabelText(/问题（golden/), { target: { value: '新问句' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    // 前端先拦住：不该让人提交一次才知道契约组是必填
    await vi.waitFor(() => expect(vi.mocked(toast.error)).toHaveBeenCalled());
    expect(vi.mocked(toast.error).mock.calls.flat().join(' ')).toContain('能力契约');
    // 且未发出创建请求（没有绕过校验静默提交）
    expect(vi.mocked(client.post)).not.toHaveBeenCalledWith('/eval/cases', expect.anything());
  });

  it('矩阵点缺口会带入层与契约，不让人从头选起', async () => {
    render(<EvalManagement />);
    fireEvent.click((await screen.findAllByText('缺口'))[0]);
    await screen.findByText('新增评测用例');
    // 该格的能力契约已预选（页面文案会告知可用键 / 模板）
    expect(document.getElementById('f-capability')).toBeInTheDocument();
    expect(screen.getByText(/已按层\+契约预填模板|该组合暂无模板/)).toBeInTheDocument();
  });

  it('期望值不代生成：表单里明写需人工判定', async () => {
    render(<EvalManagement />);
    fireEvent.click((await screen.findAllByText('缺口'))[0]);
    await screen.findByText('新增评测用例');
    const hint = document.getElementById('f-no-self-proof');
    expect(hint?.textContent).toContain('期望值需人工判定');
    expect(hint?.textContent).toContain('不能由工具自证');
  });
});

describe('门禁阈值页 — 阈值可见可调', () => {
  it('展示绝对下限与用例数下界两条口径，并标注未配策略等于门禁失效', async () => {
    const user = userEvent.setup();
    render(<EvalManagement />);
    // Radix Tabs 需要真实 pointer 事件，fireEvent.click 不切面板
    await user.click(await screen.findByRole('tab', { name: /门禁/ }));
    await screen.findByText(/绝对下限策略/);
    // 两件事都得能设：正确率下限 + 用例数下界（后者拦"删难用例刷通过率"）
    expect(await screen.findByText('用例数下界')).toBeInTheDocument();
    expect(screen.getByText(/不会当作通过/)).toBeInTheDocument();
    expect(screen.getByDisplayValue('0.9')).toBeInTheDocument();
  });
});
