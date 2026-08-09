import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import {
  Plus, Save, RotateCcw, History, Edit, Trash2, Search,
  ChevronDown, ChevronUp, Upload, Download, FileCode, FileText,
  Settings, Code, Play,
} from 'lucide-react';
import client from '@/api/client';

interface SkillTemplate {
  id: number;
  skill_key: string;
  skill_name: string;
  description: string;
  category: string;
  system_prompt: string;
  skill_config: any;
  tools_json: any;
  examples_json: any;
  version: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  created_by: string;
  change_log: string;
}

interface SkillVersion {
  id: number;
  skill_id: number;
  skill_key: string;
  version: number;
  system_prompt: string;
  skill_config: any;
  tools_json: any;
  examples_json: any;
  change_log: string;
  created_at: string;
  created_by: string;
  is_current: boolean;
}

interface SkillScript {
  id: number;
  skill_id: number;
  script_name: string;
  script_type: string;
  script_content: string;
  file_path: string;
}

const SKILL_CATEGORIES = [
  { value: 'nl2sql', label: 'NL2SQL', description: '自然语言转SQL' },
  { value: 'analysis', label: '数据分析', description: '数据统计和分析' },
  { value: 'chart', label: '图表生成', description: '数据可视化' },
  { value: 'correction', label: 'SQL纠错', description: 'SQL错误修复' },
  { value: 'prediction', label: '数据预测', description: '趋势预测' },
  { value: 'custom', label: '自定义', description: '自定义技能' },
];

