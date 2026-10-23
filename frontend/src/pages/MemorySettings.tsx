import { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { toast } from '@/lib/toast';
import { Brain, Trash2, ArrowLeft, Pencil, X, Check, Eraser } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Spinner } from '@/components/ui/spinner';
import client from '../api/client';

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

// 固定槽位定义（前端硬编码兜底，与后端 memory_tools.py 同源）
const FIXED_SLOT_DEFS = [
  { key: 'business_domain', label: '业务域' },
  { key: 'preferred_chart', label: '默认图表' },
  { key: 'default_time_grain', label: '时间粒度' },
  { key: 'output_format', label: '输出格式' },
  { key: 'number_precision', label: '数字精度' },
  { key: 'role_context', label: '角色背景' },
];

export default function MemorySettings() {
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
      toast.error('加载记忆失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSaveSlot = async (slotKey: string, content: string) => {
    if (!content.trim()) { toast.error('内容不能为空'); return; }
    try {
      await client.post('/memory', { slot_key: slotKey, content: content.trim() });
      toast.success('记忆已保存');
      setEditingId(null);
      loadMemory();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    }
  };

  const handleUpdate = async (id: number) => {
    if (!editContent.trim()) { toast.error('内容不能为空'); return; }
    try {
      await client.put(`/memory/${id}`, { content: editContent.trim() });
      toast.success('记忆已更新');
      setEditingId(null);
      loadMemory();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '更新失败');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await client.delete(`/memory/${id}`);
      toast.success('记忆已删除');
      loadMemory();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  const handleClearAll = async () => {
    if (!window.confirm(`确定清空全部 ${totalCount} 条记忆？此操作不可撤销。`)) return;
    setClearing(true);
    try {
      const items = [...(data?.fixed_slots || []), ...(data?.floating_entries || [])];
      await Promise.all(items.map(i => client.delete(`/memory/${i.id}`)));
      toast.success('记忆已清空');
      loadMemory();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '清空失败');
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
          我的记忆
        </h1>
        <div className="flex items-center gap-2">
          {totalCount > 0 && (
            <Button variant="outline" size="sm" className="text-destructive hover:text-destructive"
              onClick={handleClearAll} disabled={clearing}>
              <Eraser className="h-4 w-4 mr-1" />
              {clearing ? '清空中...' : '清空记忆'}
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => navigate(homePath)}>
            <ArrowLeft className="h-4 w-4 mr-1" />
            返回主页
          </Button>
        </div>
      </div>

      <div className="max-w-[640px] space-y-6">
        <div className="rounded-lg border bg-muted/30 p-4 text-sm text-muted-foreground">
          <p className="font-medium text-foreground mb-1">记忆由 AI 在对话中自动学习保存</p>
          <p>当你说"记住我喜欢用柱状图"或纠正口径时，AI 会自动保存。此页面用于查看、修改和清理已有记忆。</p>
          <p className="mt-1">记忆只保存稳定偏好/口径/习惯，不会保存会话上下文。</p>
        </div>

        {/* 固定槽位 */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Brain className="h-5 w-5" />
              固定偏好
            </CardTitle>
            <CardDescription>每项一个值，保存即覆盖旧值</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {(data?.fixed_slot_defs || FIXED_SLOT_DEFS).map(def => {
              const item = fixedMap.get(def.key);
              const isEditing = editingId === (item?.id ?? -1000);
              return (
                <div key={def.key} className="flex items-center gap-3">
                  <div className="w-24 text-sm font-medium shrink-0">{def.label}</div>
                  {isEditing ? (
                    <div className="flex-1 flex items-center gap-2">
                      <Input
                        value={editContent}
                        onChange={e => setEditContent(e.target.value)}
                        placeholder={`设置${def.label}`}
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
                        {item?.content || <span className="italic">未设置</span>}
                      </span>
                      {item?.source === 'auto' && <Badge variant="secondary" className="text-[10px]">AI 学习</Badge>}
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
              纠错与习惯
              <Badge variant="outline" className="ml-2">{floatingCount}/{floatingLimit}</Badge>
            </CardTitle>
            <CardDescription>AI 在对话中自动学习的口径纠错与工作习惯，上限 {floatingLimit} 条</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {floatingCount === 0 && (
              <p className="text-sm text-muted-foreground italic">
                暂无记忆。在对话中告诉 AI 你的口径偏好或纠错，它会自动记录在这里。
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
                    {item.source === 'auto' && <Badge variant="secondary" className="text-[10px] mt-0.5">AI 学习</Badge>}
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
