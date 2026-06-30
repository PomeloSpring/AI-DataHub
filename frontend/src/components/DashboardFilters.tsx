import { useCallback } from 'react';
import { Filter, X, Search } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

export interface FilterConfig {
  id: string;
  name: string;
  type: 'date_range' | 'select' | 'multi_select' | 'search' | 'number_range';
  column: string;
  options?: { label: string; value: string }[];
  defaultValue?: any;
}

interface DashboardFiltersProps {
  filters: FilterConfig[];
  values: Record<string, any>;
  onChange: (filterId: string, value: any) => void;
  onClearAll: () => void;
}

export default function DashboardFilters({ filters, values, onChange, onClearAll }: DashboardFiltersProps) {
  const activeFilterCount = Object.values(values).filter(v =>
    v !== undefined && v !== null && v !== '' && !(Array.isArray(v) && v.length === 0)
  ).length;

  const renderFilter = useCallback((filter: FilterConfig) => {
    const value = values[filter.id];

    switch (filter.type) {
      case 'date_range':
        return (
          <div className="flex gap-1">
            <Input
              type="date"
              value={value?.[0] || ''}
              onChange={(e) => onChange(filter.id, [e.target.value, value?.[1]])}
              className="w-[140px] h-8 text-xs"
            />
            <span className="text-muted-foreground">-</span>
            <Input
              type="date"
              value={value?.[1] || ''}
              onChange={(e) => onChange(filter.id, [value?.[0], e.target.value])}
              className="w-[140px] h-8 text-xs"
            />
          </div>
        );

      case 'select':
        return (
          <Select value={value || ''} onValueChange={(v) => onChange(filter.id, v || undefined)}>
            <SelectTrigger className="w-[160px] h-8">
              <SelectValue placeholder={`选择${filter.name}`} />
            </SelectTrigger>
            <SelectContent>
              {filter.options?.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        );

      case 'multi_select':
        return (
          <Select value={value?.[0] || ''} onValueChange={(v) => onChange(filter.id, v ? [v] : [])}>
            <SelectTrigger className="w-[200px] h-8">
              <SelectValue placeholder={`选择${filter.name}`} />
            </SelectTrigger>
            <SelectContent>
              {filter.options?.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        );

      case 'search':
        return (
          <div className="relative">
            <Search className="absolute left-2 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              value={value || ''}
              onChange={(e) => onChange(filter.id, e.target.value)}
              className="w-[180px] h-8 pl-8"
              placeholder={`搜索${filter.name}`}
            />
          </div>
        );

      case 'number_range':
        return (
          <div className="flex items-center gap-1">
            <Input
              type="number"
              value={value?.[0] || ''}
              onChange={(e) => onChange(filter.id, [e.target.value, value?.[1]])}
              className="w-[80px] h-8"
              placeholder="最小值"
            />
            <span className="text-muted-foreground">-</span>
            <Input
              type="number"
              value={value?.[1] || ''}
              onChange={(e) => onChange(filter.id, [value?.[0], e.target.value])}
              className="w-[80px] h-8"
              placeholder="最大值"
            />
          </div>
        );

      default:
        return null;
    }
  }, [values, onChange]);

  if (filters.length === 0) return null;

  return (
    <div className="flex items-center gap-2 px-4 py-2 bg-muted/30 border-b flex-wrap flex-shrink-0">
      <Filter className="h-4 w-4 text-primary" />
      <span className="text-sm font-medium">筛选器</span>

      {activeFilterCount > 0 && (
        <Badge variant="default" className="text-xs">{activeFilterCount}个已选</Badge>
      )}

      <div className="flex-1 flex gap-3 flex-wrap items-center">
        {filters.map(filter => (
          <div key={filter.id} className="flex items-center gap-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="text-xs text-muted-foreground whitespace-nowrap">
                  {filter.name}:
                </span>
              </TooltipTrigger>
              <TooltipContent>{filter.name}</TooltipContent>
            </Tooltip>
            {renderFilter(filter)}
          </div>
        ))}
      </div>

      {activeFilterCount > 0 && (
        <Button variant="ghost" size="sm" onClick={onClearAll} className="h-8 text-xs">
          <X className="h-3 w-3 mr-1" />
          清除
        </Button>
      )}
    </div>
  );
}

// Default filter configurations
export const DEFAULT_FILTERS: FilterConfig[] = [
  {
    id: 'date_range',
    name: '日期范围',
    type: 'date_range',
    column: 'date',
  },
  {
    id: 'category',
    name: '分类',
    type: 'select',
    column: 'category',
    options: [],
  },
  {
    id: 'search',
    name: '搜索',
    type: 'search',
    column: 'name',
  },
];
