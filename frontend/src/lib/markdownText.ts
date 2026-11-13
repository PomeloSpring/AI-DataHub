/**
 * 回答文本的"双形态复制 / 转文档"辅助。
 *
 * 两种形态语义不同，不能互相替代：
 * - 复制为Markdown：模型输出的原始 markdown（与气泡渲染同一事实源，粘到别处仍是原文）；
 * - 复制：用户看到的纯文本（去掉标记语法，特殊块换成可读占位说明）。
 *
 * 特殊块（chart/graph/artifact/design/mermaid/sql）一律走 `splitChartBlocks` 解析，
 * 不另写一套围栏识别 —— 否则渲染与复制两处口径会分叉（历史缺陷类型）。
 */
import { splitChartBlocks, type ChartSegment } from './chartBlocks';

const BULLET = '• ';

/** 去掉行内标记（加粗/斜体/删除线/行内码/链接/图片），保留可读文字。 */
function stripInlineMarks(line: string): string {
  let s = line;
  s = s.replace(/^#{1,6}\s+/, '');                 // 标题井号
  s = s.replace(/^>\s?/, '');                      // 引用尖括号
  s = s.replace(/^\s*[-*+]\s+/, BULLET);           // 无序列表符号统一为圆点
  // 有序列表保留编号：编号就是步骤顺序，去掉会把“1./2.”两段并成无差别文字
  s = s.replace(/!\[([^\]]*)\]\([^)]*\)/g, (_m, alt) => (alt ? `【图片：${alt}】` : '【图片】'));
  // 链接：正文 + 括号里的 http 地址（纯文本形态下地址是用户要的信息；非 web 协议只留正文）
  s = s.replace(/\[([^\]]*)\]\(([^)]*)\)/g,
    (_m, text, url) => (/^https?:\/\//i.test(String(url).trim()) && String(url).trim() !== String(text)
      ? `${text} (${String(url).trim()})` : text));
  s = s.replace(/`+/g, '');                        // 行内/围栏反引号
  s = s.replace(/\*\*([^*]*)\*\*/g, '$1');
  s = s.replace(/__([^_]*)__/g, '$1');
  s = s.replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1$2');
  s = s.replace(/~~([^~]*)~~/g, '$1');
  return s;
}

/** 是否表格分隔行（`| --- | :-: |`）。 */
const isTableDivider = (line: string) => /^\s*\|?[\s:|-]*-[\s:|-]*\|[\s:|-]*$/.test(line) && !/[^\s|:\-]/.test(line);

/** 是否整行分隔线（`---` / `***` / `___`）。 */
const isHr = (line: string) => /^\s*(?:([-*_])\s*){3,}$/.test(line);

/** markdown 片段 → 纯文本（逐行去标记，多空行收敛）。 */
export function markdownBlockToPlainText(md: string): string {
  const out: string[] = [];
  for (const raw of (md || '').replace(/\r\n?/g, '\n').split('\n')) {
    const line = raw.trimEnd();
    if (!line.trim()) { out.push(''); continue; }
    if (isHr(line) || isTableDivider(line)) continue;
    out.push(stripInlineMarks(line));
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

/**
 * 特殊块的纯文本占位：图上/卡片上能读到的信息（标题、文件名）保留，
 * 交互内容（图表、看板设计）以【…】标注，避免复制出来是一片空白。
 */
function segmentPlaceholder(seg: ChartSegment): string {
  switch (seg.kind) {
    case 'chart':
      return seg.title ? `【图表：${seg.title}】` : '【图表】';
    case 'chart-ref':
      // 渲染机图表：数据在受管对象里（不在消息文本中），同样给可读占位而不漏 id/JSON
      return seg.title ? `【图表：${seg.title}】` : '【图表】';
    case 'mermaid':
      return seg.title ? `【流程图：${seg.title}】` : '【流程图】';
    case 'graph':
      return seg.title ? `【关系图谱：${seg.title}】` : '【关系图谱】';
    case 'artifact':
      return `【文件：${seg.filename}】`;
    case 'design':
      return '【看板设计】';
    case 'report':
      // 交付文档正文在受管对象里（不在消息文本中）：复制时给可读占位，
      // 不把 doc_id / JSON 源码当成“正文”塞给用户
      return seg.title ? `【整页报告：${seg.title}】` : '【整页报告】';
    case 'report-pending':
      // 流式中途复制：文档还没组装完，给进度占位而不是漏 JSON 源码
      return '【整页报告生成中】';
    case 'sql':
      return seg.code;      // SQL 卡片展示的就是语句本身
    case 'raw':
      return seg.text;      // 解析失败的块：原样文本仍是用户看到的内容
    default:
      return seg.text;
  }
}

/** 整段回答 → 纯文本（"复制"用）。 */
export function markdownToPlainText(md: string): string {
  return splitChartBlocks(md || '')
    .map(seg => (seg.kind === 'markdown' ? markdownBlockToPlainText(seg.text) : segmentPlaceholder(seg)))
    .filter(t => t.trim())
    .join('\n\n')
    .trim();
}

/**
 * 整段回答 → 可转成文档的 markdown（"转文档编辑"的初稿）。
 *
 * 与渲染同源：普通片段原样保留，特殊块替换为引用行占位（文档里没有交互卡片，
 * 但要知道这里原本放了什么）。
 */
export function markdownForDocument(md: string): string {
  return splitChartBlocks(md || '')
    .map(seg => {
      if (seg.kind === 'markdown') return seg.text;
      if (seg.kind === 'sql') return seg.code;
      if (seg.kind === 'raw') return seg.text;
      return `> ${segmentPlaceholder(seg)}`;
    })
    .filter(t => t.trim())
    .join('\n\n')
    .trim();
}

/** 文档下载文件名：去掉路径非法字符，空标题回落"未命名文档"。 */
export function docFileName(title: string, ext = 'html'): string {
  const safe = (title || '').replace(/[\\/:*?"<>|]/g, '_').trim() || '未命名文档';
  return `${safe.slice(0, 60)}.${ext}`;
}

/**
 * 下载用的独立 HTML：内联最小排版样式（Word/WPS 打开即所见效果），
 * 正文原样嵌入，不做二次转换（转换即丢格式）。
 */
export function buildDownloadHtml(title: string, bodyHtml: string): string {
  const heading = (title || '').trim() || '未命名文档';
  return [
    '<!DOCTYPE html>',
    '<html lang="zh-CN"><head><meta charset="utf-8">',
    `<title>${escapeHtml(heading)}</title>`,
    '<style>body{max-width:820px;margin:40px auto;padding:0 24px;',
    'font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;',
    'line-height:1.75;color:#1f2937}h1,h2,h3{line-height:1.4}table{border-collapse:collapse}',
    'td,th{border:1px solid #d1d5db;padding:4px 10px}pre{background:#f3f4f6;padding:12px;border-radius:6px;overflow:auto}',
    'blockquote{margin:0 0 12px;padding-left:12px;border-left:3px solid #d1d5db;color:#6b7280}</style>',
    '</head><body>',
    `<h1>${escapeHtml(heading)}</h1>`,
    bodyHtml,
    '</body></html>',
  ].join('\n');
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** ISO 时间 → 「创建于 11-06 10:51」样式的可读标注（卡片/面板共用一处口径）。 */
export function formatDocTime(iso: string, prefix = ''): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  const text = `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return prefix ? `${prefix} ${text}` : text;
}
