// 智能问数输入框「/」快捷检索的契约：什么时候弹、分几栏、每栏给什么、选中后拿到什么。
//
// 这里锁三件事：
// * 候选与用户意图对齐 —— URL 里的斜杠不能把菜单顶出来，技能/定时任务/指令都能被检索到，
//   而底行已经是常驻入口的数据分析/知识问答不得在面板里再开一栏（一个能力两个入口）；
// * 分栏 + 使用度排序 —— 空栏不进标签（切过去一片空白=空承诺），常用的顶到第一屏，
//   「最近使用」只列现在还能选到的项（下架的技能不留幽灵条目）；
// * 键盘导航口径 —— ←→ 切类别、↑↓ 类内循环、Enter/Tab 添加当前高亮项。
import { render, screen, fireEvent } from '@testing-library/react';
import { renderHook, act } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SlashCommandMenu, {
  buildSlashGroups, recordSlashUsage, loadSlashUsage, useSlashMenu, slashQueryOf, stripSlashToken,
  SLASH_TRIGGERS,
  type SlashItem, type SlashOptionsPayload,
} from '../SlashCommandMenu';

const OPTIONS: SlashOptionsPayload = {
  modes: [
    { key: 'analysis', label: '数据分析', description: '按业务本体与语义层取数并给出结论（默认主路）' },
    { key: 'knowledge', label: '知识问答', description: '只依据知识库回答定义/口径/规范，不产生数据行' },
  ],
  skills: [
    { name: 'data-analysis', display_name: '数据分析师', description: '语义层取数剧本' },
    { name: 'arms-observability', display_name: '观测域分析', description: '流量与 URL 拆解' },
  ],
  tasks: [
    { id: 12, name: '每日接口请求量异常检测', question: '统计昨天的接口请求量并标出异常', cron_expression: '0 9 * * *' },
  ],
  commands: [
    { key: 'create_scheduled_task', label: '创建定时任务', description: '把本轮问题创建为周期执行的任务' },
  ],
};

const groupsOf = (query: string, usage = loadSlashUsage()) => buildSlashGroups(OPTIONS, query, usage);
const flatIds = (query: string) => groupsOf(query).flatMap(g => g.items.map(i => i.id));

beforeEach(() => localStorage.clear());

describe('slashQueryOf — 什么时候算一次检索', () => {
  it('行首的 / 及其后文本算检索词', () => {
    expect(slashQueryOf('/数据分析', 6)).toBe('数据分析');
    expect(slashQueryOf('/', 1)).toBe('');
  });

  it('空白之后的 / 也算（用户可以先打字再补指令）', () => {
    expect(slashQueryOf('帮我 /arms', 8)).toBe('arms');
  });

  it('URL 里的斜杠不得顶出菜单', () => {
    expect(slashQueryOf('https://example.com/data', 21)).toBeNull();
  });

  it('检索词里出现空白即结束（不再当检索）', () => {
    expect(slashQueryOf('/数据 分析', 6)).toBeNull();
  });

  it('光标之后的文本不参与判定', () => {
    expect(slashQueryOf('abc/def', 3)).toBeNull();
  });

  // 中文输入法（微软拼音/搜狗等）的中文标点模式下按 `/` 键出的是顿号「、」，全角模式是「／」。
  // 只认半角 `/` 就等于逼迫用户每次开面板前先切一次输入法——这是本功能最日常的用法障碍。
  it('中文输入法下的「、」与全角「／」同样顶出面板（不必切输入法）', () => {
    expect(slashQueryOf('、数据分析', 5)).toBe('数据分析');
    expect(slashQueryOf('、', 1)).toBe('');
    expect(slashQueryOf('／技能 数据', 6)).toBe('技能 数据');
    expect(SLASH_TRIGGERS).toEqual(['/', '、', '／']);
  });

  // 顿号在中文正文里太常见（「苹果、香蕉」），因此与半角 `/` 同守「行首或空白后」这道门；
  // 中途起检索先敲空格，与 `/` 的历来口径一致。
  it('正文里紧挨着前文的顿号不得顶出面板', () => {
    expect(slashQueryOf('统计苹果、香蕉的销量', 10)).toBeNull();
    expect(slashQueryOf('帮我 、技能', 6)).toBe('技能');
  });
});

