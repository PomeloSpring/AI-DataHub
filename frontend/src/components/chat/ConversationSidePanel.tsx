/**
 * 会话右侧栏：会话产物 + 关键知识。
 *
 * 为什么默认收起：正文那 25% 留白原本是留给这些信息的，但常驻占位会让长会话的阅读区
 * 变窄；用户拍板默认收起，入口在输入卡片底行（与「会话文件」同一个按钮），展开时正文列放开。
 *
 * 数据全部来自**已加载的会话消息**（见 lib/conversationInsights）与既有会话文件接口：
 * 不新造事实源、不调模型、不额外消耗 token。侧栏只做呈现，动作仍走原有落库/归档通道。
 */
import { useMemo } from 'react';
import {
  Archive, ArrowLeftRight, BarChart3, BookOpen, Crosshair, FileBarChart, FileType,
  FolderOpen, Loader2, PanelRightClose, Sparkles,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { formatSize } from '@/lib/dataGrid';
import {
  collectArtifacts, collectKnowledge,
  type CitationCard, type ScopeCard,
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

function ScopeRow({ card, onJump }: { card: ScopeCard; onJump: (i: number) => void }) {
  const meta = [
    card.timeWindow ? `窗口 ${card.timeWindow}` : '',
    card.site ? `站点 ${card.site}` : '',
  ].filter(Boolean);
  return (
    <li className="space-y-1 rounded-md border border-border/60 bg-card p-2">
      <div className="flex items-center gap-1.5">
        <span className="min-w-0 flex-1 truncate text-xs font-medium" title={card.object}>
          {card.object || '未登记对象'}
        </span>
        {meta.length > 0 && (
          <span className="shrink-0 text-[10px] text-muted-foreground">{meta.join(' · ')}</span>
        )}
        <JumpButton idx={card.idx} onJump={onJump} label={`定位到「${card.object || '该查询'}」那一轮`} />
      </div>
      {card.metrics.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {card.metrics.map(n => <Chip key={`m-${n}`}><span className="text-primary/80">指标</span> {n}</Chip>)}
        </div>
      )}
      {card.dimensions.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {card.dimensions.map(n => <Chip key={`d-${n}`}><span className="text-sky-600">维度</span> {n}</Chip>)}
        </div>
      )}
      {card.filters.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {card.filters.map(f => <Chip key={`f-${f}`}>过滤 {f}</Chip>)}
        </div>
      )}
    </li>
  );
}

function CitationRow({ card, onJump }: { card: CitationCard; onJump: (i: number) => void }) {
  return (
    <li className="space-y-1 rounded-md border border-border/60 bg-card p-2">
      <div className="flex items-start gap-1.5">
        <span className="min-w-0 flex-1 text-xs font-medium leading-snug" title={card.question}>
          {card.question || '知识检索'}
        </span>
        <JumpButton idx={card.idx} onJump={onJump} label="定位到该次检索" />
      </div>
      {/* 只到出处标题：片段正文含物理表名（内部 md），侧栏不把它当"业务卡片"端出来 */}
      <ul className="space-y-0.5">
        {card.sources.map(s => (
          <li key={s} className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <BookOpen className="h-3 w-3 shrink-0" />
            <span className="min-w-0 truncate" title={s}>{s}</span>
          </li>
        ))}
      </ul>
      <p className="text-[10px] text-muted-foreground">命中 {card.chunks} 段 · 原文见该轮执行过程</p>
    </li>
  );
}

