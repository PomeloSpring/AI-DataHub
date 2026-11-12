// 智能问数输入框的「@」文件引用面板：把「归档资产」与「本会话工作区文件」两类候选
// 按分栏呈现，↑↓ 选条目、←→ 跳类别、Enter/Tab 引用、Esc 退出（与「/」面板同一套键位，
// 用户不必记两套交互）。
//
// 为什么只有这两类（口径 2026-11-04）：**上云 = 备份，归档 = 资产**。
// 会话产物每轮自动镜像到云空间，那是换节点/清卷时的容灾副本，不是"我可以随手拿来用"
// 的清单；真要把某个产物当上下文长期复用，先在原会话归档收藏（一次显式的资产动作），
// 之后任何会话里 @ 它。所以 @ 的候选面 = 归档资产（账号级、跨工作空间）+ 本会话文件。
//
// 为什么选中后不往输入框里留 `@xxx` 文本：文件名不是引用凭据（重名、改名都会错），
// 引用凭据在 chip 上挂着并随请求体提交；正文里留一串 @文字 反而会让模型把它当问题的一部分。
import { useEffect, useMemo, useRef, useState } from 'react';
import { Archive, FolderOpen } from 'lucide-react';
import type { CloudFileRef } from '../../stores/chatStore';

/** 候选项 = 发送时的引用凭据（同一份类型，避免面板与请求体两边各写一套字段漂移）。 */
export type CloudFileItem = CloudFileRef;

export interface CloudFilesPayload { assets?: CloudFileItem[]; session_files?: CloudFileItem[] }

export type FileGroupId = 'asset' | 'session';

export interface FileGroup { id: FileGroupId; label: string; items: CloudFileItem[] }

const GROUP_LABEL: Record<FileGroupId, string> = { asset: '归档资产', session: '本会话文件' };
const GROUP_ORDER: FileGroupId[] = ['asset', 'session'];
const ICONS: Record<FileGroupId, typeof Archive> = { asset: Archive, session: FolderOpen };

/**
 * `@` 的等价触发符（均为单字符）：中文输入法的中文标点模式下 Shift+2 出的是全角「＠」。
 * 与「/」面板同理——不认它就是逼用户先切输入法。
 */
export const MENTION_TRIGGERS = ['@', '＠'];
/** 写给用户看的触发符写法（与 MENTION_TRIGGERS 同源）。 */
export const MENTION_TRIGGER_HINT = '@ 或 ＠';

/** 光标前最近一个触发符的下标（-1 = 没有）。 */
function lastTriggerAt(before: string): number {
  return MENTION_TRIGGERS.reduce((acc, ch) => Math.max(acc, before.lastIndexOf(ch)), -1);
}

/**
 * 取光标前正在输入的「触发符 + xxx」片段（片段内不得有空白，否则视为普通文字）。
 *
 * 与 `/` 不同，这里**不要求**触发符前有空白：中文输入没有分词空格，"帮我看看@销售" 就是
 * 最自然的写法。误触发的代价由调用方兜住（无候选时 Enter 只收面板、不拦发送）。
 */
export function mentionQueryOf(value: string, caret: number): string | null {
  const before = value.slice(0, Math.max(0, caret));
  const at = lastTriggerAt(before);
  if (at < 0) return null;
  const token = before.slice(at + 1);
  if (/\s/.test(token)) return null;
  return token.trim().toLowerCase();
}

/** 摘掉光标前正在输入的「触发符 + xxx」片段（Esc 退出用，不留半截检索串）。 */
export function stripMentionToken(value: string, caret: number): string {
  const at = lastTriggerAt(value.slice(0, Math.max(0, caret)));
  if (at < 0) return value;
  return value.slice(0, at) + value.slice(Math.max(0, caret));
}

