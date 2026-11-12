import { create } from 'zustand';
import client from '../api/client';
import type { MotionOverride } from '@/lib/motion';

/**
 * 用户级界面动效偏好（事实源 = 服务端 `adh_user_preferences`）。
 *
 * 为什么落库而不是像主题那样 localStorage persist：动效要跨设备一致（同一个人换机器
 * 不该重新被「演示播报」闪一次），而主题是一次性观感选择、随查看者渲染即可。
 * 因此这里**不做本地双写**：只缓存服务端返回值，保存一律等接口回读后再落 state ——
 * 界面显示与库里不一致会直接把用户引到「配了没生效」的误判上（Profile 的失败回滚同口径）。
 *
 * NULL 语义：presetCode=null = 跟随系统默认（mp_default），与「未加载」是两回事，
 * 靠 `loaded`/`error` 区分；读失败保持 loaded=false 让界面显示未加载 + 重试，
 * 不回落成某个值 —— 那会把接口故障伪装成「用户选的默认档」（no-silent-degradation）。
 */
interface MotionPrefState {
  presetCode: string | null;
  override: MotionOverride | null;
  loaded: boolean;
  error: string;
  fetching: boolean;
  fetch: (force?: boolean) => Promise<void>;
  /** code=null 表示「跟随系统默认」（服务端把列写回 NULL，不是删行） */
  savePreset: (code: string | null) => Promise<void>;
  /** override=null 表示清除差量，回到纯预设 */
  saveOverride: (override: MotionOverride | null) => Promise<void>;
}

interface PreferencesResponse {
  motion_preset_code?: string | null;
  motion_override?: MotionOverride | null;
}

export const useMotionPrefStore = create<MotionPrefState>((setState, getState) => ({
  presetCode: null,
  override: null,
  loaded: false,
  error: '',
  fetching: false,

  fetch: async (force = false) => {
    const state = getState();
    // 失败后不自动重试：否则每个渲染面挂载都会重发一个注定失败的请求（请求风暴）。
    // 读不到时 presetCode 保持 null → 解析回默认档（= 现网观感，零变化），
    // 而「重试」入口在偏好卡片上显式给（force=true）。
    if (state.fetching || (state.loaded && !force) || (state.error && !force)) return;
    setState({ fetching: true });
    try {
      const { data }: { data: PreferencesResponse } = await client.get('/users/me/preferences');
      setState({
        presetCode: data.motion_preset_code ?? null,
        override: data.motion_override ?? null,
        loaded: true, error: '', fetching: false,
      });
    } catch (err) {
      setState({ loaded: false, error: '动效偏好读取失败，请重试', fetching: false });
      console.warn('[motionPref] 读取个人动效偏好失败', err);
    }
  },

  savePreset: async (code) => {
    const prev = { presetCode: getState().presetCode, override: getState().override };
    try {
      const { data }: { data: PreferencesResponse } = await client.put('/users/me/preferences', { motion_preset_code: code });
      setState({ presetCode: data.motion_preset_code ?? null, override: data.motion_override ?? prev.override, loaded: true, error: '' });
    } catch (err) {
      setState(prev);   // 回滚 UI，不让界面停在库里没有的选择上
      throw err;
    }
  },

  saveOverride: async (override) => {
    const prev = { presetCode: getState().presetCode, override: getState().override };
    try {
      const { data }: { data: PreferencesResponse } = await client.put('/users/me/preferences', { motion_override: override });
      setState({ presetCode: data.motion_preset_code ?? prev.presetCode, override: data.motion_override ?? null, loaded: true, error: '' });
    } catch (err) {
      setState(prev);
      throw err;
    }
  },
}));
