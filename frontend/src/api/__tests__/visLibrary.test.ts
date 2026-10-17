import { describe, it, expect, vi } from 'vitest';
import { validateVisResponse } from '../visLibrary';

const validTile = {
  id: 91, code: 'tile_osm', name: 'OSM', category: 'map_tile',
  style_config: { tileUrl: 'https://x/{z}/{x}/{y}.png' },
};

describe('validateVisResponse 字模清单宽容解析', () => {
  it('非数组仍整体报错(真故障必须暴露)', () => {
    expect(() => validateVisResponse({ items: [] })).toThrow(/有效 JSON 数组/);
    expect(() => validateVisResponse(null)).toThrow(/有效 JSON 数组/);
  });

  it('未知类别/结构损坏条目只跳过该条,不再整组拒绝(存量 sql_template 行不打挂地图瓦片)', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const out = validateVisResponse([
      validTile,
      { id: 99, code: 'tpl', name: '历史 SQL 模板', category: 'sql_template', style_config: {} },
      { id: 100, code: 'bad', name: '坏条目', category: 'map_tile', style_config: null },
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].name).toBe('OSM');
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it('全部条目无法识别时仍报错(不返回空清单掩盖问题)', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(() => validateVisResponse([{ id: 1, code: 'x', name: 'x', category: 'nope', style_config: {} }]))
      .toThrow(/均无法识别/);
    warn.mockRestore();
  });
});
