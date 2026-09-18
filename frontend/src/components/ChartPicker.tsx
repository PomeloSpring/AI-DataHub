import { useState, useMemo, useEffect, useRef } from 'react';
import { Maximize2, BarChart3, Table, Code, Download } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import DashboardChart, { ChartIcon, CHART_TYPES, CHART_TYPE_CATEGORIES } from './DashboardChart';
import * as XLSX from 'xlsx';
import html2canvas from 'html2canvas';

interface Props {
  data: {
    columns: string[];
    rows: any[];
  };
  defaultType?: string;
  /** 生成该数据的查询 SQL(可选);提供时卡片内展示 SQL 视图 */
  sql?: string;
}

// 与看板共用同一套图表类型(系统新增/删减自动同步);排除看板参数控件(非取数图表)
const PICKABLE = CHART_TYPES.filter(t => t.category !== 'widget');
const VALID_TYPES = new Set(PICKABLE.map(t => t.value));

// 历史消息兼容别名(LLM 旧契约/看板外叫法)
const TYPE_ALIASES: Record<string, string> = {
  column: 'bar', table: 'bar',
};

export function resolveChartType(llmType?: string): string {
  if (!llmType || llmType === 'table') return 'bar';
  const alias = TYPE_ALIASES[llmType];
  if (alias && !VALID_TYPES.has(llmType)) return alias;
  return VALID_TYPES.has(llmType) ? llmType : 'bar';
}

export default function ChartPicker({ data, defaultType, sql }: Props) {
  const columns = data?.columns || [];
  const rows = data?.rows || [];
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const tableContainerRef = useRef<HTMLDivElement>(null);

  // 与内置管线结果卡片同构的三视图:图表/明细/SQL(无 SQL 时不展示 SQL 页)
  const [view, setView] = useState<'chart' | 'table' | 'sql'>('chart');
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

  const chartOptions = PICKABLE;

  if (columns.length < 2 || rows.length === 0) return null;

  const config = useMemo(() => ({ xCol: x, yCol: y, groupCol: seriesCol || undefined }), [x, y, seriesCol]);

  const controlsBlock = (
    <div className="space-y-3">
      <div className="flex gap-2 items-center flex-wrap">
        <Select value={chartType} onValueChange={setChartType}>
          <SelectTrigger className="w-[170px] h-8">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="max-h-[70vh]">
            {CHART_TYPE_CATEGORIES.map(cat => {
              const opts = chartOptions.filter(o => o.category === cat.key);
              if (!opts.length) return null;
              return (
                <SelectGroup key={cat.key}>
                  <SelectLabel className="text-[10px] text-muted-foreground">{cat.label}</SelectLabel>
                  {opts.map(opt => (
                    <SelectItem key={opt.value} value={opt.value}>
                      <span className="inline-flex items-center gap-1.5">
                        <ChartIcon name={opt.icon} className="h-3.5 w-3.5 shrink-0" />
                        {opt.label}
                      </span>
                    </SelectItem>
                  ))}
                </SelectGroup>
              );
            })}
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

  // 导出功能
  const exportToExcel = () => {
    const ws = XLSX.utils.json_to_sheet(rows);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Data');
    XLSX.writeFile(wb, `${sql ? 'query_result' : 'data'}.xlsx`);
  };

  const exportToCSV = () => {
    const ws = XLSX.utils.json_to_sheet(rows);
    const csv = XLSX.utils.sheet_to_csv(ws);
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${sql ? 'query_result' : 'data'}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const exportChartToPNG = async () => {
    if (!chartContainerRef.current) return;
    try {
      const canvas = await html2canvas(chartContainerRef.current, {
        backgroundColor: '#ffffff',
        scale: 2,
      });
      const url = canvas.toDataURL('image/png');
      const a = document.createElement('a');
      a.href = url;
      a.download = `${sql ? 'query_chart' : 'chart'}.png`;
      a.click();
    } catch (e) {
      console.error('Export failed:', e);
    }
  };

  return (
    <div className="mt-3">
      <div className="flex items-center justify-between mb-2">
        <Tabs value={view} onValueChange={(v) => setView(v as any)}>
          <TabsList>
            <TabsTrigger value="chart"><BarChart3 className="h-4 w-4 mr-1" />图表</TabsTrigger>
            <TabsTrigger value="table"><Table className="h-4 w-4 mr-1" />明细</TabsTrigger>
            {sql && <TabsTrigger value="sql"><Code className="h-4 w-4 mr-1" />SQL</TabsTrigger>}
          </TabsList>
        </Tabs>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="h-7 text-xs">
              <Download className="h-3 w-3 mr-1" />导出
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-32">
            <DropdownMenuItem onClick={exportToExcel}>Excel</DropdownMenuItem>
            <DropdownMenuItem onClick={exportToCSV}>CSV</DropdownMenuItem>
            <DropdownMenuItem onClick={exportChartToPNG}>图表 PNG</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {view === 'chart' && <>
        {controlsBlock}
        <div className="relative mt-3" ref={chartContainerRef}>
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
      </>}

      {view === 'table' && (
        <div className="overflow-auto max-h-[400px] mt-1" ref={tableContainerRef}>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b bg-muted/50">
                {columns.map((c: string) => (
                  <th key={c} className="h-8 px-3 text-left align-middle font-medium text-muted-foreground">{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 200).map((row: any, i: number) => (
                <tr key={i} className="border-b hover:bg-muted/50">
                  {columns.map((c: string) => (
                    <td key={c} className="px-3 py-1.5">{String(row[c] ?? '')}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {view === 'sql' && sql && (
        <pre className="p-4 bg-muted text-foreground rounded-lg border text-xs leading-relaxed overflow-auto max-h-[400px] font-mono whitespace-pre-wrap">
          {sql}
        </pre>
      )}

      <Dialog open={fullscreen} onOpenChange={setFullscreen}>
        <DialogContent className="max-w-[95vw] max-h-[95vh] h-[95vh]">
          <div className="flex flex-col h-full">
            {controlsBlock}
            <div className="relative flex-1 mt-3 min-h-0">
              <DashboardChart chartType={chartType} data={{ columns, rows: filteredRows }} config={config} />
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
