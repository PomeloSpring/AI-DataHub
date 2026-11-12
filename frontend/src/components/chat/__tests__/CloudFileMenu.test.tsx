// 聊天框「@」文件引用面板的契约：什么时候算一次检索、候选分几栏、选中后拿到什么。
//
// 锁四件事：
// * **口径**：候选只有归档资产（账号级跨工作空间）与本会话工作区文件——自动上云的副本
//   是备份层，不得被摊成"可 @ 的清单"（产品决策 2026-11-04：上云=备份、归档=资产）；
// * **不给假选项**：本轮已引用过的条目不再出现在候选里；归档与本会话里的同名文件是两个
//   独立对象（云端 key 不同），各自留在自己栏，不做跨栏名去重（去重=让"本地那份"凭空消失）；
// * **显示用 name**：条目展示文件名与来源空间名，不显裸 id（ui-resource-display §1）；
// * **失败可见**：检索报错不能让面板静默空掉（用户会误以为"我没有可引用的文件"）。
import { fireEvent, render, renderHook, screen, act } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import CloudFileMenu, {
  buildFileGroups, formatSize, mentionQueryOf, stripMentionToken, useCloudFileMenu,
  type CloudFileItem, type CloudFilesPayload,
} from '../CloudFileMenu';

const PAYLOAD: CloudFilesPayload = {
  assets: [
    { source: 'asset', ref: 'a1', filename: '月度营收报告.xlsx', name: '月度营收报告', size: 20480, origin_workspace_name: '全国数据' },
    { source: 'asset', ref: 'a2', filename: '留存曲线.png', name: '留存曲线', size: 512 * 1024 },
  ],
  session_files: [
    { source: 'session', ref: 'uploads/销售明细.csv', filename: '销售明细.csv', category: 'table', size: 1024 },
    { source: 'session', ref: '月度营收报告.xlsx', filename: '月度营收报告.xlsx', category: 'table', size: 20480 },
  ],
};

const itemOf = (ref: string): CloudFileItem | undefined =>
  [...(PAYLOAD.assets || []), ...(PAYLOAD.session_files || [])].find(i => i.ref === ref);

describe('mentionQueryOf / stripMentionToken — 什么时候算一次检索', () => {
  it('行首与中文紧挨着的 @ 都算检索（中文没有分词空格）', () => {
    expect(mentionQueryOf('@销售', 3)).toBe('销售');
    expect(mentionQueryOf('帮我看看@销售', 7)).toBe('销售');
    expect(mentionQueryOf('@', 1)).toBe('');
  });

  it('@ 后出现空白就不是检索了（半截问句不该顶出面板）', () => {
    expect(mentionQueryOf('@销售 的数据', 6)).toBeNull();
    expect(mentionQueryOf('联系 a@b.com 谢谢', 13)).toBeNull();
  });

  it('只取光标前的片段，光标之后的正文不参与判定', () => {
    expect(mentionQueryOf('@月', 2)).toBe('月');
    expect(mentionQueryOf('@月', 1)).toBe('');
  });

  it('Esc 摘掉 @xxx 片段但保留其余正文', () => {
    expect(stripMentionToken('帮我分析@月度 吧', 7)).toBe('帮我分析 吧');
    expect(stripMentionToken('没有at符号', 6)).toBe('没有at符号');
  });

  // 中文输入法（中文标点模式）下 Shift+2 出的是全角「＠」：不认它就等于让用户先切输入法，
  // 而摘不干净则会把 ＠ 留在正文里反复顶面板（与「/」面板同一条约束）。
  it('中文输入法下的全角「＠」同样算检索、同样摘得掉', () => {
    expect(mentionQueryOf('＠销售', 3)).toBe('销售');
    expect(mentionQueryOf('帮我看看＠月度', 8)).toBe('月度');
    expect(stripMentionToken('帮我分析＠月度 吧', 7)).toBe('帮我分析 吧');
  });
});

