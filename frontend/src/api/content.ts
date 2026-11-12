import client from './client';

/**
 * 内容中心 API 契约（backend/modules/platform/api/content.py）。
 *
 * 四个口径值得在前端记住，它们不是实现细节而是产品语义：
 * - **两套口径并存**：`status` 描述的是 **head（编辑中版本）** 的工作流态；「门户现在展示
 *   哪一版」看 `release` / `release_label`（期次 = 已发布过的那个 revision）。一次发布
 *   产生一期，期次号在本篇内单调递增，所以「已发布但 status=draft」（发布后又改了稿）
 *   是完全正常的组合：徽标要两个字段一起读，不能只看 status。
 * - `is_live` 只回答「门户能不能看到」；`history_hidden` 回答「读者能不能翻历史期次」
 *   （`versioning='latest_only'` 时只收读者展示面：编排面版本清单、审核流水、切换展示
 *   版本都还在，不删任何数据）。
 * - 已下架（archived）能不能一键重新上架、能不能切到别一期，**由服务端算**
 *   （`can_republish` / `head_changed_since_archive` / `can_rollback`）：判据依赖「下架时在线
 *   的那一版」与「本篇已发布几期」，那些 revision 属内部标识不下发，前端拿不到也不该自己推。
 * - 权限由服务端按权限码裁决（article:manage/review/publish；面向读者的
 *   article:read 属资讯门户模块）。报告中心的「提交到内容管理审核」也落在 article:manage，
 *   所以入口的可用性判据是 `has('article:manage')`，不是新造一个码。前端只用来
 *   决定「按钮给不给」，不用来代替裁决；403 一律原样显示后端文案，不静默藏起来。
 * - 响应体不含内部标识：正文随详情接口给，版本走版本清单，历史期次只下发**期次号**
 *   （业务可读、可直接放进 URL），标识一律是 slug/栏目 key/标签名。
 */

export type ArticleStatus = 'draft' | 'in_review' | 'approved' | 'published' | 'archived';
/** `promoted` 已退役（提升通道落地为站内正文/HTML 版式）：存量行仍可读，写入口被服务端拒 */
export type ArticleKind = 'markdown' | 'html' | 'link' | 'file' | 'promoted';
/** 读者可见性策略（与服务端 VERSIONING_LABELS 同一份取值，改一边必须改另一边） */
export type ArticleVersioning = 'keep_history' | 'latest_only';

export const VERSIONING_LABELS: Record<ArticleVersioning, string> = {
  keep_history: '保留历史期次',
  latest_only: '只保留最新版本',
};

/** 策略的业务解释：选择框与发布弹框都要说清“选了会怎样”，不能只给个名字 */
export const VERSIONING_HINTS: Record<ArticleVersioning, string> = {
  keep_history: '读者可以在内容页翻看历史期次（适合月报、季度分析这类主题递进的内容）',
  latest_only: '只把最新版本给读者看；历史期次仍留在编排面（版本清单、审核流水、切换展示版本），不删数据',
};

export const STATUS_LABELS: Record<ArticleStatus, string> = {
  draft: '草稿',
  in_review: '待审核',
  approved: '已通过待发布',
  published: '已发布',
  archived: '已下架',
};

/** 状态 → 徽章配色（沿用 tailwind 语义色，不硬编码色值） */
export const STATUS_TONES: Record<ArticleStatus, string> = {
  draft: 'bg-muted text-muted-foreground',
  in_review: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
  approved: 'bg-sky-500/15 text-sky-600 dark:text-sky-400',
  published: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  archived: 'bg-muted text-muted-foreground line-through',
};

export interface ContentCategory {
  id: number;
  key: string;
  label: string;
  description: string;
  icon: string;
  color: string;
  sort: number;
  is_active: number;
  count?: number;
}

export interface ContentTag {
  id: number;
  tag: string;
  sort: number;
  is_active: number;
  count: number;
}

export interface ContentRef {
  title: string;
  url: string;
}

/**
 * 内容附件（kind=file 的载体）。标识是 file_id（服务端行 id），**不含 object_key**。
 *
 * 没有 `view_mode`/预览地址不是漏做：服务端下载头恒为 `attachment`（把上传的
 * HTML/PDF 以本站源 inline 渲染 = 存储型 XSS），所以前端**不该**有预览入口 ——
 * 画一个「预览」按钮再让它失灵，比只给「下载」更坑（office 版式也还原不了）。
 */
