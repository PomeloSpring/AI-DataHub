import { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/toast';
import { Brain, Trash2, ArrowLeft, Pencil, X, Check, Eraser } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Spinner } from '@/components/ui/spinner';
import client from '../api/client';
import i18n from '@/i18n';

interface MemoryItem {
  id: number;
  slot_key: string;
  label: string;
  content: string;
  source: string;
  updated_at: string | null;
}

interface MemoryData {
  fixed_slots: MemoryItem[];
  floating_entries: MemoryItem[];
  fixed_slot_defs: { key: string; label: string }[];
  floating_defs: { key: string; label: string }[];
  floating_limit: number;
  content_max: number;
}

// 固定槽位定义（前端硬编码兜底，与后端 memory_tools.py 同源）；
// 显示名渲染期按 slot key 走语言包，未登记的 key 回退 label（兜底用 key 本身，不硬编码中文）
const FIXED_SLOT_DEFS = [
  { key: 'business_domain', label: 'business_domain' },
  { key: 'preferred_chart', label: 'preferred_chart' },
  { key: 'default_time_grain', label: 'default_time_grain' },
  { key: 'output_format', label: 'output_format' },
  { key: 'number_precision', label: 'number_precision' },
  { key: 'role_context', label: 'role_context' },
];

const slotLabel = (def: { key: string; label: string }): string => {
  const k = `workspace:memory.fixed.slot.${def.key}`;
  return i18n.exists(k) ? i18n.t(k as 'workspace:memory.fixed.slot.business_domain') : def.label;
};

