// 智能问数输入框的「/」快捷检索面板：按类别分栏（最近使用 / 场景 / 技能 / 定时任务 / 指令），
// 常用的排前面；↑↓ 选条目、←→ 跳类别、Enter/Tab 添加、Esc 退出。
//
// 为什么要有它：用户明确知道自己要用哪个技能（或复用哪个定时任务的口径），
// 但只能靠把名字塞进问句里碰运气命中触发词。`/` 让选择变成显式动作，
// 服务端据此强制展开对应技能正文（prompt_composer._select_skill_bodies 规则 4），
// 不再靠猜。候选清单来自 GET /api/chat/slash-options（与运行时同一口径）。
//
// 为什么分栏而不是长列表平铺：技能/任务/指令混成一列时，用户要靠滚动和连按 ↓ 找目标，
// 常用的还被冷门的挤下去。分栏把「一类一屏」还给用户，使用度排序把「我常用的」顶到最前。
//
// 「场景」栏怎么有内容了：候选来自 GET /api/chat/slash-options 的 `scenes`（知识管理·分析场景），
// 服务端只下发“当前身份有权使用的”那些 —— 场景按所绑**主体**（本体对象）授权，主体不在角色
// 授权数据源覆盖范围内就不候选（与提示词注入同一判定）。因权限被隐去的条数由 `sceneNote`
// 如实告知，不能让人以为“没配场景”。空栏仍灰显不可点、不计入 ←→ 步数（不当死开关）。
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Sparkles, Clock, Terminal, Layers } from 'lucide-react';

export interface SlashSkillOption { name: string; display_name: string; description: string }
export interface SlashTaskOption { id: number; name: string; question: string; cron_expression: string }
/**
 * 指令候选：kind 区分两类语义（与服务端 slash_commands.COMMANDS 同源）。
 * - directive：翻译成本轮作业要求，仍走模型；
 * - action：**客户端会话动作**（如清空当前会话），选中就地执行，不进请求体也不下发 LLM。
 */
export interface SlashCommandOption {
  key: string; label: string; description: string;
  kind?: 'directive' | 'action'; action?: string;
}
/** 场景候选（知识管理·分析场景）：服务端已按主体权限过滤；key = 场景 id，选中后随请求上送。 */
export interface SlashSceneOption { key: string; name: string; description: string }
/** 快速入口（与服务端 slash_commands.MODES 同 key）：由输入卡片底行渲染，不进 `/` 分栏。 */
export interface SlashModeOption { key: string; label: string; description: string }
export interface SlashOptionsPayload {
  skills: SlashSkillOption[];
  tasks: SlashTaskOption[];
  commands: SlashCommandOption[];
  modes?: SlashModeOption[];
  scenes?: SlashSceneOption[];
  /** 因主体无权限/未绑主体而被隐去的场景数（要告诉用户，不静默少返） */
  scenes_hidden?: number;
}

/** 内容类别（场景/技能/定时任务/指令）；recent 是跨类别的「最近使用」视图，不是新能力。 */
export type SlashKind = 'scene' | 'skill' | 'task' | 'command';
export type SlashGroupId = 'recent' | SlashKind;

export type SlashItem =
  | { kind: 'scene'; id: string; label: string; description: string; scene: string }
  | { kind: 'skill'; id: string; label: string; description: string; name: string }
  | { kind: 'task'; id: string; label: string; description: string; question: string }
  | { kind: 'command'; id: string; label: string; description: string; command: string; action: string };

export interface SlashGroup { id: SlashGroupId; label: string; items: SlashItem[] }

const GROUP_LABEL: Record<SlashGroupId, string> = {
  recent: '最近使用', scene: '场景', skill: '技能', task: '定时任务', command: '指令',
};
/** 固定类别（无论有无候选都出栏位，用户看到的结构不随配置漂移）+ 最近使用打头。 */
const FIXED_GROUPS: SlashKind[] = ['scene', 'skill', 'task', 'command'];
const GROUP_ORDER: SlashGroupId[] = ['recent', 'scene', 'skill', 'task', 'command'];
/** 「最近使用」栏只给一眼看完的量：太长就失去"快捷"的意义。 */
const RECENT_LIMIT = 8;

