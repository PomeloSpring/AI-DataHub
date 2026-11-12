import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { usePermissionStore } from '@/stores/permissionStore';
import {
  contentApi, contentError,
  STATUS_LABELS, STATUS_TONES,
  type ArticleStatus, type ContentArticle, type ContentCategory, type ContentTag, type GateItem,
  type KbSyncState,
} from '@/api/content';

/**
 * 内容中心各页共用的小件。
 *
 * 抽这一层的唯一理由是**同一判据不能被写五遍**：状态徽章若在每个页面各自推断，
 * 「已发布但 head 又改了」这种双指针组合态一定会在某页被简化成只看 status，
 * 于是同一篇内容在门户显示「已发布」、在管理页显示「草稿」，用户以为版本错乱。
 */

// ── 权限 ──────────────────────────────────────────────────────────────────────

/**
 * 内容中心及其知识运营子页的功能码。
 *
 * `kbdoc:*` 属“知识库文档”（看/改云端 notebook 里的语料），与 `article:*`（门户内容
 * 创作→审核→发布）是两套权限：一个改的是 AS-BOT 的知识，一个改的是对外发布物。
 */
export type ContentPerm =
  | 'article:read' | 'article:manage' | 'article:review' | 'article:publish'
  | 'kbdoc:read' | 'kbdoc:write';

/**
 * 功能权限码（决定按钮给不给）。
 *
 * 显隐真值源仍是服务端：这里只用来藏起注定 403 的入口，**不**当作裁决。
 * 所以每个写动作都要把后端返回的 detail 原样显示出来（见 ErrorBanner），
 * 不能因为"前端已经判过权限"就把 403 静默掉。
 */
export function useContentPerms() {
  const perms = usePermissionStore(s => s.perms);
  const unrestricted = usePermissionStore(s => s.unrestricted);
  const loaded = usePermissionStore(s => s.loaded);
  const has = useCallback((code: ContentPerm) => {
    if (!loaded) return false;
    if (unrestricted) return true;
    return (perms ?? []).includes(code);
  }, [loaded, unrestricted, perms]);
  return { has, loaded };
}

// ── 状态徽章 ──────────────────────────────────────────────────────────────────

/**
 * 状态文案。head 工作流态与 live 是否在线**必须一起判**：
 * 编辑已发布内容会把 status 打回 draft，但线上仍在展示 live 版本 —— 只读 status
 * 就会把"线上有内容、正在改下一版"错报成"草稿"。
 */
export function statusText(a: Pick<ContentArticle, 'status' | 'is_live' | 'has_pending_change'>): string {
  if (a.has_pending_change && a.is_live) return '已发布 · 有改动待审';
  if (a.status === 'draft' && a.is_live) return '已发布 · 正在改下一版';
  return STATUS_LABELS[a.status as ArticleStatus] ?? a.status;
}

