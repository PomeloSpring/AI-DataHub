/**
 * OntologyWorkbench — 本体建模工作台外壳（纯布局，不含数据逻辑）。
 *
 * 信息架构（重设计）：
 *   顶栏：本体建模 · 模型切换器 · 生效版/草案分段 · 待发布标记 ·（右侧）模型级动作
 *   第二行：生成与导入（数据源 / 参考知识库 / 生成 / 导入 YAML / 刷新）
 *   主体：左侧能力分区（对象/行为/规则/场景/站点·术语/标签·变更对比·全部模型）
 *         + 中央画布（由调用方给 head 与内容）+ 底部草案托盘
 *
 * 边界约定：
 * - 模型 = 领域 = 发布单元，切换器只是入口收敛，不改任何数据边界；
 * - 关系图谱已内嵌为页内页签（不跳外层菜单）；建模体检（本模型对象级缺口）/ 建模待办（跨模型资产）也已是页内页签，
 *   本组件只负责把它们当作普通 tab 渲染，不再有独立路由的 link 项；
 * - 入口显隐由调用方按权限码算好再传进来，本组件不做第二套权限判断。
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';
import { AlertTriangle, Boxes, CheckCircle2, ChevronDown, GitCompare, Loader2, Search, X } from 'lucide-react';
import { t } from '@/i18n/runtime';

export type WbStatus = 'draft' | 'active' | 'archived';

export interface WbModel {
  id: number;
  name: string;
  kind?: string;
  domain?: string;
  status: WbStatus;
  object_count: number;
  deployment_count?: number;
  has_draft?: boolean;
  updated_at?: string;
}

export interface WbRailItem {
  key: string;
  label: string;
  icon: ReactNode;
  count?: number;
  /** 计数用告警色（待处理/待发布） */
  warn?: boolean;
  /** 独立路由跳转：渲染成普通按钮，不作为页签 */
  link?: boolean;
  /** 本地动作（如打开只读文档面板）：普通按钮，不选中、不显示外链箭号 */
  action?: boolean;
  title?: string;
}

export interface WbRailSection {
  title?: string;
  items: WbRailItem[];
}

export interface WbViewSeg {
  mode: 'live' | 'draft';
  liveCount: number;
  draftCount: number;
  hasDraft: boolean;
  draftTitle: string;
  onSwitch: (m: 'live' | 'draft') => void;
}

export interface WbTray {
  pending: number;
  added: number;
  changed: number;
  removed: number;
  summary: string;
  /** 基线失效等阻断原因：必须常驻可见，不能只在按钮 title 里 */
  blockHint?: string;
  publishDisabled: boolean;
  publishTitle: string;
  publishing: boolean;
  canPublish: boolean;
  onPublish: () => void;
  onViewChanges: () => void;
}

const STATUS_CHIP: Record<WbStatus, { label: string; cls: string }> = {
  draft: { label: t('k_bbcb968bf3'), cls: 'bg-amber-500/10 text-amber-600 border-amber-500/30' },
  active: { label: t('k_83a991d77c'), cls: 'bg-green-500/10 text-green-600 border-green-500/30' },
  archived: { label: t('k_c3ba167c87'), cls: 'bg-muted text-muted-foreground border-border' },
};

