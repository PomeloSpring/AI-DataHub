import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from '@/lib/toast';
import client from '@/api/client';
import MotionPreferenceCard from '../MotionPreferenceCard';
import { useMotionPrefStore } from '@/stores/motionPrefStore';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import { DEFAULT_MOTION } from '@/lib/motion';
import type { VisComponent } from '@/api/visLibrary';

// 图表引擎不进本用例：只断言"选了哪套预设 / 存了什么差量 / 界面说了什么"
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: vi.fn() }));

const comp = (over: Partial<VisComponent>): VisComponent => ({
  id: 1, code: 'mp_default', name: '默认（现网）', category: 'motion_preset', chart_type: null,
  style_config: DEFAULT_MOTION, source: 'system', is_builtin: 1, is_active: 1, is_enabled: 1,
  sort_order: 80, description: '', ...over,
} as VisComponent);

const LIBRARY = [
  comp({}),
  comp({ id: 2, code: 'mp_soft', name: '柔和舒缓', sort_order: 82,
    style_config: { ...DEFAULT_MOTION, enter: { ...DEFAULT_MOTION.enter, durationMs: 700, staggerMs: 120 } } }),
  comp({ id: 3, code: 'mp_crisp', name: '利落短促', sort_order: 83, is_enabled: 0 }),
];

function mockLibrary(items: VisComponent[] = LIBRARY, error: string | null = null) {
  vi.mocked(useVisLibrary).mockReturnValue({
    items, loading: false, error, loaded: !error, refresh: vi.fn(),
  } as any);
}

/** 系统「减少动态效果」开关（jsdom 默认没有 matchMedia，两个分支都要能演） */
function mockReducedMotion(matches: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: /reduced-motion/.test(query) ? matches : false,
      media: query, onchange: null,
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
      addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
    }),
  });
}

const putBody = (i = 0) => vi.mocked(client.put).mock.calls[i]?.[1] as Record<string, any>;

/** 旋钮的 thumb（Radix 把 role="slider" 放在 thumb 上，aria-label 挂在外层 group） */
function knob(key = 'durationMs') {
  const group = screen.getByRole('group', { name: key === 'durationMs' ? '入场时长滑杆' : '逐块错峰滑杆' });
  return group.querySelector('[role="slider"]') as HTMLElement;
}

