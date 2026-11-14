import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog';
import { toast } from '@/lib/toast';
import { Archive, Download, Trash2, HardDrive, Sparkles, RefreshCw, Search } from 'lucide-react';
import client from '@/api/client';
import i18n from '@/i18n';

// 用户资产清单(资产跟随用户, 跨工作空间): 会话产物"归档收藏"后进入清单, 跟随账号复用;
// 资产托管对象存储(OSS), 归档后可清理本地产物(受工作空间磁盘配额约束)。

interface Asset {
  id: string;
  name: string;
  filename: string;
  category: string;
  size: number;
  source_conversation_id: number;
  origin_workspace_id: number;
  origin_workspace_name: string | null;
  created_at: string;
}

interface SessionFile { path: string; size: number; }
interface CleanupCandidate { kind: 'file' | 'session'; path?: string; session_key?: string; conversation_id?: number; filename?: string; bytes: number; }

const fmtBytes = (n: number) => {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0; let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
};

export default function WorkspaceAssets() {
  const { t } = useTranslation(['workspace', 'common', 'menu']);
  const { workspaceId } = useParams();
  const ws = Number(workspaceId) || 0;
  const [assets, setAssets] = useState<Asset[]>([]);
  const [search, setSearch] = useState('');
  const [disk, setDisk] = useState<{ usage_bytes: number; quota_bytes: number; quota_is_default?: boolean } | null>(null);
  const [loading, setLoading] = useState(false);
  // 归档对话框
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [convs, setConvs] = useState<Array<{ id: number; title: string }>>([]);
  const [convId, setConvId] = useState('');
  const [files, setFiles] = useState<SessionFile[]>([]);
  const [filePath, setFilePath] = useState('');
  const [assetName, setAssetName] = useState('');
  const [deleteSource, setDeleteSource] = useState(false);
  const [archiving, setArchiving] = useState(false);
  // 清理对话框
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [candidates, setCandidates] = useState<CleanupCandidate[]>([]);
  const [cleaning, setCleaning] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [a, d] = await Promise.all([
        client.get('/assets'),
        client.get(`/workspace-assets/${ws}/disk-status`),
      ]);
      // 响应形状校验: 未命中代理/网关误路由时会拿到 HTML/错误对象(200+非列表),
      // 不静默当空列表, 显式报错(no-silent-degradation)
      if (!Array.isArray(a.data)) {
        toast.error(i18n.t('workspace:assets.loadShapeError'));
        setAssets([]);
      } else {
        setAssets(a.data);
      }
      if (!d.data || typeof d.data !== 'object' || Array.isArray(d.data)) {
        toast.error(i18n.t('workspace:assets.diskShapeError'));
        setDisk(null);
      } else {
        setDisk(d.data);
      }
    } catch {
      toast.error(i18n.t('workspace:assets.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [ws]);
  useEffect(() => { load(); }, [load]);

  const openArchive = async () => {
    setArchiveOpen(true);
    setConvId(''); setFilePath(''); setAssetName(''); setFiles([]); setDeleteSource(false);
    try {
      const { data } = await client.get('/chat/conversations', { params: { workspace_id: ws, size: 50 } });
      setConvs((data?.items || data || []).map((c: any) => ({ id: c.id, title: c.title || i18n.t('workspace:assets.convFallback', { id: c.id }) })));
    } catch { setConvs([]); }
  };

  const pickConv = async (id: string) => {
    setConvId(id); setFilePath(''); setAssetName(''); setFiles([]);
    if (!id) return;
    try {
      const { data } = await client.get(`/workspace-assets/${ws}/conversations/${id}/files`);
      if (!Array.isArray(data)) {
        toast.error(i18n.t('workspace:assets.filesShapeError'));
        setFiles([]);
      } else {
        setFiles(data);
      }
    } catch {
      toast.error(i18n.t('workspace:assets.filesLoadFailed'));
    }
  };

  const archive = async () => {
    if (!convId || !filePath) { toast.error(t('assets.pickRequired')); return; }
    setArchiving(true);
    try {
      await client.post('/assets/archive', {
        name: assetName.trim() || filePath.split('/').pop(),
        conversation_id: Number(convId),
        path: filePath,
        delete_source: deleteSource,
      });
      toast.success(deleteSource ? t('assets.archivedCleaned') : t('assets.archived'));
      setArchiveOpen(false);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('assets.archiveFailed'));
    } finally {
      setArchiving(false);
    }
  };

  const openCleanup = async () => {
    setCleanupOpen(true);
    setCandidates([]);
    try {
      const { data } = await client.post(`/workspace-assets/${ws}/cleanup`, { confirm: false });
      setCandidates(data?.candidates || []);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('assets.cleanupLoadFailed'));
    }
  };

  const runCleanup = async () => {
    setCleaning(true);
    try {
      const { data } = await client.post(`/workspace-assets/${ws}/cleanup`, { confirm: true });
      toast.success(t('assets.cleaned', { count: data?.cleaned ?? 0, size: fmtBytes(data?.freed_bytes ?? 0) }));
      setCleanupOpen(false);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('assets.cleanupFailed'));
    } finally {
      setCleaning(false);
    }
  };

  const download = async (a: Asset) => {
    // OSS 直链优先: 浏览器直连对象存储下载(与归档同源), 不经服务端回源
    try {
      const direct = await client.get(`/assets/${a.id}/download-url`);
      if (direct.data?.url) {
        window.open(direct.data.url, '_blank');
        return;
      }
    } catch (e: any) {
      const status = e?.response?.status;
      if (status !== 400) {
        // 直链生成失败(OSS 侧问题)显式报错，不静默换道
        toast.error(e?.response?.data?.detail || t('assets.ossDownloadFailed'));
        return;
      }
      // 400=本地存储模式(未配置对象存储)，回落服务端代理下载
    }
    try {
      const res = await client.get(`/assets/${a.id}/download`, { responseType: 'blob' });
      const url = URL.createObjectURL(res.data as Blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = a.filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error(t('assets.downloadFailed'));
    }
  };

  const remove = async (a: Asset) => {
    try {
      await client.delete(`/assets/${a.id}`);
      toast.success(t('assets.removed'));
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('common:state.deleteFailed'));
    }
  };

  const filtered = assets.filter(a =>
    `${a.name} ${a.filename}`.toLowerCase().includes(search.toLowerCase()));
  const usagePct = disk && disk.quota_bytes > 0
    ? Math.min(100, Math.round((disk.usage_bytes / disk.quota_bytes) * 100)) : 0;

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-[1200px] p-6 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2"><Archive className="h-6 w-6" />{t('menu:workspace.assets')}</h1>
            <p className="text-muted-foreground mt-1 text-sm">
              {t('assets.subtitleBefore')}<code className="rounded bg-muted px-1">@</code>{t('assets.subtitleAfter')}
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={load} disabled={loading}><RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />{t('common:action.refresh')}</Button>
            <Button variant="outline" size="sm" onClick={openCleanup}><HardDrive className="h-4 w-4 mr-1" />{t('assets.cleanupLocal')}</Button>
            <Button size="sm" onClick={openArchive}><Sparkles className="h-4 w-4 mr-1" />{t('assets.archiveSession')}</Button>
          </div>
        </div>

        {disk && (
          <div className="border rounded-lg p-3 space-y-2">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>{t('assets.diskUsage')}</span>
              <span>
                {fmtBytes(disk.usage_bytes)} / {disk.quota_bytes > 0
                  ? `${fmtBytes(disk.quota_bytes)}${disk.quota_is_default ? t('assets.quotaDefault') : ''}`
                  : t('assets.unlimited')}
                {usagePct >= 90 ? t('assets.nearLimit') : ''}
              </span>
            </div>
            <div className="h-1.5 w-full rounded bg-muted overflow-hidden">
              <div className={`h-1.5 rounded ${usagePct >= 90 ? 'bg-destructive' : usagePct >= 70 ? 'bg-amber-500' : 'bg-primary'}`} style={{ width: `${usagePct}%` }} />
            </div>
          </div>
        )}

        <div className="relative max-w-md">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input aria-label={t('assets.searchAria')} placeholder={t('assets.searchPlaceholder')} className="pl-9" value={search} onChange={e => setSearch(e.target.value)} />
        </div>

        {loading ? (
          <div className="text-center py-8 text-muted-foreground">{t('common:state.loading')}</div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground">
            {search ? t('assets.noMatch') : t('assets.empty')}
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map(a => (
              <div key={a.id} className="flex items-center justify-between gap-2 border rounded-lg px-4 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium truncate">{a.name}</span>
                    <Badge variant="outline" className="text-xs">{a.category}</Badge>
                    <span className="text-xs text-muted-foreground">{fmtBytes(a.size)}</span>
                  </div>
                  <div className="text-xs text-muted-foreground truncate">
                    {a.filename}
                    {a.source_conversation_id > 0 && <> · {t('assets.fromConversation', { id: a.source_conversation_id })}</>}
                    {a.origin_workspace_id > 0 && <> · {a.origin_workspace_name || t('assets.workspaceDeleted')}</>}
                    {a.created_at && <> · {new Date(a.created_at).toLocaleString(i18n.language)}</>}
                  </div>
                </div>
                <div className="flex gap-1">
                  <Button variant="outline" size="sm" onClick={() => download(a)}><Download className="h-3.5 w-3.5" /></Button>
                  <Button variant="ghost" size="sm" className="text-destructive" onClick={() => remove(a)}><Trash2 className="h-3.5 w-3.5" /></Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 归档会话产物 */}
      <Dialog open={archiveOpen} onOpenChange={setArchiveOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('assets.archiveSession')}</DialogTitle>
            <DialogDescription>{t('assets.archive.desc')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>{t('assets.archive.conv')}</Label>
              <select aria-label={t('assets.archive.convAria')} className="w-full border rounded px-2 py-1.5 text-sm bg-background" value={convId} onChange={e => void pickConv(e.target.value)}>
                <option value="">{t('assets.archive.convPlaceholder')}</option>
                {convs.map(c => <option key={c.id} value={c.id}>{c.title}</option>)}
              </select>
            </div>
            <div className="space-y-2">
              <Label>{t('assets.archive.file')}</Label>
              <select aria-label={t('assets.archive.fileAria')} className="w-full border rounded px-2 py-1.5 text-sm bg-background" value={filePath} onChange={e => { setFilePath(e.target.value); setAssetName(e.target.value.split('/').pop() || ''); }}>
                <option value="">{convId ? (files.length ? t('assets.archive.filePlaceholder') : t('assets.archive.fileEmpty')) : t('assets.archive.convFirst')}</option>
                {files.map(f => <option key={f.path} value={f.path}>{f.path}（{fmtBytes(f.size)}）</option>)}
              </select>
            </div>
            <div className="space-y-2">
              <Label>{t('assets.archive.name')}</Label>
              <Input value={assetName} onChange={e => setAssetName(e.target.value)} placeholder={t('assets.archive.namePlaceholder')} />
            </div>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" className="h-3.5 w-3.5" checked={deleteSource} onChange={e => setDeleteSource(e.target.checked)} />
              {t('assets.archive.deleteSource')}
            </label>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setArchiveOpen(false)}>{t('common:action.cancel')}</Button>
            <Button onClick={archive} disabled={archiving}>{archiving ? t('assets.archive.submitting') : t('assets.archive.submit')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 清理本地产物 */}
      <Dialog open={cleanupOpen} onOpenChange={setCleanupOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('assets.cleanupLocal')}</DialogTitle>
            <DialogDescription>{t('assets.cleanup.desc')}</DialogDescription>
          </DialogHeader>
          {candidates.length === 0 ? (
            <p className="text-sm text-muted-foreground py-2">{t('assets.cleanup.empty')}</p>
          ) : (
            <div className="max-h-64 overflow-y-auto space-y-1">
              {candidates.map((c, i) => (
                <div key={i} className="flex items-center justify-between gap-2 border rounded px-3 py-1.5 text-xs">
                  <span className="truncate">
                    {c.kind === 'file' ? t('assets.cleanup.file', { name: c.filename || c.path }) : t('assets.cleanup.session', { id: c.conversation_id })}
                  </span>
                  <span className="text-muted-foreground flex-shrink-0">{fmtBytes(c.bytes)}</span>
                </div>
              ))}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setCleanupOpen(false)}>{t('common:action.cancel')}</Button>
            <Button variant="destructive" onClick={runCleanup} disabled={cleaning || candidates.length === 0}>
              {cleaning ? t('assets.cleanup.submitting') : t('assets.cleanup.confirm', { count: candidates.length })}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