/** 类别前缀别名：`/` 后先报一个类名就只列那一类（如 `/技能 数据`）。 */
const SCOPE_ALIASES: Record<string, SlashGroupId> = {
  recent: 'recent', 最近: 'recent', 最近使用: 'recent',
  scene: 'scene', scenes: 'scene', 场景: 'scene',
  skill: 'skill', skills: 'skill', 技能: 'skill',
  command: 'command', commands: 'command', 指令: 'command', 命令: 'command',
  task: 'task', tasks: 'task', 任务: 'task', 定时任务: 'task',
};
/** 底行与空态里给用户的用法提示（与 SCOPE_ALIASES 同源，不另写一份文案）。 */
export const SCOPE_HINT = '/场景 /技能 /任务 /指令';

/**
 * `/` 的等价触发符集合（均为单字符，故片段起点就是「触发符下标 + 1」）。
 *
 * 为什么要认三个：中文输入法的**中文标点模式**下按 `/` 键出的是顿号「、」（微软拼音/搜狗/QQ
 * 拼音皆如此），全角模式下是「／」。只认半角 `/` 就等于要求用户每次用快捷检索前先切一次
 * 输入法——这是本功能最常见的流失点，故三者同权。
 */
export const SLASH_TRIGGERS = ['/', '、', '／'];
/** 写给用户看的触发符写法（与 SLASH_TRIGGERS 同源，别处不得再各自硬编码 '/'）。 */
export const SLASH_TRIGGER_HINT = '/ 或 、';
/** 底行悬停说明：为什么要认顿号/全角斜杠（文案要短，原因给 title，不靠截断后的尾句）。 */
export const SLASH_TRIGGER_NOTE =
  '中文输入法（中文标点模式）下按 / 键出的是「、」、全角模式下是「／」，与半角 / 等价，无需切换输入法';

/** 光标前最近一个触发符的下标（-1 = 没有）。多个触发符取最靠后的，「最后输入的」才算本次检索。 */
function lastTriggerAt(before: string): number {
  return SLASH_TRIGGERS.reduce((acc, ch) => Math.max(acc, before.lastIndexOf(ch)), -1);
}

/** 拆检索串：`<类别> <关键字>` → 只在该类里按关键字过滤；单串先当类别，不是则当关键字。 */
export function parseSlashQuery(raw: string): { scope: SlashGroupId | null; keyword: string } {
  const s = (raw || '').trim().toLowerCase();
  if (!s) return { scope: null, keyword: '' };
  const sp = s.search(/\s/);
  const scope = SCOPE_ALIASES[sp < 0 ? s : s.slice(0, sp)];
  if (!scope) return { scope: null, keyword: s };
  return { scope, keyword: sp < 0 ? '' : s.slice(sp + 1).trim() };
}

/**
 * 取光标前正在输入的「触发符 + xxx」片段（无空白才算一次检索，否则视为普通文字）。
 *
 * 触发符前必须是行首或空白：URL（https://x）与正文里的顿号列举（「统计苹果、香蕉的销量」）
 * 都紧贴前文，不加这道门就是打字打出一个面板。要在句子中途起检索，先敲一个空格
 * （与半角 `/` 的历来口径一致）。
 */
export function slashQueryOf(value: string, caret: number): string | null {
  const before = value.slice(0, Math.max(0, caret));
  const at = lastTriggerAt(before);
  if (at < 0) return null;
  if (at > 0 && !/\s/.test(before[at - 1])) return null;
  const token = before.slice(at + 1);
  if (/\s/.test(token)) {
    // 只多允许一个空白：「类别 + 关键字」（如 `/技能 数据`）。其余带空白的仍不是检索
    const head = token.slice(0, token.search(/\s/)).trim().toLowerCase();
    if (!SCOPE_ALIASES[head]) return null;
  }
  return token.trim().toLowerCase();
}

/**
 * 摘掉输入框里正在检索的「触发符 + 检索词」片段（Esc 退出、选中后清理共用）。
 *
 * 必须与 slashQueryOf 同源于 SLASH_TRIGGERS：只认半角 `/` 的话，用「、」起的检索会把顿号
 * 残在正文里，下一个字符又把面板顶回来 —— 就是历史缺陷「Esc 按了没反应」的中文形态。
 */
export function stripSlashToken(value: string, caret: number): string {
  const before = value.slice(0, Math.max(0, caret));
  const at = lastTriggerAt(before);
  return at < 0 ? value : value.slice(0, at) + value.slice(Math.max(0, caret));
}

