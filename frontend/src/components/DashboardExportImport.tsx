import { useState, useCallback, useRef } from 'react';
import { toast } from '@/lib/toast';
import { Download, Upload, Copy, Check, FileJson } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { ScrollArea } from '@/components/ui/scroll-area';
import { TEMPLATE_DEMO_KEY } from '@/lib/dashboardDemo';

interface DashboardExportImportProps {
  dashboard: any;
  onImport: (data: any) => void | Promise<void>;
}

export function exportChart(chart: any) {
  const semantic = chart.query_source === 'semantic' || !!chart.semantic_query;
  const keep = (value: any, drop: (key: string) => boolean): any => Array.isArray(value)
    ? value.map(v => keep(v, drop)) : value && typeof value === 'object'
      ? Object.fromEntries(Object.entries(value).filter(([key]) => !drop(key)).map(([key, val]) => [key, keep(val, drop)])) : value;
  // 语义图：config 里任何 SQL 痕迹一律带走（护栏 §7）
  const stripSql = (value: any) => keep(value, key => /sql|data_cache|previewData|demoSql/i.test(key));
  // raw 图：sql_query 本身已是导出字段，config 只剥重复/内部键；
  // countSql 等用户显式口径必须保留，剥掉会让导入后的服务端分页静默改走自动包装 COUNT(*)（行为漂移）
  const stripInternal = (value: any) => keep(value, key => /^(sql|demoSql|data_cache|previewData)$/i.test(key));
  // 示例数据（模板导入且未绑源）随导出带走，分享/备份后导入仍能看到效果；
  // 真实查询结果属数据行，护栏 §7 禁止离开本实例，因此不写入导出体。
  const isDemo = chart.config?.[TEMPLATE_DEMO_KEY] === true;
  let sample: any;
  if (isDemo) {
    const raw = typeof chart.data_cache === 'string' ? (() => { try { return JSON.parse(chart.data_cache); } catch { return null; } })() : chart.data_cache;
    if (Array.isArray(raw?.columns) && Array.isArray(raw?.rows)) sample = raw;
  }
  return { name: chart.name, chart_type: chart.chart_type,
    ...(semantic ? { semantic_query: chart.semantic_query, query_source: 'semantic' } : { sql_query: chart.sql_query, query_source: chart.query_source || 'raw_sql' }),
    ...(sample ? { sample } : {}),
    config: semantic ? stripSql(chart.config) : stripInternal(chart.config), position: chart.position,
    source_type: chart.source_type, source_id: chart.source_id };
}

