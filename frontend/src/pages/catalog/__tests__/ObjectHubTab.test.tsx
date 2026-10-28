import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ObjectHubTab from '../ObjectHubTab';

/**
 * 「对象 · Link」页签渲染回归。
 *
 * 背景：存量源本体（kind='source'）落库的 canonical doc 用旧字段名 object_key/name，
 * 组件直接 `o.key.toLowerCase()` 在渲染期崩溃（白屏 + Uncaught TypeError）。
 * 对象缺少可读身份时也不得静默显示成空白 —— 必须显式提示。
 */

afterEach(() => cleanup());

describe('ObjectHubTab — 对象清单渲染不因字段形状崩溃', () => {
  it('旧字段名（object_key/name）的对象照常显示业务名与关系', () => {
    render(
      <ObjectHubTab
        modelId={1}
        objects={[
          { object_key: 'case_stat', name: '案例统计', primary_table: 'daily_case_stats' } as any,
          { object_key: 'region', name: '地区', primary_table: 'dim_region' } as any,
        ]}
      />,
    );
    // 清单项 + 详情标题两处都显示业务名（不得出现裸 key/undefined）
    expect(screen.getAllByText('案例统计').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('地区')).toBeTruthy();
    expect(screen.queryByText('undefined')).toBeNull();
    expect(screen.getByText('对象（2）')).toBeTruthy();
  });

  it('完全无法定位身份的对象被剔除，但页面显式提示原因', () => {
    render(
      <ObjectHubTab
        modelId={1}
        objects={[{ description: '没有 key 也没有主表' } as any]}
        issues={[{ index: 0, label: '第 1 项', reason: 'no_identity', hint: '缺少 key' }]}
      />,
    );
    expect(screen.getByText(/1 个对象定义无法展示/)).toBeTruthy();
    expect(screen.getByText('该模型的对象定义均缺少 key，无法展示（见左侧提示）')).toBeTruthy();
    expect(screen.getByText('对象（0）')).toBeTruthy();
  });

  it('悬空 Link 目标仍标为孤儿引用（不静默放行）', () => {
    render(
      <ObjectHubTab
        modelId={1}
        objects={[{ key: 'case_file', display_name: '案例', links: [{ type: 'belongs_to', target: 'region' }] } as any]}
      />,
    );
    expect(screen.getByText(/1 条 Link 目标不在本模型/)).toBeTruthy();
  });
});

describe('ObjectHubTab — 页面内编辑对象（2026-10-27 开放，不再仅 AS-BOT）', () => {
  const doc = {
    kind: 'source',
    objects: [{
      key: 'case', display_name: '案例', description: '旧描述', aliases: ['病例'],
      primary_table: 'pc_case',
      properties: [{ column: 'pc_case.id', name: '主键ID' }],
      links: [{ type: 'belongs_to', target: 'region' }],
    }],
  };
  const hub = doc.objects as any[];

  it('未传 canEdit/onSave 时不渲染编辑入口（只读展示场景不变）', () => {
    render(<ObjectHubTab modelId={1} objects={hub} />);
    expect(screen.queryByRole('button', { name: '编辑对象' })).toBeNull();
  });

  it('canEdit 时渲染编辑入口，保存后只改标量字段、不整份覆盖属性/关系', async () => {
    const onSave = vi.fn();
    render(
      <ObjectHubTab modelId={1} objects={hub} canEdit
        jsonContent={JSON.stringify(doc, null, 2)} onSave={onSave} />,
    );
    await userEvent.click(screen.getByRole('button', { name: '编辑对象' }));
    // 表单回填当前对象值
    const nameInput = screen.getByDisplayValue('案例');
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, '案例档案');
    await userEvent.click(screen.getByRole('button', { name: '保存为草案' }));
    expect(onSave).toHaveBeenCalledTimes(1);
    const saved = JSON.parse(onSave.mock.calls[0][0]);
    const obj = saved.objects.find((o: any) => o.key === 'case');
    expect(obj.display_name).toBe('案例档案');
    // 关键护栏：properties/links 必须原样保留（不整份覆盖）
    expect(obj.properties).toEqual([{ column: 'pc_case.id', name: '主键ID' }]);
    expect(obj.links).toEqual([{ type: 'belongs_to', target: 'region' }]);
    expect(obj.primary_table).toBe('pc_case');
    expect(obj.aliases).toEqual(['病例']);
  });

  it('无 ontology:save 权限（canEdit=false）时即使传了 onSave 也不给编辑入口', () => {
    render(
      <ObjectHubTab modelId={1} objects={hub} canEdit={false}
        jsonContent={JSON.stringify(doc)} onSave={vi.fn()} />,
    );
    expect(screen.queryByRole('button', { name: '编辑对象' })).toBeNull();
  });
});
