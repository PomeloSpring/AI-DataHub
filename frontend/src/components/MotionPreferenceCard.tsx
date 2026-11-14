import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Activity, RotateCcw, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Slider } from '@/components/ui/slider';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { useMotion } from '@/hooks/useMotion';
import { useMotionPrefStore } from '@/stores/motionPrefStore';
import { enterClass, enterStaggerStyle, motionSummaryLocalized, type MotionEnter } from '@/lib/motion';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import i18n from '@/i18n';

/** 差量旋钮：只开放「入场时长 / 错峰间隔」两个纯数值调，效果名一律回字模库预设里选
 *  （服务端 OVERRIDE_NUMBER_BOUNDS 同口径拒收非数值键，避免两处清单漂移）。 */
type EnterKnob = {
  key: 'durationMs' | 'staggerMs'; labelKey: string; unit: string;
  min: number; max: number; step: number; read: (enter: MotionEnter) => number;
};
const ENTER_KNOBS: EnterKnob[] = [
  { key: 'durationMs', labelKey: 'durationMs', unit: 'ms', min: 100, max: 1200, step: 20, read: e => e.durationMs },
  { key: 'staggerMs', labelKey: 'staggerMs', unit: 'ms', min: 0, max: 300, step: 10, read: e => e.staggerMs },
];

const FOLLOW_SYSTEM = '__follow__';
// 来源可读名：渲染期按 origin 走语言包，未登记的回退原值（与 ORIGIN_LABELS 旧口径同源）
const originLabel = (origin: string): string => {
  const k = `workspace:motion.origin.${origin}`;
  return i18n.exists(k) ? i18n.t(k as 'workspace:motion.origin.user') : origin;
};