export default function ConversationSidePanel({
  open, onToggle, workspaceId, conversationId, messages,
  onJumpToMessage, onOpenDoc, onEditDraft, onArchiveDraft, archivingIdx, filesRefreshKey,
}: Props) {
  // hooks 必须无条件调用（`open` 提前 return 会把后面的 useMemo 变成条件 hook）
  const artifacts = useMemo(() => collectArtifacts(messages), [messages]);
  const knowledge = useMemo(() => collectKnowledge(messages), [messages]);
  if (!open) return null;
  const hasDocs = artifacts.docs.length > 0;
  const hasReports = artifacts.reports.length > 0;
  const hasKnowledge = knowledge.scopes.length > 0
    || knowledge.citations.length > 0 || knowledge.unparsed > 0;

  return (
    <aside
      className="flex w-[320px] shrink-0 flex-col border-l bg-background 2xl:w-[380px]"
      aria-label="会话侧栏"
    >
      <div className="flex items-center gap-2 border-b px-3 py-2.5">
        <PanelRightClose className="h-4 w-4 shrink-0 text-primary" />
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">会话侧栏</h2>
        <Button
          variant="ghost" size="sm" className="h-7 w-7 shrink-0 p-0 text-muted-foreground"
          onClick={onToggle} title="收起侧栏" aria-label="收起侧栏"
        >
          <ArrowLeftRight className="h-3.5 w-3.5" />
        </Button>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
        {/* ── 会话产物 ── */}
        <section className="space-y-2">
          <SectionTitle icon={FileBarChart} title="会话产物" />

          <div className="space-y-1.5">
            <p className="text-[11px] text-muted-foreground">报告草稿</p>
            {!conversationId ? (
              <p className="text-xs text-muted-foreground">发送消息并落库后，可在回答下方「存为报告」生成草稿。</p>
            ) : hasReports ? (
              <ul className="space-y-1.5">
                {artifacts.reports.map(r => (
                  <li key={`report-${r.idx}`} className="space-y-1.5 rounded-md border border-border/60 bg-card p-2">
                    <div className="flex items-start gap-1.5">
                      <BarChart3 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-xs font-medium" title={r.title}>{r.title}</p>
                        <p className="text-[10px] text-muted-foreground">
                          {formatStamp(r.updatedAt) && `更新于 ${formatStamp(r.updatedAt)}`}
                          {r.revisions > 0 && ` · 已改 ${r.revisions} 次`}
                          {` · ${formatSize(r.size)}`}
                        </p>
                      </div>
                      <JumpButton idx={r.idx} onJump={onJumpToMessage} label={`定位到「${r.title}」那一轮`} />
                    </div>
                    {r.reportId > 0 ? (
                      <div className="flex items-center gap-1.5 text-[11px] text-emerald-700">
                        <Archive className="h-3 w-3 shrink-0" />
                        已归档进报告中心
                        <a
                          className="ml-auto shrink-0 underline"
                          href={`/report/${r.reportId}`}
                          title="在报告中心打开"
                        >
                          去查看
                        </a>
                      </div>
                    ) : (
                      <div className="flex items-center gap-1.5">
                        <Button variant="outline" size="sm" className="h-6 flex-1 text-[11px]"
                          onClick={() => onEditDraft(r.idx)}>
                          编辑草稿
                        </Button>
                        <Button size="sm" className="h-6 flex-1 text-[11px]"
                          disabled={archivingIdx !== null}
                          title="草稿定稿后进报告中心；进中心前它只属于本会话"
                          onClick={() => onArchiveDraft(r.idx)}>
                          {archivingIdx === r.idx
                            ? <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                            : <Archive className="mr-1 h-3 w-3" />}
                          归档
                        </Button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">
                还没有报告草稿。分析结果下方「更多 → 存为报告」可生成草稿，改完再归档进报告中心。
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <p className="text-[11px] text-muted-foreground">文档</p>
            {hasDocs ? (
              <ul className="space-y-1">
                {artifacts.docs.map(d => (
                  <li key={`doc-${d.idx}`} className="flex items-center gap-1.5 rounded-md border border-border/60 bg-card px-2 py-1.5">
                    <FileType className="h-3.5 w-3.5 shrink-0 text-primary" />
                    <button
                      type="button"
                      className="min-w-0 flex-1 text-left"
                      title={`打开编辑：${d.title}`}
                      onClick={() => onOpenDoc(d.idx)}
                    >
                      <span className="block truncate text-xs font-medium">{d.title}</span>
                      <span className="block truncate text-[10px] text-muted-foreground">
                        {formatStamp(d.updatedAt) && `更新于 ${formatStamp(d.updatedAt)}`}
                        {d.revisions > 0 && ` · 已存 ${d.revisions} 次`}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">还没有文档。回答下方「更多 → 转文档编辑」可把该轮结论转成可编辑文档。</p>
            )}
          </div>

          <div className="space-y-1.5">
            <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <FolderOpen className="h-3 w-3" />会话工作区文件
            </p>
            {conversationId ? (
              <SessionFilesPanel
                workspaceId={workspaceId} conversationId={conversationId} refreshKey={filesRefreshKey}
              />
            ) : (
              <p className="text-xs text-muted-foreground">发送消息后可在这里预览、下载与归档本轮产物。</p>
            )}
          </div>
        </section>

        {/* ── 关键知识 ── */}
        <section className="space-y-2">
          <SectionTitle icon={Sparkles} title="关键知识" />
          {!hasKnowledge ? (
            <p className="text-xs text-muted-foreground">
              本轮会话还没有可摘取的知识。助手检索口径、查目录或查知识库后，这里会列出用到的对象/指标/维度与出处。
            </p>
          ) : (
            <>
              {knowledge.scopes.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-[11px] text-muted-foreground">本次口径</p>
                  <ul className="space-y-1.5">
                    {knowledge.scopes.map((s, i) => (
                      <ScopeRow key={`scope-${s.idx}-${i}`} card={s} onJump={onJumpToMessage} />
                    ))}
                  </ul>
                </div>
              )}
              {knowledge.citations.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-[11px] text-muted-foreground">知识引用出处</p>
                  <ul className="space-y-1.5">
                    {knowledge.citations.map((ct, i) => (
                      <CitationRow key={`cit-${ct.idx}-${i}`} card={ct} onJump={onJumpToMessage} />
                    ))}
                  </ul>
                </div>
              )}
              {/* 截断不是“没有知识”：结果过长时结构化失败必须说出来（no-silent-degradation） */}
              {knowledge.unparsed > 0 && (
                <p role="alert" className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-[11px] text-amber-700">
                  有 {knowledge.unparsed} 段工具返回过长被截断，未能整理成卡片；完整内容在该轮的执行过程里查看。
                </p>
              )}
            </>
          )}
        </section>
      </div>
    </aside>
  );
}
