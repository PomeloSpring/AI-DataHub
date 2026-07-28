import { useState, useEffect } from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

interface CronInputProps {
  value: string;
  onChange: (value: string) => void;
}

const PRESETS: { label: string; value: string }[] = [
  { label: '每分钟', value: '* * * * *' },
  { label: '每小时', value: '0 * * * *' },
  { label: '每天 00:00', value: '0 0 * * *' },
  { label: '每天 08:00', value: '0 8 * * *' },
  { label: '每天 09:00', value: '0 9 * * *' },
  { label: '每周一 09:00', value: '0 9 * * 1' },
  { label: '每周一至五 09:00', value: '0 9 * * 1-5' },
  { label: '每月1号 09:00', value: '0 9 1 * *' },
  { label: '自定义', value: 'custom' },
];

function parseCronPreview(expr: string): string[] {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return ['无效的 Cron 表达式'];

  const [min, hour, dom, month, dow] = parts;
  const previews: string[] = [];

  // Simple preview for common patterns
  if (min === '*' && hour === '*') {
    previews.push('每分钟执行');
  } else if (hour === '*') {
    previews.push(`每小时的第 ${min} 分钟执行`);
  } else if (dom === '*' && month === '*' && dow === '*') {
    previews.push(`每天 ${hour.padStart(2, '0')}:${min.padStart(2, '0')} 执行`);
  } else if (dow !== '*' && dom === '*') {
    const dayNames: Record<string, string> = {
      '0': '周日', '1': '周一', '2': '周二', '3': '周三',
      '4': '周四', '5': '周五', '6': '周六', '7': '周日',
    };
    const dayLabel = dayNames[dow] || `周${dow}`;
    previews.push(`每${dayLabel} ${hour.padStart(2, '0')}:${min.padStart(2, '0')} 执行`);
  } else if (dom !== '*' && month === '*') {
    previews.push(`每月 ${dom} 日 ${hour.padStart(2, '0')}:${min.padStart(2, '0')} 执行`);
  } else {
    previews.push(`${expr}`);
  }

  return previews;
}

export default function CronInput({ value, onChange }: CronInputProps) {
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
      <Label>Cron 表达式</Label>
      <div className="flex gap-2">
        <Select value={mode} onValueChange={handlePresetChange}>
          <SelectTrigger className="w-[180px]">
            <SelectValue placeholder="选择预设" />
          </SelectTrigger>
          <SelectContent>
            {PRESETS.map(p => (
              <SelectItem key={p.value} value={p.value}>
                {p.label}
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
        格式：分 时 日 月 周（如 <code>0 9 * * 1-5</code> = 工作日 9 点）
      </p>
      {value && (
        <p className="text-xs text-blue-500">
          {previews.join('；')}
        </p>
      )}
    </div>
  );
}
