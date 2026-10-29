import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate, useParams } from 'react-router-dom';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { GUIDES, type TourStep } from '@/lib/onboarding/guides';
import { GUIDE_ALL_KEY, guideKey } from '@/api/releaseNotes';
import { useGuideStore } from '@/stores/guideStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { toast } from '@/lib/toast';

/**
 * 分步引导引擎(自建，不引第三方 tour 库)。
 *
 * 视觉：一层 fixed 元素按目标 rect 定位 + `box-shadow: 0 0 0 9999px` 挖洞压暗，
 * 说明卡跟随其上下左右可用空间；全部走主题变量，不硬编码色值(字模库规范)。
 *
 * 失败处理：目标锚点找不到时**不静默跳过** —— 卡片显式写明"未找到该入口"并允许
 * 继续/退出；最后一步找不到时中断且**不写 completed**(否则用户以为已看完)。
 */

/** 跨路由渲染等待上限: 导航后目标元素可能要 1s 量级才出现 */
const TARGET_WAIT_MS = 1200;
/** 目标缺失时提示卡的驻留时长, 到点自动前进 */
const MISSING_HOLD_MS = 2000;
const CARD_W = 320;
const GAP = 12;

type Rect = { top: number; left: number; width: number; height: number };

function findTarget(target: string): HTMLElement | null {
  // 同一锚点可能同时存在于桌面侧栏与抽屉菜单(窄屏下桌面侧栏 display:none),
  // 取**第一个真可见**的: 按 DOM 顺序拿首个元素会在窄屏上误判"找不到入口"
  const list = document.querySelectorAll<HTMLElement>(`[data-tour="${target}"]`);
  for (const el of list) {
    const r = el.getBoundingClientRect();
    // 尺寸为 0 = 未渲染/被隐藏(如窄屏收纳), 当作"找不到"处理, 不能画一个空气高亮
    if (r.width > 0 || r.height > 0) return el;
  }
  return null;
}

