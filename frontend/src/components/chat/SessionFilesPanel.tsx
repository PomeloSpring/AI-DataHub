import { useEffect, useState } from 'react';
import {
  Archive, FileImage, FileSpreadsheet, FileText, FileType, Code, FolderOpen,
  Loader2, RefreshCw, Trash2, type LucideIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import client from '@/api/client';
import ArtifactCard from '@/components/ArtifactCard';
import { formatSize } from '@/lib/dataGrid';

type ArtifactType = 'excel' | 'pdf' | 'html' | 'md' | 'png' | 'jpg' | 'gif' | 'webp' | 'csv';

interface SessionFile {
  path: string;
  size: number;
}

interface Props {
  open: boolean;
  onClose: () => void;
  workspaceId: number;
  conversationId: number | null;
}

/** 按扩展名推断产物预览类型;未知类型回落 md(仅下载不预览正文)。 */
function inferType(path: string): ArtifactType {
  const ext = (path.match(/\.[^.]+$/)?.[0] || '').toLowerCase();
  switch (ext) {
    case '.png': return 'png';
    case '.jpg': case '.jpeg': return 'jpg';
    case '.gif': return 'gif';
    case '.webp': return 'webp';
    case '.csv': return 'csv';
    case '.html': case '.htm': return 'html';
    case '.md': case '.txt': return 'md';
    case '.xlsx': case '.xls': return 'excel';
    case '.pdf': return 'pdf';
    default: return 'md';
  }
}

const TYPE_ICONS: Record<ArtifactType, LucideIcon> = {
  excel: FileSpreadsheet, csv: FileSpreadsheet, pdf: FileText,
  html: Code, md: FileType, png: FileImage, jpg: FileImage, gif: FileImage, webp: FileImage,
};

/**
 * 会话工作区文件面板:列出当前会话 uploads/ 与 Agent 产物文件,
 * 点击即在画布内预览(复用 ArtifactCard:图片/HTML/CSV 网格/文本),
 * 支持下载与归档收藏到工作空间资产。文件生命周期随会话(清理后面板为空)。
 */
export default function SessionFilesPanel({ open, onClose, workspaceId, conversationId }: Props) {
  const [files, setFiles] = useState<SessionFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<SessionFile | null>(null);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiveName, setArchiveName] = useState('');
  const [deleteSource, setDeleteSource] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const load = async () => {
    if (!conversationId) return;
    setLoading(true);
    setError('');
    try {
      const { data } = await client.get(
        `/workspace-assets/${workspaceId}/conversations/${conversationId}/files`);
      const list: SessionFile[] = Array.isArray(data) ? data : [];
      setFiles(list);
      setSelected(prev => (prev ? list.find(f => f.path === prev.path) || null : null));
    } catch (e: any) {
      setFiles([]);
      setError(e?.response?.data?.detail || '会话文件加载失败,请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, conversationId, workspaceId]);

  const openArchive = () => {
    if (!selected) return;
    setArchiveName(selected.path.split('/').pop()?.replace(/\.[^.]+$/, '') || '归档文件');
    setDeleteSource(false);
    setArchiveOpen(true);
  };

  const doArchive = async () => {
    if (!selected || !archiveName.trim()) return;
    setArchiving(true);
    try {
      await client.post(`/workspace-assets/${workspaceId}/assets/archive`, {
        name: archiveName.trim(),
        conversation_id: conversationId,
        path: selected.path,
        delete_source: deleteSource,
      });
      toast.success('已收藏到工作空间资产');
      setArchiveOpen(false);
      if (deleteSource) {
        setSelected(null);
        void load();
      }
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '归档失败,请稍后重试');
    } finally {
      setArchiving(false);
    }
  };

  const doDelete = async () => {
    if (!selected) return;
    const name = selected.path.split('/').pop() || selected.path;
    if (!window.confirm(`确定删除「${name}」？删除后不可恢复（已归档的资产不受影响）。`)) return;
    setDeleting(true);
    try {
      await client.delete(
        `/workspace-assets/${workspaceId}/conversations/${conversationId}/files`,
        { params: { path: selected.path } },
      );
      toast.success(`已删除 ${name}`);
      setSelected(null);
      void load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '删除失败,请稍后重试');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={o => { if (!o) onClose(); }}>
      <DialogContent className="max-w-5xl h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FolderOpen className="h-4 w-4 text-primary" />
            会话工作区文件
            <Badge variant="outline" className="text-[10px] font-normal">{files.length} 个</Badge>
            <Button variant="ghost" size="sm" className="h-7 text-xs ml-auto" onClick={() => void load()} disabled={loading}>
              {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              刷新
            </Button>
          </DialogTitle>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 gap-3">
          {/* 文件清单 */}
          <div className="w-64 shrink-0 overflow-auto rounded-lg border">
            {loading && files.length === 0 ? (
              <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />加载中…
              </div>
            ) : error ? (
              <div className="p-4 text-sm text-muted-foreground" role="alert">{error}</div>
            ) : files.length === 0 ? (
              <div className="p-4 text-sm text-muted-foreground">
                当前会话还没有文件。<br />上传附件或让助手产出图表/表格后,文件会出现在这里。
              </div>
            ) : (
              <ul>
                {files.map(f => {
                  const t = inferType(f.path);
                  const Icon = TYPE_ICONS[t];
                  const name = f.path.split('/').pop() || f.path;
                  return (
                    <li key={f.path}>
                      <button
                        onClick={() => setSelected(f)}
                        className={`flex w-full items-center gap-2 border-b px-3 py-2 text-left transition-colors hover:bg-muted/60 ${selected?.path === f.path ? 'bg-accent/50' : ''}`}
                        title={f.path}
                      >
                        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-xs font-medium">{name}</div>
                          <div className="truncate text-[10px] text-muted-foreground">{formatSize(f.size)}</div>
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* 预览区 */}
          <div className="min-w-0 flex-1 overflow-auto rounded-lg border bg-muted/20 p-3">
            {selected ? (
              <>
                <div className="mb-2 flex items-center justify-end gap-2">
                  <Button variant="outline" size="sm" className="h-7 text-xs" onClick={openArchive}>
                    <Archive className="h-3 w-3 mr-1" />归档收藏
                  </Button>
                  <Button variant="outline" size="sm" className="h-7 text-xs text-destructive hover:text-destructive" onClick={doDelete} disabled={deleting}>
                    {deleting ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Trash2 className="h-3 w-3 mr-1" />}
                    删除
                  </Button>
                </div>
                <ArtifactCard
                  key={selected.path}
                  type={inferType(selected.path)}
                  filename={selected.path.split('/').pop() || selected.path}
                  path={selected.path}
                  conversationId={conversationId}
                />
              </>
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                从左侧选择文件进行预览
              </div>
            )}
          </div>
        </div>

        {/* 归档收藏(资产为跨会话持久物,可选清理会话源文件) */}
        <Dialog open={archiveOpen} onOpenChange={setArchiveOpen}>
          <DialogContent className="max-w-sm">
            <DialogHeader>
              <DialogTitle>归档收藏到工作空间资产</DialogTitle>
            </DialogHeader>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">资产名称</label>
                <Input value={archiveName} onChange={e => setArchiveName(e.target.value)} className="mt-1" />
              </div>
              <label className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 accent-primary"
                  checked={deleteSource}
                  onChange={e => setDeleteSource(e.target.checked)}
                />
                同时清理会话内的源文件(资产不受影响)
              </label>
              <div className="flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => setArchiveOpen(false)}>取消</Button>
                <Button size="sm" onClick={() => void doArchive()} disabled={archiving || !archiveName.trim()}>
                  {archiving && <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />}
                  归档
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </DialogContent>
    </Dialog>
  );
}