function ModelSwitcher({ current, models, kw, onKw, onPick, showArchived, onShowArchived, onAllModels }: {
  current: WbModel | null;
  models: WbModel[];
  kw: string;
  onKw: (v: string) => void;
  onPick: (id: number) => void;
  showArchived: boolean;
  onShowArchived: (v: boolean) => void;
  onAllModels: () => void;
}) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  // 自定义下拉（不用 Popover）：面板就在按钮下方，点外部/按 Esc 关闭
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const k = kw.trim().toLowerCase();
  const hit = models.filter((m) => !k
    || (m.name || '').toLowerCase().includes(k)
    || (m.domain || '').toLowerCase().includes(k));
  const groups: { title: string; items: WbModel[] }[] = [
    { title: t('k_d381fb12f2'), items: hit.filter((m) => m.kind === 'business') },
    { title: t('k_f6b932be1d'), items: hit.filter((m) => m.kind === 'system') },
    { title: t('k_f5db121235'), items: hit.filter((m) => m.kind === 'source') },
    { title: t('k_b28f13ea3e'), items: hit.filter((m) => !m.kind) },
  ];
  const chip = current ? (STATUS_CHIP[current.status] || STATUS_CHIP.draft) : null;

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex h-9 items-center gap-2 rounded-lg border border-border bg-background px-2.5 text-left transition-colors hover:bg-muted/50"
      >
        <span className={cn('max-w-[230px] truncate text-sm font-medium', !current && 'text-muted-foreground')}>
          {current ? current.name : t('k_9368f072d8')}
        </span>
        {current && chip ? <Badge variant="outline" className={cn('h-5 px-1.5 text-[10px]', chip.cls)}>{chip.label}</Badge> : null}
        {current ? (
          <span className="text-[11px] text-muted-foreground">{t('k_013a59c4bf', { p0: current.object_count })}</span>
        ) : null}
        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 w-[452px] overflow-hidden rounded-xl border border-border bg-popover shadow-xl">
          <div className="border-b border-border p-2">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input value={kw} onChange={(e) => onKw(e.target.value)}
                placeholder={t('k_450232a983')} className="h-8 pl-7 text-xs" />
            </div>
          </div>
          <div className="max-h-[min(54vh,380px)] overflow-y-auto p-1.5">
            {groups.map((g) => (g.items.length ? (
              <div key={g.title} className="mb-1">
                <div className="px-2 pb-1 pt-1.5 text-[11px] uppercase tracking-wide text-muted-foreground">
                  {g.title}（{g.items.length}）
                </div>
                {g.items.map((m) => {
                  const st = STATUS_CHIP[m.status] || STATUS_CHIP.draft;
                  return (
                    <button key={m.id} type="button"
                      onClick={() => { onPick(m.id); setOpen(false); }}
                      className={cn('flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-muted/60',
                        current?.id === m.id && 'bg-primary/5')}>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] font-medium">{m.name}</span>
                        <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                          {m.domain ? `${m.domain} · ` : ''}{t('k_013a59c4bf', { p0: m.object_count })}
                          {m.updated_at ? ` · ${m.updated_at.slice(0, 16).replace('T', ' ')}` : ''}
                        </span>
                      </span>
                      <span className="flex max-w-[150px] flex-wrap justify-end gap-1">
                        {(m.deployment_count ?? 0) >= 2 && (
                          <Badge variant="outline" className="h-5 border-indigo-300 px-1.5 text-[10px] text-indigo-600">
                            {t('k_dbd9450a62', { p0: m.deployment_count })}</Badge>
                        )}
                        {m.has_draft && (
                          <Badge variant="outline" className="h-5 border-amber-500/40 px-1.5 text-[10px] text-amber-600">
                            {t('k_0737ae75a2')}</Badge>
                        )}
                        <Badge variant="outline" className={cn('h-5 px-1.5 text-[10px]', st.cls)}>{st.label}</Badge>
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : null))}
            {!hit.length && (
              <div className="px-2 py-6 text-center text-xs text-muted-foreground">{t('k_ab2ff2ce5f', { p0: kw })}</div>
            )}
          </div>
          <div className="flex items-center gap-2 border-t border-border px-2 py-1.5">
            <button type="button" onClick={() => { onAllModels(); setOpen(false); }}
              className="rounded-md px-2 py-1 text-xs text-primary hover:bg-primary/5">
              {t('k_688facc5f5')} ▦
            </button>
            <div className="flex-1" />
            <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground">
              <Switch checked={showArchived} onCheckedChange={onShowArchived} className="scale-75" />
              {t('k_208479b765')}
            </label>
          </div>
        </div>
      )}
    </div>
  );
}

