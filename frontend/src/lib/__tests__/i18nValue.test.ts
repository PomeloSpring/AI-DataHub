/** 双语元数据读写口径（编辑页 English 输入位与中文事实源必须解耦）的护栏。 */
import { describe, expect, it } from 'vitest';
import { enValue, mergeI18n, zhValue } from '../i18nValue';

describe('zhValue', () => {
  it('英文请求下读 `*_zh` 保留中文事实源（避免英文界面保存把英文写进中文列）', () => {
    expect(zhValue({ name: 'ISV Push Dashboard', name_zh: '外部合作方推送看板' }, 'name'))
      .toBe('外部合作方推送看板');
  });
  it('中文请求或字段未本地化时回落读原值', () => {
    expect(zhValue({ name: '外部合作方推送看板' }, 'name')).toBe('外部合作方推送看板');
  });
  it('缺字段返回空串而不是 undefined（避免受控输入跳变非受控）', () => {
    expect(zhValue({}, 'name')).toBe('');
  });
});

describe('enValue', () => {
  it('取 i18n[field][en-US]', () => {
    const row = { name: 'X', i18n: { name: { 'en-US': 'Ex' } } };
    expect(enValue(row, 'name')).toBe('Ex');
  });
  it('blob 缺该 field 或该 locale → 空串（触发前端 "留空 = 暂无英文"提示）', () => {
    expect(enValue({ name: 'X' }, 'name')).toBe('');
    expect(enValue({ name: 'X', i18n: { name: {} } }, 'name')).toBe('');
    expect(enValue({ name: 'X', i18n: { description: { 'en-US': 'D' } } }, 'name')).toBe('');
  });
});

describe('mergeI18n', () => {
  it('英文留空即清空该字段的 en-US（不落空串，避免前端显示成英文 "" 覆盖中文回落）', () => {
    const next = mergeI18n({ name: { 'en-US': 'Old' } }, 'name', '   ');
    expect(next.name).toBeUndefined();
  });
  it('trim 后写入 en-US；不动其他字段的 blob', () => {
    const blob = { description: { 'en-US': 'Kept' } };
    const next = mergeI18n(blob, 'name', '  ISV Push  ');
    expect(next).toEqual({ description: { 'en-US': 'Kept' }, name: { 'en-US': 'ISV Push' } });
  });
  it('不修改入参 blob（防止 React state 引用泄漏）', () => {
    const original = { name: { 'en-US': 'A' } };
    const snapshot = JSON.parse(JSON.stringify(original));
    mergeI18n(original, 'name', 'B');
    expect(original).toEqual(snapshot);
  });
});

describe('round-trip：编辑页 English 保存后中文事实源不动', () => {
  it('中文列 name 与 i18n.name.en-US 各写各的，不互相污染', () => {
    // 服务端下发一份英文界面下的行：name 已被本地化成英文，name_zh 才是中文事实源
    const incoming = {
      name: 'Machine Guess',
      name_zh: '外部合作方推送看板',
      i18n: { name: { 'en-US': 'Machine Guess' } },
    };
    // 用户在英文界面把 English 改成人工审过的译法
    const editedEn = 'ISV Push Dashboard';
    const zh = zhValue(incoming, 'name');
    const nextBlob = mergeI18n(incoming.i18n, 'name', editedEn);
    // 提交体必须：name=中文事实源，i18n.name.en-US=人工英文
    expect(zh).toBe('外部合作方推送看板');
    expect(nextBlob.name?.['en-US']).toBe('ISV Push Dashboard');
  });
});
