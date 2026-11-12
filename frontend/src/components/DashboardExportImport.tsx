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
  // 语义图：config 里任何 SQL 痕迹一律剥掉（护栏 §7）
  const stripSql = (value: any) => keep(value, key => /sql|data_cache|previewData|demoSql/i.test(key));
  // 图表不再携带口径（2026-11-06 拍板）：config 里的 SQL 痕迹一律剥。
  // 同时剥掉已退役的分页旋钮：enableServerPagination / pageLimit / maxRows ——
  // 表值图现在恒定走服务端真分页（只剩 pageSize 一个旋钮），带走旧键等于把
  // “旧口径还能用”的空承诺复制给导入方（no-silent-degradation）。
  const stripInternal = (value: any) => keep(value, key =>
    /^(sql|demoSql|countSql|data_cache|previewData|enableServerPagination|pageLimit|maxRows)$/i.test(key));
  // 示例数据（模板导入且未绑源）随导出带走，分享/备份后导入仍能看到效果；
  // 真实查询结果属数据行，护栏 §7 禁止离开本实例，因此不写入导出体。
  const isDemo = chart.config?.[TEMPLATE_DEMO_KEY] === true;
  let sample: any;
  if (isDemo) {
    const raw = typeof chart.data_cache === 'string' ? (() => { try { return JSON.parse(chart.data_cache); } catch { return null; } })() : chart.data_cache;
    if (Array.isArray(raw?.columns) && Array.isArray(raw?.rows)) sample = raw;
  }
  const onDataset = String(chart.source_type) === 'dataset' && !!chart.source_id;
  // 存量手写法图（收编未覆盖）：口径不写进导出体就会静默丢失，因此带走并标
  // needs_migration；导入侧会按占位图落位并显式提醒重新挂数据集（不静默当已收编）。
  const legacySql = !semantic && !onDataset && !!(chart.sql_query || '').trim();
  return {
    name: chart.name, chart_type: chart.chart_type,
    ...(semantic ? { semantic_query: chart.semantic_query, query_source: 'semantic' } : {}),
    // 数据集引用同时给名称（给人看）与 id（同实例回绑）；导入后以服务端角色清单为准
    ...(onDataset ? { dataset_id: chart.source_id, dataset_name: chart.dataset_name || '' } : {}),
    ...(legacySql ? { sql_query: chart.sql_query, needs_migration: true } : {}),
    ...(sample ? { sample } : {}),
    config: semantic ? stripSql(chart.config) : stripInternal(chart.config), position: chart.position,
    source_type: chart.source_type, source_id: chart.source_id,
  };
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
      // 带旧版 SQL 的图（needs_migration）会在服务端被拒，先显式告知再导入：
      // 退成占位图可以接受，静默丢掉口径或笼统报“导入失败”不可接受
      const stale = (charts || []).filter((c: any) => !c.semantic_query
        && String(c.source_type) !== 'dataset' && !(c.dataset_id) && (c.sql_query || '').trim());
      if (stale.length) {
        toast.warning(`${stale.length} 个图表带旧版直写 SQL（该通道已下线）：将按占位图导入，`
          + `请在图表配置里改挂数据集。涉及「${stale.slice(0, 3).map((c: any) => c.name).join('、')}」`,
          { duration: 9000 });
      }

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
    } catch (e: any) {
      // 带因上报（服务端回的是业务原因文案）：“导入未完成”不能只让人猜哪个图卡住了
      toast.error(e?.response?.data?.detail
        ? `导入未完成：${e.response.data.detail}；已保留输入内容`
        : '导入未完成，请检查 JSON 或服务后重试；已保留输入内容');
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
