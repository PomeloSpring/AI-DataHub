import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Eye, History, Library, ListChecks, PencilLine, Plus, Send, ShieldCheck, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { PaginationBar } from '@/components/PaginationBar';
import { useAuthStore } from '@/stores/authStore';
import { kbDocsApi, kbDocError, type KbBase } from '@/api/kbDocs';
import {
  contentApi, contentError, STATUS_LABELS, VERSIONING_LABELS,
  type ArticleStatus, type BatchKind, type BatchResult, type ContentArticle,
  type ReleaseItem,
} from '@/api/content';
import ArticleEditor from './ArticleEditor';
import ArticleFiles from './ArticleFiles';
import {
  EmptyState, ErrorBanner, GateList, KbSyncBadge, LoadingRow, NoticeBanner, StatusBadge,
  TagChips, statusText, useContentPerms, useTaxonomy,
} from './shared';

const PAGE_SIZE = 20;
/**
 * 每页条数档位：服务端 `/content/articles` 的 `page_size` 上限是 50（`Query(le=50)`），
 * 所以这里不给 100 —— 选了只会换来一个 422，而不是“多显示几条”。
 */
const PAGE_SIZES = [20, 50];
const ALL = '__all__';
const KIND_ALL = '__kind_all__';

/** 形态筛选项（label 与服务端 KIND_LABELS 一致；promoted 未开放所以不列） */
const KIND_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'markdown', label: '站内正文' },
  { value: 'html', label: 'HTML 版式' },
  { value: 'link', label: '外链条目' },
  { value: 'file', label: '附件' },
];

/**
 * 状态筛选项按作用域给：「内容管理」不提供也不回「草稿」（服务端同口径拒）。
 *
 * 未提交的工作稿属个人区；被驳回的稿会退回 draft，也就从编排面消失 —— 这是拍板口径
 * 不是漏配：审核流水与「我的草稿」仍追得到它。
 */
function statusOptionsOf(scope: 'all' | 'mine'): Array<{ value: 'all' | ArticleStatus; label: string }> {
  const flow = (['in_review', 'approved', 'published', 'archived'] as ArticleStatus[])
    .map(s => ({ value: s as 'all' | ArticleStatus, label: STATUS_LABELS[s] }));
  return [
    // 状态筛选直接用服务端的 'all' 取值（不是本文件的 __all__ 哨兵）：
    // 'all' 才是"不加状态条件", 见下方 load 的注释
    { value: 'all', label: '全部状态' },
    ...(scope === 'mine'
      ? [{ value: 'draft' as 'all' | ArticleStatus, label: STATUS_LABELS.draft }, ...flow]
      : flow),
  ];
}

/**
 * 行上的发布类动作模式（单条按钮、单条弹框与批量分组**共用这一份判据**）。
 *
 * 抽出来的原因不是省事：三处各写一遍 `is_live / can_republish / approved` 的组合，
 * 迟早会出现"行上没有上架、批量里却算了它"这种双口径（服务端才是裁决点）。
 */
function publishModeOf(a: ContentArticle): 'publish' | 'archive' | 'republish' | null {
  if (a.is_live) return 'archive';
  if (a.can_republish) return 'republish';
  /**
   * 未上线的都给发布入口（含草稿与待审）：「免审直发」开关只存在于这个弹框里，
   * 而服务端 waive 分支对**任何状态**都放行（只要逐条写明原因并过门禁硬阻断）。
   * 旧写法只给 approved / has_pending_change，于是自审被拒时那句
   * “需要直发请走「免审直发」”指向的入口在列表里根本不存在（死胡同：
   * 非 admin 作者不能自审、又没有发布按钮，只能重建一篇）。
   * 入口给上不等于默许跳审：弹框里不勾开关时确认键是死的（见 needsWaive）。
   */
  return 'publish';
}

const PUBLISH_MODE_LABELS: Record<'publish' | 'archive' | 'republish', string> = {
  publish: '发布', archive: '下架', republish: '上架',
};

/** 「提交审核」的可用性：草稿，或下架后又改了正文（那一版没审过，只能重走审核） */
function canSubmitRow(a: ContentArticle): boolean {
  return a.status === 'draft' || (a.status === 'archived' && a.head_changed_since_archive);
}

/**
 * 某条内容归不归这次批量动作管。
 *
 * 只用于把"注定被服务端拒"的条目从本次名单里分出去**并显示给用户看**（不静默剔掉），
 * 不是第二套裁决：服务端仍逐条跑单条那套状态机与门禁，判"适用"的条目照样可能被逐条拒
 * （并发被改过、门禁没过）。
 *
 * 批量发布比行上按钮更严一档：只收「已通过待发布」。行上那个 发布 入口对
 * "线上有内容、正在改下一版"（head 是没过审的草稿）也开着，因为单条弹框里可以显式勾
 * 「免审直发」并逐篇写原因；批量不代开那条例外，把它们算进本次名单只会换来一串拒因。
 */
function batchApplies(a: ContentArticle, kind: BatchKind): boolean {
  if (kind === 'submit') return canSubmitRow(a);
  if (kind === 'delete') return !a.is_live;
  if (kind === 'publish') return publishModeOf(a) === 'publish' && a.status === 'approved';
  return publishModeOf(a) === kind;
}

/** 批量动作清单（与服务端 `_BATCH_KINDS` 同一份；perm 决定按钮给不给，不是裁决） */
const BATCH_META: Record<BatchKind, {
  label: string;
  verb: string;
  perm: 'edit' | 'publish';
  destructive?: boolean;
  /** 确认框里的口径说明，与单条弹框同一段话，不另写一套结论 */
  hint: string;
}> = {
  submit: {
    label: '批量提交审核', verb: '提交审核', perm: 'edit',
    hint: '逐条跑提交门禁：缺必填项、正文含内部标识的当场拒并说明缺什么；软提醒随条目回给审核人（仍需逐条确认）。已在审核中的会被拒。',
  },
  publish: {
    label: '批量发布', verb: '发布', perm: 'publish',
    hint: '只发布「已通过待发布」的内容，发布后对全体登录用户可见（不受数据权限过滤）。'
      + '正在改下一版而未重新审核的不在本批范围：那需要逐篇走审核，或显式勾「免审直发」并写明原因。',
  },
  republish: {
    label: '批量上架', verb: '上架', perm: 'publish',
    hint: '把各篇下架时在线的那一版放回门户 —— 那一版当初是审核通过才上线的，所以这一步不重新走审核；下架之后改的正文不会被顺带放出去。',
  },
  archive: {
    label: '批量下架', verb: '下架', perm: 'publish',
    hint: '下架后门户立即不再展示。已发布内容代表公司立场，下架同样进审计与审核流水；原因对本次每篇共用。',
  },
  delete: {
    label: '批量删除', verb: '删除', perm: 'edit', destructive: true,
    hint: '删除草稿、它的全部历史版本与挂在上面的附件（附件文件同时从存储里删掉），不可撤销。已上线的内容会被逐条拒（必须先下架）。',
  },
};

/** 工具条顺序：按 写作 → 发布 → 上架 → 下架 → 删除 的工作流方向排 */
const BATCH_ORDER: BatchKind[] = ['submit', 'publish', 'republish', 'archive', 'delete'];

