import { useMemo } from 'react';
import { useLocation } from 'react-router-dom';
import { Compass, Home as HomeIcon, Folder, Database, Settings, Sparkles } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useTranslation } from 'react-i18next';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { GUIDE_ALL_KEY, GUIDE_WELCOME_KEY, noteKey, type ReleaseNote } from '@/api/releaseNotes';
import { GUIDES, asTranslate, guideAccessOf, pageGuideIdForPath } from '@/lib/onboarding/guides';
import { useGuideStore } from '@/stores/guideStore';
import { useMenuAccess } from '@/hooks/useMenuAccess';
import { useAuthStore } from '@/stores/authStore';

/**
 * 欢迎总览弹窗：首次登录 / 有未读重大版本时由 OnboardingHost 自动弹，
 * 也可从用户菜单「新手引导」手动打开。
 *
 * **只讲当前页面**：引导靠 `data-tour` 锚点定位 DOM，不在本页的引导内容播了就是
 * 点到空气，所以弹窗不列别的模块的卡片，也不给跳页的「查看新功能引导」入口；
 * 本页无可引导内容且无公告可看时，由 OnboardingHost / startGuide 判定不弹窗。
 *
 * 三档动作对应三种用户意图（跳过语义见方案《跳过（Skip）分层语义》）：
 *   开始引导   → 播放当前模块的分步引导
 *   稍后再说   → 本次不再弹（写 guide:welcome=skipped），下次登录仍按状态判定
 *   跳过全部引导 → 写 guide:all=skipped，之后任何引导都不再自动弹出，只剩手动入口
 *
 * 文案全部走 onboarding 命名空间（`guide.*` 步骤定义同源，见 lib/onboarding/guides.ts）：
 * 引导名/步骤标题不在代码里存字面量，切语言即时生效。
 */

const MODULE_META: Record<string, { icon: LucideIcon }> = {
  home: { icon: HomeIcon },
  workspace: { icon: Folder },
  data: { icon: Database },
  system: { icon: Settings },
};

export default function WelcomeDialog({
  open, upgradeNotes, onClose,
}: {
  open: boolean;
  /** 触发本次弹窗的重大版本公告(手动打开时为空) */
  upgradeNotes: ReleaseNote[];
  onClose: () => void;
}) {
  // 引导名 key 来自 GUIDES 数据（动态 key），走 asTranslate 适配
  const t = asTranslate(useTranslation('onboarding').t);
  const location = useLocation();
  const menuAccess = useMenuAccess();
  const user = useAuthStore(s => s.user);
  const putState = useGuideStore(s => s.putState);
  const startGuide = useGuideStore(s => s.startGuide);

  /** 当前页面上真实存在、且当前用户有权看的引导（'' = 本页没有可引导内容） */
  const here = useMemo(
    () => pageGuideIdForPath(location.pathname, guideAccessOf(menuAccess, user)),
    [location.pathname, menuAccess, user],
  );
  const hereGuide = here ? GUIDES[here] : undefined;
  const hereGuideLabel = hereGuide ? t(hereGuide.labelKey) : '';

  const readUpgradeKeys = () => upgradeNotes.map(n => noteKey(n.version));

  /**
   * 关闭前的落库次序: 先把本次升级公告标已读, 再落欢迎总览已处理。
   * 反过来的话一旦中途失败会留下"欢迎已跳过 + 公告未读"→ 下次又按升级弹一遍。
   */
  const settle = async (muteAll?: boolean) => {
    if (!(await putState(readUpgradeKeys(), 'read'))) return false;
    const keys = muteAll ? [GUIDE_WELCOME_KEY, GUIDE_ALL_KEY] : [GUIDE_WELCOME_KEY];
    return putState(keys, 'skipped');
  };

  /** 关闭(稍后再说 / 遮罩 / Esc): 未保存成功时保持弹窗可见, 不假装用户已跳过 */
  const dismiss = async () => {
    if (!(await settle())) return;
    onClose();
  };

  const skipAll = async () => {
    if (!(await settle(true))) return;
    onClose();
  };

  /** 从弹窗内某个入口直接播一条引导: 同样先落状态, 否则下次登录又自动弹一遍 */
  const play = async (id: string) => {
    if (!id) return;                       // 本页没有可引导内容: 不空跑一趟落库
    if (!(await settle())) return;
    onClose();
    startGuide(id);
  };

  const begin = () => play(here);

  const meta = here ? MODULE_META[here] : undefined;
  const Icon = meta?.icon || Compass;
  const desc = upgradeNotes.length
    ? (hereGuide ? t('welcome.descUpgradeWithGuide') : t('welcome.descUpgrade'))
    : (hereGuide
      ? t('welcome.descGuide', { label: hereGuideLabel, count: hereGuide.steps.length })
      : t('welcome.descFirst'));

  return (
    <Dialog open={open} onOpenChange={v => { if (!v) void dismiss(); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-primary" />
            {upgradeNotes.length ? t('welcome.titleUpgrade') : t('welcome.titleFirst')}
          </DialogTitle>
          <DialogDescription>{desc}</DialogDescription>
        </DialogHeader>

        {!!upgradeNotes.length && (
          <div className="space-y-2 rounded-lg border border-border/70 bg-muted/40 p-3">
            {upgradeNotes.map(n => (
              <div key={n.version}>
                <div className="flex items-center gap-2">
                  <Badge variant="secondary" className="text-[11px]">v{n.version}</Badge>
                  <span className="text-sm font-medium">{n.title}</span>
                  {/* 公告关联的引导就在本页时才给直达入口：跳页引导的锚点不在 DOM 里，点了就是点到空气 */}
                  {!!n.guide_key && n.guide_key === here && (
                    <Button
                      variant="link" size="sm" className="h-auto p-0 text-xs"
                      onClick={() => play(n.guide_key)}
                    >
                      {t('welcome.viewNewFeatureGuide')}
                    </Button>
                  )}
                </div>
                <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                  {n.sections.flatMap(s => s.items).slice(0, 4).map((it, i) => (
                    <li key={i}>· {it}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}

        {hereGuide && (
          <div className="space-y-1.5">
            <div className="text-xs font-medium text-muted-foreground">{t('welcome.currentPageGuide')}</div>
            <button
              type="button" onClick={() => play(here)}
              className="flex w-full items-start gap-3 rounded-lg border border-border/70 p-3 text-left transition-colors hover:bg-accent/60"
            >
              <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <Icon className="h-4 w-4" />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium">{hereGuideLabel}</span>
                <span className="block text-xs text-muted-foreground">
                  {t('welcome.stepsMeta', { count: hereGuide.steps.length, seconds: hereGuide.steps.length * 5 })}
                </span>
              </span>
            </button>
          </div>
        )}

        <DialogFooter className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-between">
          <button
            type="button" onClick={skipAll}
            className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            title={t('welcome.skipAllTitle')}
          >
            {t('welcome.skipAll')}
          </button>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={dismiss}>{t('welcome.later')}</Button>
            <Button size="sm" onClick={begin} disabled={!hereGuide}>
              <Compass className="mr-1.5 h-4 w-4" />{t('welcome.start')}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