describe('stripSlashToken — 摘掉检索片段（Esc 与选中后清理）', () => {
  it('三种触发符都摘得掉，前面的正文不动', () => {
    expect(stripSlashToken('帮我 /技能 数', 8)).toBe('帮我 ');
    expect(stripSlashToken('、数据分析', 5)).toBe('');
    expect(stripSlashToken('帮我 、技能', 6)).toBe('帮我 ');
    expect(stripSlashToken('没有触发符', 5)).toBe('没有触发符');
  });
});

describe('buildSlashGroups — 分栏与过滤', () => {
  it('空检索词出固定四栏（场景包空也在），没用过就不出「最近使用」', () => {
    expect(groupsOf('').map(g => g.id)).toEqual(['scene', 'skill', 'task', 'command']);
    // 固定栏位不随配置漂移：没接数据源时结构照旧，空不空由 items 长度决定（组件灰显）
    expect(groupsOf('').find(g => g.id === 'scene')?.items).toEqual([]);
  });

  it('数据分析/知识问答不进 `/` 面板（底行已有常驻入口，再列一遍=两个选中态口径）', () => {
    expect(groupsOf('').map(g => g.id)).not.toContain('mode');
    expect(flatIds('')).not.toContain('mode:knowledge');
    // 「场景」不是底行那两个入口的别名：它是自己的栏，现在为空
    expect(groupsOf('场景').find(g => g.id === 'scene')?.items).toEqual([]);
  });

  it('关键字跳类命中（显示名/内部名/描述/任务正文任一）', () => {
    expect(flatIds('数据分析师')).toEqual(['skill:data-analysis']);
    expect(flatIds('arms')).toEqual(['skill:arms-observability']);
    expect(flatIds('接口请求量')).toEqual(['task:12']);
    expect(flatIds('周期执行')).toEqual(['command:create_scheduled_task']);
  });

  it('单写类别名只出那一栏（中英文别名同口径）', () => {
    expect(groupsOf('技能').map(g => g.id)).toEqual(['skill']);
    expect(groupsOf('skill').map(g => g.id)).toEqual(['skill']);
    expect(groupsOf('任务').map(g => g.id)).toEqual(['task']);
    expect(groupsOf('指令').map(g => g.id)).toEqual(['command']);
    expect(groupsOf('场景').map(g => g.id)).toEqual(['scene']);
  });

  it('类别 + 关键字在该类里继续收窄，不跨类串味', () => {
    expect(flatIds('技能 arms')).toEqual(['skill:arms-observability']);
    expect(flatIds('任务 接口请求量')).toEqual(['task:12']);
    expect(flatIds('任务 数据分析师')).toEqual([]);
  });

  it('未识别为类别的词照旧当关键字跨类检索（不静默清空）', () => {
    expect(flatIds('定时')).toEqual(['command:create_scheduled_task']);
  });

  it('关键字谁都没命中：固定栏位照出但都是空的（组件给一行空态话，不只留一排灰标签）', () => {
    const groups = groupsOf('zzz');
    expect(groups.map(g => g.id)).toEqual(['scene', 'skill', 'task', 'command']);
    expect(groups.every(g => g.items.length === 0)).toBe(true);
    expect(buildSlashGroups(null, '')).toEqual([]);
  });

  it('任务选中拿到的是可编辑问题正文，不是内部 id', () => {
    const item = buildSlashGroups(OPTIONS, '接口请求量').flatMap(g => g.items)[0] as Extract<SlashItem, { kind: 'task' }>;
    expect(item.kind).toBe('task');
    expect(item.label).toBe('每日接口请求量异常检测');
    expect(item.question).toContain('统计昨天的接口请求量');
  });
});

describe('使用度 — 常用置顶与「最近使用」栏', () => {
  it('用过的排到本栏最前，没用过的保持服务端原序', () => {
    recordSlashUsage('skill:arms-observability');
    expect(groupsOf('技能').flatMap(g => g.items.map(i => i.id)))
      .toEqual(['skill:arms-observability', 'skill:data-analysis']);
  });

  it('「最近使用」按最近使用时间倒序，且只列现在还能选到的项', () => {
    recordSlashUsage('skill:data-analysis');
    recordSlashUsage('command:create_scheduled_task');
    const ids = groupsOf('').flatMap(g => g.items.map(i => i.id));
    // 最近使用栏在最前，内容是最后一次选中的指令；技能项随后仍出现在自己的栏里
    expect(ids.slice(0, 1)).toEqual(['command:create_scheduled_task']);
    expect(groupsOf('').map(g => g.id)).toEqual(['recent', 'scene', 'skill', 'task', 'command']);
    // 已下架的候选不进「最近使用」（不留选了没反应的幽灵条目）
    const ghost = buildSlashGroups(OPTIONS, '', {
      'skill:retired': { count: 5, lastAt: Date.now() },
      'task:12': { count: 1, lastAt: Date.now() - 1 },
    });
    expect(ghost.find(g => g.id === 'recent')?.items.map(i => i.id)).toEqual(['task:12']);
  });

  it('落本地的是次数与时间（次数用于置顶，时间用于最近使用）', () => {
    recordSlashUsage('skill:data-analysis');
    recordSlashUsage('skill:data-analysis');
    expect(loadSlashUsage()['skill:data-analysis'].count).toBe(2);
  });
});

