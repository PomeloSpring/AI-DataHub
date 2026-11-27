import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/lib/toast';
import { Pencil, Bot, Lock, Eye, ChevronDown, ChevronRight } from 'lucide-react';
import client from '@/api/client';
import { useAuthStore } from '../../stores/authStore';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

// ── 常量:与后端 sdk_tools 工具组 / tool_catalog 标准工具 / 图表类型对齐 ──
// 工具权限采用"逐个工具"粒度:每个进程内 MCP server 的工具可单独勾选。
// category: data=数据查询工具（答数据问题）/ platform=平台操作工具（建模/看板/资产/系统，
// 操作平台用）/ aux=辅助机制；真值源是后端 tool_catalog 的 category 字段，
// 这里的映射只兜底（后端未带时不让分组丢失）。
const CATEGORY_BY_GROUP: Record<string, string> = {
  catalog: 'data', semantic: 'data', query: 'data', lineage: 'data',
  ontology: 'platform', screen: 'platform', system: 'platform', assets: 'platform',
  memory: 'aux', render: 'aux',
};
const MCP_TOOL_GROUPS: { group: string; label: string; desc: string; category?: string; tools: { name: string; label: string; category?: string }[] }[] = [
  {
    group: 'catalog', label: tr('k_4c288b61f7'), desc: tr('k_4f9f9225bf'),
    tools: [
      { name: 'search_metadata', label: tr('k_a5c1dac8c3') },
      { name: 'get_table_schema', label: tr('k_03a0d22485') },
      { name: 'list_datasources', label: tr('k_9df8b7a905') },
    ],
  },
  {
    group: 'semantic', label: tr('k_08aea3cd16'), desc: tr('k_f5b96116a7'),
    tools: [
      { name: 'knowledge_search', label: tr('k_49626be887') },
      { name: 'get_metrics', label: tr('k_1dfc810a7b') },
      { name: 'get_glossary', label: tr('k_f4f39962ca') },
      { name: 'query_by_tags', label: tr('k_ddb558cc28') },
      { name: 'run_semantic_query', label: tr('k_c8c6211192') },
      { name: 'get_business_semantics', label: tr('k_08aea3cd16') },
      { name: 'search_business_knowledge', label: tr('k_a7d59dd0c2') },
    ],
  },
  {
    group: 'query', label: tr('k_86a50f1253'), desc: tr('k_3780e310b0'),
    tools: [{ name: 'check_sql', label: tr('k_bd59c7eebb') }, { name: 'execute_sql', label: tr('k_a8c191126e') }],
  },
  {
    group: 'ontology', label: tr('k_85eb6d1ab7'), desc: tr('k_fe556348c9'),
    tools: [
      { name: 'search_ontology', label: tr('k_6a22ed2882') },
      { name: 'get_ontology_model', label: tr('k_3388316fef') },
      { name: 'list_ontology_models', label: tr('k_851f3dfff6') },
      { name: 'get_metadata_summary', label: tr('k_9047b4c3dc') },
      { name: 'generate_ontology_draft', label: tr('k_a42485b4d8') },
      { name: 'save_ontology_model', label: tr('k_4d74bb8f08') },
      { name: 'activate_ontology_model', label: tr('k_9b2a87c17c') },
      { name: 'import_ontology_yaml', label: tr('k_aab69d9372') },
    ],
  },
  {
    group: 'screen', label: tr('k_f3e5c2d8d7'), desc: tr('k_51f5a847cb'),
    tools: [
      { name: 'create_data_screen', label: tr('k_b86b4fdb7f') },
      { name: 'get_data_screen', label: tr('k_d35feb9b8e') },
      { name: 'update_data_screen_chart', label: tr('k_03403d5292') },
      { name: 'list_data_screens', label: tr('k_54acc0ed98') },
      { name: 'update_data_screen', label: tr('k_f11d92db52') },
      { name: 'delete_data_screen', label: tr('k_e9a82f050e') },
      { name: 'list_dashboard_groups', label: tr('k_b78b6d0d33') },
      { name: 'manage_dashboard_groups', label: tr('k_f2e7c1aad1') },
      { name: 'list_vis_components', label: tr('k_64fadd975d') },
      { name: 'get_vis_component', label: tr('k_c6a035db89') },
      { name: 'save_vis_component', label: tr('k_e00d2beb95') },
    ],
  },
];
const STANDARD_TOOLS = [
  { name: 'read', label: tr('k_b6b97ac60d') }, { name: 'write', label: tr('k_9e93bdccb2') }, { name: 'edit', label: tr('k_a7f814c0a4') },
  { name: 'glob', label: tr('k_e31b4e29fb') }, { name: 'grep', label: tr('k_0a48125861') }, { name: 'bash', label: tr('k_4d9de9c399') },
  { name: 'webfetch', label: tr('k_4a74dc687c') }, { name: 'task', label: tr('k_070fc86282') },
];
const CATEGORIES = ['nl2sql', 'analysis', 'chart', 'report', 'custom'];