export function formatSize(bytes?: number): string {
  const n = Number(bytes || 0);
  if (n <= 0) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** 候选按来源分栏：服务端已按属主与检索词过滤，这里只摘掉本轮已引用过的那些。 */
export function buildFileGroups(payload: CloudFilesPayload | null, picked: CloudFileItem[]): FileGroup[] {
  const taken = new Set(picked.map(p => `${p.source}:${p.ref}`));
  // 不做跨栏同名去重：归档是本会话产物的**独立副本**，两者是两个对象（云端 key 不同、
  // 可能一份被清理过）。按来源分栏已经把"引用的是哪一个"说清了；跨栏去重反而会让
  // 「本会话里那份」凭空从清单消失，用户看不到也选不到。
  return GROUP_ORDER.map(id => ({
    id,
    label: GROUP_LABEL[id],
    items: (payload?.[id === 'asset' ? 'assets' : 'session_files'] || [])
      .filter(it => it && it.ref && !taken.has(`${it.source}:${it.ref}`)),
  }));
}

/** 分栏导航状态（↑↓ 栏内走、←→ 跳栏、检索词变了回首条），与 useSlashMenu 同行为。 */
export function useCloudFileMenu(payload: CloudFilesPayload | null, query: string | null, picked: CloudFileItem[]) {
  const [tab, setTab] = useState(0);
  const [row, setRow] = useState(0);
  const groups = useMemo(() => buildFileGroups(payload, picked), [payload, picked]);

  const enabled = groups.map((g, i) => (g.items.length ? i : -1)).filter(i => i >= 0);
  const tabIdx = enabled.includes(tab) ? tab : (enabled[0] ?? 0);
  const items = groups[tabIdx]?.items ?? [];
  const rowIdx = items.length ? Math.min(row, items.length - 1) : 0;

  useEffect(() => {
    setRow(0);
    if (query === null) setTab(0);
  }, [query]);

  const moveRow = (delta: number) => {
    if (!items.length) return;
    setRow((rowIdx + delta + items.length) % items.length);
  };
  const moveTab = (delta: number) => {
    if (enabled.length < 2) return;
    const pos = Math.max(0, enabled.indexOf(tabIdx));
    setTab(enabled[(pos + delta + enabled.length) % enabled.length]);
    setRow(0);
  };
  const gotoTab = (index: number) => {
    if (!groups[index]?.items.length) return;   // 空栏点不动，不给按了没反应的哑标签
    setTab(index);
    setRow(0);
  };
  const current = (): CloudFileItem | null => items[rowIdx] ?? null;

  return { groups, enabled, tab: tabIdx, row: rowIdx, items, setRow, moveRow, moveTab, gotoTab, current };
}

/**
 * 候选清单滚动区：限高落在原生 div 上（不用 Radix ScrollArea，理由同 SlashCommandMenu：
 * 它的 Root 会把 absolute 掉回静态流并算不出确定高度，历史现象是面板铺满整屏）。
 */
function ScrollList({ activeIndex, children }: { activeIndex: number; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current?.querySelector('[data-active]');
    // 可选调用：jsdom 不实现 scrollIntoView，不能让它把用例抱崩
    el?.scrollIntoView?.({ block: 'nearest' });
  }, [activeIndex]);
  return <div ref={ref} className="max-h-[280px] min-h-[96px] overflow-y-auto">{children}</div>;
}

/** 条目的次要信息行：归档资产说清"来自哪个空间"，会话文件说清"在工作区哪个位置"。 */
function itemHint(item: CloudFileItem): string {
  if (item.source === 'asset') {
    return item.origin_workspace_name ? `来自「${item.origin_workspace_name}」` : '归档资产';
  }
  return item.ref.includes('/') ? item.ref : '本会话产物';
}

function Key({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded border border-border/70 bg-muted/60 px-1.5 py-0.5 font-mono text-[11px] leading-none text-muted-foreground">
      {children}
    </span>
  );
}