export function StatusBadge({ article }: { article: Pick<ContentArticle, 'status' | 'is_live' | 'has_pending_change'> }) {
  const tone = article.is_live && article.status !== 'archived'
    ? STATUS_TONES.published
    : STATUS_TONES[article.status as ArticleStatus] ?? 'bg-muted text-muted-foreground';
  return (
    <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium ${tone}`}>
      {statusText(article)}
    </span>
  );
}

// ── 语料同步状态 ──────────────────────────────────────────────────────────────

/**
 * 「同步进知识库」的状态徽章（内容管理列表用）。
 *
 * 四种口径都得让用户一眼分得清，因为它们下一步动作不同：
 * 未同步 → 可以同步；已同步 → 无需动作；有改动未同步 → 要重推；上次失败 → 要看原因。
 * 把“失败”画成“未同步”是降级掩盖（静默回到初始态会让人以为从没推过）。
 * 库名一律显业务名（ui-resource-display §1），云端内部标识不在这里。
 */
export function KbSyncBadge({ sync }: { sync?: KbSyncState | null }) {
  if (!sync) {
    return <span className="inline-flex items-center rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">未同步</span>;
  }
  if (sync.state !== 'synced') {
    return (
      <span
        title={sync.last_error || '上次同步失败，服务端未给出原因'}
        className="inline-flex items-center gap-1 rounded-md bg-destructive/10 px-1.5 py-0.5 text-[11px] font-medium text-destructive"
      >
        <AlertTriangle className="h-3 w-3" />同步失败
      </span>
    );
  }
  if (sync.stale) {
    return (
      <span
        title={`已同步到「${sync.kb_name}」的还不是当前版本，需重新同步`}
        className="inline-flex items-center rounded-md bg-amber-500/15 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-400"
      >
        有改动未同步
      </span>
    );
  }
  return (
    <span
      title={`文档「${sync.doc_title}」· 同步于 ${sync.synced_at}`}
      className="inline-flex items-center rounded-md bg-emerald-500/15 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-400"
    >
      已同步·{sync.kb_name}
    </span>
  );
}

// ── 反馈 ──────────────────────────────────────────────────────────────────────

/** 错误条：后端 detail 原样显示（含门禁拒绝原因），并提供重试而不是静默清空 */
export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  if (!message) return null;
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-sm">
      <AlertTriangle className="h-4 w-4 text-destructive flex-shrink-0" />
      <span className="flex-1 min-w-[200px]">{message}</span>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RefreshCw className="h-3.5 w-3.5 mr-1" />重试
        </Button>
      )}
    </div>
  );
}

/** 成功条（动作回执）。文案来自后端 message，不在前端另写一套结论 */
export function NoticeBanner({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  if (!message) return null;
  return (
    <div className="flex items-center gap-3 rounded-xl border border-emerald-500/40 bg-emerald-500/5 p-3 text-sm">
      <CheckCircle2 className="h-4 w-4 text-emerald-600 flex-shrink-0" />
      <span className="flex-1">{message}</span>
      {onDismiss && <Button size="sm" variant="ghost" onClick={onDismiss}>关闭</Button>}
    </div>
  );
}

export function LoadingRow({ text = '加载中' }: { text?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" />{text}
    </div>
  );
}

/**
 * 空态必须说清"为什么空"。
 * 门户为空与筛选无结果是两件事：后者要带清除筛选，否则用户以为内容丢了。
 */
export function EmptyState({ title, hint, onClear }: { title: string; hint?: string; onClear?: () => void }) {
  return (
    <div className="rounded-xl border border-dashed p-10 text-center">
      <p className="text-sm font-medium">{title}</p>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
      {onClear && <Button size="sm" variant="outline" className="mt-3" onClick={onClear}>清除筛选条件</Button>}
    </div>
  );
}

// ── 分页 ──────────────────────────────────────────────────────────────────────
// 不在这里写分页条：全站列表分页唯一入口是 `@/components/PaginationBar`（有静态
// 护栏用例拦“自己写上一页”），这里曾经留过一份内联 Pagination，已收敛到统一组件。

// ── 门禁清单 ──────────────────────────────────────────────────────────────────

/**
 * 提交/审核门禁结果的展示。
 *
 * `acknowledge` 存在与否决定它是「审核确认控件」还是「作者自查提示」：审核人必须
 * **逐条**勾确认才能通过（服务端 L828 会漏一条就 409），所以这里的勾选状态要真的
 * 回传给 approve，不能只是画个复选框好看。
 */
export function GateList({ items, kind, acknowledge, onAck }: {
  items: GateItem[];
  kind: 'hard' | 'warning';
  acknowledge?: Record<string, boolean>;
  onAck?: (code: string, value: boolean) => void;
}) {
  if (!items.length) return null;
  const hard = kind === 'hard';
  return (
    <div className={`rounded-lg border p-3 text-sm ${hard ? 'border-destructive/40 bg-destructive/5' : 'border-amber-500/40 bg-amber-500/5'}`}>
      <p className={`mb-2 font-medium ${hard ? 'text-destructive' : 'text-amber-700 dark:text-amber-400'}`}>
        {hard ? '硬阻断（必须修完才能提交/通过）' : '软提醒（不阻断，但审核人需逐条确认）'}
      </p>
      <ul className="space-y-1.5">
        {items.map(g => (
          <li key={g.code} className="flex items-start gap-2">
            {onAck ? (
              <input
                type="checkbox"
                className="mt-1 size-3.5 accent-primary"
                checked={!!acknowledge?.[g.code]}
                onChange={e => onAck(g.code, e.target.checked)}
                aria-label={`确认已看过提醒：${g.message}`}
              />
            ) : (
              <AlertTriangle className={`mt-0.5 h-3.5 w-3.5 flex-shrink-0 ${hard ? 'text-destructive' : 'text-amber-600'}`} />
            )}
            <span className="flex-1">
              {g.message}
              {g.line ? <span className="ml-1 text-xs text-muted-foreground">（第 {g.line} 行）</span> : null}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── 栏目 / 标签（显示用 name，选择用选择框：ui-resource-display §1/§2）────────

export interface Taxonomy {
  categories: ContentCategory[];
  tags: ContentTag[];
  loading: boolean;
  error: string;
  reload: () => void;
}

/** 栏目与标签清单的唯一来源。任何页面都不许硬编码栏目名。 */
export function useTaxonomy(includeInactive = false): Taxonomy {
  const [categories, setCategories] = useState<ContentCategory[]>([]);
  const [tags, setTags] = useState<ContentTag[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const reload = useCallback(() => {
    setLoading(true);
    setError('');
    Promise.all([contentApi.categories(includeInactive), contentApi.tags(includeInactive)])
      .then(([c, t]) => {
        // 归一化成数组：接口少回 items 时，下游 `categories.map` 会把整块资讯区/表单炸没
        // （首页曾因此白屏）。空清单是合法状态，undefined 不是 —— 这里不收下第二种。
        setCategories(Array.isArray(c.data?.items) ? c.data.items : []);
        setTags(Array.isArray(t.data?.items) ? t.data.items : []);
      })
      .catch(err => setError(contentError(err)))
      .finally(() => setLoading(false));
  }, [includeInactive]);
  useEffect(reload, [reload]);
  return { categories, tags, loading, error, reload };
}

export function TagChips({ tags, active, onPick }: { tags: string[]; active?: string; onPick?: (t: string) => void }) {
  return (
    <span className="flex flex-wrap gap-1">
      {tags.map(t => (
        <button
          key={t}
          type="button"
          disabled={!onPick}
          onClick={() => onPick?.(t)}
          className={`rounded-md px-1.5 py-0.5 text-[11px] transition-colors
            ${active === t ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground'}
            ${onPick ? 'hover:bg-primary/15 cursor-pointer' : 'cursor-default'}`}
        >
          {t}
        </button>
      ))}
    </span>
  );
}

/** 日期只到分：内容场景不需要秒级，显示秒反而干扰阅读 */
export function formatDate(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