export interface ContentFile {
  file_id: number;
  filename: string;
  /** 类型徽章文案（Word / PPT / Excel / PDF / HTML），来自服务端白名单表 */
  label: string;
  mime: string;
  size: number;
  uploaded_at: string;
}

/** 与服务端 CONTENT_FILE_TYPES 同一份清单（改一边必须改另一边，否则选了就被 400 拒） */
export const CONTENT_FILE_ACCEPT = '.pdf,.html,.htm,.doc,.docx,.ppt,.pptx,.xls,.xlsx';
export const CONTENT_FILE_MAX_MB = 50;
export const CONTENT_FILE_MAX_COUNT = 10;

export interface GateItem {
  code: string;
  message: string;
  line?: number;
  /** 仅审核视图带出：审核人是否已逐条确认 */
  acknowledged?: boolean;
}

/**
 * 知识库语料同步状态（服务端 `content_service._kb_sync_of` 一份实现，列表与详情同源）。
 *
 * `stale` 按版本判：已发布版与已同步版不是同一个 revision 就是“有改动未同步”。
 * 云端 `source_id` 属内部标识，不下发（护栏 §7），所以这里只能按库名 + 文档名定位。
 */
export interface KbSyncState {
  kb_name: string;
  doc_title: string;
  /** synced（已成功）/ failed（上次尝试失败） */
  state: string;
  synced_at: string;
  stale: boolean;
  last_error: string;
}

export interface ContentArticle {
  id: number;
  slug: string;
  title: string;
  summary: string;
  category_id: number;
  category_label: string;
  category_icon: string;
  category_color: string;
  tags: string[];
  kind: ArticleKind;
  kind_label: string;
  link_url: string;
  link_label: string;
  refs: ContentRef[];
  pinned: number;
  featured: number;
  status: ArticleStatus;
  is_live: boolean;
  /** 读者可见性策略（只收读者展示面，不影响 head/live 指针与审核流水） */
  versioning: ArticleVersioning;
  versioning_label: string;
  /**
   * 当前在线那一期（第几期 + 发布人给的人话名）。`release=0` = 从未发布过。
   * 下发的是期次号，不是 revision id：读者面寻址与 URL 参数都用它。
   */
  release: number;
  release_label: string;
  /** 本篇已发布过几期（=1 时不画历史期次下拉：没有可切的另一期） */
  release_count: number;
  /** 策略为「只保留最新」：读者拿不到历史期次（编排面仍能看到全量） */
  history_hidden: boolean;
  /** 「切换展示版本」有可切目标：至少两期已发布且未下架（服务端算，前端不自推） */
  can_rollback: boolean;
  has_pending_change: boolean;
  /**
   * 已下架条目能不能一键重新上架：服务端按「下架时在线的那一版」是否存在判定。
   * 放回去的就是当初审核通过才上线的那一版，所以不需要重新审核（权限仍由 article:publish 裁决）。
   */
  can_republish: boolean;
  /** 下架之后 head 又改过（或追溯不到下架那一版）：这一版没审过，只能走审核/免审直发 */
  head_changed_since_archive: boolean;
  review_by: string;
  published_at: string;
  archived_at: string;
  view_count: number;
  author: string;
  updated_by: string;
  updated_at: string;
  version: number;
  body?: string;
  /** 仅 `release=N` 读历史期次时带出：正在看的那一期（与正文同源，不另开一次查询） */
  viewing_release?: number;
  viewing_release_label?: string;
  viewing_is_current?: boolean;
  /** 附件清单：详情/预览/审核稿件接口带出；列表接口不带（每行一查就是 N+1） */
  files?: ContentFile[];
  /** 知识库语料同步状态：服务端由水位线算出，未同步过是 null（不是“已同步但空”） */
  kb_sync?: KbSyncState | null;
  // ── 审核视图附加 ──
  gate_hard?: GateItem[];
  gate_warnings?: GateItem[];
  submitted_by?: string;
  is_first_release?: boolean;
}

export interface RevisionItem {
  id: number;
  change_note: string;
  created_by: string;
  created_at: string;
  is_live: boolean;
  /** 期次三列只在**已发布过**的那个版本上非空：编辑器据此分区「已发布期次 / 编辑历史」 */
  release: number;
  release_label: string;
  is_released: boolean;
  released_at: string;
}

/**
 * 一期已发布内容（读者面与编排面共用同一个形状）。
 *
 * 这里只有期次号与人话名，没 revision id：历史期次要能直接放进 URL 分享（`?release=3`）。
 */
