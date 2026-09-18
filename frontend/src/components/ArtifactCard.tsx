import { useMemo } from 'react';
import { Download, FileText, FileSpreadsheet, FileImage, Code, FileType } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface Props {
  type: 'excel' | 'pdf' | 'html' | 'md' | 'png' | 'jpg' | 'csv';
  filename: string;
  content: string;
  description?: string;
}

const TYPE_CONFIG: Record<string, { icon: any; label: string; mime: string; color: string }> = {
  excel: { icon: FileSpreadsheet, label: 'Excel', mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', color: 'text-green-600' },
  csv: { icon: FileSpreadsheet, label: 'CSV', mime: 'text/csv', color: 'text-green-600' },
  pdf: { icon: FileText, label: 'PDF', mime: 'application/pdf', color: 'text-red-600' },
  html: { icon: Code, label: 'HTML', mime: 'text/html', color: 'text-orange-600' },
  md: { icon: FileType, label: 'Markdown', mime: 'text/markdown', color: 'text-blue-600' },
  png: { icon: FileImage, label: 'PNG', mime: 'image/png', color: 'text-purple-600' },
  jpg: { icon: FileImage, label: 'JPG', mime: 'image/jpeg', color: 'text-purple-600' },
};

/** 将 base64 或纯文本内容转换为 Blob 并触发下载。 */
function downloadContent(content: string, filename: string, mime: string) {
  let blob: Blob;
  // 判断是否为 base64(常见于图片/PDF)
  if (content.startsWith('data:')) {
    // 已有 data URL
    fetch(content)
      .then(res => res.blob())
      .then(b => triggerDownload(b, filename));
    return;
  } else if (/^[A-Za-z0-9+/]+=*$/.test(content.slice(0, 100)) && content.length > 200) {
    // 可能是 base64
    try {
      const binary = atob(content);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      blob = new Blob([bytes], { type: mime });
    } catch {
      blob = new Blob([content], { type: mime });
    }
  } else {
    blob = new Blob([content], { type: mime });
  }
  triggerDownload(blob, filename);
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function ArtifactCard({ type, filename, content, description }: Props) {
  const config = TYPE_CONFIG[type] || TYPE_CONFIG.md;
  const Icon = config.icon;

  // 图片类型直接预览
  const isImage = type === 'png' || type === 'jpg';
  const imageSrc = useMemo(() => {
    if (!isImage) return '';
    if (content.startsWith('data:')) return content;
    try {
      const binary = atob(content);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      const blob = new Blob([bytes], { type: config.mime });
      return URL.createObjectURL(blob);
    } catch {
      return '';
    }
  }, [content, isImage, config.mime]);

  return (
    <div className="my-3 rounded-lg border bg-card overflow-hidden">
      <div className="px-3 py-2 border-b bg-muted/50 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className={`h-4 w-4 shrink-0 ${config.color}`} />
          <span className="text-sm font-semibold truncate">{filename}</span>
          <Badge variant="outline" className="text-[10px] shrink-0">{config.label}</Badge>
        </div>
        <Button
          variant="default"
          size="sm"
          className="h-7 text-xs"
          onClick={() => downloadContent(content, filename, config.mime)}
        >
          <Download className="h-3 w-3 mr-1" />下载
        </Button>
      </div>

      {description && (
        <div className="px-3 py-2 text-xs text-muted-foreground border-b">{description}</div>
      )}

      {isImage && imageSrc && (
        <div className="p-3 flex justify-center bg-muted/30">
          <img src={imageSrc} alt={filename} className="max-w-full max-h-[400px] object-contain rounded" />
        </div>
      )}

      {!isImage && type === 'md' && (
        <div className="p-3 max-h-[300px] overflow-auto">
          <pre className="text-xs font-mono whitespace-pre-wrap text-muted-foreground bg-muted/50 rounded p-2">
            {content.slice(0, 2000)}
            {content.length > 2000 && '\n... (内容已截断,请下载查看完整文件)'}
          </pre>
        </div>
      )}

      {!isImage && type === 'html' && (
        <div className="p-3 max-h-[300px] overflow-auto">
          <pre className="text-xs font-mono whitespace-pre-wrap text-muted-foreground bg-muted/50 rounded p-2">
            {content.slice(0, 2000)}
            {content.length > 2000 && '\n... (内容已截断,请下载查看完整文件)'}
          </pre>
        </div>
      )}

      {!isImage && type === 'csv' && (
        <div className="p-3 max-h-[300px] overflow-auto">
          <pre className="text-xs font-mono whitespace-pre-wrap text-muted-foreground bg-muted/50 rounded p-2">
            {content.slice(0, 2000)}
            {content.length > 2000 && '\n... (内容已截断,请下载查看完整文件)'}
          </pre>
        </div>
      )}
    </div>
  );
}