// ── 使用度（决定"最近使用"栏与常用置顶）──────────────────────────────
// 存在本地：它是"帮用户少按两下方向键"的排序辅助，不是需要跨端一致的业务事实，
// 也不进请求体（服务端只认用户显式选中的那一条）。
const USAGE_KEY = 'chat_slash_usage_v1';

export interface SlashUsageEntry { count: number; lastAt: number }
export type SlashUsage = Record<string, SlashUsageEntry>;

export function loadSlashUsage(): SlashUsage {
  try {
    const raw = localStorage.getItem(USAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const out: SlashUsage = {};
    Object.entries(parsed as Record<string, unknown>).forEach(([id, v]) => {
      const e = v as Partial<SlashUsageEntry>;
      if (e && typeof e.count === 'number' && typeof e.lastAt === 'number') {
        out[id] = { count: e.count, lastAt: e.lastAt };
      }
    });
    return out;
  } catch {
    // 读不到使用度只是退回默认排序（清单本身照旧可用），不是功能失败
    return {};
  }
}

/** 记一次选择：次数用于组内置顶，时间用于「最近使用」栏排序。 */
export function recordSlashUsage(id: string) {
  if (!id) return;
  try {
    const usage = loadSlashUsage();
    const prev = usage[id];
    usage[id] = { count: (prev?.count || 0) + 1, lastAt: Date.now() };
    // 只保留最近 50 条：技能/任务会随治理增删，排序用不到更老的历史
    const keep = Object.entries(usage)
      .sort((a, b) => b[1].lastAt - a[1].lastAt)
      .slice(0, 50);
    localStorage.setItem(USAGE_KEY, JSON.stringify(Object.fromEntries(keep)));
  } catch {
    // 隐私模式/配额写不进去：本次选择照样生效，只是下次打开面板没有置顶
  }
}

/** 各类别内的排序：常用的顶到前面，没用过的保持服务端原序（稳定比较，不打乱治理顺序）。 */
function sortByUsage(items: SlashItem[], usage: SlashUsage): SlashItem[] {
  return items
    .map((item, i) => ({ item, i, count: usage[item.id]?.count || 0, lastAt: usage[item.id]?.lastAt || 0 }))
    .sort((a, b) => b.count - a.count || b.lastAt - a.lastAt || a.i - b.i)
    .map(e => e.item);
}

/**
 * 按检索词分组：`<类别>` 只出那一栏，`<类别> <关键字>` 在该类里收窄，单串当关键字跨类检索。
 * 固定类别**包空也出栏**（用户看到的结构不随配置漂移），但空栏灰显不可点、不进 ←→ 步数；
 * 「最近使用」是派生视图，没历史就不占位。
 */
export function buildSlashGroups(
  options: SlashOptionsPayload | null,
  query: string,
  usage: SlashUsage = {},
): SlashGroup[] {
  if (!options) return [];
  const { scope, keyword: q } = parseSlashQuery(query);
  const hit = (...texts: string[]) => !q || texts.some(t => (t || '').toLowerCase().includes(q));

  // 场景行 id 用 key，但 key 缺失时退回业务名：同组撞 React key 会渲染出重复行，
  // 且那些多出来的节点 Esc 也关不掉（reconciliation 认不了它们）——历史缺陷。
  // 真正的标识仍取 s.key（拿不到就在选中时显式拒，不假装选上了）。
  const scene: SlashItem[] = (options.scenes || []).filter(s => hit(s.name, s.key, s.description))
    .map(s => ({ kind: 'scene' as const, id: `scene:${s.key || s.name}`, label: s.name,
                 description: s.description, scene: s.key }));
  const skill: SlashItem[] = options.skills.filter(s => hit(s.display_name, s.name, s.description))
    .map(s => ({ kind: 'skill' as const, id: `skill:${s.name}`, label: s.display_name || s.name,
                 description: s.description, name: s.name }));
  const task: SlashItem[] = options.tasks.filter(t => hit(t.name, t.question))
    .map(t => ({ kind: 'task' as const, id: `task:${t.id}`, label: t.name,
                 description: t.question, question: t.question }));
  const command: SlashItem[] = options.commands.filter(c => hit(c.label, c.key, c.description))
    .map(c => ({ kind: 'command' as const, id: `command:${c.key}`, label: c.label,
                 description: c.description, command: c.key, action: c.action || '' }));

  const byKind: Record<SlashKind, SlashItem[]> = { scene, skill, task, command };
  const pool = new Map<string, SlashItem>();
  (Object.keys(byKind) as SlashKind[]).forEach(k => byKind[k].forEach(it => pool.set(it.id, it)));

  const recent: SlashItem[] = Object.keys(usage)
    .sort((a, b) => (usage[b].lastAt - usage[a].lastAt) || (a < b ? -1 : 1))
    .map(id => pool.get(id))
    .filter((it): it is SlashItem => !!it)
    .slice(0, RECENT_LIMIT);

  const groups: SlashGroup[] = [];
  if (recent.length) groups.push({ id: 'recent', label: GROUP_LABEL.recent, items: recent });
  // 固定栏位无论有无候选都出栏（空栏由组件灰显），避免“配了才多一个标签”的结构漂移
  FIXED_GROUPS.forEach(k => groups.push({ id: k, label: GROUP_LABEL[k], items: sortByUsage(byKind[k], usage) }));
  const shown = scope ? groups.filter(g => g.id === scope) : groups;
  return GROUP_ORDER
    .map(id => shown.find(g => g.id === id))
    .filter((g): g is SlashGroup => !!g);
}

const ICONS: Record<SlashKind, typeof Sparkles> = {
  scene: Layers, skill: Sparkles, task: Clock, command: Terminal,
};

/**
 * 面板的导航状态：当前类别 + 类内高亮行。
 *
 * 状态收在这里而不是散在页面里，是因为 ←→/↑↓ 都要改它，且检索词一变
 * 类别可能消失（如 `/技能` 只剩一栏）——夹回有效范围必须由同一处负责。
 * 空栏（如还没接数据源的「场景」）不参与导航：按 ←→ 不会停在一个没有内容的栏上。
 */
export function useSlashMenu(options: SlashOptionsPayload | null, query: string | null) {
  const [tab, setTab] = useState(0);
  const [row, setRow] = useState(0);
  // 使用度随面板打开重算（选中即关闭，下次打开就能看到新的置顶与「最近使用」）
  const groups = useMemo(
    () => buildSlashGroups(options, query ?? '', loadSlashUsage()),
    [options, query],
  );
  /** 可导航的栏位下标（有候选才算），空栏只占位不可达 */
  const enabled = groups
    .map((g, i) => (g.items.length ? i : -1))
    .filter((i) => i >= 0);
  const tabIdx = enabled.includes(tab) ? tab : (enabled[0] ?? 0);
  const items = groups[tabIdx]?.items ?? [];
  const rowIdx = items.length ? Math.min(row, items.length - 1) : 0;

  // 检索词变了 = 清单重算：高亮归回首条；面板收起时类别也回到首栏（下次打开从「最近使用」开始）
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
    if (!groups[index]?.items.length) return;   // 灰显的空栏点不动（不给按了没反应的哑标签）
    setTab(index);
    setRow(0);
  };
  const current = (): SlashItem | null => items[rowIdx] ?? null;

  return { groups, enabled, tab: tabIdx, row: rowIdx, items, setRow, moveRow, moveTab, gotoTab, current };
}

