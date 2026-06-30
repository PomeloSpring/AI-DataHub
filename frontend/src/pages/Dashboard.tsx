import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';
import {
  Plus, Edit, Trash2, Star, Move, BarChart3, Play, Settings,
  AlertTriangle, Check, X, ArrowUp, ArrowDown, Search, Layout,
  LayoutDashboard, Copy,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Spinner } from '@/components/ui/spinner';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useDashboardStore } from '../stores/dashboardStore';
import DashboardChart from '../components/DashboardChart';
import AddChartModal from '../components/AddChartModal';
import CarouselView from '../components/CarouselView';
import DashboardFilters, { DEFAULT_FILTERS } from '../components/DashboardFilters';
import ChartConfigPanel from '../components/ChartConfigPanel';
import DashboardExportImport from '../components/DashboardExportImport';
import DashboardAutoRefresh from '../components/DashboardAutoRefresh';
import DashboardTemplates from '../components/DashboardTemplates';
import DashboardCrossFilter, { CrossFilter } from '../components/DashboardCrossFilter';
import DashboardSearch from '../components/DashboardSearch';
import DashboardParams from '../components/DashboardParams';
import { Badge } from '@/components/ui/badge';
import type { DashboardParam, Dashboard, DashboardStatus, PageParam } from '../stores/dashboardStore';
import { DASHBOARD_STATUS_MAP } from '../stores/dashboardStore';

// Canvas settings
const CANVAS_WIDTH = 1920;
const CANVAS_HEIGHT = 1080;
const GRID_SIZE = 20;
const DEFAULT_CHART_SIZE = { w: 400, h: 350 };

// ── Page Params Bar ─────────────────────────────────────────────────────

