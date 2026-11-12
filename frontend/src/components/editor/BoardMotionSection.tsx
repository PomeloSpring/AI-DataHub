import { useState } from 'react';
import { RotateCcw, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Slider } from '@/components/ui/slider';
import { Separator } from '@/components/ui/separator';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import { boardMotionRef, useMotion } from '@/hooks/useMotion';
import { applyScreenComponent, removeScreenComponent } from '@/lib/dashboardDesign';
import { cn } from '@/lib/utils';
import {
  enterClass, enterStaggerStyle, motionSummary, type MotionEnter, type MotionOverride, type MotionSpec,
} from '@/lib/motion';

/**
 * 看板属性面板的「动效」分组（P2：这块板播放/阅读时的出现效果）。
 *
 * 三条口径：
 * - **挂载 = 板级决定**：挂了字模就以它为准，观看者的个人预设只作用于未挂载的看板（分面裁决）；
 * - **写入口唯一**：挂载走 `applyScreenComponent`（与左侧字模库拖放同一份落库形状），
 *   取消挂载走 `removeScreenComponent`（清引用也清快照，否则「摘了但还在生效」）；
 * - **生效值与来源必须看得见**：库优先 / 快照兜底 / 禁用回退都说得出是哪一种，不让人猜。
 */

const UNMOUNT = '__unmount__';
const ORIGIN_LABELS: Record<string, string> = {
  board: '本看板挂载', user: '观看者的个人预设', default: '系统默认档（现网观感）', reduced: '系统「减少动态效果」',
};
/** 板级差量只开放纯数值旋钮（与 Profile 偏好卡、服务端 OVERRIDE_NUMBER_BOUNDS 同一口径） */
type EnterKnob = {
  key: 'durationMs' | 'staggerMs'; label: string; min: number; max: number; step: number;
  read: (enter: MotionEnter) => number;
};
const ENTER_KNOBS: EnterKnob[] = [
  { key: 'durationMs', label: '入场时长', min: 100, max: 1200, step: 20, read: e => e.durationMs },
  { key: 'staggerMs', label: '逐块错峰', min: 0, max: 300, step: 10, read: e => e.staggerMs },
];

