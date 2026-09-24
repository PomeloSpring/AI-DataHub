/**
 * PageHelp — 页面级「?」帮助说明按钮(统一样式).
 *
 * 挂在页面标题旁, Popover 展示: 本页是什么 / 数据存哪 / 被谁消费 / 与相邻模块的关系。
 * 语义层各页面(术语/标签/指标/维度/本体)职责相邻易混, 说明文案统一在此维护,
 * 帮助业务同学建立"字典 → 本体 → 取数"的心智模型。
 */
import { HelpCircle } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';

export interface HelpSection {
  title: string;
  body: string;
}

interface PageHelpProps {
  /** 一句话定位, 加粗展示 */
  summary: string;
  /** 分节说明: 是什么/数据流向/消费方/与相邻模块区别 等 */
  sections: HelpSection[];
  className?: string;
}

export default function PageHelp({ summary, sections, className }: PageHelpProps) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label="本页说明"
          title="这个页面是做什么的？"
          className={`inline-flex h-5 w-5 items-center justify-center rounded-full border border-muted-foreground/30 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors align-middle ${className || ''}`}
        >
          <HelpCircle className="h-3.5 w-3.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-96 max-w-[90vw] text-sm">
        <div className="space-y-2.5">
          <p className="font-medium leading-snug">{summary}</p>
          {sections.map((s) => (
            <div key={s.title}>
              <p className="text-xs font-semibold text-muted-foreground mb-0.5">{s.title}</p>
              <p className="text-xs leading-relaxed text-foreground/80 whitespace-pre-line">{s.body}</p>
            </div>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