interface AsBot {
  id: number;
  as_bot_key: string;
  name: string;
  display_name: string;
  description: string;
  category: string;
  system_prompt: string;
  persona: { responsibility?: string; style?: string; boundary?: string };
  tools: {
    groups: string[]; standard: string[]; mcp: Record<string, string[]>; external?: Record<string, string[]>;
    /** 功能能力的减法开关：只能关（disabled），开不了 —— 它们从角色权限码自动继承 */
    functions?: { disabled?: string[] };
  };
  mcp_server_ids: number[];
  knowledge_base_ids: number[];
  skills: string[];
  models: string[];
  /** 绑定的执行层：0 = 跟随工作空间默认执行层（后端 NULL） */
  execution_layer_id: number;
  chart_enabled: boolean;
  permission_mode: string;
  is_active: boolean;
  is_builtin?: number;
}

/** 功能能力项（A 维度）：从角色权限码继承，AS-BOT 只能做减法 */
interface FunctionAction {
  action_key: string;
  name: string;
  label: string;
  desc: string;
  object_key: string;
  perm_code: string;
  read_only: boolean;
  available: boolean;
  level: 'none' | 'read' | 'write';
  reason: string;
}

const emptyAsBot = (): AsBot => ({
  id: 0, as_bot_key: '', name: '', display_name: '', description: '', category: 'custom',
  system_prompt: '', persona: {},
  tools: { groups: [], standard: [], mcp: {}, external: {}, functions: { disabled: [] } },
  mcp_server_ids: [], knowledge_base_ids: [], skills: [], models: [], chart_enabled: true,
  execution_layer_id: 0,
  permission_mode: 'inherit', is_active: true,
});

