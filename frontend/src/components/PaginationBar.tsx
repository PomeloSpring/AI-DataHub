import * as React from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';

/** 全站默认每页条数档位（默认 20，可调 50 / 100） */
export const PAGE_SIZE_OPTIONS = [20, 50, 100];
export const DEFAULT_PAGE_SIZE = 20;

/**
 * 列表页统一分页条（全站唯一入口）
 *
 * 契约：
 * - 静态底栏：作为列表尾部的普通块，**不悬浮**（不再 sticky 钉在视口底边压住表格行——
 *   历史缺陷：sticky bottom-0 在整页滚动的列表页会把底栏浮在行上，看着像盖住数据）；
 *   不透明背景 + 上边框作为分隔；
 * - 需要「表头固定 + 底栏固定 + 中间滚动」的页面：把本组件放进**有界滚动容器之外**作为
 *   flex 尾栏（见敏感数据管理/表&字段：卡片 flex-col + 表体 flex-1 overflow-auto + 本条 shrink-0）；
 * - 版式：左侧「第 X / Y 页 · 共 N 条」+ 每页条数选择器（传入 onPageSizeChange 才显示），
 *   右侧「上一页 / 下一页」，边界禁用；
 * - 默认每页 20 条，可选 20 / 50 / 100（PAGE_SIZE_OPTIONS）；
 * - 单页时默认仍渲染（结构稳定），hideWhenSingle 可关掉。
 */
export interface PaginationBarProps {
  /** 当前页（1-based） */
  page: number;
  /** 每页条数 */
  pageSize: number;
  /** 总条数 */
  total: number;
  /** 翻页回调 */
  onChange: (page: number) => void;
  /** 改每页条数回调：提供则显示选择器（改后调用方应重置到第 1 页并重新拉取） */
  onPageSizeChange?: (size: number) => void;
  /** 每页条数候选（默认 [20,50,100]） */
  pageSizeOptions?: number[];
  /** 加载中标记：禁用按钮，避免连点 */
  busy?: boolean;
  /** 只有 1 页时是否隐藏（默认 false） */
  hideWhenSingle?: boolean;
  /** 是否显示「共 N 条」（默认 true） */
  showTotal?: boolean;
  /** 覆盖左侧文案（默认「第 X / Y 页」） */
  leftText?: React.ReactNode;
  /** 额外类名（打破父级 padding，如 `-mx-6 -mb-6 px-6`） */
  className?: string;
}

export function PaginationBar({
  page,
  pageSize,
  total,
  onChange,
  onPageSizeChange,
  pageSizeOptions = PAGE_SIZE_OPTIONS,
  busy,
  hideWhenSingle,
  showTotal = true,
  leftText,
  className,
}: PaginationBarProps) {
  const totalPages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)));
  if (hideWhenSingle && totalPages <= 1) return null;

  const canPrev = !busy && page > 1;
  const canNext = !busy && page < totalPages;

  // 保证当前 pageSize 即使不在候选里也能正确回显（如历史页用 50）
  const sizeOptions = pageSizeOptions.includes(pageSize)
    ? pageSizeOptions
    : [...pageSizeOptions, pageSize].sort((a, b) => a - b);

  return (
    <div
      data-slot="pagination-bar"
      className={cn(
        // 静态底栏：不悬浮（不 sticky），作为列表尾部/卡片尾栏随内容流式排布
        'flex items-center justify-between gap-3',
        // 不透明背景 + 上边框：作为与表格/内容的分隔
        'border-t bg-background px-4 py-2.5',
        'text-sm text-muted-foreground',
        className,
      )}
    >
      <div className="flex items-center gap-3">
        <span className="tabular-nums">
          {leftText ?? (
            <>
              第 {Math.min(page, totalPages)} / {totalPages} 页
              {showTotal ? ` · 共 ${total} 条` : ''}
            </>
          )}
        </span>
        {onPageSizeChange && (
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-muted-foreground">每页</span>
            <Select
              value={String(pageSize)}
              disabled={busy}
              onValueChange={(v) => onPageSizeChange(Number(v))}
            >
              <SelectTrigger className="h-7 w-[72px] text-xs" aria-label="每页条数">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {sizeOptions.map((n) => (
                  <SelectItem key={n} value={String(n)} className="text-xs">{n} 条</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!canPrev}
          onClick={() => canPrev && onChange(page - 1)}
        >
          上一页
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!canNext}
          onClick={() => canNext && onChange(page + 1)}
        >
          下一页
        </Button>
      </div>
    </div>
  );
}

export default PaginationBar;
