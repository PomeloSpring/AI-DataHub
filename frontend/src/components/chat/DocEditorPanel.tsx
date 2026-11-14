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
import { useTranslation } from 'react-i18next';
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

// 段落样式（formatBlock 取值）：与工具栏「正文」下拉一一对应；文案只存 key，渲染期经 t() 取
type BlockStyleKey = 'docEditor.block.p' | 'docEditor.block.h1' | 'docEditor.block.h2' | 'docEditor.block.h3' | 'docEditor.block.blockquote';
const BLOCK_STYLES: { value: string; labelKey: BlockStyleKey }[] = [
  { value: 'p', labelKey: 'docEditor.block.p' },
  { value: 'h1', labelKey: 'docEditor.block.h1' },
  { value: 'h2', labelKey: 'docEditor.block.h2' },
  { value: 'h3', labelKey: 'docEditor.block.h3' },
  { value: 'blockquote', labelKey: 'docEditor.block.blockquote' },
];
const FONT_SIZES = [12, 14, 15, 16, 18, 20, 24, 28];

// 墨色/底色是用户选的"笔的颜色"，不是界面主题色，因此这里给具体色值（面板本身仍全部走主题变量）
const TEXT_COLORS = ['#111827', '#ef4444', '#f97316', '#eab308', '#22c55e', '#0ea5e9', '#6366f1', '#a855f7'];
const HIGHLIGHT_COLORS = ['transparent', '#fef08a', '#bbf7d0', '#bfdbfe', '#fbcfe8', '#fed7aa', '#e9d5ff'];

