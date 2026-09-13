import { useCallback, useEffect, useMemo, useState } from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import { toast } from 'sonner';
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
  { value: 'all', label: '全部分类' },
  { value: 'role_style', label: '角色风格' },
  { value: 'permission_boundary', label: '权限边界' },
  { value: 'dialect', label: '方言约束' },
  { value: 'skill', label: '技能Prompt' },
];

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
    content: {
      category: r.category ?? 'skill',
      system_prompt: r.system_prompt ?? '',
      user_prompt_template: r.user_prompt_template ?? '',
      description: r.description ?? '',
    },
  };
}

export function createPromptAdapter(): VersionedConfigAdapter {
  return {
    label: 'Prompt',
    filters: [
      { key: 'category', label: '分类', options: CATEGORY_OPTIONS },
      { key: 'workspace_id', label: '作用域', dynamic: 'workspaces' },
    ],
    async listItems(filterValues) {
      const rawCategory = filterValues?.category || 'all';
      const category = rawCategory && rawCategory !== 'all' ? rawCategory : '';
      const ws = Number(filterValues?.workspace_id || '0');
      const baseParams: Record<string, unknown> = { active_only: true };
      if (category) baseParams.category = category;

      // Global scope: just the workspace_id=0 defaults
      if (!ws) {
        const { data } = await client.get('/admin/prompts/', { params: { ...baseParams, workspace_id: 0 } });
        return (data as any[]).map((r) => promptToItem(r));
      }

      // Workspace scope: merge global defaults with this workspace's overrides (copy-on-write view)
      const [globalRes, wsRes] = await Promise.all([
        client.get('/admin/prompts/', { params: { ...baseParams, workspace_id: 0 } }),
        client.get('/admin/prompts/', { params: { ...baseParams, workspace_id: ws } }),
      ]);
      const globals = globalRes.data as any[];
      const overrides = new Map<string, any>((wsRes.data as any[]).map((r) => [r.prompt_key, r]));
      const items: VersionedItem[] = [];
      for (const g of globals) {
        const ov = overrides.get(g.prompt_key);
        if (ov) items.push(promptToItem(ov, { badge: '已覆盖' }));
        else items.push(promptToItem(g, { inherited: true, workspaceId: ws, badge: '继承全局' }));
      }
      for (const o of wsRes.data as any[]) {
        if (!globals.some((g) => g.prompt_key === o.prompt_key)) {
          items.push(promptToItem(o, { badge: '已覆盖' }));
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
          change_log: changeLog || '创建工作空间覆盖',
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
  const [adapterIdx, setAdapterIdx] = useState(0);
  const adapter = adapters[adapterIdx];

  const [items, setItems] = useState<VersionedItem[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [jsonText, setJsonText] = useState('{}');
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
      toast.error(`加载列表失败: ${(e as Error).message}`);
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

  const loadDetail = useCallback(async (id: number) => {
    const item = items.find((x) => x.id === id);
    if (!item) return;
    setJsonText(JSON.stringify(item.content, null, 2));
    setChangeLog('');
    setDiffVersion(null);
    try {
      const vs = await adapter.loadVersions(id);
      setVersions(vs);
    } catch (e) {
      setVersions([]);
      toast.error(`加载版本历史失败: ${(e as Error).message}`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, adapter]);

  useEffect(() => {
    if (selectedId != null) void loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  async function handleSave() {
    if (selectedId == null || !selectedItem || jsonError) return;
    let content: Record<string, unknown>;
    try {
      content = JSON.parse(jsonText);
    } catch {
      return;
    }
    setSaving(true);
    try {
      const wasInherited = selectedItem.inherited;
      await adapter.saveItem(selectedItem, content, changeLog);
      toast.success(wasInherited ? '已创建工作空间覆盖' : '已保存并生成新版本');
      setChangeLog('');
      await reloadList();
      if (!wasInherited) {
        const vs = await adapter.loadVersions(selectedId);
        setVersions(vs);
      }
    } catch (e) {
      toast.error(`保存失败: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleRollback(v: VersionedVersion) {
    if (selectedId == null || selectedItem?.inherited) return;
    try {
      await adapter.rollbackItem(selectedId, v.version);
      toast.success(`已回滚到 v${v.version}`);
      await reloadList();
      const vs = await adapter.loadVersions(selectedId);
      setVersions(vs);
    } catch (e) {
      toast.error(`回滚失败: ${(e as Error).message}`);
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
          <h1 className="text-2xl font-bold">版本化配置管理</h1>
          <p className="text-sm text-muted-foreground">
            统一编辑 Prompt / MCP 配置，自动记录版本快照，支持历史 diff 与一键回滚。
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
                          <SelectItem value="0">全局默认</SelectItem>
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
            <span className="text-sm font-semibold">{adapter.label} 列表</span>
            <Button size="sm" variant="ghost" onClick={() => void reloadList()}>
              {loadingList ? <Loader2 className="h-4 w-4 animate-spin" /> : <History className="h-4 w-4" />}
            </Button>
          </div>
          <div className="flex-1 overflow-y-auto">
            {items.length === 0 && !loadingList && (
              <p className="p-4 text-sm text-muted-foreground">暂无数据</p>
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

        {/* JSON editor */}
        <div className="col-span-5 flex flex-col overflow-hidden rounded-lg border bg-card">
          <div className="flex items-center justify-between border-b px-3 py-2">
            <span className="text-sm font-semibold">
              配置内容{selectedItem ? ` · ${selectedItem.title}` : ''}
            </span>
            {jsonError ? (
              <span className="text-xs text-destructive">JSON 格式错误</span>
            ) : (
              <span className="flex items-center gap-1 text-xs text-green-600">
                <Check className="h-3 w-3" /> JSON 有效
              </span>
            )}
          </div>
          <div className="flex-1 overflow-auto">
            <CodeMirror
              value={jsonText}
              height="100%"
              theme="light"
              extensions={[json()]}
              onChange={(val) => setJsonText(val)}
            />
          </div>
          <div className="flex items-center gap-2 border-t px-3 py-2">
            <Input
              placeholder="变更说明 (change log)"
              value={changeLog}
              onChange={(e) => setChangeLog(e.target.value)}
              className="flex-1"
            />
            <Button onClick={() => void handleSave()} disabled={!selectedItem || !!jsonError || saving}>
              {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}
              {selectedItem?.inherited ? '创建覆盖' : '保存'}
            </Button>
          </div>
        </div>

        {/* Version history */}
        <div className="col-span-4 flex flex-col overflow-hidden rounded-lg border bg-card">
          <div className="border-b px-3 py-2 text-sm font-semibold">版本历史</div>
          <div className="flex-1 overflow-y-auto">
            {versions.length === 0 && (
              <p className="p-4 text-sm text-muted-foreground">选择一项以查看版本历史</p>
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
                    <Button size="sm" variant="ghost" onClick={() => setDiffVersion(v)} title="对比">
                      <Columns2 className="h-4 w-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void handleRollback(v)}
                      disabled={!!selectedItem?.inherited}
                      title={selectedItem?.inherited ? '继承项不可回滚' : '回滚'}
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
            <DialogTitle>版本对比 · v{diffVersion?.version} → 当前编辑内容</DialogTitle>
            <DialogDescription>左侧为历史版本快照，右侧为当前编辑器内容。</DialogDescription>
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
