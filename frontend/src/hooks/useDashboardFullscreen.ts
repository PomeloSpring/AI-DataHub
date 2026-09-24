import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type RefObject } from 'react';
import { toast } from 'sonner';

export function useDashboardFullscreen(ref: RefObject<HTMLElement>) {
  const [fullscreen, setFullscreen] = useState(false);
  const ownedElement = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const change = () => setFullscreen(!!ref.current && document.fullscreenElement === ref.current);
    document.addEventListener('fullscreenchange', change);
    return () => {
      document.removeEventListener('fullscreenchange', change);
      if (ownedElement.current && document.fullscreenElement === ownedElement.current) void document.exitFullscreen().catch(() => toast.error('退出全屏失败，请按 Esc'));
    };
  }, [ref]);
  const toggleFullscreen = useCallback(async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (ref.current?.requestFullscreen) { ownedElement.current = ref.current; await ref.current.requestFullscreen(); }
      else throw new Error('浏览器不支持全屏');
    } catch { toast.error('无法切换全屏，请检查浏览器权限'); }
  }, [ref]);
  return { fullscreen, toggleFullscreen };
}

const subscribeFullscreen = (notify: () => void) => {
  document.addEventListener('fullscreenchange', notify);
  return () => document.removeEventListener('fullscreenchange', notify);
};
export function useFullscreenContainer() {
  return useSyncExternalStore(subscribeFullscreen, () => document.fullscreenElement as HTMLElement | null, () => null);
}
