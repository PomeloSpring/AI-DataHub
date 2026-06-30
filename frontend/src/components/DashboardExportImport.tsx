import { useState, useCallback } from 'react';
import { toast } from 'sonner';
import { Download, Upload, Copy, Check, FileJson } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { ScrollArea } from '@/components/ui/scroll-area';

interface DashboardExportImportProps {
  dashboard: any;
  onImport: (data: any) => void;
}

export default function DashboardExportImport({ dashboard, onImport }: DashboardExportImportProps) {
  const [exportOpen, setExportOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [importData, setImportData] = useState('');

  const exportData = useCallback(() => {
    if (!dashboard) return null;

    const exportObj = {
      version: '1.0',
      exported_at: new Date().toISOString(),
      dashboard: {
        name: dashboard.name,
        description: dashboard.description,
        filters: dashboard.filters,
        carousel_interval: dashboard.carousel_interval,
        charts: dashboard.charts.map((chart: any) => ({
          name: chart.name,
          chart_type: chart.chart_type,
          sql_query: chart.sql_query,
          config: chart.config,
          position: chart.position,
          source_type: chart.source_type,
        })),
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

  const handleImport = useCallback(() => {
    try {
      const parsed = JSON.parse(importData);
      if (!parsed.dashboard) {
        toast.error('无效的仪表盘数据格式');
        return;
      }

      const { name, description, charts, filters, carousel_interval } = parsed.dashboard;

      onImport({
        name: name ? `${name} (导入)` : '导入的仪表盘',
        description,
        filters,
        carousel_interval,
        charts: charts || [],
      });
      toast.success('导入成功');
      setImportOpen(false);
      setImportData('');
    } catch {
      toast.error('无效的JSON格式');
    }
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
          <DropdownMenuItem onClick={() => setExportOpen(true)}>
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
            <Button variant="outline" onClick={() => { setImportOpen(false); setImportData(''); }}>
              取消
            </Button>
            <Button onClick={handleImport} disabled={!importData.trim()}>
              导入
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
