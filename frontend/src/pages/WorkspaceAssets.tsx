import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog';
import { toast } from 'sonner';
import { Archive, Download, Trash2, HardDrive, Sparkles, RefreshCw, Search } from 'lucide-react';
import client from '@/api/client';

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
  const { workspaceId } = useParams();
  const ws = Number(workspaceId) || 0;
  const [assets, setAssets] = useState<Asset[]>([]);
  const [search, setSearch] = useState('');
  const [disk, setDisk] = useState<{ usage_bytes: number; quota_bytes: number } | null>(null);
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
        toast.error('资产清单响应异常（非列表结构），请检查前端代理配置或服务日志');
        setAssets([]);
      } else {
        setAssets(a.data);
      }
      if (!d.data || typeof d.data !== 'object' || Array.isArray(d.data)) {
        toast.error('磁盘用量响应异常，请检查服务日志');
        setDisk(null);
      } else {
        setDisk(d.data);
      }
    } catch {
      toast.error('加载资产清单失败');
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
      setConvs((data?.items || data || []).map((c: any) => ({ id: c.id, title: c.title || `会话 ${c.id}` })));
    } catch { setConvs([]); }
  };

  const pickConv = async (id: string) => {
    setConvId(id); setFilePath(''); setAssetName(''); setFiles([]);
    if (!id) return;
    try {
      const { data } = await client.get(`/workspace-assets/${ws}/conversations/${id}/files`);
      if (!Array.isArray(data)) {
        toast.error('会话产物响应异常（非列表结构），请检查服务日志');
        setFiles([]);
      } else {
        setFiles(data);
      }
    } catch {
      toast.error('加载会话产物失败');
    }
  };

  const archive = async () => {
    if (!convId || !filePath) { toast.error('请选择会话与产物文件'); return; }
    setArchiving(true);
    try {
      await client.post('/assets/archive', {
        name: assetName.trim() || filePath.split('/').pop(),
        conversation_id: Number(convId),
        path: filePath,
        delete_source: deleteSource,
      });
      toast.success(deleteSource ? '已归档并清理本地产物' : '已归档到我的资产');
      setArchiveOpen(false);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '归档失败');
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
      toast.error(e.response?.data?.detail || '加载清理候选失败');
    }
  };

  const runCleanup = async () => {
    setCleaning(true);
    try {
      const { data } = await client.post(`/workspace-assets/${ws}/cleanup`, { confirm: true });
      toast.success(`已清理 ${data?.cleaned ?? 0} 项，释放 ${fmtBytes(data?.freed_bytes ?? 0)}`);
      setCleanupOpen(false);
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '清理失败');
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
        toast.error(e?.response?.data?.detail || 'OSS 直链下载失败');
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
      toast.error('下载失败');
    }
  };

  const remove = async (a: Asset) => {
    try {
      await client.delete(`/assets/${a.id}`);
      toast.success('资产已删除');
      load();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
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
            <h1 className="text-2xl font-bold flex items-center gap-2"><Archive className="h-6 w-6" />资产清单</h1>
            <p className="text-muted-foreground mt-1 text-sm">
              会话产物"归档收藏"后进入清单，跟随账号跨工作空间复用；资产托管在对象存储，本地会话产物可按磁盘配额清理。
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={load} disabled={loading}><RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />刷新</Button>
            <Button variant="outline" size="sm" onClick={openCleanup}><HardDrive className="h-4 w-4 mr-1" />清理本地产物</Button>
            <Button size="sm" onClick={openArchive}><Sparkles className="h-4 w-4 mr-1" />归档会话产物</Button>
          </div>
        </div>

        {disk && (
          <div className="border rounded-lg p-3 space-y-2">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>工作空间磁盘用量</span>
              <span>{fmtBytes(disk.usage_bytes)} / {disk.quota_bytes ? fmtBytes(disk.quota_bytes) : '未设配额'}{usagePct >= 90 ? '（接近上限，建议归档/清理）' : ''}</span>
            </div>
            <div className="h-1.5 w-full rounded bg-muted overflow-hidden">
              <div className={`h-1.5 rounded ${usagePct >= 90 ? 'bg-destructive' : usagePct >= 70 ? 'bg-amber-500' : 'bg-primary'}`} style={{ width: `${usagePct}%` }} />
            </div>
          </div>
        )}

        <div className="relative max-w-md">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input aria-label="搜索资产" placeholder="搜索资产名称或文件名…" className="pl-9" value={search} onChange={e => setSearch(e.target.value)} />
        </div>

        {loading ? (
          <div className="text-center py-8 text-muted-foreground">加载中...</div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground">
            {search ? '没有匹配的资产' : '暂无资产，点击「归档会话产物」把重要产物收藏进清单'}
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
                    {a.source_conversation_id > 0 && <> · 来自会话 #{a.source_conversation_id}</>}
                    {a.origin_workspace_id > 0 && <> · {a.origin_workspace_name || '工作空间已删除'}</>}
                    {a.created_at && <> · {new Date(a.created_at).toLocaleString('zh-CN')}</>}
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
            <DialogTitle>归档会话产物</DialogTitle>
            <DialogDescription>选择会话与产物文件收藏进资产清单；可选归档后清理本地文件。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>会话</Label>
              <select aria-label="选择会话" className="w-full border rounded px-2 py-1.5 text-sm bg-background" value={convId} onChange={e => void pickConv(e.target.value)}>
                <option value="">请选择会话</option>
                {convs.map(c => <option key={c.id} value={c.id}>{c.title}</option>)}
              </select>
            </div>
            <div className="space-y-2">
              <Label>产物文件</Label>
              <select aria-label="选择产物文件" className="w-full border rounded px-2 py-1.5 text-sm bg-background" value={filePath} onChange={e => { setFilePath(e.target.value); setAssetName(e.target.value.split('/').pop() || ''); }}>
                <option value="">{convId ? (files.length ? '请选择产物文件' : '该会话暂无产物文件') : '请先选择会话'}</option>
                {files.map(f => <option key={f.path} value={f.path}>{f.path}（{fmtBytes(f.size)}）</option>)}
              </select>
            </div>
            <div className="space-y-2">
              <Label>资产名称</Label>
              <Input value={assetName} onChange={e => setAssetName(e.target.value)} placeholder="默认使用文件名" />
            </div>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" className="h-3.5 w-3.5" checked={deleteSource} onChange={e => setDeleteSource(e.target.checked)} />
              归档后删除本地产物文件（节省磁盘配额，资产不受影响）
            </label>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setArchiveOpen(false)}>取消</Button>
            <Button onClick={archive} disabled={archiving}>{archiving ? '归档中...' : '归档'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 清理本地产物 */}
      <Dialog open={cleanupOpen} onOpenChange={setCleanupOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>清理本地产物</DialogTitle>
            <DialogDescription>候选 = 已归档资产的来源文件 + 已关闭会话目录；已归档资产保存在对象存储，不受影响。</DialogDescription>
          </DialogHeader>
          {candidates.length === 0 ? (
            <p className="text-sm text-muted-foreground py-2">暂无可清理的本地产物</p>
          ) : (
            <div className="max-h-64 overflow-y-auto space-y-1">
              {candidates.map((c, i) => (
                <div key={i} className="flex items-center justify-between gap-2 border rounded px-3 py-1.5 text-xs">
                  <span className="truncate">
                    {c.kind === 'file' ? `产物文件: ${c.filename || c.path}` : `会话目录: #${c.conversation_id}`}
                  </span>
                  <span className="text-muted-foreground flex-shrink-0">{fmtBytes(c.bytes)}</span>
                </div>
              ))}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setCleanupOpen(false)}>取消</Button>
            <Button variant="destructive" onClick={runCleanup} disabled={cleaning || candidates.length === 0}>
              {cleaning ? '清理中...' : `确认清理（${candidates.length} 项）`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