export default function OntologyWorkbench({
  title, current, models, modelKw, onModelKw, onPickModel, showArchived, onShowArchived,
  view, tools, actions, railSections, nav, onNav, canvasHead, tray, children,
}: {
  title: string;
  current: WbModel | null;
  models: WbModel[];
  modelKw: string;
  onModelKw: (v: string) => void;
  onPickModel: (id: number) => void;
  showArchived: boolean;
  onShowArchived: (v: boolean) => void;
  view: WbViewSeg;
  tools?: ReactNode;
  actions?: ReactNode;
  railSections: WbRailSection[];
  nav: string;
  onNav: (k: string) => void;
  canvasHead?: ReactNode;
  tray?: WbTray | null;
  children: ReactNode;
}) {
  return (
    <div className="flex h-[calc(100vh-3rem)] min-h-[540px] flex-col gap-2">
      {/* ── 顶栏：模型 = 入口收敛，切换不改数据边界 ── */}
      <header className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-2">
          <Boxes className="h-5 w-5 text-primary" />
          <h1 className="text-base font-semibold">{title}</h1>
        </div>
        <ModelSwitcher
          current={current}
          models={models}
          kw={modelKw}
          onKw={onModelKw}
          onPick={onPickModel}
          showArchived={showArchived}
          onShowArchived={onShowArchived}
          onAllModels={() => onNav('models')}
        />

        {/* 生效版 / 草案：同一份 doc 的两个视图，不是两个页面 */}
        <div className="inline-flex items-center rounded-md border border-border bg-muted/40 p-0.5">
          <button type="button" onClick={() => view.onSwitch('live')} title={t('k_2f0436bcbf')}
            className={cn('h-7 rounded px-2.5 text-xs transition-colors',
              view.mode === 'live' ? 'bg-background font-medium shadow-sm' : 'text-muted-foreground hover:text-foreground')}>
            {t('k_3a49375c2d', { p0: view.liveCount })}
          </button>
          <button type="button" onClick={() => view.onSwitch('draft')} disabled={!view.hasDraft}
            title={view.draftTitle}
            className={cn('inline-flex h-7 items-center gap-1 rounded px-2.5 text-xs transition-colors',
              !view.hasDraft ? 'cursor-not-allowed text-muted-foreground/50'
                : view.mode === 'draft' ? 'bg-amber-500/15 font-medium text-amber-700 shadow-sm'
                  : 'text-amber-600 hover:bg-amber-500/10')}>
            {t('k_2b37a18790', { p0: view.draftCount })}
            {tray && tray.pending > 0 && (
              <Badge variant="outline" className="h-4 border-amber-500/40 px-1 text-[10px] text-amber-600">{tray.pending}</Badge>
            )}
          </button>
        </div>

        {view.mode === 'draft' && (
          <Badge variant="outline" className="text-[10px] font-normal border-amber-500/40 text-amber-600">
            {t('k_ce96c43684')}
          </Badge>
        )}
        {/* 待发布标记：Badge 文本保持「待发布」，计数单独一格（避免拼成一个不可读的字符串） */}
        {tray && tray.pending > 0 && (
          <span className="inline-flex items-center gap-1">
            <Badge variant="outline" className="border-amber-500/40 text-amber-600">{t('k_0737ae75a2')}</Badge>
            <span className="text-[11px] tabular-nums text-amber-600">{tray.pending}</span>
          </span>
        )}

        <div className="flex-1" />
        {actions}
      </header>

      {/* ── 第二行：生成与导入（数据源只是生成目标，不是筛选器） ── */}
      {tools && (
        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-2">
          {tools}
        </div>
      )}

      {/* ── 主体：能力分区 + 画布 ── */}
      <div className="flex min-h-0 flex-1 gap-3">
        <div role="tablist" aria-orientation="vertical"
          className="flex w-[172px] shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-border pr-2">
          {railSections.map((sec, si) => (
            <div key={sec.title || si} className="flex flex-col gap-0.5">
              {sec.title && (
                <div className="px-2 pb-1 pt-2 text-[10px] uppercase tracking-wide text-muted-foreground">
                  {sec.title}
                </div>
              )}
              {sec.items.map((it) => (it.link || it.action) ? (
                <button key={it.key} type="button" onClick={() => onNav(it.key)} title={it.title}
                  className="flex h-8 items-center gap-2 rounded-md px-2 text-left text-xs text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground">
                  <span className="shrink-0 opacity-80">{it.icon}</span>
                  <span className="truncate">{it.label}</span>
                  {it.link && <span className="ml-auto shrink-0 text-[10px] text-muted-foreground/70">↗</span>}
                </button>
              ) : (
                <button key={it.key} type="button" role="tab" aria-selected={nav === it.key}
                  onClick={() => onNav(it.key)}
                  className={cn('flex h-8 items-center gap-2 rounded-md px-2 text-left text-xs transition-colors',
                    nav === it.key
                      ? 'bg-primary/10 font-medium text-primary'
                      : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground')}>
                  <span className="shrink-0 opacity-80">{it.icon}</span>
                  <span className="truncate">{it.label}</span>
                  {it.count !== undefined && it.count > 0 && (
                    <span className={cn('ml-auto shrink-0 rounded-full px-1.5 text-[10px] tabular-nums',
                      it.warn ? 'bg-amber-500/15 text-amber-600' : 'bg-muted text-muted-foreground')}>
                      {it.count}
                    </span>
                  )}
                </button>
              ))}
            </div>
          ))}
        </div>

        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          {canvasHead && <div className="shrink-0 space-y-1.5 pb-2">{canvasHead}</div>}
          <div className="relative min-h-0 flex-1 overflow-hidden rounded-lg border border-border bg-card">
            {children}
          </div>
        </main>
      </div>

      {/* ── 底部草案托盘：变更集中在一个出口，发布只有一个按钮 ── */}
      {tray && tray.pending > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-600" />
          <span className="font-medium text-amber-700">{t('k_dc832ca790')}</span>
          <span className="tabular-nums text-amber-700">{tray.pending}</span>
          {/* 托盘内的增减用 +N/-N 表达：与审阅面板里的「新增 1 / 修改 1」是不同口径，避免两处同串 */}
          <span className="inline-flex items-center gap-1">
            {tray.added > 0 && <Badge variant="outline" className="h-5 border-green-500/40 px-1.5 text-[10px] text-green-600">{t('k_2cd9e6ce81')} +{tray.added}</Badge>}
            {tray.changed > 0 && <Badge variant="outline" className="h-5 border-blue-500/40 px-1.5 text-[10px] text-blue-600">{t('k_c9c77517fe')} +{tray.changed}</Badge>}
            {tray.removed > 0 && <Badge variant="outline" className="h-5 border-red-500/40 px-1.5 text-[10px] text-red-600">{t('k_3755f56f2f')} -{tray.removed}</Badge>}
          </span>
          <span className="min-w-0 flex-1 truncate text-muted-foreground">{tray.summary}</span>
          {tray.blockHint && <span className="text-red-600">{tray.blockHint}</span>}
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <button type="button" onClick={tray.onViewChanges}
              className="inline-flex h-7 items-center gap-1 rounded-md border border-border bg-background px-2 text-xs hover:bg-accent">
              <GitCompare className="h-3 w-3" />{t('k_8d535108e6')}
            </button>
            {tray.canPublish && (
              <button type="button" onClick={tray.onPublish} disabled={tray.publishDisabled || tray.publishing}
                title={tray.publishTitle}
                className="inline-flex h-7 items-center gap-1 rounded-md bg-primary px-2.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50">
                {tray.publishing ? <Loader2 className="h-3 w-3 animate-spin" /> : <CheckCircle2 className="h-3 w-3" />}
                {t('k_d4768779cc', { p0: tray.pending })}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** 画布头部的「说明条」：统一措辞，避免每个页签各写一套 */
export function CanvasNote({ tone = 'muted', children }: { tone?: 'muted' | 'warn' | 'amber'; children: ReactNode }) {
  const cls = tone === 'warn' ? 'border-red-500/30 bg-red-500/5 text-red-600'
    : tone === 'amber' ? 'border-amber-500/30 bg-amber-500/5 text-amber-600'
      : 'border-border bg-muted/40 text-muted-foreground';
  return (
    <div className={cn('flex items-start gap-1.5 rounded-md border px-2.5 py-1 text-[11px]', cls)}>
      <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export { X as WbCloseIcon };