describe('useSlashMenu — 类别与行高亮', () => {
  it('←→ 只跳有内容的栏，不会停在灰显的空栏上', () => {
    const { result } = renderHook(() => useSlashMenu(OPTIONS, ''));
    // 「场景」是第一个栏位但没候选，因此初始高亮落在首个有内容的栏（技能）
    expect(result.current.groups[0].id).toBe('scene');
    expect(result.current.tab).toBe(1);
    expect(result.current.enabled).toEqual([1, 2, 3]);
    act(() => result.current.moveTab(1));
    expect(result.current.groups[result.current.tab].id).toBe('task');
    act(() => result.current.moveTab(1));
    expect(result.current.groups[result.current.tab].id).toBe('command');
    // 循环回来仍然跳过空栏
    act(() => result.current.moveTab(1));
    expect(result.current.groups[result.current.tab].id).toBe('skill');
    // gotoTab 对空栏无效（灰显标签不是死开关）
    act(() => result.current.gotoTab(0));
    expect(result.current.tab).toBe(1);
  });

  it('←→ 跳类别、↑↓ 类内循环，current() 给出当前高亮条目', () => {
    const { result } = renderHook(() => useSlashMenu(OPTIONS, ''));
    expect(result.current.tab).toBe(1);   // 首栏「场景」为空，高亮从技能开始
    expect(result.current.current()?.kind).toBe('skill');
    act(() => result.current.moveRow(1));
    expect(result.current.current()?.id).toBe('skill:arms-observability');
    // 类内循环：从末条再往下回到首条（不越界、不停在看不见的条目上）
    act(() => result.current.moveRow(1));
    expect(result.current.current()?.id).toBe('skill:data-analysis');
    // 切栏：行高亮归首条；反向切栏跳过空栏回到末栏（循环）
    act(() => result.current.moveTab(1));
    expect(result.current.groups[result.current.tab].id).toBe('task');
    expect(result.current.row).toBe(0);
    act(() => result.current.moveTab(-2));
    expect(result.current.groups[result.current.tab].id).toBe('command');
  });

  it('检索词变了：高亮归回首条；只剩一栏时切栏不越界', () => {
    const { result, rerender } = renderHook(
      ({ query }: { query: string | null }) => useSlashMenu(OPTIONS, query),
      { initialProps: { query: '' as string | null } },
    );
    act(() => { result.current.moveRow(1); });
    expect(result.current.row).toBe(1);
    // 切到只有一条的栏（定时任务）：行高亮归首条，不指到看不见的条目上
    act(() => { result.current.moveTab(1); });
    expect(result.current.row).toBe(0);
    rerender({ query: '技能' });
    act(() => result.current.moveTab(1));
    expect(result.current.groups).toHaveLength(1);
    expect(result.current.tab).toBe(0);
    expect(result.current.row).toBe(0);
  });

  it('无候选时 current() 返回 null（由调用方把面板收起，不把半截过滤串发出去）', () => {
    const { result } = renderHook(() => useSlashMenu(OPTIONS, 'zzz'));
    expect(result.current.current()).toBeNull();
  });
});

