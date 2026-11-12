import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { toast } from '@/lib/toast';
import { Settings, Database, Link, Plus, Trash2, ExternalLink, AlertTriangle, Play } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { CHART_TYPES, ChartIcon } from './DashboardChart';
import { TABLE_VALUE_PAGE_SIZES, pageSizeOf } from '@/lib/dashboardDesign';
import { isDemoChart } from '@/lib/dashboardDemo';
import { applyFieldFormats } from '@/lib/fieldFormat';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Slider } from '@/components/ui/slider';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { SearchSelect } from '@/components/ui/search-select';
import { DATASET_KIND_LABELS, datasetPreviewPayload, datasetSearchText } from '@/lib/datasetPickerFilter';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import client from '../api/client';

interface ChartConfigPanelProps {
  open: boolean;
  chart: any;
  onClose: () => void;
  onSave: (config: any) => void | Promise<void>;
}

/** 预览样本上限：看数用，不是导出；真取数仍走图表刷新（带看板参数与分页） */
const PREVIEW_ROWS = 20;

const CHART_COLORS = [
  '#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de',
  '#3ba272', '#fc8452', '#9a60b4', '#ea7ccc', '#48b8d0',
  '#f7c5a0', '#d4a4eb', '#a3d4f7', '#b8e6b8', '#ffd700',
];