export interface ReleaseItem {
  release: number;
  label: string;
  released_at: string;
  is_current: boolean;
}

export interface ReleaseList {
  items: ReleaseItem[];
  versioning: ArticleVersioning;
  versioning_label: string;
  /** 清单已被策略收过：读者面只拿到当前这一期（UI 要说明“只发布最新版本”，不能假装只有一期） */
  history_hidden: boolean;
  /** 附件不进版本链（全局已知取舍）：切期次不换附件，这句话必须由 UI 显式说出来 */
  attachments_shared: boolean;
}

export interface ReviewLogItem {
  action: string;
  comment: string;
  gate_warnings: GateItem[];
  actor: string;
  actor_channel: string;
  revision_id: number;
  created_at: string;
}

export interface ArticleList {
  items: ContentArticle[];
  total: number;
  page: number;
  page_size: number;
}

export interface ReviewQueue extends ArticleList {
  /** 到期复核清单（已上线但 review_by 已过期的内容） */
  review_expired: ContentArticle[];
}

/**
 * 状态机动作的统一返回体。
 *
 * 动作接口**不**返回整篇文章：状态转换的结论就是 `{status, message}`，前端拿到后
 * 重新拉清单。把它们谎称成 ContentArticle 会让调用点读到 undefined 字段。
 */
export interface ActionResult {
  status: ArticleStatus;
  message: string;
  /** 仅「提交审核」带出：不阻断但审核人必须逐条确认的提醒 */
  warnings?: GateItem[];
  /** 旁路没做干净的提醒（下架时语料还在库里、删除时附件没删掉），必须拼在结论后显示 */
  warning?: string;
  /** 发布/切换展示版本带出：本次生效的那一期（回执要说得出“现在是第几期”） */
  release?: number;
  release_label?: string;
  release_count?: number;
}

/**
 * 批量动作的五个取值（与服务端 `_BATCH_KINDS` 同一份清单）。
 *
 * 语义与单条完全一致，只是把同一个动作按勾选项逐条跑一遍：同一个状态机、同一道门、
 * 同一条审计。批量**不**提供免审直发 —— 那是需要逐条写明原因的显式例外，做成批量开关
 * 就等于给分级审核开了个暗门；未审核的条目会被逐条拒掉并说明下一步。
 */
export type BatchKind = 'submit' | 'publish' | 'republish' | 'archive' | 'delete';

export interface BatchSucceededItem {
  slug: string;
  /** 业务可读名（回执按标题点名，不拿裸 slug 面用户：ui-resource-display §1） */
  title: string;
  /** 转换后的 head 工作流态；删除动作没有状态，回空串 */
  status: ArticleStatus | '';
  message: string;
  /** 该条的旁路提醒（语料还在库里、附件没删干净），不得被汇总文案顶掉 */
  warning?: string;
  /** 该条提交审核带出的软提醒：审核人仍需逐条确认，批量只批量“提交” */
  warnings?: GateItem[];
}

export interface BatchFailedItem {
  slug: string;
  title: string;
  /** 逐条拒因（来自单条动作本身：未审核不得发布、已上线不能删、仍在审核中） */
  reason: string;
}

export interface BatchResult {
  action: BatchKind;
  succeeded: BatchSucceededItem[];
  failed: BatchFailedItem[];
  success_count: number;
  failed_count: number;
  /** 服务端汇总文案（结论以服务端为准，前端不另写一套） */
  message: string;
}

export interface ArticleWritePayload {
  title?: string;
  slug?: string;
  summary?: string;
  /** 栏目 key（业务标识，不传内部 id） */
  category?: string;
  tags?: string[];
  refs?: ContentRef[];
  kind?: ArticleKind;
  body?: string;
  link_url?: string;
  link_label?: string;
  pinned?: number;
  featured?: number;
  review_by?: string;
  change_note?: string;
  /** 读者可见性策略（编辑器「版本策略」选择框）；非法取值服务端直接拒，不静默回默认 */
  versioning?: ArticleVersioning;
  expected_version?: number;
}