export default function SkillsTemplateManager() {
  const [skills, setSkills] = useState<SkillTemplate[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<SkillTemplate | null>(null);
  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [scripts, setScripts] = useState<SkillScript[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchKey, setSearchKey] = useState('');
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [showEditDialog, setShowEditDialog] = useState(false);
  const [showVersionDialog, setShowVersionDialog] = useState(false);
  const [showScriptDialog, setShowScriptDialog] = useState(false);
  const [editingScript, setEditingScript] = useState<SkillScript | null>(null);

  // Form state
  const [form, setForm] = useState({
    skill_key: '',
    skill_name: '',
    description: '',
    category: 'custom',
    system_prompt: '',
    change_log: '',
  });

  // Script form state
  const [scriptForm, setScriptForm] = useState({
    script_name: '',
    script_type: 'python',
    script_content: '',
  });

  useEffect(() => {
    loadSkills();
  }, []);

  const loadSkills = async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/admin/skills');
      setSkills(Array.isArray(data) ? data : []);
    } catch (error) {
      console.error('Failed to load skills:', error);
      toast.error('加载技能列表失败');
    } finally {
      setLoading(false);
    }
  };

  const loadVersions = async (skillKey: string) => {
    try {
      const { data } = await client.get(`/admin/skills/${skillKey}/versions`);
      setVersions(Array.isArray(data) ? data : []);
    } catch (error) {
      console.error('Failed to load versions:', error);
    }
  };

  const loadScripts = async (skillId: number) => {
    try {
      const { data } = await client.get(`/admin/skills/${skillId}/scripts`);
      setScripts(Array.isArray(data) ? data : []);
    } catch (error) {
      console.error('Failed to load scripts:', error);
    }
  };

  const handleCreate = async () => {
    if (!form.skill_key || !form.skill_name) {
      toast.error('请填写技能标识和名称');
      return;
    }
    try {
      await client.post('/admin/skills', form);
      toast.success('技能创建成功');
      setShowCreateDialog(false);
      resetForm();
      loadSkills();
    } catch (error) {
      toast.error('创建失败');
    }
  };

  const handleUpdate = async () => {
    if (!selectedSkill) return;
    try {
      await client.put(`/admin/skills/${selectedSkill.skill_key}`, form);
      toast.success('技能更新成功');
      setShowEditDialog(false);
      resetForm();
      loadSkills();
    } catch (error) {
      toast.error('更新失败');
    }
  };

  const handleDelete = async (skillKey: string) => {
    if (!confirm('确定删除此技能？')) return;
    try {
      await client.delete(`/admin/skills/${skillKey}`);
      toast.success('技能已删除');
      if (selectedSkill?.skill_key === skillKey) {
        setSelectedSkill(null);
      }
      loadSkills();
    } catch (error) {
      toast.error('删除失败');
    }
  };

  const handleRollback = async (skillKey: string, version: number) => {
    if (!confirm(`确定回滚到版本 ${version}？`)) return;
    try {
      await client.post(`/admin/skills/${skillKey}/rollback`, { version });
      toast.success('回滚成功');
      loadSkills();
      loadVersions(skillKey);
    } catch (error) {
      toast.error('回滚失败');
    }
  };

  const handleSaveScript = async () => {
    if (!selectedSkill) return;
    try {
      if (editingScript) {
        await client.put(`/admin/skills/${selectedSkill.id}/scripts/${editingScript.id}`, scriptForm);
        toast.success('脚本更新成功');
      } else {
        await client.post(`/admin/skills/${selectedSkill.id}/scripts`, scriptForm);
        toast.success('脚本创建成功');
      }
      setShowScriptDialog(false);
      setEditingScript(null);
      resetScriptForm();
      loadScripts(selectedSkill.id);
    } catch (error) {
      toast.error('保存脚本失败');
    }
  };

  const handleDeleteScript = async (scriptId: number) => {
    if (!selectedSkill || !confirm('确定删除此脚本？')) return;
    try {
      await client.delete(`/admin/skills/${selectedSkill.id}/scripts/${scriptId}`);
      toast.success('脚本已删除');
      loadScripts(selectedSkill.id);
    } catch (error) {
      toast.error('删除失败');
    }
  };

  const handleExport = async (skillKey: string) => {
    try {
      const { data } = await client.get(`/admin/skills/${skillKey}/export`, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `${skillKey}.zip`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      toast.success('导出成功');
    } catch (error) {
      toast.error('导出失败');
    }
  };

  const handleImport = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    try {
      await client.post('/admin/skills/import', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      toast.success('导入成功');
      loadSkills();
    } catch (error) {
      toast.error('导入失败');
    }
  };

  const resetForm = () => {
    setForm({
      skill_key: '',
      skill_name: '',
      description: '',
      category: 'custom',
      system_prompt: '',
      change_log: '',
    });
  };

  const resetScriptForm = () => {
    setScriptForm({
      script_name: '',
      script_type: 'python',
      script_content: '',
    });
  };

  const openEditDialog = (skill: SkillTemplate) => {
    setSelectedSkill(skill);
    setForm({
      skill_key: skill.skill_key,
      skill_name: skill.skill_name,
      description: skill.description || '',
      category: skill.category || 'custom',
      system_prompt: skill.system_prompt || '',
      change_log: '',
    });
    setShowEditDialog(true);
  };

  const openVersionDialog = (skill: SkillTemplate) => {
    setSelectedSkill(skill);
    loadVersions(skill.skill_key);
    setShowVersionDialog(true);
  };

  const openScriptDialog = (skill: SkillTemplate) => {
    setSelectedSkill(skill);
    loadScripts(skill.id);
    setShowScriptDialog(true);
  };

  const openNewScriptDialog = () => {
    setEditingScript(null);
    resetScriptForm();
    setScriptForm({ ...scriptForm, script_name: '', script_content: '' });
  };

  const openEditScriptDialog = (script: SkillScript) => {
    setEditingScript(script);
    setScriptForm({
      script_name: script.script_name,
      script_type: script.script_type,
      script_content: script.script_content,
    });
  };

  const filteredSkills = skills.filter(s =>
    s.skill_key.includes(searchKey) ||
    s.skill_name.includes(searchKey) ||
    s.description?.includes(searchKey)
  );

  return (
    <div className="h-full flex gap-4">
      {/* Left: Skill List */}
      <div className="w-80 flex flex-col border rounded-lg bg-card">
        <div className="p-4 border-b space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">Skills 模板</h2>
            <div className="flex gap-1">
              <label>
                <input type="file" accept=".zip" className="hidden" onChange={handleImport} />
                <Button variant="ghost" size="icon" className="h-8 w-8" asChild>
                  <span><Upload className="h-4 w-4" /></span>
                </Button>
              </label>
              <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => { resetForm(); setShowCreateDialog(true); }}>
                <Plus className="h-4 w-4" />
              </Button>
            </div>
          </div>
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="搜索技能..."
              value={searchKey}
              onChange={(e) => setSearchKey(e.target.value)}
              className="pl-8 h-8 text-xs"
            />
          </div>
        </div>
        <div className="flex-1 overflow-auto p-2 space-y-1">
          {loading ? (
            <div className="text-center py-8 text-muted-foreground text-sm">加载中...</div>
          ) : filteredSkills.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground text-sm">
              {searchKey ? '未找到匹配的技能' : '暂无技能'}
            </div>
          ) : (
            filteredSkills.map(skill => (
              <div
                key={skill.id}
                className={`p-3 rounded-lg cursor-pointer transition-colors ${
                  selectedSkill?.id === skill.id ? 'bg-primary/10 border border-primary/20' : 'hover:bg-muted/50'
                }`}
                onClick={() => setSelectedSkill(skill)}
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-sm truncate">{skill.skill_name}</div>
                    <div className="text-xs text-muted-foreground truncate mt-0.5">{skill.skill_key}</div>
                  </div>
                  <div className="flex gap-0.5 ml-2">
                    <Button variant="ghost" size="icon" className="h-6 w-6" onClick={(e) => { e.stopPropagation(); openEditDialog(skill); }}>
                      <Edit className="h-3 w-3" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-6 w-6" onClick={(e) => { e.stopPropagation(); handleDelete(skill.skill_key); }}>
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
                <div className="flex items-center gap-2 mt-2">
                  <span className="text-xs bg-muted px-1.5 py-0.5 rounded">
                    {SKILL_CATEGORIES.find(c => c.value === skill.category)?.label || skill.category}
                  </span>
                  <span className="text-xs text-muted-foreground">v{skill.version}</span>
                  {!skill.is_active && <span className="text-xs text-destructive">已禁用</span>}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Right: Skill Detail */}
      <div className="flex-1 flex flex-col border rounded-lg bg-card">
        {selectedSkill ? (
          <>
            <div className="p-4 border-b">
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="text-lg font-semibold">{selectedSkill.skill_name}</h2>
                  <p className="text-sm text-muted-foreground mt-1">{selectedSkill.description || '暂无描述'}</p>
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={() => openVersionDialog(selectedSkill)}>
                    <History className="h-4 w-4 mr-1" />
                    版本历史
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => openScriptDialog(selectedSkill)}>
                    <Code className="h-4 w-4 mr-1" />
                    脚本管理
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => handleExport(selectedSkill.skill_key)}>
                    <Download className="h-4 w-4 mr-1" />
                    导出
                  </Button>
                </div>
              </div>
              <div className="flex items-center gap-4 mt-3">
                <span className="text-xs bg-muted px-2 py-1 rounded">
                  {SKILL_CATEGORIES.find(c => c.value === selectedSkill.category)?.label || selectedSkill.category}
                </span>
                <span className="text-xs text-muted-foreground">版本 {selectedSkill.version}</span>
                <span className="text-xs text-muted-foreground">
                  更新于 {new Date(selectedSkill.updated_at).toLocaleString()}
                </span>
              </div>
            </div>
            <div className="flex-1 overflow-auto p-4 space-y-4">
              <div>
                <Label className="text-sm font-medium">系统提示词</Label>
                <div className="mt-2 p-4 bg-muted/30 rounded-lg text-sm whitespace-pre-wrap font-mono">
                  {selectedSkill.system_prompt || '暂无系统提示词'}
                </div>
              </div>
              {selectedSkill.tools_json && (
                <div>
                  <Label className="text-sm font-medium">工具定义</Label>
                  <div className="mt-2 p-4 bg-muted/30 rounded-lg text-sm font-mono">
                    <pre>{JSON.stringify(selectedSkill.tools_json, null, 2)}</pre>
                  </div>
                </div>
              )}
              {selectedSkill.examples_json && (
                <div>
                  <Label className="text-sm font-medium">示例</Label>
                  <div className="mt-2 p-4 bg-muted/30 rounded-lg text-sm font-mono">
                    <pre>{JSON.stringify(selectedSkill.examples_json, null, 2)}</pre>
                  </div>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            <div className="text-center">
              <FileCode className="h-12 w-12 mx-auto mb-3 opacity-30" />
              <p className="text-sm">选择一个技能查看详情</p>
              <p className="text-xs mt-1">或点击左上角 + 创建新技能</p>
            </div>
          </div>
        )}
      </div>

      {/* Create Dialog */}
      <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>创建技能</DialogTitle>
            <DialogDescription>创建一个新的 Skill 模板</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>技能标识 (英文)</Label>
                <Input value={form.skill_key} onChange={e => setForm({ ...form, skill_key: e.target.value })} placeholder="my_skill" />
              </div>
              <div className="space-y-2">
                <Label>技能名称</Label>
                <Input value={form.skill_name} onChange={e => setForm({ ...form, skill_name: e.target.value })} placeholder="我的技能" />
              </div>
            </div>
            <div className="space-y-2">
              <Label>描述</Label>
              <Input value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="技能描述" />
            </div>
            <div className="space-y-2">
              <Label>类别</Label>
              <Select value={form.category} onValueChange={v => setForm({ ...form, category: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {SKILL_CATEGORIES.map(cat => (
                    <SelectItem key={cat.value} value={cat.value}>{cat.label} - {cat.description}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>系统提示词</Label>
              <Textarea
                value={form.system_prompt}
                onChange={e => setForm({ ...form, system_prompt: e.target.value })}
                rows={8}
                placeholder="输入系统提示词..."
              />
            </div>
            <div className="space-y-2">
              <Label>变更说明</Label>
              <Input value={form.change_log} onChange={e => setForm({ ...form, change_log: e.target.value })} placeholder="初始版本" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setShowCreateDialog(false)}>取消</Button>
            <Button onClick={handleCreate}>创建</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit Dialog */}
      <Dialog open={showEditDialog} onOpenChange={setShowEditDialog}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>编辑技能</DialogTitle>
            <DialogDescription>修改技能配置（将创建新版本）</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>技能名称</Label>
              <Input value={form.skill_name} onChange={e => setForm({ ...form, skill_name: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label>描述</Label>
              <Input value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label>类别</Label>
              <Select value={form.category} onValueChange={v => setForm({ ...form, category: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {SKILL_CATEGORIES.map(cat => (
                    <SelectItem key={cat.value} value={cat.value}>{cat.label} - {cat.description}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>系统提示词</Label>
              <Textarea
                value={form.system_prompt}
                onChange={e => setForm({ ...form, system_prompt: e.target.value })}
                rows={8}
              />
            </div>
            <div className="space-y-2">
              <Label>变更说明</Label>
              <Input value={form.change_log} onChange={e => setForm({ ...form, change_log: e.target.value })} placeholder="说明本次修改内容" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setShowEditDialog(false)}>取消</Button>
            <Button onClick={handleUpdate}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Version History Dialog */}
      <Dialog open={showVersionDialog} onOpenChange={setShowVersionDialog}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>版本历史</DialogTitle>
            <DialogDescription>{selectedSkill?.skill_name} 的版本记录</DialogDescription>
          </DialogHeader>
          <div className="max-h-96 overflow-auto space-y-2">
            {versions.map(v => (
              <div key={v.id} className="flex items-center justify-between p-3 border rounded-lg">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm">版本 {v.version}</span>
                    {v.is_current && <span className="text-xs bg-primary/10 text-primary px-1.5 py-0.5 rounded">当前</span>}
                  </div>
                  <div className="text-xs text-muted-foreground mt-1">{v.change_log || '无变更说明'}</div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {v.created_by} · {new Date(v.created_at).toLocaleString()}
                  </div>
                </div>
                {!v.is_current && (
                  <Button variant="outline" size="sm" onClick={() => handleRollback(v.skill_key, v.version)}>
                    <RotateCcw className="h-3 w-3 mr-1" />
                    回滚
                  </Button>
                )}
              </div>
            ))}
            {versions.length === 0 && (
              <div className="text-center py-8 text-muted-foreground text-sm">暂无版本记录</div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Scripts Dialog */}
      <Dialog open={showScriptDialog} onOpenChange={setShowScriptDialog}>
        <DialogContent className="max-w-4xl max-h-[80vh]">
          <DialogHeader>
            <DialogTitle>脚本管理</DialogTitle>
            <DialogDescription>{selectedSkill?.skill_name} 的关联脚本</DialogDescription>
          </DialogHeader>
          <div className="flex gap-4 overflow-hidden">
            {/* Script List */}
            <div className="w-64 flex flex-col border rounded-lg">
              <div className="p-3 border-b">
                <Button size="sm" className="w-full" onClick={openNewScriptDialog}>
                  <Plus className="h-4 w-4 mr-1" />
                  新建脚本
                </Button>
              </div>
              <div className="flex-1 overflow-auto p-2 space-y-1">
                {scripts.map(script => (
                  <div
                    key={script.id}
                    className="flex items-center justify-between p-2 rounded hover:bg-muted/50 cursor-pointer"
                    onClick={() => openEditScriptDialog(script)}
                  >
                    <div className="flex items-center gap-2">
                      <FileCode className="h-4 w-4 text-muted-foreground" />
                      <span className="text-sm truncate">{script.script_name}</span>
                    </div>
                    <Button variant="ghost" size="icon" className="h-5 w-5" onClick={(e) => { e.stopPropagation(); handleDeleteScript(script.id); }}>
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                ))}
                {scripts.length === 0 && (
                  <div className="text-center py-4 text-muted-foreground text-xs">暂无脚本</div>
                )}
              </div>
            </div>
            {/* Script Editor */}
            <div className="flex-1 flex flex-col space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label className="text-xs">脚本名称</Label>
                  <Input
                    value={scriptForm.script_name}
                    onChange={e => setScriptForm({ ...scriptForm, script_name: e.target.value })}
                    placeholder="my_script.py"
                    className="h-8 text-xs"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">脚本类型</Label>
                  <Select value={scriptForm.script_type} onValueChange={v => setScriptForm({ ...scriptForm, script_type: v })}>
                    <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="python">Python</SelectItem>
                      <SelectItem value="shell">Shell</SelectItem>
                      <SelectItem value="javascript">JavaScript</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="flex-1 flex flex-col space-y-1.5">
                <Label className="text-xs">脚本内容</Label>
                <Textarea
                  value={scriptForm.script_content}
                  onChange={e => setScriptForm({ ...scriptForm, script_content: e.target.value })}
                  className="flex-1 font-mono text-xs"
                  placeholder="# 输入脚本内容..."
                />
              </div>
              <div className="flex justify-end gap-2">
                <Button variant="ghost" size="sm" onClick={() => { setEditingScript(null); resetScriptForm(); }}>清空</Button>
                <Button size="sm" onClick={handleSaveScript}>
                  <Save className="h-4 w-4 mr-1" />
                  {editingScript ? '更新' : '保存'}
                </Button>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
