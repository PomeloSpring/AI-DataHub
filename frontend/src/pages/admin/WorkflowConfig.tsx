import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { toast } from 'sonner';
import { Check, Loader2, RefreshCw, Save, Zap, Workflow, Brain } from 'lucide-react';
import client from '@/api/client';

interface WorkflowTemplate {
  id: number;
  name: string;
  description?: string;
  is_active: boolean;
  is_default: boolean;
  workflow_type: string;
  created_at: string;
  step_count?: number;
}

interface WorkspaceWorkflowConfig {
  workspace_id: number;
  workflow_template_id?: number;
  pipeline_mode: string;
  retrieval_strategy: string;
  max_iterations: number;
  is_active: boolean;
}

const PIPELINE_MODES = [
  { value: 'quick', label: '快速模式', icon: Zap, color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20', description: '简化 RAG，响应快' },
  { value: 'deep', label: '深度模式', icon: Workflow, color: 'bg-blue-500/10 text-blue-500 border-blue-500/20', description: '平台内置 Agent，自主工具调用' },
  { value: 'agent', label: 'Agent模式', icon: Brain, color: 'bg-purple-500/10 text-purple-500 border-purple-500/20', description: '外部执行层（默认 Claude）' },
];

const RETRIEVAL_STRATEGIES = [
  { value: 'hybrid', label: '混合检索', description: '关键词 + 向量匹配' },
  { value: 'full_table', label: '全表检索', description: '检索完整元数据' },
  { value: 'column_first', label: '字段优先', description: '优先字段级元数据' },
  { value: 'two_stage', label: '两阶段检索', description: '粗筛 + 精排' },
  { value: 'bidirectional', label: '双向检索', description: '正向 + 反向匹配' },
];

export default function WorkflowConfig() {
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [config, setConfig] = useState<WorkspaceWorkflowConfig>({
    workspace_id: 0, workflow_template_id: undefined, pipeline_mode: 'agent',
    retrieval_strategy: 'hybrid', max_iterations: 10, is_active: true,
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const pathParts = window.location.pathname.split('/');
  const wsIndex = pathParts.indexOf('ws');
  const workspaceId = wsIndex >= 0 ? parseInt(pathParts[wsIndex + 1]) : 0;

  useEffect(() => { loadTemplates(); if (workspaceId) loadWorkspaceConfig(); }, [workspaceId]);

  const loadTemplates = async () => {
    try {
      const { data } = await client.get('/admin/workflows', { params: { size: 100 } });
      setTemplates(data.items || []);
    } catch (error) { console.error('Failed to load templates:', error); }
  };

  const loadWorkspaceConfig = async () => {
    setLoading(true);
    try {
      const { data } = await client.get(`/workspaces/${workspaceId}/workflow-config`);
      if (data) setConfig(data);
    } catch {} finally { setLoading(false); }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await client.put(`/workspaces/${workspaceId}/workflow-config`, config);
      toast.success('工作流配置已保存');
    } catch { toast.error('保存失败'); }
    finally { setSaving(false); }
  };

  const modeConfig = PIPELINE_MODES.find(m => m.value === config.pipeline_mode) || PIPELINE_MODES[2];

  return (
    <div className="p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">工作流配置</h1>
          <p className="text-muted-foreground text-sm mt-1">配置当前工作空间的 AI 推理工作流</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadWorkspaceConfig}>
            <RefreshCw className="w-4 h-4 mr-1" /> 刷新
          </Button>
          <Button size="sm" onClick={handleSave} disabled={saving}>
            {saving ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}
            保存配置
          </Button>
        </div>
      </div>

      {/* Pipeline Mode Table */}
      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-center w-12 p-3 font-medium">选择</th>
              <th className="text-left p-3 font-medium">模式</th>
              <th className="text-left p-3 font-medium">说明</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={3} className="p-8 text-center text-muted-foreground">加载中...</td></tr>
            ) : (
              PIPELINE_MODES.map(mode => {
                const Icon = mode.icon;
                const isSelected = config.pipeline_mode === mode.value;
                return (
                  <tr key={mode.value} className={`border-t hover:bg-muted/30 cursor-pointer ${isSelected ? 'bg-primary/5' : ''}`}
                    onClick={() => setConfig({ ...config, pipeline_mode: mode.value })}>
                    <td className="p-3 text-center">
                      <input type="radio" checked={isSelected} onChange={() => {}} className="rounded" />
                    </td>
                    <td className="p-3">
                      <div className="flex items-center gap-2">
                        <Icon className="h-4 w-4" />
                        <span className="font-medium">{mode.label}</span>
                        {isSelected && <Check className="h-4 w-4 text-primary" />}
                      </div>
                    </td>
                    <td className="p-3 text-muted-foreground">{mode.description}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Template Selection */}
      <div className="border rounded-lg overflow-hidden">
        <div className="bg-muted/50 px-3 py-2 font-medium text-sm">工作流模板</div>
        <table className="w-full text-sm">
          <thead className="bg-muted/30">
            <tr>
              <th className="text-center w-12 p-3 font-medium">选择</th>
              <th className="text-left p-3 font-medium">模板名称</th>
              <th className="text-left p-3 font-medium">类型</th>
              <th className="text-left p-3 font-medium">步骤数</th>
              <th className="text-left p-3 font-medium">描述</th>
            </tr>
          </thead>
          <tbody>
            <tr className={`border-t hover:bg-muted/30 cursor-pointer ${!config.workflow_template_id ? 'bg-primary/5' : ''}`}
              onClick={() => setConfig({ ...config, workflow_template_id: undefined })}>
              <td className="p-3 text-center"><input type="radio" checked={!config.workflow_template_id} onChange={() => {}} /></td>
              <td className="p-3 font-medium">默认工作流</td>
              <td className="p-3">-</td>
              <td className="p-3">-</td>
              <td className="p-3 text-muted-foreground">使用系统默认配置</td>
            </tr>
            {templates.map(template => {
              const isSelected = config.workflow_template_id === template.id;
              return (
                <tr key={template.id} className={`border-t hover:bg-muted/30 cursor-pointer ${isSelected ? 'bg-primary/5' : ''}`}
                  onClick={() => setConfig({ ...config, workflow_template_id: template.id })}>
                  <td className="p-3 text-center"><input type="radio" checked={isSelected} onChange={() => {}} /></td>
                  <td className="p-3 font-medium">{template.name}</td>
                  <td className="p-3"><Badge variant="outline">{template.workflow_type === 'dag' ? 'DAG' : '线性'}</Badge></td>
                  <td className="p-3">{template.step_count || 0}</td>
                  <td className="p-3 text-muted-foreground max-w-[300px] truncate">{template.description || '-'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Retrieval Strategy */}
      <div className="border rounded-lg overflow-hidden">
        <div className="bg-muted/50 px-3 py-2 font-medium text-sm">检索策略</div>
        <table className="w-full text-sm">
          <thead className="bg-muted/30">
            <tr>
              <th className="text-center w-12 p-3 font-medium">选择</th>
              <th className="text-left p-3 font-medium">策略</th>
              <th className="text-left p-3 font-medium">说明</th>
            </tr>
          </thead>
          <tbody>
            {RETRIEVAL_STRATEGIES.map(strategy => {
              const isSelected = config.retrieval_strategy === strategy.value;
              return (
                <tr key={strategy.value} className={`border-t hover:bg-muted/30 cursor-pointer ${isSelected ? 'bg-primary/5' : ''}`}
                  onClick={() => setConfig({ ...config, retrieval_strategy: strategy.value })}>
                  <td className="p-3 text-center"><input type="radio" checked={isSelected} onChange={() => {}} /></td>
                  <td className="p-3 font-medium">{strategy.label}</td>
                  <td className="p-3 text-muted-foreground">{strategy.description}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Status */}
      <div className="flex items-center gap-4 p-4 border rounded-lg">
        <div className="flex items-center gap-2">
          <Switch checked={config.is_active} onCheckedChange={(checked) => setConfig({ ...config, is_active: checked })} />
          <Label>启用工作流配置</Label>
        </div>
        <Badge variant="outline" className={modeConfig.color}>{modeConfig.label}</Badge>
        <Badge variant="outline">{config.retrieval_strategy}</Badge>
      </div>
    </div>
  );
}
