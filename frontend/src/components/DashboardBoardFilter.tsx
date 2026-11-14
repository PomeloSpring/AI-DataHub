import { ListFilter, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import type { BoardFilter } from '@/lib/dashboardCatalog';

// 看板清单的名称 / 标签过滤控件（组内切换条与一级清单页共用同一份外观与判据）。
//
// 为什么收成独立控件而不是标签芯片行：看板一多（如「App 单应用看板」组合下 24 张）
// 芯片行会把筛选区撑成第二行，且"没有标签时整行消失"= 用户以为没有筛选功能
// （看板管理页上一轮已按同样结论从芯片行改成下拉框，这里保持同一口径）。
//
// 这个控件只改"看得见哪几张板"，不参与取数与权限：可见集仍由
// GET /dashboard/groups/visible 服务端 fail-closed 裁决。

const TRIGGER_CLS = 'h-8 shrink-0 gap-1.5 rounded-full bg-muted/60 px-3 text-xs '
  + 'ring-1 ring-border/60 hover:bg-muted';

export function BoardFilterControl({
  tags, value, onChange, matched, total, label,
}: {
  /** 可选标签（业务可读名）：来自当前清单的去重并集，无标签时下拉置灰说明原因 */
  tags: string[];
  value: BoardFilter;
  onChange: (next: BoardFilter) => void;
  /** 命中数 / 总数：筛完剩几个必须当场看得见，不能让用户以为看板凭空少了 */
  matched: number;
  total: number;
  label?: string;
}) {
  const { t } = useTranslation(['dashboard']);
  const triggerLabel = label ?? t('filter.defaultLabel');
  const active = Boolean(value.query.trim() || value.tag);
  const summary = value.tag
    ? `#${value.tag}`
    : value.query.trim() ? `“${value.query.trim()}”` : '';
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" aria-label={triggerLabel} aria-pressed={active}
          title={active ? t('filter.activeTitle', { summary, matched, count: matched }) : triggerLabel}
          className={`${TRIGGER_CLS} inline-flex items-center ${active ? 'font-medium text-primary' : 'text-muted-foreground'}`}>
          <ListFilter className="h-3.5 w-3.5" />
          <span className="max-w-[9rem] truncate">{active ? summary : t('filter.trigger')}</span>
          {active && <span className="tabular-nums text-[11px]">{matched}/{total}</span>}
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 space-y-3">
        <div className="space-y-1.5">
          <label className="text-xs text-muted-foreground" htmlFor="board-filter-name">{t('filter.nameLabel')}</label>
          <Input id="board-filter-name" aria-label={t('filter.nameAria')} placeholder={t('filter.namePlaceholder')}
            className="h-8 text-xs" value={value.query}
            onChange={e => onChange({ ...value, query: e.target.value })} />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs text-muted-foreground" htmlFor="board-filter-tag">{t('filter.tagLabel')}</label>
          <select id="board-filter-tag" aria-label={t('filter.tagAria')} role="combobox"
            className="h-8 w-full rounded-md border bg-background px-2 text-xs disabled:text-muted-foreground"
            disabled={tags.length === 0} value={value.tag}
            onChange={e => onChange({ ...value, tag: e.target.value })}>
            <option value="">{tags.length === 0 ? t('filter.allTagsEmpty') : t('filter.allTags')}</option>
            {tags.map(tg => <option key={tg} value={tg}>#{tg}</option>)}
          </select>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-muted-foreground">{t('filter.hitCount', { matched, total })}</span>
          <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" disabled={!active}
            onClick={() => onChange({ query: '', tag: '' })}>
            <X className="mr-1 h-3.5 w-3.5" />{t('filter.clear')}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