export default function AsBotManager() {
  const { t } = useTranslation('ui');
  const [asBots, setAsBots] = useState<AsBot[]>([]);
  const [mcpServers, setMcpServers] = useState<any[]>([]);
  const [toolGroups, setToolGroups] = useState<typeof MCP_TOOL_GROUPS>([]);
  const [standardTools, setStandardTools] = useState<typeof STANDARD_TOOLS>([]);
  const [functionActions, setFunctionActions] = useState<FunctionAction[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<any[]>([]);
  const [skillOptions, setSkillOptions] = useState<any[]>([]);
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  // 执行层清单：供「执行层」下拉选择与模型候选联动（选项显示 display_name，值用 id）
  const [layers, setLayers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<AsBot | null>(null);
  const [form, setForm] = useState<AsBot>(emptyAsBot());
  // 列表默认不平铺能力标签，点开「能力详情」才展开（避免刷屏）
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [wRes, mcpRes, kbRes, skillRes, catalogRes] = await Promise.all([
        client.get('/admin/as-bots/'),
        client.get('/admin/mcp-servers'),
        client.get('/knowledge-bases'),
        client.get('/admin/skills'),
        client.get('/admin/as-bots/tools/catalog'),
      ]);
      // 工具目录是渲染主数据，契约不符必须 fail-loud（勿把 undefined 传进状态致渲染崩溃）
      const groups = catalogRes.data?.groups;
      if (!Array.isArray(groups)) throw new Error(tr('k_cb0720e346'));
      setToolGroups(groups);
      setStandardTools(Array.isArray(catalogRes.data?.standard) ? catalogRes.data.standard : []);
      setFunctionActions(catalogRes.data?.functions?.items || []);
      setAsBots(Array.isArray(wRes.data) ? wRes.data.map((r: any) => normalizeAsBot(r, groups)) : []);
      setMcpServers(Array.isArray(mcpRes.data) ? mcpRes.data : []);
      setKnowledgeBases(Array.isArray(kbRes.data) ? kbRes.data.filter((k: any) => k.status === 'active') : []);
      setSkillOptions(Array.isArray(skillRes.data) ? skillRes.data : []);
    } catch {
      toast.error(t('k_866b795eae'));
    } finally {
      setLoading(false);
    }
    // 模型候选单独非阻塞加载(需先定位实际 qoder 执行层再按 layer_id 取模型,可能较慢)
    loadModelOptions();
  };

  // 可用模型候选：来自**本轮实际生效的那条执行层**的 list_models。
  // 不能直接用 cli_name=qoder 查（会因缺少层配置/路径/令牌返回空），须用 layer_id。
  // AS-BOT 绑了层就按那条层取候选；没绑则回退到默认层（与后端选层口径同构），
  // 避免“候选来自 A 层、实际跑在 B 层”的双口径。
  const loadModelOptions = async (layerId = 0) => {
    try {
      const { data: layersResp } = await client.get('/admin/execution-layers');
      const arr = Array.isArray(layersResp) ? layersResp : [];
      setLayers(arr);
      const cli =
        (layerId && arr.find((x: any) => x.id === Number(layerId))) ||
        arr.find((x: any) => x.layer_type === 'cli' && x.is_default) ||
        arr.find((x: any) => x.layer_type === 'cli' && ((x.config || {}).cli_name === 'qoder')) ||
        arr.find((x: any) => x.layer_type === 'cli');
      if (!cli) { setModelOptions([]); return; }
      const { data } = await client.get(`/admin/execution-layers/models?layer_id=${cli.id}`);
      setModelOptions(Array.isArray(data?.models) ? data.models : []);
    } catch {
      setModelOptions([]);
    }
  };
  useEffect(() => { load(); }, []);

  const mcpName = (id: number) => {
    // 内置服务用业务可读 label（如「Elasticsearch 检索服务」），不是内部 name；
    // 已下架时给可读标注而不光裸 id（id 仅作为排查线索留在括号里）。
    const srv = mcpServers.find((s) => s.id === id);
    return srv ? (srv.label || srv.name) : tr('k_a728b202b8', { p0: id });
  };
  const kbName = (id: number) => knowledgeBases.find((k) => k.id === id)?.name || `#${id}`;
  // 展示用中文标签(目录里缺标签时回退原始 key, 不丢信息; key 放 title 供排查)
  const groupLabel = (g: string) => toolGroups.find((x) => x.group === g)?.label || g;
  const toolLabel = (g: string, t: string) =>
    toolGroups.find((x) => x.group === g)?.tools.find((x) => x.name === t)?.label || t;
  const standardLabel = (t: string) => standardTools.find((x) => x.name === t)?.label || t;

  const openEdit = (w: AsBot) => { setEditing(w); setForm(JSON.parse(JSON.stringify(w))); setFormOpen(true); };

  const toggleArr = (arr: (string | number)[], val: string | number) =>
    arr.includes(val) ? arr.filter((x) => x !== val) : [...arr, val];

  // 逐工具 MCP 勾选
  const setMcp = (group: string, names: string[]) =>
    setForm((f) => ({ ...f, tools: { ...f.tools, mcp: { ...(f.tools?.mcp || {}), [group]: names } } }));
  const toggleMcpTool = (group: string, tool: string) => {
    const cur = form.tools?.mcp?.[group] || [];
    setMcp(group, cur.includes(tool) ? cur.filter((x) => x !== tool) : [...cur, tool]);
  };
  /** 卡级全选/取消：只动本卡的工具（一个组可能跨能力区，如语义组的目录三件已归系统工具）。 */
  const toggleCard = (group: string, names: string[]) => {
    const cur: string[] = form.tools?.mcp?.[group] || [];
    const allOn = names.length > 0 && names.every((n) => cur.includes(n));
    setMcp(group, allOn ? cur.filter((x) => !names.includes(x)) : Array.from(new Set([...cur, ...names])));
  };
  /** 逐工具类别（真值源 = tool_catalog 的 category，缺省回落到组类别）。 */
  const toolCat = (g: { group: string; category?: string }, t: { category?: string }) =>
    t.category || g.category || CATEGORY_BY_GROUP[g.group] || 'aux';

  const handleSave = async () => {
    if (!form.name.trim() && !form.as_bot_key.trim()) { toast.error(t('k_1b3acf6993')); return; }
    const key = (form.as_bot_key || form.name).trim();
    // 从 mcp 逐工具勾选反向派生 groups(有≥一个工具的组),保持向后兼容与列表展示
    const mcpSel = form.tools?.mcp || {};
    const derivedGroups = Object.keys(mcpSel).filter((g) => (mcpSel[g] || []).length > 0);
    const payload = {
      ...form,
      as_bot_key: key,
      name: form.name.trim() || key,
      display_name: form.display_name || form.name,
      tools: { ...form.tools, groups: derivedGroups,
        external: Object.fromEntries((form.mcp_server_ids || []).map(id => [String(id), form.tools.external?.[String(id)] || []])) },
      chart_enabled: !!form.chart_enabled,
      is_active: !!form.is_active,
    };
    try {
      if (editing) {
        await client.put(`/admin/as-bots/${editing.id}`, payload);
        toast.success(t('k_1b92092d63'));
      } else {
        await client.post('/admin/as-bots/', payload);
        toast.success(t('k_2d4c5dcc74'));
      }
      setFormOpen(false);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_6de920b4e4'));
    }
  };


  const { user } = useAuthStore();
  const isAdmin = user?.role === 'admin';
  const isReadOnly = !!(editing?.is_builtin && !isAdmin);
  // 可选执行层：只列 cli 型（AS-BOT 安全执行链只接受 mode=sdk 的 cli 层）
  const cliLayers = layers.filter((x: any) => x.layer_type === 'cli');

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><Bot className="h-6 w-6 text-primary" />{t("k_6446c15db1")}</h1>
          <p className="text-muted-foreground text-sm mt-1">{t('k_0a5c6e7d59')}</p>
        </div>
      </div>

      <Tabs defaultValue="asBots">
        <TabsList>
          <TabsTrigger value="asBots">{t("k_9bc2af27c4")}</TabsTrigger>
        </TabsList>

        {/* ── AS-BOT 列表 ── */}
        <TabsContent value="asBots" className="mt-4">
          <div className="border border-border rounded-xl divide-y divide-border">
            {asBots.map((w) => {
              const expanded = expandedId === w.id;
              const capabilityCount =
                Object.values(w.tools?.mcp || {}).reduce((n, names) => n + (names?.length || 0), 0) +
                (w.tools?.standard || []).length +
                (w.mcp_server_ids?.length || 0) +
                (w.knowledge_base_ids?.length || 0) +
                (w.skills || []).length;
              return (
                <div key={w.id} className="p-3 hover:bg-muted/30">
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5 min-w-0">
                      <div className="font-medium text-sm flex items-center gap-2 flex-wrap">
                        {w.display_name || w.name}
                        <span className="text-xs text-muted-foreground">({w.name})</span>
                        {!!w.is_builtin && <Badge variant="secondary" className="text-xs"><Lock className="h-2.5 w-2.5 mr-0.5 inline" />{t("k_ae8da0a608")}</Badge>}
                        {w.chart_enabled && <Badge variant="outline" className="text-xs">{t("k_a66b71e29e")}</Badge>}
                        {!w.is_active && <span className="text-xs text-destructive">{t("k_0fe5a98e9f")}</span>}
                      </div>
                      <div className="text-xs text-muted-foreground truncate">{w.description}</div>
                      <button
                        type="button"
                        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mt-0.5"
                        onClick={() => setExpandedId(expanded ? null : w.id)}
                        aria-expanded={expanded}
                      >
                        {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                        {tr('k_7ae5790d48')}{capabilityCount} {tr('k_a667e2db51')}{' '}</button>
                    </div>
                    <div className="flex gap-1 flex-shrink-0">
                      {(w.is_builtin && !isAdmin) ? (
                        <Button size="sm" variant="ghost" onClick={() => openEdit(w)} title={t('k_144b972c2f')}><Eye className="h-4 w-4" /></Button>
                      ) : (
                        <Button size="sm" variant="ghost" onClick={() => openEdit(w)}><Pencil className="h-4 w-4" /></Button>
                      )}
                    </div>
                  </div>
                  {expanded && (
                    <div className="flex gap-1.5 mt-2 flex-wrap">
                      {Object.entries(w.tools?.mcp || {}).flatMap(([g, names]) =>
                        (names || []).map((t) => (
                          <span key={`${g}.${t}`} title={`${g}·${t}`} className="text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded">{groupLabel(g)}·{toolLabel(g, t)}</span>
                        ))
                      )}
                      {(w.tools?.standard || []).map((t) => (
                        <span key={t} title={t} className="text-xs bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded">{standardLabel(t)}</span>
                      ))}
                      {w.mcp_server_ids?.map((id) => (
                        <span key={`m${id}`} className="text-xs bg-green-50 text-green-600 px-1.5 py-0.5 rounded">MCP: {mcpName(id)}</span>
                      ))}
                      {w.knowledge_base_ids?.map((id) => (
                        <span key={`k${id}`} className="text-xs bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded">{t('k_9e2924f64f', { p0: kbName(id) })}</span>
                      ))}
                      {(w.skills || []).map((sk) => (
                        <span key={`s${sk}`} className="text-xs bg-purple-50 text-purple-600 px-1.5 py-0.5 rounded">{t('k_60529dd5f9', { p0: sk })}</span>
                      ))}
                      {capabilityCount === 0 && (
                        <span className="text-xs text-muted-foreground">{t("k_28fc8a5ff0")}</span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            {!loading && asBots.length === 0 && (
              <div className="text-sm text-muted-foreground text-center py-12">{t("k_95955874e7")}</div>
            )}
          </div>
        </TabsContent>
      </Tabs>

      {/* ── 编辑对话框 ── */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {isReadOnly ? t('k_8eb3971fb9') : t('k_e24ff8a278')}
              {isReadOnly && <Badge variant="secondary" className="text-xs"><Lock className="h-3 w-3 mr-1" />{t("k_f2ed07bc58")}</Badge>}
            </DialogTitle>
            <DialogDescription>{isReadOnly ? t('k_8c55e5fa67') : t('k_6103b137e4')}</DialogDescription>
          </DialogHeader>

          <div className={`space-y-4 ${isReadOnly ? 'pointer-events-none opacity-90' : ''}`}>
            <div className="grid grid-cols-2 gap-4">
              <Field label={t('k_d463525dcd')}>
                <Input value={form.as_bot_key} disabled={!!editing} onChange={(e) => setForm({ ...form, as_bot_key: e.target.value })} placeholder="data_analyst" />
              </Field>
              <Field label={t("k_1be7ae4fc2")}>
                <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="data_analyst" />
              </Field>
              <Field label={t("k_75ae6a8a7d")}>
                <Input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder={t("k_a13c6b88bd")} />
              </Field>
              <Field label={t("k_435c5259e4")}>
                <Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{CATEGORIES.map((c) => (<SelectItem key={c} value={c}>{c}</SelectItem>))}</SelectContent>
                </Select>
              </Field>
            </div>

            <Field label={t("k_412f54dc38")}>
              <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder={t("k_47822cc724")} />
            </Field>

            <Field label={t('k_2d521f6d0a')}>
              <Textarea rows={3} value={form.system_prompt} onChange={(e) => setForm({ ...form, system_prompt: e.target.value })} placeholder={t('k_d5a9dae327')} />
            </Field>

            <div className="grid grid-cols-3 gap-4">
              <Field label={t("k_db181821a1")}><Textarea rows={2} value={form.persona?.responsibility || ''} onChange={(e) => setForm({ ...form, persona: { ...form.persona, responsibility: e.target.value } })} /></Field>
              <Field label={t("k_a4ce420c69")}><Textarea rows={2} value={form.persona?.style || ''} onChange={(e) => setForm({ ...form, persona: { ...form.persona, style: e.target.value } })} /></Field>
              <Field label={t("k_b90609d07d")}><Textarea rows={2} value={form.persona?.boundary || ''} onChange={(e) => setForm({ ...form, persona: { ...form.persona, boundary: e.target.value } })} /></Field>
            </div>

            <Field label={t('k_59ab6c4880')}>
              <p className="text-sm text-muted-foreground">{t('k_9a34d7c03d')}</p>
              <p className="text-xs text-destructive">{t("k_8ee6487a9b")}</p>
              
              {/* 能力分区：平台能力 vs 数据工具显式区分（与后端 tool_catalog.category 同源）
                  data=答数据问题 / platform=操作平台资产（不用于答数据问题）/ aux=辅助机制 */}
              <div className="space-y-4">
                {([
                  { key: 'data', label: tr('k_5d1e9a7c70'), desc: tr('k_5d1e9a7c73') },
                  { key: 'platform', label: tr('k_5d1e9a7c71'), desc: tr('k_5d1e9a7c74') },
                  { key: 'aux', label: tr('k_5d1e9a7c72'), desc: '' },
                ]).map((sec) => {
                  // 按逐工具类别分桶：组可能跨区（语义组的 get_metrics/get_glossary/query_by_tags
                  // 已归系统工具，2026-11-27 拍板），跨区时一个组拆成两张卡、勾选互不干扰
                  const cards = toolGroups
                    .map((g) => ({ g, tools: g.tools.filter((t) => toolCat(g, t) === sec.key) }))
                    .filter((c) => c.tools.length > 0);
                  if (!cards.length) return null;
                  return (
                    <div key={sec.key}>
                      <div className="text-xs font-semibold">
                        {sec.label}
                        {sec.desc && (
                          <span className="ml-2 font-normal text-muted-foreground">{sec.desc}</span>
                        )}
                      </div>
                      <div className="space-y-3 mt-1.5">
                        {cards.map(({ g, tools }) => {
                          const groupSel: string[] = form.tools?.mcp?.[g.group] || [];
                          const sel = groupSel.filter((n) => tools.some((t) => t.name === n));
                          const all = tools.map((t) => t.name);
                          const allOn = all.length > 0 && sel.length === all.length;
                          return (
                            <div key={g.group} className="border border-border rounded-lg p-2.5">
                              <button
                                type="button"
                                className="flex items-center gap-2 text-sm font-medium w-full text-left"
                                onClick={() => toggleCard(g.group, all)}
                                title={g.desc}
                              >
                                <span className={`inline-block w-3.5 h-3.5 rounded border ${allOn ? 'bg-primary border-primary' : 'border-muted-foreground/40'}`} />
                                {/* 组被跨区拆分时子集卡换标题（如语义组的目录三件在平台区叫「目录查询」） */}
                                {tools.length < g.tools.length ? tr('k_5d1e9a7c76') : g.label}
                                <span className="text-xs text-muted-foreground">({sel.length}/{all.length})</span>
                              </button>
                              <div className="flex flex-wrap gap-2 mt-2 pl-5">
                                {tools.map((t) => {
                                  const active = sel.includes(t.name);
                                  return (
                                    <Badge key={t.name} variant={active ? 'default' : 'outline'} className="cursor-pointer"
                                      title={t.name}
                                      onClick={() => toggleMcpTool(g.group, t.name)}>
                                      {t.label}
                                    </Badge>
                                  );
                                })}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            </Field>

            <Field label={t('k_e63f418b22')}>
              <p className="text-xs text-muted-foreground">
                {t('k_7f1355024a')}
              </p>
              {functionActions.length === 0 ? (
                <p className="text-xs text-muted-foreground py-1">{t("k_51f23243a9")}</p>
              ) : (
                <div className="space-y-1 max-h-[180px] overflow-y-auto">
                  {functionActions.map((a) => {
                    const disabled = (form.tools?.functions?.disabled || []).includes(a.action_key);
                    const toggleDisabled = () => setForm(f => ({
                      ...f,
                      tools: {
                        ...f.tools,
                        functions: {
                          ...f.tools?.functions,
                          disabled: toggleArr(f.tools?.functions?.disabled || [], a.action_key) as string[],
                        },
                      },
                    }));
                    return (
                      <div key={a.action_key}
                        className={`border rounded px-2 py-1 ${a.available ? '' : 'opacity-60'}`}>
                        <div className="flex items-center justify-between gap-2">
                          <label className={`flex items-center gap-2 min-w-0 ${a.available ? 'cursor-pointer' : ''}`}>
                            <input type="checkbox" className="h-3.5 w-3.5 flex-shrink-0"
                              checked={a.available && !disabled}
                              disabled={!a.available}
                              onChange={toggleDisabled} />
                            <span className="text-xs font-medium truncate">{a.label}</span>
                            <Badge variant="outline" className="text-[9px] flex-shrink-0">
                              {a.read_only ? t('k_85541bd9f7') : t('k_a5230ad612')}
                            </Badge>
                            <code className="text-[10px] text-muted-foreground flex-shrink-0"
                              title={t('k_e8e39d22e9', { p0: a.perm_code, p1: a.object_key })}>
                              {a.name}
                            </code>
                          </label>
                          {!a.available && (
                            <Badge variant="outline" className="text-[9px] flex-shrink-0">{t("k_beff4a1cd1")}</Badge>
                          )}
                        </div>
                        {a.desc && <p className="text-[10px] text-muted-foreground pl-6">{a.desc}</p>}
                        {!a.available && a.reason && (
                          <p className="text-[10px] text-amber-600 pl-6">{a.reason}</p>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </Field>

            <Field label={t('k_e95cb5fc1a')}>
              <p className="text-xs text-muted-foreground">{t("k_ba82234b4d")}</p>
              <div className="flex flex-wrap gap-2">
                {standardTools.map((t) => {
                  const active = (form.tools?.standard || []).includes(t.name);
                  return (
                    <Badge key={t.name} variant={active ? 'default' : 'outline'} className="cursor-pointer"
                      onClick={() => setForm({ ...form, tools: { ...form.tools, standard: toggleArr(form.tools?.standard || [], t.name) as string[] } })}>
                      {t.label}
                    </Badge>
                  );
                })}
              </div>
            </Field>

            <div className="grid grid-cols-2 gap-4">
              <Field label={t("k_6fbd803d2a")}>
                <p className="mb-2 text-xs text-muted-foreground">{t("k_0c6608c435")}</p>
                <div className="flex flex-wrap gap-2 max-h-[140px] overflow-y-auto">
                  {mcpServers.filter((s) => s.is_active).map((s) => {
                    const active = (form.mcp_server_ids || []).includes(s.id);
                    return (
                      <Badge key={s.id} variant={active ? 'default' : 'outline'} className="cursor-pointer"
                        onClick={() => setForm({ ...form, mcp_server_ids: toggleArr(form.mcp_server_ids || [], s.id) as number[] })}>
                        {s.label || s.name}
                        {s.builtin ? <span className="ml-1 text-[10px] opacity-70">{t("k_09ceea7644")}</span> : null}
                      </Badge>
                    );
                  })}
                  {mcpServers.filter((s) => s.is_active).length === 0 && <span className="text-xs text-muted-foreground">{t("k_c6bdf555e8")}</span>}
                </div>
                {(form.mcp_server_ids || []).map(id => {
                  const srv = mcpServers.find(s => s.id === id);
                  const discovered = srv?.discovered_tools;
                  const tools = Array.isArray(discovered) ? discovered : (discovered?.tools || []);
                  const selected = form.tools.external?.[String(id)] || [];
                  return <div key={id} className="mt-2 border rounded p-2 text-xs">
                    <p>{t('k_b3c6aa7584', { p0: mcpName(id) })}</p>
                    {srv?.builtin || srv?.transport === 'builtin' ? (
                      // 内置服务在会话进程内直接执行（带真实身份与出站守卫），
                      // 不得套用远程 MCP 的“当前禁止执行”文案误导管理员。
                      <p className="text-muted-foreground">{t("k_7a7cc0b761")}</p>
                    ) : (
                      <p className="text-destructive">{t("k_5c5f7a6c7b")}</p>
                    )}
                    {tools.length === 0 && <p className="text-destructive">{t("k_8c5175298a")}</p>}
                    <div className="flex flex-wrap gap-1 mt-1">{tools.map((t: any) => <Badge key={t.name}
                      title={t.description || t.name} className="cursor-pointer"
                      variant={selected.includes(t.name) ? 'default' : 'outline'}
                      onClick={() => setForm(f => ({...f, tools: {...f.tools, external: {...f.tools.external,
                        [String(id)]: toggleArr(selected, t.name) as string[]}}}))}>{t.name}</Badge>)}</div>
                  </div>;
                })}
              </Field>
              <Field label={t("k_8625efdd21")}>
                <p className="mb-2 text-xs text-muted-foreground">{t('k_00298db84d')}</p>
                <div className="flex flex-wrap gap-2 max-h-[140px] overflow-y-auto">
                  {knowledgeBases.map((kb) => {
                    const active = (form.knowledge_base_ids || []).includes(kb.id);
                    // 未接入检索通道的类型：灰显 + 原样显示服务端原因，不得做成“可选但不生效”的开关
                    // （服务端 validate_knowledge_binding 同样会拒，只是这里先让用户看出区别）。
                    // `retrieval` 缺失时按可用处理：不得因为旧载荷没带这个字段就锁死正常库。
                    const blocked = kb.retrieval && kb.retrieval.readable === false;
                    return (
                      <Badge key={kb.id} variant={active ? 'default' : 'outline'}
                        className={blocked ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'}
                        title={blocked ? (kb.retrieval.reason || t('k_c48dda7ab6')) : kb.description}
                        onClick={() => !blocked && setForm({ ...form, knowledge_base_ids: toggleArr(form.knowledge_base_ids || [], kb.id) as number[] })}>
                        {kb.name}{blocked ? t('k_597b7ca28b') : ''}
                      </Badge>
                    );
                  })}
                  {knowledgeBases.length === 0 && <span className="text-xs text-muted-foreground">{t("k_a4e921d64b")}</span>}
                </div>
              </Field>
            </div>

            {/* 技能勾选绑定 */}
            <Field label={t('k_9f2c4d9438')}>
              <div className="flex flex-wrap gap-2 max-h-[140px] overflow-y-auto">
                {skillOptions.map((sk) => {
                  const active = (form.skills || []).includes(sk.name);
                  return (
                    <Badge key={sk.name} variant={active ? 'default' : 'outline'} className="cursor-pointer"
                      title={`${sk.description || ''}${sk.is_builtin ? tr('k_0c058f4e87') : ''}`}
                      onClick={() => setForm({ ...form, skills: toggleArr(form.skills || [], sk.name) as string[] })}>
                      {sk.display_name || sk.name}{sk.is_builtin ? t('k_a3a8a97115') : ''}
                    </Badge>
                  );
                })}
                {skillOptions.length === 0 && <span className="text-xs text-muted-foreground">{t('k_58dc0a3572')}</span>}
              </div>
            </Field>

            {/* 执行层：不选=跟随工作空间默认层；选了就用那条层。
                选项显示业务可读名（display_name），value 用 id；已停用给可读标注而不是裸 id。 */}
            <Field label={t('k_15154b6ac9')}>
              <Select value={String(form.execution_layer_id || 0)}
                onValueChange={(v) => {
                  const id = Number(v) || 0;
                  // 换层后模型候选不再适用于新通道，清掉旧选择并重新拉候选，
                  // 避免“拿着 A 层的模型名跑在 B 层”这种配不上的组合被静默提交。
                  setForm({ ...form, execution_layer_id: id, models: [] });
                  loadModelOptions(id);
                }}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="0">{t("k_eda5dce5f9")}</SelectItem>
                  {cliLayers.map((l: any) => (
                    <SelectItem key={l.id} value={String(l.id)}>
                      {l.display_name || l.name}{l.status !== 'active' ? t('k_f918805cd2') : ''}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {!!form.execution_layer_id && !cliLayers.some((l: any) => l.id === form.execution_layer_id) && (
                <p className="mt-1 text-xs text-destructive">
                  {t('k_742d05ba93')}
                </p>
              )}
            </Field>

            {/* 可用模型（Chat 端可切换；仅一个/留空时不展示选择框） */}
            <Field label={t('k_5be6fae06c')}>
              <div className="flex flex-wrap gap-2 max-h-[140px] overflow-y-auto">
                {(() => {
                  const opts = Array.from(new Set([...modelOptions, ...(form.models || [])]));
                  return opts.map((m) => {
                    const active = (form.models || []).includes(m);
                    return (
                      <Badge key={m} variant={active ? 'default' : 'outline'} className="cursor-pointer"
                        onClick={() => setForm({ ...form, models: toggleArr(form.models || [], m) as string[] })}>
                        {m}
                      </Badge>
                    );
                  });
                })()}
                {modelOptions.length === 0 && (form.models || []).length === 0 && (
                  <span className="text-xs text-muted-foreground">{t("k_ed92120798")}</span>
                )}
              </div>
            </Field>

            <div className="grid grid-cols-2 gap-4 items-end">
              <div className="flex items-center gap-2">
                <Switch checked={!!form.chart_enabled} onCheckedChange={(v) => setForm({ ...form, chart_enabled: v })} />
                <span className="text-sm">{t("k_6041d6d363")}</span>
              </div>
              <div className="flex items-center gap-2">
                <Switch checked={!!form.is_active} onCheckedChange={(v) => setForm({ ...form, is_active: v })} />
                <span className="text-sm">{t("k_d4e9ca3dd4")}</span>
              </div>
            </div>
          </div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setFormOpen(false)}>{isReadOnly ? t('k_6c14bd7f6f') : t('k_4d0b4688c7')}</Button>
            {!isReadOnly && <Button onClick={handleSave}>{t("k_fadf24dbc5")}</Button>}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

// DB 行 → 前端表单结构(persona/tools/mcp_server_ids/skills 兜底为对象/数组)
function normalizeAsBot(r: any, catalog = MCP_TOOL_GROUPS): AsBot {
  const tools = typeof r.tools === 'string' ? JSON.parse(r.tools || '{}') : (r.tools || {});
  const groups: string[] = Array.isArray(tools) ? tools : (tools.groups || []);
  const standard: string[] = Array.isArray(tools) ? [] : (tools.standard || []);
  // 优先用已配置的逐工具 mcp;旧数据无 mcp 时按 groups 展开全部工具(向后兼容)
  let mcp: Record<string, string[]> = (!Array.isArray(tools) && tools.mcp) ? tools.mcp : {};
  if (Array.isArray(tools) || !Object.prototype.hasOwnProperty.call(tools, 'mcp')) {
    const groupTools = Object.fromEntries(catalog.map(g => [g.group, g.tools.map(t => t.name)]));
    mcp = Object.fromEntries(groups.filter(g => groupTools[g]).map(g => [g, groupTools[g]]));
  }
  return {
    ...r,
    persona: typeof r.persona === 'string' ? JSON.parse(r.persona || '{}') : (r.persona || {}),
    // functions 是功能能力的减法开关，必须随存随取，否则保存一轮就丢了
    tools: {
      groups, standard, mcp,
      external: tools.external || {},
      functions: (Array.isArray(tools) ? {} : tools.functions) || { disabled: [] },
    },
    mcp_server_ids: parseArr(r.mcp_server_ids),
    knowledge_base_ids: parseArr(r.knowledge_base_ids),
    skills: parseArr(r.skills)
      .map((s: any) => (typeof s === 'string' ? s : String(s?.name || s?.key || '')))
      .filter(Boolean),
    models: parseArr(r.models).map((m: any) => String(m)).filter(Boolean),
    execution_layer_id: Number(r.execution_layer_id || 0),
    chart_enabled: !!r.chart_enabled,
    is_active: !!r.is_active,
  };
}

function parseArr(v: any): any[] {
  if (Array.isArray(v)) return v;
  if (typeof v === 'string') { try { return JSON.parse(v) || []; } catch { return []; } }
  return [];
}
