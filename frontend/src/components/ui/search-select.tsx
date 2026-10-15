import * as React from "react"
import { Check, ChevronsUpDown, RefreshCw } from "lucide-react"
import { Popover, PopoverContent, PopoverTrigger } from "./popover"
import { Input } from "./input"
import { Button } from "./button"
import { cn } from "@/lib/utils"

export interface SearchSelectOption {
  value: string
  /** 展示名(默认取 value) */
  label?: string
  /** 次要说明(注释/类型等), 参与搜索匹配 */
  description?: string
}

interface SearchSelectProps {
  value: string
  onValueChange: (v: string) => void
  options: SearchSelectOption[]
  placeholder?: string
  searchPlaceholder?: string
  /** 无候选(非加载/非错误)时的文案 */
  emptyText?: string
  loading?: boolean
  /** 加载失败原因; 必须显式展示, 不得伪装成"无候选"(no-silent-degradation) */
  error?: string | null
  onRetry?: () => void
  disabled?: boolean
  className?: string
  ariaLabel?: string
}

const MAX_VISIBLE = 200

/**
 * 选择搜索框: 从资源清单中搜索+点选, 不手填内部名称
 * (ui-resource-display §2: 一切从集合中选一个的输入必须用选择框)。
 * 数据源/表/列/角色属性等可枚举资源的录入一律用它。
 */
export function SearchSelect({
  value, onValueChange, options,
  placeholder = "搜索并选择", searchPlaceholder = "输入关键词过滤…",
  emptyText = "暂无可选项",
  loading = false, error = null, onRetry,
  disabled = false, className, ariaLabel,
}: SearchSelectProps) {
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const searchRef = React.useRef<HTMLInputElement>(null);

  const selected = options.find(o => o.value === value);
  const display = value
    ? (selected?.label ?? value) + (selected ? "" : "（不在清单中）")
    : "";

  const q = query.trim().toLowerCase();
  const filtered = q
    ? options.filter(o =>
        o.value.toLowerCase().includes(q)
        || (o.label || "").toLowerCase().includes(q)
        || (o.description || "").toLowerCase().includes(q))
    : options;
  const visible = filtered.slice(0, MAX_VISIBLE);

  const pick = (v: string) => {
    onValueChange(v);
    setOpen(false);
    setQuery("");
  };

  return (
    <Popover open={open} onOpenChange={o => { setOpen(o); if (!o) setQuery(""); }}>
      <PopoverTrigger asChild disabled={disabled}>
        <button
          type="button"
          role="combobox"
          aria-expanded={open}
          aria-label={ariaLabel}
          className={cn(
            "flex h-9 w-full items-center justify-between gap-2 rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm transition-colors hover:bg-muted/50 disabled:cursor-not-allowed disabled:opacity-50",
            className
          )}
        >
          <span className={cn("truncate text-left", !display && "text-muted-foreground")}>
            {display || placeholder}
          </span>
          <ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[var(--radix-popover-trigger-width)] min-w-[240px] p-2"
        onOpenAutoFocus={e => {
          e.preventDefault();
          searchRef.current?.focus();
        }}
      >
        <Input
          ref={searchRef}
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder={searchPlaceholder}
          className="mb-2 h-8 text-sm"
          onKeyDown={e => {
            if (e.key === "Enter" && visible.length > 0) { e.preventDefault(); pick(visible[0].value); }
          }}
        />
        {loading ? (
          <div className="py-4 text-center text-sm text-muted-foreground">加载中…</div>
        ) : error ? (
          <div className="space-y-2 py-2">
            <p className="text-sm text-destructive">{error}</p>
            {onRetry && (
              <Button variant="outline" size="sm" className="h-7 text-xs" onClick={onRetry}>
                <RefreshCw className="h-3 w-3 mr-1" />重试
              </Button>
            )}
          </div>
        ) : visible.length === 0 ? (
          <div className="py-4 text-center text-sm text-muted-foreground">
            {q ? "无匹配项" : emptyText}
          </div>
        ) : (
          <div className="max-h-60 overflow-y-auto">
            {visible.map(o => (
              <button
                key={o.value}
                type="button"
                role="option"
                aria-selected={o.value === value}
                onClick={() => pick(o.value)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground",
                  o.value === value && "bg-accent/60"
                )}
              >
                <div className="flex-1 min-w-0">
                  <div className="truncate">{o.label || o.value}</div>
                  {o.description && (
                    <div className="truncate text-xs text-muted-foreground">{o.description}</div>
                  )}
                </div>
                <Check className={cn("h-4 w-4 shrink-0", o.value === value ? "opacity-100" : "opacity-0")} />
              </button>
            ))}
            {filtered.length > MAX_VISIBLE && (
              <p className="px-2 py-1 text-xs text-muted-foreground">
                共 {filtered.length} 项，仅显示前 {MAX_VISIBLE} 项，请继续输入缩小范围
              </p>
            )}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