export default function MemorySettings() {
  const { t } = useTranslation(['workspace', 'common']);
  const navigate = useNavigate();
  const location = useLocation();
  const homePath = (() => {
    const p = location.pathname;
    if (p.startsWith('/system')) return '/system';
    const m = p.match(/^\/(ws|ask)\/([^/]+)/);
    if (m) return m[1] === 'ws' ? `/${m[1]}/${m[2]}/chat` : `/${m[1]}/${m[2]}`;
    return '/data';
  })();

  const [data, setData] = useState<MemoryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editContent, setEditContent] = useState('');
  const [clearing, setClearing] = useState(false);

  useEffect(() => { loadMemory(); }, []);

  const loadMemory = async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/memory');
      setData(data);
    } catch {
      toast.error(t('memory.toast.loadFailed'));
    } finally {
      setLoading(false);
    }
  };

  const handleSaveSlot = async (slotKey: string, content: string) => {
    if (!content.trim()) { toast.error(t('memory.toast.contentRequired')); return; }
    try {
      await client.post('/memory', { slot_key: slotKey, content: content.trim() });
      toast.success(t('memory.toast.saved'));
      setEditingId(null);
      loadMemory();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('common:state.saveFailed'));
    }
  };

  const handleUpdate = async (id: number) => {
    if (!editContent.trim()) { toast.error(t('memory.toast.contentRequired')); return; }
    try {
      await client.put(`/memory/${id}`, { content: editContent.trim() });
      toast.success(t('memory.toast.updated'));
      setEditingId(null);
      loadMemory();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('profile.toast.saveFailed'));
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await client.delete(`/memory/${id}`);
      toast.success(t('memory.toast.deleted'));
      loadMemory();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('common:state.deleteFailed'));
    }
  };

  const handleClearAll = async () => {
    if (!window.confirm(t('memory.clearConfirm', { count: totalCount }))) return;
    setClearing(true);
    try {
      const items = [...(data?.fixed_slots || []), ...(data?.floating_entries || [])];
      await Promise.all(items.map(i => client.delete(`/memory/${i.id}`)));
      toast.success(t('memory.toast.cleared'));
      loadMemory();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('memory.toast.clearFailed'));
    } finally {
      setClearing(false);
    }
  };

  if (loading) {
    return <div className="flex items-center justify-center h-full"><Spinner size={32} /></div>;
  }

  const fixedMap = new Map((data?.fixed_slots || []).map(i => [i.slot_key, i]));
  const floatingCount = (data?.floating_entries || []).length;
  const floatingLimit = data?.floating_limit || 5;
  const contentMax = data?.content_max || 300;
  const totalCount = (data?.fixed_slots?.length || 0) + floatingCount;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Brain className="h-6 w-6" />
          {t('memory.title')}
        </h1>
        <div className="flex items-center gap-2">
          {totalCount > 0 && (
            <Button variant="outline" size="sm" className="text-destructive hover:text-destructive"
              onClick={handleClearAll} disabled={clearing}>
              <Eraser className="h-4 w-4 mr-1" />
              {clearing ? t('memory.clearing') : t('memory.clear')}
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => navigate(homePath)}>
            <ArrowLeft className="h-4 w-4 mr-1" />
            {t('profile.backHome')}
          </Button>
        </div>
      </div>

      <div className="max-w-[640px] space-y-6">
        <div className="rounded-lg border bg-muted/30 p-4 text-sm text-muted-foreground">
          <p className="font-medium text-foreground mb-1">{t('memory.introTitle')}</p>
          <p>{t('memory.introBody')}</p>
          <p className="mt-1">{t('memory.introNote')}</p>
        </div>

        {/* 固定槽位 */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Brain className="h-5 w-5" />
              {t('memory.fixed.title')}
            </CardTitle>
            <CardDescription>{t('memory.fixed.desc')}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {(data?.fixed_slot_defs || FIXED_SLOT_DEFS).map(def => {
              const item = fixedMap.get(def.key);
              const isEditing = editingId === (item?.id ?? -1000);
              return (
                <div key={def.key} className="flex items-center gap-3">
                  <div className="w-24 text-sm font-medium shrink-0">{slotLabel(def)}</div>
                  {isEditing ? (
                    <div className="flex-1 flex items-center gap-2">
                      <Input
                        value={editContent}
                        onChange={e => setEditContent(e.target.value)}
                        placeholder={t('memory.fixed.setPlaceholder', { label: slotLabel(def) })}
                        maxLength={contentMax}
                        className="flex-1"
                        autoFocus
                        onKeyDown={e => { if (e.key === 'Enter') handleSaveSlot(def.key, editContent); }}
                      />
                      <Button size="sm" variant="ghost" onClick={() => handleSaveSlot(def.key, editContent)}>
                        <Check className="h-4 w-4" />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>
                        <X className="h-4 w-4" />
                      </Button>
                    </div>
                  ) : (
                    <div className="flex-1 flex items-center gap-2">
                      <span className="text-sm text-muted-foreground flex-1">
                        {item?.content || <span className="italic">{t('memory.fixed.unset')}</span>}
                      </span>
                      {item?.source === 'auto' && <Badge variant="secondary" className="text-[10px]">{t('memory.aiLearned')}</Badge>}
                      <Button size="sm" variant="ghost"
                        onClick={() => {
                          setEditingId(item?.id ?? -1000);
                          setEditContent(item?.content || '');
                        }}>
                        <Pencil className="h-3 w-3" />
                      </Button>
                      {item && (
                        <Button size="sm" variant="ghost" onClick={() => handleDelete(item.id)}>
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </CardContent>
        </Card>

        {/* 浮动条目 */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Brain className="h-5 w-5" />
              {t('memory.floating.title')}
              <Badge variant="outline" className="ml-2">{floatingCount}/{floatingLimit}</Badge>
            </CardTitle>
            <CardDescription>{t('memory.floating.desc', { limit: floatingLimit })}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {floatingCount === 0 && (
              <p className="text-sm text-muted-foreground italic">
                {t('memory.floating.empty')}
              </p>
            )}
            {(data?.floating_entries || []).map(item => (
              <div key={item.id} className="flex items-start gap-3 p-3 rounded-lg border bg-muted/30">
                <Badge variant="secondary" className="mt-0.5 shrink-0 text-[10px]">{item.label}</Badge>
                {editingId === item.id ? (
                  <div className="flex-1 flex items-center gap-2">
                    <Input
                      value={editContent}
                      onChange={e => setEditContent(e.target.value)}
                      maxLength={contentMax}
                      className="flex-1"
                      autoFocus
                      onKeyDown={e => { if (e.key === 'Enter') handleUpdate(item.id); }}
                    />
                    <Button size="sm" variant="ghost" onClick={() => handleUpdate(item.id)}>
                      <Check className="h-4 w-4" />
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                ) : (
                  <>
                    <span className="flex-1 text-sm">{item.content}</span>
                    {item.source === 'auto' && <Badge variant="secondary" className="text-[10px] mt-0.5">{t('memory.aiLearned')}</Badge>}
                    <Button size="sm" variant="ghost"
                      onClick={() => { setEditingId(item.id); setEditContent(item.content); }}>
                      <Pencil className="h-3 w-3" />
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => handleDelete(item.id)}>
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
