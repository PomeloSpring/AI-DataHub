/**
 * 文档编辑面板（参照千问「转文档编辑」）：把一条回答变成可直接编辑、复制、下载的文档。
 *
 * 三条实现口径，改动前必读：
 * 1. **正文以 DOM 为事实源**。contentEditable 一旦被 React 受控重渲染就会把光标弹回开头，
 *    所以挂载时一次性写入 innerHTML，之后只从 DOM 取值（onInput → 防抖自动保存），
 *    工具栏改格式也直接作用在 DOM 上。
 * 2. **初稿与渲染同源**。回答里还没有文档正文时，用与气泡同一个 ReactMarkdown 渲染器
 *    先把 markdown 转成 HTML（特殊块按占位说明写进引用块），不另写一套 markdown 解析。
 * 3. **保存失败不假装成功**。自动保存失败时面板保持打开并给出「重试保存」，
 *    关闭动作也会被拦住 —— 静默丢弃用户刚敲的字比报错严重得多。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  AlertTriangle, Bold, ChevronDown, Copy, Download, Highlighter, Italic, List, ListOrdered,
  Loader2, Palette, Quote, Redo2, Strikethrough, TextAlignCenter, TextAlignEnd,
  TextAlignStart, Type, Underline, Undo2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { toast } from '@/lib/toast';
import { copyRichText } from '@/lib/clipboard';
import { buildDownloadHtml, docFileName, formatDocTime, markdownForDocument } from '@/lib/markdownText';
import type { ChatDoc } from '@/stores/chatStore';

interface Props {
  doc: ChatDoc;
  /** 回答原文（markdown）：文档还没有正文时用它初稿化 */
  sourceMarkdown: string;
  onSave: (doc: ChatDoc) => Promise<boolean>;
  onClose: () => void;
}

type SaveStatus = 'clean' | 'dirty' | 'saving' | 'saved' | 'error';

const AUTOSAVE_DELAY_MS = 800;

// 段落样式（formatBlock 取值）：与工具栏「正文」下拉一一对应
const BLOCK_STYLES = [
  { value: 'p', label: '正文' },
  { value: 'h1', label: '标题 1' },
  { value: 'h2', label: '标题 2' },
  { value: 'h3', label: '标题 3' },
  { value: 'blockquote', label: '引用' },
];
const FONT_SIZES = [12, 14, 15, 16, 18, 20, 24, 28];

// 墨色/底色是用户选的"笔的颜色"，不是界面主题色，因此这里给具体色值（面板本身仍全部走主题变量）
const TEXT_COLORS = ['#111827', '#ef4444', '#f97316', '#eab308', '#22c55e', '#0ea5e9', '#6366f1', '#a855f7'];
const HIGHLIGHT_COLORS = ['transparent', '#fef08a', '#bbf7d0', '#bfdbfe', '#fbcfe8', '#fed7aa', '#e9d5ff'];

