import { useState, useCallback, useEffect } from 'react';
import { Link, X, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

export interface CrossFilter {
  id: string;
  sourceChartId: number;
  column: string;
  value: any;
  operator: 'eq' | 'neq' | 'contains' | 'gt' | 'lt' | 'between';
}

interface DashboardCrossFilterProps {
  filters: CrossFilter[];
  onFilterChange: (filters: CrossFilter[]) => void;
  charts: any[];
}

export default function DashboardCrossFilter({ filters, onFilterChange, charts }: DashboardCrossFilterProps) {
  const [activeFilters, setActiveFilters] = useState<CrossFilter[]>(filters);

  useEffect(() => {
    setActiveFilters(filters);
  }, [filters]);

  const removeFilter = useCallback((filterId: string) => {
    const newFilters = activeFilters.filter(f => f.id !== filterId);
    setActiveFilters(newFilters);
    onFilterChange(newFilters);
  }, [activeFilters, onFilterChange]);

  const clearAllFilters = useCallback(() => {
    setActiveFilters([]);
    onFilterChange([]);
  }, [onFilterChange]);

  const getChartName = useCallback((chartId: number) => {
    const chart = charts.find(c => c.id === chartId);
    return chart?.name || `图表 ${chartId}`;
  }, [charts]);

  const getFilterLabel = useCallback((filter: CrossFilter) => {
    const operatorMap = {
      eq: '=',
      neq: '≠',
      contains: '包含',
      gt: '>',
      lt: '<',
      between: '介于',
    };

    const operator = operatorMap[filter.operator] || filter.operator;
    const value = Array.isArray(filter.value)
      ? `${filter.value[0]} ~ ${filter.value[1]}`
      : String(filter.value);

    return `${filter.column} ${operator} ${value}`;
  }, []);

  if (activeFilters.length === 0) {
    return null;
  }

  return (
    <div className="flex items-center gap-2 px-4 py-2 bg-primary/5 border-b border-primary/20 flex-shrink-0">
      <Link className="h-4 w-4 text-primary" />
      <span className="text-sm font-medium text-primary">跨图表筛选</span>

      <div className="flex-1 flex gap-2 flex-wrap items-center">
        {activeFilters.map(filter => (
          <Tooltip key={filter.id}>
            <TooltipTrigger asChild>
              <Badge variant="default" className="gap-1 pr-1">
                <span>{getFilterLabel(filter)}</span>
                <button
                  onClick={() => removeFilter(filter.id)}
                  className="ml-1 rounded-full hover:bg-primary-foreground/20 p-0.5"
                >
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            </TooltipTrigger>
            <TooltipContent>来源: {getChartName(filter.sourceChartId)}</TooltipContent>
          </Tooltip>
        ))}
      </div>

      <Button
        variant="ghost"
        size="sm"
        onClick={clearAllFilters}
        className="h-8 text-xs text-primary"
      >
        <Trash2 className="h-3 w-3 mr-1" />
        清除全部
      </Button>
    </div>
  );
}

// Utility function to create a cross filter from chart click event
export function createCrossFilterFromClick(
  chartId: number,
  columnName: string,
  value: any,
  operator: CrossFilter['operator'] = 'eq'
): CrossFilter {
  return {
    id: `cf_${chartId}_${columnName}_${Date.now()}`,
    sourceChartId: chartId,
    column: columnName,
    value,
    operator,
  };
}

// Utility function to apply cross filters to data
export function applyCrossFilters(
  data: { columns: string[]; rows: any[] },
  filters: CrossFilter[]
): { columns: string[]; rows: any[] } {
  if (filters.length === 0) return data;

  const filteredRows = data.rows.filter(row => {
    return filters.every(filter => {
      const cellValue = row[filter.column];
      if (cellValue === undefined || cellValue === null) return false;

      switch (filter.operator) {
        case 'eq':
          return String(cellValue) === String(filter.value);
        case 'neq':
          return String(cellValue) !== String(filter.value);
        case 'contains':
          return String(cellValue).toLowerCase().includes(String(filter.value).toLowerCase());
        case 'gt':
          return Number(cellValue) > Number(filter.value);
        case 'lt':
          return Number(cellValue) < Number(filter.value);
        case 'between':
          const [min, max] = filter.value;
          const numValue = Number(cellValue);
          return numValue >= Number(min) && numValue <= Number(max);
        default:
          return true;
      }
    });
  });

  return {
    columns: data.columns,
    rows: filteredRows,
  };
}

// Hook for managing cross filters
export function useCrossFilters() {
  const [filters, setFilters] = useState<CrossFilter[]>([]);

  const addFilter = useCallback((filter: CrossFilter) => {
    setFilters(prev => [...prev, filter]);
  }, []);

  const removeFilter = useCallback((filterId: string) => {
    setFilters(prev => prev.filter(f => f.id !== filterId));
  }, []);

  const clearFilters = useCallback(() => {
    setFilters([]);
  }, []);

  const toggleFilter = useCallback((filter: CrossFilter) => {
    setFilters(prev => {
      const exists = prev.some(f => f.id === filter.id);
      if (exists) {
        return prev.filter(f => f.id !== filter.id);
      } else {
        return [...prev, filter];
      }
    });
  }, []);

  return {
    filters,
    addFilter,
    removeFilter,
    clearFilters,
    toggleFilter,
    setFilters,
  };
}
