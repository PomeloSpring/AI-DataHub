import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import {
  ChevronDown, ChevronRight, Plus, Edit, Trash2, ArrowUp, ArrowDown,
  FolderOpen, Folder,
} from 'lucide-react';
import * as LucideIcons from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Spinner } from '@/components/ui/spinner';
import { ScrollArea } from '@/components/ui/scroll-area';
import client from '../api/client';

// ── Types ─────────────────────────────────────────────────────────────

interface MenuItem {
  id: number;
  parent_id: number | null;
  name: string;
  icon: string;
  page_id: number | null;
  link_type: string;
  is_system: boolean;
  sort_order: number;
  children: MenuItem[];
}

interface Dashboard {
  id: number;
  name: string;
  status?: string;
}

// ── Icon picker options ───────────────────────────────────────────────

const ICON_OPTIONS = [
  // 分组/文件夹
  { value: 'Folder', label: '文件夹' },
  { value: 'FolderOpen', label: '打开文件夹' },
  // 数据/分析
  { value: 'BarChart3', label: '柱状图' },
  { value: 'LineChart', label: '折线图' },
  { value: 'PieChart', label: '饼图' },
  { value: 'TrendingUp', label: '趋势' },
  { value: 'Activity', label: '动态' },
  { value: 'Database', label: '数据库' },
  { value: 'Table', label: '表格' },
  { value: 'LayoutDashboard', label: '仪表盘' },
  { value: 'Monitor', label: '大屏' },
  // 文档/内容
  { value: 'FileText', label: '文档' },
  { value: 'BookOpen', label: '书本' },
  { value: 'Clipboard', label: '剪贴板' },
  { value: 'Inbox', label: '收件箱' },
  // 导航/操作
  { value: 'Home', label: '首页' },
  { value: 'Settings', label: '设置' },
  { value: 'Search', label: '搜索' },
  { value: 'Filter', label: '筛选' },
  { value: 'Download', label: '下载' },
  { value: 'Upload', label: '上传' },
  { value: 'RefreshCw', label: '刷新' },
  // 人员/组织
  { value: 'Users', label: '用户组' },
  { value: 'User', label: '用户' },
  { value: 'Building', label: '机构' },
  { value: 'Contact', label: '联系人' },
  // 状态/标记
  { value: 'Star', label: '星标' },
  { value: 'Heart', label: '收藏' },
  { value: 'Bookmark', label: '书签' },
  { value: 'Flag', label: '旗帜' },
  { value: 'Bell', label: '通知' },
  // 通用
  { value: 'Calendar', label: '日历' },
  { value: 'Clock', label: '时钟' },
  { value: 'Globe', label: '全球' },
  { value: 'Map', label: '地图' },
  { value: 'Zap', label: '闪电' },
  { value: 'Grid3x3', label: '网格' },
  { value: 'Layers', label: '图层' },
  { value: 'Tag', label: '标签' },
  { value: 'Link', label: '链接' },
  { value: 'Lock', label: '锁' },
  { value: 'Key', label: '钥匙' },
];

function getIconComponent(iconName: string): React.ComponentType<{ className?: string }> {
  const icons = LucideIcons as Record<string, any>;
  return icons[iconName] || Folder;
}

// ── TreeNode component ────────────────────────────────────────────────

interface TreeNodeProps {
  item: MenuItem;
  depth: number;
  dashboards: Dashboard[];
  onEdit: (item: MenuItem) => void;
  onDelete: (id: number) => void;
  onMove: (id: number, direction: 'up' | 'down') => void;
  onAddChild: (parentId: number) => void;
  isFirst: boolean;
  isLast: boolean;
}

