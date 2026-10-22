import { describe, expect, it } from 'vitest';
import { normalizeDocObjects, resolveDisplayName, resolveObjectKey, showsM234, showsSiteScope, countM234Declarations } from '@/lib/ontologyObjects';

/**
 * 回归：存量源本体（kind='source'，B6 退役中）的 canonical doc 用旧字段名
 * object_key/name，前端直接读 o.key 会得到 undefined 并在「对象 · Link」页签渲染期崩溃
 * （TypeError: Cannot read properties of undefined (reading 'toLowerCase')）。
 */
describe('normalizeDocObjects — 本体 doc 对象清单的读取适配', () => {
  it('canonical 形状（key/display_name）原样保留并补全身份', () => {
    const out = normalizeDocObjects([{ key: 'case_file', display_name: '案例', primary_table: 't_case' }]);
    expect(out.issues).toEqual([]);
    expect(out.objects).toEqual([{ key: 'case_file', display_name: '案例', primary_table: 't_case' }]);
  });

  it('旧字段名 object_key/name 被适配为 key/display_name（不再产出 undefined key）', () => {
    const out = normalizeDocObjects([
      { object_key: 'case_stat', name: '案例统计', primary_table: 'daily_case_stats' },
    ]);
    expect(out.issues).toEqual([]);
    expect(out.objects[0].key).toBe('case_stat');
    expect(out.objects[0].display_name).toBe('案例统计');
    // 其余字段不丢（links/properties 等仍按原样透传）
    expect(out.objects[0].primary_table).toBe('daily_case_stats');
  });

  it('缺 key 但声明了主表时以主表回落为身份（与服务端 save_draft 同口径）', () => {
    const out = normalizeDocObjects([{ display_name: '设备', primary_table: 'dwd_equipment' }]);
    expect(out.objects[0].key).toBe('dwd_equipment');
    expect(out.issues).toEqual([]);
  });

  it('无法定位身份的元素被剔除，但必须作为 issue 显式上报（不静默当作无对象）', () => {
    const out = normalizeDocObjects([
      { description: '既无 key 也无主表' },
      '不是对象',
      null,
      { key: 'ok_one' },
    ]);
    expect(out.objects.map((o) => o.key)).toEqual(['ok_one']);
    expect(out.issues.map((e) => e.reason)).toEqual(['no_identity', 'not_object', 'not_object']);
    expect(out.issues[0].label).toBe('第 1 项');
  });

  it('非数组入参返回空清单而非抛错（ObjectHubTab 上界保护）', () => {
    expect(normalizeDocObjects(undefined).objects).toEqual([]);
    expect(normalizeDocObjects(null).issues).toEqual([]);
  });
});

describe('resolveObjectKey / resolveDisplayName — 单值解析', () => {
  it('key 优先级：key > object_key > code > primary_table', () => {
    expect(resolveObjectKey({ key: 'a', object_key: 'b' })).toBe('a');
    expect(resolveObjectKey({ object_key: 'b', code: 'c' })).toBe('b');
    expect(resolveObjectKey({ code: 'c', primary_table: 't' })).toBe('c');
    expect(resolveObjectKey({ primary_table: 't' })).toBe('t');
    expect(resolveObjectKey([])).toBe('');
  });

  it('展示名优先级：display_name > name > label > key（禁止裸显内部 id 之外的不可读值）', () => {
    expect(resolveDisplayName({ display_name: '订单', name: '旧名' }, 'k')).toBe('订单');
    expect(resolveDisplayName({ name: '旧名' }, 'k')).toBe('旧名');
    expect(resolveDisplayName({ label: 'L' }, 'k')).toBe('L');
    expect(resolveDisplayName({}, 'k')).toBe('k');
  });
});

/** 拍板：源本体不需要行为/规则/场景（M2/M3/M4 只对系统能力本体开放） */
describe('showsM234 / countM234Declarations — 建模位可见域', () => {
  it('源本体与业务本体不展示行为/规则/场景，系统能力本体与空 kind 存量模型仍展示', () => {
    expect(showsM234('source')).toBe(false);
    expect(showsM234('business')).toBe(false);
    expect(showsM234('system')).toBe(true);
    expect(showsM234('')).toBe(true);
    expect(showsM234(undefined)).toBe(true);
  });

  it('行为计入对象级 actions，规则/场景计入顶层字段', () => {
    expect(countM234Declarations({
      objects: [{ key: 'a', actions: [{ key: 'x' }, { key: 'y' }] }, { key: 'b' }],
      rules: [{ key: 'r1' }],
      scenarios: [{ key: 's1' }],
    })).toBe(4);
    expect(countM234Declarations({ objects: [{ key: 'a' }] })).toBe(0);
    expect(countM234Declarations(null)).toBe(0);
  });
});

/** 拍板：源本体不展示「适用站点」（它是单源物理投影，跨站路由属于业务契约层） */
describe('showsSiteScope — 适用站点页签可见域', () => {
  it('源本体隐藏，业务/系统本体与空 kind 存量模型保留', () => {
    expect(showsSiteScope('source')).toBe(false);
    expect(showsSiteScope('business')).toBe(true);
    expect(showsSiteScope('system')).toBe(true);
    expect(showsSiteScope('')).toBe(true);
    expect(showsSiteScope(undefined)).toBe(true);
  });
});