export default function MotionPreferenceCard() {
  const { t } = useTranslation(['workspace', 'common']);
  const { motion, preset, origin, missing, invalid, reason, cssVars, presets, prefLoaded, prefError, reloadPref } = useMotion();
  const override = useMotionPrefStore(s => s.override);
  const savePreset = useMotionPrefStore(s => s.savePreset);
  const saveOverride = useMotionPrefStore(s => s.saveOverride);
  const [saving, setSaving] = useState(false);
  // 预览靠重挂载播一次（key 变了才重新触发入场动画），不常驻循环：大屏长期挂机不能烧 GPU
  const [previewKey, setPreviewKey] = useState(0);

  const enterOverride = override?.enter || {};
  const disabled = saving || !prefLoaded || origin === 'reduced';
  // 滑杆拖拽中的本地值（松手提交后回到服务端回读值）
  const [draft, setDraft] = useState<Record<string, number>>({});

  const patch = async (next: Record<string, unknown>) => {
    const merged = { ...(override || {}), enter: { ...(override?.enter || {}), ...next } };
    setSaving(true);
    try {
      await saveOverride(merged);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('motion.toast.overrideFailed'));
    } finally {
      setSaving(false);
    }
  };

  const resetKnob = async (key: string) => {
    const rest = { ...enterOverride };
    delete rest[key];
    setSaving(true);
    try {
      // 一个键都不剩时清除差量（回到纯预设），而不是留一个空 enter 段
      await saveOverride(Object.keys(rest).length ? { ...(override || {}), enter: rest } : null);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('motion.toast.resetFailed'));
    } finally {
      setSaving(false);
    }
  };

  const choosePreset = async (code: string | null) => {
    setSaving(true);
    try {
      await savePreset(code);
      toast.success(code ? t('motion.toast.presetSwitched', { name: presets.find(p => p.code === code)?.name || code }) : t('motion.toast.followSystem'));
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('motion.toast.presetFailed'));
    } finally {
      setSaving(false);
    }
  };

  const summary = useMemo(() => motionSummaryLocalized(motion), [motion]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity className="h-5 w-5" />
          {t('motion.title')}
        </CardTitle>
        <CardDescription>
          {t('motion.desc')}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!prefLoaded && (
          <div role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
            {prefError || t('motion.notLoaded')}
            <Button variant="link" size="sm" className="h-auto px-1 text-xs" onClick={() => void reloadPref()}>{t('common:action.retry')}</Button>
          </div>
        )}
        {reason && (
          <p role="status" className="text-xs text-muted-foreground">{reason}</p>
        )}
        {missing.length > 0 && (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            {t('motion.missingPrefix')}{missing.join(t('motion.missingSeparator'))}{t('motion.missingSuffix')}
          </p>
        )}
        {invalid.length > 0 && (
          <p className="text-xs text-destructive">{t('motion.invalidPrefix')}{invalid.join(t('motion.invalidSeparator'))}</p>
        )}

        <div className="space-y-2">
          <Label htmlFor="motion-preset">{t('motion.presetLabel')}</Label>
          <Select value={preset.code || FOLLOW_SYSTEM} disabled={disabled}
            onValueChange={v => void choosePreset(v === FOLLOW_SYSTEM ? null : v)}>
            <SelectTrigger id="motion-preset" className="w-full">
              <SelectValue placeholder={t('motion.presetPlaceholder')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={FOLLOW_SYSTEM}>{t('motion.followSystem')}</SelectItem>
              {presets.map(p => (
                <SelectItem key={p.code} value={p.code}>
                  <span className="inline-flex items-center gap-1.5">
                    <Sparkles className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    {p.name}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            {/* 被系统压制时不能还写着预设名：那看起来像“设了但在跑”，其实是零动效 */}
            {origin === 'reduced'
              ? <>{t('motion.activeReduced', { name: preset.name })}</>
              : <>{t('motion.activeSource', { name: preset.name, origin: originLabel(origin) })}</>}
            {origin === 'board' && t('motion.boardNote')}
          </p>
        </div>

        <div className="space-y-3">
          {ENTER_KNOBS.map(knob => {
            const value = knob.read(motion.enter);
            const tuned = enterOverride[knob.key] !== undefined;
            const knobLabel = t(`motion.knob.${knob.labelKey}` as 'motion.knob.durationMs');
            return (
              <div key={knob.key} role="group" aria-label={t('motion.knob.sliderAria', { label: knobLabel })}
                data-testid={`motion-knob-${knob.key}`} aria-disabled={disabled}
                className="space-y-1.5">
                <div className="flex items-center justify-between gap-2">
                  <Label className="text-xs">{knobLabel}
                    <span className="ml-1 text-muted-foreground">{value}{knob.unit}{tuned ? t('motion.knob.tuned') : t('motion.knob.presetValue')}</span>
                  </Label>
                  {tuned && (
                    <Button variant="ghost" size="sm" className="h-6 px-1.5 text-xs" disabled={disabled}
                      onClick={() => void resetKnob(knob.key)}>
                      <RotateCcw className="h-3 w-3 mr-1" />{t('motion.knob.reset')}
                    </Button>
                  )}
                </div>
                {/* 拖动只改本地显示，松手才提交：onValueChange 逐帧 PUT 会打出十几个重复保存 */}
                <Slider min={knob.min} max={knob.max} step={knob.step}
                  value={[Math.min(knob.max, Math.max(knob.min, draft[knob.key] ?? value))]}
                  disabled={disabled}
                  onValueChange={([v]) => setDraft(d => ({ ...d, [knob.key]: v }))}
                  onValueCommit={([v]) => {
                    setDraft(d => { const rest = { ...d }; delete rest[knob.key]; return rest; });
                    if (v !== value) void patch({ [knob.key]: v });
                  }} />
              </div>
            );
          })}
        </div>

        {/* 预览与真实渲染同源：同一套 class + CSS 变量（globals.css 只读变量），
            所以这里看到的节奏就是 Chat/看板上的节奏（ui-vis-library-style §2） */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label className="text-xs">{t('motion.preview')}</Label>
            <Button variant="outline" size="sm" className="h-6 text-xs" onClick={() => setPreviewKey(k => k + 1)}>{t('motion.replay')}</Button>
          </div>
          <div key={previewKey} style={cssVars} className="flex items-end gap-2 rounded-lg border bg-muted/20 p-3">
            {[0, 1, 2].map(i => (
              <div key={i}
                className={cn('h-10 flex-1 rounded-md bg-primary/70', enterClass(motion.enter))}
                style={enterStaggerStyle(i, motion.enter)}
                title={t('motion.blockTitle', { n: i + 1 })} />
            ))}
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          {summary.map(row => (
            <div key={row.label} className="flex items-baseline justify-between gap-2 border-b py-1">
              <dt className="text-muted-foreground">{row.label}</dt>
              <dd className="text-right">{row.text}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}
