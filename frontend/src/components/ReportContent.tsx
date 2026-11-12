import DOMPurify from 'dompurify';
import MarkdownWithCharts from './MarkdownWithCharts';
import { isStandaloneHtmlDocument } from '@/lib/reportExport';

export function sanitizeReportHtml(content: string): string {
  return DOMPurify.sanitize(content, {
    ALLOWED_TAGS: ['p', 'div', 'span', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'ul', 'ol', 'li', 'strong', 'em', 'b', 'i', 'br', 'hr', 'blockquote', 'pre', 'code', 'a'],
    ALLOWED_ATTR: ['href', 'title', 'colspan', 'rowspan'],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
  });
}

export default function ReportContent({ content, format }: { content: string; format: string }) {
  if (format !== 'html') {
    return <MarkdownWithCharts text={content} className="prose prose-sm max-w-none" />;
  }
  // 整份自包含 HTML 文档（AI 生成的报告：带 <style> 与内联 SVG）必须隔离渲染：
  // 白名单净化会把样式与图表剥光（存了却不长那样），而把 <style> 注进主文档会
  // 污染全局（CSS 不按容器隔离）。与产物下载卡片同一口径（sandbox="allow-scripts"）。
  if (isStandaloneHtmlDocument(content)) {
    return (
      <iframe
        title="报告正文"
        srcDoc={content}
        sandbox="allow-scripts"
        data-report-standalone-html
        className="w-full h-[78vh] rounded-md border bg-white"
      />
    );
  }
  return <article className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: sanitizeReportHtml(content) }} />;
}