export interface ArticleQuery {
  category?: string;
  /** 排除栏目（key 或 id）：首页资讯流用它把「关于本公司」让给简介卡，服务端同一口径减总数 */
  exclude_category?: string;
  kind?: string;
  tag?: string;
  q?: string;
  /**
   * 工作流态。空 = 只看门户在线内容；`all` = **不加状态条件**（含草稿），
   * 仅对具备编辑权的调用方生效 —— 内容管理页选「全部状态」必须传 all，
   * 传空串会得到一份"看起来少了几篇草稿"的清单。
   */
  status?: '' | 'all' | ArticleStatus;
  mine?: string;
  /** 服务端只接受这三个值（latest/hot/rec），写错会被直接拒而不是默默换默认 */
  sort?: 'latest' | 'hot' | 'rec';
  page?: number;
  page_size?: number;
}

/**
 * 提升预判（`GET /content/promotions/match`）：点下去**之前**就要看见“会追加到哪一篇的第几期之后”。
 *
 * 归集口径（报表侧 series_key）只有服务端一份，前端不自己猜标题、不自己拼系列：
 * “提交后才发现历史版本被拆成两条内容”就是这个预判没做。
 */
export interface PromotePreview {
  title: string;
  /** 建议摘要（可改）：服务端从正文里抽第一段像正文的文字 */
  summary: string;
  kind: ArticleKind;
  kind_label: string;
  body_chars: number;
  /** 命中的已有条目：非 null = 本次作为新一期追加到它；null = 本次新建一篇 */
  matched: {
    slug: string;
    title: string;
    status: ArticleStatus;
    release_count: number;
  } | null;
  next_release_after: number;
  /** 服务端给的归集结论（弹框里原样回显，不要换成前端自己的措词） */
  reason: string;
  versioning_options: { value: ArticleVersioning; label: string }[];
  categories: { key: string; label: string }[];
}

/** 提升回执：只有 slug 与业务名，没报表 id / 条目 id / revision id。 */
export interface PromoteResult {
  slug: string;
  title: string;
  status: ArticleStatus;
  /** false = 追加到已有条目的新版本；true = 本次新建了一篇草稿 */
  created: boolean;
  /** 本次是否真的开了一版：正文与已有那一版一字不差时没开（回执不得谎称“已追加”） */
  new_version: boolean;
  /** 门禁/审核那一步的结论分开说：submitted=false 就是「草稿已存但未进审核」 */
  submitted?: boolean;
  /** 逐条硬阻断原因（必须一条条列出来，与内容中心提交页同一形状） */
  gate_blocked: string[];
  warnings?: GateItem[];
  versioning: ArticleVersioning;
  versioning_label: string;
  release: number;
  release_label: string;
  release_count: number;
  is_live: boolean;
  message: string;
}

