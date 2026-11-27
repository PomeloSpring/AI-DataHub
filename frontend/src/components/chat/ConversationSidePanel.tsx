/**
 * 会话右侧栏：会话技能 + 会话产物 + 关键知识。
 *
 * 为什么默认收起：正文那 25% 留白原本是留给这些信息的，但常驻占位会让长会话的阅读区
 * 变窄；用户拍板默认收起，入口在输入卡片底行（与「会话文件」同一个按钮），展开时正文列放开。
 *
 * 数据全部来自**已加载的会话消息**（见 lib/conversationInsights）与既有会话文件接口：
 * 不新造事实源、不调模型、不额外消耗 token。侧栏只做呈现，动作仍走原有落库/归档通道。
 * 「会话技能」列的是回合 meta 里的装载事实（常驻/附带/取回），不是模型自述，
 * 也不与技能**绑定清单**混为一谈：没加载的技能不进这一节（侧栏只报“真读过哪些细则”）。
 */
import { useMemo, useState } from 'react';
import {
  Archive, ArrowLeftRight, BarChart3, Blocks, BookOpen, ChevronDown, ChevronRight,
  Crosshair, FileBarChart, FileType, FolderOpen, Loader2, PanelRightClose, Sparkles,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { formatSize } from '@/lib/dataGrid';
import {
  collectArtifacts, collectKnowledge, collectSkills,
  type CitationCard, type ScopeCard, type SkillUsageCard,
} from '@/lib/conversationInsights';
import type { ChatMessage } from '@/stores/chatStore';
import SessionFilesPanel from './SessionFilesPanel';

interface Props {
  open: boolean;
  onToggle: () => void;
  workspaceId: number;
  conversationId: number | null;
  messages: ChatMessage[];
  /** 跳到产生该条目的一轮（与提问节点导航共用同一锚点口径） */
  onJumpToMessage: (idx: number) => void;
  onOpenDoc: (idx: number) => void;
  onEditDraft: (idx: number) => void;
  onArchiveDraft: (idx: number) => void;
  /** 归档进行中的消息下标（按钮转圈，避免重复提交） */
  archivingIdx: number | null;
  /** 会话文件区刷新计数（「存为工作区」等产物落盘后递增）：不依会话切换也能看到新文件 */
  filesRefreshKey?: number;
  /** 技能名 → 业务显示名（来自 `/` 候选同源清单）：侧栏不露 screen-builder 这类技术名 */
  skillDisplayNames?: Record<string, string>;
}

/** 时间戳呈现：服务端草稿用 `YYYY-MM-DD HH:mm:ss`，消息文档用 ISO —— 两种都要能读，
 *  且不靠 Date 解析非 ISO 串（Safari 会得到 Invalid Date，显示成空白）。 */
function formatStamp(value: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(value || ''));
  if (m) return `${m[2]}-${m[3]} ${m[4]}:${m[5]}`;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function SectionTitle({ icon: Icon, title, hint }: {
  icon: typeof BarChart3; title: string; hint?: string;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <Icon className="h-3.5 w-3.5 shrink-0 text-primary" />
      <span className="text-xs font-medium">{title}</span>
      {hint && <span className="ml-auto text-[10px] text-muted-foreground">{hint}</span>}
    </div>
  );
}

/** 定位到产生它的那一轮：与提问节点导航同一个锚点，不另造跳转口径。 */
function JumpButton({ idx, onJump, label }: { idx: number; onJump: (i: number) => void; label: string }) {
  return (
    <button
      type="button"
      onClick={() => onJump(idx)}
      title={label}
      aria-label={label}
      className="shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
    >
      <Crosshair className="h-3 w-3" />
    </button>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="max-w-full truncate rounded border border-border/70 bg-muted/40 px-1.5 py-0.5 text-[11px]">
      {children}
    </span>
  );
}

function SkillRow({ card, label, onJump }: { card: SkillUsageCard; label: string; onJump: (i: number) => void }) {
  const { t } = useTranslation('chat');
  const turns = card.resident + card.attached + card.fetched;
  return (
    <li className="rounded-md border border-border/60 bg-card px-2 py-1.5">
      <div className="flex items-center gap-1.5">
        <Sparkles className="h-3.5 w-3.5 shrink-0 text-primary" />
        <span className="min-w-0 flex-1 truncate text-xs font-medium" title={label}>{label}</span>
        <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
          {t('sidePanel.skillTurns', { count: turns })}
        </span>
        <JumpButton idx={card.idx} onJump={onJump} label={t('sidePanel.jumpToSkill', { name: label })} />
      </div>
      {/* 三个数各自是「回合数」：随提示词常驻 / 随工具结果附带 / 模型主动取回 */}
      <p className="mt-0.5 text-[10px] text-muted-foreground">
        {t('sidePanel.skillChannels', {
          resident: card.resident, attached: card.attached, fetched: card.fetched,
        })}
      </p>
    </li>
  );
}

/** 站点并列展示的上限：meta 行是 shrink-0，站点全量铺开会把对象名挤没（name 必须可见），
 *  超出就收成「等 N 站」，完整清单放 title。 */
const SITES_META_MAX = 3;

function ScopeRow({ card, onJump }: { card: ScopeCard; onJump: (i: number) => void }) {
  const { t } = useTranslation('chat');
  const windowMeta = card.timeWindow ? t('sidePanel.windowMeta', { value: card.timeWindow }) : '';
  const siteMetaFull = card.sites.length ? t('sidePanel.siteMeta', { value: card.sites.join('/') }) : '';
  const siteMeta = !siteMetaFull ? '' : card.sites.length > SITES_META_MAX
    ? t('sidePanel.siteMetaMore', {
      value: card.sites.slice(0, SITES_META_MAX).join('/'), count: card.sites.length,
    })
    : siteMetaFull;
  const meta = [windowMeta, siteMeta].filter(Boolean);
  // 收起后的完整口径进 title（站点清单不因展示上限而丢）
  const metaFull = [windowMeta, siteMetaFull].filter(Boolean);
  return (
    <li className="space-y-1 rounded-md border border-border/60 bg-card p-2">
      <div className="flex items-center gap-1.5">
        <span className="min-w-0 flex-1 truncate text-xs font-medium" title={card.object}>
          {card.object || t('sidePanel.unregisteredObject')}
        </span>
        {meta.length > 0 && (
          <span className="max-w-[55%] shrink-0 truncate text-[10px] text-muted-foreground"
            title={metaFull.join(' · ')}>
            {meta.join(' · ')}
          </span>
        )}
        <JumpButton idx={card.idx} onJump={onJump} label={t('sidePanel.jumpTo', { title: card.object || t('sidePanel.jumpFallbackObject') })} />
      </div>
      {card.metrics.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {card.metrics.map(n => <Chip key={`m-${n}`}><span className="text-primary/80">{t('sidePanel.metricChip')}</span> {n}</Chip>)}
        </div>
      )}
      {card.dimensions.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {card.dimensions.map(n => <Chip key={`d-${n}`}><span className="text-sky-600">{t('sidePanel.dimensionChip')}</span> {n}</Chip>)}
        </div>
      )}
      {card.filters.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {card.filters.map(f => <Chip key={`f-${f}`}>{t('sidePanel.filterChip', { value: f })}</Chip>)}
        </div>
      )}
    </li>
  );
}

