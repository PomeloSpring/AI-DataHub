import { useState, useCallback, useEffect } from 'react';
import { toast } from 'sonner';
import { Settings, Copy, Play, Loader2, Database, Link, Plus, Trash2 } from 'lucide-react';
import { CHART_TYPES, ChartIcon } from './DashboardChart';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Slider } from '@/components/ui/slider';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import client from '../api/client';

interface ChartConfigPanelProps {
  open: boolean;
  chart: any;
  onClose: () => void;
  onSave: (config: any) => void;
}

const CHART_COLORS = [
  '#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de',
  '#3ba272', '#fc8452', '#9a60b4', '#ea7ccc', '#48b8d0',
  '#f7c5a0', '#d4a4eb', '#a3d4f7', '#b8e6b8', '#ffd700',
];

export default function ChartConfigPanel({ open, chart, onClose, onSave }: ChartConfigPanelProps) {
  const [config, setConfig] = useState<any>({});
  const [sqlQuery, setSqlQuery] = useState('');
  const [executing, setExecuting] = useState(false);
  const [previewData, setPreviewData] = useState<{ columns: string[]; rows: any[] } | null>(null);
  const [datasources, setDatasources] = useState<any[]>([]);
  const [selectedDsId, setSelectedDsId] = useState<number>(0);
  const [dashboardsList, setDashboardsList] = useState<any[]>([]);

  // Load datasources and sync from chart prop on open
  useEffect(() => {
    if (open) {
      const chartConfig = chart?.config || {};
      const dsId = chart?.source_id || chartConfig.datasource_id || 0;

      setConfig(chartConfig);
      setSqlQuery(chart?.sql_query || '');
      setSelectedDsId(dsId);
      setPreviewData(null);

      client.get('/datasources/').then(({ data }) => {
        setDatasources(data);
        // Auto-select default only if chart has no datasource configured
        if (!dsId && data.length > 0) {
          const defaultDs = data.find((d: any) => d.is_default) || data[0];
          setSelectedDsId(defaultDs.id);
        }
      }).catch(() => {});

      // Load dashboards list for drill-through link config
      client.get('/dashboard/').then(({ data }) => {
        setDashboardsList(data);
      }).catch(() => {});
    }
  }, [open, chart]);

  const handleSave = useCallback(() => {
    onSave({
      ...config,
      _chart_type: config.chartType || chart?.chart_type,
      _sql_query: sqlQuery,
      _previewData: previewData,
      _datasource_id: selectedDsId,
    });
    onClose();
  }, [config, chart, sqlQuery, previewData, selectedDsId, onSave, onClose]);

  const handleExecuteSql = async () => {
    if (!sqlQuery.trim()) return;
    setExecuting(true);
    try {
      const { data } = await client.post('/playground/execute', {
        sql: sqlQuery.trim(),
        datasource_id: selectedDsId || undefined,
      });
      if (data.columns && data.rows) {
        setPreviewData({ columns: data.columns, rows: data.rows });
        toast.success(`查询成功，返回 ${data.rows.length} 行`);
      } else if (data.detail) {
        toast.error(data.detail);
      }
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '执行失败');
    } finally {
      setExecuting(false);
    }
  };

  const updateConfig = useCallback((key: string, value: any) => {
    setConfig((prev: any) => ({ ...prev, [key]: value }));
  }, []);

  const isWidget = chart?.chart_type?.startsWith('widget_');

  if (!chart) return null;

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-[600px] max-h-[80vh]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Settings className="h-5 w-5" />
            {isWidget ? '控件配置' : '图表配置'} - {chart.name}
          </DialogTitle>
        </DialogHeader>

        <ScrollArea className="h-[500px] pr-4">
          <Tabs defaultValue={isWidget ? 'widget' : 'basic'}>
            <TabsList className="w-full">
              {isWidget ? (
                <TabsTrigger value="widget" className="flex-1">参数设置</TabsTrigger>
              ) : (
                <>
                  <TabsTrigger value="basic" className="flex-1">基础设置</TabsTrigger>
                  <TabsTrigger value="style" className="flex-1">样式设置</TabsTrigger>
                  <TabsTrigger value="axis" className="flex-1">坐标轴</TabsTrigger>
                  <TabsTrigger value="datasource" className="flex-1">数据源</TabsTrigger>
                </>
              )}
            </TabsList>

            {isWidget && (
              <TabsContent value="widget" className="space-y-4 mt-4">
                <div className="space-y-2">
                  <Label>参数Key</Label>
                  <Input
                    value={config.paramKey || ''}
                    onChange={e => updateConfig('paramKey', e.target.value)}
                    placeholder="如 site, date_start"
                  />
                  <p className="text-xs text-muted-foreground">
                    图表 SQL 中使用 {'{{paramKey}}'} 引用此参数值
                  </p>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>显示标签</Label>
                    <Input
                      value={config.label || ''}
                      onChange={e => updateConfig('label', e.target.value)}
                      placeholder="参数名称"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>标签位置</Label>
                    <Select value={config.labelPosition || 'top'} onValueChange={v => updateConfig('labelPosition', v)}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="top">上方</SelectItem>
                        <SelectItem value="left">左侧</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>占位提示</Label>
                  <Input
                    value={config.placeholder || ''}
                    onChange={e => updateConfig('placeholder', e.target.value)}
                    placeholder="请输入/请选择"
                  />
                </div>

                <div className="space-y-2">
                  <Label>默认值</Label>
                  <Input
                    value={config.defaultValue ?? ''}
                    onChange={e => updateConfig('defaultValue', e.target.value)}
                    placeholder="留空则无默认值"
                  />
                </div>

                <div className="space-y-2">
                  <Label>绑定页面参数 (bind_param)</Label>
                  <Input
                    value={config.bind_param || ''}
                    onChange={e => updateConfig('bind_param', e.target.value)}
                    placeholder={chart.chart_type === 'widget_daterange' ? '如 date_start,date_end（逗号分隔）' : '如 site, date_start'}
                  />
                  <p className="text-xs text-muted-foreground">
                    {chart.chart_type === 'widget_daterange'
                      ? '日期范围绑定两个页面参数，用逗号分隔（如 date_start,date_end）'
                      : '控件值变化时自动更新对应的页面参数'}
                  </p>
                </div>

                {(chart.chart_type === 'widget_select' || chart.chart_type === 'widget_multi_select') && (
                  <div className="space-y-2">
                    <Label>选项（逗号分隔）</Label>
                    <Input
                      value={(config.options || []).map((o: any) => o.label || o.value).join(',')}
                      onChange={e => {
                        const opts = e.target.value.split(',').map(s => s.trim()).filter(Boolean)
                          .map(s => ({ label: s, value: s }));
                        updateConfig('options', opts);
                      }}
                      placeholder="北京,上海,广州"
                    />
                  </div>
                )}

                {chart.chart_type === 'widget_number' && (
                  <div className="grid grid-cols-3 gap-2">
                    <div className="space-y-2">
                      <Label>最小值</Label>
                      <Input type="number" value={config.min ?? ''} onChange={e => updateConfig('min', e.target.value ? Number(e.target.value) : undefined)} />
                    </div>
                    <div className="space-y-2">
                      <Label>最大值</Label>
                      <Input type="number" value={config.max ?? ''} onChange={e => updateConfig('max', e.target.value ? Number(e.target.value) : undefined)} />
                    </div>
                    <div className="space-y-2">
                      <Label>步长</Label>
                      <Input type="number" value={config.step || 1} onChange={e => updateConfig('step', Number(e.target.value) || 1)} />
                    </div>
                  </div>
                )}
              </TabsContent>
            )}

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

              {/* Table value specific settings */}
              {(config.chartType || chart.chart_type) === 'table_value' && (
                <>
                  <Separator />
                  <div className="space-y-4">
                    <h4 className="font-medium">表值图设置</h4>
                    <div className="space-y-2">
                      <Label>最大显示行数</Label>
                      <Input
                        type="number"
                        value={config.maxRows || 100}
                        onChange={e => updateConfig('maxRows', parseInt(e.target.value) || 100)}
                      />
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
                      <Label>启用分页</Label>
                    </div>
                    {config.enablePagination !== false && (
                      <div className="space-y-2">
                        <Label>每页行数</Label>
                        <Select
                          value={String(config.pageSize || 20)}
                          onValueChange={v => updateConfig('pageSize', parseInt(v))}
                        >
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="10">10 行</SelectItem>
                            <SelectItem value="20">20 行</SelectItem>
                            <SelectItem value="50">50 行</SelectItem>
                            <SelectItem value="100">100 行</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={config.enableServerPagination === true}
                        onCheckedChange={v => updateConfig('enableServerPagination', v)}
                      />
                      <Label>服务端分页</Label>
                    </div>
                    {config.enableServerPagination && (
                      <>
                        <div className="space-y-1">
                          <Label>每页条数 (page_limit)</Label>
                          <Select
                            value={String(config.pageLimit || 20)}
                            onValueChange={v => updateConfig('pageLimit', parseInt(v))}
                          >
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value="10">10</SelectItem>
                              <SelectItem value="20">20</SelectItem>
                              <SelectItem value="50">50</SelectItem>
                              <SelectItem value="100">100</SelectItem>
                              <SelectItem value="200">200</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        <div className="space-y-1">
                          <Label>总数查询 SQL (可选)</Label>
                          <textarea
                            className="w-full h-16 text-xs p-2 rounded border bg-background resize-none"
                            placeholder="如: SELECT COUNT(*) AS cnt FROM dwd_table WHERE 1=1 AND ('{{site}}' = '' OR site = '{{site}}')"
                            value={config.countSql || ''}
                            onChange={e => updateConfig('countSql', e.target.value)}
                          />
                          <p className="text-xs text-muted-foreground">留空则自动用主 SQL 包装 COUNT(*)，填写后更精确</p>
                        </div>
                        <p className="text-xs text-muted-foreground">
                          主 SQL 中使用 <code>{'{{page_limit}}'}</code> 和 <code>{'{{page_offset}}'}</code>，后端自动拼接 LIMIT/OFFSET
                        </p>
                      </>
                    )}
                    {config.enablePagination === false && !config.enableServerPagination && (
                      <p className="text-xs text-muted-foreground">分页已关闭，将显示全部数据</p>
                    )}
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
                                {(() => {
                                  try {
                                    const cached = chart?.data_cache ? JSON.parse(chart.data_cache) : null;
                                    return (cached?.columns || previewData?.columns || []).map((col: string) => (
                                      <SelectItem key={col} value={col}>{col}</SelectItem>
                                    ));
                                  } catch { return null; }
                                })()}
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
                        config.colors?.includes(color) ? 'border-primary' : 'border-transparent'
                      }`}
                      style={{ background: color }}
                      onClick={() => {
                        const currentColors = config.colors || [];
                        const newColors = currentColors.includes(color)
                          ? currentColors.filter((c: string) => c !== color)
                          : [...currentColors, color];
                        updateConfig('colors', newColors);
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
                <Label>X轴标签旋转角度: {config.xAxisLabelRotate || 0}°</Label>
                <Slider
                  min={0}
                  max={90}
                  value={[config.xAxisLabelRotate || 0]}
                  onValueChange={([v]) => updateConfig('xAxisLabelRotate', v)}
                />
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

            <TabsContent value="datasource" className="space-y-4 mt-4">
              {/* Datasource selector */}
              <div className="space-y-2">
                <Label className="flex items-center gap-2">
                  <Database className="h-4 w-4" />
                  数据源
                </Label>
                <Select
                  value={String(selectedDsId || '')}
                  onValueChange={(v) => setSelectedDsId(Number(v))}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="选择数据源" />
                  </SelectTrigger>
                  <SelectContent>
                    {datasources.map((ds) => (
                      <SelectItem key={ds.id} value={String(ds.id)}>
                        <div className="flex items-center gap-2">
                          <Database className="h-4 w-4 text-muted-foreground" />
                          {ds.name}
                          <Badge variant="outline" className="text-xs">{ds.db_type}</Badge>
                          {ds.is_default ? <Badge variant="secondary" className="text-xs">默认</Badge> : null}
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  💡 选择数据源后，SQL 将在该数据源上执行。即使是快照图表也可以切换数据源重新查询。
                </p>
              </div>

              <Separator />

              {/* Chart info */}
              <div className="p-4 bg-muted rounded-lg">
                <h4 className="font-medium mb-2">图表信息</h4>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div><span className="text-muted-foreground">图表ID：</span>{chart.id}</div>
                  <div><span className="text-muted-foreground">图表类型：</span>{chart.chart_type}</div>
                  <div><span className="text-muted-foreground">数据来源：</span>{chart.source_type === 'empty' ? '空图表' : chart.source_type || 'query'}</div>
                  <div><span className="text-muted-foreground">更新时间：</span>{chart.updated_at ? new Date(chart.updated_at).toLocaleString('zh-CN') : '-'}</div>
                </div>
              </div>

              {/* SQL query */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <Label>SQL查询</Label>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!sqlQuery.trim()}
                      onClick={() => {
                        navigator.clipboard.writeText(sqlQuery);
                        toast.success('SQL已复制到剪贴板');
                      }}
                    >
                      <Copy className="h-4 w-4 mr-1" />
                      复制
                    </Button>
                    <Button
                      size="sm"
                      disabled={!sqlQuery.trim() || executing}
                      onClick={handleExecuteSql}
                    >
                      {executing ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Play className="h-4 w-4 mr-1" />}
                      执行
                    </Button>
                  </div>
                </div>
                <Textarea
                  value={sqlQuery}
                  onChange={e => setSqlQuery(e.target.value)}
                  placeholder="SELECT * FROM table_name WHERE ...&#10;&#10;输入SQL查询语句，点击执行按钮预览数据"
                  rows={6}
                  className="font-mono text-sm"
                />
                <p className="text-xs text-muted-foreground mt-1">
                  💡 修改SQL后点击"执行"预览数据，保存后生效。数据源快照图表的SQL也会在此展示。
                </p>
              </div>

              {/* Preview data */}
              {previewData && previewData.rows.length > 0 && (
                <div>
                  <h4 className="font-medium mb-2">数据预览 <Badge variant="secondary">{previewData.rows.length} 行</Badge></h4>
                  <div className="border rounded-lg overflow-auto max-h-[200px]">
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-muted">
                        <tr>
                          {previewData.columns.map((col: string) => (
                            <th key={col} className="px-2 py-1.5 text-left font-medium text-muted-foreground">{col}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {previewData.rows.slice(0, 20).map((row: any, i: number) => (
                          <tr key={i} className="border-t hover:bg-muted/50">
                            {previewData.columns.map((col: string) => (
                              <td key={col} className="px-2 py-1">{row[col]?.toString() || '-'}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </TabsContent>
          </Tabs>
        </ScrollArea>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={handleSave}>保存</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
