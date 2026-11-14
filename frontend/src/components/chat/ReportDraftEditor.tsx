/**
 * 报告草稿编辑面板。
 *
 * 两段式的第一段：草稿只属于本会话（随消息 JSON 落库），改到满意再点「归档」进报告中心。
 * 归档前必须先落库 —— 服务端归档读的是会话里那份草稿（正文不信请求体，护栏 §2），
 * 所以「归档」按钮做的是「存 → 成功后归档」，而不是把编辑器里的内容直接递过去。
 */
import { useEffect, useState } from 'react';
import { Archive, Check, Loader2, Save, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { toast } from '@/lib/toast';
import type { ChatReportDraft } from '@/stores/chatStore';

interface Props {
  draft: ChatReportDraft;
  onClose: () => void;
  /** 落库草稿；返回 false = 没存成（调用方不得继续归档） */
  onSave: (patch: ChatReportDraft) => Promise<boolean>;
  /** 归档进报告中心（只在保存成功后被调用） */
  onArchive: () => void;
  busy?: boolean;
}

export default function ReportDraftEditor({ draft, onClose, onSave, onArchive, busy = false }: Props) {
  const { t } = useTranslation(['chat', 'common']);
  const [title, setTitle] = useState(draft.title);
  const [markdown, setMarkdown] = useState(draft.markdown);
  const [saving, setSaving] = useState(false);
  // 编辑器内容与会话里的草稿不一致时禁止直接关闭（改了就走，下次归档读到的还是旧稿）
  const dirty = title !== draft.title || markdown !== draft.markdown;
  const archived = !!draft.archived;

  useEffect(() => {
    setTitle(draft.title);
    setMarkdown(draft.markdown);
  }, [draft.title, draft.markdown]);

  const persist = async (): Promise<boolean> => {
    if (!title.trim()) {
      toast.error(t('reportDraft.titleRequired'));
      return false;
    }
    if (!markdown.trim()) {
      toast.error(t('reportDraft.bodyRequired'));
      return false;
    }
    setSaving(true);
    try {
      return await onSave({
        ...draft,
        title: title.trim(),
        markdown,
        updated_at: new Date().toISOString(),
        revisions: Number(draft.revisions || 0) + 1,
      });
    } finally {
      setSaving(false);
    }
  };

  const handleSave = async () => {
    const ok = await persist();
    if (ok) toast.success(t('reportDraft.saved'));
    else toast.error(t('reportDraft.saveFailed'));
  };

  const handleArchive = async () => {
    if (dirty) {
      const ok = await persist();
      if (!ok) {
        toast.error(t('reportDraft.archiveBlocked'));
        return;
      }
    }
    onArchive();
  };

  const handleClose = () => {
    if (dirty) {
      if (!window.confirm(t('reportDraft.unsavedConfirm'))) return;
    }
    onClose();
  };

  const working = saving || busy;
  // HTML 报告产物的草稿：正文就是那份自包含 HTML（样式 + 内联图表），
  // 当纯文本给入 markdown 编辑器改一处就能把整份报告改坏 → 只让改标题 + 看预览
  const isHtmlDoc = draft.format === 'html';

  return (
    <Dialog open onOpenChange={open => { if (!open) handleClose(); }}>
      <DialogContent className="flex h-[88vh] max-h-[88vh] w-[min(1000px,94vw)] max-w-none flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="flex-shrink-0 space-y-1 border-b px-4 py-3">
          <DialogTitle className="flex items-center gap-2 text-sm">
            <Archive className="h-4 w-4 shrink-0 text-primary" />
            {t('reportDraft.title')}
            {archived && (
              <span className="shrink-0 rounded border border-emerald-600/40 bg-emerald-500/10 px-1.5 py-0.5 text-[11px] font-normal text-emerald-700">
                {t('sidePanel.archivedToCenter')}
              </span>
            )}
          </DialogTitle>
          <DialogDescription className="text-xs">
            {archived
              ? t('reportDraft.archivedDesc')
              : t('reportDraft.draftDesc')}
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-3">
          <label className="block space-y-1">
            <span className="text-xs text-muted-foreground">{t('reportDraft.titleLabel')}</span>
            <Input
              value={title}
              onChange={e => setTitle(e.target.value)}
              disabled={archived}
              placeholder={t('reportDraft.titlePlaceholder')}
            />
          </label>
          {isHtmlDoc ? (
            <div className="space-y-1">
              <span className="flex items-center gap-2 text-xs text-muted-foreground">
                {t('reportDraft.bodyHtml')}
                <span className="ml-auto text-[10px]">{t('reportDraft.charCount', { count: markdown.length })}</span>
              </span>
              <p className="text-xs text-muted-foreground">
                {draft.document
                  ? t('reportDraft.htmlSourceDoc')
                  : t('reportDraft.htmlSourceArtifact', { filename: draft.artifact?.filename || t('reportDraft.defaultArtifactName') })}
                {t('reportDraft.htmlEditHint')}
              </p>
              <iframe
                title={t('reportDraft.previewAria')}
                srcDoc={markdown}
                sandbox="allow-scripts"
                className="w-full h-[46vh] rounded-md border bg-white"
              />
            </div>
          ) : (
            <label className="block space-y-1">
              <span className="flex items-center gap-2 text-xs text-muted-foreground">
                {t('reportDraft.bodyMarkdown')}
                <span className="ml-auto text-[10px]">{t('reportDraft.charCount', { count: markdown.length })}</span>
              </span>
              <Textarea
                value={markdown}
                onChange={e => setMarkdown(e.target.value)}
                disabled={archived}
                className="min-h-[46vh] font-mono text-xs leading-relaxed"
              />
            </label>
          )}
        </div>

        <div className="flex flex-shrink-0 items-center gap-2 border-t px-4 py-3">
          <Button variant="ghost" size="sm" onClick={handleClose} disabled={working}>
            <X className="mr-1 h-4 w-4" />{t('common:action.close')}
          </Button>
          <span className="flex-1" />
          {!archived && (
            <>
              <Button variant="outline" size="sm" onClick={() => void handleSave()} disabled={working}>
                {saving
                  ? <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  : <Save className="mr-1 h-4 w-4" />}
                {t('reportDraft.saveDraft')}
              </Button>
              <Button size="sm" onClick={() => void handleArchive()} disabled={working}>
                {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Check className="mr-1 h-4 w-4" />}
                {t('reportDraft.archive')}
              </Button>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