function CitationRow({ card, onJump }: { card: CitationCard; onJump: (i: number) => void }) {
  const { t } = useTranslation('chat');
  // 一次展开一条出处：知识正文是排查“知识不全/错误”与学习的入口，逐条看才不刷屏
  const [openTitle, setOpenTitle] = useState<string | null>(null);
  return (
    <li className="space-y-1 rounded-md border border-border/60 bg-card p-2">
      <div className="flex items-start gap-1.5">
        <span className="min-w-0 flex-1 text-xs font-medium leading-snug" title={card.question}>
          {card.question || t('sidePanel.knowledgeFallback')}
        </span>
        <JumpButton idx={card.idx} onJump={onJump} label={t('sidePanel.jumpToCitation')} />
      </div>
      <ul className="space-y-0.5">
        {card.sources.map(s => {
          const expandable = !!s.excerpt;
          const open = openTitle === s.title;
          return (
            <li key={s.title}>
              <button
                type="button"
                disabled={!expandable}
                onClick={() => setOpenTitle(open ? null : s.title)}
                title={expandable
                  ? t(open ? 'sidePanel.citationHide' : 'sidePanel.citationShow', { title: s.title })
                  : s.title}
                className="flex w-full items-center gap-1 rounded px-0.5 py-0.5 text-left text-[11px] text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground disabled:cursor-default disabled:hover:bg-transparent disabled:hover:text-muted-foreground"
              >
                <BookOpen className="h-3 w-3 shrink-0" />
                <span className="min-w-0 flex-1 truncate">{s.title}</span>
                {expandable && (open
                  ? <ChevronDown className="h-3 w-3 shrink-0" />
                  : <ChevronRight className="h-3 w-3 shrink-0" />)}
              </button>
              {open && (
                <div className="mt-0.5 space-y-1 rounded border border-border/60 bg-muted/40 p-1.5">
                  {s.kb && (
                    <p className="text-[10px] text-muted-foreground">
                      {t('sidePanel.citationKb', { name: s.kb })}
                    </p>
                  )}
                  <p className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed">
                    {s.excerpt}
                  </p>
                  {s.clipped && (
                    <p className="text-[10px] text-muted-foreground">{t('sidePanel.citationClipped')}</p>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {/* 旧会话的摘要只存了标题：明确说正文没留存，不摆个点不开的展开键装作有内容 */}
      {card.contentMissing && (
        <p className="text-[10px] text-muted-foreground">{t('sidePanel.citationContentMissing')}</p>
      )}
      <p className="text-[10px] text-muted-foreground">{t('sidePanel.citationHits', { count: card.chunks })}</p>
    </li>
  );
}

export default function ConversationSidePanel({
  open, onToggle, workspaceId, conversationId, messages,
  onJumpToMessage, onOpenDoc, onEditDraft, onArchiveDraft, archivingIdx, filesRefreshKey,
  skillDisplayNames,
}: Props) {
  const { t } = useTranslation(['chat', 'common']);
  // hooks 必须无条件调用（`open` 提前 return 会把后面的 useMemo 变成条件 hook）
  const artifacts = useMemo(() => collectArtifacts(messages), [messages]);
  const knowledge = useMemo(() => collectKnowledge(messages), [messages]);
  const skills = useMemo(() => collectSkills(messages), [messages]);
  if (!open) return null;
  const hasDocs = artifacts.docs.length > 0;
  const hasReports = artifacts.reports.length > 0;
  // 侧栏只列引用，不展示「工具返回被截断」这类过程提示（完整内容在该轮执行过程里看）
  const hasKnowledge = knowledge.scopes.length > 0 || knowledge.citations.length > 0;

  return (
    <aside
      className="flex w-[320px] shrink-0 flex-col border-l bg-background 2xl:w-[380px]"
      aria-label={t('sidePanel.aria')}
    >
      <div className="flex items-center gap-2 border-b px-3 py-2.5">
        <PanelRightClose className="h-4 w-4 shrink-0 text-primary" />
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{t('sidePanel.title')}</h2>
        <Button
          variant="ghost" size="sm" className="h-7 w-7 shrink-0 p-0 text-muted-foreground"
          onClick={onToggle} title={t('sidePanel.collapse')} aria-label={t('sidePanel.collapse')}
        >
          <ArrowLeftRight className="h-3.5 w-3.5" />
        </Button>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
        {/* ── 会话技能 ──
            本会话每轮真正读到过哪几份作业细则（回合 meta，不是模型自述）：用户排查
            “答得不按口径”时，这里是“细则到底加载了没有”的第一手依据。 */}
        <section className="space-y-2">
          <SectionTitle icon={Blocks} title={t('sidePanel.skills')} />
          {skills.turns === 0 ? (
            <p className="text-xs text-muted-foreground">{t('sidePanel.skillsIdle')}</p>
          ) : !skills.recorded ? (
            /* 字段整份缺失 = 该功能上线前的历史会话，不得说成“没加载”（两件事不能混说） */
            <p className="text-xs text-muted-foreground">{t('sidePanel.skillsUnrecorded')}</p>
          ) : skills.cards.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t('sidePanel.skillsEmpty')}</p>
          ) : (
            <ul className="space-y-1">
              {skills.cards.map(card => (
                <SkillRow
                  key={card.name}
                  card={card}
                  label={skillDisplayNames?.[card.name] || card.name}
                  onJump={onJumpToMessage}
                />
              ))}
            </ul>
          )}
        </section>

        {/* ── 会话产物 ── */}
        <section className="space-y-2">
          <SectionTitle icon={FileBarChart} title={t('sidePanel.artifacts')} />

          <div className="space-y-1.5">
            <p className="text-[11px] text-muted-foreground">{t('sidePanel.reportDrafts')}</p>
            {!conversationId ? (
              <p className="text-xs text-muted-foreground">{t('sidePanel.reportDraftsNoConv')}</p>
            ) : hasReports ? (
              <ul className="space-y-1.5">
                {artifacts.reports.map(r => (
                  <li key={`report-${r.idx}`} className="space-y-1.5 rounded-md border border-border/60 bg-card p-2">
                    <div className="flex items-start gap-1.5">
                      <BarChart3 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-xs font-medium" title={r.title}>{r.title}</p>
                        <p className="text-[10px] text-muted-foreground">
                          {formatStamp(r.updatedAt) && t('sidePanel.updatedAt', { time: formatStamp(r.updatedAt) })}
                          {r.revisions > 0 && t('sidePanel.revisions', { count: r.revisions })}
                          {` · ${formatSize(r.size)}`}
                        </p>
                      </div>
                      <JumpButton idx={r.idx} onJump={onJumpToMessage} label={t('sidePanel.jumpTo', { title: r.title })} />
                    </div>
                    {r.reportId > 0 ? (
                      <div className="flex items-center gap-1.5 text-[11px] text-emerald-700">
                        <Archive className="h-3 w-3 shrink-0" />
                        {t('sidePanel.archivedToCenter')}
                        <a
                          className="ml-auto shrink-0 underline"
                          href={`/report/${r.reportId}`}
                          title={t('sidePanel.openInCenter')}
                        >
                          {t('sidePanel.goView')}
                        </a>
                      </div>
                    ) : (
                      <div className="flex items-center gap-1.5">
                        <Button variant="outline" size="sm" className="h-6 flex-1 text-[11px]"
                          onClick={() => onEditDraft(r.idx)}>
                          {t('sidePanel.editDraft')}
                        </Button>
                        <Button size="sm" className="h-6 flex-1 text-[11px]"
                          disabled={archivingIdx !== null}
                          title={t('sidePanel.archiveDraftTitle')}
                          onClick={() => onArchiveDraft(r.idx)}>
                          {archivingIdx === r.idx
                            ? <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                            : <Archive className="mr-1 h-3 w-3" />}
                          {t('artifact.archive')}
                        </Button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">
                {t('sidePanel.reportsEmpty')}
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <p className="text-[11px] text-muted-foreground">{t('sidePanel.docs')}</p>
            {hasDocs ? (
              <ul className="space-y-1">
                {artifacts.docs.map(d => (
                  <li key={`doc-${d.idx}`} className="flex items-center gap-1.5 rounded-md border border-border/60 bg-card px-2 py-1.5">
                    <FileType className="h-3.5 w-3.5 shrink-0 text-primary" />
                    <button
                      type="button"
                      className="min-w-0 flex-1 text-left"
                      title={t('sidePanel.openDocTitle', { title: d.title })}
                      onClick={() => onOpenDoc(d.idx)}
                    >
                      <span className="block truncate text-xs font-medium">{d.title}</span>
                      <span className="block truncate text-[10px] text-muted-foreground">
                        {formatStamp(d.updatedAt) && t('sidePanel.updatedAt', { time: formatStamp(d.updatedAt) })}
                        {d.revisions > 0 && t('sidePanel.docRevisions', { count: d.revisions })}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">{t('sidePanel.docsEmpty')}</p>
            )}
          </div>

          <div className="space-y-1.5">
            <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <FolderOpen className="h-3 w-3" />{t('sidePanel.workspaceFiles')}
            </p>
            {conversationId ? (
              <SessionFilesPanel
                workspaceId={workspaceId} conversationId={conversationId} refreshKey={filesRefreshKey}
              />
            ) : (
              <p className="text-xs text-muted-foreground">{t('sidePanel.filesEmpty')}</p>
            )}
          </div>
        </section>

        {/* ── 关键知识 ── */}
        <section className="space-y-2">
          <SectionTitle icon={Sparkles} title={t('sidePanel.knowledge')} />
          {!hasKnowledge ? (
            <p className="text-xs text-muted-foreground">
              {t('sidePanel.knowledgeEmpty')}
            </p>
          ) : (
            <>
              {knowledge.scopes.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-[11px] text-muted-foreground">{t('sidePanel.scopes')}</p>
                  <ul className="space-y-1.5">
                    {knowledge.scopes.map((s, i) => (
                      <ScopeRow key={`scope-${s.idx}-${i}`} card={s} onJump={onJumpToMessage} />
                    ))}
                  </ul>
                </div>
              )}
              {knowledge.citations.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-[11px] text-muted-foreground">{t('sidePanel.citations')}</p>
                  <ul className="space-y-1.5">
                    {knowledge.citations.map((ct, i) => (
                      <CitationRow key={`cit-${ct.idx}-${i}`} card={ct} onJump={onJumpToMessage} />
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </aside>
  );
}
