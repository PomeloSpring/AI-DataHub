import { useCallback, useEffect, useMemo, useState } from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import { toast } from '@/lib/toast';
import {
  History,
  Loader2,
  RotateCcw,
  Save,
  Columns2,
  Check,
} from 'lucide-react';
import client from '@/api/client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

// ── Public types ───────────────────────────────────────────────────────

export interface VersionedVersion {
  version: number;
  content: Record<string, unknown>;
  change_log?: string;
  created_at?: string;
  created_by?: string;
  is_current?: boolean;
}

export interface VersionedItem {
  id: number;
  title: string;
  subtitle?: string;
  version?: number;
  content: Record<string, unknown>;
  /** Prompt-only extras (category filter + workspace copy-on-write). */
  category?: string;
  workspaceId?: number;
  promptKey?: string;
  /** True when this is a global row shown under a workspace scope with no override yet. */
  inherited?: boolean;
  /** Small label rendered in the list, e.g. '继承全局' / '已覆盖'. */
  badge?: string;
  /** NL2SQL 合并项: 已并入本条的系统生成规则行的 id, 保存时清空该行避免双重拼接。 */
  rulesRowId?: number;
}

export interface AdapterFilterOption {
  value: string;
  label: string;
}

export interface AdapterFilter {
  /** State/query key, e.g. 'category' | 'workspace_id'. */
  key: string;
  label: string;
  /** Static options; when `dynamic` is set the component loads options at runtime. */
  options?: AdapterFilterOption[];
  /** 'workspaces' → component fetches the workspace list to build options. */
  dynamic?: 'workspaces';
}

export interface VersionedConfigAdapter {
  /** Human-readable label shown in the kind selector. */
  label: string;
  /** Optional filter controls rendered above the list. */
  filters?: AdapterFilter[];
  /**
   * 表单模式的长文本字段(如 system_prompt): 这些字段用多行文本框编辑(正常换行),
   * 其余字段(分类等元数据)保持只读; 未声明时仅有 JSON 模式。
   */
  textFieldKeys?: string[];
  listItems(filterValues?: Record<string, string>): Promise<VersionedItem[]>;
  loadVersions(id: number): Promise<VersionedVersion[]>;
  saveItem(item: VersionedItem, content: Record<string, unknown>, changeLog: string): Promise<void>;
  rollbackItem(id: number, version: number): Promise<void>;
}

// ── Diff helpers (line-based LCS) ──────────────────────────────────────

type DiffKind = 'same' | 'added' | 'removed';
interface DiffLine {
  kind: DiffKind;
  text: string;
}

function pretty(value: unknown): string[] {
  try {
    return JSON.stringify(value, null, 2).split('\n');
  } catch {
    return [String(value)];
  }
}

