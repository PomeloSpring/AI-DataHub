import { useMemo, useState } from 'react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { cn } from '@/lib/utils';
import {
  CHART_APPEARS, DEFAULT_MOTION, ENTER_EFFECTS, MOTION_EASINGS, MOTION_SECTION_KEYS,
  REVEAL_EFFECTS, TRANSITION_EFFECTS, enterClass, enterStaggerStyle, g2Animate,
  motionCssVars, revealClass, sectionStaggerStyle, transitionClass, validateMotion, type MotionSpec,
} from '@/lib/motion';

type Section = keyof MotionSpec;
const SECTIONS: { key: Section; label: string; hint: string }[] = [
  { key: 'enter', label: '卡片入场', hint: 'Chat 结果卡与看板卡片的出现方式' },
  { key: 'chart', label: '图表动画', hint: '下发给图表引擎的 enter' },
  { key: 'transition', label: '切板转场', hint: '大屏/看板播放换板时的整层出现' },
  { key: 'reveal', label: '正文出现', hint: '详情页正文的出现方式' },
];
const NUMBER_FIELDS: Record<string, { label: string; max: number; step: number }> = {
  durationMs: { label: '时长 ms', max: 5000, step: 10 },
  staggerMs: { label: '错峰 ms', max: 1000, step: 10 },
  maxStaggerMs: { label: '错峰上限 ms', max: 5000, step: 50 },
};

const EFFECT_LABELS: Record<string, string> = {
  none: '无（直接呈现）',
  // 卡片入场
  fadeUp: '自下而上淡入', fade: '淡入', slideDown: '自上而下', slideLeft: '自右向左',
  scale: '缩放渐入', blur: '模糊渐显', wipeDown: '擦除展开',
  pop: '弹入落位（带一拍回弹）', flipUp: '透视翻入',
  // 图表（G2 注册表名）
  fadeIn: '淡入', pathIn: '折线描绘', growInY: '柱体生长', waveIn: '波次入场', zoomIn: '缩放推入',
  // 切板 / 正文
  pushLeft: '横向推入', sectionStagger: '分段错峰渐入',
  // 缓动
  default: '跟随现状（不额外指定）',
  standard: '常规（先快后慢）', snappy: '干脆', linear: '线性', ease: '平滑',
};

/** 图表段真正下发的值（不是 CSS 演示）：default 压根不写这个键，spec 逐字节不变 */
export function g2AnimateHint(spec: MotionSpec): string {
  const animate = g2Animate(spec.chart);
  if (animate === undefined) return '不写 animate 键（与改造前逐字节相同）';
  if (animate === false) return 'animate: false（关掉图表动画）';
  return `animate: ${JSON.stringify(animate)}`;
}

/** 迷你卡片预览：class / CSS 变量 / 错峰全部来自 lib/motion 同一份实现。 */
export function MotionPresetPreview({ spec, replay, onReplay }: { spec: MotionSpec; replay: number; onReplay: () => void }) {
  const vars = motionCssVars(spec);
  const enter = enterClass(spec.enter);
  const transition = transitionClass(spec.transition);
  const reveal = revealClass(spec.reveal);
  const staggered = spec.reveal.effect === 'sectionStagger';

  return (
    <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold">预览（与真实渲染同一份实现）</span>
        <Button type="button" variant="outline" size="sm" className="h-6 text-[11px]" onClick={onReplay}>重新播放</Button>
      </div>

      <div key={replay} style={vars} className="grid gap-3 sm:grid-cols-3">
        <div className="space-y-1">
          <div className="text-[10px] text-muted-foreground">卡片入场</div>
          <div className="flex h-14 items-end gap-1">
            {[0, 1, 2].map(i => (
              <div key={i} className={cn('h-9 flex-1 rounded bg-primary/70', enter)} style={enterStaggerStyle(i, spec.enter)} />
            ))}
          </div>
        </div>

        <div className="space-y-1">
          <div className="text-[10px] text-muted-foreground">切板转场</div>
          <div className="relative h-14 overflow-hidden rounded border bg-background">
            <div className="absolute inset-1 rounded bg-muted" />
            <div className={cn('absolute inset-1 rounded bg-primary/25', transition)} />
          </div>
        </div>

        <div className="space-y-1">
          <div className="text-[10px] text-muted-foreground">正文出现</div>
          <div className={cn('space-y-1 border bg-background p-1', !staggered && reveal)}>
            {[0, 1, 2].map(i => (
              <div key={i} className={cn('h-2 rounded bg-muted', staggered && reveal)}
                style={staggered ? sectionStaggerStyle(i, spec) : undefined} />
            ))}
          </div>
        </div>
      </div>

      <p className="text-[10px] text-muted-foreground">
        图表动画实际下发：{g2AnimateHint(spec)}
        <br />切板语义是「新层渐入」（旧层直接卸载），不做双缓冲淡出。
      </p>
    </div>
  );
}

