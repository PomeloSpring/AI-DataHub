import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import TourEngine from './TourEngine';
import WelcomeDialog from './WelcomeDialog';
import { GUIDES } from '@/lib/onboarding/guides';
import { selectAutoPrompt, useGuideStore } from '@/stores/guideStore';
import { useAuthStore } from '@/stores/authStore';
import { usePermissionStore } from '@/stores/permissionStore';
import type { ReleaseNote } from '@/api/releaseNotes';

/**
 * 引导/公告的无 UI 宿主(挂在 BrowserRouter 内、Routes 外)。
 *
 * 职责只有两件:
 *   1. 身份 + 权限就绪后拉一次服务端状态(公告清单 + 本人引导状态);
 *   2. 按《触发与状态语义》判定是否自动弹欢迎总览, 并渲染 <TourEngine> 消费 activeGuide。
 *
 * 自动弹每次会话只判定一次(autoFired): 关闭失败已有 toast 显式暴露, 不在这里反复骚扰用户。
 * 手动入口(用户菜单/公告面板)不经过本组件的判定, 因此静音标记只压制自动弹, 压不住手动重看。
 */

/** 这些路由不弹引导: 登录页无身份; 大屏/分享报告/编辑器是消费态页面, 覆盖一层向导只会碍事 */
const NO_AUTO_PATHS = ['/login', '/report', '/screen', '/dashboard/editor'];

export default function OnboardingHost() {
  const location = useLocation();
  const token = useAuthStore(s => s.token);
  const permLoaded = usePermissionStore(s => s.loaded);
  const notes = useGuideStore(s => s.notes);
  const states = useGuideStore(s => s.states);
  const loaded = useGuideStore(s => s.loaded);
  const load = useGuideStore(s => s.load);
  const activeGuide = useGuideStore(s => s.activeGuide);
  const welcomeOpen = useGuideStore(s => s.welcomeOpen);
  const requestWelcome = useGuideStore(s => s.requestWelcome);
  const closeWelcome = useGuideStore(s => s.closeWelcome);

  const [upgradeNotes, setUpgradeNotes] = useState<ReleaseNote[]>([]);
  const autoFired = useRef(false);

  useEffect(() => {
    if (!token || !permLoaded) return;
    void load();
  }, [token, permLoaded, load]);

  const auto = useMemo(() => selectAutoPrompt({ notes, states }), [notes, states]);

  // 判定只在状态真变化时发生(load 每会话一次); autoFired 保证本次登录只骚扰一遍
  useEffect(() => {
    if (!token || !loaded || autoFired.current || !auto) return;
    if (welcomeOpen || activeGuide) return;   // 用户已手动打开: 不覆盖它的内容快照
    if (NO_AUTO_PATHS.some(p => location.pathname.startsWith(p))) return;
    autoFired.current = true;
    setUpgradeNotes(auto.notes);
    requestWelcome();
  }, [auto, token, loaded, location.pathname, welcomeOpen, activeGuide, requestWelcome]);

  if (!token) return null;

  return (
    <>
      <WelcomeDialog
        open={welcomeOpen}
        upgradeNotes={upgradeNotes}
        onClose={closeWelcome}
      />
      {activeGuide && GUIDES[activeGuide] && <TourEngine guideId={activeGuide} />}
    </>
  );
}