describe('buildFileGroups — 候选结构与去重', () => {
  it('两栏按来源分栏，本会话同名产物留在自己栏', () => {
    const groups = buildFileGroups(PAYLOAD, []);
    expect(groups.map(g => g.id)).toEqual(['asset', 'session']);
    expect(groups[0].items.map(i => i.ref)).toEqual(['a1', 'a2']);
    expect(groups[1].items.map(i => i.ref)).toEqual(['uploads/销售明细.csv', '月度营收报告.xlsx']);
    expect(groups[1].label).toBe('本会话文件');
  });

  it('已引用的文件不再出现在候选里（同一凭据不能被 @ 两次）', () => {
    const picked = [itemOf('a1')!, itemOf('uploads/销售明细.csv')!];
    const groups = buildFileGroups(PAYLOAD, picked);
    expect(groups[0].items.map(i => i.ref)).toEqual(['a2']);
    expect(groups[1].items.map(i => i.ref)).toEqual(['月度营收报告.xlsx']);
  });

  it('大小按人类可读单位显示（不铺裸字节）', () => {
    expect(formatSize(512)).toBe('512 B');
    expect(formatSize(20480)).toBe('20 KB');
    expect(formatSize(512 * 1024)).toBe('512 KB');
    expect(formatSize(0)).toBe('');
  });
});

describe('useCloudFileMenu — 键盘导航口径', () => {
  it('↑↓ 在栏内循环，←→ 只在有两栏时跳栏', () => {
    const { result } = renderHook(() => useCloudFileMenu(PAYLOAD, '', []));
    expect(result.current.items).toHaveLength(2);
    act(() => result.current.moveRow(1));
    expect(result.current.current()?.ref).toBe('a2');
    act(() => result.current.moveRow(1));
    expect(result.current.row).toBe(0);            // 末尾回绕，不是卡在最后一条

    act(() => result.current.moveTab(1));
    expect(result.current.tab).toBe(1);
    expect(result.current.current()?.ref).toBe('uploads/销售明细.csv');
  });

  it('空栏不可进入（切过去只剩灰标签=空承诺），检索词变了高亮回首条', () => {
    const onlyAssets: CloudFilesPayload = { assets: PAYLOAD.assets, session_files: [] };
    const { result, rerender } = renderHook(
      ({ q }: { q: string | null }) => useCloudFileMenu(onlyAssets, q, []),
      { initialProps: { q: '' as string | null } },
    );
    act(() => { result.current.setRow(1); result.current.moveTab(1); });
    expect(result.current.tab).toBe(0);            // 会话栏是空的，跳不过去

    rerender({ q: '月' });
    expect(result.current.row).toBe(0);
  });
});

describe('CloudFileMenu 面板', () => {
  const noop = vi.fn();
  const props = {
    groups: buildFileGroups(PAYLOAD, []),
    tab: 0,
    activeIndex: 0,
    onHover: noop,
    onTabChange: noop,
    onPick: noop,
    query: '',
  };

  it('候选显示文件名与来源空间名，不显示裸 id', () => {
    render(<CloudFileMenu {...props} />);
    expect(screen.getByText('月度营收报告')).toBeInTheDocument();
    expect(screen.getByText('来自「全国数据」')).toBeInTheDocument();
    expect(screen.queryByText('a1')).not.toBeInTheDocument();
  });

  it('点击候选即引用该文件（拿到的是引用凭据，不是文件名）', () => {
    const onPick = vi.fn();
    render(<CloudFileMenu {...props} onPick={onPick} />);
    fireEvent.click(screen.getByText('月度营收报告'));
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ source: 'asset', ref: 'a1' }));
  });

  it('无候选时把"怎么才能有"说清楚（引导归档，而不是只说没有）', () => {
    render(<CloudFileMenu {...props} groups={buildFileGroups({ assets: [], session_files: [] }, [])} />);
    expect(screen.getByText(/还没有可 @ 引用的文件/)).toBeInTheDocument();
    expect(screen.getByText(/先在那条消息里「归档」/)).toBeInTheDocument();
  });

  it('检索失败必须可见，不得静默显示成"没有文件"', () => {
    render(<CloudFileMenu {...props} error="文件清单检索失败，输入 @ 可重试" />);
    expect(screen.getByText('文件清单检索失败，输入 @ 可重试')).toBeInTheDocument();
    expect(screen.queryByText(/还没有可 @ 引用的文件/)).not.toBeInTheDocument();
  });

  it('检索中给进行中文案，避免空面板被误读为无结果', () => {
    render(<CloudFileMenu {...props} groups={buildFileGroups({ assets: [], session_files: [] }, [])} loading />);
    expect(screen.getByText('正在检索云空间文件…')).toBeInTheDocument();
  });
});
