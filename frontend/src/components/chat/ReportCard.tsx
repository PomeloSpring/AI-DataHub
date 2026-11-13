/**
 * 交付形态渲染卡 — ```report 块在该位置渲染整份交付文档（随消息持久化与回放）。
 *
 * 为什么不是"下载卡片"：整份 HTML 报告原先只能作为会话工作区文件交付，用户要在
 * 文件清单里找入口、存进报告中心还拿不到正文。现在它是**运行时一等交付形态**：
 * 正文在受管文档对象里，卡片按 doc_id 取回并就地渲染，用户看到的就是报告本身。
 *
 * 三条不可妥协的口径：
 * 1. 整份 HTML 只能进隔离 iframe（`sandbox="allow-scripts"`）：主文档 DOM 里内联
 *    模型产出的 HTML 就是 XSS 面；而 DOMPurify 白名单会剥掉 style/svg/class（历史
 *    缺陷：报告样式被剥光 → 一片黑白），所以"清洗后内联"不是可选项。
 *    iframe 不吃主文档主题变量 ⇒ 令牌必须随正文走，这里不注入任何主文档样式。
 * 2. 引用取不到必须显式报错并区分成因（已清理 / 无权访问 / 暂时失败 + 重试），
 *    **不得**回退渲染源码或显示空白（no-silent-degradation）。
 * 3. 体检的非阻断提示（warnings）要看得见：用户有权知道这份文档处于什么状态。
 */
