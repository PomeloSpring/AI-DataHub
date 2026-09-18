import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Bot, Shield, CheckCircle, XCircle, Clock, Boxes, FileText, ShieldCheck, Loader2, ChevronRight, ChevronDown, ExternalLink } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import client from '../../api/client';

// ── Types ─────────────────────────────────────────────────────────

interface ActionDef {
  key: string;
  label: string;
  description: string;
  category: 'ontology' | 'metadata';
  requires_approval: boolean;
}

const ACTION_DEFS: ActionDef[] = [
  { key: 'ontology.generate', label: '生成本体草案', description: '调用 LLM 生成本体模型草稿', category: 'ontology', requires_approval: true },
  { key: 'ontology.save', label: '保存本体模型', description: '保存对本体模型的编辑', category: 'ontology', requires_approval: true },
  { key: 'ontology.activate', label: '激活本体模型', description: '将本体模型设为激活状态', category: 'ontology', requires_approval: true },
  { key: 'ontology.import_yaml', label: '导入 YAML', description: '导入 Palantir 格式 YAML 本体', category: 'ontology', requires_approval: true },
  { key: 'metadata.sync', label: '同步元数据', description: '从数据源同步元数据信息', category: 'metadata', requires_approval: true },
];

const ACTION_ICONS: Record<string, any> = {
  'ontology.generate': Boxes,
  'ontology.save': FileText,
  'ontology.activate': ShieldCheck,
  'ontology.import_yaml': FileText,
  'metadata.sync': Loader2,
};

interface ApprovalRecord {
  id: number;
  user_id: number;
  action_key: string;
  payload: any;
  status: string;
  result: any;
  created_at: string;
  decided_at: string | null;
  decided_by: number | null;
}

const STATUS_BADGE: Record<string, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline'; className: string }> = {
  pending: { label: '待审批', variant: 'outline', className: 'border-yellow-500 text-yellow-600' },
  approved: { label: '已批准', variant: 'default', className: 'bg-green-500/10 text-green-600 border-green-500/30' },
  rejected: { label: '已拒绝', variant: 'secondary', className: '' },
  executed: { label: '已执行', variant: 'default', className: 'bg-green-500 text-green-50' },
  failed: { label: '失败', variant: 'destructive', className: '' },
};

interface OntologyProperty {
  column: string; name: string; type: string; is_key: boolean; description: string;
}
interface OntologyObject {
  key: string; display_name: string; description: string; primary_table: string;
  aliases: string[]; property_count: number; properties: OntologyProperty[];
}
interface OntologyModel {
  id: number; name: string; status: string; datasource_id: number; object_count: number;
  created_at: string; updated_at: string; objects: OntologyObject[];
}

// ── Component ─────────────────────────────────────────────────────