export default function DashboardExportImport({ dashboard, onImport }: DashboardExportImportProps) {
  const [exportOpen, setExportOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [importData, setImportData] = useState('');
  const [importing, setImporting] = useState(false);
  const importLock = useRef(false);

  const exportData = useCallback(() => {
    if (!dashboard) return null;

    const exportObj = {
      version: '1.1',
      exported_at: new Date().toISOString(),
      dashboard: {
        name: dashboard.name,
        description: dashboard.description,
        filters: dashboard.filters,
        carousel_interval: dashboard.carousel_interval,
        params: dashboard.params,
        page_params: dashboard.page_params,
        charts: dashboard.charts.map(exportChart),
      },
    };

    return JSON.stringify(exportObj, null, 2);
  }, [dashboard]);

  const handleExportJSON = useCallback(() => {
    const data = exportData();
    if (!data) return;

    const blob = new Blob([data], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `dashboard_${dashboard?.name || 'export'}_${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast.success('导出成功');
    setExportOpen(false);
  }, [dashboard, exportData]);

  const handleCopyJSON = useCallback(async () => {
    const data = exportData();
    if (!data) return;

    try {
      await navigator.clipboard.writeText(data);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      toast.success('已复制到剪贴板');
    } catch {
      toast.error('复制失败，请手动复制');
    }
  }, [exportData]);

  const handleImport = useCallback(async () => {
    if (importLock.current) return;
    importLock.current = true; setImporting(true);
    try {
      const parsed = JSON.parse(importData);
      if (!parsed.dashboard || !Array.isArray(parsed.dashboard.charts)) {
        toast.error('无效的仪表盘数据格式');
        return;
      }

      const { name, description, charts, filters, carousel_interval, params, page_params } = parsed.dashboard;

      await onImport({
        name: name ? `${name} (导入)` : '导入的仪表盘',
        description,
        filters,
        carousel_interval,
        charts: charts.map(exportChart), params, page_params,
      });
      toast.success('导入成功');
      setImportOpen(false);
      setImportData('');
    } catch {
      toast.error('导入未完成，请检查 JSON 或服务后重试；已保留输入内容');
    } finally { importLock.current = false; setImporting(false); }
  }, [importData, onImport]);

  const handleFileUpload = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      const content = e.target?.result as string;
      setImportData(content);
    };
    reader.readAsText(file);
  }, []);

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm">
            <Download className="h-4 w-4 mr-2" />
            导入/导出
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem disabled={!dashboard} onClick={() => setExportOpen(true)}>
            <Upload className="h-4 w-4 mr-2" />
            导出仪表盘
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => setImportOpen(true)}>
            <Download className="h-4 w-4 mr-2" />
            导入仪表盘
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Export Modal */}
      <Dialog open={exportOpen} onOpenChange={setExportOpen}>
        <DialogContent className="max-w-[600px]">
          <DialogHeader>
            <DialogTitle>导出仪表盘</DialogTitle>
          </DialogHeader>

          <p className="text-sm text-muted-foreground">
            将仪表盘配置导出为JSON文件，可用于备份或分享给其他用户。
          </p>

          <ScrollArea className="h-[300px] bg-muted p-4 rounded-lg">
            <pre className="text-xs whitespace-pre-wrap break-all">
              {exportData()}
            </pre>
          </ScrollArea>

          <div className="flex gap-2">
            <Button onClick={handleExportJSON}>
              <Download className="h-4 w-4 mr-2" />
              下载JSON文件
            </Button>
            <Button variant="outline" onClick={handleCopyJSON}>
              {copied ? <Check className="h-4 w-4 mr-2" /> : <Copy className="h-4 w-4 mr-2" />}
              {copied ? '已复制' : '复制到剪贴板'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Import Modal */}
      <Dialog open={importOpen} onOpenChange={(open) => {
        if (importing) return;
        if (!open) setImportData('');
        setImportOpen(open);
      }}>
        <DialogContent className="max-w-[600px]">
          <DialogHeader>
            <DialogTitle>导入仪表盘</DialogTitle>
          </DialogHeader>

          <p className="text-sm text-muted-foreground">
            从JSON文件导入仪表盘配置。支持从其他AI-DataHub实例导出的配置文件。
          </p>

          <div className="space-y-4">
            <div className="border-2 border-dashed rounded-lg p-8 text-center">
              <FileJson className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
              <p className="text-sm mb-2">点击或拖拽JSON文件到此区域</p>
              <p className="text-xs text-muted-foreground mb-4">支持 .json 格式的仪表盘配置文件</p>
              <input
                type="file"
                accept=".json"
                onChange={handleFileUpload}
                className="hidden"
                id="file-upload"
              />
              <label htmlFor="file-upload">
                <Button variant="outline" size="sm" asChild>
                  <span>选择文件</span>
                </Button>
              </label>
            </div>

            <div className="space-y-2">
              <Label>或粘贴JSON内容：</Label>
              <Textarea
                value={importData}
                onChange={(e) => setImportData(e.target.value)}
                placeholder="粘贴仪表盘JSON配置..."
                rows={8}
                className="font-mono text-xs"
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" disabled={importing} onClick={() => { setImportOpen(false); setImportData(''); }}>
              取消
            </Button>
            <Button onClick={handleImport} disabled={importing || !importData.trim()}>
              {importing ? '导入中…' : '导入'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
