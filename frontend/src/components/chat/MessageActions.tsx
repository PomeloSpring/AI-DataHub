/**
 * 消息操作行（参照千问网页端）：复制 / 复制为Markdown + 赞踩 + 更多（转文档编辑、存为报告草稿）。
 *
 * 布局约束：一条回答只有**一行操作**且全部左靠（不得“图标在左、反馈在右”分两端，
 * 也不得另起一行）；不是每轮都有的入口（转文档编辑需有正文、存为报告需服务端判 reportable）
 * 一律收进 `⋯` 菜单，不进主行。
 *
 * 两种复制形态语义不同，必须分开给：
 * - 复制为Markdown：模型原始输出（粘到文档/代码库里仍是原文）；
 * - 复制：用户读到的纯文本（去掉标记语法，图表等特殊块给可读占位）。
 * 默认点击复制图标走"复制"（多数人要的），下拉里再给 Markdown 形态。
 *
 * 只做展示与剪贴板/文档动作，不碰取数与权限：正文本来就已在页面上呈现。
 */
import { useState } from 'react';
import {
  Check, ChevronDown, Copy, FileBarChart, FileText, Loader2, MoreHorizontal,
  SquarePen, ThumbsDown, ThumbsUp,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { toast } from '@/lib/toast';
import { copyPlainText } from '@/lib/clipboard';
import { markdownToPlainText } from '@/lib/markdownText';
import type { ChatDoc } from '@/stores/chatStore';

interface Props {
  /** 回答正文（markdown 原样） */
  markdown: string;
  /** 助手回答才可转文档；用户气泡只给复制 */
  allowDoc?: boolean;
  doc?: ChatDoc | null;
  /** 正在生成文档（转文档编辑的落库请求进行中） */
  docBusy?: boolean;
  /** 流式进行中不给操作：内容还没定稿，复制出去是半截答案 */
  disabled?: boolean;
  onCreateDoc?: () => void;
  onOpenDoc?: () => void;
  /** 赞踩（结果准确?）：与复制同一行；value 有值时只展示已反馈标注 */
  feedback?: { value?: 'up' | 'down'; onUp: () => void; onDown: () => void };
  /** 存为报告：只在服务端判定本轮 reportable 时给，且收进「更多」菜单 */
  report?: { busy?: boolean; onSave: () => void; label?: string };
}

const ICON_BTN = 'h-7 w-7 p-0 text-muted-foreground hover:text-foreground';

export default function MessageActions({
  markdown, allowDoc = false, doc, docBusy = false, disabled = false, onCreateDoc, onOpenDoc,
  feedback, report,
}: Props) {
  const [copied, setCopied] = useState<'plain' | 'md' | ''>('');
  const hasText = !!markdown.trim();

  const flash = (kind: 'plain' | 'md') => {
    setCopied(kind);
    window.setTimeout(() => setCopied(prev => (prev === kind ? '' : prev)), 1500);
  };

  const doCopy = async (kind: 'plain' | 'md') => {
    if (!hasText || disabled) return;
    const payload = kind === 'md' ? markdown : markdownToPlainText(markdown);
    if (!payload.trim()) {
      // 特殊块之外的正文为空（如整条只有一个看板设计卡片）：说清楚没什么可复制，
      // 而不是"已复制"却剪贴板是空的
      toast.info('这条回答没有可复制的文本内容');
      return;
    }
    try {
      await copyPlainText(payload);
      flash(kind);
      toast.success(kind === 'md' ? '已复制为 Markdown' : '已复制');
    } catch (e: any) {
      toast.error(e?.message || '复制失败，请手动选择文本复制');
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-1 mt-2">
      <div className="flex items-center rounded-md border border-transparent hover:border-border/60 hover:bg-muted transition-colors">
        <Button
          variant="ghost" size="sm" className={`${ICON_BTN} rounded-r-none`}
          disabled={!hasText || disabled}
          aria-label="复制回答文本"
          title={disabled ? '回答结束后才能复制' : '复制'}
          onClick={() => { void doCopy('plain'); }}
        >
          {copied === 'plain' ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost" size="sm" className={`${ICON_BTN} w-5 rounded-l-none`}
              disabled={!hasText || disabled}
              aria-label="复制方式"
              title={disabled ? '回答结束后才能复制' : '复制方式'}
            >
              <ChevronDown className="h-3 w-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-40">
            <DropdownMenuItem onClick={() => { void doCopy('md'); }}>
              <FileText className="mr-2 h-4 w-4" />
              复制为Markdown
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => { void doCopy('plain'); }}>
              <Copy className="mr-2 h-4 w-4" />
              复制
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* 赞踩与复制同一行（不分两端）：点踩不是打分，是打开归因区的入口 */}
      {feedback && (feedback.value ? (
        <span className="px-1 text-xs text-muted-foreground">
          {feedback.value === 'up' ? '👍 已反馈' : '👎 已反馈'}
        </span>
      ) : (
        <>
          <Button variant="ghost" size="sm" className={ICON_BTN}
            aria-label="结果准确？满意"
            title="结果准确？满意"
            disabled={disabled}
            onClick={feedback.onUp}
          >
            <ThumbsUp className="h-3.5 w-3.5" />
          </Button>
          <Button variant="ghost" size="sm" className={ICON_BTN}
            aria-label="结果准确？不满意"
            title="结果准确？不满意，可补充期望的业务指标/维度"
            disabled={disabled}
            onClick={feedback.onDown}
          >
            <ThumbsDown className="h-3.5 w-3.5" />
          </Button>
        </>
      ))}

      {(allowDoc || report) && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className={`${ICON_BTN} rounded-md`}
              disabled={disabled || docBusy}
              aria-label="更多操作"
              title={disabled ? '回答结束后可用' : '更多'}
            >
              {docBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MoreHorizontal className="h-3.5 w-3.5" />}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-40">
            {allowDoc && (doc ? (
              <DropdownMenuItem onClick={onOpenDoc}>
                <SquarePen className="mr-2 h-4 w-4" />
                继续编辑文档
              </DropdownMenuItem>
            ) : (
              <DropdownMenuItem onClick={onCreateDoc}>
                <SquarePen className="mr-2 h-4 w-4" />
                转文档编辑
              </DropdownMenuItem>
            ))}
            {/* 只有真产出过数据/分析的回合才给（服务端 reportable 判定）：
                纯问答/系统操作确认不是报告，不进菜单（否则报告中心会堆满无意义条目）。
                菜单文案跟服务端分段语义对齐：这里只生成会话内草稿，归档才进报告中心。 */}
            {report && (
              <DropdownMenuItem disabled={report.busy} onClick={report.onSave}>
                {report.busy
                  ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  : <FileBarChart className="mr-2 h-4 w-4" />}
                {report.label || '存为报告草稿'}
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
}