describe('SlashCommandMenu — 渲染与交互', () => {
  const groups = buildSlashGroups(OPTIONS, '', {});

  const renderMenu = (props: Partial<ComponentProps<typeof SlashCommandMenu>> = {}) => render(
    <SlashCommandMenu
      groups={groups}
      tab={0}
      activeIndex={0}
      onHover={() => {}}
      onTabChange={() => {}}
      onPick={() => {}}
      {...props}
    />,
  );

  it('顶部是类别标签（场景包空也出栏但灰显），一栏一屏且不把裸 id 当名字', () => {
    renderMenu();
    ['场景', '技能', '定时任务', '指令'].forEach(label =>
      expect(screen.getByRole('tab', { name: label })).toBeInTheDocument());
    // 空栏不可点（不是按了没反应的死开关）
    expect(screen.getByRole('tab', { name: '场景' })).toBeDisabled();
    // 默认高亮落在首个有内容的栏（技能）：其余栏的条目不在 DOM 里，靠切栏而不是滚动去看
    expect(screen.getByText('数据分析师')).toBeInTheDocument();
    expect(screen.queryByText('创建定时任务')).not.toBeInTheDocument();
    expect(screen.queryByText('skill:data-analysis')).not.toBeInTheDocument();
    // 底行已有的快速入口不得在面板里重现（同一能力两个入口）
    expect(screen.queryByText('知识问答')).not.toBeInTheDocument();
    expect(screen.queryByText('数据分析')).not.toBeInTheDocument();
  });

  it('点灰显的空栏不会切过去（不产生“选中了一个没内容的栏”）', () => {
    const onTabChange = vi.fn();
    renderMenu({ onTabChange });
    fireEvent.click(screen.getByRole('tab', { name: '场景' }));
    expect(onTabChange).not.toHaveBeenCalled();
  });

  it('切到指令栏给出指令名与说明（name 为主，描述次要）', () => {
    renderMenu({ tab: 3 });
    expect(screen.getByText('创建定时任务')).toBeInTheDocument();
    expect(screen.getByText('把本轮问题创建为周期执行的任务')).toBeInTheDocument();
    expect(screen.queryByText('数据分析师')).not.toBeInTheDocument();
  });

  it('点击标签切类别，点击候选回传该条目', () => {
    const onTabChange = vi.fn();
    const onPick = vi.fn();
    renderMenu({ tab: 1, onTabChange, onPick });
    fireEvent.click(screen.getByRole('tab', { name: '指令' }));
    expect(onTabChange).toHaveBeenCalledWith(3);
    fireEvent.click(screen.getByText('数据分析师'));
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ kind: 'skill', name: 'data-analysis' }));
  });

  it('键位提示行齐备且在滚动区外（候选再长也不会被滚走）', () => {
    renderMenu();
    ['↑↓', '←→', 'Enter', 'Tab', 'Esc'].forEach(k =>
      expect(screen.getByText(k)).toBeInTheDocument());
    const hintRow = screen.getByText('Esc').closest('.border-t');
    expect(hintRow).not.toBeNull();
    expect(hintRow!.closest('.overflow-y-auto')).toBeNull();
  });

  it('只有一栏时不提示切类别（不给按了没反应的哑键）', () => {
    renderMenu({ groups: groups.filter(g => g.id === 'skill') });
    expect(screen.queryByText('←→')).not.toBeInTheDocument();
  });

  it('候选清单必须限高可滚，而不是全部铺开盖住输入区', () => {
    // 历史缺陷：限高写在 Radix ScrollArea 上不起作用（Root 只有 max-height 时
    // Viewport 的 h-full 解析不出确定高度），面板铺到整屏、卡片被盖住。
    renderMenu({ tab: 0, activeIndex: 1 });
    const list = screen.getByText('数据分析师').closest('.overflow-y-auto');
    expect(list).not.toBeNull();
    // 只锁“有限高且给足一屏阅读区”（具体数值随字号调档，不把它钉死）
    expect(list!.className).toMatch(/max-h-\[\d{3}px\]/);
    expect(list!.className).toMatch(/min-h-\[\d{3}px\]/);
    // 高亮条目带标记（技能栏第二条：观测域分析），供键盘导航把它滚进视口
    expect(screen.getByText('观测域分析').closest('[data-active]')).not.toBeNull();
    expect(screen.getByText('数据分析师').closest('[data-active]')).toBeNull();
  });

  it('加载失败显示错误而不是空清单（不得静默当成没有技能）', () => {
    renderMenu({ groups: [], error: '技能/任务清单加载失败' });
    expect(screen.getByText('技能/任务清单加载失败')).toBeInTheDocument();
  });

  it('空态说清是哪一类里没找到什么', () => {
    renderMenu({ groups: [], query: '技能 zzz' });
    expect(screen.getByText(/技能类里没有匹配「zzz」的候选/)).toBeInTheDocument();
  });

  it('关键字全部不命中：给一行空态话，而不是只留一排灰标签', () => {
    renderMenu({ groups: buildSlashGroups(OPTIONS, 'zzz', {}), query: 'zzz' });
    expect(screen.getByText(/没有匹配「zzz」的候选/)).toBeInTheDocument();
    expect(screen.queryAllByRole('tab')).toHaveLength(0);
  });
});