function TreeNode({ item, depth, dashboards, onEdit, onDelete, onMove, onAddChild, isFirst, isLast }: TreeNodeProps) {
  const [expanded, setExpanded] = useState(true);
  const hasChildren = item.children && item.children.length > 0;
  const Icon = getIconComponent(item.icon);
  const linkedDashboard = item.page_id ? dashboards.find(d => d.id === item.page_id) : null;

  return (
    <div>
      <div
        className="group flex items-center gap-2 px-2 py-2 rounded-md hover:bg-muted/50 transition-colors"
        style={{ paddingLeft: `${depth * 24 + 8}px` }}
      >
        {/* Expand/collapse toggle */}
        <button
          className="h-5 w-5 flex items-center justify-center flex-shrink-0"
          onClick={() => setExpanded(!expanded)}
          style={{ visibility: hasChildren ? 'visible' : 'hidden' }}
        >
          {expanded
            ? <ChevronDown className="h-4 w-4 text-muted-foreground" />
            : <ChevronRight className="h-4 w-4 text-muted-foreground" />
          }
        </button>

        {/* Icon */}
        <Icon className="h-4 w-4 flex-shrink-0 text-muted-foreground" />

        {/* Name */}
        <span className="text-sm font-medium truncate">{item.name}</span>

        {/* Linked dashboard badge */}
        {linkedDashboard && (
          <Badge variant="outline" className="text-xs flex-shrink-0">
            {linkedDashboard.name}
          </Badge>
        )}

        {/* System badge */}
        {item.is_system && (
          <Badge variant="secondary" className="text-xs flex-shrink-0">
            系统
          </Badge>
        )}

        {/* Spacer */}
        <div className="flex-1" />

        {/* Action buttons (visible on hover) */}
        <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={() => onMove(item.id, 'up')}
            disabled={isFirst}
            title="上移"
          >
            <ArrowUp className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={() => onMove(item.id, 'down')}
            disabled={isLast}
            title="下移"
          >
            <ArrowDown className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={() => onAddChild(item.id)}
            title="添加子菜单"
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={() => onEdit(item)}
            title="编辑"
          >
            <Edit className="h-3.5 w-3.5" />
          </Button>
          {!item.is_system && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0"
              onClick={() => onDelete(item.id)}
              title="删除"
            >
              <Trash2 className="h-3.5 w-3.5 text-destructive" />
            </Button>
          )}
        </div>
      </div>

      {/* Children */}
      {expanded && hasChildren && (
        <div>
          {item.children.map((child, idx) => (
            <TreeNode
              key={child.id}
              item={child}
              depth={depth + 1}
              dashboards={dashboards}
              onEdit={onEdit}
              onDelete={onDelete}
              onMove={onMove}
              onAddChild={onAddChild}
              isFirst={idx === 0}
              isLast={idx === item.children.length - 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main MenuEditorTab component ──────────────────────────────────────

export default function MenuEditorTab({ workspaceId }: { workspaceId?: number } = {}) {
  const [tree, setTree] = useState<MenuItem[]>([]);
  const [dashboards, setDashboards] = useState<Dashboard[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<MenuItem | null>(null);
  const [parentId, setParentId] = useState<number | null>(null);
  const [formValues, setFormValues] = useState<any>({ name: '', icon: '', page_id: null, link_type: 'page' });

  const loadTree = useCallback(async () => {
    try {
      const params = workspaceId ? `?workspace_id=${workspaceId}` : '';
      const { data } = await client.get(`/admin/menu-tree${params}`);
      setTree(data || []);
    } catch {
      toast.error('加载菜单树失败');
    }
  }, [workspaceId]);

  const loadDashboards = useCallback(async () => {
    try {
      const { data } = await client.get('/dashboard/');
      setDashboards(data || []);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    Promise.all([loadTree(), loadDashboards()])
      .finally(() => setLoading(false));
  }, [loadTree, loadDashboards]);

  const openCreate = (pId: number | null = null) => {
    setEditing(null);
    setParentId(pId);
    setFormValues({ name: '', icon: '', page_id: null, link_type: 'page' });
    setModalOpen(true);
  };

  const openEdit = (item: MenuItem) => {
    setEditing(item);
    setParentId(item.parent_id);
    setFormValues({
      name: item.name,
      icon: item.icon || '',
      page_id: item.page_id,
      link_type: item.link_type || 'page',
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    if (!formValues.name?.trim()) {
      toast.error('请输入菜单名称');
      return;
    }

    try {
      if (editing) {
        await client.put(`/admin/menu-tree/${editing.id}`, {
          name: formValues.name,
          icon: formValues.icon || '',
          page_id: formValues.page_id || null,
          link_type: formValues.link_type || 'page',
        });
        toast.success('已更新');
      } else {
        await client.post('/admin/menu-tree', {
          name: formValues.name,
          icon: formValues.icon || '',
          parent_id: parentId,
          page_id: formValues.page_id || null,
          ...(workspaceId ? { workspace_id: workspaceId } : {}),
          link_type: formValues.link_type || 'page',
          is_system: false,
        });
        toast.success('已创建');
      }
      setModalOpen(false);
      loadTree();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '操作失败');
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('确定删除此菜单项？子菜单也会被一并删除。')) return;
    try {
      await client.delete(`/admin/menu-tree/${id}`);
      toast.success('已删除');
      loadTree();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  const handleMove = async (id: number, direction: 'up' | 'down') => {
    try {
      await client.put(`/admin/menu-tree/${id}/move?direction=${direction}`);
      loadTree();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '移动失败');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size={32} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">菜单编辑</h3>
          <p className="text-sm text-muted-foreground">管理侧边栏菜单结构，支持拖拽排序和多级嵌套</p>
        </div>
        <Button onClick={() => openCreate(null)}>
          <Plus className="h-4 w-4 mr-2" />
          添加顶级菜单
        </Button>
      </div>

      <div className="rounded-lg border bg-card overflow-hidden">
        {tree.length === 0 ? (
          <div className="text-center py-16 text-muted-foreground">
            <FolderOpen className="h-14 w-14 mx-auto mb-4 opacity-30" />
            <p className="text-lg font-medium mb-1">暂无菜单</p>
            <p className="text-sm mb-6">点击上方按钮添加第一个菜单项</p>
          </div>
        ) : (
          <ScrollArea className="h-[600px]">
            <div className="py-2">
              {tree.map((item, idx) => (
                <TreeNode
                  key={item.id}
                  item={item}
                  depth={0}
                  dashboards={dashboards}
                  onEdit={openEdit}
                  onDelete={handleDelete}
                  onMove={handleMove}
                  onAddChild={(pId) => openCreate(pId)}
                  isFirst={idx === 0}
                  isLast={idx === tree.length - 1}
                />
              ))}
            </div>
          </ScrollArea>
        )}
      </div>

      {/* Edit/Create Modal */}
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-[480px]">
          <DialogHeader>
            <DialogTitle>{editing ? '编辑菜单项' : '添加菜单项'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>菜单名称 *</Label>
              <Input
                placeholder="如: 流量分析"
                value={formValues.name || ''}
                onChange={(e) => setFormValues({ ...formValues, name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>图标</Label>
              <Select
                value={formValues.icon || '__none__'}
                onValueChange={(v) => setFormValues({ ...formValues, icon: v === '__none__' ? '' : v })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择图标（可选）" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">无图标</SelectItem>
                  {ICON_OPTIONS.map((opt) => {
                    const IconComp = getIconComponent(opt.value);
                    return (
                      <SelectItem key={opt.value} value={opt.value}>
                        <div className="flex items-center gap-2">
                          <IconComp className="h-4 w-4" />
                          {opt.label}
                        </div>
                      </SelectItem>
                    );
                  })}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>关联仪表盘</Label>
              <Select
                value={formValues.page_id ? String(formValues.page_id) : '__none__'}
                onValueChange={(v) => setFormValues({ ...formValues, page_id: v === '__none__' ? null : Number(v) })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择仪表盘（留空为分组节点）" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">无（分组节点）</SelectItem>
                  {dashboards
                    .filter(d => d.status === 'enabled' || !d.status)
                    .map(d => (
                      <SelectItem key={d.id} value={String(d.id)}>
                        {d.name}
                      </SelectItem>
                    ))
                  }
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                不关联仪表盘则作为分组节点，可包含子菜单
              </p>
            </div>
            {formValues.page_id && (
              <div className="space-y-2">
                <Label>打开方式</Label>
                <Select
                  value={formValues.link_type || 'page'}
                  onValueChange={(v) => setFormValues({ ...formValues, link_type: v })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="page">普通页面</SelectItem>
                    <SelectItem value="screen">可视化大屏（全屏/轮播）</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  可视化大屏模式支持全屏、轮播、定时刷新等功能
                </p>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>取消</Button>
            <Button onClick={handleSave}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