export default function TourEngine({ guideId }: { guideId: string }) {
  const guide = GUIDES[guideId];
  const navigate = useNavigate();
  const { workspaceId } = useParams();
  const stopGuide = useGuideStore(s => s.stopGuide);
  const putState = useGuideStore(s => s.putState);

  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  /** 目标缺失原因(空串 = 目标正常) */
  const [missing, setMissing] = useState('');
  const navDoneFor = useRef<string>('');

  const steps = guide?.steps || [];
  const step: TourStep | undefined = steps[index];
  const isLast = index >= steps.length - 1;

  // 需要换页的步骤: 先导航再找元素(整步一次, 避免重渲染反复跳转)
  useLayoutEffect(() => {
    if (!step?.route) return;
    const mark = `${guideId}:${index}`;
    if (navDoneFor.current === mark) return;
    const wsId = workspaceId
      || useWorkspaceStore.getState().currentWorkspaceId
      || useWorkspaceStore.getState().getDefaultWorkspaceId();
    if (!wsId) {
      setMissing('未确定当前工作空间，无法打开该页面');
      return;
    }
    navDoneFor.current = mark;
    navigate(step.route.replace(':wsId', String(wsId)), { replace: true });
  }, [step, index, guideId, navigate, workspaceId]);

  // 定位 + 跟随(滚动/尺寸变化重算)
  useEffect(() => {
    const target = step?.target;
    if (!target) { setRect(null); setMissing(''); return; }
    let raf = 0;
    let deadline = 0;
    const tick = () => {
      const el = findTarget(target);
      if (el) {
        setMissing('');
        const r = el.getBoundingClientRect();
        setRect({ top: r.top, left: r.left, width: r.width, height: r.height });
        el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' });
        return;
      }
      if (!deadline) deadline = Date.now() + TARGET_WAIT_MS;
      if (Date.now() < deadline) { raf = requestAnimationFrame(tick); return; }
      setRect(null);
      setMissing(`未找到「${step?.title}」入口（页面变化或窗口过小），已跳过这一步`);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [step]);

  useEffect(() => {
    if (missing) {
      // 显式提示后自动前进; 最后一步缺失时不自动"完成"(见 finish 分支)
      const t = setTimeout(() => {
        if (!isLast) setIndex(i => i + 1);
      }, MISSING_HOLD_MS);
      return () => clearTimeout(t);
    }
    const onReflow = () => {
      const target = step?.target;
      if (!target) return;
      const el = findTarget(target);
      if (!el) return;
      const r = el.getBoundingClientRect();
      setRect({ top: r.top, left: r.left, width: r.width, height: r.height });
    };
    window.addEventListener('resize', onReflow);
    window.addEventListener('scroll', onReflow, true);
    return () => {
      window.removeEventListener('resize', onReflow);
      window.removeEventListener('scroll', onReflow, true);
    };
  }, [step, missing, isLast]);

  // 键盘: Esc = 跳过本引导, Enter/→ 下一步, ← 上一步
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); void skipGuide(); return; }
      if (e.key === 'Enter' || e.key === 'ArrowRight') { e.preventDefault(); void next(); return; }
      if (e.key === 'ArrowLeft') { e.preventDefault(); setIndex(i => Math.max(0, i - 1)); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const finish = async () => {
    if (missing && isLast) {
      // 末步没讲到就退出: 不落 completed, 让用户知道本次没走完
      toast.warning('本次引导未走完（页面元素未出现），可从用户菜单「新手引导」重试');
      stopGuide();
      return;
    }
    const ok = await putState([guideKey(guideId)], 'completed');
    if (!ok) return;
    stopGuide();
    toast.success('引导已完成，可随时从右上角用户菜单「新手引导」重新观看');
  };

  const skipGuide = async () => {
    const ok = await putState([guideKey(guideId)], 'skipped');
    if (!ok) return;   // 未保存成功则保持引导可见，不假装已跳过
    stopGuide();
  };

  const muteAll = async () => {
    const ok = await putState([GUIDE_ALL_KEY], 'skipped');
    if (!ok) return;
    stopGuide();
  };

  const next = async () => {
    if (isLast) { await finish(); return; }
    setIndex(i => i + 1);
  };

  if (!guide || !step) return null;

  const cardStyle = computeCardStyle(rect, step.placement || 'auto');
  const zBase = 120;

  return createPortal(
    <div role="dialog" aria-label={`${guide.label}引导`} data-testid="tour-engine">
      {/* 点击拦截层: 引导期间不响应页面操作(说明用, 页面跳转由引擎负责) */}
      <div data-testid="tour-blocker" style={{ position: 'fixed', inset: 0, zIndex: zBase - 2 }} />
      {/* 压暗 + 挖洞高亮 */}
      {rect && (
        <div
          data-testid="tour-highlight"
          style={{
            position: 'fixed', zIndex: zBase - 1,
            top: Math.max(0, rect.top - 4), left: Math.max(0, rect.left - 4),
            width: rect.width + 8, height: rect.height + 8,
            borderRadius: 10,
            boxShadow: '0 0 0 9999px rgba(2, 6, 23, 0.62)',
            border: '1px solid hsl(var(--primary))',
            transition: 'top .15s ease, left .15s ease, width .15s ease, height .15s ease',
            pointerEvents: 'none',
          }}
        />
      )}
      <div
        data-testid="tour-card"
        style={{ position: 'fixed', zIndex: zBase, width: CARD_W, ...cardStyle }}
        className="rounded-xl border border-border/70 bg-card p-4 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-2">
          <div className="text-sm font-semibold text-card-foreground">{step.title}</div>
          <Button
            variant="ghost" size="sm" className="h-6 w-6 p-0 text-muted-foreground"
            aria-label="跳过本引导（Esc）" onClick={skipGuide}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
        <div className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{step.body}</div>
        {missing && (
          <div className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-600 dark:text-amber-400">
            {missing}
          </div>
        )}
        <div className="mt-3 flex items-center justify-between gap-2">
          <span className="text-[11px] text-muted-foreground">第 {index + 1}/{steps.length} 步</span>
          <div className="flex items-center gap-1.5">
            <Button variant="outline" size="sm" className="h-7 px-2 text-xs"
              disabled={index === 0} onClick={() => setIndex(i => Math.max(0, i - 1))}>上一步</Button>
            <Button size="sm" className="h-7 px-2.5 text-xs" onClick={next}>
              {isLast ? '完成' : '下一步'}
            </Button>
          </div>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border/60 pt-2">
          <button type="button" className="text-[11px] text-muted-foreground hover:text-foreground"
            onClick={() => { if (!isLast) setIndex(i => i + 1); else void finish(); }}>
            跳过这一步
          </button>
          <button type="button" className="text-[11px] text-muted-foreground hover:text-foreground"
            onClick={skipGuide}>
            跳过本引导
          </button>
          <button type="button" className="text-[11px] text-muted-foreground hover:text-foreground"
            onClick={muteAll} title="之后不再自动弹出，可随时从用户菜单手动重看">
            不再自动弹出
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** 卡片落点样式: 优先目标下方 → 上方 → 右侧; 无目标(纯讲解步)居中 */
function computeCardStyle(
  rect: Rect | null,
  placement: 'auto' | 'bottom' | 'top' | 'right',
): CSSProperties {
  if (!rect) return { left: '50%', top: '50%', transform: 'translate(-50%, -50%)' };
  const vh = window.innerHeight || 800;
  const vw = window.innerWidth || 1200;
  const CARD_H = 190;
  const clampLeft = (x: number) => Math.min(Math.max(8, x), Math.max(8, vw - CARD_W - 8));
  const fitsAbove = rect.top - CARD_H - GAP >= 0;
  const fitsBelow = rect.top + rect.height + CARD_H + GAP <= vh;
  const fitsRight = rect.left + rect.width + CARD_W + GAP <= vw;
  if (placement === 'right') {
    if (fitsRight) return { left: clampLeft(rect.left + rect.width + GAP), top: Math.max(8, rect.top) };
  } else if (placement === 'top' || (placement === 'auto' && !fitsBelow && fitsAbove)) {
    return { left: clampLeft(rect.left), top: Math.max(8, rect.top - CARD_H - GAP) };
  } else if (placement === 'bottom' || fitsBelow) {
    return { left: clampLeft(rect.left), top: rect.top + rect.height + GAP };
  }
  if (fitsRight) return { left: clampLeft(rect.left + rect.width + GAP), top: Math.max(8, rect.top) };
  // 上下右都装不下(小窗口/长元素): 贴着目标夹到视口内
  return { left: clampLeft(rect.left), top: Math.max(8, Math.min(rect.top, vh - CARD_H - 8)) };
}