export default function MotionPresetEditor({ value, onChange }: { value: string; onChange: (next: string) => void }) {
  const parsed = useMemo(() => {
    try { return { ok: true as const, data: (JSON.parse(value || '{}') ?? {}) as Record<string, unknown> }; }
    catch { return { ok: false as const, data: null }; }
  }, [value]);
  const [replay, setReplay] = useState(0);

  const spec = (parsed.data ?? {}) as unknown as MotionSpec;
  const errors = parsed.ok ? validateMotion(spec) : ['JSON 解析失败：先修好语法，再改参数'];

  const patch = (section: Section, key: string, next: unknown) => {
    const merged = { ...((spec[section] ?? {}) as unknown as Record<string, unknown>), [key]: next };
    onChange(JSON.stringify({ ...spec, [section]: merged }, null, 2));
  };

  if (!parsed.ok) {
    return (
      <div role="status" className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
        当前 JSON 解析不了，旋钮无法同步。修好语法会自动恢复，也可以「恢复默认」重写。
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-[11px] text-muted-foreground">
        四段都可留空（缺省走内置 mp_default：卡片自下而上淡入 450ms、图表跟随现状、切板与正文不额外动效）。
      </p>

      {!!errors.length && (
        <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/5 p-2 text-[11px] text-destructive">
          <div className="mb-0.5 font-medium">这些值不会生效（保存前请修正）：</div>
          <ul className="list-disc space-y-0.5 pl-4">{errors.map(e => <li key={e}>{e}</li>)}</ul>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {SECTIONS.map(({ key: section, label, hint }) => (
          <fieldset key={section} className="space-y-2 rounded-lg border p-2.5">
            <legend className="px-1 text-[11px] font-semibold">{label}</legend>
            <p className="text-[10px] text-muted-foreground">{hint}</p>
            {(MOTION_SECTION_KEYS[section] as readonly string[]).map(field => {
              const raw = (spec[section] as unknown as unknown as Record<string, unknown> | undefined)?.[field];
              if (field === 'effect' || field === 'appear' || field === 'easing') {
                return (
                  <div key={field} className="space-y-1">
                    <Label className="text-[10px] text-muted-foreground">
                      {{ effect: '效果', appear: '出现方式', easing: '缓动' }[field]}
                    </Label>
                    <Select value={String(raw ?? '')} onValueChange={v => patch(section, field, v)}>
                      <SelectTrigger className="h-7 text-xs" aria-label={`${label}${field}`}><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {optionsOf(section, field).map(o => (
                          <SelectItem key={o} value={o} className="text-xs">{EFFECT_LABELS[o] ?? o}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                );
              }
              const meta = NUMBER_FIELDS[field];
              if (!meta) return null;
              return (
                <div key={field} className="space-y-1">
                  <Label className="text-[10px] text-muted-foreground">{meta.label}</Label>
                  <Input type="number" className="h-7 text-xs" min={0} max={meta.max} step={meta.step}
                    aria-label={`${label}${field}`} value={Number(raw ?? 0)}
                    onChange={e => { const n = Number(e.target.value); patch(section, field, Number.isFinite(n) ? n : 0); }} />
                </div>
              );
            })}
          </fieldset>
        ))}
      </div>

      <div className="flex justify-end">
        <Button type="button" variant="outline" size="sm" className="h-6 text-[11px]"
          onClick={() => onChange(JSON.stringify(DEFAULT_MOTION, null, 2))}>恢复默认</Button>
      </div>

      <MotionPresetPreview spec={spec} replay={replay} onReplay={() => setReplay(r => r + 1)} />
    </div>
  );
}

/** 下拉可选值 */
function optionsOf(section: Section, key: string): readonly string[] {
  if (key === 'appear') return CHART_APPEARS;
  if (key === 'effect') return section === 'enter' ? ENTER_EFFECTS : section === 'transition' ? TRANSITION_EFFECTS : REVEAL_EFFECTS;
  return MOTION_EASINGS;
}
