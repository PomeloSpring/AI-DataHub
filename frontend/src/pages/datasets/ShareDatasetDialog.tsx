import { useCallback, useEffect, useState } from 'react';
import { t as tr } from '@/i18n/runtime';
import { Loader2, Users } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import client from '../../api/client';

/**
 * 数据集归属共享（private ⇄ workspace）。
 *
 * 口径（2026-11-20 用户拍板）：
 * - 「仅创建者」= private，只有属主可见/可取数（admin 在管理面可见可编辑）；
 * - 「按角色共享」= workspace，所选角色可在数据集页看到、并挂到看板取数；
 * - 共享是**显式动作**，不会在建数据集时自动补授角色（历史缺陷：chat 建的数据集
 *   一补授就成了同角色可见）。
 *
 * 角色以**业务名**呈现（ui-resource-display）：不显示角色 id，选项来自 `/roles/`。
 */
interface ShareDialogProps {
  open: boolean;
  datasetId: number;
  datasetName?: string;
  onClose: () => void;
  onSaved?: () => void;
}

export default function ShareDatasetDialog({
  open, datasetId, datasetName, onClose, onSaved,
}: ShareDialogProps) {
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [ownerName, setOwnerName] = useState('');
  const [roles, setRoles] = useState<Array<{ id: number; display_name?: string; name?: string }>>([]);
  const [visibility, setVisibility] = useState<'private' | 'workspace'>('private');
  const [roleIds, setRoleIds] = useState<number[]>([]);
  const [references, setReferences] = useState<any[]>([]);

  const load = useCallback(async () => {
    if (!open) return;
    setLoading(true);
    setLoadError('');
    try {
      const [sharing, roleList] = await Promise.all([
        client.get(`/datasets/${datasetId}/sharing`),
        client.get('/roles/'),
      ]);
      const s = sharing.data || {};
      setOwnerName(s.owner_name || '');
      setVisibility(s.visibility === 'workspace' ? 'workspace' : 'private');
      setRoleIds((s.roles || []).map((r: any) => Number(r.id)));
      setReferences(s.references || []);
      setRoles(Array.isArray(roleList.data) ? roleList.data : []);
    } catch (e: any) {
      // 失败必须显式（不得静默显示成“未共享”）：给出后端原因与重试入口
      setLoadError(e?.response?.data?.detail || tr('k_71f0a83c4e'));
    } finally {
      setLoading(false);
    }
  }, [open, datasetId]);

  useEffect(() => { load(); }, [load]);

  const toggleRole = (id: number) => {
    setRoleIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  };

  const save = async () => {
    if (visibility === 'workspace' && roleIds.length === 0) {
      toast.error(tr('k_0c73e5a9f1'));
      return;
    }
    setBusy(true);
    try {
      const { data } = await client.put(`/datasets/${datasetId}/sharing`, {
        visibility,
        role_ids: visibility === 'workspace' ? roleIds : [],
      });
      (data?.warnings || []).forEach((w: string) => toast.warning(w));
      toast.success(tr('k_6af90d12b5'));
      onSaved?.();
      onClose();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || tr('k_9d5b1e7a02'));
    } finally {
      setBusy(false);
    }
  };

  const roleLabel = (r: any) => r.display_name || r.name || '';

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{tr('k_5c07b93e2a')}</DialogTitle>
          <DialogDescription>
            {datasetName || ''}{ownerName ? ` · ${tr('k_3a91f0c4d7')}：${ownerName}` : ''}
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex justify-center py-8 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : loadError ? (
          <div className="space-y-2 py-4" role="alert">
            <p className="text-sm text-destructive">{loadError}</p>
            <Button size="sm" variant="outline" onClick={load}>{tr('k_1f9c3a5b70')}</Button>
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-xs text-muted-foreground">{tr('k_8d24f61ca7')}</p>

            <div className="grid grid-cols-2 gap-2">
              <button type="button" onClick={() => setVisibility('private')}
                className={`rounded-md border px-3 py-2 text-left text-sm ${visibility === 'private' ? 'border-primary bg-primary/5' : ''}`}>
                <div className="font-medium">{tr('k_9e40c7b158')}</div>
                <div className="text-[11px] text-muted-foreground">{tr('k_d3a82c6f14')}</div>
              </button>
              <button type="button" onClick={() => setVisibility('workspace')}
                className={`rounded-md border px-3 py-2 text-left text-sm ${visibility === 'workspace' ? 'border-primary bg-primary/5' : ''}`}>
                <div className="font-medium">{tr('k_2f81d5ae64')}</div>
                <div className="text-[11px] text-muted-foreground">{tr('k_71b6c08d5a')}</div>
              </button>
            </div>

            {visibility === 'workspace' && (
              <div className="space-y-2">
                <div className="text-xs font-medium flex items-center gap-1">
                  <Users className="h-3.5 w-3.5" /> {tr('k_4b8e2d97c0')}
                </div>
                <div className="max-h-52 overflow-auto rounded-md border p-2 space-y-1">
                  {roles.length === 0 ? (
                    <p className="text-xs text-muted-foreground">{tr('k_6c40a1e8f3')}</p>
                  ) : roles.map(r => (
                    <button key={r.id} type="button" onClick={() => toggleRole(Number(r.id))}
                      className={`w-full text-left rounded px-2 py-1 text-sm ${roleIds.includes(Number(r.id)) ? 'bg-primary/10 text-foreground' : 'hover:bg-muted'}`}>
                      {roleLabel(r)}
                    </button>
                  ))}
                </div>
                {roleIds.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {roles.filter(r => roleIds.includes(Number(r.id))).map(r => (
                      <Badge key={r.id} variant="secondary" className="text-[10px]">{roleLabel(r)}</Badge>
                    ))}
                  </div>
                )}
              </div>
            )}

            {references.length > 0 && (
              <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-700 dark:text-amber-400">
                {tr('k_e2a53c7b18').replace('{n}', String(references.length))}
              </p>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>{tr('k_4d0b4688c7')}</Button>
          <Button onClick={save} disabled={busy || loading || !!loadError}>
            {busy ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : null}
            {tr('k_fadf24dbc5')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
