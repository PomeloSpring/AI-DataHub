import { useEffect, useMemo, useRef } from 'react';
import DOMPurify from 'dompurify';

/**
 * HTML 版式正文的站内渲染口（内容中心 `kind='html'` 的唯一消费者）。
 *
 * 为什么必须过一遍清洗：正文是"库里存的字符串"，任何一次编辑都可能带进 `<script>`、
 * `onclick` 或 `javascript:` 链接 —— 直接 `dangerouslySetInnerHTML` 就是存储型 XSS。
 * 这里只留排版标签与 `style/class/href/title`，可执行的东西一律剥掉。
 *
 * 两道口径要一致（与 `content_service.HTML_UNSAFE_RE` 同源）：服务端门禁在**入库时**就拒
 * 这批标记，前端在**渲染时**再兜一层。这样不会出现"存得进去、渲染出来缺块东西"——
 * 那是把缺陷藏到读者眼里，比报错难查得多。
 *
 * `<style>` 整块不允许：一段没有作用域的选择器能把整个门户版式打乱（比脚本更早出事）。
 * 版式写在元素的 `style` 属性里，作用域天然受限；颜色请用主题变量
 * （`hsl(var(--primary))` 这类），暗/亮两套主题才不用各写一份。
 * `class` 允许保留但不保证有样式：Tailwind 只编译源码里出现过的类名，库内容扫不到。
 */
const ALLOWED_TAGS = [
  'div', 'section', 'header', 'footer', 'article', 'aside', 'span', 'p', 'br', 'hr',
  'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'dl', 'dt', 'dd',
  'strong', 'em', 'b', 'i', 'small', 'sub', 'sup', 'blockquote', 'code', 'pre',
  'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
  'a', 'figure', 'figcaption',
];
const ALLOWED_ATTR = ['style', 'class', 'href', 'title', 'colspan', 'rowspan', 'lang', 'dir'];
// 双保险：白名单本来就不含这些，再显式 forbid，避免日后加白名单时手滑放行
const FORBID_TAGS = ['script', 'style', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'link', 'meta', 'svg', 'math'];

export function sanitizeArticleHtml(html: string): string {
  return DOMPurify.sanitize(html || '', {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
    FORBID_TAGS,
    FORBID_ATTR: ['src', 'srcset', 'onerror', 'onload', 'formaction'],
  });
}

export default function HtmlArticle({ html, className = '' }: { html: string; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const clean = useMemo(() => sanitizeArticleHtml(html), [html]);

  // 站外链接统一新窗口 + rel=noopener（作者手写时漏了也补上）；站内相对路径保持同页导航
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    root.querySelectorAll<HTMLAnchorElement>('a[href]').forEach(a => {
      const href = a.getAttribute('href') || '';
      if (/^https?:\/\//i.test(href)) {
        a.setAttribute('target', '_blank');
        a.setAttribute('rel', 'noopener noreferrer');
      }
    });
  }, [clean]);

  return <div ref={ref} className={className} dangerouslySetInnerHTML={{ __html: clean }} />;
}
