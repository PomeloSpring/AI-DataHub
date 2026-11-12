import { useEffect, useState } from 'react';
import {
  Cloud, CloudOff, FileImage, FileSpreadsheet, FileText, FileType, Code,
  FolderOpen, Loader2, MoreHorizontal, RefreshCw, Trash2, type LucideIcon,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import client from '@/api/client';
import ArtifactCard from '@/components/ArtifactCard';
import { formatSize } from '@/lib/dataGrid';

type ArtifactType = 'excel' | 'pdf' | 'html' | 'md' | 'png' | 'jpg' | 'gif' | 'webp' | 'csv';

interface SessionFile {
  path: string;
  size: number;
  /** 是否已同步云空间（后端按对象存储实际清单实判；未配置对象存储时恒 false） */
  synced?: boolean;
}

/** 云空间同步态：configured=对象存储是否可用，enabled=用户是否开启自动上云 */
interface SyncState {
  enabled: boolean;
  configured: boolean;
  note?: string;
}

interface Props {
  workspaceId: number;
  conversationId: number | null;
  /** 外部要求重读工作区的计数（如「存为工作区」刚落盘一个 xlsx）。
   *  只靠 conversationId 变化会漏：同一会话里新产物不会触发重拉，清单停在旧状态。 */
  refreshKey?: number;
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
 * 会话工作区文件清单（右侧栏「会话产物」小节）。
 *
 * 旧形态是居中弹窗（SessionFilesPanel），与看板/文档/报告草稿分处两个界面、来回开合；
 * 现在收进侧栏：清单常驻在会话旁边，预览仍走弹层（窄栏放不下 Excel 多 sheet 与图片大图），
 * 下载由预览里的 ArtifactCard 自带按钮承担，不在清单行重复一套。
 *
 * 文件生命周期随会话（清理后面板为空）；同步态以对象存储实际清单为准 ——
 * 未同步就是没上云，不假装「已保存」。
 */
export default function SessionFilesPanel({ workspaceId, conversationId, refreshKey }: Props) {
  const [files, setFiles] = useState<SessionFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<SessionFile | null>(null);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiveName, setArchiveName] = useState('');
  const [deleteSource, setDeleteSource] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [sync, setSync] = useState<SyncState | null>(null);

  const load = async () => {
    if (!conversationId) return;
    setLoading(true);
    setError('');
    try {
      const { data } = await client.get(
        `/workspace-assets/${workspaceId}/conversations/${conversationId}/files`);
      // 同步态另走轻接口（不 LIST 云端）；它失败不拦清单展示
      try {
        const r = await client.get(
          `/workspace-assets/${workspaceId}/conversations/${conversationId}/files-sync`);
        setSync(r.data as SyncState);
      } catch { setSync(null); }
      // 非列表结构(如未命中代理拿到 HTML)不静默当空列表, 显式报错
      const list: SessionFile[] = Array.isArray(data) ? data : [];
      if (!Array.isArray(data)) setError('会话产物响应异常（非列表结构），请检查服务日志');
      setFiles(list);
      setPreview(prev => (prev ? list.find(f => f.path === prev.path) || null : null));
    } catch (e: any) {
      setFiles([]);
      setError(e?.response?.data?.detail || '会话文件加载失败,请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (conversationId) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, workspaceId, refreshKey]);

  const openArchive = (file: SessionFile) => {
    setPreview(null);
    setArchiveName(file.path.split('/').pop()?.replace(/\.[^.]+$/, '') || '归档文件');
    setDeleteSource(false);
    setArchiveOpen(true);
  };

  const doArchive = async () => {
    if (!preview || !archiveName.trim()) return;
    setArchiving(true);
    try {
      await client.post('/assets/archive', {
        name: archiveName.trim(),
        conversation_id: conversationId,
        path: preview.path,
        delete_source: deleteSource,
      });
      toast.success('已收藏到我的资产');
      setArchiveOpen(false);
      if (deleteSource) {
        setPreview(null);
        void load();
      }
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '归档失败,请稍后重试');
    } finally {
      setArchiving(false);
    }
  };

  const doDelete = async (file: SessionFile) => {
    const name = file.path.split('/').pop() || file.path;
    if (!window.confirm(`确定删除「${name}」？删除后不可恢复（已归档的资产不受影响）。`)) return;
    setDeleting(true);
    try {
      await client.delete(
        `/workspace-assets/${workspaceId}/conversations/${conversationId}/files`,
        { params: { path: file.path } },
      );
      toast.success(`已删除 ${name}`);
      setPreview(null);
      void load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '删除失败,请稍后重试');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <Badge variant="outline" className="text-[10px] font-normal">{files.length} 个</Badge>
        <Button
          variant="ghost" size="sm" className="ml-auto h-6 px-1.5 text-[11px] text-muted-foreground"
          onClick={() => void load()} disabled={loading} title="重新读取会话工作区"
        >
          {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
        </Button>
      </div>

      {loading && files.length === 0 ? (
        <div className="flex items-center gap-2 py-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />加载中…
        </div>
      ) : error ? (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
          {error}
        </p>
      ) : files.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          当前会话还没有文件。上传附件或让助手产出图表/表格后，文件会出现在这里。
        </p>
      ) : (
        <>
          <ul className="space-y-1">
            {files.map(f => {
              const Icon = TYPE_ICONS[inferType(f.path)];
              const name = f.path.split('/').pop() || f.path;
              return (
                <li key={f.path} className="group flex items-center gap-1.5 rounded-md border border-border/60 bg-card px-2 py-1.5">
                  <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left"
                    title={f.path}
                    onClick={() => setPreview(f)}
                  >
                    <span className="block truncate text-xs font-medium">{name}</span>
                    <span className="block truncate text-[10px] text-muted-foreground">{formatSize(f.size)}</span>
                  </button>
                  {/* 同步态以对象存储实际清单为准：未同步就是没上云，不假装“已保存” */}
                  {f.synced
                    ? <Cloud className="h-3 w-3 shrink-0 text-emerald-600" aria-label="已同步云空间" />
                    : <CloudOff className="h-3 w-3 shrink-0 text-amber-600" aria-label="未同步云空间" />}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost" size="sm" className="h-6 w-6 shrink-0 p-0 text-muted-foreground"
                        aria-label={`文件 ${name} 的操作`}
                      >
                        <MoreHorizontal className="h-3.5 w-3.5" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-36">
                      <DropdownMenuItem onClick={() => setPreview(f)}>预览</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => openArchive(f)}>归档收藏</DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        className="text-destructive focus:text-destructive"
                        disabled={deleting}
                        onClick={() => void doDelete(f)}
                      >
                        <Trash2 className="mr-1 h-3.5 w-3.5" />删除
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </li>
              );
            })}
          </ul>
          {/* 同步态说明条：未配置/已关闭时把事实讲清楚，不让人以为产物已在云上 */}
          <p className="text-[10px] text-muted-foreground">
            {sync && !sync.configured
              ? '对象存储未配置，产物只在本地（换节点或清理磁盘会丢失）'
              : sync && !sync.enabled
                ? '个人设置已关闭「会话产物自动上云」，新产物不再同步云空间'
                : `${files.filter(f => f.synced).length}/${files.length} 已同步云空间`}
          </p>
        </>
      )}

      {/* 预览（下载按钮在 ArtifactCard 内） */}
      <Dialog open={!!preview} onOpenChange={open => { if (!open) setPreview(null); }}>
        <DialogContent className="max-w-4xl h-[80vh] flex flex-col gap-2">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-sm">
              <FolderOpen className="h-4 w-4 text-primary" />
              <span className="truncate" title={preview?.path}>{preview?.path.split('/').pop()}</span>
            </DialogTitle>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-auto rounded-lg border bg-muted/20 p-3">
            {preview && (
              <ArtifactCard
                key={preview.path}
                type={inferType(preview.path)}
                filename={preview.path.split('/').pop() || preview.path}
                path={preview.path}
                conversationId={conversationId}
              />
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* 归档收藏(资产为跨会话持久物,可选清理会话源文件) */}
      <Dialog open={archiveOpen} onOpenChange={setArchiveOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>归档收藏到我的资产</DialogTitle>
            {/* 说清归档换来什么：产物自动上云只是备份(换节点不丢)，归档才是跨会话可复用的资产 */}
            <DialogDescription>
              归档后跟随账号跨工作空间保留，可在聊天框用 @ 引用为会话上下文。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-muted-foreground" htmlFor="archive-name">资产名称</label>
              <Input
                id="archive-name"
                value={archiveName}
                onChange={e => setArchiveName(e.target.value)}
                className="mt-1"
              />
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
    </div>
  );
}