export default function DocEditorPanel({ doc, sourceMarkdown, onSave, onClose }: Props) {
  const { t } = useTranslation(['chat', 'common']);
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
      toast.info(t('docEditor.selectTextFirst'));
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
      toast.error(t('docEditor.crossParagraph'));
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
    if (!text) { toast.info(t('docEditor.emptyDoc')); return; }
    try {
      const mode = await copyRichText(el?.innerHTML || '', text);
      toast.success(mode === 'rich'
        ? t('docEditor.copiedRich')
        : t('docEditor.copiedPlain'));
    } catch (e: any) {
      toast.error(e?.message || t('messageActions.copyFailed'));
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
    toast.success(t('docEditor.downloadStarted'));
  };

  const onTitleChange = (v: string) => {
    setTitle(v);
    titleRef.current = v;
    scheduleSave();
  };

  const statusText = ({
    clean: t('docEditor.statusClean', { count: revisions }),
    dirty: t('docEditor.statusDirty'),
    saving: t('docEditor.statusSaving'),
    saved: t('docEditor.statusSaved', { count: revisions }),
    error: t('docEditor.statusError'),
  } as Record<SaveStatus, string>)[status];

  return (
    <Dialog open onOpenChange={(open) => { if (!open) void handleClose(); }}>
      <DialogContent className="flex h-[92vh] max-h-[92vh] w-[min(1080px,94vw)] max-w-none flex-col gap-0 overflow-hidden p-0">
        {/* 顶栏：标题（可改）+ 编辑次数 + 复制 / 下载；右侧给基座关闭按钮留位 */}
        <div className="flex shrink-0 items-center gap-2 border-b px-4 py-3 pr-12">
          <input
            aria-label={t('docEditor.titleAria')}
            value={title}
            placeholder={t('docCard.untitled')}
            onChange={e => onTitleChange(e.target.value)}
            className="min-w-0 flex-1 bg-transparent text-base font-semibold outline-none placeholder:text-muted-foreground"
          />
          <span className="shrink-0 text-xs text-muted-foreground">{t('docEditor.editCount', { count: revisions })}</span>
          <Button variant="outline" size="sm" onClick={() => { void handleCopy(); }}>
            <Copy className="mr-1 h-3.5 w-3.5" />{t('common:action.copy')}
          </Button>
          <Button variant="outline" size="sm" onClick={handleDownload}>
            <Download className="mr-1 h-3.5 w-3.5" />{t('common:action.download')}
          </Button>
        </div>

        {/* 工具栏：按下不抢焦点（onMouseDown preventDefault），格式才能落在原选区上 */}
        <div className="flex shrink-0 flex-wrap items-center gap-1 border-b bg-muted/20 px-4 py-2">
          <ToolButton icon={Undo2} label={t('docEditor.tool.undo')} onClick={() => exec('undo')} />
          <ToolButton icon={Redo2} label={t('docEditor.tool.redo')} onClick={() => exec('redo')} />
          <MenuButton
            label={t('docEditor.blockStyle')}
            items={BLOCK_STYLES.map(b => ({ key: b.value, label: t(b.labelKey) }))}
            onPick={v => exec('formatBlock', `<${v}>`)}
          />
          <MenuButton
            label={t('docEditor.fontSize')}
            items={FONT_SIZES.map(px => ({ key: String(px), label: `${px}px` }))}
            onPick={v => applyFontSize(Number(v))}
          />
          <Divider />
          <ToolButton icon={Bold} label={t('docEditor.tool.bold')} onClick={() => exec('bold')} />
          <ToolButton icon={Strikethrough} label={t('docEditor.tool.strike')} onClick={() => exec('strikeThrough')} />
          <ToolButton icon={Underline} label={t('docEditor.tool.underline')} onClick={() => exec('underline')} />
          <ToolButton icon={Italic} label={t('docEditor.tool.italic')} onClick={() => exec('italic')} />
          <Divider />
          <ToolButton icon={List} label={t('docEditor.tool.ul')} onClick={() => exec('insertUnorderedList')} />
          <ToolButton icon={ListOrdered} label={t('docEditor.tool.ol')} onClick={() => exec('insertOrderedList')} />
          <Divider />
          <ToolButton icon={TextAlignStart} label={t('docEditor.tool.alignLeft')} onClick={() => exec('justifyLeft')} />
          <ToolButton icon={TextAlignCenter} label={t('docEditor.tool.alignCenter')} onClick={() => exec('justifyCenter')} />
          <ToolButton icon={TextAlignEnd} label={t('docEditor.tool.alignRight')} onClick={() => exec('justifyRight')} />
          <Divider />
          <ColorPopover
            icon={Palette}
            label={t('docEditor.tool.textColor')}
            colors={TEXT_COLORS}
            onPick={c => exec('foreColor', c)}
          />
          <ColorPopover
            icon={Highlighter}
            label={t('docEditor.tool.highlight')}
            colors={HIGHLIGHT_COLORS}
            onPick={c => (c === 'transparent' ? exec('removeFormat') : exec('hiliteColor', c))}
          />
          <Divider />
          <ToolButton icon={Quote} label={t('docEditor.tool.quote')} onClick={() => exec('formatBlock', '<blockquote>')} />
          <ToolButton icon={Type} label={t('docEditor.tool.clearFormat')} onClick={() => exec('removeFormat')} />
        </div>

        {unsupported && (
          <div className="flex shrink-0 items-center gap-2 border-b bg-amber-500/10 px-4 py-1.5 text-xs text-amber-600 dark:text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5" />
            {t('docEditor.unsupported')}
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
            aria-label={t('docEditor.bodyAria')}
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
            {doc.created_at && <span className="ml-2 opacity-70">· {formatDocTime(doc.created_at, t('docCard.createdPrefix'))}</span>}
          </span>
          <span className="flex items-center gap-2">
            {status === 'error' && (
              <Button variant="outline" size="sm" onClick={() => { void persist(true); }}>{t('docEditor.retrySave')}</Button>
            )}
            <span className="opacity-70">{t('docEditor.lifecycleNote')}</span>
            <Button variant="outline" size="sm" onClick={() => { void handleClose(); }}>{t('common:action.close')}</Button>
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
  const { t } = useTranslation('chat');
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
              aria-label={t('docEditor.colorAria', { label, color: c === 'transparent' ? t('docEditor.colorNone') : c })}
              title={c === 'transparent' ? t('docEditor.noHighlight') : c}
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