import { useCallback, useEffect, useState } from 'react';
import { Download, FileText, FolderPlus, Maximize2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import ReportDoc from './ReportDoc';
import {
  getRenderDocument, renderDocumentErrorText, type RenderDocument,
} from '@/api/renderDocument';

interface Props {
  /** ```report 块携带的文档对象引用 */
  docId: string;
  /** 正文形态：html=自包含文档，spec=结构化章节（平台组件逐章渲染） */
  bodyKind: string;
  /** 块内声明的标题；以后端文档标题为准，声明值只作首屏占位 */
  title?: string;
  /** 图上下钻（spec 章节里的图表与对话内图表卡同一个入口） */
  onDrill?: (question: string) => void;
  /**
   * 存为报告草稿入口（由外层把消息操作行那一套传进来：同一函数 + 同一 reportable 判定）。
   * 卡片里给这个入口是因为“刚看完这份报告就想存下来”是真实动作；不传就一个也不显示。
   */
  saveReport?: { label?: string; busy?: boolean; onSave: () => void };
}

/** 自包含 HTML 文档的隔离渲染面：iframe srcDoc，不进主文档 DOM。 */
function HtmlFrame({ doc, tall }: { doc: RenderDocument; tall?: boolean }) {
  return (
    <iframe
      title={doc.title || '交付文档'}
      srcDoc={doc.body}
      sandbox="allow-scripts"
      className={`w-full border-0 bg-white rounded-md ${tall ? 'h-[80vh]' : 'h-[520px]'}`}
    />
  );
}

export default function ReportCard({ docId, bodyKind, title, onDrill, saveReport }: Props) {
  const [doc, setDoc] = useState<RenderDocument | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);

  const reload = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      setDoc(await getRenderDocument(docId));
    } catch (e: any) {
      // 取不到就显式说清楚，不回退成"把 JSON 源码给用户看"，也不空白
      setDoc(null);
      setError(renderDocumentErrorText(e?.response?.status));
    } finally {
      setBusy(false);
    }
  }, [docId]);

  useEffect(() => { void reload(); }, [reload]);

  const onDownload = () => {
    if (!doc) return;
    const blob = new Blob([doc.body], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${(doc.title || '交付文档').slice(0, 60)}.html`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // 形态分派：html 只能进隔离 iframe，spec 走平台组件逐章渲染（富图型与下钻不缩水）
  const kind = doc ? doc.body_kind : bodyKind;
  const unsupported = !!doc && kind !== 'html' && kind !== 'spec';
  // 首屏占位用块内声明的形态（文档取回后以后端为准）：两者不一致时后端赢，不得拿声明值当事实
  const formLabel = (form: string) => (form === 'html' ? '整页文档' : form === 'spec' ? '结构化报告' : form);

  return (
    <div className="my-3 rounded-lg border bg-card overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b bg-muted/30">
        <FileText className="h-4 w-4 text-muted-foreground shrink-0" aria-hidden />
        <span className="text-sm font-medium truncate">{doc?.title || title || '交付文档'}</span>
        {doc && (
          <span className="text-[11px] text-muted-foreground shrink-0" title={`正文摘要 ${doc.digest}`}>
            {formLabel(doc.body_kind)}
            {doc.theme ? ` · 主题 ${doc.theme}` : ''}
            {doc.size_chars ? ` · ${Math.round(doc.size_chars / 1024)} KB` : ''}
          </span>
        )}
        {!doc && !error && (
          <span className="text-[11px] text-muted-foreground shrink-0" title={docId}>{formLabel(bodyKind)}</span>
        )}
        <span className="flex-1" />
        {doc && (
          <>
            <Button size="sm" variant="ghost" className="h-7 px-2" onClick={() => setFullscreen(true)}>
              <Maximize2 className="h-3.5 w-3.5 mr-1" aria-hidden />全屏阅读
            </Button>
            {/* 存草稿：与会话侧同一份两段式口径（这里只生成会话内草稿，归档在侧栏） */}
            {saveReport && (
              <Button size="sm" variant="ghost" className="h-7 px-2"
                disabled={saveReport.busy} onClick={saveReport.onSave}>
                <FolderPlus className="h-3.5 w-3.5 mr-1" aria-hidden />{saveReport.label || '存为报告草稿'}
              </Button>
            )}
            {/* 下载只对整页文档：spec 的正文是章节 JSON，当 .html 下下来只会得到一个坏文件 */}
            {kind === 'html' && (
              <Button size="sm" variant="ghost" className="h-7 px-2" onClick={onDownload}>
                <Download className="h-3.5 w-3.5 mr-1" aria-hidden />下载
              </Button>
            )}
          </>
        )}
      </div>

      {error ? (
        <div className="px-3 py-3 text-xs text-muted-foreground space-y-2" title={docId}>
          <p>{error}</p>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => { void reload(); }}>
            {busy ? '正在重试…' : '重试'}
          </Button>
        </div>
      ) : !doc ? (
        <div role="status" className="px-3 py-6 text-xs text-muted-foreground">正在加载交付内容…</div>
      ) : unsupported ? (
        // 未接线的形态同样要显式暴露：静默空白会让用户以为"报告坏了一次"，而不是"这种交付还没上"
        <div className="px-3 py-3 text-xs text-muted-foreground">
          该交付内容以 {doc.body_kind} 形态产出，当前版本尚未支持就地渲染。
          请让助手改用整页文档（HTML）或结构化报告交付，或把内容存进报告中心后查看。
        </div>
      ) : kind === 'spec' ? (
        <ReportDoc doc={doc} onDrill={onDrill} />
      ) : (
        <div className="px-2 pb-2">
          {doc.warnings && doc.warnings.length > 0 && (
            <div className="flex flex-wrap gap-1 px-1 py-2">
              {doc.warnings.map((w, i) => (
                <Badge key={i} variant="secondary" className="text-[11px] font-normal" title={w}>
                  {w.length > 46 ? `${w.slice(0, 46)}…` : w}
                </Badge>
              ))}
            </div>
          )}
          <HtmlFrame doc={doc} />
        </div>
      )}

      {fullscreen && doc && (
        <Dialog open onOpenChange={open => { if (!open) setFullscreen(false); }}>
          <DialogContent className="max-w-[min(1400px,96vw)] w-[96vw] h-[92vh] flex flex-col gap-2">
            <DialogHeader>
              <DialogTitle className="text-sm">{doc.title || '交付文档'}</DialogTitle>
            </DialogHeader>
            {kind === 'spec'
              ? <div className="flex-1 min-h-0 overflow-auto"><ReportDoc doc={doc} onDrill={onDrill} /></div>
              : <HtmlFrame doc={doc} tall />}
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}