export default function CloudFileMenu({
  groups, tab, activeIndex, onHover, onTabChange, onPick, query, loading, error,
}: {
  groups: FileGroup[];
  tab: number;
  activeIndex: number;
  onHover: (index: number) => void;
  onTabChange: (index: number) => void;
  onPick: (item: CloudFileItem) => void;
  query?: string;
  loading?: boolean;
  error?: string;
}) {
  const shell = 'absolute bottom-full left-0 right-0 z-30 mb-2 rounded-xl border bg-card shadow-lg';
  if (error) {
    // 候选加载失败必须可见，不得静默当成"你没有可引用的文件"
    return <div className={`${shell} px-4 py-3 text-[13px] text-destructive`}>{error}</div>;
  }
  if (loading && !groups.some(g => g.items.length)) {
    return <div className={`${shell} px-4 py-3 text-[13px] text-muted-foreground`}>正在检索云空间文件…</div>;
  }
  if (!groups.some(g => g.items.length)) {
    const kw = (query || '').trim();
    return (
      <div className={`${shell} px-4 py-3 text-[13px] text-muted-foreground`}>
        <div>
          {kw ? `没有文件名含「${kw}」的可引用文件` : '还没有可 @ 引用的文件'}
          （归档资产与本会话工作区文件）
        </div>
        <div className="mt-1 text-[12px] opacity-80">
          产物会自动上云备份；要在别的会话复用某个产物，先在那条消息里「归档」，之后任何会话都能 @ 它。
        </div>
      </div>
    );
  }
  const active = groups[tab]?.items.length ? groups[tab] : groups.find(g => g.items.length)!;
  const Icon = ICONS[active.id];
  const navigable = groups.filter(g => g.items.length).length;
  return (
    <div className={shell}>
      <div role="tablist" aria-label="文件来源" className="flex items-center gap-6 border-b px-4 pt-2.5">
        {groups.map((g, i) => {
          const empty = !g.items.length;
          return (
            <button
              key={g.id}
              type="button"
              role="tab"
              aria-selected={i === tab && !empty}
              aria-disabled={empty || undefined}
              disabled={empty}
              onClick={() => onTabChange(i)}
              className={`relative pb-2 text-[13px] transition-colors ${
                empty ? 'cursor-not-allowed text-muted-foreground/45' : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              <span className={i === tab && !empty ? 'font-semibold text-foreground' : undefined}>
                {g.label}
                <span className="ml-1 text-[11px] text-muted-foreground/80">{g.items.length}</span>
              </span>
              {i === tab && !empty && (
                <span className="absolute bottom-1 left-1/2 h-1 w-1 -translate-x-1/2 rounded-full bg-foreground" />
              )}
            </button>
          );
        })}
      </div>
      <ScrollList activeIndex={activeIndex}>
        <div className="py-1">
          {active.items.map((item, i) => {
            const hint = itemHint(item);
            return (
              <button
                key={`${item.source}:${item.ref}`}
                type="button"
                data-active={i === activeIndex || undefined}
                onMouseEnter={() => onHover(i)}
                onClick={() => onPick(item)}
                className={`flex w-full items-center gap-2.5 px-4 py-2 text-left text-sm ${
                  i === activeIndex ? 'bg-primary/10 text-primary' : 'hover:bg-muted/60'
                }`}
              >
                <Icon className="h-4 w-4 shrink-0 opacity-70" />
                <span className="max-w-[46%] shrink-0 truncate font-semibold" title={item.filename}>
                  {item.name || item.filename}
                </span>
                <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{hint}</span>
                <span className="shrink-0 text-[11px] text-muted-foreground/80">{formatSize(item.size)}</span>
              </button>
            );
          })}
        </div>
      </ScrollList>
      <div className="flex items-center gap-5 border-t bg-card px-4 py-2 text-[11px] text-muted-foreground/90">
        <span className="flex items-center gap-1.5"><Key>↑↓</Key> 选择</span>
        {navigable > 1 && <span className="flex items-center gap-1.5"><Key>←→</Key> 切换来源</span>}
        <span className="flex items-center gap-1.5"><Key>Enter</Key> / <Key>Tab</Key> 引用</span>
        <span className="ml-auto flex items-center gap-1.5"><Key>Esc</Key> 退出</span>
      </div>
    </div>
  );
}
