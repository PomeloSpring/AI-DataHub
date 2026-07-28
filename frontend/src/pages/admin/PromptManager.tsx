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
  Plus,
  Save,
  RotateCcw,
  History,
  Edit,
  Trash2,
  Search,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import client from '@/api/client';

interface Prompt {
  id: number;
  prompt_key: string;
  prompt_name: string;
  system_prompt: string;
  user_prompt_template: string;
  description: string;
  version: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  created_by: string;
  change_log: string;
}

interface PromptVersion {
  id: number;
  prompt_id: number;
  prompt_key: string;
  version: number;
  system_prompt: string;
  user_prompt_template: string;
  change_log: string;
  created_at: string;
  created_by: string;
  is_current: boolean;
}

// Prompt key options
const PROMPT_KEYS = [
  { value: 'metadata_supplement', label: '元数据补充分析' },
  { value: 'sql_generation', label: 'SQL生成' },
  { value: 'result_analysis', label: '结果分析' },
  { value: 'llm_analysis', label: 'LLM分析' },
  { value: 'chart_generation', label: '图表生成' },
];

export default function PromptManager() {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [selectedPrompt, setSelectedPrompt] = useState<Prompt | null>(null);
  const [versions, setVersions] = useState<PromptVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchKey, setSearchKey] = useState('');
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [showEditDialog, setShowEditDialog] = useState(false);
  const [showVersionDialog, setShowVersionDialog] = useState(false);
  const [expandedPrompt, setExpandedPrompt] = useState<string | null>(null);
  const [createForm, setCreateForm] = useState({
    prompt_key: '',
    prompt_name: '',
    system_prompt: '',
    user_prompt_template: '',
    description: '',
    change_log: '',
  });
  const [editForm, setEditForm] = useState({
    prompt_name: '',
    system_prompt: '',
    user_prompt_template: '',
    description: '',
    change_log: '',
  });

  // Load prompts
  const loadPrompts = async () => {
    setLoading(true);
    try {
      const res = await client.get('/admin/prompts', {
        params: { search: searchKey, size: 100 },
      });
      setPrompts(res.data.items || []);
    } catch (err: any) {
      toast.error('加载Prompt失败: ' + (err.response?.data?.detail || err.message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadPrompts();
  }, [searchKey]);

  // Load versions for a prompt
  const loadVersions = async (promptKey: string) => {
    try {
      const res = await client.get(`/admin/prompts/${promptKey}/versions`);
      setVersions(res.data.items || []);
    } catch (err: any) {
      toast.error('加载版本历史失败: ' + (err.response?.data?.detail || err.message));
    }
  };

  // Create new prompt
  const createPrompt = async () => {
    if (!createForm.prompt_key || !createForm.prompt_name) {
      toast.error('请填写Prompt Key和名称');
      return;
    }
    try {
      await client.post('/admin/prompts', createForm);
      toast.success('Prompt创建成功');
      setShowCreateDialog(false);
      setCreateForm({
        prompt_key: '',
        prompt_name: '',
        system_prompt: '',
        user_prompt_template: '',
        description: '',
        change_log: '',
      });
      loadPrompts();
    } catch (err: any) {
      toast.error('创建失败: ' + (err.response?.data?.detail || err.message));
    }
  };

  // Update prompt (create new version)
  const updatePrompt = async () => {
    if (!selectedPrompt) return;
    try {
      await client.put(`/admin/prompts/${selectedPrompt.prompt_key}`, editForm);
      toast.success('Prompt已更新（新版本已创建）');
      setShowEditDialog(false);
      loadPrompts();
    } catch (err: any) {
      toast.error('更新失败: ' + (err.response?.data?.detail || err.message));
    }
  };

  // Rollback to version
  const rollbackToVersion = async (version: number) => {
    if (!selectedPrompt) return;
    if (!confirm(`确定要回退到版本 ${version} 吗？这将创建一个新版本。`)) {
      return;
    }
    try {
      await client.post(`/admin/prompts/${selectedPrompt.prompt_key}/rollback?version=${version}`);
      toast.success(`已回退到版本 ${version}`);
      setShowVersionDialog(false);
      loadPrompts();
    } catch (err: any) {
      toast.error('回退失败: ' + (err.response?.data?.detail || err.message));
    }
  };

  // Open edit dialog
  const openEditDialog = (prompt: Prompt) => {
    setSelectedPrompt(prompt);
    setEditForm({
      prompt_name: prompt.prompt_name,
      system_prompt: prompt.system_prompt,
      user_prompt_template: prompt.user_prompt_template,
      description: prompt.description,
      change_log: '',
    });
    setShowEditDialog(true);
  };

  // Open version dialog
  const openVersionDialog = async (prompt: Prompt) => {
    setSelectedPrompt(prompt);
    await loadVersions(prompt.prompt_key);
    setShowVersionDialog(true);
  };

  // Group prompts by key
  const groupedPrompts = prompts.reduce((acc, prompt) => {
    if (!acc[prompt.prompt_key]) {
      acc[prompt.prompt_key] = [];
    }
    acc[prompt.prompt_key].push(prompt);
    return acc;
  }, {} as Record<string, Prompt[]>);

  // Get current active version for each key
  const activePrompts = Object.entries(groupedPrompts).map(([key, versions]) => {
    const active = versions.find(v => v.is_active) || versions[0];
    return { key, active, allVersions: versions };
  });

  return (
    <div className="w-full">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Prompt管理</h1>
          <p className="text-gray-500">管理LLM提示词模板，支持版本管理和回退</p>
        </div>
        <Button onClick={() => setShowCreateDialog(true)}>
          <Plus className="w-4 h-4 mr-2" />
          新建Prompt
        </Button>
      </div>

      {/* Search */}
      <div className="mb-6">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4" />
          <Input
            placeholder="搜索Prompt..."
            value={searchKey}
            onChange={e => setSearchKey(e.target.value)}
            className="pl-10"
          />
        </div>
      </div>

      {/* Prompt list */}
      <div className="space-y-4">
        {activePrompts.map(({ key, active, allVersions }) => (
          <div key={key} className="border rounded-lg overflow-hidden">
            {/* Header */}
            <div
              className="flex items-center justify-between p-4 bg-gray-50 cursor-pointer hover:bg-gray-100"
              onClick={() => setExpandedPrompt(expandedPrompt === key ? null : key)}
            >
              <div className="flex items-center gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">{active.prompt_name}</span>
                    <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded">
                      v{active.version}
                    </span>
                    <span className="text-xs bg-gray-200 text-gray-600 px-2 py-0.5 rounded">
                      {key}
                    </span>
                  </div>
                  <p className="text-sm text-gray-500 mt-1">{active.description}</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={e => {
                    e.stopPropagation();
                    openEditDialog(active);
                  }}
                >
                  <Edit className="w-4 h-4 mr-1" />
                  编辑
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={e => {
                    e.stopPropagation();
                    openVersionDialog(active);
                  }}
                >
                  <History className="w-4 h-4 mr-1" />
                  版本历史
                </Button>
                {expandedPrompt === key ? (
                  <ChevronUp className="w-5 h-5 text-gray-400" />
                ) : (
                  <ChevronDown className="w-5 h-5 text-gray-400" />
                )}
              </div>
            </div>

            {/* Expanded content */}
            {expandedPrompt === key && (
              <div className="p-4 border-t">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <Label className="text-sm font-medium text-gray-700">System Prompt</Label>
                    <div className="mt-1 p-3 bg-gray-50 rounded-md text-sm font-mono whitespace-pre-wrap max-h-60 overflow-auto">
                      {active.system_prompt || '(空)'}
                    </div>
                  </div>
                  <div>
                    <Label className="text-sm font-medium text-gray-700">User Prompt Template</Label>
                    <div className="mt-1 p-3 bg-gray-50 rounded-md text-sm font-mono whitespace-pre-wrap max-h-60 overflow-auto">
                      {active.user_prompt_template || '(空)'}
                    </div>
                  </div>
                </div>
                <div className="mt-4 text-xs text-gray-500">
                  <span>创建人: {active.created_by}</span>
                  <span className="mx-2">|</span>
                  <span>更新时间: {active.updated_at}</span>
                  {active.change_log && (
                    <>
                      <span className="mx-2">|</span>
                      <span>变更说明: {active.change_log}</span>
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}

        {activePrompts.length === 0 && (
          <div className="text-center py-12 text-gray-500">
            {loading ? '加载中...' : '暂无Prompt配置'}
          </div>
        )}
      </div>

      {/* Create dialog */}
      <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>新建Prompt</DialogTitle>
            <DialogDescription>创建新的Prompt模板</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label>Prompt Key *</Label>
                <Select
                  value={createForm.prompt_key}
                  onValueChange={value => setCreateForm({ ...createForm, prompt_key: value })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="选择Prompt类型" />
                  </SelectTrigger>
                  <SelectContent>
                    {PROMPT_KEYS.map(pk => (
                      <SelectItem key={pk.value} value={pk.value}>
                        {pk.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label>Prompt名称 *</Label>
                <Input
                  value={createForm.prompt_name}
                  onChange={e => setCreateForm({ ...createForm, prompt_name: e.target.value })}
                  placeholder="输入Prompt名称"
                />
              </div>
            </div>
            <div>
              <Label>描述</Label>
              <Input
                value={createForm.description}
                onChange={e => setCreateForm({ ...createForm, description: e.target.value })}
                placeholder="输入描述"
              />
            </div>
            <div>
              <Label>System Prompt</Label>
              <Textarea
                value={createForm.system_prompt}
                onChange={e => setCreateForm({ ...createForm, system_prompt: e.target.value })}
                placeholder="输入系统提示词"
                rows={10}
                className="font-mono text-sm"
              />
            </div>
            <div>
              <Label>User Prompt Template</Label>
              <Textarea
                value={createForm.user_prompt_template}
                onChange={e => setCreateForm({ ...createForm, user_prompt_template: e.target.value })}
                placeholder="输入用户提示词模板，使用 {variable} 作为变量占位符"
                rows={5}
                className="font-mono text-sm"
              />
            </div>
            <div>
              <Label>变更说明</Label>
              <Input
                value={createForm.change_log}
                onChange={e => setCreateForm({ ...createForm, change_log: e.target.value })}
                placeholder="输入变更说明"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowCreateDialog(false)}>
              取消
            </Button>
            <Button onClick={createPrompt}>
              <Save className="w-4 h-4 mr-2" />
              创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit dialog */}
      <Dialog open={showEditDialog} onOpenChange={setShowEditDialog}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>编辑Prompt: {selectedPrompt?.prompt_name}</DialogTitle>
            <DialogDescription>
              编辑后将创建新版本（当前版本: v{selectedPrompt?.version}）
            </DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className="lg:col-span-2 space-y-4">
              <div>
                <Label>Prompt名称</Label>
                <Input
                  value={editForm.prompt_name}
                  onChange={e => setEditForm({ ...editForm, prompt_name: e.target.value })}
                />
              </div>
              <div>
                <Label>描述</Label>
                <Input
                  value={editForm.description}
                  onChange={e => setEditForm({ ...editForm, description: e.target.value })}
                />
              </div>
              <div>
                <Label>System Prompt</Label>
                <Textarea
                  value={editForm.system_prompt}
                  onChange={e => setEditForm({ ...editForm, system_prompt: e.target.value })}
                  rows={10}
                  className="font-mono text-sm"
                />
              </div>
              <div>
                <Label>User Prompt Template</Label>
                <Textarea
                  value={editForm.user_prompt_template}
                  onChange={e => setEditForm({ ...editForm, user_prompt_template: e.target.value })}
                  rows={5}
                  className="font-mono text-sm"
                />
              </div>
              <div>
                <Label>变更说明 *</Label>
                <Input
                  value={editForm.change_log}
                  onChange={e => setEditForm({ ...editForm, change_log: e.target.value })}
                  placeholder="说明本次修改内容"
                />
              </div>
            </div>
            {/* Variable reference panel */}
            <div className="lg:col-span-1">
              <div className="bg-gray-50 p-4 rounded-lg sticky top-0">
                <h4 className="font-semibold text-sm mb-3">可用变量</h4>
                <div className="space-y-2 text-xs">
                  <div className="p-2 bg-white rounded border">
                    <code className="text-blue-600">{'{question}'}</code>
                    <p className="text-gray-500 mt-1">用户输入的问题</p>
                  </div>
                  <div className="p-2 bg-white rounded border">
                    <code className="text-blue-600">{'{current_metadata}'}</code>
                    <p className="text-gray-500 mt-1">当前元数据上下文(JSON格式)</p>
                  </div>
                  <div className="p-2 bg-white rounded border">
                    <code className="text-blue-600">{'{query_result}'}</code>
                    <p className="text-gray-500 mt-1">SQL查询结果(JSON格式)</p>
                  </div>
                  <div className="p-2 bg-white rounded border">
                    <code className="text-blue-600">{'{history}'}</code>
                    <p className="text-gray-500 mt-1">对话历史</p>
                  </div>
                  <div className="p-2 bg-white rounded border">
                    <code className="text-blue-600">{'{schema}'}</code>
                    <p className="text-gray-500 mt-1">表结构(M-Schema格式)</p>
                  </div>
                  <div className="p-2 bg-white rounded border">
                    <code className="text-blue-600">{'{terminologies}'}</code>
                    <p className="text-gray-500 mt-1">业务术语</p>
                  </div>
                  <div className="p-2 bg-white rounded border">
                    <code className="text-blue-600">{'{er_diagram}'}</code>
                    <p className="text-gray-500 mt-1">表关联关系(ER图)</p>
                  </div>
                  <div className="p-2 bg-white rounded border">
                    <code className="text-blue-600">{'{current_time}'}</code>
                    <p className="text-gray-500 mt-1">当前时间</p>
                  </div>
                  <div className="p-2 bg-white rounded border">
                    <code className="text-blue-600">{'{error_msg}'}</code>
                    <p className="text-gray-500 mt-1">错误信息(用于重试)</p>
                  </div>
                </div>
                <p className="text-xs text-gray-400 mt-3">
                  使用 {'{variable_name}'} 格式在Prompt中插入变量，系统会自动替换为实际值。
                </p>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowEditDialog(false)}>
              取消
            </Button>
            <Button onClick={updatePrompt}>
              <Save className="w-4 h-4 mr-2" />
              保存（创建新版本）
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Version history dialog */}
      <Dialog open={showVersionDialog} onOpenChange={setShowVersionDialog}>
        <DialogContent className="max-w-4xl">
          <DialogHeader>
            <DialogTitle>版本历史: {selectedPrompt?.prompt_name}</DialogTitle>
            <DialogDescription>查看历史版本并支持回退</DialogDescription>
          </DialogHeader>
          <div className="max-h-[60vh] overflow-auto">
            <table className="w-full">
              <thead className="sticky top-0 bg-white">
                <tr className="border-b">
                  <th className="text-left p-3">版本</th>
                  <th className="text-left p-3">变更说明</th>
                  <th className="text-left p-3">创建人</th>
                  <th className="text-left p-3">创建时间</th>
                  <th className="text-left p-3">状态</th>
                  <th className="text-right p-3">操作</th>
                </tr>
              </thead>
              <tbody>
                {versions.map(v => (
                  <tr key={v.id} className="border-b hover:bg-gray-50">
                    <td className="p-3">
                      <span className="font-mono">v{v.version}</span>
                    </td>
                    <td className="p-3 text-sm">{v.change_log || '-'}</td>
                    <td className="p-3 text-sm">{v.created_by}</td>
                    <td className="p-3 text-sm">{v.created_at}</td>
                    <td className="p-3">
                      {v.is_current ? (
                        <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded">
                          当前版本
                        </span>
                      ) : (
                        <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded">
                          历史版本
                        </span>
                      )}
                    </td>
                    <td className="p-3 text-right">
                      {!v.is_current && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => rollbackToVersion(v.version)}
                        >
                          <RotateCcw className="w-3 h-3 mr-1" />
                          回退
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowVersionDialog(false)}>
              关闭
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
