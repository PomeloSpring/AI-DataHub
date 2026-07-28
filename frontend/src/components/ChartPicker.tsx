import { useState, useMemo, useEffect } from 'react';
import { Maximize2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import DashboardChart from './DashboardChart';

interface Props {
  data: {
    columns: string[];
    rows: any[];
  };
  defaultType?: string;
}

const CHART_TYPE_MAP: Record<string, string> = {
  column: 'bar', bar: 'bar', line: 'line', pie: 'pie',
  sankey: 'sankey', funnel: 'funnel', chord: 'chord',
  calendar_heatmap: 'calendar_heatmap', big_number_trend: 'big_number_trend',
  boxplot: 'boxplot', bubble: 'bubble',
  timeseries_table: 'timeseries_table', timeseries_area: 'timeseries_area',
  timeseries_bar: 'timeseries_bar', timeseries_line: 'timeseries_line',
  timeseries_percent: 'timeseries_percent', timeseries_pivot: 'timeseries_pivot',
  tree: 'tree', treemap: 'treemap', waterfall: 'waterfall',
};

function resolveChartType(llmType?: string): string {
  if (!llmType || llmType === 'table') return 'bar';
  return CHART_TYPE_MAP[llmType] || 'bar';
}

export default function ChartPicker({ data, defaultType }: Props) {
  const columns = data?.columns || [];
  const rows = data?.rows || [];

  const [chartType, setChartType] = useState(() => resolveChartType(defaultType));
  const [xCol, setXCol] = useState('');
  const [yCol, setYCol] = useState('');
  const [seriesCol, setSeriesCol] = useState('');
  const [selectedSeries, setSelectedSeries] = useState<string[]>([]);
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    if (defaultType) setChartType(resolveChartType(defaultType));
  }, [defaultType]);

  const numericCols = useMemo(
    () => columns.filter((c: string) => rows.length > 0 && typeof rows[0][c] === 'number'),
    [columns, rows],
  );
  const stringCols = useMemo(
    () => columns.filter((c: string) => rows.length > 0 && typeof rows[0][c] === 'string'),
    [columns, rows],
  );

  // Auto-detect columns
  useEffect(() => {
    if (!yCol && numericCols.length > 0) setYCol(numericCols[0]);

    if (!xCol && stringCols.length > 0) {
      const cardinalities = stringCols.map(c => ({
        col: c,
        unique: new Set(rows.map(r => r[c])).size,
      }));

      const sorted = [...cardinalities].sort((a, b) => b.unique - a.unique);
      setXCol(sorted[0].col);

      if (!seriesCol && cardinalities.length >= 2) {
        const xVal = sorted[0].col;
        const seriesCandidate = cardinalities
          .filter(c => c.col !== xVal && c.unique >= 2 && c.unique <= 20)
          .sort((a, b) => a.unique - b.unique)[0];
        if (seriesCandidate) setSeriesCol(seriesCandidate.col);
      }
    }
  }, [stringCols, numericCols, xCol, yCol, seriesCol, rows]);

  const x = xCol || stringCols[0] || columns[0] || '';
  const y = yCol || numericCols[0] || columns[1] || '';

  const seriesValues = useMemo(() => {
    if (!seriesCol) return [];
    return [...new Set(rows.map(r => String(r[seriesCol])))].sort();
  }, [rows, seriesCol]);

  const filteredRows = useMemo(() => {
    let result = rows;
    if (seriesCol && selectedSeries.length > 0) {
      const set = new Set(selectedSeries);
      result = result.filter(r => set.has(String(r[seriesCol])));
    }
    return result;
  }, [rows, seriesCol, selectedSeries]);

  const hasSeries = seriesCol && seriesValues.length > 1;

  const chartOptions = [
    { value: 'bar', label: '📊 柱状图' },
    { value: 'line', label: '📈 折线图' },
    { value: 'pie', label: '🥧 饼图' },
    { value: 'funnel', label: '🔽 漏斗图' },
    { value: 'timeseries_line', label: '📈 时间序列折线图' },
    { value: 'timeseries_bar', label: '📶 时间序列柱状图' },
    { value: 'timeseries_area', label: '📊 时间序列面积图' },
  ];

  if (columns.length < 2 || rows.length === 0) return null;

  const config = useMemo(() => ({ xCol: x, yCol: y, groupCol: seriesCol || undefined }), [x, y, seriesCol]);

  const controlsBlock = (
    <div className="space-y-3">
      <div className="flex gap-2 items-center flex-wrap">
        <Select value={chartType} onValueChange={setChartType}>
          <SelectTrigger className="w-[140px] h-8">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {chartOptions.map(opt => (
              <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <span className="text-xs text-muted-foreground">X:</span>
        <Select value={x} onValueChange={setXCol}>
          <SelectTrigger className="w-[120px] h-8">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {columns.map((c: string) => (
              <SelectItem key={c} value={c}>{c}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <span className="text-xs text-muted-foreground">Y:</span>
        <Select value={y} onValueChange={setYCol}>
          <SelectTrigger className="w-[120px] h-8">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {numericCols.map((c: string) => (
              <SelectItem key={c} value={c}>{c}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <span className="text-xs text-muted-foreground">分组:</span>
        <Select value={seriesCol || '__none__'} onValueChange={(v) => { setSeriesCol(v === '__none__' ? '' : v); setSelectedSeries([]); }}>
          <SelectTrigger className="w-[120px] h-8">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__none__">无</SelectItem>
            {stringCols.filter(c => c !== x).map((c: string) => (
              <SelectItem key={c} value={c}>{c}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {hasSeries && (
        <div className="flex gap-1 flex-wrap items-center">
          <span className="text-xs text-muted-foreground mr-1">筛选:</span>
          <Badge
            variant={selectedSeries.length === 0 ? 'default' : 'outline'}
            className="cursor-pointer"
            onClick={() => setSelectedSeries([])}
          >
            全部
          </Badge>
          {seriesValues.map(sv => {
            const active = selectedSeries.length === 0 || selectedSeries.includes(sv);
            return (
              <Badge
                key={sv}
                variant={active ? 'default' : 'outline'}
                className={`cursor-pointer ${active ? '' : 'opacity-50'}`}
                onClick={() => {
                  if (selectedSeries.length === 0) setSelectedSeries([sv]);
                  else if (selectedSeries.includes(sv)) setSelectedSeries(selectedSeries.filter(s => s !== sv));
                  else setSelectedSeries([...selectedSeries, sv]);
                }}
              >
                {sv}
              </Badge>
            );
          })}
        </div>
      )}
    </div>
  );

  return (
    <div className="mt-3">
      {controlsBlock}
      <div className="relative mt-3">
        <div className="h-[360px]">
          <DashboardChart chartType={chartType} data={{ columns, rows: filteredRows }} config={config} />
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="absolute top-1 right-1"
          onClick={() => setFullscreen(true)}
        >
          <Maximize2 className="h-4 w-4" />
        </Button>
      </div>

      <Dialog open={fullscreen} onOpenChange={setFullscreen}>
        <DialogContent className="max-w-[95vw] max-h-[95vh] h-[95vh]">
          <div className="flex flex-col h-full">
            {controlsBlock}
            <div className="flex-1 mt-3 min-h-0">
              <DashboardChart chartType={chartType} data={{ columns, rows: filteredRows }} config={config} />
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
