import DOMPurify from 'dompurify';
import MarkdownWithCharts from './MarkdownWithCharts';

export function sanitizeReportHtml(content: string): string {
  return DOMPurify.sanitize(content, {
    ALLOWED_TAGS: ['p', 'div', 'span', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'ul', 'ol', 'li', 'strong', 'em', 'b', 'i', 'br', 'hr', 'blockquote', 'pre', 'code', 'a'],
    ALLOWED_ATTR: ['href', 'title', 'colspan', 'rowspan'],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
  });
}

export default function ReportContent({ content, format }: { content: string; format: string }) {
  return format === 'html'
    ? <article className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: sanitizeReportHtml(content) }} />
    : <MarkdownWithCharts text={content} className="prose prose-sm max-w-none" />;
}