export const contentApi = {
  // ── 读 ──
  categories: (includeInactive = false) =>
    client.get<{ items: ContentCategory[] }>('/content/categories',
      { params: { include_inactive: includeInactive ? 1 : 0 } }),
  tags: (includeInactive = false) =>
    client.get<{ items: ContentTag[] }>('/content/tags',
      { params: { include_inactive: includeInactive ? 1 : 0 } }),
  list: (params: ArticleQuery = {}) =>
    client.get<ArticleList>('/content/articles', { params }),
  /**
   * revision='head' 是编辑预览，仅可编辑者可得。
   * `release=N` 读已发布的第 N 期（历史期次）：与 revision='head' 互斥，服务端会拒同时给。
   * 策略为只保留最新时，读者读旧期会被当成「不存在」拒（不区分“存在但不可读”）。
   * countView 默认关：阅读计数是门户行为，编排页每次查看都 +1 会把
   * 「最多阅读」排成「最近被同事审过的那篇」。
   */
  detail: (slug: string, revision: 'live' | 'head' = 'live', countView = false, release = 0) =>
    client.get<ContentArticle>(`/content/articles/${encodeURIComponent(slug)}`,
      { params: { revision, count_view: countView ? 1 : 0, ...(release > 0 ? { release } : {}) } }),
  /**
   * 已发布期次清单（同一个端点，服务层按身份分口径）：
   * 可编辑者拿全量（含被策略隐藏的期次），读者在 `latest_only` 下只拿当前这一期 + `history_hidden`。
   */
  releases: (slug: string) =>
    client.get<ReleaseList>(`/content/articles/${encodeURIComponent(slug)}/releases`),
  /** 相关推荐属门户能力，本批无页面调用；端点保留以免门户批次再改后端 */
  related: (slug: string) =>
    client.get<{ items: ContentArticle[] }>(`/content/articles/${encodeURIComponent(slug)}/related`),
  revisions: (slug: string) =>
    client.get<{ items: RevisionItem[] }>(`/content/articles/${encodeURIComponent(slug)}/revisions`),
  history: (slug: string) =>
    client.get<{ items: ReviewLogItem[] }>(`/content/articles/${encodeURIComponent(slug)}/history`),

  // ── 写（article:manage）──
  create: (data: ArticleWritePayload) =>
    client.post<ContentArticle>('/content/articles', data),
  update: (slug: string, data: ArticleWritePayload) =>
    client.put<ContentArticle>(`/content/articles/${encodeURIComponent(slug)}`, data),
  remove: (slug: string) =>
    // warning：条目已删但对象存储里的附件没删掉（服务端必须显式说出来，不得静默成纯成功）
    client.delete<{ deleted: boolean; warning?: string }>(`/content/articles/${encodeURIComponent(slug)}`),
  submit: (slug: string) =>
    client.post<ActionResult>(`/content/articles/${encodeURIComponent(slug)}/submit`),

  // ── 附件（上传/清单/删除属编排面 article:manage；下载有两条路）──
  /**
   * 'articles' = 编排面（需 article:manage），'reviews' = 审核面（只需 article:review）。
   * 两个前缀各开一份下载路由不是冗余：只勾审核权的角色拿不到 articles*，
   * 看不到附件的审核是空承诺（附件形态的稿子就没法审）。
   */
  files: (slug: string) =>
    client.get<{ items: ContentFile[] }>(`/content/articles/${encodeURIComponent(slug)}/files`),
  uploadFile: (slug: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return client.post<{ file: ContentFile; message: string }>(
      `/content/articles/${encodeURIComponent(slug)}/files`, form,
      { headers: { 'Content-Type': 'multipart/form-data' } });
  },
  removeFile: (slug: string, fileId: number) =>
    client.delete<{ deleted: boolean; filename: string; warning?: string }>(
      `/content/articles/${encodeURIComponent(slug)}/files/${fileId}`),

  createCategory: (data: Partial<ContentCategory>) =>
    client.post<ContentCategory>('/content/categories', data),
  updateCategory: (id: number, data: Partial<ContentCategory>) =>
    client.put<ContentCategory>(`/content/categories/${id}`, data),
  removeCategory: (id: number) =>
    client.delete<{ success: boolean }>(`/content/categories/${id}`),
  createTag: (data: { tag: string; sort?: number; is_active?: number }) =>
    client.post<ContentTag>('/content/tags', data),
  updateTag: (id: number, data: { tag?: string; sort?: number; is_active?: number }) =>
    client.put<ContentTag>(`/content/tags/${id}`, data),
  removeTag: (id: number) =>
    client.delete<{ success: boolean }>(`/content/tags/${id}`),

  // ── 审核与发布（独立前缀 = 独立权限码）──
  pendingReviews: (params: { page?: number; page_size?: number } = {}) =>
    client.get<ReviewQueue>('/content/reviews/pending', { params }),
  /** 审核人读稿：走 /reviews* 让 article:review 自己裁决，不必再具备编辑权 */
  reviewDraft: (slug: string) =>
    client.get<ContentArticle>(`/content/reviews/${encodeURIComponent(slug)}/draft`),
  approve: (slug: string, comment: string, acknowledged: GateItem[]) =>
    client.post<ActionResult>(`/content/reviews/${encodeURIComponent(slug)}/approve`,
      { comment, acknowledged }),
  reject: (slug: string, comment: string) =>
    client.post<ActionResult>(`/content/reviews/${encodeURIComponent(slug)}/reject`, { comment }),
  publish: (slug: string, body: { expected_version?: number; waive_review?: boolean; reason?: string; release_label?: string } = {}) =>
    client.post<ActionResult>(`/content/publish/${encodeURIComponent(slug)}`, { action: 'publish', ...body }),
  archive: (slug: string, reason: string, expected_version?: number) =>
    client.post<ActionResult>(`/content/publish/${encodeURIComponent(slug)}`,
      { action: 'archive', reason, expected_version }),
  /**
   * 重新上架：把**下架时在线的那一版**放回门户（服务端不拿 head 上线，下架后又改的
   * 正文留在草稿位，回执里会把这件事显式说出来）。与发布/下架同一个端点、同一个权限码。
   */
  republish: (slug: string, reason: string, expected_version?: number) =>
    client.post<ActionResult>(`/content/publish/${encodeURIComponent(slug)}`,
      { action: 'republish', reason, expected_version }),
  /**
   * 切换展示版本（回滚）：把门户现在展示的那一期切到已经发布过的第 `release` 期。
   *
   * 与 republish 同一个论证：切过去的那一版本来就审过才上线，所以不重审；但如果草稿位
   * 还有没审过的改动，那些改动**不会跟着上线**（回执里会显式说出来）。服务端只认期次号，
   * 不接受 revision id；没发布过的版本切不了。
   */
  rollbackRelease: (slug: string, release: number, reason: string, expected_version?: number) =>
    client.post<ActionResult>(`/content/publish/${encodeURIComponent(slug)}`,
      { action: 'rollback', release, reason, expected_version }),

  // ── 报告中心提升通道（同一份 article:manage 裁决，不新增权限码）──
  /** 提交前的归集预判：会追加到哪一篇的第几期之后，还是会新建 */
  promoteMatch: (reportId: number) =>
    client.get<PromotePreview>('/content/promotions/match', { params: { report_id: reportId } }),
  /**
   * 把一份报告提交到内容管理审核（复制正文，不搬报表快照）。
   *
   * 没期次名字段：期次在发布那一步才产生，由发布人命名（开两个入口等于两处各存一份）。
   */
  promoteFromReport: (data: { report_id: number; category?: string; title?: string;
                              summary?: string; versioning?: ArticleVersioning; submit?: boolean }) =>
    client.post<PromoteResult>('/content/promotions', data),

  // ── 批量（不新增权限码：逐条走单条实现，端点落在单条已登记的 api_pattern 里）──
  // 三条通道各自固定动作，避免一个端点里再判一次“这个 action 该归哪个权限码”：
  //   batch-submit / batch-delete → /articles*（article:manage）
  //   publish-batch              → /publish*（article:publish）
  /** 批量提交审核：不合格的逐条回原因（已在审中 / 门禁硬阻断） */
  batchSubmit: (slugs: string[]) =>
    client.post<BatchResult>('/content/articles/batch-submit', { slugs }),
  /** 批量删除：已上线的逐条拒（必须先下架），不可撤销 */
  batchDelete: (slugs: string[]) =>
    client.post<BatchResult>('/content/articles/batch-delete', { slugs }),
  /**
   * 批量发布 / 上架 / 下架。`reason` 对整批共用（写进每条的审核流水），不填不阻断。
   * 没有 `waive_review` 参数：免审直发是逐条写明原因的显式例外，不开放批量。
   */
  batchPublish: (action: 'publish' | 'archive' | 'republish', slugs: string[], reason = '') =>
    client.post<BatchResult>('/content/publish-batch', { action, slugs, reason }),
};

