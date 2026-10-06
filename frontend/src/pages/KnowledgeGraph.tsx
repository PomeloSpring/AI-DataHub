import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import {
  Database, Network, GitBranch, Search, RefreshCw, Terminal,
} from 'lucide-react';
import KnowledgeGraphView from '@/components/graph/KnowledgeGraph';
import { ModelGraphTab } from './catalog/ModelAssetTabs';
import { useGraphStore } from '@/stores/graphStore';
import axios from 'axios';
import { toast } from 'sonner';

// ── Types ──────────────────────────────────────────────────────────────

type GraphType = 'ontology-overview' | 'table-relation' | 'business-knowledge' | 'data-lineage';
type ViewMode = 'view' | 'edit' | 'ask';
type ActiveTab = 'graph' | 'sparql';

// ── API Helpers ────────────────────────────────────────────────────────

const API_BASE = '/api/graph';

function getAuthHeader() {
  const token = localStorage.getItem('token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ── Main Component ─────────────────────────────────────────────────────

/** 系统配置入口的精简图页: 只展示 AS-BOT 系统能力本体图(锁定总览, ds:0) */
function AsBotGraphPage() {
  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-4 p-4 border-b bg-background shrink-0">
        <div className="flex items-center gap-2">
          <Network className="h-6 w-6 text-primary" />
          <h1 className="text-xl font-semibold">AS-BOT 本体图</h1>
        </div>
        <p className="text-sm text-muted-foreground">
          仅展示系统助手依赖的本体模型结构（对象为点、Link 为名边）；业务本体图谱请前往「数据平台 → 本体工作区 → 图谱」。
        </p>
      </div>
      <div className="flex-1 p-4 min-h-0">
        {/* 系统助手总览必须走系统图 ds:-1(kind=system), 否则 datasource_id=0 命中聚合图串入业务本体 */}
        <ModelGraphTab datasourceId={0} kind="system" singleView="ontology-overview" />
      </div>
    </div>
  );
}

export default function KnowledgeGraph({ systemOnly = false }: { systemOnly?: boolean } = {}) {
  // 拆独立组件避免条件化 hooks; 全功能图页见本体工作区图谱页签/旧路由。
  if (systemOnly) return <AsBotGraphPage />;
  return <KnowledgeGraphFull />;
}

function KnowledgeGraphFull() {
  const [searchParams, setSearchParams] = useSearchParams();

  // State from URL — 默认落地视图 = 本体总览(对象=点, Link=名边; 业务主语视角)
  const graphType = (searchParams.get('type') as GraphType) || 'ontology-overview';
  const viewMode = (searchParams.get('mode') as ViewMode) || 'view';

  // Local state
  const [activeTab, setActiveTab] = useState<ActiveTab>('graph');
  const [searchQuery, setSearchQuery] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  // SPARQL query state
  // SPARQL 数据均入命名图 <...ds:ID>，默认查询需遍历 GRAPH 才有结果
  const [sparqlQuery, setSparqlQuery] = useState('SELECT ?s ?p ?o WHERE { GRAPH ?g { ?s ?p ?o } } LIMIT 25');
  const [sparqlResult, setSparqlResult] = useState<any>(null);

  // Store
  const {
    selectedNode,
    fetchGraphData, searchNodes, syncGraph,
    getUpstream, getDownstream, getImpactAnalysis,
    setSelectedNode
  } = useGraphStore();

  // ── Effects ────────────────────────────────────────────────────────

  useEffect(() => {
    if (activeTab === 'graph') {
      loadGraphData();
    }
  }, [graphType, activeTab]);

  // ── Graph Handlers ─────────────────────────────────────────────────

  const loadGraphData = async () => {
    setIsLoading(true);
    try {
      await fetchGraphData({ graphType, limit: 200 });
    } catch (error) {
      toast.error('加载图谱数据失败');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) {
      await loadGraphData();
      return;
    }
    setIsLoading(true);
    try {
      await searchNodes(searchQuery);
    } catch (error) {
      toast.error('搜索失败');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSync = async () => {
    setIsLoading(true);
    try {
      const result = await syncGraph(0);
      if (result.success) {
        toast.success(
          `图谱构建完成: ${result.tables} 表, ${result.columns} 字段, ${result.terms} 术语, ` +
          `${result.metrics ?? 0} 指标, ${result.sql_templates ?? 0} SQL 模板`
        );
        await loadGraphData();
      } else {
        toast.error(result.message || '图谱构建失败');
      }
    } catch (error) {
      toast.error('图谱构建失败');
    } finally {
      setIsLoading(false);
    }
  };

  // ── SPARQL Handler ────────────────────────────────────────────────

  const handleExecuteSparql = async () => {
    setIsLoading(true);
    try {
      const response = await axios.post(`${API_BASE}/query`, { query: sparqlQuery }, { headers: getAuthHeader() });
      setSparqlResult(response.data);
      toast.success('查询执行成功');
    } catch (error) {
      toast.error('SPARQL查询失败');
    } finally {
      setIsLoading(false);
    }
  };

  // ── Config ─────────────────────────────────────────────────────────

  const graphTypeConfig = {
    'ontology-overview': { icon: Network, label: '本体总览', description: '业务对象为点、Link 为带名字的边；列/表/数据源不在此层' },
    'table-relation': { icon: Database, label: '表关系图', description: '展示数据库表之间的关联关系' },
    'business-knowledge': { icon: Network, label: '业务知识图', description: '展示业务术语、指标、维度的定义关系' },
    'data-lineage': { icon: GitBranch, label: '数据血缘图', description: '展示数据从源头到应用的流转路径' }
  };

  // ── Render ─────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b bg-background">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Network className="h-6 w-6 text-primary" />
            <h1 className="text-xl font-semibold">知识图谱</h1>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleSync}
            disabled={isLoading}
            title="从元数据重建知识图谱（表/字段/术语/指标/SQL 模板）"
          >
            <RefreshCw className={`h-4 w-4 mr-1 ${isLoading ? 'animate-spin' : ''}`} />
            构建图谱
          </Button>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-hidden">
        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as ActiveTab)} className="h-full flex flex-col">
          <div className="px-4 pt-2 border-b">
            <TabsList>
              <TabsTrigger value="graph" className="flex items-center gap-2">
                <Network className="h-4 w-4" />
                图谱可视化
              </TabsTrigger>
              <TabsTrigger value="sparql" className="flex items-center gap-2">
                <Terminal className="h-4 w-4" />
                SPARQL查询
              </TabsTrigger>
            </TabsList>
          </div>

          {/* Graph Tab */}
          <TabsContent value="graph" className="flex-1 overflow-hidden m-0">
            <div className="flex h-full">
              {/* Left Sidebar */}
              <div className="w-64 border-r bg-muted/30 flex flex-col p-4 space-y-4">
                {/* Graph Type Selector */}
                <div>
                  <Label className="text-xs font-medium mb-2 block">图谱类型</Label>
                  <div className="space-y-2">
                    {Object.entries(graphTypeConfig).map(([type, config]) => (
                      <Button
                        key={type}
                        variant={graphType === type ? 'default' : 'outline'}
                        className="w-full justify-start"
                        size="sm"
                        onClick={() => setSearchParams({ type, mode: viewMode })}
                      >
                        <config.icon className="h-4 w-4 mr-2" />
                        {config.label}
                      </Button>
                    ))}
                  </div>
                </div>

                {/* Search */}
                <div>
                  <Label className="text-xs font-medium mb-2 block">搜索</Label>
                  <div className="flex gap-2">
                    <Input
                      placeholder="搜索节点..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                      className="h-8 text-xs"
                    />
                    <Button variant="outline" size="sm" onClick={handleSearch} className="h-8 w-8 p-0">
                      <Search className="h-4 w-4" />
                    </Button>
                  </div>
                </div>

                {/* Node Detail */}
                {selectedNode && (
                  <div className="flex-1 overflow-auto">
                    <Label className="text-xs font-medium mb-2 block">节点详情</Label>
                    <Card>
                      <CardHeader className="p-3">
                        <CardTitle className="text-sm flex items-center gap-2">
                          <Badge>{selectedNode.label}</Badge>
                          <span className="truncate">{selectedNode.properties.name || selectedNode.id}</span>
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="p-3 pt-0">
                        <ScrollArea className="h-48">
                          <div className="space-y-2 text-xs">
                            {Object.entries(selectedNode.properties).map(([key, value]) => (
                              <div key={key}>
                                <span className="text-muted-foreground">{key}: </span>
                                <span className="font-mono">{String(value)}</span>
                              </div>
                            ))}
                          </div>
                        </ScrollArea>
                      </CardContent>
                    </Card>

                    {/* Lineage Actions */}
                    {graphType === 'data-lineage' && (
                      <div className="mt-4 space-y-2">
                        <Label className="text-xs font-medium">血缘追踪</Label>
                        <div className="grid grid-cols-2 gap-2">
                          <Button variant="outline" size="sm" className="text-xs" onClick={async () => {
                            const data = await getUpstream(selectedNode.id);
                            toast.info(`找到 ${data.nodes.length} 个上游节点`);
                          }}>
                            <GitBranch className="h-3 w-3 mr-1 rotate-180" />
                            上游追溯
                          </Button>
                          <Button variant="outline" size="sm" className="text-xs" onClick={async () => {
                            const data = await getDownstream(selectedNode.id);
                            toast.info(`找到 ${data.nodes.length} 个下游节点`);
                          }}>
                            <GitBranch className="h-3 w-3 mr-1" />
                            下游追踪
                          </Button>
                        </div>
                        <Button variant="outline" size="sm" className="w-full text-xs" onClick={async () => {
                          const result = await getImpactAnalysis(selectedNode.id);
                          if (result) {
                            toast.info(`影响分析: ${result.impact_summary.total_affected} 个节点受影响`);
                          }
                        }}>
                          <Network className="h-3 w-3 mr-1" />
                          影响分析
                        </Button>
                      </div>
                    )}
                  </div>
                )}

                {/* Legend */}
                <div className="border-t pt-4">
                  <Label className="text-xs font-medium mb-2 block">图例</Label>
                  <div className="space-y-1.5 text-xs">
                    {graphType === 'ontology-overview' && (
                      <>
                        <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-indigo-500" /><span>业务对象</span></div>
                        <div className="flex items-center gap-2"><div className="w-4 h-0.5 bg-indigo-400" /><span>Link(名字·基数)</span></div>
                        <div className="flex items-center gap-2"><div className="w-2 h-2 rounded-full bg-green-500" /><span>已绑定</span><div className="w-2 h-2 rounded-full bg-amber-500" /><span>漂移</span></div>
                      </>
                    )}
                    <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-blue-500" /><span>表</span></div>
                    <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-green-500" /><span>字段</span></div>
                    <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-purple-500" /><span>术语</span></div>
                    <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-orange-500" /><span>指标</span></div>
                    <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-teal-500" /><span>维度</span></div>
                    {graphType === 'data-lineage' && (
                      <>
                        <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-cyan-500" /><span>数据源</span></div>
                        <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-amber-500" /><span>ETL任务</span></div>
                      </>
                    )}
                  </div>
                </div>
              </div>

              {/* Graph Canvas */}
              <div className="flex-1 relative">
                <KnowledgeGraphView
                  graphType={graphType}
                  viewMode={viewMode}
                  isLoading={isLoading}
                  onNodeSelect={setSelectedNode}
                  onRefresh={loadGraphData}
                />
              </div>
            </div>
          </TabsContent>

          {/* SPARQL Query Tab */}
          <TabsContent value="sparql" className="flex-1 overflow-auto m-0 p-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Terminal className="h-5 w-5" />
                  SPARQL 查询控制台
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label>查询语句</Label>
                  <Textarea
                    value={sparqlQuery}
                    onChange={(e) => setSparqlQuery(e.target.value)}
                    placeholder="输入SPARQL查询语句..."
                    className="font-mono text-sm h-32"
                  />
                </div>
                <Button onClick={handleExecuteSparql} disabled={isLoading}>
                  <Terminal className="h-4 w-4 mr-2" />
                  执行查询
                </Button>
                {sparqlResult && (
                  <div className="space-y-2">
                    <Label>查询结果</Label>
                    <pre className="bg-muted p-4 rounded-lg text-xs overflow-auto max-h-96">
                      {JSON.stringify(sparqlResult, null, 2)}
                    </pre>
                  </div>
                )}
                <div className="text-xs text-muted-foreground">
                  💡 常用查询示例：
                  <ul className="mt-1 space-y-1 list-disc list-inside">
                    <li><code className="px-1 bg-muted rounded">SELECT ?s ?p ?o WHERE {'{'} GRAPH ?g {'{'} ?s ?p ?o {'}'} {'}'} LIMIT 25</code> - 查看所有三元组(需遍历命名图 GRAPH)</li>
                    <li><code className="px-1 bg-muted rounded">SELECT ?t ?label WHERE {'{'} GRAPH ?g {'{'} ?t a adh:Table ; rdfs:label ?label {'}'} {'}'}</code> - 查看所有表</li>
                    <li><code className="px-1 bg-muted rounded">SELECT ?m ?label WHERE {'{'} GRAPH ?g {'{'} ?m a adh:Metric ; rdfs:label ?label {'}'} {'}'}</code> - 查看所有指标</li>
                  </ul>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
