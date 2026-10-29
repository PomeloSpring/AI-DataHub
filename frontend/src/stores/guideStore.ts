import { create } from 'zustand';
import {
  releaseNotesApi,
  guideKey, noteKey,
  GUIDE_ALL_KEY, GUIDE_WELCOME_KEY,
  type GuideStatus, type ReleaseNote, type GuideStateItem,
} from '@/api/releaseNotes';
import { toast } from '@/lib/toast';

/**
 * 新手引导 + 版本公告 store。
 *
 * 事实源在服务端(adh_user_guide_state / adh_release_notes)，本 store 只做内存镜像:
 * 不用 localStorage 兜底 —— 换浏览器/清缓存重看引导、跨设备状态不一致都是缺陷。
 * 写状态**只在请求成功后**才落到本地镜像: 失败时保留"未处理"事实，弹窗仍会提示，
 * 并把错误显式暴露给用户（见 no-silent-degradation）。
 */

interface GuideState {
  notes: ReleaseNote[];
  states: Record<string, GuideStateItem>;
  loading: boolean;
  loaded: boolean;
  /** 最近一次失败原因(公告按钮据此渲染告警态 + 重试) */
  error: string;
  /** 正在播放的分步引导 id(GUIDES 键); null = 未在播放 */
  activeGuide: string | null;
  /** 欢迎总览弹窗是否打开 */
  welcomeOpen: boolean;

  load: (force?: boolean) => Promise<void>;
  putState: (keys: string[], status: GuideStatus) => Promise<boolean>;
  markAllNotesRead: () => Promise<boolean>;
  /** 显式启动引导(用户手动点击): 无视静音标记 */
  startGuide: (id: string) => void;
  stopGuide: () => void;
  requestWelcome: () => void;
  closeWelcome: () => void;
}

/** 未读公告(顶栏红点口径) */
export function selectUnreadNotes(s: { notes: ReleaseNote[]; states: Record<string, GuideStateItem> }) {
  return s.notes.filter(n => !s.states[noteKey(n.version)]);
}

/**
 * 自动弹出判定(唯一口径):
 *   - 命中全局静音(guide:all) → 永不自动弹，只剩手动入口;
 *   - 没有 guide:welcome → 首次登录，弹欢迎总览;
 *   - 有未读重大版本 → 升级，弹欢迎总览(顶部显示亮点)。
 */
export function selectAutoPrompt(
  s: { notes: ReleaseNote[]; states: Record<string, GuideStateItem> },
): { reason: 'first' | 'upgrade'; notes: ReleaseNote[] } | null {
  if (s.states[GUIDE_ALL_KEY]) return null;
  const unreadMajor = selectUnreadNotes(s).filter(n => n.is_major);
  if (!s.states[GUIDE_WELCOME_KEY]) return { reason: 'first', notes: unreadMajor };
  if (unreadMajor.length) return { reason: 'upgrade', notes: unreadMajor };
  return null;
}

export const useGuideStore = create<GuideState>((set, get) => ({
  notes: [],
  states: {},
  loading: false,
  loaded: false,
  error: '',
  activeGuide: null,
  welcomeOpen: false,

  load: async (force = false) => {
    const { loading, loaded } = get();
    if (loading || (loaded && !force)) return;
    set({ loading: true });
    try {
      const [notesRes, stateRes] = await Promise.all([
        releaseNotesApi.list(),
        releaseNotesApi.getState(),
      ]);
      set({
        notes: notesRes.data.items || [],
        states: stateRes.data.states || {},
        loading: false, loaded: true, error: '',
      });
    } catch (e: any) {
      // 显式暴露: 顶栏公告按钮转告警态 + 面板内可重试，不静默当作"没有公告/已看过"
      const detail = e?.response?.data?.detail || e?.message || '未知错误';
      set({ loading: false, loaded: true, error: String(detail) });
    }
  },

  putState: async (keys, status) => {
    if (!keys.length) return true;
    try {
      await releaseNotesApi.putState(keys, status);
      set(s => {
        const next = { ...s.states };
        keys.forEach(k => { next[k] = { status, updated_at: new Date().toISOString() }; });
        return { states: next };
      });
      return true;
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || '未知错误';
      set({ error: String(detail) });
      toast.error(`操作未保存，下次仍会提示（${detail}）`, { duration: 5000 });
      return false;
    }
  },

  markAllNotesRead: async () => {
    const keys = get().notes.map(n => noteKey(n.version));
    if (!keys.length) return true;
    return get().putState(keys, 'read');
  },

  startGuide: (id) => {
    if (!id || id === 'welcome') { set({ welcomeOpen: true }); return; }
    set({ activeGuide: id });
  },
  stopGuide: () => set({ activeGuide: null }),
  requestWelcome: () => set({ welcomeOpen: true }),
  closeWelcome: () => set({ welcomeOpen: false }),
}));

/** 供非响应式上下文读取静音标记(与 store 同源，不另造判断) */
export const isAutoGuideMuted = () => !!useGuideStore.getState().states[GUIDE_ALL_KEY];

export { GUIDE_ALL_KEY, GUIDE_WELCOME_KEY, guideKey, noteKey };
