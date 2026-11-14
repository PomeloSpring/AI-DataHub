import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import i18n from '@/i18n';

interface CronInputProps {
  value: string;
  onChange: (value: string) => void;
}

// 预设只存 key，文案渲染期 t() 解析（模块顶层写死文案会冻结语言）
const PRESETS: { labelKey: string; value: string }[] = [
  { labelKey: 'everyMinute', value: '* * * * *' },
  { labelKey: 'hourly', value: '0 * * * *' },
  { labelKey: 'daily00', value: '0 0 * * *' },
  { labelKey: 'daily08', value: '0 8 * * *' },
  { labelKey: 'daily09', value: '0 9 * * *' },
  { labelKey: 'weeklyMon', value: '0 9 * * 1' },
  { labelKey: 'weekdays', value: '0 9 * * 1-5' },
  { labelKey: 'monthly1', value: '0 9 1 * *' },
  { labelKey: 'custom', value: 'custom' },
];

function parseCronPreview(expr: string): string[] {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return [i18n.t('workspace:cron.invalid')];

  const [min, hour, dom, month, dow] = parts;
  const previews: string[] = [];

  // Simple preview for common patterns
  if (min === '*' && hour === '*') {
    previews.push(i18n.t('workspace:cron.previewEveryMinute'));
  } else if (hour === '*') {
    previews.push(i18n.t('workspace:cron.previewHourly', { min }));
  } else if (dom === '*' && month === '*' && dow === '*') {
    previews.push(i18n.t('workspace:cron.previewDaily', { time: `${hour.padStart(2, '0')}:${min.padStart(2, '0')}` }));
  } else if (dow !== '*' && dom === '*') {
    const dayLabel = i18n.exists(`workspace:cron.days.${dow}`)
      ? i18n.t(`workspace:cron.days.${dow}` as 'workspace:cron.days.1')
      : i18n.t('workspace:cron.dayFallback', { dow });
    previews.push(i18n.t('workspace:cron.previewWeekly', { day: dayLabel, time: `${hour.padStart(2, '0')}:${min.padStart(2, '0')}` }));
  } else if (dom !== '*' && month === '*') {
    previews.push(i18n.t('workspace:cron.previewMonthly', { dom, time: `${hour.padStart(2, '0')}:${min.padStart(2, '0')}` }));
  } else {
    previews.push(`${expr}`);
  }

  return previews;
}

export default function CronInput({ value, onChange }: CronInputProps) {
  const { t } = useTranslation(['workspace']);
  const [mode, setMode] = useState<string>('custom');

  useEffect(() => {
    const preset = PRESETS.find(p => p.value === value);
    if (preset && preset.value !== 'custom') {
      setMode(preset.value);
    } else {
      setMode('custom');
    }
  }, [value]);

  const handlePresetChange = (preset: string) => {
    setMode(preset);
    if (preset !== 'custom') {
      onChange(preset);
    }
  };

  const previews = parseCronPreview(value);

  return (
    <div className="space-y-2">
      <Label>{t('cron.label')}</Label>
      <div className="flex gap-2">
        <Select value={mode} onValueChange={handlePresetChange}>
          <SelectTrigger className="w-[180px]">
            <SelectValue placeholder={t('cron.presetPlaceholder')} />
          </SelectTrigger>
          <SelectContent>
            {PRESETS.map(p => (
              <SelectItem key={p.value} value={p.value}>
                {t(`cron.preset.${p.labelKey}` as 'workspace:cron.preset.custom')}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          value={value}
          onChange={e => {
            onChange(e.target.value);
            setMode('custom');
          }}
          placeholder="* * * * *"
          className="font-mono flex-1"
        />
      </div>
      <p className="text-xs text-muted-foreground">
        {t('cron.formatBefore')}<code>0 9 * * 1-5</code>{t('cron.formatAfter')}
      </p>
      {value && (
        <p className="text-xs text-blue-500">
          {previews.join(t('cron.previewSeparator'))}
        </p>
      )}
    </div>
  );
}
