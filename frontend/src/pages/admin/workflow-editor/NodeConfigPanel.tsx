import { useState, useEffect } from 'react';
import { X, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import type { WorkflowNode } from './types';
import { STEP_TYPE_OPTIONS } from './types';

interface NodeConfigPanelProps {
  node: WorkflowNode | null;
  onUpdate: (nodeId: string, data: Record<string, any>) => void;
  onClose: () => void;
}

export default function NodeConfigPanel({ node, onUpdate, onClose }: NodeConfigPanelProps) {
  const [formData, setFormData] = useState<Record<string, any>>({});

  useEffect(() => {
    if (node) {
      setFormData({
        label: node.data.label || '',
        step_type: node.data.step_type || '',
        step_name: node.data.step_name || '',
        max_rounds: node.data.max_rounds || 1,
        is_enabled: node.data.is_enabled !== false,
        prompt_key: node.data.prompt_key || '',
        condition_expr: node.data.condition_expr || '',
        config: JSON.stringify(node.data.config || {}, null, 2),
      });
    }
  }, [node]);

  if (!node) return null;

  const handleSave = () => {
    let config = {};
    try {
      config = JSON.parse(formData.config || '{}');
    } catch {
      // keep empty object
    }
    onUpdate(node.id, {
      ...formData,
      config,
      max_rounds: parseInt(formData.max_rounds) || 1,
    });
  };

  const isStartOrEnd = node.type === 'start' || node.type === 'end';

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">节点配置</h3>
        <Button variant="ghost" size="icon" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="space-y-3">
        <div>
          <Label htmlFor="label">节点名称</Label>
          <Input
            id="label"
            value={formData.label}
            onChange={(e) => setFormData((prev) => ({ ...prev, label: e.target.value }))}
            placeholder="输入节点名称"
          />
        </div>

        {!isStartOrEnd && (
          <>
            <div>
              <Label htmlFor="step_type">步骤类型</Label>
              <Select
                value={formData.step_type}
                onValueChange={(v) => setFormData((prev) => ({ ...prev, step_type: v }))}
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择步骤类型" />
                </SelectTrigger>
                <SelectContent>
                  {STEP_TYPE_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      <div>
                        <div>{opt.label}</div>
                        <div className="text-xs text-muted-foreground">{opt.description}</div>
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div>
              <Label htmlFor="max_rounds">最大轮次</Label>
              <Input
                id="max_rounds"
                type="number"
                min={1}
                max={10}
                value={formData.max_rounds}
                onChange={(e) => setFormData((prev) => ({ ...prev, max_rounds: e.target.value }))}
              />
            </div>

            <div>
              <Label htmlFor="prompt_key">Prompt Key</Label>
              <Input
                id="prompt_key"
                value={formData.prompt_key}
                onChange={(e) => setFormData((prev) => ({ ...prev, prompt_key: e.target.value }))}
                placeholder="关联的 Prompt Key"
              />
            </div>

            {node.type === 'condition' && (
              <div>
                <Label htmlFor="condition_expr">条件表达式</Label>
                <Textarea
                  id="condition_expr"
                  value={formData.condition_expr}
                  onChange={(e) => setFormData((prev) => ({ ...prev, condition_expr: e.target.value }))}
                  placeholder="例如: result.row_count > 0"
                  rows={3}
                />
              </div>
            )}

            <div className="flex items-center justify-between">
              <Label htmlFor="is_enabled">启用</Label>
              <Switch
                id="is_enabled"
                checked={formData.is_enabled}
                onCheckedChange={(checked) => setFormData((prev) => ({ ...prev, is_enabled: checked }))}
              />
            </div>

            <div>
              <Label htmlFor="config">配置 (JSON)</Label>
              <Textarea
                id="config"
                value={formData.config}
                onChange={(e) => setFormData((prev) => ({ ...prev, config: e.target.value }))}
                placeholder="{}"
                rows={5}
                className="font-mono text-xs"
              />
            </div>
          </>
        )}
      </div>

      <Button onClick={handleSave} className="w-full">
        <Save className="h-4 w-4 mr-2" />
        保存配置
      </Button>
    </div>
  );
}
