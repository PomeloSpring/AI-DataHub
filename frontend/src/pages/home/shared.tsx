import { useCallback, useMemo, useState } from 'react';
import { Eye, ExternalLink, FileText } from 'lucide-react';
import type { ContentArticle, ContentCategory } from '@/api/content';

/**
 * 首页资讯区与阅读页共用的口径件。
 *
 * 抽这一层的理由不是"少写几行"，而是三条判据必须与内容中心同源：
 * - 栏目名/主色只能来自 `/content/categories`（ui-resource-display §1：显示用 name）；
 * - 「相对时间」与「阅读数」的展示口径在两处必须一致，否则首页说"3 天前"、
 *   阅读页说"2026-11-03"，用户会怀疑是不是两个版本；
 * - 已读状态只能有一份真值源。目前是浏览器本地（见 useReadState 的说明），
 *   绝不在组件里另算一份"看起来未读"。
 */

/**
 * 「关于本公司」栏目的稳定 key（与 seed 迁移同源，简介卡的事实源）。
 * 首页对它的分工是：右上角简介卡展示它，资讯流不重复展示（同屏两份同一条目是噪声），
 * 两处都只认这一个 key，不得在组件里各自硬编码字符串。
 */
export const ABOUT_CATEGORY = 'about';

/** 相对时间：资讯卡片只关心"新不新"，绝对日期留给阅读页 */
export function relTime(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const days = Math.floor((Date.now() - d.getTime()) / 86_400_000);
  if (days <= 0) return '今天';
  if (days === 1) return '昨天';
  if (days < 30) return `${days} 天前`;
  if (days < 365) return `${Math.floor(days / 30)} 个月前`;
  return `${Math.floor(days / 365)} 年前`;
}

/** 阅读数压缩显示：12 万写成 120k，个位数不加噪声 */
export function viewCount(n: number): string {
  const v = Number(n) || 0;
  if (v >= 1000) return `${(v / 1000).toFixed(1).replace(/\.0$/, '')}k`;
  return String(v);
}

/** 形态徽章：站内正文（Markdown 与 HTML 版式）无标，两者只是排版能力不同，
 * 对读者都不是需要预告的行为差异；只有外链与附件影响“点开能不能看到东西” */
export function kindHint(a: Pick<ContentArticle, 'kind' | 'kind_label'>) {
  if (a.kind === 'markdown' || a.kind === 'html') return null;
  if (a.kind === 'link') return { label: a.kind_label || '原文', icon: ExternalLink };
  if (a.kind === 'file') return { label: a.kind_label || '附件', icon: FileText };
  return null;
}

/**
 * 文章 → 栏目对象（徽章文案、封面主色/图标都从这里取）。
 *
 * 两级口径，都是接口数据，任何一级都不落回裸 id（ui-resource-display §1）：
 * 1. 按 `category_id` 命中 `/content/categories` 清单 —— 这是栏目名的权威来源；
 * 2. 命中不到（清单还没加载完、栏目被停用/删除）时用服务端随文章一并下发的
 *    `category_label/icon/color`（清单 SQL JOIN `adh_content_categories` 出来的同一份值）。
 * 两级都拿不到才交给 `CategoryBadge` 显示「栏目未知」。
 *
 * 抽成一处的原因是历史缺陷：资讯卡曾把**已解析出的栏目名**再喂给按 key/id 查的
 * `catOf`（`catOf(catLabel(a))`），永远查不中 → 首页每张卡都显示「栏目未知」。
 * 消费点只认这一个函数，不要再自己拼解析链。
 */
export function categoryOfArticle(
  a: Pick<ContentArticle, 'category_id' | 'category_label' | 'category_icon' | 'category_color'>,
  categories: ContentCategory[],
): ContentCategory | undefined {
  // category_id 服务端保证是整数（content_service 出参 `int(row.get("category_id") or 0)`），
  // 只按 id 命中；旧写法还比过 `c.key === a.category_id`，整数对字符串 key 永为 false，
  // 是死分支且撞 TS2367（string 与 number 无重叠），故删除。
  const hit = categories.find(c => c.id === a.category_id);
  if (hit) return hit;
  if (!a.category_label) return undefined;
  return {
    id: a.category_id, key: '', label: a.category_label, description: '',
    icon: a.category_icon, color: a.category_color, sort: 0, is_active: 1,
  };
}

/**
 * 期次标记（“第 N 期·2026-10”）：首页资讯卡与阅读页共用一份画法。
 *
 * 只读服务端下发的 `release`/`release_label`（= 当前在线那一期）：期次是发布动作产生的
 * 事实，前端不拿 published_at 自己算月份 —— 那会在“10 月发布 9 月那一期”时说错。
 * 未发布过的条目（release=0）不画：门户清单里本来只会有在线内容，真出现就是数据不对，
 * 静默画个“第 0 期”比不画更坑。
 */