/**
 * 候选清单的滚动区。
 *
 * 限高与滚动都落在原生 div 上，不用 Radix ScrollArea：它的 Root 自带 inline
 * `position: relative`（会把 className 里的 absolute 掉回静态流），且 Root 只有
 * max-height 时 Viewport 的 `h-full` 解析不出确定高度——两个坑叠加就是历史现象：
 * 面板铺到整屏、盖住输入卡片与推荐问题，下面的候选根本点不到。
 *
 * 另外负责键盘导航：↑↓ 走到清单末尾时，高亮项必须自己滚进视口，
 * 否则“选中了一个看不见的条目”。
 */
function ScrollList({ activeIndex, children }: { activeIndex: number; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current?.querySelector('[data-active]');
    // 可选调用：jsdom 不实现 scrollIntoView，不能让它把用例抱崩
    el?.scrollIntoView?.({ block: 'nearest' });
  }, [activeIndex]);
  return <div ref={ref} className="max-h-[240px] min-h-[116px] overflow-y-auto">{children}</div>;
}

/** 底行键位提示的小键帽（与参考交互同构：把快捷键当面包展示，而不是塞进一句话）。 */
function Key({ children }: { children: ReactNode }) {
  return (
    <span className="rounded border border-border/70 bg-muted/60 px-1 py-0.5 font-mono text-[10px] leading-none text-muted-foreground">
      {children}
    </span>
  );
}

