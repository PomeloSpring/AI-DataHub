import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('sonner', () => {
  // sonner 的 toast 既是函数又挂了方法（toast(...) / toast.success(...)）——
  // mock 必须同样可调用，否则测不出「默认 toast 可调用」这条契约
  const callable: any = vi.fn();
  callable.success = vi.fn();
  callable.info = vi.fn();
  callable.warning = vi.fn();
  callable.error = vi.fn();
  callable.dismiss = vi.fn();
  callable.custom = vi.fn();
  callable.promise = vi.fn();
  return { Toaster: (props: any) => ({ type: 'toaster', props }), toast: callable };
});

const sonner = await import('sonner');
const mod = await import('../toast');

beforeEach(() => {
  vi.mocked(sonner.toast.success).mockClear();
  vi.mocked(sonner.toast.info).mockClear();
  vi.mocked(sonner.toast.warning).mockClear();
  vi.mocked(sonner.toast.error).mockClear();
  vi.mocked(sonner.toast as any).mockClear?.();
});

describe('默认 toast(...) 可调用', () => {
  it('toast 直接调用也走分档（不是只挂了方法的对象）', () => {
    mod.toast('已删除「销售看板」');
    expect(sonner.toast).toHaveBeenCalledWith('已删除「销售看板」', expect.objectContaining({
      duration: mod.TOAST_DURATION.info,
    }));
  });

  it('带可点按钮时自动取长档 —— 2s 内来不及点会把「撤销」实际废掉', () => {
    const action = { label: '撤销', onClick: () => {} };
    mod.toast('已删除 1 个组件', { action });
    expect(sonner.toast).toHaveBeenCalledWith('已删除 1 个组件', expect.objectContaining({
      action,
      duration: mod.TOAST_DURATION.error,
    }));
    expect(mod.TOAST_DURATION.error).toBeGreaterThan(mod.TOAST_DURATION.info);
  });
});

describe('toast 分档时长', () => {
  it('success/info 短、error/warning 长 —— 确认条一闪即逝，报错留出阅读时间', () => {
    expect(mod.TOAST_DURATION.success).toBeLessThanOrEqual(mod.TOAST_DURATION.error);
    expect(mod.TOAST_DURATION.info).toBeLessThanOrEqual(mod.TOAST_DURATION.warning);
    expect(mod.TOAST_DURATION.success).toBe(2000);
    expect(mod.TOAST_DURATION.error).toBe(3000);
  });

  it('每档都把 duration 显式下发，不靠 sonner 默认值', () => {
    mod.toast.success('ok');
    mod.toast.error('bad');
    expect(sonner.toast.success).toHaveBeenCalledWith('ok', { duration: mod.TOAST_DURATION.success });
    expect(sonner.toast.error).toHaveBeenCalledWith('bad', { duration: mod.TOAST_DURATION.error });
  });

  it('调用点可覆盖时长（长驻/自定义不受分档限制）', () => {
    mod.toast.error('important', { duration: Infinity });
    expect(sonner.toast.error).toHaveBeenCalledWith('important', { duration: Infinity });
    mod.toast.success('pin', { duration: 10_000 });
    expect(sonner.toast.success).toHaveBeenCalledWith('pin', { duration: 10_000 });
  });
});

describe('AppToaster 全局配置', () => {
  it('必须开启 closeButton —— 用户抱怨「没法主动消掉」，这是主要诉求', () => {
    const el = mod.AppToaster() as any;
    expect(el.props.closeButton).toBe(true);
  });

  it('限制堆叠数量，避免连续保存时盖住界面', () => {
    const el = mod.AppToaster() as any;
    expect(el.props.visibleToasts).toBeGreaterThan(0);
    expect(el.props.visibleToasts).toBeLessThanOrEqual(5);
  });
});
