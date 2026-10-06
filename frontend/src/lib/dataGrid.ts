/**
 * 数据网格纯函数工具 — CSV 解析/序列化、排序、过滤、单元格格式化。
 * DataGrid / ArtifactCard / SessionFilesPanel 共用;纯函数便于单测。
 */

export interface GridData {
  columns: string[];
  rows: Record<string, any>[];
}

/** 单元格显示文本:null/undefined → 空串,其余 String()。 */
export function formatCell(v: any): string {
  if (v === null || v === undefined) return '';
  return String(v);
}

/**
 * CSV 文本解析为 {columns, rows}。
 * 支持带引号字段(内含逗号/换行/双引号转义),首行视为表头;
 * 首列 BOM 自动剥离;列数不齐的行按空值补齐/截断。
 */
export function parseCsv(text: string): GridData {
  const src = (text || '').replace(/^\uFEFF/, '');
  const records: string[][] = [];
  let field = '';
  let record: string[] = [];
  let inQuotes = false;
  const pushField = () => { record.push(field); field = ''; };
  const pushRecord = () => { pushField(); records.push(record); record = []; };

  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    if (inQuotes) {
      if (ch === '"') {
        if (src[i + 1] === '"') { field += '"'; i++; }
        else inQuotes = false;
      } else field += ch;
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ',') {
      pushField();
    } else if (ch === '\n') {
      pushRecord();
    } else if (ch === '\r') {
      // \r\n 归一为一条记录;孤立 \r 也按换行处理
      if (src[i + 1] === '\n') i++;
      pushRecord();
    } else {
      field += ch;
    }
  }
  if (field.length > 0 || record.length > 0) pushRecord();

  // 全空行(仅换行/空字段)不参与表头与数据行
  const nonEmpty = records.filter(r => r.some(c => c !== ''));
  const header = nonEmpty.shift() || [];
  const columns = header.map((c, i) => c.trim() || `col_${i + 1}`);
  const rows = nonEmpty.map(r => {
    const row: Record<string, any> = {};
    columns.forEach((c, i) => { row[c] = r[i] ?? ''; });
    return row;
  });
  return { columns, rows };
}

function csvEscape(v: any): string {
  const s = formatCell(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** 序列化为 CSV(带 BOM,便于 Excel 直接打开不乱码)。 */
export function toCsv(columns: string[], rows: Record<string, any>[]): string {
  const lines = [columns.map(csvEscape).join(',')];
  for (const row of rows) lines.push(columns.map(c => csvEscape(row[c])).join(','));
  return '\uFEFF' + lines.join('\n');
}

/** 序列化为 TSV(复制到剪贴板/表格软件用)。 */
export function toTsv(columns: string[], rows: Record<string, any>[]): string {
  const lines = [columns.join('\t')];
  for (const row of rows) lines.push(columns.map(c => formatCell(row[c]).replace(/\t/g, ' ')).join('\t'));
  return lines.join('\n');
}

/** 排序:两侧均可解析为数值时按数值比,否则按字符串 localeCompare;null 恒排最后。 */
export function sortRows(
  rows: Record<string, any>[], column: string, dir: 'asc' | 'desc',
): Record<string, any>[] {
  const sign = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = a[column];
    const vb = b[column];
    const ea = va === null || va === undefined || va === '';
    const eb = vb === null || vb === undefined || vb === '';
    if (ea && eb) return 0;
    if (ea) return 1;
    if (eb) return -1;
    const na = Number(va);
    const nb = Number(vb);
    if (!Number.isNaN(na) && !Number.isNaN(nb)) return sign * (na - nb);
    return sign * String(va).localeCompare(String(vb), 'zh');
  });
}

/** 全字段关键字过滤(大小写不敏感,任一列包含即命中)。 */
export function filterRows(
  rows: Record<string, any>[], columns: string[], keyword: string,
): Record<string, any>[] {
  const kw = (keyword || '').trim().toLowerCase();
  if (!kw) return rows;
  return rows.filter(r => columns.some(c => formatCell(r[c]).toLowerCase().includes(kw)));
}

/** 字节数人类可读(文件面板用)。 */
export function formatSize(bytes: number): string {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
