import { useState, useEffect } from 'react';
import {
  Plus, Edit2, Trash2, Eye, Search, Sparkles, BookOpen, Lock, FileCode,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Spinner } from '@/components/ui/spinner';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { skillApi, type Skill, type SkillUpsert } from '@/api/skill';

// ── Category Options ───────────────────────────────────────────────

const CATEGORIES = [
  { value: 'nl2sql', label: 'NL2SQL' },
  { value: 'analysis', label: '数据分析' },
  { value: 'chart', label: '图表生成' },
  { value: 'correction', label: 'SQL纠错' },
  { value: 'prediction', label: '数据预测' },
  { value: 'custom', label: '自定义' },
];

const catLabel = (c: string) => CATEGORIES.find((x) => x.value === c)?.label || c || '未分类';

// ── Main Component ─────────────────────────────────────────────────

export default function SkillsManager() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [filterBuiltin, setFilterBuiltin] = useState<'all' | 'builtin' | 'custom'>('all');

  const [viewing, setViewing] = useState<Skill | null>(null);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Skill | null>(null);
  const [deleting, setDeleting] = useState<Skill | null>(null);

  useEffect(() => {
    loadSkills();
  }, []);

  const loadSkills = async () => {
    setLoading(true);
    try {
      const { data } = await skillApi.list();
      setSkills(Array.isArray(data) ? data : []);
    } catch (error: any) {
      // 接口无数据 / 未命中(404) 不弹错,仅展示空状态;只有真正的服务端错误才提示
      const status = error?.response?.status;
      console.error('Failed to load skills:', error);
      setSkills([]);
      if (status && status >= 500) {
        toast.error('加载技能列表失败');
      }
    } finally {
      setLoading(false);
    }
  };

  const openView = async (name: string) => {
    try {
      const { data } = await skillApi.get(name);
      setViewing(data);
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '加载技能详情失败');
    }
  };

  const handleDelete = async () => {
    if (!deleting) return;
    try {
      await skillApi.delete(deleting.name);
      toast.success(`已删除技能 "${deleting.display_name || deleting.name}"`);
      setDeleting(null);
      loadSkills();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '删除失败');
    }
  };

  const filtered = skills.filter((s) => {
    if (filterBuiltin === 'builtin' && !s.is_builtin) return false;
    if (filterBuiltin === 'custom' && s.is_builtin) return false;
    const kw = search.trim().toLowerCase();
    if (!kw) return true;
    return (
      s.name.toLowerCase().includes(kw) ||
      (s.display_name || '').toLowerCase().includes(kw) ||
      (s.description || '').toLowerCase().includes(kw)
    );
  });

  const builtinCount = skills.filter((s) => s.is_builtin).length;
  const customCount = skills.length - builtinCount;

  return (
    <div className="p-6 w-full">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">技能配置</h1>
          <p className="text-muted-foreground mt-1">
            管理 Agent 技能（Qoder「文件夹 + SKILL.md」规范），系统内置 {builtinCount} 个、自定义 {customCount} 个，可在 Waker 中勾选绑定
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus className="h-4 w-4 mr-2" />
          新建技能
        </Button>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3 mb-4">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            className="pl-9"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索技能名称/描述"
          />
        </div>
        <div className="flex items-center gap-1">
          {(['all', 'builtin', 'custom'] as const).map((k) => (
            <Button
              key={k}
              size="sm"
              variant={filterBuiltin === k ? 'default' : 'outline'}
              onClick={() => setFilterBuiltin(k)}
            >
              {k === 'all' ? '全部' : k === 'builtin' ? '内置' : '自定义'}
            </Button>
          ))}
        </div>
      </div>

      {/* List */}
      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner size={32} />
        </div>
      ) : filtered.length === 0 ? (
        <div className="border rounded-lg p-12 text-center">
          <Sparkles className="h-12 w-12 mx-auto mb-4 text-muted-foreground" />
          <h3 className="text-lg font-medium mb-2">暂无技能</h3>
          <p className="text-muted-foreground mb-4">新建一个自定义技能，或调整筛选条件</p>
          <Button onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4 mr-2" />
            新建技能
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {filtered.map((s) => (
            <div key={s.name} className="border rounded-lg p-4 flex flex-col gap-2 hover:bg-muted/30 transition-colors">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold truncate">{s.display_name || s.name}</span>
                    {s.is_builtin ? (
                      <Badge className="bg-indigo-100 text-indigo-800">
                        <Lock className="h-3 w-3 mr-1" />内置
                      </Badge>
                    ) : (
                      <Badge variant="secondary">自定义</Badge>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground font-mono truncate">{s.name}</div>
                </div>
                {s.format === 'skill_md' && (
                  <FileCode className="h-4 w-4 text-muted-foreground shrink-0" aria-label="SKILL.md" />
                )}
              </div>

              <p className="text-sm text-muted-foreground line-clamp-2 flex-1">
                {s.description || '（无描述）'}
              </p>

              <div className="flex items-center gap-2">
                <Badge variant="outline">{catLabel(s.category)}</Badge>
              </div>

              <div className="flex items-center justify-end gap-1 pt-1">
                <Button variant="ghost" size="sm" onClick={() => openView(s.name)} title="查看">
                  <Eye className="h-4 w-4" />
                </Button>
                {!s.is_builtin && (
                  <>
                    <Button variant="ghost" size="sm" onClick={() => setEditing(s)} title="编辑">
                      <Edit2 className="h-4 w-4" />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setDeleting(s)} title="删除">
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* View Dialog */}
      {viewing && (
        <Dialog open onOpenChange={(v) => { if (!v) setViewing(null); }}>
          <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <BookOpen className="h-5 w-5" />
                {viewing.display_name || viewing.name}
                {viewing.is_builtin && <Badge className="bg-indigo-100 text-indigo-800">内置·只读</Badge>}
              </DialogTitle>
              <DialogDescription className="font-mono text-xs">{viewing.name}</DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              {viewing.description && (
                <div>
                  <div className="text-sm font-medium mb-1">描述</div>
                  <p className="text-sm text-muted-foreground">{viewing.description}</p>
                </div>
              )}
              <div>
                <div className="text-sm font-medium mb-1">SKILL.md 正文</div>
                <pre className="whitespace-pre-wrap break-words text-xs bg-muted/50 rounded-lg p-3 max-h-[50vh] overflow-y-auto">
                  {viewing.markdown || viewing.system_prompt || '（无内容）'}
                </pre>
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setViewing(null)}>关闭</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

      {/* Create / Edit Dialog */}
      {(creating || editing) && (
        <SkillFormDialog
          skill={editing}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={() => { setCreating(false); setEditing(null); loadSkills(); }}
        />
      )}

      {/* Delete Confirmation */}
      <Dialog open={!!deleting} onOpenChange={() => setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除自定义技能 "{deleting?.display_name || deleting?.name}" 吗？该操作会移除其 SKILL.md 文件夹，不可撤销。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleting(null)}>取消</Button>
            <Button variant="destructive" onClick={handleDelete}>删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Create / Edit Dialog ───────────────────────────────────────────

function SkillFormDialog({
  skill,
  onClose,
  onSaved,
}: {
  skill: Skill | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = !!skill;
  const [form, setForm] = useState<SkillUpsert>({
    name: skill?.name || '',
    display_name: skill?.display_name || '',
    description: skill?.description || '',
    category: skill?.category || 'custom',
    system_prompt: '',
  });
  const [saving, setSaving] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);

  useEffect(() => {
    if (!skill) return;
    // 拉取详情填充正文
    setLoadingDetail(true);
    skillApi.get(skill.name)
      .then(({ data }) => {
        setForm((prev) => ({
          ...prev,
          display_name: data.display_name || prev.display_name,
          description: data.description || prev.description,
          category: data.category || prev.category,
          system_prompt: data.markdown || data.system_prompt || '',
        }));
      })
      .catch(() => toast.error('加载技能正文失败'))
      .finally(() => setLoadingDetail(false));
  }, [skill]);

  const handleSave = async () => {
    if (!form.name.trim()) {
      toast.error('请输入技能标识名');
      return;
    }
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(form.name)) {
      toast.error('标识名仅允许字母/数字/下划线/连字符');
      return;
    }
    setSaving(true);
    try {
      if (isEdit && skill) {
        await skillApi.update(skill.name, form);
        toast.success('技能已更新');
      } else {
        await skillApi.create(form);
        toast.success('技能已创建');
      }
      onSaved();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? '编辑技能' : '新建技能'}</DialogTitle>
          <DialogDescription>
            技能将按 Qoder 规范保存为 config/skills/&#123;name&#125;/SKILL.md（frontmatter + 正文）
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <Label>标识名 (name)</Label>
            <Input
              value={form.name}
              disabled={isEdit}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="my-custom-skill"
              className="font-mono"
            />
            <p className="text-xs text-muted-foreground">文件夹名，创建后不可修改</p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label>显示名</Label>
              <Input
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                placeholder="我的技能"
              />
            </div>
            <div className="space-y-2">
              <Label>分类</Label>
              <Select
                value={form.category}
                onValueChange={(v) => setForm({ ...form, category: v })}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {CATEGORIES.map((c) => (
                    <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-2">
            <Label>描述</Label>
            <Textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="一句话说明该技能的用途"
              rows={2}
            />
          </div>
          <div className="space-y-2">
            <Label>SKILL.md 正文 (系统提示词)</Label>
            <Textarea
              value={form.system_prompt}
              onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
              placeholder="# 技能说明&#10;&#10;描述该技能何时使用、如何做…"
              rows={12}
              className="font-mono text-xs"
              disabled={loadingDetail}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={handleSave} disabled={saving || loadingDetail}>
            {saving ? '保存中...' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
