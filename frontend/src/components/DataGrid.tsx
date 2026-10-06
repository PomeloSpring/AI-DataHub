import { useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, Copy, Download, Search } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useVisConfig } from '@/lib/visStyle';
import {
  filterRows, formatCell, sortRows, toCsv, toTsv,
} from '../lib/dataGrid';

interface Props {
  columns: string[];
  rows: Record<string, any>[];
  /** 滚动视口高度(px),默认 400 */
  height?: number;
  /** 导出文件名前缀(不含扩展名) */
  filename?: string;
  emptyText?: string;
  /** 可编辑: 双击单元格进入编辑,Enter/失焦提交、Esc 取消 */
  editable?: boolean;
  onEdit?: (row: Record<string, any>, column: string, value: string) => void;
}

// 字模缺失时的内置默认(与 seed vis_analysis_migration_v2.sql 同值);
// rowHeight 同时驱动虚拟滚动行高(字模 style_config 可调)
const DEFAULT_GRID = {
  headerBg: 'hsl(var(--muted) / 0.6)',
  zebraBg: 'hsl(var(--muted) / 0.25)',
  rowHeight: 30,
  borderRadius: 8,
};
const OVERSCAN = 8;

/**
 * 统一数据网格:查询结果表 / CSV 预览 / 文件面板共用。
 * 表头点击排序(数值感知,空值恒排最后)、全字段关键字过滤、
 * 复制 TSV、导出 CSV(带 BOM);固定行高虚拟滚动,万行级流畅。
 */
export default function DataGrid({ columns, rows, height = 400, filename = 'data', emptyText, editable, onEdit }: Props) {
  const gridCfg = useVisConfig('cs_table_grid', DEFAULT_GRID);
  const rowHeight = Math.max(18, Number(gridCfg.rowHeight) || 30);
  const [keyword, setKeyword] = useState('');
  const [sort, setSort] = useState<{ col: string; dir: 'asc' | 'desc' } | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [editing, setEditing] = useState<{ row: Record<string, any>; col: string } | null>(null);
  const [draft, setDraft] = useState('');

  const view = useMemo(() => {
    const filtered = filterRows(rows, columns, keyword);
    return sort ? sortRows(filtered, sort.col, sort.dir) : filtered;
  }, [rows, columns, keyword, sort]);

  const gridTemplate = `repeat(${Math.max(columns.length, 1)}, minmax(120px, 1fr))`;
  const viewportRows = Math.ceil(height / rowHeight);
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - OVERSCAN);
  const end = Math.min(view.length, start + viewportRows + OVERSCAN * 2);
  const slice = view.slice(start, end);

  const toggleSort = (col: string) => {
    setSort(prev => {
      if (!prev || prev.col !== col) return { col, dir: 'asc' };
      if (prev.dir === 'asc') return { col, dir: 'desc' };
      return null;
    });
  };

  const commitEdit = () => {
    if (editing && onEdit) onEdit(editing.row, editing.col, draft);
    setEditing(null);
  };

  const copyAll = async () => {
    try {
      await navigator.clipboard.writeText(toTsv(columns, view));
      toast.success(`已复制 ${view.length} 行(TSV,可直接粘贴到表格软件)`);
    } catch {
      toast.error('复制失败,请手动选择内容复制');
    }
  };

  const exportCsv = () => {
    const blob = new Blob([toCsv(columns, view)], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${filename}_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="overflow-hidden border bg-card"
      style={{ borderRadius: gridCfg.borderRadius }}>
      {/* 工具栏:过滤 + 行数 + 复制/导出 */}
      <div className="flex items-center gap-2 px-3 py-2 border-b bg-muted/40">
        <div className="relative flex-1 max-w-[260px]">
          <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            placeholder="过滤所有列…"
            className="h-7 pl-7 text-xs"
          />
        </div>
        <span className="text-xs text-muted-foreground whitespace-nowrap">
          {view.length === rows.length ? `${rows.length} 行` : `${view.length} / ${rows.length} 行`}
          {sort && ` · 按 ${sort.col} ${sort.dir === 'asc' ? '升序' : '降序'}`}
          {editable && ' · 双击单元格可编辑'}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <Button variant="outline" size="sm" className="h-7 text-xs" onClick={copyAll} disabled={view.length === 0}>
            <Copy className="h-3 w-3 mr-1" />复制
          </Button>
          <Button variant="outline" size="sm" className="h-7 text-xs" onClick={exportCsv} disabled={view.length === 0}>
            <Download className="h-3 w-3 mr-1" />导出 CSV
          </Button>
        </div>
      </div>

      {/* 网格:表头 + 虚拟滚动行 */}
      <div
        className="overflow-auto"
        style={{ height }}
        onScroll={e => setScrollTop((e.target as HTMLDivElement).scrollTop)}
      >
        {columns.length === 0 || view.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            {emptyText || (rows.length === 0 ? '暂无数据' : '没有匹配的行,试试换个关键字')}
          </div>
        ) : (
          <div style={{ minWidth: columns.length * 120 }}>
            <div
              className="sticky top-0 z-10 grid border-b text-xs font-medium text-muted-foreground"
              style={{ gridTemplateColumns: gridTemplate, background: gridCfg.headerBg }}
            >
              {columns.map(c => (
                <button
                  key={c}
                  onClick={() => toggleSort(c)}
                  className="flex h-8 items-center gap-1 border-r px-3 text-left hover:bg-muted"
                  title={`按 ${c} 排序`}
                >
                  <span className="truncate">{c}</span>
                  {sort?.col === c && (sort.dir === 'asc'
                    ? <ArrowUp className="h-3 w-3 shrink-0 text-primary" />
                    : <ArrowDown className="h-3 w-3 shrink-0 text-primary" />)}
                </button>
              ))}
            </div>
            {/* 虚拟滚动:上下占位 + 仅渲染可视窗口 */}
            <div style={{ height: start * rowHeight }} />
            {slice.map((row, i) => (
              <div
                key={start + i}
                className="grid border-b text-xs hover:bg-muted/40"
                style={{
                  gridTemplateColumns: gridTemplate,
                  height: rowHeight,
                  background: (start + i) % 2 === 1 ? gridCfg.zebraBg : undefined,
                }}
              >
                {columns.map(c => (
                  editing && editing.row === row && editing.col === c ? (
                    <input
                      key={c}
                      autoFocus
                      value={draft}
                      onChange={e => setDraft(e.target.value)}
                      onBlur={commitEdit}
                      onKeyDown={e => {
                        if (e.key === 'Enter') commitEdit();
                        if (e.key === 'Escape') setEditing(null);
                      }}
                      className="h-full w-full border-0 bg-background px-2 text-xs outline-none ring-1 ring-primary"
                    />
                  ) : (
                    <div
                      key={c}
                      className={`flex items-center border-r px-3 truncate ${editable ? 'cursor-text hover:bg-primary/5' : ''}`}
                      title={formatCell(row[c])}
                      onDoubleClick={() => {
                        if (editable && onEdit) {
                          setEditing({ row, col: c });
                          setDraft(formatCell(row[c]));
                        }
                      }}
                    >
                      {formatCell(row[c])}
                    </div>
                  )
                ))}
              </div>
            ))}
            <div style={{ height: Math.max(0, (view.length - end) * rowHeight) }} />
          </div>
        )}
      </div>
    </div>
  );
}