/**
 * 取错误文案。后端把门禁拒绝写成 `{status, detail}`，422 的 detail 已由 client
 * 拦截器压成字符串；这里只兜底网络层无响应的情形（不能把失败静默成空串）。
 */
export function contentError(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown }; status?: number }, message?: string };
  const detail = e?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (detail) return JSON.stringify(detail);
  return e?.message || '请求失败，请稍后重试';
}

/**
 * 下载内容附件。与「我的资产」同口径：对象存储直链优先（50MB 级发布物不经服务端
 * 回源），仅当直链路由回 400（本地存储模式，未配对象存储）才回落代理下载。
 *
 * 其余失败（403/410/502）**原样抛出**给调用点显示：不能静默换道，否则“权限没配”
 * 与“本地模式”在用户眼里长得一样，掩盖的是完全不同的根因。
 */
export async function downloadContentFile(scope: 'articles' | 'reviews', slug: string,
                                          file: Pick<ContentFile, 'file_id' | 'filename'>): Promise<void> {
  const base = `/content/${scope}/${encodeURIComponent(slug)}/files/${file.file_id}`;
  try {
    const direct = await client.get<{ url: string; filename: string }>(`${base}/download-url`);
    if (direct.data?.url) {
      window.open(direct.data.url, '_blank', 'noopener');
      return;
    }
    // 200 但无 url = 服务端契约不对，不能当成“那就走代理”悄悄过去
    throw new Error('附件直链返回为空，请重试或改用代理下载');
  } catch (err) {
    const status = (err as { response?: { status?: number } })?.response?.status;
    if (status !== 400) throw err;
  }
  const res = await client.get(`${base}/download`, { responseType: 'blob' });
  const url = URL.createObjectURL(res.data as Blob);
  try {
    const link = document.createElement('a');
    link.href = url;
    link.download = file.filename;
    link.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}