export default function DocEditorPanel({ doc, sourceMarkdown, onSave, onClose }: Props) {
  const [title, setTitle] = useState(doc.title);
  const [revisions, setRevisions] = useState(doc.revisions);
  const [status, setStatus] = useState<SaveStatus>('clean');
  const [unsupported, setUnsupported] = useState(false);

  const bodyRef = useRef<HTMLDivElement | null>(null);
  const seedRef = useRef<HTMLDivElement | null>(null);
  const savedRangeRef = useRef<Range | null>(null);
  const titleRef = useRef(doc.title);
  const revisionsRef = useRef(doc.revisions);
  const savedHtmlRef = useRef(doc.html);
  const dirtyRef = useRef(false);
  const timerRef = useRef<number | null>(null);
  const seededRef = useRef(false);
  // Radix Portal 的正文节点在首个 commit 里还不存在（父组件的挂载副作用跑在它之前），
  // 因此用 ref 回调置 ready，等正文真的挂上后再播种（否则 innerHTML 写到一个空引用上，文档是白的）。
  const [editorReady, setEditorReady] = useState(false);

  const persist = useCallback(async (force = false): Promise<boolean> => {
    const html = bodyRef.current?.innerHTML ?? '';
    if (!force && !dirtyRef.current && html === savedHtmlRef.current) return true;
    if (timerRef.current != null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    dirtyRef.current = false;
    setStatus('saving');
    const now = new Date().toISOString();
    const nextRevisions = revisionsRef.current + 1;
    const ok = await onSave({
      title: titleRef.current,
      html,
      created_at: doc.created_at || now,
      updated_at: now,
      revisions: nextRevisions,
    });
    if (ok) {
      revisionsRef.current = nextRevisions;
      savedHtmlRef.current = html;
      setRevisions(nextRevisions);
      setStatus('saved');
    } else {
      dirtyRef.current = true;
      setStatus('error');
    }
    return ok;
  }, [onSave, doc.created_at]);

  const scheduleSave = useCallback(() => {
    dirtyRef.current = true;
    setStatus('dirty');
    if (timerRef.current != null) return;
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      void persist();
    }, AUTOSAVE_DELAY_MS);
  }, [persist]);

  // 就绪：有正文就用正文，没有就用回答初稿化（与气泡同一渲染器）后落一次库
  useEffect(() => {
    const el = bodyRef.current;
    if (!editorReady || !el || seededRef.current) return;
    seededRef.current = true;
    if (doc.html) {
      el.innerHTML = doc.html;
      savedHtmlRef.current = doc.html;
      return;
    }
    const seed = seedRef.current?.innerHTML?.trim() || '';
    el.innerHTML = seed;
    savedHtmlRef.current = '';
    void persist(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editorReady]);

  const attachBody = useCallback((node: HTMLDivElement | null) => {
    bodyRef.current = node;
    if (node) setEditorReady(true);
  }, []);

  useEffect(() => () => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
  }, []);

  /** 记住当前选区：Radix 下拉/气泡会抢焦点，抢回来后要能把格式作用在原来那段文字上。 */
  const captureSelection = () => {
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    if (bodyRef.current?.contains(range.commonAncestorContainer)) {
      savedRangeRef.current = range.cloneRange();
    }
  };

  const exec = (command: string, value?: string) => {
    const el = bodyRef.current;
    if (!el) return false;
    if (typeof document.execCommand !== 'function') {
      // 不支持就不假装改好了：显式提示，正文仍可手改
      setUnsupported(true);
      return false;
    }
    el.focus();
    const sel = window.getSelection();
    if (sel && savedRangeRef.current && sel.isCollapsed) {
      sel.removeAllRanges();
      sel.addRange(savedRangeRef.current);
    }
    try {
      document.execCommand('styleWithCSS', false, 'true');
    } catch { /* 部分浏览器不支持该开关，后续命令仍可用 */ }
    const ok = document.execCommand(command, false, value);
    if (!ok) { setUnsupported(true); return false; }
    scheduleSave();
    return true;
  };

  /** 字号：execCommand('fontSize') 只认 1~7 档且落 <font>，这里直接包 span 用 px。 */
  const applyFontSize = (px: number) => {
    const sel = window.getSelection();
    const range = (sel?.rangeCount ? sel.getRangeAt(0) : savedRangeRef.current) || null;
    if (!range || range.collapsed || !bodyRef.current
      || !bodyRef.current.contains(range.commonAncestorContainer)) {
      // 选区不在正文里（或已收起）时不能静默不动：用户会以为字号改了
      toast.info('请先在文档里选中要改字号的文字');
      return;
    }
    bodyRef.current.focus();
    if (sel) { sel.removeAllRanges(); sel.addRange(range); }
    const span = document.createElement('span');
    span.style.fontSize = `${px}px`;
    try {
      range.surroundContents(span);
    } catch {
      span.appendChild(range.extractContents());
      range.insertNode(span);
    }
    if (!bodyRef.current.contains(span)) {
      toast.error('选区跨越了段落边界，请选中同一段内的文字再改字号');
      return;
    }
    captureSelection();
    scheduleSave();
  };

  const handleClose = async () => {
    const ok = await persist();
    if (!ok) return;   // 没存上就不关：否则用户以为已保存
    onClose();
  };

  const handleCopy = async () => {
    const el = bodyRef.current;
    const text = (el?.innerText || el?.textContent || '').trim();
    if (!text) { toast.info('文档还没有内容'); return; }
    try {
      const mode = await copyRichText(el?.innerHTML || '', text);
      toast.success(mode === 'rich'
        ? '已复制（保留格式）'
        : '已复制为纯文本（当前浏览器不支持富文本剪贴板）');
    } catch (e: any) {
      toast.error(e?.message || '复制失败，请手动选择文本复制');
    }
  };

  const handleDownload = () => {
    const el = bodyRef.current;
    const html = buildDownloadHtml(titleRef.current, el?.innerHTML || '');
    const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = docFileName(titleRef.current);
    a.click();
    // 下一拍再释放：与 click 同帧 revoke 会抢在浏览器取 blob 之前，下载偶发变空文件
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast.success('已开始下载（HTML 格式，可用 Word/WPS 打开）');
  };

  const onTitleChange = (v: string) => {
    setTitle(v);
    titleRef.current = v;
    scheduleSave();
  };

  const statusText = ({
    clean: `已保存（编辑 ${revisions} 次）`,
    dirty: '有改动待保存…',
    saving: '正在保存…',
    saved: `已自动保存（编辑 ${revisions} 次）`,
    error: '保存失败，改动只在当前页面，关掉就没了',
  } as Record<SaveStatus, string>)[status];

  return (
    <Dialog open onOpenChange={(open) => { if (!open) void handleClose(); }}>
      <DialogContent className="flex h-[92vh] max-h-[92vh] w-[min(1080px,94vw)] max-w-none flex-col gap-0 overflow-hidden p-0">
        {/* 顶栏：标题（可改）+ 编辑次数 + 复制 / 下载；右侧给基座关闭按钮留位 */}
        <div className="flex shrink-0 items-center gap-2 border-b px-4 py-3 pr-12">
          <input
            aria-label="文档标题"
            value={title}
            placeholder="无标题"
            onChange={e => onTitleChange(e.target.value)}
            className="min-w-0 flex-1 bg-transparent text-base font-semibold outline-none placeholder:text-muted-foreground"
          />
          <span className="shrink-0 text-xs text-muted-foreground">编辑 {revisions} 次</span>
          <Button variant="outline" size="sm" onClick={() => { void handleCopy(); }}>
            <Copy className="mr-1 h-3.5 w-3.5" />复制
          </Button>
          <Button variant="outline" size="sm" onClick={handleDownload}>
            <Download className="mr-1 h-3.5 w-3.5" />下载
          </Button>
        </div>

        {/* 工具栏：按下不抢焦点（onMouseDown preventDefault），格式才能落在原选区上 */}
        <div className="flex shrink-0 flex-wrap items-center gap-1 border-b bg-muted/20 px-4 py-2">
          <ToolButton icon={Undo2} label="撤销" onClick={() => exec('undo')} />
          <ToolButton icon={Redo2} label="重做" onClick={() => exec('redo')} />
          <MenuButton
            label="段落样式"
            items={BLOCK_STYLES.map(b => ({ key: b.value, label: b.label }))}
            onPick={v => exec('formatBlock', `<${v}>`)}
          />
          <MenuButton
            label="字号"
            items={FONT_SIZES.map(px => ({ key: String(px), label: `${px}px` }))}
            onPick={v => applyFontSize(Number(v))}
          />
          <Divider />
          <ToolButton icon={Bold} label="加粗" onClick={() => exec('bold')} />
          <ToolButton icon={Strikethrough} label="删除线" onClick={() => exec('strikeThrough')} />
          <ToolButton icon={Underline} label="下划线" onClick={() => exec('underline')} />
          <ToolButton icon={Italic} label="斜体" onClick={() => exec('italic')} />
          <Divider />
          <ToolButton icon={List} label="无序列表" onClick={() => exec('insertUnorderedList')} />
          <ToolButton icon={ListOrdered} label="有序列表" onClick={() => exec('insertOrderedList')} />
          <Divider />
          <ToolButton icon={TextAlignStart} label="左对齐" onClick={() => exec('justifyLeft')} />
          <ToolButton icon={TextAlignCenter} label="居中" onClick={() => exec('justifyCenter')} />
          <ToolButton icon={TextAlignEnd} label="右对齐" onClick={() => exec('justifyRight')} />
          <Divider />
          <ColorPopover
            icon={Palette}
            label="文字颜色"
            colors={TEXT_COLORS}
            onPick={c => exec('foreColor', c)}
          />
          <ColorPopover
            icon={Highlighter}
            label="底色"
            colors={HIGHLIGHT_COLORS}
            onPick={c => (c === 'transparent' ? exec('removeFormat') : exec('hiliteColor', c))}
          />
          <Divider />
          <ToolButton icon={Quote} label="引用段" onClick={() => exec('formatBlock', '<blockquote>')} />
          <ToolButton icon={Type} label="清除格式" onClick={() => exec('removeFormat')} />
        </div>

        {unsupported && (
          <div className="flex shrink-0 items-center gap-2 border-b bg-amber-500/10 px-4 py-1.5 text-xs text-amber-600 dark:text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5" />
            当前浏览器不支持富文本工具栏；正文仍可直接编辑，格式改动未生效。
          </div>
        )}

        {/* 正文：DOM 即事实源（React 不接管内容，避免光标被重置） */}
        <div className="min-h-0 flex-1 overflow-y-auto bg-background">
          <div
            ref={attachBody}
            contentEditable
            suppressContentEditableWarning
            role="textbox"
            aria-multiline="true"
            aria-label="文档正文"
            spellCheck={false}
            onInput={scheduleSave}
            onMouseUp={captureSelection}
            onKeyUp={captureSelection}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
                e.preventDefault();
                void persist();
              }
            }}
            className="mx-auto min-h-full w-full max-w-[820px] px-8 py-6 leading-relaxed focus:outline-none
              prose prose-sm max-w-none [&_:is(h1,h2,h3)]:font-semibold [&_table]:text-xs"
          />
        </div>

        <div className="flex shrink-0 items-center justify-between gap-2 border-t px-4 py-2 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            {status === 'saving' && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {status === 'error' && <AlertTriangle className="h-3.5 w-3.5 text-destructive" />}
            {statusText}
            {doc.created_at && <span className="ml-2 opacity-70">· {formatDocTime(doc.created_at, '创建于')}</span>}
          </span>
          <span className="flex items-center gap-2">
            {status === 'error' && (
              <Button variant="outline" size="sm" onClick={() => { void persist(true); }}>重试保存</Button>
            )}
            <span className="opacity-70">文档随会话保存，删除会话即一并清理</span>
            <Button variant="outline" size="sm" onClick={() => { void handleClose(); }}>关闭</Button>
          </span>
        </div>

        {/* 初稿渲染器：与气泡同一 ReactMarkdown，取 innerHTML 作为文档正文（仅无正文时挂载） */}
        {!doc.html && (
          <div ref={seedRef} aria-hidden className="sr-only">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdownForDocument(sourceMarkdown)}</ReactMarkdown>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Divider() {
  return <span className="mx-1 h-4 w-px bg-border" />;
}

/**
 * 工具栏里的下拉动作（段落样式 / 字号）。
 *
 * 用菜单而不是“当前值”下拉：光标所在段落到底是什么样式需要反向探测，
 * 探测不做就会显示一个错的当前值（比不给当前值更容易误导）。
 */
function MenuButton({
  label, items, onPick,
}: { label: string; items: { key: string; label: string }[]; onPick: (key: string) => void }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          title={label}
          aria-label={label}
          onMouseDown={e => e.preventDefault()}
          className="flex h-7 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          {label}
          <ChevronDown className="h-3 w-3" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-32">
        {items.map(it => (
          <DropdownMenuItem key={it.key} className="text-xs" onClick={() => onPick(it.key)}>
            {it.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ToolButton({
  icon: Icon, label, onClick,
}: { icon: any; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onMouseDown={e => e.preventDefault()}
      onClick={onClick}
      className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
    >
      <Icon className="h-4 w-4" />
    </button>
  );
}

function ColorPopover({
  icon: Icon, label, colors, onPick,
}: { icon: any; label: string; colors: string[]; onPick: (color: string) => void }) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          title={label}
          aria-label={label}
          onMouseDown={e => e.preventDefault()}
          className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Icon className="h-4 w-4" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-auto p-2">
        <div className="flex flex-wrap gap-1.5" style={{ maxWidth: 168 }}>
          {colors.map(c => (
            <button
              key={c}
              type="button"
              aria-label={`${label}：${c === 'transparent' ? '无' : c}`}
              title={c === 'transparent' ? '无底色' : c}
              onClick={() => { onPick(c); }}
              className="h-5 w-5 rounded border border-border/70"
              style={{ background: c === 'transparent' ? 'repeating-conic-gradient(#e2e8f0 0% 25%, #fff 0% 50%) 50%/8px 8px' : c }}
            />
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