/**
 * 内容清单表（「内容管理」与「我的草稿」共用）。
 *
 * 两个页面共用一份的原因不是省事，而是**动作口径必须一致**：删除、提交、发布这些
 * 判断若各写一遍，很容易出现"管理页能删、草稿页删不掉"这类半成品体验。
 */
export default function ArticleTable({ scope }: { scope: 'all' | 'mine' }) {
  const navigate = useNavigate();
  const [sp] = useSearchParams();
  const { has } = useContentPerms();
  const { user } = useAuthStore();
  const { categories, reload: reloadTaxonomy } = useTaxonomy();

  const canEdit = has('article:manage');
  const canPublish = has('article:publish');
  // 审核与发布是两个码：原「审核发布」页已并入本页，入口按码给
  const canReview = has('article:review');
  // 同步走语料通道自己的权限码：能编内容/能发布不等于能写 AS-BOT 的知识
  const canSyncKb = has('kbdoc:write');

  const [items, setItems] = useState<ContentArticle[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [status, setStatus] = useState<'all' | ArticleStatus>(() => {
    // 初始筛选可由地址带（旧 /content/review 重定向到 ?status=in_review，书签/分享同理）：
    // 只接受本页筛选器真有的取值，不合法就回落 all（编排面的“草稿”不是合法取值）
    const v = sp.get('status');
    return v && statusOptionsOf(scope).some(o => o.value === v) ? (v as ArticleStatus) : 'all';
  });
  // 到期复核是“待处理”的另一类（已上线但复核日期过了），与状态不同维度，单开一个开关
  const [expired, setExpired] = useState(sp.get('expired') === '1');
  const [category, setCategory] = useState(ALL);
  const [kind, setKind] = useState(KIND_ALL);
  const [qInput, setQInput] = useState('');
  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const [editingSlug, setEditingSlug] = useState<string | undefined>(undefined);
  const [editorOpen, setEditorOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ContentArticle | null>(null);
  const [publishTarget, setPublishTarget] = useState<ContentArticle | null>(null);
  // 审核弹框（逐条确认门禁提醒 + 通过/驳回）：从已删除的「审核发布」页搬到行操作上
  const [reviewTarget, setReviewTarget] = useState<ContentArticle | null>(null);
  // 「切换展示版本」单列一个入口：它必须逐条指名期次，所以不进批量（同免审直发那条纪律）
  const [rollbackTarget, setRollbackTarget] = useState<ContentArticle | null>(null);
  const [syncTarget, setSyncTarget] = useState<ContentArticle | null>(null);
  // 批量操作：勾选是**视图态**（不进 store），避免切页/换筛选后残留一堆“看不见却待处理”的名单
  const [batchMode, setBatchMode] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [batchKind, setBatchKind] = useState<BatchKind | null>(null);
  const [batchResult, setBatchResult] = useState<BatchResult | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    contentApi.list({
      // 「我的草稿」按服务端识别的当前用户名过滤，不接受前端传任意用户名看别人的草稿
      mine: scope === 'mine' ? (user?.username ?? '') : '',
      // 「全部状态」必须显式传 all：传空串会被服务端当成"只看门户在线内容"，
      // 草稿就会从管理清单里消失（不是少几行，是整个草稿看不见）
      status,
      expired,
      category: category === ALL ? '' : category,
      // 哨兵值不往外传：服务端 kind 只认四个形态名，传错是直接拒而不是默默空清单
      kind: kind === KIND_ALL ? '' : kind,
      q,
      page,
      page_size: pageSize,
      sort: 'latest',
    })
      .then(res => { setItems(res.data.items); setTotal(res.data.total); })
      .catch(err => { setError(contentError(err)); setItems([]); setTotal(0); })
      .finally(() => setLoading(false));
  }, [scope, user?.username, status, expired, category, kind, q, page, pageSize]);

  useEffect(load, [load]);

  // 换筛选条件或翻页就清勾选：勾选属于“这一页这份清单”，带过去等于让用户对没看到的条目下手
  useEffect(() => { setSelected([]); setBatchResult(null); }, [status, expired, category, kind, q, page, pageSize]);

  const picked = items.filter(a => selected.includes(a.slug));
  const allPicked = items.length > 0 && picked.length === items.length;
  const toggleRow = (slug: string) => setSelected(prev =>
    (prev.includes(slug) ? prev.filter(s => s !== slug) : [...prev, slug]));
  const toggleAll = () => setSelected(allPicked ? [] : items.map(a => a.slug));
  const exitBatch = () => { setBatchMode(false); setSelected([]); setBatchKind(null); setBatchResult(null); };

  const act = (fn: () => Promise<unknown>, ok: string) => {
    setError(''); setNotice('');
    fn()
      .then(res => {
        // 结论以服务端文案为准（它会说"已发布，全体登录用户可见"这类完整口径）；
        // 不返 message 的接口（如删除）才回退到本地 ok。
        const d = (res as { data?: { message?: string; warning?: string; warnings?: string[] } })?.data;
        // 服务端显式带的提醒（旧库那篇没撤掉、附件没删干净）不得被 ok 文案顶掉
        const warns = [...(d?.warnings ?? []), d?.warning ?? ''].filter(Boolean);
        setNotice([d?.message || ok, ...warns].join(' —— '));
        load();
      })
      // 服务端拒绝原因（未审核不得发布、作者自审、并发冲突）必须原样显示
      .catch(err => setError(contentError(err)));
  };

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold">{scope === 'mine' ? '我的草稿' : '内容管理'}</h1>
          <p className="text-sm text-muted-foreground">
            {scope === 'mine'
              ? '这里只列你名下未发布的工作稿，新建内容也在这里；提交审核后仍会留在列表里，直到审核出结论。'
              : '编排已提交的内容：待审、已通过、已发布与已下架。未提交的草稿（含被驳回退回的）只在「我的草稿」，新建与编写也在那里；审核与发布都在行上，按各自权限码给。'}
          </p>
        </div>
        {/* 新建入口只给「我的草稿」：写作是作者在自己名下开工，内容管理是编排面
            （看待审/已发布/已下架 + 发布与同步），在那里放个“新建”会把两个职责混成一个 */}
        {canEdit && scope === 'mine' && (
          <Button onClick={() => { setEditingSlug(undefined); setEditorOpen(true); }}>
            <Plus className="mr-1.5 h-4 w-4" />新建内容
          </Button>
        )}
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-[200px] flex-1">
          <Input
            value={qInput} onChange={e => setQInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { setQ(qInput.trim()); setPage(1); } }}
            placeholder="搜索标题或摘要，回车检索" aria-label="搜索内容"
          />
        </div>
        <Select value={status} onValueChange={v => { setStatus(v as 'all' | ArticleStatus); setPage(1); }}>
          <SelectTrigger className="w-[150px]" aria-label="状态筛选"><SelectValue /></SelectTrigger>
          <SelectContent>
            {statusOptionsOf(scope).map(s => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={category} onValueChange={v => { setCategory(v); setPage(1); }}>
          <SelectTrigger className="w-[160px]" aria-label="栏目筛选"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>全部栏目</SelectItem>
            {categories.map(c => <SelectItem key={c.key} value={c.key}>{c.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={kind} onValueChange={v => { setKind(v); setPage(1); }}>
          <SelectTrigger className="w-[130px]" aria-label="形态筛选"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={KIND_ALL}>全部形态</SelectItem>
            {KIND_OPTIONS.map(k => <SelectItem key={k.value} value={k.value}>{k.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <Button variant="outline" onClick={() => { setQ(qInput.trim()); setPage(1); }}>搜索</Button>
        {/* 到期复核快捷筛选：它不是状态而是“该重新确认口径”（已上线且 review_by 过期），
            原「审核发布」页顶部那张清单并入到这里，口径仍走服务端同一谓词 */}
        {canReview && scope === 'all' && (
          <Button variant={expired ? 'default' : 'outline'} size="sm" aria-pressed={expired}
            title="只看过了复核日期、需要重新确认口径的内容"
            onClick={() => { setExpired(v => !v); setStatus('all'); setPage(1); }}>
            到期复核
          </Button>
        )}
        {/* 批量入口按权限给：只读角色本来也进不了这一页（菜单按 article:manage 反查） */}
        {(canEdit || canPublish) && (
          <Button variant={batchMode ? 'default' : 'outline'} aria-pressed={batchMode}
            title={batchMode ? '退出批量操作' : '批量操作：勾选多条后统一提交审核 / 发布 / 上架 / 下架 / 删除'}
            onClick={() => (batchMode ? exitBatch() : setBatchMode(true))}>
            <ListChecks className="mr-1.5 h-4 w-4" />批量操作
          </Button>
        )}
      </div>

      <ErrorBanner message={error} onRetry={() => { load(); reloadTaxonomy(); }} />
      <NoticeBanner message={notice} onDismiss={() => setNotice('')} />
      {batchResult && <BatchResultBanner result={batchResult} onDismiss={() => setBatchResult(null)} />}

      {batchMode && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-muted/30 px-3 py-2">
          <span className="text-xs font-medium">已选 {picked.length} 项</span>
          {BATCH_ORDER
            .filter(k => (BATCH_META[k].perm === 'edit' ? canEdit : canPublish))
            .map(k => {
              const n = picked.filter(a => batchApplies(a, k)).length;
              return (
                <Button key={k} size="sm"
                  variant={BATCH_META[k].destructive ? 'destructive' : 'outline'}
                  disabled={!n}
                  title={n ? BATCH_META[k].hint : `已选内容里没有可${BATCH_META[k].verb}的（判据与服务端一致，换对应动作即可）`}
                  onClick={() => setBatchKind(k)}>
                  {BATCH_META[k].label}{n ? ` (${n})` : ''}
                </Button>
              );
            })}
          <Button size="sm" variant="ghost" disabled={!picked.length} onClick={() => setSelected([])}>取消选择</Button>
          <span className="ml-auto text-[11px] text-muted-foreground">
            只处理当前页勾选项；服务端逐条裁决，不合格的点名回原因
          </span>
        </div>
      )}

      {loading && <LoadingRow text="正在加载内容清单" />}

      {/* 拉失败时不得再画“没有符合条件的内容”：那是把 500 伪装成空结果。
          空态文案要指对地方：内容管理页已不放新建按钮，再让人“点右上”就是空承诺。 */}
      {!loading && !error && items.length === 0 && (
        <EmptyState
          title={scope === 'mine' ? '你名下还没有草稿' : '没有符合条件的内容'}
          hint={canEdit
            ? (scope === 'mine'
              ? '点右上「新建内容」开始编写；保存草稿不会影响门户。'
              : '这里是编排面：新建与编写在左侧「我的草稿」进行，内容发布后回到这里审核与下架。')
            : '内容需要由具备编辑权限的同事编写并发布。'}
        />
      )}

      {!loading && items.length > 0 && (
        <div className="overflow-x-auto rounded-xl border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground">
              <tr>
                {batchMode && (
                  <th className="w-9 px-3 py-2">
                    {/* 全选只管本页：跨页"全选"会让人以为选中了整库，而批量上限只覆盖当前清单 */}
                    <input type="checkbox" className="size-3.5 accent-primary" aria-label="全选本页"
                      checked={allPicked} onChange={toggleAll} />
                  </th>
                )}
                <th className="px-3 py-2 text-left font-medium">标题</th>
                <th className="px-3 py-2 text-left font-medium">栏目</th>
                <th className="px-3 py-2 text-left font-medium">状态</th>
                {canSyncKb && <th className="px-3 py-2 text-left font-medium">语料</th>}
                <th className="px-3 py-2 text-left font-medium">标签</th>
                <th className="px-3 py-2 text-right font-medium">阅读</th>
                <th className="px-3 py-2 text-left font-medium">更新</th>
                <th className="px-3 py-2 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {items.map(a => {
                const mode = publishModeOf(a);
                return (
                <tr key={a.slug} className="hover:bg-accent/40">
                  {batchMode && (
                    <td className="px-3 py-2">
                      <input type="checkbox" className="size-3.5 accent-primary"
                        aria-label={`选择 ${a.title}`} checked={selected.includes(a.slug)}
                        onChange={() => toggleRow(a.slug)} />
                    </td>
                  )}
                  {/* 标题列不放摘要：列表要一眼扫完，摘要是详情页的事。
                      宽度约束必须落在标题自己上（不是 td）：`<td>` 在自动表格布局下不吃
                      max-width，写在 td 等于没写，长标题会把列撑破并盖到右边栏目/状态列上（历史缺陷）。
                      截断后靠 title 悬停看到全名。 */}
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      title={a.title}
                      className="block max-w-[320px] truncate text-left font-medium hover:text-primary"
                      onClick={() => navigate(`/content/preview/${a.slug}`)}
                    >
                      {a.title}
                    </button>
                    {/* 形态只在这一行补个徽标：附件稿的"能不能点开"取决于有没有文件，
                        跟状态徽章混在一起会被读成工作流态 */}
                    {a.kind !== 'markdown' && (
                      <div className="mt-1">
                        <span className="inline-flex rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                          {a.kind_label}
                        </span>
                      </div>
                    )}
                    {/* 期次与形态同列不同义：“现在是第几期”是发布事实，“是什么形态”是录入方式 */}
                    {(!!a.release || a.history_hidden) && (
                      <div className="mt-1 flex items-center gap-1.5">
                        <ReleaseBadge article={a} />
                        {a.history_hidden && (
                          <span className="text-[10px] text-muted-foreground"
                            title={VERSIONING_LABELS.latest_only}>
                            历史期次不对读者展示
                          </span>
                        )}
                      </div>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-xs">{a.category_icon ? `${a.category_icon} ` : ''}{a.category_label}</td>
                  <td className="whitespace-nowrap px-3 py-2"><StatusBadge article={a} /></td>
                  {canSyncKb && (
                    <td className="whitespace-nowrap px-3 py-2"><KbSyncBadge sync={a.kb_sync} /></td>
                  )}
                  <td className="px-3 py-2"><TagChips tags={a.tags.slice(0, 3)} /></td>
                  <td className="whitespace-nowrap px-3 py-2 text-right text-xs text-muted-foreground">{a.view_count}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">
                    {a.updated_at.slice(0, 16).replace('T', ' ')}
                    <div>{a.updated_by || a.author}</div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    <div className="flex items-center justify-end gap-1">
                      <Button size="sm" variant="ghost" aria-label={`查看 ${a.title}`} onClick={() => navigate(`/content/preview/${a.slug}`)}>
                        <Eye className="h-3.5 w-3.5" />
                      </Button>
                      {canEdit && (
                        <>
                          <Button size="sm" variant="ghost" aria-label={`编辑 ${a.title}`}
                            onClick={() => { setEditingSlug(a.slug); setEditorOpen(true); }}>
                            <PencilLine className="h-3.5 w-3.5" />
                          </Button>
                          {/* 已下架且 head 是下架后新改的版本也要给提交入口：否则那篇既走不了一键上架
                              （没可放回的下架版）又看不到送审入口，只能重新建一篇 */}
                          {canSubmitRow(a) && (
                            <Button size="sm" variant="ghost" aria-label={`提交审核 ${a.title}`}
                              title={a.status === 'archived'
                                ? '下架之后改过正文，这一版需要重新审核才能上线' : undefined}
                              onClick={() => act(() => contentApi.submit(a.slug), '已提交审核')}>
                              <Send className="h-3.5 w-3.5" />
                            </Button>
                          )}
                          {!a.is_live && (
                            <Button size="sm" variant="ghost" className="text-destructive" aria-label={`删除 ${a.title}`}
                              onClick={() => setDeleteTarget(a)}>
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                          )}
                        </>
                      )}
                      {canSyncKb && (
                        <Button size="sm" variant="ghost" aria-label={`同步到知识库 ${a.title}`}
                          title="把已发布内容推成 AS-BOT 可检索的语料（需单独的知识库文档写人权）"
                          onClick={() => setSyncTarget(a)}>
                          <Library className="h-3.5 w-3.5" />
                        </Button>
                      )}
                      {/* 三个改门户可见性的动作共用一个入口（服务端 can_republish 决定给不给）：
                          已下架不给上架入口就是死胡同 —— 下架只收入口，不推翻审核结论 */}
                      {/* 审核入口只在「内容管理」的行上（拍板：审核不必单开一页，但也不处处开）：
                          已提交的稿子本来就在编排面，在「我的草稿」重复给一份审核入口
                          只会把“作者个人区”又变成审核工作台 */}
                      {canReview && scope === 'all' && a.status === 'in_review' && !expired && (
                        <Button size="sm" variant="outline" onClick={() => setReviewTarget(a)}>
                          <ShieldCheck className="mr-1 h-3.5 w-3.5" />审核
                        </Button>
                      )}
                      {canPublish && mode && (
                        <Button size="sm" variant="outline" onClick={() => setPublishTarget(a)}>
                          {PUBLISH_MODE_LABELS[mode]}
                        </Button>
                      )}
                      {/* 切换展示版本（回滚）：服务端算得“至少两期已发布且未下架”才给入口
                          （can_rollback）：只有一期时这个开关只能选中自己，点了也不会变 */}
                      {canPublish && a.can_rollback && (
                        <Button size="sm" variant="ghost" aria-label={`切换展示版本 ${a.title}`}
                          title="把门户展示的那一期切回已发布过的某一期（不重写正文、不重新审核）"
                          onClick={() => setRollbackTarget(a)}>
                          <History className="h-3.5 w-3.5" />
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 统一分页条：不拿 hideWhenSingle 关掉——编排面需要“共 N 条”与每页档位常驻可见
          （单页时关掉会让人以为本页没有分页机制）；空清单不占一条底栏，
          重拉期间保留整条并把按钮置灰（避免筛一下底栏就闪没） */}
      {total > 0 && (
        <PaginationBar
          page={page} pageSize={pageSize} total={total} busy={loading}
          onChange={setPage}
          onPageSizeChange={n => { setPageSize(n); setPage(1); }}
          pageSizeOptions={PAGE_SIZES}
        />
      )}

      <ArticleEditor
        open={editorOpen}
        slug={editingSlug}
        onOpenChange={setEditorOpen}
        onSaved={() => { load(); }}
      />

      <ConfirmDialog
        open={!!deleteTarget}
        title="删除草稿"
        description={deleteTarget
          ? `确定删除「${deleteTarget.title}」？草稿、它的全部历史版本与挂在上面的附件会被一并移除（附件文件同时从存储里删掉），此操作不可撤销。已上线的内容必须先下架。`
          : ''}
        confirmLabel="删除"
        destructive
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => {
          const a = deleteTarget!;
          setDeleteTarget(null);
          act(() => contentApi.remove(a.slug), '已删除');
        }}
      />

      <PublishDialog
        article={publishTarget}
        onClose={() => setPublishTarget(null)}
        onDone={(msg, warnings) => {
          setPublishTarget(null);
          // 服务端返回的提醒（如“已同步的语料还在库里”）拼在结论后面，不顶掉结论也不静默
          setNotice([msg, ...warnings].filter(Boolean).join(' —— '));
          load();
        }}
      />

      <ReviewDialog
        article={reviewTarget}
        onClose={() => setReviewTarget(null)}
        onDone={(msg, warnings) => {
          setReviewTarget(null);
          setNotice([msg, ...warnings].filter(Boolean).join(' —— '));
          load();
        }}
      />

      <KbSyncDialog
        article={syncTarget}
        onClose={() => setSyncTarget(null)}
        onDone={(msg, warnings) => { setSyncTarget(null); setNotice([msg, ...warnings].join(' —— ')); load(); }}
      />

      <RollbackDialog
        article={rollbackTarget}
        onClose={() => setRollbackTarget(null)}
        onDone={(msg, warnings) => {
          setRollbackTarget(null);
          setNotice([msg, ...warnings].filter(Boolean).join(' —— '));
          load();
        }}
      />

      {batchKind && (
        <BatchDialog
          kind={batchKind}
          articles={picked}
          onClose={() => setBatchKind(null)}
          onDone={res => {
            const kind = batchKind;
            setBatchKind(null);
            setBatchResult(res);
            // 勾了但这次没动的（状态不适用）与逐条失败的都留在勾选里：
            // 前者换个动作就是下一步（已上线的先下架才删得掉），后者改完可以直接重试
            const keep = new Set([
              ...items.filter(a => !batchApplies(a, kind)).map(a => a.slug),
              ...res.failed.map(f => f.slug),
            ]);
            setSelected(prev => prev.filter(s => keep.has(s)));
            load();
          }}
        />
      )}
    </div>
  );
}

/** 「本期/共几期」徽标：期次是发布出来的那一版，与 `status`（head 工作流态）是两套口径 */
function ReleaseBadge({ article }: { article: ContentArticle }) {
  if (!article.release) return null;
  return (
    <span
      title={article.release_count > 1
        ? `本篇共 ${article.release_count} 期：读者在内容页可翻历史期次`
        : '本篇目前只有这一期'}
      className="inline-flex rounded bg-sky-500/15 px-1.5 py-0.5 text-[10px] text-sky-600 dark:text-sky-400"
    >
      第 {article.release} 期{article.release_label ? `·${article.release_label}` : ''}
      {article.release_count > 1 && <span className="ml-1 opacity-80">/ 共 {article.release_count} 期</span>}
    </span>
  );
}

/** 发布时的期次名建议：与缺省口径一致，前端只是把服务端会算的值先给人看 */
function suggestReleaseLabel(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

/**
 * 审核弹框（原「审核发布」工作台的行内版）。
 *
 * 三条把关与合并前完全一致，只是发生位置变了：
 * 1. 软提醒必须**逐条**确认后才可能通过（服务端 409 兜底，前端先把按钮按住）；
 * 2. 硬阻断直接说“必须修完”，不给通过按钮；
 * 3. 驳回必须写原因（作者要据此改）。
 * 读稿走 /reviews/{slug}/draft：审核人不必具备编辑权（由 article:review 自己裁决）。
 */
function ReviewDialog({ article, onClose, onDone }: {
  article: ContentArticle | null;
  onClose: () => void;
  onDone: (msg: string, warnings: string[]) => void;
}) {
  const [detail, setDetail] = useState<ContentArticle | null>(null);
  const [acks, setAcks] = useState<Record<string, boolean>>({});
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // 读稿：重入（附件上传后刷新、换条目）都回到同一个入口，不在本组件另存一份真值
  const read = useCallback(() => {
    if (!article) return;
    contentApi.reviewDraft(article.slug)
      .then(res => setDetail(res.data))
      .catch(err => setError(contentError(err)));
  }, [article?.slug]);

  useEffect(() => {
    setDetail(null); setAcks({}); setComment(''); setError('');
    if (!article) return;
    read();
  }, [article?.slug, read]);

  if (!article) return null;
  const warnings = detail?.gate_warnings ?? [];
  const hard = detail?.gate_hard ?? [];
  const allAcked = warnings.every(w => acks[w.code]);
  // 通过按钮的可用条件与服务端一致：拿到稿、无硬阻断、提醒逐条确认完
  const canApprove = !!detail && hard.length === 0 && allAcked;

  const run = (kind: 'approve' | 'reject') => {
    if (kind === 'reject' && !comment.trim()) {
      // 前端先拦一次，服务端同样会拒（驳回必须有原因，作者要据此改）
      setError('驳回必须写原因，作者要据此修改。');
      return;
    }
    setBusy(true); setError('');
    const call = kind === 'approve'
      ? contentApi.approve(article.slug, comment.trim(),
                           warnings.map(w => ({ ...w, acknowledged: !!acks[w.code] })))
      : contentApi.reject(article.slug, comment.trim());
    // 回执里的 warning（如“这一版由作者本人审核通过”）必须带到结论后面，不得只取 message
    call.then(res => onDone(res.data.message, [res.data.warning ?? ''].filter(Boolean)))
      .catch(err => setError(contentError(err)))
      .finally(() => setBusy(false));
  };

  return (
    <Dialog open onOpenChange={v => { if (!v) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>审核内容</DialogTitle>
          <DialogDescription>
            审核是人的判断，AI 不参与批与发。通过后可由具「发布与下架内容」权限的人上线。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <ErrorBanner message={error} />
          {!detail && !error && <LoadingRow text="正在读稿与门禁结果" />}

          {detail && (
            <div className="space-y-3">
              <div className="rounded-lg border bg-muted/30 p-3 text-xs leading-5 space-y-1.5">
                <p className="font-medium text-sm">{detail.title}</p>
                <p className="flex flex-wrap items-center gap-1.5">
                  当前状态：<StatusBadge article={detail} />
                  {!!detail.submitted_by && <span className="text-muted-foreground">· 提交人 {detail.submitted_by}</span>}
                  <a
                    href={`/content/preview/${detail.slug}`} target="_blank" rel="noreferrer"
                    className="ml-auto text-primary hover:underline"
                  >打开全文预览</a>
                </p>
                <TagChips tags={detail.tags} />
              </div>

              <GateList items={hard} kind="hard" />
              {/* 附件：审核人必能下载。挂 scope=reviews 走 article:review 自己裁决 ——
                  只勾审核权的角色拿不到 articles*，看不到附件的审核是空承诺。 */}
              {!!(detail.files ?? []).length && (
                <ArticleFiles slug={detail.slug} files={detail.files ?? []}
                  scope="reviews" onRefresh={read} />
              )}
              {!!warnings.length && (
                <GateList items={warnings} kind="warning" acknowledge={acks}
                  onAck={(code, value) => setAcks(prev => ({ ...prev, [code]: value }))} />
              )}
              {!hard.length && !warnings.length && (
                <p className="text-xs text-muted-foreground">门禁没有提醒，也没得确认项，可直接通过。</p>
              )}

              <div className="space-y-1">
                <Label htmlFor="review-comment">审核意见（驳回时必填，通过可选填）</Label>
                <Textarea id="review-comment" rows={2} value={comment} onChange={e => setComment(e.target.value)}
                  placeholder="例如：第 3 节的样本区间与标题不一致，改后重交" />
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>取消</Button>
          <Button variant="outline" onClick={() => run('reject')} disabled={busy}>
            驳回
          </Button>
          <Button onClick={() => run('approve')} disabled={busy || !canApprove}>
            {busy ? '处理中' : '审核通过'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PublishDialog({ article, onClose, onDone }: {
  article: ContentArticle | null;
  onClose: () => void;
  /** 第二个参数是服务端返回的提醒（如“已同步的语料还在库里”），不得被丢弃 */
  onDone: (msg: string, warnings: string[]) => void;
}) {
  const [waive, setWaive] = useState(false);
  const [reason, setReason] = useState('');
  // 期次名由**发布人**给（缺省则服务端按当月自动命名）：批量发布不收它，一律用自动建议值
  const [releaseLabel, setReleaseLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setWaive(false); setReason(''); setError(''); setReleaseLabel('');
  }, [article?.slug]);

  if (!article) return null;
  /**
   * 三个动作同一个弹框：它们都只改“门户可见性”，拆成三份很容易写出
   * “下架要填原因、上架不用”这种双口径。mode 只由条目当前状态决定，不再推一遍权限。
   */
  // mode 与行上按钮、批量分组同一份判据；未上线的一律落在 'publish'（上面 `?? 'publish'`
  // 只是类型兼容，publishModeOf 已不再返回 null）
  const mode = publishModeOf(article) ?? 'publish';
  /**
   * 这一版没审过（非 approved）时，不勾「免审直发」的发布点了也**必定被服务端拒**。
   * 与其让人吃一个 422，不如在点之前把条件说清并把确认键置灰：
   * 开关仍默认不勾（不拿默认值替人跳审），“要么走审核、要么显式留痕迹”两条路都递到眼前。
   */
  const needsWaive = mode === 'publish' && article.status !== 'approved';
  const blockedByReview = needsWaive && !waive;

  const submit = async () => {
    setBusy(true);
    setError('');
    try {
      const res = mode === 'archive'
        ? await contentApi.archive(article.slug, reason.trim(), article.version)
        : mode === 'republish'
          ? await contentApi.republish(article.slug, reason.trim(), article.version)
          : await contentApi.publish(article.slug, {
              expected_version: article.version,
              waive_review: waive,
              reason: reason.trim(),
              release_label: releaseLabel.trim(),
            });
      onDone(res.data.message || '操作完成', [res.data.warning ?? ''].filter(Boolean));
    } catch (err) {
      setError(contentError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={v => { if (!v) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {mode === 'archive' ? '下架内容' : mode === 'republish' ? '重新上架' : '发布内容'}
          </DialogTitle>
          <DialogDescription>
            {mode === 'archive'
              ? '下架后门户立即不再展示。已发布内容代表公司立场，下架同样进审计与审核流水。'
              : mode === 'republish'
                ? '上架后门户立即恢复展示下架前那一版。那一版当初是审核通过才上线的，所以这一步不重新走审核。'
                : '发布后对全体登录用户可见（不受数据权限过滤）。'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <ErrorBanner message={error} />

          {mode === 'republish' && (
            <div className="space-y-3">
              <div className="rounded-lg border bg-muted/30 p-3 text-xs leading-5 space-y-1.5">
                <p className="flex items-center gap-1.5">当前状态：<StatusBadge article={article} /></p>
                <p className="text-muted-foreground">
                  放回的是下架时在线的那一版{article.archived_at
                    ? `（下架于 ${article.archived_at.slice(0, 16).replace('T', ' ')}）` : ''}；
                  下架之后改的正文不会被顺带放出去。
                </p>
                {article.head_changed_since_archive && (
                  <p className="text-amber-700 dark:text-amber-400">
                    这篇在下架之后又改过正文：改动不会跟着上线（那一版没经过审核）。
                    要发这一版请「提交审核」通过后发布。
                  </p>
                )}
              </div>
              <div className="space-y-1">
                <Label htmlFor="republish-reason">上架原因（选填，会写入审核流水）</Label>
                <Textarea id="republish-reason" rows={2} value={reason} onChange={e => setReason(e.target.value)}
                  placeholder="例如：法规条款已复核，口径无需修改" />
              </div>
            </div>
          )}

          {mode === 'publish' && (
            <div className="rounded-lg border bg-muted/30 p-3 text-xs leading-5">
              <p>当前状态：<StatusBadge article={article} />{article.has_pending_change ? '（线上已有版本，发布会替换成这一版）' : ''}</p>
              <div className="mt-2 space-y-1">
                <Label htmlFor="publish-release-label">期次名（会显示给读者，如「2026-10」）</Label>
                <Input id="publish-release-label" value={releaseLabel}
                  onChange={e => setReleaseLabel(e.target.value)}
                  placeholder={`留空则自动用发布当月：${suggestReleaseLabel()}`} />
                <p className="text-[11px] text-muted-foreground">
                  本次发布会让这一版成为新的第 {article.release_count + 1} 期；
                  期次号由服务端逐次分配，不靠名字排序（改名不会打乱期次顺序）。
                </p>
              </div>
              {/* 策略后果必须在点之前说清：只留最新不是“删了旧的”，但读者确实从此翻不到 */}
              {article.versioning === 'latest_only' ? (
                <p className="mt-1.5 text-amber-700 dark:text-amber-400">
                  本篇策略为「{VERSIONING_LABELS.latest_only}」：发布后上一期
                  {article.release_label ? `〈${article.release_label}〉` : ''}
                  不再对读者展示（历史期次仍可在编辑器版本清单与审核流水里看到，切回去也还能切）。
                </p>
              ) : article.release_count >= 1 ? (
                <p className="mt-1.5 text-muted-foreground">
                  本篇策略为「{VERSIONING_LABELS.keep_history}」：发布后共 {article.release_count + 1} 期，
                  读者可在内容页历史期次里翻看旧期。
                </p>
              ) : null}
              <label className="mt-2 flex items-center gap-2 text-sm">
                <Switch checked={waive} onCheckedChange={setWaive} />
                免审直发（跳过审核直接上线）
              </label>
              {needsWaive && (
                <p className="mt-1.5 text-amber-700 dark:text-amber-400">
                  这一版还没审核通过（当前是「{STATUS_LABELS[article.status]}」）。
                  正常路径是「提交审核 → 审核人通过 → 发布」；
                  要跳过这一步只能勾下面的「免审直发」并写明原因（进审计）。
                </p>
              )}
              {waive && (
                <div className="mt-2 space-y-1">
                  <Label htmlFor="waive-reason">免审原因（必填，会写入审计）</Label>
                  <Textarea id="waive-reason" rows={2} value={reason} onChange={e => setReason(e.target.value)}
                    placeholder="例如：合规部门已在评审会上确认，负责人张三" />
                </div>
              )}
              {!waive && !needsWaive && (
                <p className="mt-1.5 text-muted-foreground">
                  未走审核的内容不能发布。需要跳过审核请显式勾选「免审直发」并写明原因 —— 这个例外会留下痕迹，不是暗门。
                </p>
              )}
            </div>
          )}

          {mode === 'archive' && (
            <div className="space-y-1">
              <Label htmlFor="archive-reason">下架原因</Label>
              <Textarea id="archive-reason" rows={2} value={reason} onChange={e => setReason(e.target.value)}
                placeholder="例如：引用的法规条款已更新，待复核后重发" />
              <p className="text-[11px] text-muted-foreground">
                下架不会自动撤回已同步的知识库语料（那会动云端文档，是单独一个动作）。
                若这篇已进过语料，回执里会把“AS-BOT 仍会检索到”显式说出来，并指回「知识库文档」去处理。
              </p>
            </div>
          )}

        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>取消</Button>
          <Button onClick={submit}
            disabled={busy || blockedByReview || (mode === 'publish' && waive && !reason.trim())}>
            {busy ? '处理中'
              : mode === 'archive' ? '确认下架'
                : mode === 'republish' ? '确认上架' : '确认发布'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * 「切换展示版本」弹框（回滚 = 把门户展示的那一期切到已发布过的某一期）。
 *
 * 四条不能省的口径：
 * 1. **候选只从服务端拿**（`releases` 接口）：编排面拿全量，包括策略为只留最新时
 *    被隐藏的那些期——否则“能切到哪些期”变成只有数据库知道的事实。
 * 2. **原因必填**：改的是对外可见内容，与下架同一等级；审计与审核流水都靠它。
 * 3. **不拿 head 顺带上线**：草稿位假设还有没审过的改动，回执会把它显式说出来。
 * 4. **不进批量**：切到哪一期必须逐条显式指定（与批量不代开免审直发同一纪律）。
 */
function RollbackDialog({ article, onClose, onDone }: {
  article: ContentArticle | null;
  onClose: () => void;
  onDone: (msg: string, warnings: string[]) => void;
}) {
  const [releases, setReleases] = useState<ReleaseItem[] | null>(null);
  const [target, setTarget] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // 重试 = 真的重发一次请求（不是把本地清单置空：那会把“拉不到”伪装成“没发过”）
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setTarget(''); setReason(''); setError(''); setReleases(null);
    if (!article) return;
    let alive = true;
    contentApi.releases(article.slug)
      .then(res => { if (alive) setReleases(res.data.items); })
      .catch(err => { if (alive) setError(contentError(err)); });
    return () => { alive = false; };
  }, [article?.slug, reloadKey]);

  if (!article) return null;
  // 已经是当前那一期的项置灰（选中自己不是“切了一次”，而是一个看似成功实则空动作）
  const options = (releases ?? []).filter(r => r.release !== article.release);

  const submit = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await contentApi.rollbackRelease(article.slug, Number(target), reason.trim(), article.version);
      onDone(res.data.message || '已切换展示版本', [res.data.warning ?? ''].filter(Boolean));
    } catch (err) {
      setError(contentError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={v => { if (!v) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>切换展示版本</DialogTitle>
          <DialogDescription>
            把门户现在展示的这一期切回已经发布过的另一期。切过去的那一版本来就审过才上线，
            所以这一步不重新走审核；正文不会被重写，也不会把草稿位没审过的改动顺带上线。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <ErrorBanner message={error} onRetry={() => setReloadKey(k => k + 1)} />
          <p className="text-xs text-muted-foreground">
            当前展示：第 {article.release} 期{article.release_label ? `〈${article.release_label}〉` : ''}。
            {article.versioning === 'latest_only'
              && ' 本篇策略为只保留最新版本：下面列出的历史期次读者现在看不到，但可以切成当前展示那一期。'}
          </p>

          {!releases && !error && <LoadingRow text="正在取已发布期次…" />}
          {!!releases?.length && (
            <div className="space-y-1.5">
              <Label>切到哪一期</Label>
              <Select value={target} onValueChange={setTarget}>
                <SelectTrigger aria-label="选择要切回的期次"><SelectValue placeholder="选择已发布期次" /></SelectTrigger>
                <SelectContent>
                  {options.map(r => (
                    <SelectItem key={r.release} value={String(r.release)}>
                      第 {r.release} 期{r.label ? `·${r.label}` : ''}
                      {r.released_at ? `（发布于 ${r.released_at.slice(0, 10)}）` : ''}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">
                附件不进版本链：切换期次只换正文，条目上的附件清单不变。
              </p>
            </div>
          )}
          {!!releases?.length && !options.length && (
            <p className="text-xs text-muted-foreground">没有其他已发布期次可切（只有一期时不需要切换）。</p>
          )}

          <div className="space-y-1">
            <Label htmlFor="rollback-reason">切换原因（必填，写入审核流水与审计）</Label>
            <Textarea id="rollback-reason" rows={2} value={reason}
              onChange={e => setReason(e.target.value)}
              placeholder="例如：上一期的价格数据已修正，本期排版有误先切回" />
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>取消</Button>
          <Button onClick={submit}
            disabled={busy || !target || !reason.trim() || !options.length}>
            {busy ? '处理中' : '确认切换'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** 目录选择里的“不放进子目录”哨兵（实现细节，不外泄内部 id） */
const ROOT_DIR = '__root__';

/**
 * 「同步到知识库」弹框：选目标库（+ 可选目录），把已发布内容推成语料。
 *
 * 五条口径：
 * 1. **目标库用选择框**，选项来自 `/content/knowledge-bases`（显业务名）；
 *    不可写的库**照样列出但置灰**并带上服务端原因 —— 藏掉选项会让人以为“没这个库”。
 * 2. **未发布不开口**：进不了语料是业务规则，不是错误；这里直接说明原因，
 *    而不是让人选完库再被服务端 409 打回来。
 * 3. **换库要说后果**：旧库里那篇会被撤掉（一篇只对一个库），提前的文案与
 *    服务端回执的 warning 同一口径，不出现“选了才发现旧库还留一份”。
 * 4. 发布与同步是两个动作：本框只写语料，不附带发布/下架。
 * 5. 服务端拒绝原因（同名人工语料、云端不可达）原样显示，不转成“同步失败请重试”。
 */
function KbSyncDialog({ article, onClose, onDone }: {
  article: ContentArticle | null;
  onClose: () => void;
  onDone: (msg: string, warnings: string[]) => void;
}) {
  const [bases, setBases] = useState<KbBase[]>([]);
  const [kbName, setKbName] = useState('');
  const [parentId, setParentId] = useState(ROOT_DIR);
  const [folders, setFolders] = useState<Array<{ source_id: string; title: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [treeError, setTreeError] = useState('');

  const open = !!article;
  const live = !!article?.is_live;

  useEffect(() => {
    if (!open) return;
    setKbName(''); setParentId(ROOT_DIR); setFolders([]); setError(''); setTreeError('');
    setLoading(true);
    kbDocsApi.bases()
      .then(res => setBases(res.data.items))
      .catch(err => { setError(kbDocError(err)); setBases([]); })
      .finally(() => setLoading(false));
  }, [open, article?.slug]);

  if (!article) return null;
  const current = article.kb_sync;
  const target = bases.find(b => b.name === kbName);
  const switching = !!current && !!kbName && current.kb_name !== kbName;

  const pickKb = (name: string) => {
    setKbName(name); setParentId(ROOT_DIR); setFolders([]); setTreeError('');
    // 目录清单只在真要往哪放时拉一次；拉失败不阻断“放根目录”这个显式选择
    kbDocsApi.tree(name)
      .then(res => setFolders(res.data.nodes.filter(n => n.is_dir).map(n => ({ source_id: n.source_id, title: n.title }))))
      .catch(err => setTreeError(kbDocError(err)));
  };

  const submit = async () => {
    setBusy(true); setError('');
    try {
      const res = await kbDocsApi.syncFromArticle(kbName, article.slug,
        parentId === ROOT_DIR ? '' : parentId);
      onDone(res.data.message, res.data.warnings ?? []);
    } catch (err) {
      setError(kbDocError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={v => { if (!v) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>同步到知识库</DialogTitle>
          <DialogDescription>
            把这篇的已发布版本推成 AS-BOT 可检索的语料。发布与同步是两个动作：
            发布只决定门户展不展示，同步才动知识库。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <ErrorBanner message={error} />

          {!live && (
            <div className="rounded-lg border bg-muted/30 p-3 text-xs leading-5">
              只有「已发布」的内容能进知识库。草稿与已下架的稿子还没定稿，推成语料会让
              AS-BOT 拿未审内容回答用户 —— 请先发布（或让审核人发布）再同步。
            </div>
          )}

          {live && (
            <>
              <div className="rounded-lg border bg-muted/30 p-3 text-xs leading-5">
                <p className="flex items-center gap-1.5">
                  当前语料状态：<KbSyncBadge sync={current} />
                  {current ? <span className="text-muted-foreground">库「{current.kb_name}」· 文档 {current.doc_title}</span> : null}
                </p>
                <p className="mt-1.5 text-muted-foreground">
                  语料文档名按条目标识（slug）生成，改标题不会新建副本；重复同步是更新同一篇，不是再传一份。
                </p>
              </div>

              <div className="space-y-1">
                <Label>目标知识库</Label>
                {loading && <LoadingRow text="正在加载知识库清单" />}
                {!loading && (
                  <Select value={kbName} onValueChange={pickKb}>
                    <SelectTrigger aria-label="选择目标知识库"><SelectValue placeholder="选择要同步进哪个知识库" /></SelectTrigger>
                    <SelectContent>
                      {bases.length === 0 && <SelectItem value="__none__" disabled>还没登记知识库</SelectItem>}
                      {bases.map(b => {
                        const usable = b.supports_docs && b.can_edit;
                        return (
                          <SelectItem key={b.name} value={b.name} disabled={!usable}>
                            {b.name}{usable ? '' : `（${b.reason || '不可写'}）`}
                          </SelectItem>
                        );
                      })}
                    </SelectContent>
                  </Select>
                )}
                {target && !target.supports_docs && (
                  <p className="text-[11px] text-destructive">{target.reason}</p>
                )}
              </div>

              {kbName && (
                <div className="space-y-1">
                  <Label>放入目录</Label>
                  <Select value={parentId} onValueChange={setParentId}>
                    <SelectTrigger aria-label="选择放入目录"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value={ROOT_DIR}>根目录</SelectItem>
                      {folders.map(f => <SelectItem key={f.source_id} value={f.source_id}>{f.title}</SelectItem>)}
                    </SelectContent>
                  </Select>
                  {/* 目录拉不到时不静默当成“没子目录”：说清楚，并保留“根目录”这个真实选项 */}
                  {treeError && (
                    <p className="text-[11px] text-amber-700 dark:text-amber-400">
                      目录清单加载失败（{treeError}），只能先放根目录；重试可拿到子目录。
                    </p>
                  )}
                </div>
              )}

              {switching && (
                <p className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-xs leading-5 text-amber-700 dark:text-amber-400">
                  一篇内容只能对一个库。本次会在新库建立语料，并撤掉旧库「{current?.kb_name}」里那篇；
                  撤不掉时回执里会作为提醒说出来（两处都有同名语料会被重复检索命中）。
                </p>
              )}
            </>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>关闭</Button>
          {live && (
            <Button onClick={submit} disabled={busy || !kbName || !target?.can_edit}>
              {busy ? '同步中' : current && !current.stale && !switching ? '重新同步' : '开始同步'}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * 批量确认框：执行前把「这次动哪几篇、哪几篇不动、为什么不动」摊开说清。
 *
 * 三条纪律：
 * 1. **不适用项显式列出**（带当前状态文案），不静默从勾选里剔掉 —— 否则用户会以为
 *    "勾上了就都发了"，而实际只发了一部分（与 no-silent-degradation 同一口径）；
 * 2. 破坏性动作（删除）走 destructive 并逐条列出名单，不只给一个数字；
 * 3. 结论与原因以服务端回执为准，这里只负责执行前的口径说明；服务端仍逐条裁决。
 */
function BatchDialog({ kind, articles, onClose, onDone }: {
  kind: BatchKind;
  articles: ContentArticle[];
  onClose: () => void;
  onDone: (res: BatchResult) => void;
}) {
  const meta = BATCH_META[kind];
  const targets = articles.filter(a => batchApplies(a, kind));
  const skipped = articles.filter(a => !batchApplies(a, kind));
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // 下架/上架有说明性原因（选填，写进每篇的审核流水）；发布不接受原因（非免审直发时用不上）
  const hasReason = kind === 'archive' || kind === 'republish';

  const submit = async () => {
    setBusy(true);
    setError('');
    try {
      const slugs = targets.map(a => a.slug);
      const res = kind === 'submit'
        ? await contentApi.batchSubmit(slugs)
        : kind === 'delete'
          ? await contentApi.batchDelete(slugs)
          : await contentApi.batchPublish(kind, slugs, reason.trim());
      onDone(res.data);
    } catch (err) {
      // 整批被拒（未勾选/超上限/无权）原样显示，不转成"操作失败请重试"
      setError(contentError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={v => { if (!v && !busy) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{meta.label}</DialogTitle>
          <DialogDescription>{meta.hint}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <ErrorBanner message={error} />

          <div className="space-y-1">
            <p className="text-xs font-medium">本次处理 {targets.length} 篇</p>
            <div className="max-h-36 space-y-1 overflow-y-auto rounded-md border p-2 text-xs">
              {targets.map(a => (
                <div key={a.slug} className="truncate" title={a.title}>· {a.title}</div>
              ))}
            </div>
          </div>

          {skipped.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-medium text-amber-700 dark:text-amber-400">
                另有 {skipped.length} 篇当前状态不适用，本次不处理（保留勾选，换对应动作即可）
              </p>
              <div className="max-h-28 space-y-1 overflow-y-auto rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-xs">
                {skipped.map(a => (
                  <div key={a.slug} className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate" title={a.title}>· {a.title}</span>
                    <span className="flex-shrink-0 text-muted-foreground">{statusText(a)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {hasReason && (
            <div className="space-y-1">
              <Label htmlFor="batch-reason">
                {kind === 'archive' ? '下架原因' : '上架原因'}（选填，写入每篇的审核流水）
              </Label>
              <Textarea id="batch-reason" rows={2} value={reason} onChange={e => setReason(e.target.value)}
                placeholder={kind === 'archive'
                  ? '例如：引用的法规条款已更新，待复核后重发'
                  : '例如：法规条款已复核，口径无需修改'} />
            </div>
          )}

          {kind === 'delete' && (
            <p className="text-[11px] text-destructive">
              删除会一并移除草稿、它的全部历史版本与挂在上面的附件（附件文件同时从存储里删掉），此操作不可撤销。
            </p>
          )}

          {kind === 'publish' && (
            <p className="text-[11px] text-muted-foreground">
              批量不代开「免审直发」：那条例外需要逐篇写明原因并入审计，所以未审核通过的会被逐条拒掉并说明下一步。
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>取消</Button>
          <Button variant={meta.destructive ? 'destructive' : 'default'} onClick={submit}
            disabled={busy || targets.length === 0}>
            {busy ? '处理中' : `确认${meta.verb} ${targets.length} 篇`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * 批量回执：服务端汇总 + 逐条失败原因 + 逐条提醒。
 *
 * 部分成功不能只报一个数字：失败要点名到标题并带上服务端原话（未审核不得发布 /
 * 已上线不能删 / 仍在审核中），否则用户只能挨个猜哪篇没动。提醒（语料还在库里、
 * 附件没删干净、软提醒待确认）同理不得被汇总文案顶掉。
 */
function BatchResultBanner({ result, onDismiss }: {
  result: BatchResult;
  onDismiss: () => void;
}) {
  const partial = result.failed_count > 0;
  const warned = result.succeeded.filter(s => s.warning || (s.warnings?.length ?? 0) > 0);
  return (
    <div className={`rounded-xl border p-4 text-sm ${
      partial ? 'border-amber-500/40 bg-amber-500/5' : 'border-emerald-500/40 bg-emerald-500/5'}`}>
      <p className="font-medium">{result.message}</p>
      {result.failed.length > 0 && (
        <ul className="mt-2 space-y-1">
          {result.failed.slice(0, 8).map(f => (
            <li key={f.slug} className="text-destructive">「{f.title || '该条目'}」{f.reason}</li>
          ))}
          {result.failed.length > 8 && (
            <li className="text-xs text-muted-foreground">
              …另有 {result.failed.length - 8} 条未处理，可缩小勾选范围分批重试
            </li>
          )}
        </ul>
      )}
      {warned.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-amber-700 dark:text-amber-400">
          {warned.slice(0, 5).map(s => (
            <li key={s.slug}>
              「{s.title || '该条目'}」{s.warning || `有 ${s.warnings?.length ?? 0} 条提醒，审核人仍需逐条确认`}
            </li>
          ))}
          {warned.length > 5 && (
            <li>…另有 {warned.length - 5} 条带提醒</li>
          )}
        </ul>
      )}
      <div className="mt-2 flex justify-end">
        <Button size="sm" variant="ghost" onClick={onDismiss}>关闭</Button>
      </div>
    </div>
  );
}

export function ConfirmDialog({ open, title, description, confirmLabel, destructive, onClose, onConfirm }: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  destructive?: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  if (!open) return null;
  return (
    <Dialog open onOpenChange={v => { if (!v) onClose(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button variant={destructive ? 'destructive' : 'default'} onClick={onConfirm}>{confirmLabel}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
