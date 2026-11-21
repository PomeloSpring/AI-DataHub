/**
 * KnowledgeUpdateDialog — 一条「知识更新提议」的处置入口。
 *
 * 提议本身只是备忘录：它记录"应该改什么"，永远不直接改资产。真正的写入由人在这里
 * 用**目标域的权限**执行，三个分支各自复用那条域既有的写通道，不另造：
 *  - `kb_doc`          → `KbDocUpdateDialog`（`kbdoc:write`，带 leak 扫描与 sha256 乐观锁）
 *  - `ontology_object` → `OntologyScalarDialog`（`ontology:save` 落草案，发布另需 `ontology:publish`）
 *  - `scenario`        → 共用 `ScenarioEditorDialog`，由本组件只做「新建 / 追加到已有」的选择
 *
 * 为什么不把三种写入塞进同一个表单：三者的护栏、代价与"生效"的含义完全不同
 * （文档是整篇替换+重解析延迟、本体是草案+独立发布、场景是即时生效），
 * 合成一个"保存"按钮就必须在文案里同时解释三种后果，等于什么都没解释。
 */
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { useTranslation } from 'react-i18next';
import KbDocUpdateDialog from './KbDocUpdateDialog';
import OntologyScalarDialog from './OntologyScalarDialog';

export interface KnowledgeProposal {
  id: number;
  source: string;
  target_type: 'kb_doc' | 'scenario' | 'ontology_object';
  title: string;
  content: string;
  rationale: string;
  target_hint: string;
  datasource_name: string;
  status: string;
  applied_ref: string;
  decided_by: string;
  decided_at: string;
  decided_note: string;
  origin_conversation_id: number;
  origin_message_uuid: string;
  created_at: string;
  updated_at: string;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  proposal: KnowledgeProposal | null;
  /** `kbdoc:write`：知识库文档写入的门 */
  canWriteDoc: boolean;
  /** 场景分支：交给调用方用共享的场景编辑器打开（新建 / 追加） */
  onScenario: (mode: 'create' | 'append') => void;
  /** 目标域写入成功回执（随后由调用方把提议标记 applied） */
  onApplied: (ref: string) => void;
}

export default function KnowledgeUpdateDialog({
  open, onOpenChange, proposal, canWriteDoc, onScenario, onApplied,
}: Props) {
  const { t } = useTranslation('ui');
  const type = proposal?.target_type;

  if (type === 'kb_doc') {
    return (
      <KbDocUpdateDialog
        open={open}
        onOpenChange={onOpenChange}
        canWrite={canWriteDoc}
        prefill={{
          kbName: proposal?.target_hint || '',
          docTitle: proposal?.title || '',
          content: proposal?.content || '',
        }}
        onApplied={onApplied}
      />
    );
  }

  if (type === 'ontology_object') {
    return (
      <OntologyScalarDialog
        open={open}
        onOpenChange={onOpenChange}
        prefill={{
          objectName: proposal?.target_hint || proposal?.title || '',
          description: proposal?.content || '',
        }}
        onApplied={onApplied}
      />
    );
  }

  // scenario：只做模式选择，表单交给共享的场景编辑器（避免两套表单两套校验）
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{proposal?.title || t('k_23b0aa42b9')}</DialogTitle>
          <DialogDescription>{t('k_6d5778907c')}</DialogDescription>
        </DialogHeader>
        {!!proposal?.content && (
          <div className="max-h-[220px] overflow-y-auto rounded border bg-muted/30 p-2 text-xs leading-5 whitespace-pre-wrap">
            {proposal.content}
          </div>
        )}
        <div className="grid gap-2 sm:grid-cols-2">
          {([
            { mode: 'create' as const, title: t('k_8578e003e6'), hint: t('k_57a32d0e6c') },
            { mode: 'append' as const, title: t('k_8236b45e16'), hint: t('k_cec78b8fa9') },
          ]).map(opt => (
            // 用原生 button 而不是 <Button>：后者是 inline-flex + whitespace-nowrap 的
            // 单行控件，两行说明会被挤成一行并溢出弹窗（真机截图里文字直接叠在一起）
            <button
              key={opt.mode}
              type="button"
              onClick={() => onScenario(opt.mode)}
              className="rounded-lg border p-3 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <div className="text-sm font-medium">{opt.title}</div>
              <div className="mt-1 text-[11px] leading-4 text-muted-foreground">{opt.hint}</div>
            </button>
          ))}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t('k_4d0b4688c7')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}