function diffLines(before: string[], after: string[]): DiffLine[] {
  const n = before.length;
  const m = after.length;
  // Build LCS table
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = before[i] === after[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (before[i] === after[j]) {
      out.push({ kind: 'same', text: before[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ kind: 'removed', text: before[i] });
      i++;
    } else {
      out.push({ kind: 'added', text: after[j] });
      j++;
    }
  }
  while (i < n) out.push({ kind: 'removed', text: before[i++] });
  while (j < m) out.push({ kind: 'added', text: after[j++] });
  return out;
}

// ── Built-in adapters ──────────────────────────────────────────────────

function parseMaybeJson(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object') return value as Record<string, unknown>;
  if (typeof value === 'string') {
    try {
      return JSON.parse(value);
    } catch {
      return {};
    }
  }
  return {};
}

const CATEGORY_OPTIONS: AdapterFilterOption[] = [
  { value: 'all', label: tr('k_a8e369c4b6') },
  { value: 'permission_boundary', label: tr('k_488a5bccbf') },
  { value: 'dialect', label: tr('k_8cc95fc975') },
  { value: 'skill', label: tr('k_476dcfee99') },
];

// 版本化 Prompt 页只治理"护栏 + NL2SQL 生成链"两类真消费点:
// - guardrail:role_style 已退出(角色风格唯一配置点 = AS-BOT persona);
// - 分析类提示词(LLM 分析/元数据分析/结果分析)属技能职责, 由 Skills 管理承接, 不在此呈现。
const PROMPT_KEY_PREFIXES = ['guardrail:', 'nl2sql:'];
const PROMPT_HIDDEN_KEYS = ['guardrail:role_style'];

function promptToItem(
  r: any,
  opts: { inherited?: boolean; workspaceId?: number; badge?: string } = {},
): VersionedItem {
  return {
    id: r.id,
    title: r.prompt_name || r.prompt_key,
    subtitle: r.prompt_key,
    version: r.version,
    promptKey: r.prompt_key,
    category: r.category ?? 'skill',
    workspaceId: opts.inherited ? (opts.workspaceId ?? 0) : (r.workspace_id ?? 0),
    inherited: !!opts.inherited,
    badge: opts.badge,
    rulesRowId: r._rules_id || undefined,
    content: {
      category: r.category ?? 'skill',
      system_prompt: r.system_prompt ?? '',
      user_prompt_template: r.user_prompt_template ?? '',
      description: r.description ?? '',
    },
  };
}

/**
 * nl2sql:rules 已并入 nl2sql:system 单一条目（文件层已合并，DB 行由页面展示合并）：
 * 合并项保存时清空 rules 行，消费端 system+rules 拼接自然退化为单源，无双拼。
 */
function mergeNl2sqlRules(rows: any[]): any[] {
  const rules = rows.find((r) => r.prompt_key === 'nl2sql:rules');
  const rest = rows.filter((r) => r.prompt_key !== 'nl2sql:rules');
  if (!rules) return rest;
  return rest.map((r) => {
    if (r.prompt_key !== 'nl2sql:system') return r;
    const merged = [(r.system_prompt || '').trim(), (rules.system_prompt || '').trim()]
      .filter(Boolean).join('\n\n');
    return {
      ...r,
      prompt_name: (r.prompt_name || tr('k_b017169c9b')) + tr('k_b83526dd48'),
      system_prompt: merged,
      _rules_id: rules.id,
    };
  });
}

export function createPromptAdapter(): VersionedConfigAdapter {
  return {
    label: 'Prompt',
    textFieldKeys: ['system_prompt', 'user_prompt_template', 'description'],
    filters: [
      { key: 'category', label: tr('k_435c5259e4'), options: CATEGORY_OPTIONS },
      { key: 'workspace_id', label: tr('k_4705b88497'), dynamic: 'workspaces' },
    ],
    async listItems(filterValues) {
      const rawCategory = filterValues?.category || 'all';
      const category = rawCategory && rawCategory !== 'all' ? rawCategory : '';
      const ws = Number(filterValues?.workspace_id || '0');
      const baseParams: Record<string, unknown> = { active_only: true };
      if (category) baseParams.category = category;
      const visible = (rows: any[]) => (rows as any[]).filter(
        (r) => PROMPT_KEY_PREFIXES.some((p) => String(r.prompt_key).startsWith(p))
          && !PROMPT_HIDDEN_KEYS.includes(r.prompt_key));

      // Global scope: just the workspace_id=0 defaults
      if (!ws) {
        const { data } = await client.get('/admin/prompts/', { params: { ...baseParams, workspace_id: 0 } });
        return mergeNl2sqlRules(visible(data)).map((r) => promptToItem(r));
      }

      // Workspace scope: merge global defaults with this workspace's overrides (copy-on-write view)
      const [globalRes, wsRes] = await Promise.all([
        client.get('/admin/prompts/', { params: { ...baseParams, workspace_id: 0 } }),
        client.get('/admin/prompts/', { params: { ...baseParams, workspace_id: ws } }),
      ]);
      const globals = mergeNl2sqlRules(visible(globalRes.data));
      const overrides = new Map<string, any>(
        mergeNl2sqlRules(visible(wsRes.data)).map((r: any) => [r.prompt_key, r]));
      const items: VersionedItem[] = [];
      for (const g of globals) {
        const ov = overrides.get(g.prompt_key);
        if (ov) items.push(promptToItem(ov, { badge: tr('k_22c87aaebb') }));
        else items.push(promptToItem(g, { inherited: true, workspaceId: ws, badge: tr('k_50021fe86e') }));
      }
      for (const o of visible(wsRes.data) as any[]) {
        if (!globals.some((g: any) => g.prompt_key === o.prompt_key)) {
          items.push(promptToItem(o, { badge: tr('k_22c87aaebb') }));
        }
      }
      return items;
    },
    async loadVersions(id) {
      const { data } = await client.get(`/admin/prompts/${id}/versions`);
      return (data as any[]).map((v) => ({
        version: v.version,
        change_log: v.change_log,
        created_at: v.created_at,
        created_by: v.created_by,
        is_current: !!v.is_current,
        content: {
          system_prompt: v.system_prompt ?? '',
          user_prompt_template: v.user_prompt_template ?? '',
        },
      }));
    },
    async saveItem(item, content, changeLog) {
      if (item.inherited && item.promptKey) {
        // Copy-on-write: create this workspace's override from the global row
        await client.post('/admin/prompts/', {
          prompt_key: item.promptKey,
          prompt_name: item.title,
          category: content.category ?? item.category ?? 'skill',
          system_prompt: content.system_prompt ?? '',
          user_prompt_template: content.user_prompt_template ?? '',
          description: content.description ?? '',
          workspace_id: item.workspaceId ?? 0,
          change_log: changeLog || tr('k_d08b329250'),
        });
        return;
      }
      await client.put(`/admin/prompts/${item.id}`, {
        category: content.category,
        system_prompt: content.system_prompt,
        user_prompt_template: content.user_prompt_template,
        description: content.description,
        change_log: changeLog || 'Updated',
      });
      // 合并项保存后清空已并入的 rules 行, 保持单一事实源(消费端不再双拼)
      if (item.rulesRowId) {
        try {
          await client.put(`/admin/prompts/${item.rulesRowId}`, {
            system_prompt: '', change_log: '已并入 NL2SQL 系统提示词',
          });
        } catch {
          // 清空失败不阻断本次保存(下次保存可重试)
        }
      }
    },
    async rollbackItem(id, version) {
      await client.post(`/admin/prompts/${id}/rollback`, { version });
    },
  };
}

export function createMcpAdapter(): VersionedConfigAdapter {
  return {
    label: 'MCP Server',
    async listItems() {
      const { data } = await client.get('/admin/mcp-servers/');
      return (data as any[]).map((r) => ({
        id: r.id,
        title: r.name,
        subtitle: r.description,
        version: r.version,
        content: {
          transport: r.transport ?? 'sse',
          url: r.url ?? '',
          command: r.command ?? '',
          args: r.args ?? [],
          env: r.env ?? {},
          tools_config: r.tools_config ?? {},
          description: r.description ?? '',
        },
      }));
    },
    async loadVersions(id) {
      const { data } = await client.get(`/admin/mcp-servers/${id}/versions`);
      return (data as any[]).map((v) => ({
        version: v.version,
        change_log: v.change_log,
        created_at: v.created_at,
        created_by: v.created_by,
        is_current: !!v.is_current,
        content: parseMaybeJson(v.content),
      }));
    },
    async saveItem(item, content, changeLog) {
      await client.put(`/admin/mcp-servers/${item.id}`, { ...content, change_log: changeLog || 'Updated' });
    },
    async rollbackItem(id, version) {
      await client.post(`/admin/mcp-servers/${id}/rollback`, { version });
    },
  };
}

// ── Component ──────────────────────────────────────────────────────────

interface VersionedConfigEditorProps {
  adapters: VersionedConfigAdapter[];
}

export default function VersionedConfigEditor({ adapters }: VersionedConfigEditorProps) {
  const { t } = useTranslation('ui');
  const [adapterIdx, setAdapterIdx] = useState(0);
  const adapter = adapters[adapterIdx];

  const [items, setItems] = useState<VersionedItem[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [jsonText, setJsonText] = useState('{}');
  // 表单模式(长文本字段用多行文本框, 正常换行) vs JSON 模式(结构化字段直编)
  const [mode, setMode] = useState<'form' | 'json'>('form');
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [otherFields, setOtherFields] = useState<Record<string, unknown>>({});
  const [changeLog, setChangeLog] = useState('');
  const [versions, setVersions] = useState<VersionedVersion[]>([]);
  const [loadingList, setLoadingList] = useState(false);
  const [saving, setSaving] = useState(false);
  const [diffVersion, setDiffVersion] = useState<VersionedVersion | null>(null);
  const [filterValues, setFilterValues] = useState<Record<string, string>>({});
  const [workspaces, setWorkspaces] = useState<{ id: number; name: string }[]>([]);

  const jsonError = useMemo(() => {
    try {
      JSON.parse(jsonText);
      return null;
    } catch (e) {
      return (e as Error).message;
    }
  }, [jsonText]);

  const reloadList = useCallback(async () => {
    setLoadingList(true);
    try {
      const list = await adapter.listItems(filterValues);
      setItems(list);
      setSelectedId((prev) => {
        if (prev && list.some((x) => x.id === prev)) return prev;
        return list[0]?.id ?? null;
      });
    } catch (e) {
      toast.error(t('k_318cd0757d', { p0: (e as Error).message }));
    } finally {
      setLoadingList(false);
    }
  }, [adapter, filterValues]);

  useEffect(() => {
    void reloadList();
  }, [reloadList]);

  // Load workspace options once for adapters that expose a dynamic workspace scope filter.
  const needsWorkspaces = adapter.filters?.some((f) => f.dynamic === 'workspaces') ?? false;
  useEffect(() => {
    if (!needsWorkspaces) return;
    let cancelled = false;
    (async () => {
      try {
        const { data } = await client.get('/workspaces');
        if (cancelled) return;
        const list = Array.isArray(data) ? data : ((data as any)?.items ?? []);
        setWorkspaces(
          (list as any[]).map((w) => ({ id: w.id, name: w.name || w.workspace_name || `#${w.id}` })),
        );
      } catch {
        // Non-fatal: the scope filter simply stays empty.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [needsWorkspaces]);

  const selectedItem = items.find((x) => x.id === selectedId) ?? null;

  const splitContent = useCallback((content: Record<string, unknown>) => {
    const keys = adapter.textFieldKeys || [];
    const tf: Record<string, string> = {};
    const other: Record<string, unknown> = {};
    Object.entries(content || {}).forEach(([k, v]) => {
      if (keys.includes(k)) tf[k] = typeof v === 'string' ? v : JSON.stringify(v ?? '');
      else other[k] = v;
    });
    return { tf, other };
  }, [adapter]);

  const loadDetail = useCallback(async (id: number) => {
    const item = items.find((x) => x.id === id);
    if (!item) return;
    setJsonText(JSON.stringify(item.content, null, 2));
    const { tf, other } = splitContent(item.content);
    setFormValues(tf);
    setOtherFields(other);
    setMode((adapter.textFieldKeys?.length ? 'form' : 'json') as 'form' | 'json');
    setChangeLog('');
    setDiffVersion(null);
    try {
      const vs = await adapter.loadVersions(id);
      setVersions(vs);
    } catch (e) {
      setVersions([]);
      toast.error(t('k_45232bf6d2', { p0: (e as Error).message }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, adapter, splitContent]);

  useEffect(() => {
    if (selectedId != null) void loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  /** 当前编辑态合并为 content(表单模式 = 元数据 + 长文本字段; JSON 模式 = 解析 jsonText) */
  function effectiveContent(): Record<string, unknown> | null {
    if (mode === 'form' && adapter.textFieldKeys?.length) {
      return { ...otherFields, ...formValues };
    }
    try {
      return JSON.parse(jsonText);
    } catch {
      return null;
    }
  }

  function switchMode(next: 'form' | 'json') {
    if (next === mode) return;
    if (next === 'json') {
      const c = effectiveContent();
      if (c) setJsonText(JSON.stringify(c, null, 2));
    } else {
      try {
        const { tf, other } = splitContent(JSON.parse(jsonText));
        setFormValues(tf);
        setOtherFields(other);
      } catch {
        toast.error(t('k_132be1b44e'));
        return;
      }
    }
    setMode(next);
  }

  async function handleSave() {
    if (selectedId == null || !selectedItem) return;
    const content = effectiveContent();
    if (!content) return;
    setSaving(true);
    try {
      const wasInherited = selectedItem.inherited;
      await adapter.saveItem(selectedItem, content, changeLog);
      toast.success(wasInherited ? t('k_6be9810519') : t('k_a4c6b78e21'));
      setChangeLog('');
      await reloadList();
      if (!wasInherited) {
        const vs = await adapter.loadVersions(selectedId);
        setVersions(vs);
      }
    } catch (e) {
      toast.error(t('k_c55464dc09', { p0: (e as Error).message }));
    } finally {
      setSaving(false);
    }
  }

  async function handleRollback(v: VersionedVersion) {
    if (selectedId == null || selectedItem?.inherited) return;
    try {
      await adapter.rollbackItem(selectedId, v.version);
      toast.success(t('k_e671518b65', { p0: v.version }));
      await reloadList();
      const vs = await adapter.loadVersions(selectedId);
      setVersions(vs);
    } catch (e) {
      toast.error(t('k_7ada8ff4a9', { p0: (e as Error).message }));
    }
  }

  const diffLines = useMemo(() => {
    if (!diffVersion) return [];
    let current: unknown = {};
    try {
      current = JSON.parse(jsonText);
    } catch {
      current = jsonText;
    }
    return diffLines_pretty(diffVersion.content, current);
  }, [diffVersion, jsonText]);

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{t("k_4802a49d8f")}</h1>
          <p className="text-sm text-muted-foreground">
            {t('k_d6e0536cf2')}
          </p>
        </div>
        <div className="w-56">
          <Select
            value={String(adapterIdx)}
            onValueChange={(val) => {
              setAdapterIdx(Number(val));
              setSelectedId(null);
              setFilterValues({});
            }}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {adapters.map((a, i) => (
                <SelectItem key={a.label} value={String(i)}>
                  {a.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {adapter.filters && adapter.filters.length > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          {adapter.filters.map((f) => {
            const value = filterValues[f.key] ?? (f.dynamic === 'workspaces' ? '0' : 'all');
            return (
              <div key={f.key} className="flex items-center gap-2">
                <span className="text-xs font-medium text-muted-foreground">{f.label}</span>
                <div className="w-48">
                  <Select
                    value={value}
                    onValueChange={(val) =>
                      setFilterValues((prev) => ({ ...prev, [f.key]: val }))
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {f.dynamic === 'workspaces' ? (
                        <>
                          <SelectItem value="0">{t("k_e0523a661c")}</SelectItem>
                          {workspaces.map((w) => (
                            <SelectItem key={w.id} value={String(w.id)}>
                              {w.name}
                            </SelectItem>
                          ))}
                        </>
                      ) : (
                        (f.options ?? []).map((o) => (
                          <SelectItem key={o.value} value={o.value}>
                            {o.label}
                          </SelectItem>
                        ))
                      )}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="grid flex-1 grid-cols-12 gap-4 overflow-hidden">
        {/* Item list */}
        <div className="col-span-3 flex flex-col overflow-hidden rounded-lg border bg-card">
          <div className="flex items-center justify-between border-b px-3 py-2">
            <span className="text-sm font-semibold">{t('k_84596fe537', { p0: adapter.label })}</span>
            <Button size="sm" variant="ghost" onClick={() => void reloadList()}>
              {loadingList ? <Loader2 className="h-4 w-4 animate-spin" /> : <History className="h-4 w-4" />}
            </Button>
          </div>
          <div className="flex-1 overflow-y-auto">
            {items.length === 0 && !loadingList && (
              <p className="p-4 text-sm text-muted-foreground">{t("k_b246458f20")}</p>
            )}
            {items.map((it) => (
              <button
                key={it.id}
                onClick={() => setSelectedId(it.id)}
                className={cn(
                  'block w-full border-b px-3 py-2 text-left text-sm hover:bg-accent',
                  selectedId === it.id && 'bg-accent',
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="truncate font-medium">{it.title}</div>
                  {it.badge && (
                    <span
                      className={cn(
                        'shrink-0 rounded px-1.5 py-0.5 text-[10px]',
                        it.inherited
                          ? 'bg-amber-100 text-amber-700'
                          : 'bg-blue-100 text-blue-700',
                      )}
                    >
                      {it.badge}
                    </span>
                  )}
                </div>
                {it.subtitle && (
                  <div className="truncate text-xs text-muted-foreground">{it.subtitle}</div>
                )}
              </button>
            ))}
          </div>
        </div>

        {/* Config editor */}
        <div className="col-span-5 flex flex-col overflow-hidden rounded-lg border bg-card">
          <div className="flex items-center justify-between border-b px-3 py-2">
            <span className="text-sm font-semibold">
              {t('k_4b0e86946c', { p0: selectedItem ? ` · ${selectedItem.title}` : '' })}
            </span>
            <div className="flex items-center gap-2">
              {adapter.textFieldKeys?.length ? (
                <div className="flex rounded-md border text-xs">
                  <button
                    className={cn('px-2 py-1', mode === 'form' && 'bg-muted font-semibold')}
                    onClick={() => switchMode('form')}
                  >{t("k_1f515561b6")}</button>
                  <button
                    className={cn('px-2 py-1', mode === 'json' && 'bg-muted font-semibold')}
                    onClick={() => switchMode('json')}
                  >JSON</button>
                </div>
              ) : null}
              {mode === 'json' && (jsonError ? (
                <span className="text-xs text-destructive">{t("k_f285764449")}</span>
              ) : (
                <span className="flex items-center gap-1 text-xs text-green-600">
                  <Check className="h-3 w-3" /> {t('k_7f19d12210')}
                </span>
              ))}
            </div>
          </div>
          <div className="flex-1 overflow-auto">
            {mode === 'form' && adapter.textFieldKeys?.length ? (
              <div className="space-y-4 p-3">
                {adapter.textFieldKeys.map((fk) => (
                  <div key={fk}>
                    <label className="mb-1 block text-xs font-medium text-muted-foreground">
                      {fk === 'system_prompt' ? t('k_6c47406c83')
                        : fk === 'user_prompt_template' ? t('k_2e607bef54')
                        : fk === 'description' ? t('k_26670dda42')
                        : fk}
                    </label>
                    <textarea
                      value={formValues[fk] ?? ''}
                      onChange={(e) => setFormValues((prev) => ({ ...prev, [fk]: e.target.value }))}
                      className="w-full rounded-md border bg-background p-2 text-sm leading-relaxed focus:outline-none focus:ring-1 focus:ring-ring"
                      style={{ minHeight: fk === 'description' ? '3rem' : '16rem', fontFamily: 'ui-monospace, monospace' }}
                    />
                  </div>
                ))}
                {Object.keys(otherFields).length > 0 && (
                  <div className="rounded-md bg-muted/40 p-2 text-xs text-muted-foreground">
                    {t('k_e6c593b19b', { p0: JSON.stringify(otherFields) })}
                  </div>
                )}
              </div>
            ) : (
              <CodeMirror
                value={jsonText}
                height="100%"
                theme="light"
                extensions={[json()]}
                onChange={(val) => setJsonText(val)}
              />
            )}
          </div>
          <div className="flex items-center gap-2 border-t px-3 py-2">
            <Input
              placeholder={t('k_41e3b933b5')}
              value={changeLog}
              onChange={(e) => setChangeLog(e.target.value)}
              className="flex-1"
            />
            <Button
              onClick={() => void handleSave()}
              disabled={!selectedItem || saving || (mode === 'json' && !!jsonError)}
            >
              {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}
              {selectedItem?.inherited ? t('k_4f4f045e47') : t('k_fadf24dbc5')}
            </Button>
          </div>
        </div>

        {/* Version history */}
        <div className="col-span-4 flex flex-col overflow-hidden rounded-lg border bg-card">
          <div className="border-b px-3 py-2 text-sm font-semibold">{t("k_8770418ba3")}</div>
          <div className="flex-1 overflow-y-auto">
            {versions.length === 0 && (
              <p className="p-4 text-sm text-muted-foreground">{t("k_fff4ee42b2")}</p>
            )}
            {versions.map((v) => (
              <div key={v.version} className="border-b px-3 py-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="font-semibold">
                    v{v.version}
                    {v.is_current && (
                      <span className="ml-2 rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-700">
                        current
                      </span>
                    )}
                  </span>
                  <div className="flex items-center gap-1">
                    <Button size="sm" variant="ghost" onClick={() => setDiffVersion(v)} title={t("k_ef765015fe")}>
                      <Columns2 className="h-4 w-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void handleRollback(v)}
                      disabled={!!selectedItem?.inherited}
                      title={selectedItem?.inherited ? t('k_9c720d858e') : t('k_d00b485b26')}
                    >
                      <RotateCcw className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
                {v.change_log && <div className="text-xs text-muted-foreground">{v.change_log}</div>}
                <div className="text-xs text-muted-foreground">
                  {v.created_by || 'system'} · {v.created_at ? String(v.created_at).slice(0, 19) : ''}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Diff dialog */}
      <Dialog open={!!diffVersion} onOpenChange={(o) => !o && setDiffVersion(null)}>
        <DialogContent className="max-w-4xl">
          <DialogHeader>
            <DialogTitle>{t('k_395a0428b0', { p0: diffVersion?.version })}</DialogTitle>
            <DialogDescription>{t("k_b4930c5203")}</DialogDescription>
          </DialogHeader>
          <div className="max-h-[60vh] overflow-auto rounded border font-mono text-xs">
            {diffLines.map((line, idx) => (
              <div
                key={idx}
                className={cn(
                  'whitespace-pre px-2',
                  line.kind === 'added' && 'bg-green-50 text-green-700',
                  line.kind === 'removed' && 'bg-red-50 text-red-700',
                  line.kind === 'same' && 'text-foreground',
                )}
              >
                {line.kind === 'added' && '+ '}
                {line.kind === 'removed' && '- '}
                {line.text}
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function diffLines_pretty(before: unknown, after: unknown): DiffLine[] {
  return diffLines(pretty(before), pretty(after));
}

// Keep a named export alias for convenient page wiring
export { VersionedConfigEditor };
