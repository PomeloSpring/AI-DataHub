// 报告导出入口：Markdown / HTML / PDF 三个出口，正文取当前屏幕上这一份。
//
// 为什么不用 Radix DropdownMenu：本仓已有 jsdom 打不开 Radix 下拉的坑（Pointer
// Capture 未实现，见 tests 里的既有绕法），而这里的选项只有三项、无键盘导航需求；
// 同一头栏的「版本管理」也是原生控件。用最小状态自绘可测、可点、不引新依赖。
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Download, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toast } from '@/lib/toast';
import { containerForKind } from '@/lib/pageWidth';
import {
  availableExportFormats, collectAppCss, exportLabel, isStandaloneHtmlDocument, printReport, renderedHtml,
  reportFileName, standaloneHtml, downloadTextFile, type ReportExportFormat,
} from '@/lib/reportExport';

interface Props {
  title: string;
  createdAt: string;
  /** 报告正文格式（markdown|html）：决定可导出的格式集合 */
  bodyFormat?: string;
  /** 原始正文文本；取不到（还没加载）时导出入口禁用并说明原因 */
  body?: string;
  /** 渲染容器：HTML/PDF 走所见即所得，从这里取 DOM */
  getContainer: () => HTMLElement | null;
}

export default function ReportExportMenu({ title, createdAt, bodyFormat, body, getContainer }: Props) {
  const { t } = useTranslation(['report', 'common']);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<ReportExportFormat | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const hasBody = !!body && !!body.trim();
  // 整份 HTML 文档在隔离 iframe 里渲染：DOM 快照取不到内容，导出直接用原始正文
  const standaloneDoc = bodyFormat === 'html' && isStandaloneHtmlDocument(body);
  const formats = availableExportFormats(bodyFormat, standaloneDoc);

  useEffect(() => {
    if (!open) return;
    const onDocDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDocDown);
    return () => document.removeEventListener('mousedown', onDocDown);
  }, [open]);

  const run = async (fmt: ReportExportFormat) => {
    setOpen(false);
    setBusy(fmt);
    try {
      if (fmt === 'md') {
        downloadTextFile(reportFileName(title, createdAt, 'md'), body || '', 'text/markdown');
        toast.success(t('export.exportedMd'));
        return;
      }
      if (fmt === 'html' && standaloneDoc) {
        // 正文本身就是单文件自包含报告（样式/内联图表全在）：原样给出，不取 DOM 快照
        // （隔离 iframe 里根本取不到内容），也不要求渲染面已就绪
        downloadTextFile(reportFileName(title, createdAt, 'html'), body || '', 'text/html');
        toast.success(t('export.exportedHtmlStandalone'));
        return;
      }
      const container = getContainer();
      if (!container) {
        // 拿不到渲染面就不假装导出成功：说清是"正文还没渲染出来"
        toast.error(t('export.notRendered'));
        return;
      }
      if (fmt === 'html') {
        // 所见即所得：内联本站编译 CSS + 镜像根主题类 + 保住正文容器自己的类 + 版心取屏幕上那份口径，
        // 缺一就退回"只有语义结构 / 挤在中间一列"的裸排版
        downloadTextFile(reportFileName(title, createdAt, 'html'),
          standaloneHtml({
            title, innerHtml: await renderedHtml(container),
            css: collectAppCss(),
            rootClass: document.documentElement.className,
            contentClass: container.className,
            // 屏幕上的宽度口径由 pageWidth 一处裁决（html 版式填满、markdown 长文窄栏），
            // 导出另写一份 max-width 就是第二个口径，必然与屏幕不一致
            shellClass: containerForKind(bodyFormat),
          }), 'text/html');
        toast.success(t('export.exportedHtml'));
        return;
      }
      await printReport();
    } catch (e: any) {
      toast.error(e?.message || t('export.exportFailed'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="relative shrink-0" ref={boxRef}>
      <Button variant="outline" size="sm" className="h-8 text-xs" aria-haspopup="menu"
        aria-expanded={open} disabled={!hasBody} data-report-export
        title={hasBody ? t('export.exportTitle') : t('export.exportDisabledTitle')}
        onClick={() => setOpen(v => !v)}>
        {busy ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Download className="h-3 w-3 mr-1" />}
        {t('common:action.export')}
      </Button>
      {open && (
        <div role="menu" className="absolute right-0 z-20 mt-1 w-44 rounded-md border bg-popover p-1 shadow-md">
          {formats.map(f => (
            <button key={f} role="menuitem" type="button"
              className="w-full rounded px-2 py-1.5 text-left text-xs hover:bg-accent hover:text-accent-foreground"
              onClick={() => void run(f)}>
              {exportLabel(f)}
            </button>
          ))}
          {standaloneDoc && (
            // 不给 PDF 不是漏配：隔离预览拿不到全文分页，静默截页比少一个入口更坑
            <p className="px-2 pt-1.5 text-[10px] leading-relaxed text-muted-foreground">
              {t('export.standalonePdfNote')}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
