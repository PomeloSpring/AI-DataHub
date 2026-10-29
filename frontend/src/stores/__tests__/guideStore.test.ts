import { beforeEach, describe, expect, it, vi } from 'vitest';
import client from '@/api/client';
import {
  useGuideStore, selectAutoPrompt, selectUnreadNotes, isAutoGuideMuted,
} from '@/stores/guideStore';
import {
  GUIDE_ALL_KEY, GUIDE_WELCOME_KEY, guideKey, noteKey,
  type GuideStateItem, type ReleaseNote,
} from '@/api/releaseNotes';
import { toast } from '@/lib/toast';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

/**
 * guideStore 的判定口径回归。
 *
 * 重点盯两件事: ① 状态以服务端为事实源 —— 写失败绝不本地"当作已跳过";
 * ② 自动弹出的判定只有一处(selectAutoPrompt), 静音标记必须压住自动弹但压不住手动入口。
 */

const NOTES: ReleaseNote[] = [
  { id: 1, version: '1.1.0', title: '新手引导与版本公告', summary: '', sections: [], guide_key: 'welcome', is_major: 1 },
  { id: 2, version: '1.0.0', title: '首个版本', summary: '', sections: [], guide_key: '', is_major: 0 },
];

function mockLoad(notes: ReleaseNote[] = NOTES, states: Record<string, GuideStateItem> = {}) {
  vi.mocked(client.get).mockImplementation(async (url: string) => (
    url === '/release-notes'
      ? { data: { items: notes, latest_version: notes[0]?.version || '' } }
      : { data: { states } }
  ) as any);
}

const seed = (states: Record<string, GuideStateItem> = {}, notes: ReleaseNote[] = NOTES) =>
  useGuideStore.setState({
    notes, states, loading: false, loaded: true, error: '', activeGuide: null, welcomeOpen: false,
  });

beforeEach(() => {
  // resetAllMocks(而非 clearAllMocks): 还要抖掉上个用例留下的 mockImplementation/mockRejectedValue,
  // 否则 client.put 的失败桩会泄漏到后面的用例, 测出来的是桩而不是代码
  vi.resetAllMocks();
  seed();
});

describe('load', () => {
  it('把公告与本人状态一起落到内存镜像', async () => {
    mockLoad(NOTES, { [GUIDE_WELCOME_KEY]: { status: 'skipped' } });
    await useGuideStore.getState().load(true);
    const s = useGuideStore.getState();
    expect(s.notes.map(n => n.version)).toEqual(['1.1.0', '1.0.0']);
    expect(s.states[GUIDE_WELCOME_KEY]?.status).toBe('skipped');
    expect(s.error).toBe('');
    expect(s.loaded).toBe(true);
  });

  it('接口失败置 error(供顶栏按钮转告警态), 不静默当成"没有公告"', async () => {
    vi.mocked(client.get).mockRejectedValue({ response: { data: { detail: '数据库连接失败' } } });
    await useGuideStore.getState().load(true);
    const s = useGuideStore.getState();
    expect(s.error).toBe('数据库连接失败');
    // 保留上一次成功镜像(不抹空): 面板仍给内容 + 顶栏转告警态, 两件事都看得见
    expect(s.notes).toHaveLength(2);
  });
});

describe('putState', () => {
  it('请求成功后才更新本地镜像', async () => {
    vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
    const ok = await useGuideStore.getState().putState([guideKey('home')], 'completed');
    expect(ok).toBe(true);
    expect(vi.mocked(client.put)).toHaveBeenCalledWith('/release-notes/state', {
      keys: ['guide:home'], status: 'completed',
    });
    expect(useGuideStore.getState().states[guideKey('home')]?.status).toBe('completed');
  });

  it('写失败: 保留未处理事实 + 显式报错, 不假装已跳过', async () => {
    vi.mocked(client.put).mockRejectedValue({ response: { data: { detail: '登录状态已失效' } } });
    const ok = await useGuideStore.getState().putState([GUIDE_ALL_KEY], 'skipped');
    expect(ok).toBe(false);
    expect(useGuideStore.getState().states[GUIDE_ALL_KEY]).toBeUndefined();
    expect(useGuideStore.getState().error).toBe('登录状态已失效');
    expect(vi.mocked(toast.error)).toHaveBeenCalledWith(
      expect.stringContaining('登录状态已失效'), expect.anything(),
    );
  });
});

describe('markAllNotesRead', () => {
  it('一次请求覆盖全部已发布版本(红点口径)', async () => {
    vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
    await useGuideStore.getState().markAllNotesRead();
    expect(vi.mocked(client.put)).toHaveBeenCalledWith('/release-notes/state', {
      keys: [noteKey('1.1.0'), noteKey('1.0.0')], status: 'read',
    });
    const s = useGuideStore.getState();
    expect(selectUnreadNotes(s)).toHaveLength(0);
  });

  it('没有公告时不发请求', async () => {
    seed({}, []);
    await useGuideStore.getState().markAllNotesRead();
    expect(vi.mocked(client.put)).not.toHaveBeenCalled();
  });
});

describe('startGuide(手动入口)', () => {
  it('全局静音后手动启动仍然生效(只压制自动弹)', () => {
    seed({ [GUIDE_ALL_KEY]: { status: 'skipped' } });
    useGuideStore.getState().startGuide('workspace');
    expect(useGuideStore.getState().activeGuide).toBe('workspace');
    expect(isAutoGuideMuted()).toBe(true);
  });

  it("welcome 走总览弹窗而不是分步引擎", () => {
    useGuideStore.getState().startGuide('welcome');
    expect(useGuideStore.getState().welcomeOpen).toBe(true);
    expect(useGuideStore.getState().activeGuide).toBeNull();
  });
});

describe('selectAutoPrompt(自动弹出唯一口径)', () => {
  it('命中全局静音 → 任何情况都不弹', () => {
    expect(selectAutoPrompt({ notes: NOTES, states: { [GUIDE_ALL_KEY]: { status: 'skipped' } } })).toBeNull();
  });

  it('没有 guide:welcome → 首次登录弹总览(未读重大版本作为亮点)', () => {
    const prompt = selectAutoPrompt({ notes: NOTES, states: {} });
    expect(prompt?.reason).toBe('first');
    expect(prompt?.notes.map(n => n.version)).toEqual(['1.1.0']);
  });

  it('已看过欢迎 + 有未读重大版本 → 按升级弹', () => {
    const prompt = selectAutoPrompt({
      notes: NOTES,
      states: { [GUIDE_WELCOME_KEY]: { status: 'skipped' }, [noteKey('1.0.0')]: { status: 'read' } },
    });
    expect(prompt?.reason).toBe('upgrade');
    expect(prompt?.notes.map(n => n.version)).toEqual(['1.1.0']);
  });

  it('欢迎已处理且重大版本已读 → 不再打扰(普通公告只留红点)', () => {
    const states: Record<string, GuideStateItem> = {
      [GUIDE_WELCOME_KEY]: { status: 'skipped' },
      [noteKey('1.1.0')]: { status: 'read' },
    };
    expect(selectAutoPrompt({ notes: NOTES, states })).toBeNull();
    // 1.0.0 仍未读: 顶栏红点仍要亮
    expect(selectUnreadNotes({ notes: NOTES, states })).toHaveLength(1);
  });
});
