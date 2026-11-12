// 次要动作收纳入口（「更多」）：头栏只留高频主操作，其余收进一个下拉。
//
// 为什么要有它（用户实拍：报告详情头栏按钮越加越多，挤到标题都看不见）：
// 每加一个能力就占一个常驻按钮是不可持续的膨胀。口径是**常驻只留主操作**，
// 低频/危险动作（编辑、删除、沉淀模板、建定时任务）收进这里，且收纳不等于藏起来：
// 项名与原来按钮文案一字不差，置灰原因照旧写进 title（能看见的入口不能点下去才知道不行）。
//
// 为什么不用 Radix DropdownMenu：与同头栏的 ReportExportMenu 同一取舍
// （jsdom 打不开 Radix 下拉的历史坑、选项少、无键盘导航需求），最小状态自绘，
// 可测、可点、不引新依赖。
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ChevronDown, MoreHorizontal } from 'lucide-react';
import { Button } from '@/components/ui/button';

export interface OverflowItem {
  key: string;
  label: string;
  icon?: ReactNode;
  /** 提示/置灰原因：与常驻按钮同口径，hover 就能看到「为什么不能点」 */
  title?: string;
  disabled?: boolean;
  /** 危险动作（删除一类）标红，和普通项之间用分隔线隔开 */
  danger?: boolean;
  separatorBefore?: boolean;
  onSelect: () => void;
}

interface Props {
  items: OverflowItem[];
  label?: string;
  triggerTitle?: string;
}

export default function ActionOverflowMenu({ items, label = '更多', triggerTitle }: Props) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDocDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // 一个次要动作都没有就别留一个空开关（点了只出一个空白框）
  if (!items.length) return null;

  return (
    <div className="relative shrink-0" ref={boxRef}>
      <Button variant="outline" size="sm" className="h-8 text-xs"
        aria-haspopup="menu" aria-expanded={open} data-testid="action-overflow-menu"
        title={triggerTitle || `更多操作（${items.map(i => i.label).join(' / ')}）`}
        onClick={() => setOpen(v => !v)}>
        <MoreHorizontal className="h-3 w-3 mr-1" /> {label}
        <ChevronDown className="h-3 w-3 ml-1 opacity-60" />
      </Button>
      {open && (
        <div role="menu" className="absolute right-0 z-20 mt-1 w-52 rounded-md border bg-popover p-1 shadow-md">
          {items.map(item => (
            <div key={item.key}>
              {item.separatorBefore && <div className="-mx-1 my-1 h-px bg-border" />}
              <button type="button" role="menuitem" disabled={item.disabled} title={item.title}
                onClick={() => { setOpen(false); item.onSelect(); }}
                className={`w-full flex items-center gap-2 rounded px-2 py-1.5 text-left text-xs
                  hover:bg-accent hover:text-accent-foreground
                  disabled:pointer-events-none disabled:opacity-50
                  ${item.danger ? 'text-destructive' : ''}`}>
                {item.icon}
                <span className="truncate">{item.label}</span>
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