export function ReleaseChip({ article }: { article: Pick<ContentArticle, 'release' | 'release_label' | 'release_count'> }) {
  if (!article.release) return null;
  return (
    <span
      title={article.release_count > 1
        ? `本篇共 ${article.release_count} 期：点进去可翻历史期次`
        : '本期内容'}
      className="rounded-md bg-sky-500/15 px-1.5 py-0.5 text-[11px] font-medium text-sky-600 dark:text-sky-400"
    >
      第 {article.release} 期{article.release_label ? `·${article.release_label}` : ''}
    </span>
  );
}

/**
 * 栏目徽章 + 封面。主色是**业务数据**（`adh_content_categories.color`，管理员在
 * 「栏目与标签」页可改），不是前端硬编码的样式常量，所以直接内联使用；
 * 其余色彩一律走主题变量（与 ui-vis-library-style 一致）。
 * 入参一律由 `categoryOfArticle` 解析得出，消费点不要自己按 label/id 拼查找。
 */
export function CategoryBadge({ category, withIcon = true }: { category?: ContentCategory; withIcon?: boolean }) {
  if (!category) {
    // 栏目被删/未加载完时不得回退成裸 id 或空串（ui-resource-display §1）
    return <span className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">栏目未知</span>;
  }
  return (
    <span
      className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-white"
      style={{ backgroundColor: category.color || undefined }}
    >
      {withIcon && category.icon ? <span aria-hidden>{category.icon}</span> : null}
      {category.label}
    </span>
  );
}

/** 阅读数 + 未读点：未读点是"有新东西"的唯一提示，读完即消失 */
export function ReadMeta({ article, unread }: { article: ContentArticle; unread: boolean }) {
  return (
    <span className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
      <span className="flex items-center gap-1">
        <Eye className="h-3 w-3" />{viewCount(article.view_count)} 阅读
      </span>
      <span>·</span>
      <span>{relTime(article.published_at || article.updated_at)}</span>
      {unread && <span className="ml-auto size-1.5 rounded-full bg-primary" title="未读" aria-label="未读" />}
    </span>
  );
}

const READ_KEY = 'home_read_articles';

/**
 * 已读状态：按浏览器本地记录（localStorage）。
 *
 * 为什么先落本地而不是服务端：门户的 `adh_content_user_state` 表还没建（属后续批次），
 * 而后端没有"已读"写接口 —— 这里如果假装记在服务端，换台电脑就"全部未读"的现象
 * 会被当成 bug 上报。本地口径是诚实的：未读点表示"这台设备上看过的不再提示"。
 * 上服务端时把这两个 hook 换成接口读写即可，消费点不用改。
 */
export function useReadState() {
  const [read, setRead] = useState<Set<string>>(() => {
    try {
      const raw = JSON.parse(localStorage.getItem(READ_KEY) || '[]');
      return new Set(Array.isArray(raw) ? raw.map(String) : []);
    } catch { return new Set(); }
  });
  const mark = useCallback((slug: string) => {
    setRead(prev => {
      if (!slug || prev.has(slug)) return prev;
      const next = new Set(prev);
      next.add(slug);
      // 只留最近 200 条：资讯长期累积不该把这份清单撑到读不出来
      try { localStorage.setItem(READ_KEY, JSON.stringify([...next].slice(-200))); } catch { /* 隐私模式下写不进去，未读点退化为"本会话内已读" */ }
      return next;
    });
  }, []);
  const isUnread = useCallback((slug: string) => !read.has(slug), [read]);
  return useMemo(() => ({ mark, isUnread }), [mark, isUnread]);
}

/**
 * 带问题进智能问数：与 Chat 共享的 sessionStorage 一次性契约。
 *
 * `home_question` 存问题原文（形状不变，旧调用方与用例照用），
 * `home_question_send` 标记“到问数页就执行”：
 *  - 首页提问框回车/「问数」= 用户已经按下发送，落点必须直接出结果，不能只预填让人再按一次；
 *  - 阅读页「就本主题提问」递的是半句（`关于《…》：`），等着被补全，只预填不发送。
 */
export interface StashedQuestion {
  question: string;
  /** true = 落地即自动发送 */
  send: boolean;
}

const QUESTION_KEY = 'home_question';
const SEND_KEY = 'home_question_send';

export function stashQuestion(q: string, opts: { send?: boolean } = {}) {
  sessionStorage.setItem(QUESTION_KEY, q);
  if (opts.send) sessionStorage.setItem(SEND_KEY, '1');
  else sessionStorage.removeItem(SEND_KEY);
}

/** 消费一次（取完即清两个键）：重复挂载/StrictMode 重跑不会把同一问题发两遍 */
export function consumeStashedQuestion(): StashedQuestion | null {
  const question = sessionStorage.getItem(QUESTION_KEY);
  const send = sessionStorage.getItem(SEND_KEY) === '1';
  if (question === null) return null;
  sessionStorage.removeItem(QUESTION_KEY);
  sessionStorage.removeItem(SEND_KEY);
  return { question, send };
}
