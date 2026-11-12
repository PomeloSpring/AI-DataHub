/**
 * markdown 章节切分 — 把回答正文按标题切成"阶段性内容卡片"。
 *
 * chat 回答输出卡片化用:每个标题段落切成一张可滑动展开/收起的卡片,
 * 开头无标题段落单独成节(卡片头用默认文案)。
 * 只认围栏代码块之外的 ATX 标题(# ~ ####),围栏内的 # 不误切。
 */

export interface MarkdownSection {
  /** 标题文字(已去掉 # 与加粗记号);开头无标题段落为 null */
  title: string | null;
  /** 标题层级(1-4);无标题段落为 0 */
  level: number;
  /** 该章节正文(不含标题行) */
  body: string;
}

// 围栏行(``` 或 ~~~ 开头),用于跳过代码块内的伪标题
const FENCE = /^\s*(```|~~~)/;
// ATX 标题行: # ~ #### + 标题文字
const HEADING = /^(#{1,4})\s+(.*?)\s*#*\s*$/;

export function splitMarkdownSections(text: string): MarkdownSection[] {
  const sections: MarkdownSection[] = [];
  let inFence = false;
  let current: MarkdownSection = { title: null, level: 0, body: '' };

  for (const line of (text || '').split(/\r?\n/)) {
    if (FENCE.test(line)) {
      inFence = !inFence;
      current.body += (current.body ? '\n' : '') + line;
      continue;
    }
    const m = inFence ? null : HEADING.exec(line);
    if (m) {
      sections.push(current);
      current = { title: m[2].replace(/\*\*/g, '').trim() || null, level: m[1].length, body: '' };
    } else {
      current.body += (current.body ? '\n' : '') + line;
    }
  }
  sections.push(current);
  // 空体节不产卡片(如末尾孤标题),避免空白卡片
  return sections.filter(s => s.body.trim().length > 0);
}

/**
 * 按标题把正文切成「保留标题行」的连续片段（内容详情正文分段渐入用）。
 *
 * 与 splitMarkdownSections 的差别是刻意且不能合并的：那边把标题抽去当卡头，
 * 这边一段都不能少 —— 阅读页丢掉正文标题就是丢内容。
 * 只在围栏代码块之外的 ATX 标题处切，围栏内的 # 不误切。
 */
export function splitMarkdownBlocks(text: string): string[] {
  const blocks: string[] = [];
  let inFence = false;
  let current = '';
  for (const line of (text || '').split(/\r?\n/)) {
    if (FENCE.test(line)) inFence = !inFence;
    // 首段还没内容时不在这里切（开头的标题属于第一段）
    if (!inFence && current.trim() && HEADING.test(line)) {
      blocks.push(current.trimEnd());
      current = '';
    }
    current += (current ? '\n' : '') + line;
  }
  if (current.trim()) blocks.push(current.trimEnd());
  return blocks;
}