export default function ChartConfigPanel({ open, chart, onClose, onSave }: ChartConfigPanelProps) {
  const [config, setConfig] = useState<any>({});
  const semantic = chart?.query_source === 'semantic' || !!chart?.semantic_query;
  const [saving, setSaving] = useState(false);
  const saveLock = useRef(false);
  // 取数口径属于数据集（2026-11-06 拍板）：本面板不再录入/执行 SQL，只能从
  // 「角色权限 → 数据集」清单里选一个数据集（清单与组件库「数据集」页同一接口）。
  const [datasets, setDatasets] = useState<any[]>([]);
  const [datasetsLoading, setDatasetsLoading] = useState(false);
  const [datasetsError, setDatasetsError] = useState('');
  const [datasetId, setDatasetId] = useState<number>(0);
  // 查看数据：走数据集预览入口（`POST /datasets/{id}/preview` → query_dataset），
  // 服务端编译口径 + 角色权限/RLS/敏感屏蔽全部施加，本面板仍不录入也不执行 SQL。
  const [preview, setPreview] = useState<{ columns: string[]; rows: any[] } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [dashboardsList, setDashboardsList] = useState<any[]>([]);
  const navigate = useNavigate();

  const loadDatasets = useCallback(() => {
    setDatasetsLoading(true);
    setDatasetsError('');
    // 清单唯一数据源 = /datasets/（后端按角色授权裁决, 与组件库「数据集」页同一口径）：
    // 不得在本面板另拼一份"能挂哪些数据集"的判断（那会出现清单里看不到但能挂上）。
    client.get('/datasets/').then(({ data }) => {
      setDatasets(Array.isArray(data?.items) ? data.items : []);
    }).catch(() => {
      // 清单拉不到不能退成"没有数据集"（那会被读成业务结论），也不能在看不见清单时
      // 让用户保存（静默丢绑挂）：显式报错 + 可重试
      setDatasets([]);
      setDatasetsError('数据集清单加载失败，请重试；未加载清单时无法改挂数据集。');
    }).finally(() => setDatasetsLoading(false));
  }, []);

  // Load datasets and sync from chart prop on open
  useEffect(() => {
    if (open) {
      const chartConfig = chart?.config || {};

      setConfig(chartConfig);
      setDatasetId(String(chart?.source_type) === 'dataset' ? Number(chart?.source_id || 0) : 0);
      // 上一张图的样本不能跟着面板“留着”，否则看起来像这张图的数据
      setPreview(null);
      setPreviewError('');

      if (!semantic) loadDatasets();

      // Load dashboards list for drill-through link config
      client.get('/dashboard/').then(({ data }) => {
        setDashboardsList(data);
      }).catch(() => {});
    }
  }, [open, chart, semantic, loadDatasets]);

  // 换数据集就丢掉上一份样本，避免“看着是新数据集的数据，其实是旧响应”
  useEffect(() => { setPreview(null); setPreviewError(''); }, [datasetId]);

  /** 预览当前所选数据集的样本行：口径由服务端编译，权限/RLS/敏感屏蔽照旧施加 */
  const runPreview = useCallback(async () => {
    if (!datasetId) { setPreviewError('请先选择数据集'); return; }
    setPreviewLoading(true);
    setPreviewError('');
    setPreview(null);
    try {
      // 只给行数上限，不塞字段映射（实测：SQL 型传字段会编译出非法 SQL，见 datasetPreviewPayload）
      // 选源只给业务名（护栏 §7）：钉了就按钉的那个预览，未钉不注入（交服务端定源）
      const payload: Record<string, any> = datasetPreviewPayload(PREVIEW_ROWS);
      if (config.datasource) payload.datasource = String(config.datasource);
      const { data } = await client.post(`/datasets/${datasetId}/preview`, payload);
      // 显示格式随结果下发（数据集声明），预览表与看板渲染走同一实现，
      // 避免"配置面板里还是 T00:00:00.000、存进看板就变了"的双口径
      setPreview(applyFieldFormats({
        columns: data.columns || [],
        rows: (data.rows || []).slice(0, PREVIEW_ROWS),
        field_formats: data.field_formats,
      }));
    } catch (e: any) {
      // 服务端回的是业务原因文案（包候选源/口径问题），不得退成“没有数据”冒充结果
      setPreviewError(e?.response?.data?.detail || '预览未完成，请稍后重试或到数据集页核实该数据集是否可取数');
    } finally {
      setPreviewLoading(false);
    }
  }, [datasetId, config.datasource]);

  const selectedDataset = datasets.find(d => Number(d.id) === Number(datasetId)) || null;
  const boundDataset = String(chart?.source_type) === 'dataset' && !!chart?.source_id;
  // 看板级选源（2026-11-12）：当前身份在该数据集上真正可执行的数据源业务名清单
  // （后端 `usable_now` = 可用范围 ∩ 角色授权源，与取数/挂图同一裁决口径，不在前端另判）。
  // 长度 > 1 才给切换器（只有一个源时没得切，渲出来就是空承诺）。
  const sourceScope: string[] = (selectedDataset?.usable_now?.length
    ? selectedDataset.usable_now : selectedDataset?.available_source_names) || [];
  const pinnedSource = String(config.datasource || '');
  const legacySqlChart = !semantic && !!(chart?.sql_query || '').trim()
    && !(chart?.config as any)?.as_bot_design && !isDemoChart({ config });
  // 字段候选：已取数的列 + 数据集声明的字段业务名（选择框不让人手拼列名）
  const columnOptions = useMemo(() => {
    const cols: string[] = [];
    try {
      const cached = chart?.data_cache ? JSON.parse(chart.data_cache) : null;
      (cached?.columns || []).forEach((c: any) => c && cols.push(String(c)));
    } catch { /* 脏缓存不污染候选清单 */ }
    const decl = selectedDataset?.field_config;
    const fields = typeof decl === 'string' ? (() => { try { return JSON.parse(decl); } catch { return []; } })() : decl;
    (Array.isArray(fields) ? fields : []).forEach((f: any) => {
      const name = f?.label || f?.field;
      if (name) cols.push(String(name));
    });
    return Array.from(new Set(cols));
  }, [chart?.data_cache, selectedDataset]);

  const handleSave = useCallback(async () => {
    if (saveLock.current) return;
    if (!semantic && datasetsLoading) { toast.warning('数据集清单尚未加载完成，请稍候再保存'); return; }
    if (!semantic && datasetsError) { toast.error(datasetsError); return; }
    // 数据集图表必须真的挂上一个清单内的数据集（挂不上就报错，不开“保存了但无数据”的空承诺）
    if (!semantic && !datasetId && String(chart?.source_type) === 'dataset') {
      toast.error('请从清单里选择一个数据集（或先把图表改为尚未配数的占位图）');
      return;
    }
    saveLock.current = true; setSaving(true);
    try {
      const updates: any = { ...config, _chart_type: config.chartType || chart?.chart_type };
      if (!semantic) updates._dataset_id = datasetId;
      await onSave(updates);
      onClose();
    } catch { toast.error('保存失败，配置已保留，请重试'); }
    finally { saveLock.current = false; setSaving(false); }
  }, [config, chart, semantic, datasetId, datasetsLoading, datasetsError, onSave, onClose]);

  const updateConfig = useCallback((key: string, value: any) => {
    setConfig((prev: any) => ({ ...prev, [key]: value }));
  }, []);

  // 控件配置唯一入口在右侧属性面板（PropertyPanel），本面板只服务图表；
  // 两处各配一半曾经造成同一 config 的双口径混乱。
  const isWidget = chart?.chart_type?.startsWith('widget_');

  if (!chart || isWidget) return null;

  return (
    <Dialog open={open} onOpenChange={value => { if (!value && !saving) onClose(); }}>
      <DialogContent className="max-w-[600px] max-h-[80vh]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Settings className="h-5 w-5" />
            图表配置 - {chart.name}
          </DialogTitle>
        </DialogHeader>

        <ScrollArea className="h-[500px] pr-4">
          <Tabs defaultValue="basic">
            <TabsList className="w-full">
              <TabsTrigger value="basic" className="flex-1">基础设置</TabsTrigger>
              <TabsTrigger value="style" className="flex-1">样式设置</TabsTrigger>
              <TabsTrigger value="axis" className="flex-1">坐标轴</TabsTrigger>
              {!semantic && <TabsTrigger value="datasource" className="flex-1">数据</TabsTrigger>}
            </TabsList>

            <TabsContent value="basic" className="space-y-4 mt-4">
              <div className="space-y-2">
                <Label>图表标题</Label>
                <Input
                  placeholder="输入图表标题"
                  value={config.title || ''}
                  onChange={e => updateConfig('title', e.target.value)}
                />
              </div>

              <div className="space-y-2">
                <Label>图表类型</Label>
                <Select value={config.chartType || chart.chart_type || 'bar'} onValueChange={v => updateConfig('chartType', v)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CHART_TYPES.map(ct => (
                      <SelectItem key={ct.value} value={ct.value}>
                        <span className="flex items-center gap-2">
                          <ChartIcon name={ct.icon} className="h-4 w-4" />
                          {ct.label}
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>X轴列</Label>
                  <Input
                    placeholder="自动选择"
                    value={config.xCol || ''}
                    onChange={e => updateConfig('xCol', e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label>Y轴列</Label>
                  <Input
                    placeholder="自动选择"
                    value={config.yCol || ''}
                    onChange={e => updateConfig('yCol', e.target.value)}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label>分组列</Label>
                <Input
                  placeholder="自动检测（如站点、类别等）"
                  value={config.groupCol || ''}
                  onChange={e => updateConfig('groupCol', e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  💡 分组列用于多维度分组展示，例如：时间为X轴，统计数为Y轴，站点为分组列，则不同站点会用不同颜色的折线显示。
                </p>
              </div>

              {/* 多系列柱图的排布：分组（各系列并排、颜色区分）还是堆叠（累计口径）。
                  默认分组：堆叠会把几个系列挤进同一根柱，读不出谁是谁。 */}
              {['bar', 'timeseries_bar'].includes(config.chartType || chart.chart_type || 'bar') && (
                <div className="space-y-2">
                  <Label>多系列排布</Label>
                  <Select value={config.seriesLayout === 'stack' ? 'stack' : 'group'}
                    onValueChange={v => updateConfig('seriesLayout', v)}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="group">分组（每个系列一根柱，颜色区分）</SelectItem>
                      <SelectItem value="stack">堆叠（看部分与整体累计）</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              )}

              {/* Table value specific settings */}
              {(config.chartType || chart.chart_type) === 'table_value' && (
                <>
                  <Separator />
                  <div className="space-y-4">
                    <h4 className="font-medium">表值图设置</h4>
                    <div className="space-y-2">
                      <Label>每页行数</Label>
                      <Select
                        value={String(pageSizeOf(config))}
                        onValueChange={v => updateConfig('pageSize', parseInt(v))}
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {TABLE_VALUE_PAGE_SIZES.map(n => (
                            <SelectItem key={n} value={String(n)}>{n} 行</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <p className="text-xs text-muted-foreground">
                        分页由服务端按 pageSize / pageNum 取数（每页上限 100 行）：
                        绑定了「支持分页」数据集的明细表恒定按页取，SQL 通道可直接跳页，
                        ES 通道按游标逐页往后翻；统计展示型数据集整页返回。
                      </p>
                      {semantic && (
                        <p className="text-xs text-amber-600">
                          声明式语义图表不支持按页取数（语义层只产 LIMIT n，没有 OFFSET/行总数形状），
                          请改挂数据集。
                        </p>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={config.showIndex !== false}
                        onCheckedChange={v => updateConfig('showIndex', v)}
                      />
                      <Label>显示行号</Label>
                    </div>
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={config.striped !== false}
                        onCheckedChange={v => updateConfig('striped', v)}
                      />
                      <Label>斑马纹</Label>
                    </div>
                    <Separator />
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={config.enablePagination !== false}
                        onCheckedChange={v => updateConfig('enablePagination', v)}
                      />
                      <Label>行数超出时显示分页条</Label>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      只影响不支持分页的数据集（整页返回的统计结果）。明细表走服务端真分页时
                      分页条恒显示 —— 关掉它只会让用户看不到后面还有多少页。
                    </p>
                  </div>
                </>
              )}

              {/* Drill-through link config for table_value */}
              {(config.chartType || chart.chart_type) === 'table_value' && (
                <>
                  <Separator />
                  <div className="space-y-4">
                    <h4 className="font-medium flex items-center gap-2">
                      <Link className="h-4 w-4" />
                      钻透链接
                    </h4>
                    <p className="text-xs text-muted-foreground">
                      配置表格列的钻透链接，点击单元格可跳转到目标页面
                    </p>

                    {(config.links || []).map((link: any, idx: number) => (
                      <div key={idx} className="border rounded-lg p-3 space-y-3">
                        <div className="flex items-center justify-between">
                          <Badge variant="outline">链接 {idx + 1}</Badge>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              const newLinks = [...(config.links || [])];
                              newLinks.splice(idx, 1);
                              updateConfig('links', newLinks);
                            }}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                          <div className="space-y-1">
                            <Label className="text-xs">链接列</Label>
                            <Select
                              value={link.column || ''}
                              onValueChange={v => {
                                const newLinks = [...(config.links || [])];
                                newLinks[idx] = { ...newLinks[idx], column: v };
                                updateConfig('links', newLinks);
                              }}
                            >
                              <SelectTrigger><SelectValue placeholder="选择列" /></SelectTrigger>
                              <SelectContent>
                                {columnOptions.map((col: string) => (
                                  <SelectItem key={col} value={col}>{col}</SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>

                          <div className="space-y-1">
                            <Label className="text-xs">目标页面</Label>
                            <Select
                              value={String(link.target_page_id || '')}
                              onValueChange={v => {
                                const newLinks = [...(config.links || [])];
                                newLinks[idx] = { ...newLinks[idx], target_page_id: Number(v) };
                                updateConfig('links', newLinks);
                              }}
                            >
                              <SelectTrigger><SelectValue placeholder="选择页面" /></SelectTrigger>
                              <SelectContent>
                                {dashboardsList.map((d: any) => (
                                  <SelectItem key={d.id} value={String(d.id)}>{d.name}</SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                        </div>

                        <div className="space-y-1">
                          <Label className="text-xs">打开方式</Label>
                          <Select
                            value={link.open_mode || 'modal'}
                            onValueChange={v => {
                              const newLinks = [...(config.links || [])];
                              newLinks[idx] = { ...newLinks[idx], open_mode: v };
                              updateConfig('links', newLinks);
                            }}
                          >
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value="modal">弹窗打开</SelectItem>
                              <SelectItem value="new_page">新页面打开</SelectItem>
                              <SelectItem value="same_page">当前页面参数</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>

                        <div className="space-y-1">
                          <Label className="text-xs">参数映射</Label>
                          <p className="text-[10px] text-muted-foreground">格式：目标参数名=源列名，每行一个</p>
                          <Textarea
                            className="h-16 text-xs font-mono"
                            placeholder={"region=region\ndate=dt"}
                            value={Object.entries(link.param_mapping || {}).map(([k, v]) => `${k}=${v}`).join('\n')}
                            onChange={e => {
                              const mapping: Record<string, string> = {};
                              e.target.value.split('\n').forEach(line => {
                                const [k, v] = line.split('=').map(s => s.trim());
                                if (k && v) mapping[k] = v;
                              });
                              const newLinks = [...(config.links || [])];
                              newLinks[idx] = { ...newLinks[idx], param_mapping: mapping };
                              updateConfig('links', newLinks);
                            }}
                          />
                        </div>
                      </div>
                    ))}

                    <Button
                      variant="outline"
                      size="sm"
                      className="w-full"
                      onClick={() => {
                        const newLinks = [...(config.links || []), {
                          column: '',
                          target_page_id: 0,
                          open_mode: 'modal',
                          param_mapping: {},
                        }];
                        updateConfig('links', newLinks);
                      }}
                    >
                      <Plus className="h-4 w-4 mr-1" />
                      添加钻透链接
                    </Button>
                  </div>
                </>
              )}

              {/* Text display specific settings */}
              {(config.chartType || chart.chart_type) === 'text_display' && (
                <>
                  <Separator />
                  <div className="space-y-4">
                    <h4 className="font-medium">文本展示设置</h4>
                    <div className="space-y-2">
                      <Label>数值格式</Label>
                      <Select value={config.valueFormat || 'number'} onValueChange={v => updateConfig('valueFormat', v)}>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="number">数字 (1,234)</SelectItem>
                          <SelectItem value="percent">百分比 (12.3%)</SelectItem>
                          <SelectItem value="currency">货币 (¥1,234)</SelectItem>
                          <SelectItem value="raw">原始值</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-2">
                      <Label>前缀</Label>
                      <Input
                        placeholder="如 ¥、#、总量："
                        value={config.valuePrefix || ''}
                        onChange={e => updateConfig('valuePrefix', e.target.value)}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>后缀</Label>
                      <Input
                        placeholder="如 个、次、%"
                        value={config.valueSuffix || ''}
                        onChange={e => updateConfig('valueSuffix', e.target.value)}
                      />
                    </div>
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={config.showComparison !== false}
                        onCheckedChange={v => updateConfig('showComparison', v)}
                      />
                      <Label>显示同比/环比</Label>
                    </div>
                    <div className="space-y-2">
                      <Label>同比基准列</Label>
                      <Input
                        placeholder="去年同月数据列名"
                        value={config.yoyColumn || ''}
                        onChange={e => updateConfig('yoyColumn', e.target.value)}
                      />
                      <p className="text-xs text-muted-foreground">
                        💡 同比：与去年同期对比（YoY），需在 SQL 中查询去年数据作为单独列
                      </p>
                    </div>
                    <div className="space-y-2">
                      <Label>环比基准列</Label>
                      <Input
                        placeholder="上月数据列名"
                        value={config.momColumn || ''}
                        onChange={e => updateConfig('momColumn', e.target.value)}
                      />
                      <p className="text-xs text-muted-foreground">
                        💡 环比：与上一周期对比（MoM），需在 SQL 中查询上期数据作为单独列
                      </p>
                    </div>
                    <div className="space-y-2">
                      <Label>数值字号: {config.valueFontSize || 48}px</Label>
                      <Slider
                        min={24}
                        max={96}
                        value={[config.valueFontSize || 48]}
                        onValueChange={([v]) => updateConfig('valueFontSize', v)}
                      />
                    </div>
                  </div>
                </>
              )}

              <Separator />

              <div className="space-y-4">
                <h4 className="font-medium">时间聚合</h4>

                <div className="flex items-center gap-2">
                  <Switch
                    checked={config.enableTimeAgg || false}
                    onCheckedChange={v => updateConfig('enableTimeAgg', v)}
                  />
                  <Label>启用时间聚合</Label>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>时间粒度</Label>
                    <Select
                      value={config.timeGranularity || 'auto'}
                      onValueChange={v => updateConfig('timeGranularity', v)}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="auto">自动检测</SelectItem>
                        <SelectItem value="hour">按小时</SelectItem>
                        <SelectItem value="day">按天</SelectItem>
                        <SelectItem value="week">按周</SelectItem>
                        <SelectItem value="month">按月</SelectItem>
                        <SelectItem value="year">按年</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label>聚合方式</Label>
                    <Select
                      value={config.aggMethod || 'sum'}
                      onValueChange={v => updateConfig('aggMethod', v)}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="sum">求和</SelectItem>
                        <SelectItem value="avg">平均值</SelectItem>
                        <SelectItem value="max">最大值</SelectItem>
                        <SelectItem value="min">最小值</SelectItem>
                        <SelectItem value="count">计数</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <p className="text-xs text-muted-foreground">
                  💡 时间聚合会自动按指定粒度对数据进行分组汇总。例如选择"按天"，则同一小时的数据会合并为一条。
                </p>
              </div>
            </TabsContent>

            <TabsContent value="style" className="space-y-4 mt-4">
              <div className="space-y-2">
                <Label>颜色方案</Label>
                <div className="flex flex-wrap gap-2">
                  {CHART_COLORS.map((color, index) => (
                    <div
                      key={index}
                      className={`w-8 h-8 rounded cursor-pointer border-2 ${
                        config.colorScheme?.includes(color) ? 'border-primary' : 'border-transparent'
                      }`}
                      style={{ background: color }}
                      onClick={() => {
                        const currentColors = config.colorScheme || [];
                        const newColors = currentColors.includes(color)
                          ? currentColors.filter((c: string) => c !== color)
                          : [...currentColors, color];
                        updateConfig('colorScheme', newColors);
                      }}
                    />
                  ))}
                </div>
              </div>

              <div className="space-y-2">
                <Label>边框圆角: {config.borderRadius || 10}px</Label>
                <Slider
                  min={0}
                  max={20}
                  value={[config.borderRadius || 10]}
                  onValueChange={([v]) => updateConfig('borderRadius', v)}
                />
              </div>

              <div className="space-y-2">
                <Label>内边距: {config.padding || 8}px</Label>
                <Slider
                  min={0}
                  max={32}
                  value={[config.padding || 8]}
                  onValueChange={([v]) => updateConfig('padding', v)}
                />
              </div>
            </TabsContent>

            <TabsContent value="axis" className="space-y-4 mt-4">
              <div className="flex items-center gap-2">
                <Switch
                  checked={config.showXAxis !== false}
                  onCheckedChange={v => updateConfig('showXAxis', v)}
                />
                <Label>显示X轴</Label>
              </div>

              <div className="space-y-2">
                <Label>
                  X轴标签旋转角度: {config.xAxisLabelRotate ? `${config.xAxisLabelRotate}°` : '自动'}
                </Label>
                <Slider
                  min={0}
                  max={90}
                  value={[config.xAxisLabelRotate || 0]}
                  onValueChange={([v]) => updateConfig('xAxisLabelRotate', v)}
                />
                {/* 0 = 自动：类目多/标签长时由图表引擎选角度并撑高轴带，避免 x 轴文字挤成一坨 */}
                <p className="text-[10px] text-muted-foreground">0° 为自动（数据多、名称长时自动倾斜并留足轴下空间）</p>
              </div>

              <div className="flex items-center gap-2">
                <Switch
                  checked={config.showYAxis !== false}
                  onCheckedChange={v => updateConfig('showYAxis', v)}
                />
                <Label>显示Y轴</Label>
              </div>

              <div className="space-y-2">
                <Label>Y轴名称</Label>
                <Input
                  placeholder="Y轴名称"
                  value={config.yAxisName || ''}
                  onChange={e => updateConfig('yAxisName', e.target.value)}
                />
              </div>

              <div className="flex items-center gap-2">
                <Switch
                  checked={config.showGrid !== false}
                  onCheckedChange={v => updateConfig('showGrid', v)}
                />
                <Label>显示网格线</Label>
              </div>
            </TabsContent>

            {!semantic && <TabsContent value="datasource" className="space-y-4 mt-4">
              {/* 取数口径唯一入口：从角色授权内的数据集清单选一个（本面板不录入也不执行 SQL） */}
              <div className="space-y-2">
                <Label className="flex items-center gap-2">
                  <Database className="h-4 w-4" />
                  数据集
                </Label>
                {/* 可搜索选择（ui-resource-display §2）：清单 20+ 条时下拉盲选不可用，
                    关键词同时命名称/描述/字段业务名/数据源名（keywords 不展示只参与匹配） */}
                <SearchSelect
                  ariaLabel="数据集"
                  value={String(datasetId || '')}
                  onValueChange={(v: string) => { setDatasetId(Number(v)); updateConfig('datasource', undefined); }}
                  options={datasets.map((ds: any) => ({
                    value: String(ds.id),
                    label: ds.name,
                    description: [
                      ds.datasource_name || '默认引擎',
                      DATASET_KIND_LABELS[String(ds.source_type || 'sql').toLowerCase()] || ds.source_type,
                      ds.field_count ? `${ds.field_count} 字段` : '',
                    ].filter(Boolean).join(' · '),
                    keywords: datasetSearchText(ds),
                  }))}
                  placeholder={datasetsLoading ? '正在加载数据集清单…' : '搜索并选择数据集'}
                  searchPlaceholder="按名称/描述/字段/数据源过滤…"
                  emptyText="当前角色清单内没有可选数据集"
                  loading={datasetsLoading}
                  error={datasetsError}
                  onRetry={loadDatasets}
                  disabled={datasetsLoading}
                />
                {datasetsError && (
                  <div className="flex items-center gap-2 text-xs text-destructive">
                    <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" />
                    <span className="flex-1">{datasetsError}</span>
                    <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={loadDatasets}>重试</Button>
                  </div>
                )}
                {!datasetsLoading && !datasetsError && datasets.length === 0 && (
                  <p className="text-xs text-muted-foreground">
                    当前角色没有可挂的数据集。可挂清单 =「角色权限 → 数据集」已授权
                    <span className="whitespace-nowrap"> ∧ </span>「角色权限 → 数据源」至少能执行该数据集的一个源：
                    两层任一为空都会回到这里（请先到「数据集」页定义口径，再找管理员补授权）。
                  </p>
                )}
                <p className="text-xs text-muted-foreground">
                  💡 口径在「数据集」页定义（字段声明 / 认证 / 血缘 / 角色清单都在那一层），
                  看板只做可视化装配；数据集改了口径图表即生效（不复制 SQL，单口径）。
                </p>

                {/* 执行数据源切换（看板级选源）：仅当该数据集对当前身份可在 >1 个源上执行时提供。
                    选项一律业务名（内部 id 属黑盒 §7）；存进 config.datasource，保存后随刷新下递服务端。 */}
                {sourceScope.length > 1 && (
                  <div className="space-y-1.5 rounded-md border bg-muted/20 p-2">
                    <Label className="flex items-center gap-1.5 text-xs">
                      <Database className="h-3.5 w-3.5" />执行数据源
                    </Label>
                    <Select value={pinnedSource || '__auto__'}
                      onValueChange={v => updateConfig('datasource', v === '__auto__' ? undefined : v)}>
                      <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__auto__">自动（服务端定源）</SelectItem>
                        {sourceScope.map(n => <SelectItem key={n} value={n}>{n}</SelectItem>)}
                      </SelectContent>
                    </Select>
                    <p className="text-[11px] text-muted-foreground">
                      该数据集可在 {sourceScope.length} 个已授权数据源上执行；此处为当前图表钉选执行源（存业务名，
                      跨查看者按其角色权限裁决）。不选则按数据集默认口径自动定源。
                    </p>
                  </div>
                )}

                {/* 查看数据：只预览已授权数据集的样本行（服务端编译与治理），不录入也不执行任何 SQL */}
                <div className="space-y-2 pt-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Button size="sm" variant="outline" className="h-7 text-xs"
                      onClick={runPreview}
                      disabled={!datasetId || previewLoading || datasetsLoading || !!datasetsError}>
                      <Play className="h-3 w-3 mr-1" />
                      {previewLoading ? '取数中…' : '预览数据'}
                    </Button>
                    <span className="text-[11px] text-muted-foreground">
                      按数据集口径取前 {PREVIEW_ROWS} 行（未套用看板当前参数与筛选）
                    </span>
                  </div>
                  {previewError && (
                    <div role="alert" className="flex items-center gap-2 text-xs text-destructive">
                      <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" />
                      <span className="flex-1">{previewError}</span>
                      <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={runPreview}
                        disabled={previewLoading}>重试</Button>
                    </div>
                  )}
                  {preview && (preview.columns.length === 0
                    ? <p className="text-xs text-muted-foreground">本次预览没有返回行列（口径可能命中空集）。</p>
                    : (
                      <div className="max-h-52 overflow-auto rounded-md border">
                        <table className="w-full text-[11px]">
                          <thead className="sticky top-0 bg-muted">
                            <tr>{preview.columns.map(c => (
                              <th key={c} className="px-2 py-1 text-left font-medium whitespace-nowrap">{c}</th>
                            ))}</tr>
                          </thead>
                          <tbody>
                            {preview.rows.map((r, i) => (
                              <tr key={i} className="border-t">
                                {preview.columns.map(c => (
                                  <td key={c} className="px-2 py-1 whitespace-nowrap">{r?.[c] ?? ''}</td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ))}
                  {preview && (
                    <p className="text-[11px] text-muted-foreground">
                      样本 {preview.rows.length} 行 · {preview.columns.length} 列；列名与行数均为当前身份可见口径（敏感列已按治理剔除/脱敏）。
                    </p>
                  )}
                </div>
              </div>

              <Separator />

              {/* Chart info（资源引用一律显示业务名，不裸显内部 id） */}
              <div className="p-4 bg-muted rounded-lg space-y-2">
                <h4 className="font-medium mb-2">图表信息</h4>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div>
                    <span className="text-muted-foreground">当前数据集：</span>
                    {selectedDataset?.name
                      || (boundDataset ? '不在当前角色清单内（可能已撤权/停用，请重新选择）' : '尚未绑定')}
                  </div>
                  <div><span className="text-muted-foreground">图表类型：</span>
                    {CHART_TYPES.find(t => t.value === (config.chartType || chart.chart_type))?.label || chart.chart_type}
                  </div>
                  <div>
                    <span className="text-muted-foreground">执行数据源：</span>
                    {pinnedSource
                      || selectedDataset?.datasource_name
                      || (boundDataset ? '按查看者角色定源' : '未绑定')}
                    {pinnedSource && <span className="ml-1 text-[11px] text-muted-foreground">（本图钉选）</span>}
                  </div>
                  <div><span className="text-muted-foreground">更新时间：</span>
                    {chart.updated_at ? new Date(chart.updated_at).toLocaleString('zh-CN') : '-'}
                  </div>
                </div>
                {(selectedDataset || (boundDataset && chart?.source_id)) && (
                  <Button variant="link" size="sm" className="h-6 p-0 text-xs flex items-center gap-1"
                    onClick={() => navigate(`/data/datasets/${datasetId || chart.source_id}`)}>
                    <ExternalLink className="h-3.5 w-3.5" />在数据集页查看口径
                  </Button>
                )}
              </div>

              {legacySqlChart && (
                <div className="rounded-lg border border-amber-300 bg-amber-50 dark:bg-amber-950/30 p-3 text-xs flex items-start gap-2">
                  <AlertTriangle className="h-4 w-4 text-amber-600 flex-shrink-0 mt-0.5" />
                  <div className="space-y-1">
                    <p className="font-medium">该图表仍携带旧版直写 SQL（该取数通道已下线）</p>
                    <p>选一个数据集后保存即可改挂；口径本体请到「数据集」页重新定义，图表不再展示与编辑 SQL。</p>
                  </div>
                </div>
              )}
            </TabsContent>}
          </Tabs>
          {semantic && <p className="mt-4 text-xs text-muted-foreground">此图表使用声明式语义查询。可调整字段映射与外观，不展示或修改生成 SQL。</p>}
        </ScrollArea>

        <DialogFooter>
          <Button variant="outline" disabled={saving} onClick={onClose}>取消</Button>
          <Button disabled={saving} onClick={handleSave}>{saving ? '保存中…' : '保存'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
