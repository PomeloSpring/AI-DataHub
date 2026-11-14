import { useMemo } from 'react';
import { BarChart3 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Badge } from '@/components/ui/badge';
import { useVisConfig } from '@/lib/visStyle';
import { profileTable, type ColumnProfile } from '@/lib/dataProfile';

interface Props {
  columns: string[];
  rows: Record<string, any>[];
  title?: string;
}

// 列类型文案只存 key，渲染期经 t() 取（模块顶层常量会冻结语言）
type ProfileKindKey = 'profile.kind.number' | 'profile.kind.category' | 'profile.kind.text' | 'profile.kind.datetime' | 'profile.kind.empty';
const KIND_LABEL_KEYS: Record<ColumnProfile['kind'], ProfileKindKey> = {
  number: 'profile.kind.number', category: 'profile.kind.category', text: 'profile.kind.text',
  datetime: 'profile.kind.datetime', empty: 'profile.kind.empty',
};

// 字模缺失时的内置默认样式(与 seed 迁移 vis_analysis_migration.sql 同值,保证离线可用);
// topN/bins 为可视化旋钮(字模 style_config 可调)
const DEFAULT_KPI = {
  cardBg: 'hsl(var(--card))', cardBorder: '1px solid hsl(var(--border))',
  borderRadius: '10px', valueColor: 'hsl(var(--foreground))', labelColor: 'hsl(var(--muted-foreground))',
  topN: 5, bins: 8,
};
const DEFAULT_HIST = { colorScheme: ['hsl(var(--chart-1))', 'hsl(var(--chart-2))'], borderRadius: 2 };

/** 迷你分布条(数值分箱/类别 TopN),颜色取 chart_style 字模色板。 */
function MiniBars({ bins, colorScheme, radius }: {
  bins: { label: string; count: number }[]; colorScheme: string[]; radius: number;
}) {
  const max = Math.max(1, ...bins.map(b => b.count));
  return (
    <div className="flex h-12 items-end gap-0.5" title={bins.map(b => `${b.label}: ${b.count}`).join('\n')}>
      {bins.map((b, i) => (
        <div
          key={i}
          className="min-w-0 flex-1"
          style={{
            height: `${Math.max(4, (b.count / max) * 100)}%`,
            background: colorScheme[i % colorScheme.length],
            borderRadius: radius,
          }}
        />
      ))}
    </div>
  );
}

function StatTile({ label, value, cfg }: { label: string; value: string; cfg: typeof DEFAULT_KPI }) {
  return (
    <div
      className="flex-1 min-w-0 px-2 py-1.5"
      style={{ background: cfg.cardBg, border: cfg.cardBorder, borderRadius: cfg.borderRadius }}
    >
      <div className="truncate text-[10px]" style={{ color: cfg.labelColor }}>{label}</div>
      <div className="truncate text-sm font-semibold" style={{ color: cfg.valueColor }} title={value}>{value}</div>
    </div>
  );
}

/**
 * 数据画像卡片:逐列统计(类型/非空/缺失率/唯一值)+ 迷你分布。
 * 视觉样式优先取字模库(card_profile_stat 统计瓦片 / cs_profile_hist 分布直方图),
 * 字模缺失时回落内置默认,不阻断渲染。
 */
export default function DataProfileCard({ columns, rows, title }: Props) {
  const { t } = useTranslation('data');
  const kpiCfg = useVisConfig('card_profile_stat', DEFAULT_KPI);
  const histCfg = useVisConfig('cs_profile_hist', DEFAULT_HIST);
  const profiles = useMemo(
    () => profileTable(columns, rows, kpiCfg.topN, kpiCfg.bins),
    [columns, rows, kpiCfg.topN, kpiCfg.bins]);

  return (
    <div className="my-3 rounded-lg border bg-card overflow-hidden">
      <div className="flex items-center gap-2 border-b bg-muted/50 px-3 py-2">
        <BarChart3 className="h-4 w-4 text-primary" />
        <span className="text-sm font-semibold">{title || t('profile.title')}</span>
        <Badge variant="outline" className="text-[10px] font-normal">
          {t('profile.shape', { rows: rows.length, cols: columns.length })}
        </Badge>
      </div>
      <div className="grid grid-cols-1 gap-2 p-3 sm:grid-cols-2 xl:grid-cols-3">
        {profiles.map(p => (
          <div key={p.name} className="rounded-lg border bg-background/40 p-2.5">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs font-medium" title={p.name}>{p.name}</span>
              <Badge variant="secondary" className="shrink-0 text-[10px] font-normal">{t(KIND_LABEL_KEYS[p.kind])}</Badge>
            </div>
            <div className="mt-2 flex gap-1.5">
              <StatTile label={t('profile.missingRate')} value={`${(p.missingRate * 100).toFixed(1)}%`} cfg={kpiCfg} />
              <StatTile label={t('profile.nonNullUnique')} value={`${p.count} / ${p.uniqueCount}`} cfg={kpiCfg} />
              {p.kind === 'number' ? (
                <StatTile label={t('profile.mean')} value={Number(p.mean ?? 0).toLocaleString()} cfg={kpiCfg} />
              ) : (
                <StatTile label={t('profile.topValue')} value={p.topValues?.[0]?.label ?? '-'} cfg={kpiCfg} />
              )}
            </div>
            {p.kind === 'number' && (
              <div className="mt-1.5 flex justify-between text-[10px]" style={{ color: kpiCfg.labelColor }}>
                <span>min {p.min?.toLocaleString()}</span>
                <span>max {p.max?.toLocaleString()}</span>
              </div>
            )}
            {p.histogram && p.histogram.length > 0 && (
              <div className="mt-1.5">
                <MiniBars bins={p.histogram} colorScheme={histCfg.colorScheme} radius={histCfg.borderRadius ?? 2} />
              </div>
            )}
            {p.topValues && p.kind !== 'category' && (
              <div className="mt-1 truncate text-[10px]" style={{ color: kpiCfg.labelColor }}>
                {p.topValues.map(tv => `${tv.label}(${tv.count})`).join(t('profile.listSeparator'))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
