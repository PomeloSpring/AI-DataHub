import { useMemo } from 'react';
import { Download, FileText, FileSpreadsheet, FileImage, Code, FileType } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface Props {
  type: 'excel' | 'pdf' | 'html' | 'md' | 'png' | 'jpg' | 'csv';
  filename: string;
  /** 内联内容(base64 或纯文本);与 path 二选一。 */
  content?: string;
  /** 会话工作区相对路径(按引用下载/预览);优先于 content。 */
  path?: string;
  description?: string;
  /** 产物采用的主题 id(展示用; HTML 已在生成时内联主题快照)。 */
  theme?: string;
  /** 当前会话 ID,用于按引用构造 session-file 下载/预览 URL。 */
  conversationId?: number | null;
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

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** 将 base64 或纯文本内容转换为 Blob 并触发下载。 */
function downloadContent(content: string, filename: string, mime: string) {
  if (content.startsWith('data:')) {
    fetch(content).then(res => res.blob()).then(b => triggerDownload(b, filename));
    return;
  }
  let blob: Blob;
  if (/^[A-Za-z0-9+/]+=*$/.test(content.slice(0, 100)) && content.length > 200) {
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

/** 把内联 base64 图片转成 data URL(无 path 时的预览回退)。 */
function inlineImageSrc(content: string | undefined, mime: string): string {
  if (!content) return '';
  if (content.startsWith('data:')) return content;
  try {
    const binary = atob(content);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return URL.createObjectURL(new Blob([bytes], { type: mime }));
  } catch {
    return '';
  }
}

export default function ArtifactCard({ type, filename, content, path, description, theme, conversationId }: Props) {
  const config = TYPE_CONFIG[type] || TYPE_CONFIG.md;
  const Icon = config.icon;
  const byRef = !!path && !!conversationId;

  // 按引用: 构造 session-file URL(带 token, 供 <a>/<img>/<iframe> 无法带自定义头的场景)。
  const fileUrl = useMemo(() => {
    if (!path || !conversationId) return '';
    const token = localStorage.getItem('token') || '';
    return `/api/chat/session-file?conversation_id=${conversationId}&path=${encodeURIComponent(path)}&token=${encodeURIComponent(token)}`;
  }, [path, conversationId]);

  const isImage = type === 'png' || type === 'jpg';
  const previewSrc = byRef ? fileUrl : (isImage ? inlineImageSrc(content, config.mime) : '');

  const onDownload = () => {
    if (byRef) {
      const a = document.createElement('a');
      a.href = `${fileUrl}&download=1`;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } else if (content) {
      downloadContent(content, filename, config.mime);
    }
  };

  const canDownload = byRef || !!content;

  return (
    <div className="my-3 rounded-lg border bg-card overflow-hidden">
      <div className="px-3 py-2 border-b bg-muted/50 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className={`h-4 w-4 shrink-0 ${config.color}`} />
          <span className="text-sm font-semibold truncate">{filename}</span>
          <Badge variant="outline" className="text-[10px] shrink-0">{config.label}</Badge>
          {theme && <Badge variant="outline" className="text-[10px] shrink-0 font-normal text-muted-foreground">{theme}</Badge>}
        </div>
        <Button variant="default" size="sm" className="h-7 text-xs" onClick={onDownload} disabled={!canDownload}>
          <Download className="h-3 w-3 mr-1" />下载
        </Button>
      </div>

      {description && (
        <div className="px-3 py-2 text-xs text-muted-foreground border-b">{description}</div>
      )}

      {/* 图片预览: 按引用用 fileUrl, 内联用 data/object URL */}
      {isImage && previewSrc && (
        <div className="p-3 flex justify-center bg-muted/30">
          <img src={previewSrc} alt={filename} className="max-w-full max-h-[400px] object-contain rounded" />
        </div>
      )}

      {/* HTML 可视化预览: 按引用用 iframe src, 内联用 srcDoc */}
      {type === 'html' && (byRef || content) && (
        <div className="border-t bg-white">
          <iframe
            title={filename}
            src={byRef ? fileUrl : undefined}
            srcDoc={byRef ? undefined : content}
            sandbox="allow-scripts"
            className="w-full h-[420px] border-0"
          />
        </div>
      )}

      {/* 文本类(md/csv)且非按引用: 预览前 2000 字符 */}
      {!byRef && (type === 'md' || type === 'csv') && content && (
        <div className="p-3 max-h-[300px] overflow-auto border-t">
          <pre className="text-xs font-mono whitespace-pre-wrap text-muted-foreground bg-muted/50 rounded p-2">
            {content.slice(0, 2000)}
            {content.length > 2000 && '\n... (内容已截断,请下载查看完整文件)'}
          </pre>
        </div>
      )}
    </div>
  );
}
