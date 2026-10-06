import { describe, it, expect } from 'vitest';
import {
  parseCsv, toCsv, toTsv, sortRows, filterRows, formatCell, formatSize,
} from '../dataGrid';

describe('parseCsv', () => {
  it('解析简单 CSV,首行为表头', () => {
    const { columns, rows } = parseCsv('id,name\n1,alpha\n2,beta\n');
    expect(columns).toEqual(['id', 'name']);
    expect(rows).toEqual([{ id: '1', name: 'alpha' }, { id: '2', name: 'beta' }]);
  });

  it('支持带引号字段(逗号/换行/转义引号)', () => {
    const text = 'id,desc\n1,"a,b"\n2,"line1\nline2"\n3,"say ""hi"""\n';
    const { columns, rows } = parseCsv(text);
    expect(columns).toEqual(['id', 'desc']);
    expect(rows[0].desc).toBe('a,b');
    expect(rows[1].desc).toBe('line1\nline2');
    expect(rows[2].desc).toBe('say "hi"');
  });

  it('剥离 BOM 与 CRLF,列数不齐补齐空值', () => {
    const { columns, rows } = parseCsv('\uFEFFa,b,c\r\n1,2\r\n');
    expect(columns).toEqual(['a', 'b', 'c']);
    expect(rows).toEqual([{ a: '1', b: '2', c: '' }]);
  });

  it('空文本返回空结构', () => {
    expect(parseCsv('')).toEqual({ columns: [], rows: [] });
    expect(parseCsv('\n\n')).toEqual({ columns: [], rows: [] });
  });

  it('空表头占位列名', () => {
    const { columns } = parseCsv('a,,c\n1,2,3');
    expect(columns).toEqual(['a', 'col_2', 'c']);
  });
});

describe('toCsv / toTsv', () => {
  const columns = ['id', 'name'];
  const rows = [{ id: 1, name: 'a,b' }, { id: 2, name: 'x"y' }, { id: 3, name: null }];

  it('toCsv 带 BOM 且转义,可被 parseCsv 还原', () => {
    const csv = toCsv(columns, rows);
    expect(csv.startsWith('\uFEFF')).toBe(true);
    const back = parseCsv(csv);
    expect(back.columns).toEqual(columns);
    expect(back.rows[0].name).toBe('a,b');
    expect(back.rows[1].name).toBe('x"y');
    expect(back.rows[2].name).toBe(''); // null → 空串
  });

  it('toTsv 以制表符分隔,null 兼容', () => {
    const tsv = toTsv(columns, rows);
    expect(tsv.split('\n')[0]).toBe('id\tname');
    expect(tsv.split('\n')[3]).toBe('3\t');
  });
});

describe('sortRows', () => {
  const rows = [{ v: '10' }, { v: '2' }, { v: null }, { v: '' }, { v: '1' }];

  it('数值感知升序,空值恒排最后', () => {
    expect(sortRows(rows, 'v', 'asc').map(r => r.v)).toEqual(['1', '2', '10', null, '']);
  });

  it('降序', () => {
    expect(sortRows(rows, 'v', 'desc')[0].v).toBe('10');
  });

  it('非数值按字符串比较', () => {
    const s = [{ n: 'b' }, { n: 'a' }, { n: 'c' }];
    expect(sortRows(s, 'n', 'asc').map(r => r.n)).toEqual(['a', 'b', 'c']);
  });
});

describe('filterRows', () => {
  const columns = ['a', 'b'];
  const rows = [{ a: 'Hello', b: 'World' }, { a: '你好', b: '世界' }, { a: 'foo', b: 'bar' }];

  it('大小写不敏感的全字段过滤', () => {
    expect(filterRows(rows, columns, 'hello')).toHaveLength(1);
    expect(filterRows(rows, columns, '世界')).toHaveLength(1);
    expect(filterRows(rows, columns, 'o')).toHaveLength(2); // Hello / foo·bar
  });

  it('空关键字不过滤', () => {
    expect(filterRows(rows, columns, '  ')).toHaveLength(3);
  });
});

describe('formatCell / formatSize', () => {
  it('空值显示为空串', () => {
    expect(formatCell(null)).toBe('');
    expect(formatCell(undefined)).toBe('');
    expect(formatCell(0)).toBe('0');
    expect(formatCell(false)).toBe('false');
  });

  it('字节数人类可读', () => {
    expect(formatSize(512)).toBe('512 B');
    expect(formatSize(2048)).toBe('2.0 KB');
    expect(formatSize(3 * 1024 * 1024)).toBe('3.0 MB');
  });
});