export default function BoardMotionSection({ filters, onFiltersChange, onReplay }: {
  filters: any;
  onFiltersChange: (filters: any) => void;
  /** 拖拽主画布临时重播一次入场（与预览色块同步） */
  onReplay?: () => void;
}) {
  const { items } = useVisLibrary();
  const board = boardMotionRef(filters);
  const { motion, preset, origin, missing, invalid, reason, cssVars, presets } = useMotion({ board });
  // 预览靠重挂载播一次（改 key 才重触发入场动画），不常驻循环：编辑器长期开着不该烧 GPU
  const [previewKey, setPreviewKey] = useState(0);
  const [draft, setDraft] = useState<Record<string, number>>({});
  const override: MotionOverride = board?.override || {};
  const enterOverride = override.enter || {};
  const mountedCode = board?.ref || '';
  const tuned = (key: string) => enterOverride[key] !== undefined;

  const choose = (code: string) => {
    if (code === UNMOUNT) { onFiltersChange(removeScreenComponent(filters, 'motion_preset')); return; }
    const component = items.find(c => c.category === 'motion_preset' && c.code === code);
    // 清单里的每一项都来自库（在用口径），拿不到就是清单与库脱节 —— 报错而不是静默不挂
    if (!component) return;
    onFiltersChange(applyScreenComponent(filters, component));
  };

  const writeOverride = (patch: Record<string, unknown> | null) => {
    const design = { ...(filters?.design || {}) };
    if (patch === null) delete design.motionOverride;
    else design.motionOverride = patch;
    onFiltersChange({ ...(filters || {}), design });
  };
  const patchKnob = (key: string, value: number) => {
    if (value === ENTER_KNOBS.find(k => k.key === key)!.read(motion.enter)) return;   // 与预设同值 = 不必存差量
    writeOverride({ ...override, enter: { ...enterOverride, [key]: value } });
  };
  const resetKnob = (key: string) => {
    const rest = { ...enterOverride };
    delete rest[key];
    writeOverride(Object.keys(rest).length ? { ...override, enter: rest } : null);
  };

  return (
    <>
      <Separator />
      <div className="space-y-3">
        <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1">
          <Sparkles className="h-3 w-3" /> 动效（出现效果）
        </div>
        <p className="text-[10px] text-muted-foreground leading-relaxed">
          这块板在阅读页与大屏播放时的入场、图表动画与切板转场。挂载后以板为准（观看者自己的动效偏好只作用于未挂载的看板）；
          不挂载则跟随观看者。动效与主题配色互不影响。
        </p>
        <div className="space-y-1">
          <Label className="text-xs" htmlFor="board-motion-preset">动效预设</Label>
          <Select value={mountedCode || UNMOUNT} onValueChange={choose}>
            <SelectTrigger id="board-motion-preset" className="h-7 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={UNMOUNT} className="text-xs">不挂载（跟随观看者偏好）</SelectItem>
              {presets.map(p => <SelectItem key={p.code} value={p.code} className="text-xs" title={p.code}>{p.name}</SelectItem>)}
            </SelectContent>
          </Select>
          <p className="text-[10px] text-muted-foreground">
            {/* 系统压制时要说清“现在其实没动效”，留着预设名会让人以为还在跑那套节奏 */}
            {origin === 'reduced'
              ? <>当前生效：无动效（预设「{preset.name}」被系统「减少动态效果」压制）</>
              : <>当前生效：{preset.name}（来源：{ORIGIN_LABELS[origin] || origin}）</>}
          </p>
          {/* 失效原因不藏着：挂了但被禁用/下架时，界面必须说出为什么按默认呈现 */}
          {reason && <p role="status" className="text-[10px] text-amber-600 dark:text-amber-400">{reason}</p>}
          {missing.length > 0 && <p className="text-[10px] text-amber-600 dark:text-amber-400">引用不在用：{missing.join('、')}</p>}
          {invalid.length > 0 && <p role="alert" className="text-[10px] text-destructive">非法旋钮，已按默认值呈现：{invalid.join('；')}</p>}
        </div>

        {origin !== 'reduced' && mountedCode && ENTER_KNOBS.map(knob => {
          const value = knob.read(motion.enter);
          return (
            <div key={knob.key} role="group" aria-label={`本看板${knob.label}滑杆`} className="space-y-1">
              <div className="flex items-center justify-between gap-2">
                <Label className="text-[10px] text-muted-foreground">{knob.label}
                  <span className="ml-1">{value}ms{tuned(knob.key) ? '（本看板调整）' : '（预设值）'}</span>
                </Label>
                {tuned(knob.key) && (
                  <Button variant="ghost" size="sm" className="h-5 px-1.5 text-[10px]" onClick={() => resetKnob(knob.key)}>
                    <RotateCcw className="h-3 w-3 mr-1" />恢复预设值
                  </Button>
                )}
              </div>
              {/* 拖动只改本地显示，松手才写草稿：逐帧写 filters 会把撤销栈灌满 */}
              <Slider min={knob.min} max={knob.max} step={knob.step}
                value={[Math.min(knob.max, Math.max(knob.min, draft[knob.key] ?? value))]}
                onValueChange={([v]) => setDraft(d => ({ ...d, [knob.key]: v }))}
                onValueCommit={([v]) => {
                  setDraft(d => { const rest = { ...d }; delete rest[knob.key]; return rest; });
                  patchKnob(knob.key, v);
                }} />
            </div>
          );
        })}
        {origin === 'reduced' && <p className="text-[10px] text-muted-foreground">系统已开启「减少动态效果」，本看板的调整暂时不可用。</p>}

        {/* 预览与真实渲染同源：同一套 class + CSS 变量（globals.css 只读变量） */}
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <Label className="text-[10px] text-muted-foreground">入场预览</Label>
            <Button variant="outline" size="sm" className="h-5 text-[10px]"
              onClick={() => { setPreviewKey(k => k + 1); onReplay?.(); }}>重新播放</Button>
          </div>
          <div key={previewKey} style={cssVars as React.CSSProperties} className="flex items-end gap-1.5 rounded-md border bg-muted/20 p-2">
            {[0, 1, 2].map(i => <div key={i} className={cn('h-7 flex-1 rounded bg-primary/70', enterClass(motion.enter))}
              style={enterStaggerStyle(i, motion.enter)} title={`第 ${i + 1} 块`} />)}
          </div>
        </div>

        <dl className="grid grid-cols-1 gap-y-0.5 text-[10px]">
          {motionSummary(motion as MotionSpec).map(row => (
            <div key={row.label} className="flex items-baseline justify-between gap-2 border-b py-0.5">
              <dt className="text-muted-foreground">{row.label}</dt>
              <dd>{row.text}</dd>
            </div>
          ))}
        </dl>
        <p className="text-[10px] text-muted-foreground">只改草稿，点击「保存」后在阅读页与大屏播放生效。</p>
      </div>
    </>
  );
}