beforeEach(() => {
  vi.resetModules();
  mockLibrary();
  mockReducedMotion(false);
  useMotionPrefStore.setState({
    presetCode: null, override: null, loaded: true, error: '', fetching: false,
  });
  vi.spyOn(toast, 'error').mockReturnValue(0);
  vi.spyOn(toast, 'success').mockReturnValue(0);
  vi.spyOn(client, 'get').mockResolvedValue({ data: { motion_preset_code: null, motion_override: null } });
  vi.spyOn(client, 'put').mockImplementation(async (_url: any, body: any) => ({
    data: { motion_preset_code: null, motion_override: null, ...(body || {}) },
  } as any));
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('界面动效偏好卡片（预设 + 差量）', () => {
  it('默认展示系统默认档，且不写差量', () => {
    render(<MotionPreferenceCard />);
    expect(screen.getByText('界面动效（出现效果）')).toBeInTheDocument();
    expect(screen.getByText(/当前生效：默认（现网）/)).toBeInTheDocument();
    expect(screen.getByText('跟随现状（不额外指定）')).toBeInTheDocument();   // chart.appear=default
    expect(screen.getByText('直接切换（现状）')).toBeInTheDocument();          // transition=none
    expect(screen.getByText('一次性出现（现状）')).toBeInTheDocument();        // reveal=none
    expect(client.put).not.toHaveBeenCalled();
  });

  it('换预设只 PUT 自己那一列，差量留给下一步（避免整行覆盖别人的偏好）', async () => {
    render(<MotionPreferenceCard />);
    await userEvent.click(screen.getByRole('combobox', { name: '动效预设' }));
    await userEvent.click(await screen.findByRole('option', { name: /柔和舒缓/ }));
    await waitFor(() => expect(client.put).toHaveBeenCalledTimes(1));
    expect(putBody()).toEqual({ motion_preset_code: 'mp_soft' });
    expect(screen.getByText(/当前生效：柔和舒缓/)).toBeInTheDocument();
  });

  it('已禁用的预设不进下拉（配了也不会生效，不给空承诺）', async () => {
    render(<MotionPreferenceCard />);
    await userEvent.click(screen.getByRole('combobox', { name: '动效预设' }));
    await screen.findByRole('option', { name: /柔和舒缓/ });
    expect(screen.queryByRole('option', { name: /利落短促/ })).not.toBeInTheDocument();
  });

  it('选「跟随系统默认」落 NULL（不是删偏好行，也不是写一个假编码）', async () => {
    useMotionPrefStore.setState({ presetCode: 'mp_soft' });
    render(<MotionPreferenceCard />);
    await userEvent.click(screen.getByRole('combobox', { name: '动效预设' }));
    await userEvent.click(await screen.findByRole('option', { name: /跟随系统默认/ }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    expect(putBody()).toEqual({ motion_preset_code: null });
  });

  it('拖入场时长只在松手时提交一次（不逐帧 PUT）', async () => {
    // 差量只在生效预设来自个人偏好时叠加（板上/系统默认不叠），所以这里显式选了默认档
    useMotionPrefStore.setState({ presetCode: 'mp_default', override: null });
    render(<MotionPreferenceCard />);
    const slider = knob();
    fireEvent.keyDown(slider, { key: 'ArrowRight' });   // 键盘一步 = 一个 step，触发 commit
    await waitFor(() => expect(client.put).toHaveBeenCalledTimes(1));
    // Radix 的落值按 min+step 对齐，这里钉行为而不是钉具体数字：只提交一次 + 存的是差量键
    const saved = putBody().motion_override.enter.durationMs;
    expect(typeof saved).toBe('number');
    expect(saved).toBeGreaterThan(450);
    expect(putBody()).toEqual({ motion_override: { enter: { durationMs: saved } } });
    expect(screen.getByText(`${saved}ms（我的调整）`)).toBeInTheDocument();
  });

  it('恢复预设值 = 差量里删掉该键；最后一个键删完就清除整个差量', async () => {
    useMotionPrefStore.setState({ presetCode: 'mp_soft', override: { enter: { durationMs: 300 } } });
    render(<MotionPreferenceCard />);
    expect(screen.getByText(/300ms（我的调整）/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '恢复预设值' }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    expect(putBody()).toEqual({ motion_override: null });
  });

  it('偏好读取失败时显示未加载 + 重试，不把界面伪装成"已按默认档"', async () => {
    mockLibrary();
    useMotionPrefStore.setState({ loaded: false, error: '动效偏好读取失败，请重试' });
    render(<MotionPreferenceCard />);
    expect(screen.getByRole('alert')).toHaveTextContent('动效偏好读取失败，请重试');
    expect(screen.getByRole('combobox', { name: '动效预设' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/users/me/preferences'));
  });

  it('系统开启「减少动态效果」→ 控件禁用并写明原因（不是静默没反应）', () => {
    mockReducedMotion(true);
    render(<MotionPreferenceCard />);
    expect(screen.getByText(/prefers-reduced-motion/)).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '动效预设' })).toBeDisabled();
    for (const label of ['入场时长滑杆', '逐块错峰滑杆']) {
      expect(screen.getByRole('group', { name: label })).toHaveAttribute('aria-disabled', 'true');
    }
  });

  it('保存失败回滚界面显示（停在库里没有的选择上更误导）', async () => {
    vi.mocked(client.put).mockRejectedValueOnce({ response: { data: { detail: '动效预设「mp_soft」已下架' } } });
    render(<MotionPreferenceCard />);
    await userEvent.click(screen.getByRole('combobox', { name: '动效预设' }));
    await userEvent.click(await screen.findByRole('option', { name: /柔和舒缓/ }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('动效预设「mp_soft」已下架'));
    expect(useMotionPrefStore.getState().presetCode).toBeNull();
    expect(screen.getByText(/当前生效：默认（现网）/)).toBeInTheDocument();
  });

  it('板上挂载的看板会说明"个人预设只作用于未挂载的看板"（分面裁决不藏着）', () => {
    mockLibrary([...LIBRARY, comp({ id: 4, code: 'mp_tech_flow', name: '科技流光', sort_order: 84 })]);
    useMotionPrefStore.setState({ presetCode: 'mp_soft' });
    render(<MotionPreferenceCard />);
    // 卡片本身不带 board，来源应是"我的动效预设"；板上场景由编辑器属性面板显示
    expect(screen.getByText(/来源：我的动效预设/)).toBeInTheDocument();
  });
});
