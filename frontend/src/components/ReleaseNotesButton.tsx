import { useEffect, useMemo, useState } from 'react';
import { Megaphone, AlertTriangle, Compass } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { noteKey, type SectionKind } from '@/api/releaseNotes';
import { TOUR } from '@/lib/onboarding/guides';
import { selectUnreadNotes, useGuideStore } from '@/stores/guideStore';

/**
 * 顶栏版本更新公告按钮(放在模块切换左侧, 四套 header 同一组件)。
 *
 * 未读公告 → 右上角红点(带数量); 加载失败 → 图标转告警态并可在面板内重试,
 * 不静默退化成"没有公告"(见 no-silent-degradation)。
 */

const SECTION_LABELS: Record<SectionKind, string> = {
  feature: '新增',
  improvement: '优化',
  fix: '修复',
  other: '其他',
};

/** 分组展示顺序; 未知 kind 归到「其他」并保留原始名(后端已回填 raw_kind) */
const SECTION_ORDER: SectionKind[] = ['feature', 'improvement', 'fix', 'other'];

export function ReleaseNotesDialog({ open, onOpenChange }: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const notes = useGuideStore(s => s.notes);
  const states = useGuideStore(s => s.states);
  const loading = useGuideStore(s => s.loading);
  const error = useGuideStore(s => s.error);
  const load = useGuideStore(s => s.load);
  const markAllNotesRead = useGuideStore(s => s.markAllNotesRead);
  const startGuide = useGuideStore(s => s.startGuide);

  const unread = useMemo(() => selectUnreadNotes({ notes, states }), [notes, states]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const active = notes.find(n => n.id === activeId) || notes[0] || null;

  // 面板内可滚动切换版本: 打开时回到最新一版(不沿用上次选中的旧版本)
  useEffect(() => { if (open) setActiveId(null); }, [open]);

  const close = () => {
    // 关闭即标已读: 看没看完都算处理过这条公告, 否则红点永远挂着
    void markAllNotesRead();
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={v => { if (!v) close(); else onOpenChange(v); }}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Megaphone className="h-4 w-4 text-primary" />
            版本更新公告
            {!!unread.length && (
              <Badge variant="secondary" className="ml-1">{unread.length} 条未读</Badge>
            )}
          </DialogTitle>
          <DialogDescription>这里记录每次版本发了什么功能与更新项，由管理员维护。</DialogDescription>
        </DialogHeader>

        {error ? (
          <div className="flex flex-col items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
            <span className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              公告加载失败：{error}
            </span>
            <Button variant="outline" size="sm" onClick={() => void load(true)}>重试</Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[220px_1fr]">
            <div className="min-w-0">
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-xs font-medium text-muted-foreground">全部版本</span>
                {!!unread.length && (
                  <button
                    type="button" className="text-xs text-primary hover:underline"
                    onClick={() => void markAllNotesRead()}
                  >
                    全部标记已读
                  </button>
                )}
              </div>
              <ScrollArea className="h-[320px] rounded-lg border border-border/70 pr-1">
                <div className="flex flex-col gap-1 p-1.5">
                  {loading && !notes.length && <div className="p-2 text-xs text-muted-foreground">加载中…</div>}
                  {!loading && !notes.length && (
                    <div className="p-2 text-xs text-muted-foreground">还没有已发布的公告</div>
                  )}
                  {notes.map(n => (
                    <button
                      key={n.id} type="button" onClick={() => setActiveId(n.id)}
                      className={`rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-accent/60 ${
                        active?.id === n.id ? 'bg-accent text-accent-foreground' : ''
                      }`}
                    >
                      <span className="flex items-center gap-1.5">
                        <span className="font-medium">v{n.version}</span>
                        {!states[noteKey(n.version)] && (
                          <span aria-label="未读" className="h-1.5 w-1.5 shrink-0 rounded-full bg-destructive" />
                        )}
                      </span>
                      <span className="mt-0.5 block truncate text-muted-foreground">{n.title}</span>
                    </button>
                  ))}
                </div>
              </ScrollArea>
            </div>

            <div className="min-w-0">
              {!active && <div className="p-2 text-sm text-muted-foreground">选择左侧版本查看更新内容</div>}
              {active && (
                <div className="flex h-[320px] flex-col gap-3 overflow-y-auto rounded-lg border border-border/70 p-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="secondary">v{active.version}</Badge>
                      {!!active.is_major && <Badge>重大版本</Badge>}
                      {active.published_at && (
                        <span className="text-xs text-muted-foreground">
                          {String(active.published_at).slice(0, 10)}
                        </span>
                      )}
                    </div>
                    <h3 className="mt-2 text-sm font-semibold">{active.title}</h3>
                    {!!active.summary && (
                      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{active.summary}</p>
                    )}
                  </div>

                  {SECTION_ORDER.map(kind => {
                    const section = active.sections.find(s => s.kind === kind);
                    if (!section || !section.items.length) return null;
                    const label = kind === 'other' && section.raw_kind
                      ? `其他（${section.raw_kind}）`
                      : SECTION_LABELS[kind];
                    return (
                      <div key={kind}>
                        <div className="mb-1 text-xs font-medium text-foreground">{label}</div>
                        <ul className="space-y-1 text-xs text-muted-foreground">
                          {section.items.map((it, i) => (
                            <li key={i} className="flex gap-1.5">
                              <span className="shrink-0">·</span>
                              <span className="min-w-0 break-words">{it}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  })}

                  {!!active.guide_key && (
                    <div className="mt-auto border-t border-border/60 pt-2">
                      <Button
                        variant="outline" size="sm"
                        // 显式动作: 即使 guide:all 已静音也要能从这里启动
                        onClick={() => { void markAllNotesRead(); onOpenChange(false); startGuide(active.guide_key); }}
                      >
                        <Compass className="mr-1.5 h-4 w-4" />查看新功能引导
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function ReleaseNotesButton() {
  const notes = useGuideStore(s => s.notes);
  const states = useGuideStore(s => s.states);
  const error = useGuideStore(s => s.error);
  const load = useGuideStore(s => s.load);
  const unreadCount = useMemo(
    () => selectUnreadNotes({ notes, states }).length,
    [notes, states],
  );
  const [open, setOpen] = useState(false);

  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost" size="sm"
            className="relative h-9 w-9 p-0"
            aria-label="版本更新公告"
            data-tour={TOUR.headerReleaseNotes}
            data-testid="release-notes-button"
            onClick={() => { void load(); setOpen(true); }}
          >
            {error
              ? <AlertTriangle className="h-4 w-4 text-destructive" />
              : <Megaphone className="h-4 w-4" />}
            {unreadCount > 0 && (
              <span
                data-testid="release-notes-dot"
                className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-medium leading-none text-destructive-foreground"
              >
                {unreadCount > 9 ? '9+' : unreadCount}
              </span>
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{error ? '公告加载失败' : '版本更新公告'}</TooltipContent>
      </Tooltip>
      <ReleaseNotesDialog open={open} onOpenChange={setOpen} />
    </>
  );
}