export default function SlashCommandMenu({ groups, tab, activeIndex, onHover, onTabChange, onPick, error, query, sceneNote }: {
  groups: SlashGroup[];
  /** 当前类别下标（←→ 由调用方翻页，高亮由本组件的 tab 状态驱动） */
  tab: number;
  activeIndex: number;
  onHover: (index: number) => void;
  onTabChange: (index: number) => void;
  onPick: (item: SlashItem) => void;
  error?: string;
  /** 当前检索串（包类别前缀），用于空态把话说完整：哪一类里没找到什么 */
  query?: string;
  /** 场景栏的补充说明（如“另有 N 个场景因主体无权限未列出”），不静默少返 */
  sceneNote?: string;
}) {
  // 定位壳只负责锚位（卡片正上方、与卡片同宽）；限高交给 ScrollList，
  // 顶部类别栏与底部提示行固定在滚动区外，候选再长也看不丢这条生命线。
  const shell = 'absolute bottom-full left-0 right-0 z-30 mb-2 rounded-lg border bg-card shadow-lg';
  if (error) {
    // 候选加载失败必须可见并可重试，不得静默当成“没有技能可用”
    return <div className={`${shell} px-3.5 py-2.5 text-xs text-destructive`}>{error}</div>;
  }
  if (!groups.length || groups.every(g => !g.items.length)) {
    // 固定栏位照出，但一栏内容都没有时给一行话，而不是只留一排灰标签
    const { scope, keyword } = parseSlashQuery(query || '');
    const where = scope ? `${GROUP_LABEL[scope]}类里` : '';
    return (
      <div className={`${shell} px-3.5 py-2.5 text-xs text-muted-foreground`}>
        {where}没有匹配{keyword ? `「${keyword}」` : ''}的候选
      </div>
    );
  }
  const active = groups[tab]?.items.length ? groups[tab] : groups.find(g => g.items.length)!;
  const navigable = groups.filter(g => g.items.length).length;
  return (
    <div className={shell}>
      <div role="tablist" aria-label="候选类别" className="flex items-center gap-5 border-b px-3.5 pt-2">
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
              title={empty ? `${g.label}暂无可用候选` : undefined}
              onClick={() => onTabChange(i)}
              className={`relative pb-1.5 text-xs transition-colors ${
                empty ? 'cursor-not-allowed text-muted-foreground/45' : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              <span className={i === tab && !empty ? 'font-semibold text-foreground' : undefined}>
                {g.label}
              </span>
              {i === tab && !empty && (
                <span className="absolute bottom-1 left-1/2 h-1 w-1 -translate-x-1/2 rounded-full bg-foreground" />
              )}
            </button>
          );
        })}
      </div>
      <ScrollList activeIndex={activeIndex}>
        <div className="py-0.5">
          {active.id === 'scene' && sceneNote && (
            <p className="px-3.5 pb-1 text-[11px] text-muted-foreground/90">{sceneNote}</p>
          )}
          {active.items.map((item, i) => {
            const Icon = ICONS[item.kind];
            return (
              <button
                key={item.id}
                type="button"
                data-active={i === activeIndex || undefined}
                onMouseEnter={() => onHover(i)}
                onClick={() => onPick(item)}
                className={`flex w-full items-center gap-2 px-3.5 py-1.5 text-left text-[13px] ${
                  i === activeIndex ? 'bg-primary/10 text-primary' : 'hover:bg-muted/60'
                }`}
              >
                <Icon className="h-3.5 w-3.5 shrink-0 opacity-70" />
                <span className="max-w-[42%] shrink-0 truncate font-semibold">{item.label}</span>
                {item.description && (
                  <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">{item.description}</span>
                )}
              </button>
            );
          })}
        </div>
      </ScrollList>
      <div className="flex items-center gap-4 border-t bg-card px-3.5 py-1.5 text-[11px] text-muted-foreground/90">
        <span className="flex items-center gap-1.5"><Key>↑↓</Key> 选择</span>
        {navigable > 1 && <span className="flex items-center gap-1.5"><Key>←→</Key> 切换类别</span>}
        <span className="flex items-center gap-1.5"><Key>Enter</Key> / <Key>Tab</Key> 添加</span>
        <span className="ml-auto flex items-center gap-1.5"><Key>Esc</Key> 退出</span>
      </div>
    </div>
  );
}