function PageParamsBar({
  params,
  values,
  onChange,
}: {
  params: PageParam[];
  values: Record<string, any>;
  onChange: (name: string, value: any) => void;
}) {
  if (!params || params.length === 0) return null;

  return (
    <div className="flex items-center gap-4 px-4 py-2 border-b bg-muted/20 flex-wrap">
      {params.map((p) => {
        const val = values[p.name] ?? p.default ?? '';

        return (
          <div key={p.name} className="flex items-center gap-2">
            <Label className="text-xs text-muted-foreground whitespace-nowrap">
              {p.label || p.name}
            </Label>
            {p.type === 'date' ? (
              <Input
                type="date"
                value={val}
                onChange={(e) => onChange(p.name, e.target.value)}
                className="h-8 w-[140px] text-xs"
              />
            ) : p.type === 'date_range' ? (
              <Input
                type="text"
                value={val}
                placeholder="YYYY-MM-DD,YYYY-MM-DD"
                onChange={(e) => onChange(p.name, e.target.value)}
                className="h-8 w-[220px] text-xs"
              />
            ) : p.type === 'number' ? (
              <Input
                type="number"
                value={val}
                onChange={(e) => onChange(p.name, e.target.value)}
                className="h-8 w-[120px] text-xs"
              />
            ) : (
              <Input
                type="text"
                value={val}
                onChange={(e) => onChange(p.name, e.target.value)}
                className="h-8 w-[140px] text-xs"
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Page Params Config Section ──────────────────────────────────────────

function PageParamsConfigSection({
  pageParams,
  setPageParams,
}: {
  pageParams: PageParam[];
  setPageParams: (params: PageParam[]) => void;
}) {
  const addPageParam = () => {
    setPageParams([...pageParams, { name: '', label: '', type: 'string', default: '' }]);
  };

  const updatePageParam = (idx: number, field: string, value: any) => {
    const next = [...pageParams];
    (next[idx] as any)[field] = value;
    setPageParams(next);
  };

  const removePageParam = (idx: number) => {
    setPageParams(pageParams.filter((_, i) => i !== idx));
  };

  return (
    <div className="space-y-2">
      <Label>页面参数</Label>
      <p className="text-xs text-muted-foreground">
        定义页面级参数后，在图表 SQL 中使用 {'${param_name}'} 占位符引用参数值
      </p>
      <div className="space-y-3 mt-2">
        {pageParams.map((p, idx) => (
          <div key={idx} className="flex items-start gap-2 p-3 border rounded-lg bg-muted/30">
            <div className="flex-1 grid grid-cols-2 gap-2">
              <Input
                size={20}
                placeholder="参数名 (英文)"
                value={p.name}
                onChange={(e) => updatePageParam(idx, 'name', e.target.value)}
              />
              <Input
                size={20}
                placeholder="显示标签"
                value={p.label}
                onChange={(e) => updatePageParam(idx, 'label', e.target.value)}
              />
              <Select
                value={p.type}
                onValueChange={(v) => updatePageParam(idx, 'type', v)}
              >
                <SelectTrigger className="h-8">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="string">文本</SelectItem>
                  <SelectItem value="number">数字</SelectItem>
                  <SelectItem value="date">日期</SelectItem>
                  <SelectItem value="date_range">日期范围</SelectItem>
                </SelectContent>
              </Select>
              <Input
                size={20}
                placeholder="默认值"
                value={p.default}
                onChange={(e) => updatePageParam(idx, 'default', e.target.value)}
              />
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 w-8 p-0 flex-shrink-0"
              onClick={() => removePageParam(idx)}
            >
              <X className="h-4 w-4 text-destructive" />
            </Button>
          </div>
        ))}
        <Button variant="outline" size="sm" onClick={addPageParam}>
          <Plus className="h-4 w-4 mr-1" />
          添加页面参数
        </Button>
      </div>
    </div>
  );
}

// ── Dashboard Settings Content ──────────────────────────────────────────

function DashboardSettingsContent({
  current, carouselInterval, setCarouselInterval, updateDashboard, onClose,
}: {
  current: Dashboard;
  carouselInterval: number;
  setCarouselInterval: (v: number) => void;
  updateDashboard: (id: number, data: Partial<Dashboard>) => Promise<void>;
  onClose: () => void;
}) {
  const [params, setParams] = useState<DashboardParam[]>(current.params || []);
  const [pageParams, setPageParams] = useState<PageParam[]>(current.page_params || []);
  const [status, setStatus] = useState<DashboardStatus>(current.status || 'designing');

  const addParam = () => {
    setParams([...params, { name: '', label: '', type: 'text', options: [], default: '', placeholder: '' }]);
  };

  const updateParam = (idx: number, field: string, value: any) => {
    const next = [...params];
    (next[idx] as any)[field] = value;
    setParams(next);
  };

  const removeParam = (idx: number) => {
    setParams(params.filter((_, i) => i !== idx));
  };

  const handleSave = async () => {
    // Validate: names must be non-empty and unique
    const validParams = params.filter(p => p.name.trim());
    const names = new Set(validParams.map(p => p.name.trim()));
    if (names.size !== validParams.length) {
      toast.error('参数名称不能重复');
      return;
    }
    // Validate page params: names must be non-empty and unique
    const validPageParams = pageParams.filter(p => p.name.trim());
    const pageNames = new Set(validPageParams.map(p => p.name.trim()));
    if (pageNames.size !== validPageParams.length) {
      toast.error('页面参数名称不能重复');
      return;
    }
    await updateDashboard(current.id, {
      params: validParams,
      page_params: validPageParams,
      status,
      carousel_interval: carouselInterval,
    } as any);
    toast.success('设置已保存');
    onClose();
  };

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Label>仪表盘状态</Label>
        <p className="text-xs text-muted-foreground">仅「已启用」状态的仪表盘可在可视化大屏和统计分析中展示</p>
        <div className="flex gap-2">
          {(Object.entries(DASHBOARD_STATUS_MAP) as [DashboardStatus, typeof DASHBOARD_STATUS_MAP[DashboardStatus]][]).map(([key, val]) => (
            <Button key={key} variant={status === key ? 'default' : 'outline'} size="sm"
              className={status === key ? '' : ''}
              onClick={() => setStatus(key)}>
              {val.label}
            </Button>
          ))}
        </div>
      </div>

      <div className="space-y-2">
        <Label>设为默认仪表盘</Label>
        <div className="flex gap-2">
          <Button variant={current.is_default ? 'default' : 'outline'} size="sm"
            onClick={async () => { await updateDashboard(current.id, { is_default: true } as any); toast.success('已设为默认'); }}>
            是
          </Button>
          <Button variant={!current.is_default ? 'default' : 'outline'} size="sm"
            onClick={async () => { await updateDashboard(current.id, { is_default: false } as any); }}>
            否
          </Button>
        </div>
      </div>

      <div className="space-y-2">
        <Label>查询参数</Label>
        <p className="text-xs text-muted-foreground">
          定义参数后，在图表 SQL 中使用 {'{{param_name}}'} 占位符引用参数值
        </p>
        <div className="space-y-3 mt-2">
          {params.map((p, idx) => (
            <div key={idx} className="flex items-start gap-2 p-3 border rounded-lg bg-muted/30">
              <div className="flex-1 grid grid-cols-2 gap-2">
                <Input size={20} placeholder="参数名 (英文)" value={p.name}
                  onChange={e => updateParam(idx, 'name', e.target.value)} />
                <Input size={20} placeholder="显示标签" value={p.label}
                  onChange={e => updateParam(idx, 'label', e.target.value)} />
                <Select value={p.type} onValueChange={v => updateParam(idx, 'type', v)}>
                  <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="text">文本输入</SelectItem>
                    <SelectItem value="number">数字输入</SelectItem>
                    <SelectItem value="date">日期选择</SelectItem>
                    <SelectItem value="select">下拉选择</SelectItem>
                  </SelectContent>
                </Select>
                <Input size={20} placeholder="默认值" value={p.default || ''}
                  onChange={e => updateParam(idx, 'default', e.target.value)} />
                {p.type === 'select' && (
                  <Input size={20} className="col-span-2" placeholder="选项（逗号分隔）"
                    value={(p.options || []).join(',')}
                    onChange={e => updateParam(idx, 'options', e.target.value.split(',').map(s => s.trim()).filter(Boolean))} />
                )}
              </div>
              <Button variant="ghost" size="sm" className="h-8 w-8 p-0 flex-shrink-0"
                onClick={() => removeParam(idx)}>
                <X className="h-4 w-4 text-destructive" />
              </Button>
            </div>
          ))}
          <Button variant="outline" size="sm" onClick={addParam}>
            <Plus className="h-4 w-4 mr-1" />添加参数
          </Button>
        </div>
      </div>

      <PageParamsConfigSection pageParams={pageParams} setPageParams={setPageParams} />

      <div className="space-y-2">
        <Label>轮播间隔（秒）</Label>
        <div className="flex gap-2">
          {[5, 10, 30, 60].map(v => (
            <Button key={v} variant={carouselInterval === v ? 'default' : 'outline'} size="sm"
              onClick={() => setCarouselInterval(v)}>
              {v}秒
            </Button>
          ))}
        </div>
      </div>

      <Button onClick={handleSave}>保存</Button>
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const { workspaceId: urlWorkspaceId } = useParams<{ workspaceId: string }>();
  const {
    dashboards, currentId, loading, globalFilters, crossFilters, favorites,
    paramValues, pageParams, pageParamValues, refreshing,
    loadDashboards, setCurrent, createDashboard, updateDashboard,
    deleteDashboard, copyDashboard, setDefault, addChart, updateChart, reorderDashboards,
    setGlobalFilters, setCrossFilters, toggleFavorite, createFromTemplate,
    setParamValue, setPageParamValue, refreshCharts,
  } = useDashboardStore();

  const [addModalOpen, setAddModalOpen] = useState(false);
  const [carouselOpen, setCarouselOpen] = useState(false);
  const [carouselInterval, setCarouselInterval] = useState(10);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [newName, setNewName] = useState('');
  const [editingName, setEditingName] = useState<number | null>(null);
  const [nameValue, setNameValue] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [editingChartId, setEditingChartId] = useState<number | null>(null);
  const [editingChartName, setEditingChartName] = useState('');
  const [configPanelOpen, setConfigPanelOpen] = useState(false);
  const [selectedChart, setSelectedChart] = useState<any>(null);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [deleteTargetId, setDeleteTargetId] = useState<number | null>(null);

  const canvasRef = useRef<HTMLDivElement>(null);
  const [canvasSize, setCanvasSize] = useState({ width: CANVAS_WIDTH, height: CANVAS_HEIGHT });
  const [scale, setScale] = useState(1);
  const [panOffset, setPanOffset] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });

  useEffect(() => {
    const wsId = urlWorkspaceId ? Number(urlWorkspaceId) : undefined;
    loadDashboards(wsId);
  }, [urlWorkspaceId]);

  const current = useMemo(() => dashboards.find(d => d.id === currentId) || null, [dashboards, currentId]);

  // Auto-migrate settings-based params to canvas widgets (run once per dashboard)
  const migratedDashboardsRef = useRef<Set<number>>(new Set());
  useEffect(() => {
    if (!current) return;
    const params = current.params;
    if (!params || params.length === 0) return;
    if (migratedDashboardsRef.current.has(current.id)) return;

    // Check if migration already done (widget charts exist)
    const hasWidgets = current.charts.some(c => c.chart_type.startsWith('widget_'));
    if (hasWidgets) {
      migratedDashboardsRef.current.add(current.id);
      return;
    }

    // Mark as migrating to prevent re-entry
    migratedDashboardsRef.current.add(current.id);

    // Convert each param to a widget chart
    const typeMap: Record<string, string> = {
      text: 'widget_text',
      number: 'widget_number',
      date: 'widget_date',
      select: 'widget_select',
    };

    params.forEach((p, idx) => {
      const widgetType = typeMap[p.type] || 'widget_text';
      const widgetConfig: any = {
        paramKey: p.name,
        label: p.label || p.name,
        placeholder: p.placeholder || '',
        defaultValue: p.default || '',
        labelPosition: 'left',
      };

      if (p.type === 'select' && p.options?.length) {
        widgetConfig.options = p.options.map(o => ({ label: o, value: o }));
      }

      addChart(current.id, {
        name: p.label || p.name || `参数 ${idx + 1}`,
        chart_type: widgetType,
        sql_query: '',
        config: widgetConfig,
        source_type: 'widget',
        source_id: 0,
        data_cache: '',
        position: { x: 0, y: idx * 80, w: 240, h: 70 },
      } as any);
    });

    // Clear old params
    updateDashboard(current.id, { params: [] } as any);
    toast.success(`已将 ${params.length} 个参数迁移到画布控件`);
  }, [current?.id, current?.params]);

  const displayCharts = useMemo(() => {
    return current?.charts || [];
  }, [current]);

  useEffect(() => {
    if (!current || !displayCharts.length) {
      setCanvasSize({ width: CANVAS_WIDTH, height: CANVAS_HEIGHT });
      return;
    }
    let maxY = CANVAS_HEIGHT;
    displayCharts.forEach(chart => {
      const bottom = (chart.position?.y ?? 0) + (chart.position?.h ?? DEFAULT_CHART_SIZE.h);
      if (bottom > maxY) maxY = bottom;
    });
    setCanvasSize({ width: CANVAS_WIDTH, height: Math.max(CANVAS_HEIGHT, maxY + 200) });
  }, [current, displayCharts]);

  useEffect(() => {
    if (!canvasRef.current) return;
    const container = canvasRef.current.parentElement;
    if (!container) return;
    const containerWidth = container.clientWidth - 48;
    const containerHeight = container.clientHeight - 48;
    const scaleX = containerWidth / canvasSize.width;
    const scaleY = containerHeight / canvasSize.height;
    setScale(Math.min(scaleX, scaleY, 1));
  }, [canvasSize]);

  // Memoize parsed chart data to avoid creating new object references on every render
  const chartDataMap = useMemo(() => {
    const map: Record<number, { columns: string[]; rows: any[] } | null> = {};
    for (const chart of displayCharts) {
      if (chart.data_cache) {
        try {
          const parsed = JSON.parse(chart.data_cache);
          if (parsed.columns && parsed.rows) {
            map[chart.id] = parsed;
            continue;
          }
        } catch { /* ignore */ }
      }
      map[chart.id] = chart.config?.data || null;
    }
    return map;
  }, [displayCharts]);

  const handleFilterChange = useCallback((filterId: string, value: any) => {
    setGlobalFilters({ ...globalFilters, [filterId]: value });
  }, [globalFilters, setGlobalFilters]);

  const handleClearFilters = useCallback(() => setGlobalFilters({}), [setGlobalFilters]);
  const handleChartConfig = useCallback((chart: any) => { setSelectedChart(chart); setConfigPanelOpen(true); }, []);

  const handleSaveChartConfig = useCallback(async (configWithMeta: any) => {
    if (selectedChart) {
      // Extract meta fields prefixed with underscore
      const { _sql_query, _previewData, ...config } = configWithMeta;
      const updates: any = { config };

      // If SQL was edited, save it and update data cache
      if (_sql_query !== undefined) {
        updates.sql_query = _sql_query;
        if (_previewData) {
          updates.data_cache = JSON.stringify({ columns: _previewData.columns, rows: _previewData.rows.slice(0, 200) });
          updates.source_type = 'query';
        }
      }

      if (current) {
        await updateChart(current.id, selectedChart.id, { ...selectedChart, ...updates });
      }
      toast.success('图表配置已保存');
    }
  }, [selectedChart, current, updateChart]);

  const handleImportDashboard = useCallback(async (data: any) => {
    await createFromTemplate(data);
    toast.success('仪表盘导入成功');
  }, [createFromTemplate]);

  const handleRefresh = useCallback(() => refreshCharts(), [refreshCharts]);

  const handleApplyTemplate = useCallback(async (template: any) => {
    const newDashboardId = await createFromTemplate(template);
    if (newDashboardId) {
      const wsId = urlWorkspaceId ? Number(urlWorkspaceId) : undefined;
      await loadDashboards(wsId);
      setCurrent(newDashboardId);
      toast.success('仪表盘已从模板创建');
    }
  }, [createFromTemplate, loadDashboards, setCurrent, urlWorkspaceId]);

  const handleCrossFilterChange = useCallback((filters: CrossFilter[]) => setCrossFilters(filters), [setCrossFilters]);

  const handleAddChart = useCallback(async (chart: any) => {
    // Find available position
    const isWidget = chart.chart_type?.startsWith('widget_');
    const defaultSize = isWidget ? { w: 240, h: 70 } : DEFAULT_CHART_SIZE;

    const existingPositions = displayCharts.map(c => ({
      x: c.position?.x ?? 0, y: c.position?.y ?? 0,
      w: c.position?.w ?? DEFAULT_CHART_SIZE.w, h: c.position?.h ?? DEFAULT_CHART_SIZE.h,
    }));
    let newX = 0, newY = 0, found = false;
    for (let y = 0; y < canvasSize.height && !found; y += GRID_SIZE) {
      for (let x = 0; x < canvasSize.width - defaultSize.w && !found; x += GRID_SIZE) {
        const overlaps = existingPositions.some(pos =>
          x < pos.x + pos.w && x + defaultSize.w > pos.x &&
          y < pos.y + pos.h && y + defaultSize.h > pos.y
        );
        if (!overlaps) { newX = x; newY = y; found = true; }
      }
    }

    const chartWithPosition = {
      ...chart,
      position: { x: newX, y: newY, w: defaultSize.w, h: defaultSize.h },
    };

    if (current) {
      await addChart(current.id, chartWithPosition);
    }
    toast.success('图表已添加');
  }, [current, addChart, displayCharts, canvasSize]);

  const handleCreateDashboard = useCallback(async () => {
    const name = newName.trim() || `仪表盘 ${dashboards.length + 1}`;
    const wsId = urlWorkspaceId ? Number(urlWorkspaceId) : undefined;
    await createDashboard(name, wsId);
    setNewName('');
    toast.success('仪表盘已创建');
  }, [newName, dashboards.length, createDashboard, urlWorkspaceId]);

  const handleRename = useCallback(async (id: number) => {
    if (nameValue.trim()) {
      await updateDashboard(id, { name: nameValue.trim() } as any);
      toast.success('已重命名');
    }
    setEditingName(null);
  }, [nameValue, updateDashboard]);

  const handleSetDefault = useCallback(async (id: number) => {
    await setDefault(id);
    toast.success('已设为默认');
  }, [setDefault]);

  const handleDeleteDashboard = useCallback((id: number) => {
    setDeleteTargetId(id);
    setDeleteConfirmOpen(true);
  }, []);

  const confirmDeleteDashboard = useCallback(async () => {
    if (deleteTargetId) {
      await deleteDashboard(deleteTargetId);
      toast.success('已删除');
      setDeleteConfirmOpen(false);
      setDeleteTargetId(null);
    }
  }, [deleteTargetId, deleteDashboard]);

  const handleMoveDashboard = useCallback(async (id: number, direction: 'up' | 'down') => {
    const idx = dashboards.findIndex(d => d.id === id);
    if (idx < 0) return;
    const targetIdx = direction === 'up' ? idx - 1 : idx + 1;
    if (targetIdx < 0 || targetIdx >= dashboards.length) return;
    const newDashboards = [...dashboards];
    [newDashboards[idx], newDashboards[targetIdx]] = [newDashboards[targetIdx], newDashboards[idx]];
    await reorderDashboards(newDashboards.map((d, i) => ({ id: d.id, sort_order: i })));
  }, [dashboards, reorderDashboards]);

  const handleRenameChart = useCallback(async (chartId: number) => {
    if (!editingChartName.trim()) { setEditingChartId(null); return; }
    if (current) {
      const chart = current.charts.find(c => c.id === chartId);
      if (chart) {
        await updateChart(current.id, chartId, { ...chart, name: editingChartName.trim() } as any);
      }
    }
    setEditingChartId(null);
    toast.success('图表已重命名');
  }, [current, editingChartName, updateChart]);

  const handleDragStart = useCallback((_e: React.MouseEvent, _chartId: number) => {
    // Drag/resize removed from dashboard view — use fullscreen editor instead
  }, []);

  const handlePanStart = useCallback((e: React.MouseEvent) => {
    if (e.button === 1) {
      e.preventDefault();
      setIsPanning(true);
      setPanStart({ x: e.clientX - panOffset.x, y: e.clientY - panOffset.y });
    }
  }, [panOffset]);

  const handlePanMove = useCallback((e: MouseEvent) => {
    if (!isPanning) return;
    setPanOffset({ x: e.clientX - panStart.x, y: e.clientY - panStart.y });
  }, [isPanning, panStart]);

  const handlePanEnd = useCallback(() => setIsPanning(false), []);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    if (e.ctrlKey) {
      e.preventDefault();
      const delta = e.deltaY > 0 ? 0.9 : 1.1;
      const newScale = Math.min(2, Math.max(0.3, scale * delta));

      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return;

      const centerX = rect.width / 2;
      const centerY = rect.height / 2;
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;

      const scaleFactor = newScale / scale;
      const newPanX = panOffset.x * scaleFactor + (mouseX - centerX) * (1 - scaleFactor);
      const newPanY = panOffset.y * scaleFactor + (mouseY - centerY) * (1 - scaleFactor);

      setScale(newScale);
      setPanOffset({ x: newPanX, y: newPanY });
    }
  }, [scale, panOffset]);

  // Window-level event listeners for pan
  useEffect(() => {
    if (!isPanning) return;

    const handleGlobalMouseMove = (e: MouseEvent) => {
      handlePanMove(e);
    };

    const handleGlobalMouseUp = () => {
      handlePanEnd();
    };

    window.addEventListener('mousemove', handleGlobalMouseMove);
    window.addEventListener('mouseup', handleGlobalMouseUp);

    return () => {
      window.removeEventListener('mousemove', handleGlobalMouseMove);
      window.removeEventListener('mouseup', handleGlobalMouseUp);
    };
  }, [isPanning, handlePanMove, handlePanEnd]);

  // Cancel pan on Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (isPanning) setIsPanning(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isPanning]);

  if (loading && dashboards.length === 0) {
    return <div className="flex items-center justify-center h-screen"><Spinner size={48} /></div>;
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* Sidebar */}
      <div className={`flex-shrink-0 flex flex-col border-r bg-muted/30 transition-all duration-200 ${sidebarCollapsed ? 'w-0' : 'w-[220px]'}`}>
        <div className="p-3 border-b">
          <Button className="w-full" size="sm" onClick={handleCreateDashboard}>
            <Plus className="h-4 w-4 mr-2" />新建仪表盘
          </Button>
          <Input size={20} placeholder="仪表盘名称" value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleCreateDashboard()}
            className="mt-1.5" />
        </div>
        <ScrollArea className="flex-1 p-2">
          {dashboards.length === 0 && (
            <div className="text-center py-12 text-muted-foreground">暂无仪表盘</div>
          )}
          {dashboards.map((db, idx) => (
            <div
              key={db.id}
              className={`flex items-center gap-1.5 px-2.5 py-2 rounded-md cursor-pointer mb-1 transition-all ${
                currentId === db.id ? 'bg-primary/10 border border-primary/30' : 'border border-transparent hover:bg-muted'
              }`}
              onClick={() => setCurrent(db.id)}
            >
              <Tooltip>
                <TooltipTrigger asChild>
                  <span
                    onClick={(e) => { e.stopPropagation(); toggleFavorite(db.id); }}
                    className="flex-shrink-0 cursor-pointer"
                  >
                    {favorites.includes(db.id) ? (
                      <Star className="h-3 w-3 fill-yellow-500 text-yellow-500" />
                    ) : db.is_default ? (
                      <Star className="h-3 w-3 fill-yellow-500 text-yellow-500" />
                    ) : (
                      <BarChart3 className={`h-3 w-3 ${currentId === db.id ? 'text-primary' : 'text-muted-foreground'}`} />
                    )}
                  </span>
                </TooltipTrigger>
                <TooltipContent>{favorites.includes(db.id) ? '取消收藏' : '收藏'}</TooltipContent>
              </Tooltip>
              {editingName === db.id ? (
                <div className="flex items-center gap-1 flex-1">
                  <Input value={nameValue} autoFocus
                    onChange={e => setNameValue(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') handleRename(db.id); }}
                    onClick={e => e.stopPropagation()}
                    className="text-xs flex-1" />
                  <Check className="h-3 w-3 text-primary cursor-pointer" onClick={() => handleRename(db.id)} />
                  <X className="h-3 w-3 text-muted-foreground cursor-pointer" onClick={() => setEditingName(null)} />
                </div>
              ) : (
                <span className={`text-xs flex-1 truncate ${currentId === db.id ? 'text-primary' : ''} ${db.is_default ? 'font-medium' : ''}`}>
                  {db.name}
                </span>
              )}
              <div className="flex items-center gap-0 flex-shrink-0">
                <ArrowUp className={`h-3 w-3 text-muted-foreground cursor-pointer ${idx === 0 ? 'opacity-30' : ''}`}
                  onClick={(e) => { e.stopPropagation(); handleMoveDashboard(db.id, 'up'); }} />
                <ArrowDown className={`h-3 w-3 text-muted-foreground cursor-pointer ${idx === dashboards.length - 1 ? 'opacity-30' : ''}`}
                  onClick={(e) => { e.stopPropagation(); handleMoveDashboard(db.id, 'down'); }} />
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-5 w-5 p-0 ml-1"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Settings className="h-3 w-3" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent>
                    <DropdownMenuItem onClick={() => handleSetDefault(db.id)}>
                      <Star className="h-4 w-4 mr-2" />设为默认
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => { setEditingName(db.id); setNameValue(db.name); }}>
                      <Edit className="h-4 w-4 mr-2" />重命名
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => copyDashboard(db.id)}>
                      <Copy className="h-4 w-4 mr-2" />拷贝
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => handleDeleteDashboard(db.id)} className="text-destructive">
                      <Trash2 className="h-4 w-4 mr-2" />删除
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>
          ))}
        </ScrollArea>
      </div>

      {/* Main area */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        {/* Toolbar */}
        <div className="flex items-center justify-between px-4 py-2 border-b flex-shrink-0 gap-2 flex-wrap">
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => setSidebarCollapsed(!sidebarCollapsed)}>
              <Move className="h-4 w-4" />
            </Button>
            <h2 className="text-base font-bold">{current?.name || '仪表盘设计'}</h2>
            {current?.is_default && <Star className="h-4 w-4 fill-yellow-500 text-yellow-500" />}
            {current && (
              <Badge variant="outline" className={`text-xs ${DASHBOARD_STATUS_MAP[current.status || 'designing'].color}`}>
                {DASHBOARD_STATUS_MAP[current.status || 'designing'].label}
              </Badge>
            )}
            <span className="text-xs text-muted-foreground ml-2">
              {Math.round(scale * 100)}% | {canvasSize.width}×{canvasSize.height}
            </span>
          </div>
          <div className="flex gap-1.5 flex-wrap items-center">
              <>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button size="sm" variant="outline" onClick={() => navigate(`/dashboard/editor/${currentId}`)} disabled={!current}>
                      <Edit className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>编辑</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button size="sm" variant="outline" onClick={() => setTemplatesOpen(true)}>
                      <Layout className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>模板</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button size="sm" variant="outline" onClick={() => setSearchOpen(true)}>
                      <Search className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>搜索</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button size="sm" variant="outline"
                      onClick={() => { setCarouselInterval(current?.carousel_interval || 10); setCarouselOpen(true); }}
                      disabled={dashboards.length === 0}>
                      <Play className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>轮播</TooltipContent>
                </Tooltip>
                <DashboardExportImport dashboard={current} onImport={handleImportDashboard} />
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button size="sm" variant="outline" onClick={() => setSettingsOpen(true)} disabled={!current}>
                      <Settings className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>设置</TooltipContent>
                </Tooltip>
              </>
          </div>
        </div>

        {/* Auto-refresh */}
        {current && <DashboardAutoRefresh onRefresh={handleRefresh} loading={loading || refreshing} />}

        {/* Page Params Bar */}
        {pageParams.length > 0 && (
          <PageParamsBar
            params={pageParams}
            values={pageParamValues}
            onChange={setPageParamValue}
          />
        )}

        {/* Dashboard Params */}
        {current?.params && current.params.length > 0 && (
          <DashboardParams
            params={current.params}
            values={paramValues}
            onChange={setParamValue}
          />
        )}

        {/* Global Filters */}
        <DashboardFilters filters={DEFAULT_FILTERS} values={globalFilters} onChange={handleFilterChange} onClearAll={handleClearFilters} />

        {/* Cross Filters */}
        <DashboardCrossFilter filters={crossFilters} onFilterChange={handleCrossFilterChange} charts={displayCharts} />

        {/* Canvas area */}
        <div
          className={`dashboard-canvas-viewport flex-1 overflow-auto relative ${isPanning ? 'panning' : ''}`}
          onWheel={handleWheel} onMouseDown={handlePanStart}
        >
          {!current ? (
            <div className="flex flex-col items-center justify-center h-full text-center px-4">
              <LayoutDashboard className="h-16 w-16 text-muted-foreground/30 mb-4" />
              <p className="text-lg font-medium text-muted-foreground mb-2">请选择或创建一个仪表盘</p>
              <p className="text-sm text-muted-foreground/70 mb-6">仪表盘可以帮助您可视化和监控关键业务指标</p>
              <Button onClick={handleCreateDashboard}>
                <Plus className="h-4 w-4 mr-2" />创建第一个仪表盘
              </Button>
            </div>
          ) : displayCharts.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center px-4">
              <BarChart3 className="h-16 w-16 text-muted-foreground/30 mb-4" />
              <p className="text-lg font-medium text-muted-foreground mb-2">暂无图表</p>
              <p className="text-sm text-muted-foreground/70 mb-6">进入编辑器添加图表和控件</p>
              <Button size="lg" onClick={() => navigate(`/dashboard/editor/${currentId}`)}>
                <Edit className="h-5 w-5 mr-2" />进入编辑器
              </Button>
            </div>
          ) : (
            <div className="flex items-center justify-center min-h-full p-8">
              <div
                ref={canvasRef}
                className="dashboard-canvas"
                style={{
                  width: canvasSize.width, height: canvasSize.height,
                  transform: `translate(${panOffset.x}px, ${panOffset.y}px) scale(${scale})`,
                  transformOrigin: 'center center',
                  flexShrink: 0,
                }}
              >
                {displayCharts.map((chart) => {
                  const chartData = chartDataMap[chart.id];
                  const posX = chart.position?.x ?? 0;
                  const posY = chart.position?.y ?? 0;
                  const posW = chart.position?.w ?? DEFAULT_CHART_SIZE.w;
                  const posH = chart.position?.h ?? DEFAULT_CHART_SIZE.h;

                  return (
                    <div
                      key={chart.id}
                      className="dashboard-chart-cell"
                      style={{
                        position: 'absolute', left: posX, top: posY, width: posW, height: posH,
                        zIndex: 1,
                        ...(chart.chart_type.startsWith('widget_') ? (() => {
                          const ws = chart.config?.widgetStyle || {};
                          return {
                            background: ws.backgroundColor || undefined,
                            borderRadius: ws.borderRadius != null ? ws.borderRadius : undefined,
                            borderLeft: `3px solid ${ws.borderColor || 'hsl(var(--primary))'}`,
                            borderWidth: ws.borderWidth != null ? ws.borderWidth : undefined,
                            borderColor: ws.borderColor || undefined,
                            borderStyle: ws.borderStyle || undefined,
                            boxShadow: ws.boxShadow || undefined,
                            opacity: ws.opacity != null ? ws.opacity : undefined,
                          };
                        })() : {}),
                      }}
                      onMouseDown={(e) => handleDragStart(e, chart.id)}
                    >
                      {!chart.chart_type.startsWith('widget_') && (
                      <div className="dashboard-chart-header flex items-center justify-between px-3.5 py-2.5 flex-shrink-0">
                        {editingChartId === chart.id ? (
                          <div className="flex items-center gap-1 flex-1">
                            <Input value={editingChartName} autoFocus
                              onChange={e => setEditingChartName(e.target.value)}
                              onKeyDown={e => { if (e.key === 'Enter') handleRenameChart(chart.id); }}
                              onClick={e => e.stopPropagation()}
                              className="text-sm flex-1" />
                            <Check className="h-4 w-4 text-primary cursor-pointer" onClick={() => handleRenameChart(chart.id)} />
                            <X className="h-4 w-4 text-muted-foreground cursor-pointer" onClick={() => setEditingChartId(null)} />
                          </div>
                        ) : (
                          <span className="text-sm font-medium truncate flex-1">
                            {chart.name}
                          </span>
                        )}
                        <div className="flex gap-1 ml-2 flex-shrink-0">
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button size="sm" variant="ghost" className="h-7 w-7 p-0"
                                onClick={(e) => { e.stopPropagation(); handleChartConfig(chart); }}>
                                <Settings className="h-4 w-4 text-muted-foreground" />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>图表配置</TooltipContent>
                          </Tooltip>
                        </div>
                      </div>
                      )}
                      <div className="p-2 flex-1 min-h-0 overflow-hidden"
                        style={{
                          ...(chart.chart_type.startsWith('widget_') && chart.config?.widgetStyle ? {
                            color: chart.config.widgetStyle.textColor || undefined,
                            fontSize: chart.config.widgetStyle.fontSize || undefined,
                          } : {}),
                        }}
                      >
                        <DashboardChart
                          chartType={chart.chart_type}
                          data={chartData || { columns: [], rows: [] }}
                          config={chart.config || {}}
                          chartId={chart.id}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Modals */}
      <AddChartModal open={addModalOpen} onClose={() => setAddModalOpen(false)} onAdd={handleAddChart} />

      {carouselOpen && (
        <CarouselView
          dashboards={dashboards}
          startIndex={dashboards.findIndex(d => d.id === currentId)}
          interval={carouselInterval}
          onClose={() => setCarouselOpen(false)}
        />
      )}

      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent className="max-w-[600px] max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>仪表盘设置</DialogTitle>
          </DialogHeader>
          {current && (
            <DashboardSettingsContent
              current={current}
              carouselInterval={carouselInterval}
              setCarouselInterval={setCarouselInterval}
              updateDashboard={updateDashboard}
              onClose={() => setSettingsOpen(false)}
            />
          )}
        </DialogContent>
      </Dialog>

      <ChartConfigPanel open={configPanelOpen} chart={selectedChart}
        onClose={() => { setConfigPanelOpen(false); setSelectedChart(null); }}
        onSave={handleSaveChartConfig} />

      <DashboardTemplates open={templatesOpen} onClose={() => setTemplatesOpen(false)} onApply={handleApplyTemplate} />

      <Dialog open={searchOpen} onOpenChange={setSearchOpen}>
        <DialogContent className="max-w-[600px]">
          <DialogHeader>
            <DialogTitle>搜索仪表盘</DialogTitle>
          </DialogHeader>
          <DashboardSearch dashboards={dashboards}
            onSelect={(id) => { setCurrent(id); setSearchOpen(false); }}
            onToggleFavorite={toggleFavorite} favorites={favorites} />
        </DialogContent>
      </Dialog>

      <Dialog open={deleteConfirmOpen} onOpenChange={setDeleteConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-destructive" />
              确定删除此仪表盘？
            </DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">删除后不可恢复，包含的所有图表也将被删除。</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteConfirmOpen(false)}>取消</Button>
            <Button variant="destructive" onClick={confirmDeleteDashboard}>删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
