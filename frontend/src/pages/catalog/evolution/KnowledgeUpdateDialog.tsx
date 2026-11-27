/**
 * KnowledgeUpdateDialog — 一条「知识更新提议」的处置入口（P1-1 收敛后）。
 *
 * 提议本身只是备忘录：它记录"应该改什么"，永远不直接改资产。真正的写入在**目标域**
 * 既有页面执行（P1-1：进化中心不承载本体/知识库/场景的第二套维护界面）：
 *  - `kb_doc`          → 跳「知识库」页（kbdoc:write，带 leak 扫描与乐观锁）；
 *  - `ontology_object` → 跳「本体建模」页（ontology:save 落草案，发布另需 ontology:publish）；
 *  - `scenario`        → 复用共享的 `ScenarioEditorDialog`（目标域同一编辑器，非另造表单）。
 *
 * 「去处理」携带业务名预填（sessionStorage 一次性携带，与 AS-BOT 面板同惯例）；
 * 保存成功后回到本页填**落点业务名**并「标记已处理」（applied_ref 必填，
 * 空落点假闭环在服务层也会被拒），确认落点即走统一决策链（版本 + 幂等）。
 */
import { useState } from 'react';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ExternalLink } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

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
  /** 决策版本：标记/驳回走统一决策链时的 expected_version（服务端条件更新仲裁）。 */
  expected_version?: number;
  decision_version?: number;
  origin_conversation_id: number;
  origin_message_uuid: string;
  created_at: string;
  updated_at: string;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  proposal: KnowledgeProposal | null;
  /** `knowledge:manage`：「标记已处理」按钮的权限门（写落点属提议处置）。 */
  canMarkApplied: boolean;
  /** 场景分支：交给调用方用共享的场景编辑器打开（新建 / 追加） */
  onScenario: (mode: 'create' | 'append') => void;
  /** kb_doc 分支：跳「知识库」目标域页面（携带业务名预填） */
  onGoKb: (proposal: KnowledgeProposal) => void;
  /** ontology_object 分支：跳「本体建模」目标域页面（携带业务名预填） */
  onGoModel: (proposal: KnowledgeProposal) => void;
  /** 落点已填 → 由调用方走统一决策链标记 applied */
  onApplied: (ref: string) => void;
}

export default function KnowledgeUpdateDialog({
  open, onOpenChange, proposal, canMarkApplied, onScenario, onGoKb, onGoModel, onApplied,
}: Props) {
  const { t } = useTranslation('ui');
  const [ref, setRef] = useState('');
  const type = proposal?.target_type;

  const submitApplied = () => {
    const value = ref.trim();
    if (!value) return; // applied_ref 必填：空落点假闭环在服务层也会被拒（422）
    onApplied(value);
    setRef('');
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={o => { if (!o) setRef(''); onOpenChange(o); }}>
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
        {type === 'scenario' ? (
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
        ) : (
          <Button variant="outline" className="w-full"
            onClick={() => proposal && (type === 'kb_doc' ? onGoKb(proposal) : onGoModel(proposal))}>
            <ExternalLink className="h-3.5 w-3.5 mr-1" />{tr('k_bfdd85e099')}
          </Button>
        )}
        <div className="space-y-2">
          <Label className="text-xs">{tr('k_d169148f67')}</Label>
          <Input value={ref} onChange={e => setRef(e.target.value)}
            placeholder={tr('k_5d1e9a7c43')} />
          <p className="text-[11px] text-muted-foreground">{tr('k_5d1e9a7c42')}</p>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t('k_4d0b4688c7')}</Button>
          <Button onClick={submitApplied} disabled={!canMarkApplied || !ref.trim()}>
            {tr('k_8412b77794')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
