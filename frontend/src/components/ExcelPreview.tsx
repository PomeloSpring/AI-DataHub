import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Loader2, RefreshCw, Save, Undo2 } from 'lucide-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import client from '@/api/client';
import DataGrid from '@/components/DataGrid';

interface Props {
  /** 会话工作区相对路径(xlsx) */
  path: string;
  conversationId: number;
  filename: string;
}

interface SheetData {
  sheets: string[];
  active: string;
  columns: string[];
  rows: Record<string, any>[];
  row_count: number;
  truncated: boolean;
}

/**
 * Excel 在线预览与编辑:xlsx 多 sheet 切换(首行作表头)、双击单元格编辑、
 * 保存写回会话工作区(**生成新版本文件,原文件保留**;openpyxl 重写可能丢失图表等特性,原稿即回退)。
 * 单元格修改按工作表聚合为脏改集,支持逐表保存/放弃。
 */
export default function ExcelPreview({ path, conversationId, filename }: Props) {
  const [currentPath, setCurrentPath] = useState(path);
  const [data, setData] = useState<SheetData | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [dirtyCount, setDirtyCount] = useState(0);
  // 脏改集按工作表分桶:key = `${Excel行号}:${列号1-based}` → 新值(原文)
  const dirtyRef = useRef<Record<string, Map<string, string>>>({});

  const applyDirty = (d: SheetData): SheetData => {
    const m = dirtyRef.current[d.active];
    if (!m || m.size === 0) return d;
    return {
      ...d,
      rows: d.rows.map(r => {
        const excelRow = r.__row as number;
        let next = r;
        m.forEach((value, key) => {
          const [rowNo, colNo] = key.split(':').map(Number);
          if (rowNo !== excelRow) return;
          const col = d.columns[colNo - 1];
          if (col) next = { ...next, [col]: value };
        });
        return next;
      }),
    };
  };

  const load = useCallback(async (sheet?: string) => {
    setLoading(true);
    setError('');
    try {
      const { data: raw } = await client.get('/chat/session-file/xlsx', {
        params: { conversation_id: conversationId, path: currentPath, sheet: sheet || '' },
      });
      const d: SheetData = {
        ...raw,
        rows: (raw.rows || []).map((r: Record<string, any>, i: number) => ({ ...r, __row: i + 2 })),
      };
      const withDirty = applyDirty(d);
      setData(withDirty);
      setDirtyCount(dirtyRef.current[withDirty.active]?.size || 0);
    } catch (e: any) {
      setData(null);
      setError(e?.response?.data?.detail || 'Excel 预览加载失败,请稍后重试');
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, currentPath]);

  useEffect(() => { void load(); }, [load]);

  const onEdit = (row: Record<string, any>, column: string, value: string) => {
    if (!data) return;
    const colNo = data.columns.indexOf(column) + 1;
    if (!colNo) return;
    const key = `${row.__row}:${colNo}`;
    const m = dirtyRef.current[data.active] || (dirtyRef.current[data.active] = new Map());
    m.set(key, value);
    setData(prev => (prev ? {
      ...prev,
      rows: prev.rows.map(r => (r.__row === row.__row ? { ...r, [column]: value } : r)),
    } : prev));
    setDirtyCount(m.size);
  };

  const discard = () => {
    if (data) dirtyRef.current[data.active]?.clear();
    setDirtyCount(0);
    void load(data?.active);
  };

  const save = async () => {
    if (!data) return;
    const m = dirtyRef.current[data.active];
    if (!m || m.size === 0) return;
    setSaving(true);
    try {
      const changes = [...m.entries()].map(([key, value]) => {
        const [row, col] = key.split(':').map(Number);
        return { row, col, value };
      });
      const { data: resp } = await client.post('/chat/session-file/xlsx/save', {
        conversation_id: conversationId,
        path: currentPath,
        sheet: data.active,
        changes,
      });
      toast.success(`已保存为 ${resp.filename}（原文件保留）`);
      m.clear();
      setDirtyCount(0);
      setCurrentPath(resp.path); // 后续编辑/预览均落在新版本文件上
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败,请稍后重试');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-2">
      {/* 工作表切换 + 编辑状态条 */}
      <div className="flex flex-wrap items-center gap-1.5">
        {data?.sheets.map(s => (
          <button
            key={s}
            onClick={() => { if (s !== data.active) void load(s); }}
            className={`rounded-md border px-2.5 py-1 text-xs transition-colors ${s === data.active ? 'border-primary bg-primary/10 text-primary font-medium' : 'bg-card text-muted-foreground hover:bg-muted'}`}
          >
            {s}
          </button>
        ))}
        {data?.truncated && (
          <Badge variant="outline" className="text-[10px] font-normal">
            <AlertTriangle className="h-3 w-3 mr-1" />仅预览前 {data.row_count} 行
          </Badge>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {dirtyCount > 0 && (
            <span className="text-xs text-primary">{dirtyCount} 处未保存</span>
          )}
          <Button variant="outline" size="sm" className="h-7 text-xs" onClick={discard} disabled={dirtyCount === 0 || saving}>
            <Undo2 className="h-3 w-3 mr-1" />放弃修改
          </Button>
          <Button size="sm" className="h-7 text-xs" onClick={() => void save()} disabled={dirtyCount === 0 || saving}>
            {saving ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Save className="h-3 w-3 mr-1" />}
            保存为新文件
          </Button>
          <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => void load(data?.active)} disabled={loading}>
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          </Button>
        </div>
      </div>

      {error ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive" role="alert">
          {error}
        </div>
      ) : loading && !data ? (
        <div className="flex h-40 items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />解析 Excel 中…
        </div>
      ) : data ? (
        <DataGrid
          columns={data.columns}
          rows={data.rows}
          height={360}
          filename={filename.replace(/\.[^.]+$/, '')}
          emptyText="该工作表没有数据"
          editable
          onEdit={onEdit}
        />
      ) : null}

      <div className="text-[10px] text-muted-foreground">
        编辑后「保存为新文件」写回会话工作区（{currentPath.split('/').pop()} 的新版本），原文件保留;
        按原单元格类型转换(数字/布尔保持类型,清空即置空)。
      </div>
    </div>
  );
}
