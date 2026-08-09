import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { toast } from 'sonner';
import { Search, Loader2, FileCode, RefreshCw } from 'lucide-react';
import client from '@/api/client';
import { useParams } from 'react-router-dom';

interface SkillTemplate {
  id: number;
  skill_key: string;
  skill_name: string;
  description: string;
  category: string;
  version: number;
  is_active: boolean;
}

interface WorkspaceSkill {
  skill_key: string;
  is_enabled: boolean;
}

const CATEGORY_LABELS: Record<string, { label: string; color: string }> = {
  nl2sql: { label: 'NL2SQL', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  analysis: { label: '数据分析', color: 'bg-green-500/10 text-green-500 border-green-500/20' },
  chart: { label: '图表生成', color: 'bg-purple-500/10 text-purple-500 border-purple-500/20' },
  correction: { label: 'SQL纠错', color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
  prediction: { label: '数据预测', color: 'bg-cyan-500/10 text-cyan-500 border-cyan-500/20' },
  custom: { label: '自定义', color: 'bg-gray-500/10 text-gray-500 border-gray-500/20' },
};

export default function WorkspaceSkillsConfig() {
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const [skills, setSkills] = useState<SkillTemplate[]>([]);
  const [workspaceSkills, setWorkspaceSkills] = useState<WorkspaceSkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchKey, setSearchKey] = useState('');

  useEffect(() => { loadData(); }, [workspaceId]);

  const loadData = async () => {
    setLoading(true);
    try {
      const { data: allSkills } = await client.get('/admin/skills');
      setSkills(Array.isArray(allSkills) ? allSkills : []);
      try {
        const { data: wsSkills } = await client.get(`/workspaces/${workspaceId}/skills`);
        setWorkspaceSkills(Array.isArray(wsSkills) ? wsSkills : []);
      } catch { setWorkspaceSkills([]); }
    } catch (error) {
      toast.error('加载技能列表失败');
    } finally { setLoading(false); }
  };

  const isSkillEnabled = (skillKey: string) => {
    const wsSkill = workspaceSkills.find(s => s.skill_key === skillKey);
    return wsSkill ? wsSkill.is_enabled : true;
  };

  const toggleSkill = async (skillKey: string, enabled: boolean) => {
    try {
      await client.put(`/workspaces/${workspaceId}/skills/${skillKey}`, { is_enabled: enabled });
      setWorkspaceSkills(prev => {
        const existing = prev.find(s => s.skill_key === skillKey);
        if (existing) return prev.map(s => s.skill_key === skillKey ? { ...s, is_enabled: enabled } : s);
        return [...prev, { skill_key: skillKey, is_enabled: enabled }];
      });
      toast.success(enabled ? '已启用' : '已禁用');
    } catch { toast.error('保存失败'); }
  };

  const filteredSkills = skills.filter(s =>
    s.skill_key.includes(searchKey) || s.skill_name.includes(searchKey) || s.description?.includes(searchKey)
  );

  return (
    <div className="p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Skills 配置</h1>
          <p className="text-muted-foreground text-sm mt-1">配置当前工作空间可用的 Skills 技能</p>
        </div>
        <Button variant="outline" size="sm" onClick={loadData}>
          <RefreshCw className="w-4 h-4 mr-1" /> 刷新
        </Button>
      </div>

      {/* Search */}
      <div className="relative max-w-sm">
        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input placeholder="搜索技能..." value={searchKey} onChange={(e) => setSearchKey(e.target.value)} className="pl-8" />
      </div>

      {/* Skills Table */}
      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-3 font-medium">技能名称</th>
              <th className="text-left p-3 font-medium">标识</th>
              <th className="text-left p-3 font-medium">类别</th>
              <th className="text-left p-3 font-medium">版本</th>
              <th className="text-left p-3 font-medium">描述</th>
              <th className="text-center p-3 font-medium">启用</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={6} className="p-8 text-center text-muted-foreground">加载中...</td></tr>
            ) : filteredSkills.length === 0 ? (
              <tr><td colSpan={6} className="p-8 text-center text-muted-foreground">{searchKey ? '未找到匹配的技能' : '暂无可用技能'}</td></tr>
            ) : (
              filteredSkills.map(skill => {
                const isEnabled = isSkillEnabled(skill.skill_key);
                const catConfig = CATEGORY_LABELS[skill.category] || CATEGORY_LABELS.custom;
                return (
                  <tr key={skill.id} className={`border-t hover:bg-muted/30 ${!isEnabled ? 'opacity-60' : ''}`}>
                    <td className="p-3 font-medium">{skill.skill_name}</td>
                    <td className="p-3"><code className="text-xs bg-muted px-1.5 py-0.5 rounded">{skill.skill_key}</code></td>
                    <td className="p-3"><Badge variant="outline" className={catConfig.color}>{catConfig.label}</Badge></td>
                    <td className="p-3 text-muted-foreground">v{skill.version}</td>
                    <td className="p-3 text-muted-foreground max-w-[300px] truncate">{skill.description || '-'}</td>
                    <td className="p-3 text-center">
                      <Switch checked={isEnabled} onCheckedChange={(checked) => toggleSkill(skill.skill_key, checked)} />
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