export default function AsBotSettings() {
  const [actions, setActions] = useState<ActionDef[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [ontologyModels, setOntologyModels] = useState<OntologyModel[]>([]);
  const [loadingOntology, setLoadingOntology] = useState(false);
  const [expandedObjects, setExpandedObjects] = useState<Set<string>>(new Set());

  useEffect(() => {
    client.get('/as-bot/actions').then(({ data }) => {
      if (data.actions) setActions(data.actions); else setActions(ACTION_DEFS);
    }).catch(() => setActions(ACTION_DEFS));

    client.get('/as-bot/approvals', { params: { limit: 50 } }).then(({ data }) => {
      setApprovals(data.approvals || []);
    }).catch(() => {});

    loadOntologyModels();
  }, []);

  const loadOntologyModels = async () => {
    setLoadingOntology(true);
    try {
      const { data } = await client.get('/as-bot/ontology');
      setOntologyModels(data.models || []);
    } catch { /* */ }
    setLoadingOntology(false);
  };

  const toggleObject = (modelId: number, objKey: string) => {
    const key = `${modelId}-${objKey}`;
    setExpandedObjects(prev => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n; });
  };

  const pendingCount = approvals.filter(a => a.status === 'pending').length;
  const executedCount = approvals.filter(a => a.status === 'executed').length;

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center">
            <Bot className="h-5 w-5 text-primary" />
          </div>
          <div>
            <h1 className="text-xl font-bold">AI 助手 (AS-BOT)</h1>
            <p className="text-sm text-muted-foreground">系统级智能助手 — 动作管理与审批</p>
          </div>
        </div>
        <Button variant="outline" size="sm" asChild>
          <Link to="/system/wakers">
            查看 Waker 配置 <ExternalLink className="h-3 w-3 ml-1" />
          </Link>
        </Button>
      </div>

      {/* Quick stats */}
      <div className="grid grid-cols-4 gap-4">
        <Card><CardHeader className="pb-2"><CardDescription>可用动作</CardDescription></CardHeader>
          <CardContent><div className="text-2xl font-bold">{actions.length || ACTION_DEFS.length}</div>
            <p className="text-xs text-muted-foreground">本体 + 元数据</p></CardContent></Card>
        <Card><CardHeader className="pb-2"><CardDescription>待审批</CardDescription></CardHeader>
          <CardContent><div className="text-2xl font-bold text-yellow-500">{pendingCount}</div></CardContent></Card>
        <Card><CardHeader className="pb-2"><CardDescription>已执行</CardDescription></CardHeader>
          <CardContent><div className="text-2xl font-bold text-green-500">{executedCount}</div></CardContent></Card>
        <Card><CardHeader className="pb-2"><CardDescription>本体模型</CardDescription></CardHeader>
          <CardContent><div className="text-2xl font-bold">{ontologyModels.length}</div>
            <p className="text-xs text-muted-foreground">系统能力依赖</p></CardContent></Card>
      </div>

      <Tabs defaultValue="ontology">
        <TabsList>
          <TabsTrigger value="ontology"><Boxes className="h-3.5 w-3.5 mr-1" />本体模型</TabsTrigger>
          <TabsTrigger value="actions">动作定义</TabsTrigger>
          <TabsTrigger value="approvals">审批历史</TabsTrigger>
          <TabsTrigger value="about">说明</TabsTrigger>
        </TabsList>

        {/* ── Ontology Models ────────────────────────────────── */}
        <TabsContent value="ontology" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <Boxes className="h-4 w-4 text-orange-500" />
                AS-BOT 依赖的本体模型
              </CardTitle>
              <CardDescription>系统自身能力的本体模型，AS-BOT 通过这些模型理解功能边界</CardDescription>
            </CardHeader>
            <CardContent>
              {loadingOntology ? (
                <div className="flex items-center gap-2 text-muted-foreground py-4">
                  <Loader2 className="h-4 w-4 animate-spin" />加载中...
                </div>
              ) : ontologyModels.length === 0 ? (
                <div className="text-center py-6 text-muted-foreground">
                  <Boxes className="h-8 w-8 mx-auto mb-2 opacity-30" />
                  <p className="text-sm">暂无本体模型</p>
                  <p className="text-xs mt-1">AS-BOT 可通过「生成本体草案」创建</p>
                </div>
              ) : (
                <div className="space-y-4">
                  {ontologyModels.map(model => (
                    <div key={model.id} className="border rounded-lg overflow-hidden">
                      <div className="flex items-center gap-2 px-3 py-2 bg-muted/50 border-b">
                        <Boxes className="h-4 w-4 text-orange-500 shrink-0" />
                        <span className="text-sm font-medium flex-1">{model.name}</span>
                        <Badge variant={model.status === 'active' ? 'default' : 'outline'} className="text-xs">
                          {model.status === 'active' ? '已激活' : model.status === 'draft' ? '草稿' : model.status}
                        </Badge>
                        <span className="text-xs text-muted-foreground">{model.object_count} 个对象</span>
                      </div>
                      <div className="divide-y">
                        {model.objects.map(obj => {
                          const objId = `${model.id}-${obj.key}`;
                          const expanded = expandedObjects.has(objId);
                          return (
                            <div key={obj.key} className="px-3 py-2">
                              <button className="flex items-center gap-2 w-full text-left hover:bg-muted/30 rounded px-1 py-0.5"
                                onClick={() => toggleObject(model.id, obj.key)}>
                                {expanded ? <ChevronDown className="h-3 w-3 text-muted-foreground" /> : <ChevronRight className="h-3 w-3 text-muted-foreground" />}
                                <span className="text-sm font-medium">{obj.display_name || obj.key}</span>
                                <code className="text-[10px] bg-muted px-1 rounded text-muted-foreground">{obj.key}</code>
                                {obj.primary_table && <span className="text-[10px] text-muted-foreground ml-auto">→ {obj.primary_table}</span>}
                                <Badge variant="outline" className="text-[10px] shrink-0">{obj.property_count} 属性</Badge>
                              </button>
                              {obj.description && expanded && (
                                <p className="text-xs text-muted-foreground mt-1 ml-5">{obj.description}</p>
                              )}
                              {expanded && obj.properties?.length > 0 && (
                                <div className="mt-2 ml-5 border rounded overflow-hidden">
                                  <table className="w-full text-xs">
                                    <thead><tr className="bg-muted/50">
                                      <th className="text-left px-2 py-1 font-medium text-muted-foreground">属性</th>
                                      <th className="text-left px-2 py-1 font-medium text-muted-foreground">列</th>
                                      <th className="text-left px-2 py-1 font-medium text-muted-foreground">类型</th>
                                      <th className="text-left px-2 py-1 font-medium text-muted-foreground">说明</th>
                                    </tr></thead>
                                    <tbody>{obj.properties.map((p, i) => (
                                      <tr key={i} className="border-t">
                                        <td className="px-2 py-1"><span className="font-medium">{p.name}</span>
                                          {p.is_key && <Badge variant="outline" className="text-[9px] ml-1">PK</Badge>}</td>
                                        <td className="px-2 py-1 font-mono text-muted-foreground">{p.column}</td>
                                        <td className="px-2 py-1 text-muted-foreground">{p.type}</td>
                                        <td className="px-2 py-1 text-muted-foreground max-w-[180px] truncate">{p.description}</td>
                                      </tr>
                                    ))}</tbody>
                                  </table>
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Actions ────────────────────────────────────────── */}
        <TabsContent value="actions">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">可用动作</CardTitle>
              <CardDescription>角色权限中按角色分配；所有写操作需用户审批。配置入口：角色权限 → AS-BOT Tab</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {(actions.length ? actions : ACTION_DEFS).map(action => {
                  const Icon = ACTION_ICONS[action.key] || Shield;
                  return (
                    <div key={action.key} className="flex items-center gap-3 p-3 border rounded-lg">
                      <Icon className="h-4 w-4 text-primary shrink-0" />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium">{action.label}</span>
                          <code className="text-[10px] bg-muted px-1.5 py-0.5 rounded text-muted-foreground">{action.key}</code>
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5">{action.description}</p>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <Badge variant="outline" className="text-xs">{action.category === 'ontology' ? '本体' : '元数据'}</Badge>
                        {action.requires_approval && (
                          <Badge variant="outline" className="text-xs border-yellow-500/50 text-yellow-600">
                            <Shield className="h-3 w-3 mr-1" />需审批
                          </Badge>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Approvals ──────────────────────────────────────── */}
        <TabsContent value="approvals">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">审批记录</CardTitle>
              <CardDescription>所有 AS-BOT 写操作的审批历史</CardDescription>
            </CardHeader>
            <CardContent>
              {approvals.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground">
                  <Clock className="h-8 w-8 mx-auto mb-2 opacity-30" />
                  <p className="text-sm">暂无审批记录</p>
                </div>
              ) : (
                <ScrollArea className="h-[400px]">
                  <div className="space-y-2">
                    {approvals.map(a => {
                      const sc = STATUS_BADGE[a.status] || STATUS_BADGE.pending;
                      const Icon = ACTION_ICONS[a.action_key] || Shield;
                      const ad = ACTION_DEFS.find(d => d.key === a.action_key);
                      return (
                        <div key={a.id} className="flex items-center gap-3 p-3 border rounded-lg">
                          <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-medium">{ad?.label || a.action_key}</span>
                              <Badge variant={sc.variant as any} className={`text-xs ${sc.className}`}>{sc.label}</Badge>
                            </div>
                            <p className="text-xs text-muted-foreground mt-0.5">#{a.id} · {new Date(a.created_at).toLocaleString()}</p>
                          </div>
                          {a.result && (a.result.success
                            ? <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />
                            : <XCircle className="h-4 w-4 text-destructive shrink-0" />)}
                        </div>
                      );
                    })}
                  </div>
                </ScrollArea>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── About ──────────────────────────────────────────── */}
        <TabsContent value="about">
          <Card>
            <CardHeader><CardTitle className="text-base">AS-BOT 说明</CardTitle></CardHeader>
            <CardContent className="space-y-4 text-sm leading-relaxed">
              <div className="space-y-2">
                <h3 className="font-medium flex items-center gap-2"><Bot className="h-4 w-4 text-primary" />什么是 AS-BOT?</h3>
                <p className="text-muted-foreground">系统级 AI 助手，绑定项目知识库（qMind）和本体模型能力，协助构建本体、管理元数据、了解系统功能。</p>
              </div>
              <div className="space-y-2">
                <h3 className="font-medium flex items-center gap-2"><Shield className="h-4 w-4 text-primary" />安全机制</h3>
                <ul className="text-muted-foreground space-y-1 list-disc list-inside">
                  <li>所有写操作必须经过用户审批</li>
                  <li>不直连数据源，所有信息通过 API 工具获取</li>
                  <li>基于角色的动作权限控制</li>
                  <li>遵循数据护城河安全规范</li>
                </ul>
              </div>
              <div className="space-y-2">
                <h3 className="font-medium">配置入口</h3>
                <ul className="text-muted-foreground space-y-1">
                  <li><Link to="/system/wakers" className="text-primary underline underline-offset-2">Waker 配置</Link> — 查看/编辑系统 Waker 绑定的知识库、技能、工具组</li>
                  <li><Link to="/system/roles" className="text-primary underline underline-offset-2">角色权限</Link> → AS-BOT Tab — 配置每个角色的动作权限</li>
                  <li><Link to="/system/skills" className="text-primary underline underline-offset-2">技能配置</Link> — 管理 ontology-builder / metadata-manager 技能</li>
                  <li><Link to="/system/knowledge-base" className="text-primary underline underline-offset-2">知识库</Link> — 管理 qMind 知识库绑定</li>
                </ul>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
