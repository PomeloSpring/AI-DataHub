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
  Database, Network, GitBranch, Search, Terminal,
} from 'lucide-react';
import KnowledgeGraphView from '@/components/graph/KnowledgeGraph';
import { ModelGraphTab } from './catalog/ModelAssetTabs';
import { useGraphStore } from '@/stores/graphStore';
import axios from 'axios';
import { toast } from '@/lib/toast';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

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
  const { t } = useTranslation('ui');
  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-4 p-4 border-b bg-background shrink-0">
        <div className="flex items-center gap-2">
          <Network className="h-6 w-6 text-primary" />
          <h1 className="text-xl font-semibold">{t("k_711c9de0db")}</h1>
        </div>
        <p className="text-sm text-muted-foreground">
          {tr('k_38dc96912b')}</p>
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
  const { t } = useTranslation('ui');
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
    fetchGraphData, searchNodes,
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
      toast.error(tr('k_147f97a3f5'));
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
      toast.error(tr('k_ec6049efef'));
    } finally {
      setIsLoading(false);
    }
  };

  // 图谱重建（旧「构建图谱」按钮）已移到「本体建模」工作台工具栏：那里按当前模型的域
  // （系统/业务/源）选择重建哪张命名图，本页固定 ds=0 会重建错的图。

  // ── SPARQL Handler ────────────────────────────────────────────────

  const handleExecuteSparql = async () => {
    setIsLoading(true);
    try {
      const response = await axios.post(`${API_BASE}/query`, { query: sparqlQuery }, { headers: getAuthHeader() });
      setSparqlResult(response.data);
      toast.success(tr('k_5e05f4dc40'));
    } catch (error) {
      toast.error(tr('k_4c5f2396ab'));
    } finally {
      setIsLoading(false);
    }
  };

  // ── Config ─────────────────────────────────────────────────────────

  const graphTypeConfig = {
    'ontology-overview': { icon: Network, label: tr('k_038f52b5c3'), description: tr('k_69e71b0171') },
    'table-relation': { icon: Database, label: tr('k_7b72f01f08'), description: tr('k_f38f233797') },
    'business-knowledge': { icon: Network, label: tr('k_dc9b53849b'), description: tr('k_0f431a4492') },
    'data-lineage': { icon: GitBranch, label: tr('k_2b13801194'), description: tr('k_f176ffc46b') }
  };

  // ── Render ─────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b bg-background">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Network className="h-6 w-6 text-primary" />
            <h1 className="text-xl font-semibold">{t("k_f111898eb0")}</h1>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {/* 图谱生成入口在「本体建模」工作台工具栏（需按模型域定作用域 + graph:manage 把关） */}
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-hidden">
        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as ActiveTab)} className="h-full flex flex-col">
          <div className="px-4 pt-2 border-b">
            <TabsList>
              <TabsTrigger value="graph" className="flex items-center gap-2">
                <Network className="h-4 w-4" />
                {tr('k_b1404f0154')}</TabsTrigger>
              <TabsTrigger value="sparql" className="flex items-center gap-2">
                <Terminal className="h-4 w-4" />
                {tr('k_dfced75501')}</TabsTrigger>
            </TabsList>
          </div>

          {/* Graph Tab */}
          <TabsContent value="graph" className="flex-1 overflow-hidden m-0">
            <div className="flex h-full">
              {/* Left Sidebar */}
              <div className="w-64 border-r bg-muted/30 flex flex-col p-4 space-y-4">
                {/* Graph Type Selector */}
                <div>
                  <Label className="text-xs font-medium mb-2 block">{t("k_a39b8eeb36")}</Label>
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
                  <Label className="text-xs font-medium mb-2 block">{t("k_f04090805c")}</Label>
                  <div className="flex gap-2">
                    <Input
                      placeholder={tr('k_28b5afbce5')}
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
                    <Label className="text-xs font-medium mb-2 block">{t("k_47571d7c1b")}</Label>
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
                        <Label className="text-xs font-medium">{t("k_f389321e4d")}</Label>
                        <div className="grid grid-cols-2 gap-2">
                          <Button variant="outline" size="sm" className="text-xs" onClick={async () => {
                            const data = await getUpstream(selectedNode.id);
                            toast.info(tr('k_e99a4b6f7b', { p0: data.nodes.length }));
                          }}>
                            <GitBranch className="h-3 w-3 mr-1 rotate-180" />
                            {tr('k_285e6cd4e2')}</Button>
                          <Button variant="outline" size="sm" className="text-xs" onClick={async () => {
                            const data = await getDownstream(selectedNode.id);
                            toast.info(tr('k_7aafb530dc', { p0: data.nodes.length }));
                          }}>
                            <GitBranch className="h-3 w-3 mr-1" />
                            {tr('k_3998913c08')}</Button>
                        </div>
                        <Button variant="outline" size="sm" className="w-full text-xs" onClick={async () => {
                          const result = await getImpactAnalysis(selectedNode.id);
                          if (result) {
                            toast.info(tr('k_030c299528', { p0: result.impact_summary.total_affected }));
                          }
                        }}>
                          <Network className="h-3 w-3 mr-1" />
                          {tr('k_e8540d99eb')}</Button>
                      </div>
                    )}
                  </div>
                )}

                {/* Legend */}
                <div className="border-t pt-4">
                  <Label className="text-xs font-medium mb-2 block">{t("k_b43daba10d")}</Label>
                  <div className="space-y-1.5 text-xs">
                    {graphType === 'ontology-overview' && (
                      <>
                        <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-indigo-500" /><span>{t("k_66695434da")}</span></div>
                        <div className="flex items-center gap-2"><div className="w-4 h-0.5 bg-indigo-400" /><span>{tr('k_f072051fec')}</span></div>
                        <div className="flex items-center gap-2"><div className="w-2 h-2 rounded-full bg-green-500" /><span>{t("k_b3addb5e3f")}</span><div className="w-2 h-2 rounded-full bg-amber-500" /><span>{t("k_d8edae8ecd")}</span></div>
                      </>
                    )}
                    <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-blue-500" /><span>{t("k_6a954390e3")}</span></div>
                    <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-green-500" /><span>{t("k_77a49f2c38")}</span></div>
                    <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-purple-500" /><span>{t("k_f4f39962ca")}</span></div>
                    <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-orange-500" /><span>{t("k_1dfc810a7b")}</span></div>
                    <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-teal-500" /><span>{t("k_72fa9ecdc4")}</span></div>
                    {graphType === 'data-lineage' && (
                      <>
                        <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-cyan-500" /><span>{t("k_a3ccf702c5")}</span></div>
                        <div className="flex items-center gap-2"><div className="w-3 h-3 rounded bg-amber-500" /><span>{t("k_ff4c55999e")}</span></div>
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
                  {tr('k_a4b6401dac')}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label>{t("k_2e684d4f31")}</Label>
                  <Textarea
                    value={sparqlQuery}
                    onChange={(e) => setSparqlQuery(e.target.value)}
                    placeholder={tr('k_5b833f3d46')}
                    className="font-mono text-sm h-32"
                  />
                </div>
                <Button onClick={handleExecuteSparql} disabled={isLoading}>
                  <Terminal className="h-4 w-4 mr-2" />
                  {tr('k_4fb36a6129')}</Button>
                {sparqlResult && (
                  <div className="space-y-2">
                    <Label>{t("k_afeb896ef0")}</Label>
                    <pre className="bg-muted p-4 rounded-lg text-xs overflow-auto max-h-96">
                      {JSON.stringify(sparqlResult, null, 2)}
                    </pre>
                  </div>
                )}
                <div className="text-xs text-muted-foreground">
                  {tr('k_3554c6e6e7')}<ul className="mt-1 space-y-1 list-disc list-inside">
                    <li><code className="px-1 bg-muted rounded">SELECT ?s ?p ?o WHERE {'{'} GRAPH ?g {'{'} ?s ?p ?o {'}'} {'}'} LIMIT 25</code> {tr('k_0a24ce15c1')}</li>
                    <li><code className="px-1 bg-muted rounded">SELECT ?t ?label WHERE {'{'} GRAPH ?g {'{'} ?t a adh:Table ; rdfs:label ?label {'}'} {'}'}</code> {t("k_5f444e8d9a")}</li>
                    <li><code className="px-1 bg-muted rounded">SELECT ?m ?label WHERE {'{'} GRAPH ?g {'{'} ?m a adh:Metric ; rdfs:label ?label {'}'} {'}'}</code> {t("k_bbaeb3bfd8")}</li>
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
