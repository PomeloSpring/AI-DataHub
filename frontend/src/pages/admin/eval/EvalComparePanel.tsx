/**
 * 版本对比面板：把一次运行与指定/自动基线按 case_key 对齐。
 *
 * 关键点是**覆盖度流失要显式呈现**：基线里通过、现在用例被删/退役的条目会掉出
 * 分母，如果不列出来，"删难用例"就能把通过率刷上去。门禁里另有 min_cases 下界
 * 拦这个，这里负责让人看见具体是哪几条。
 */
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from '@/lib/toast';
import { GitCompare } from 'lucide-react';
import client from '@/api/client';
import { SUITE_LABEL, pct, type CompareResult, type EvalRun } from './shared';

interface Props {
  runs: EvalRun[];
}

export default function EvalComparePanel({ runs }: Props) {
  const [base, setBase] = useState('');
  const [cmp, setCmp] = useState<CompareResult | null>(null);
  const [target, setTarget] = useState<EvalRun | null>(null);
  const [busy, setBusy] = useState(false);

  const done = runs.filter(r => r.status === 'done');

  const compare = async (run: EvalRun) => {
    setBusy(true);
    try {
      const { data } = await client.get(`/eval/runs/${run.id}/compare`, {
        params: base ? { baseline_id: base } : {},
      });
      setCmp(data);
      setTarget(run);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '版本对比失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border rounded-md p-3 space-y-3">
      <div className="flex items-center gap-2">
        <GitCompare className="h-4 w-4" />
        <b className="text-sm">版本对比（按 case_key 对齐）</b>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <Label className="text-xs">基线运行</Label>
        <Select value={base || 'auto'} onValueChange={v => setBase(v === 'auto' ? '' : v)}>
          <SelectTrigger className="w-[280px] h-8 text-xs">
            <SelectValue placeholder="自动（该层最近一次完成运行）" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="auto">自动（该层最近一次完成运行）</SelectItem>
            {done.map(r => (
              <SelectItem key={r.id} value={String(r.id)}>
                {SUITE_LABEL[r.suite] || r.suite} #{r.id} · {pct(r.accuracy)} · {r.finished_at || r.created_at}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-[11px] text-muted-foreground">
          对比对象从下方「运行」按钮选；只比确定性 passed，不看 LLM 评分
        </span>
      </div>

      <div className="flex flex-wrap gap-1">
        {done.slice(0, 15).map(r => (
          <Button key={r.id} size="sm" variant="outline" className="h-6 text-[10px]"
            disabled={busy} onClick={() => compare(r)}>
            {SUITE_LABEL[r.suite] || r.suite} #{r.id}（{pct(r.accuracy)}）
          </Button>
        ))}
      </div>

      {cmp && (
        <div className="border rounded-md p-2.5 space-y-1.5">
          <div className={`text-sm font-medium ${cmp.baseline_ok ? 'text-green-600' : 'text-destructive'}`}>
            {cmp.verdict}
          </div>
          <div className="text-xs text-muted-foreground">
            {target ? `${SUITE_LABEL[target.suite] || target.suite} #${target.id}` : `运行 #${cmp.run_id}`}
            {' ← '}基线 #{cmp.baseline_run_id ?? '无'}
            {'　'}正确率 {pct(cmp.baseline_accuracy)} → {pct(cmp.current_accuracy)}
          </div>
          {Object.keys(cmp.regression || {}).length > 0 && (
            <div className="text-xs">
              <b className="text-destructive">回退：</b>
              {Object.entries(cmp.regression).map(([k, v]) => (
                <div key={k} className="pl-2 text-destructive">
                  {k}：{v.from} → {v.to}（{v.reason}）
                </div>
              ))}
            </div>
          )}
          {cmp.coverage_loss?.length > 0 && (
            <div className="text-xs text-amber-600">
              覆盖度流失（基线通过、现在用例已删）：{cmp.coverage_loss.join('、')}
              <div className="text-[10px] text-muted-foreground">
                这类条目会掉出门禁分母。若确实该退役，请到用例里标 retired 并写明原因，
                而不是删除 —— 删除会让"少了哪些保护"变得无法追溯。
              </div>
            </div>
          )}
          {cmp.fixed?.length > 0 && (
            <div className="text-xs text-green-600">修好：{cmp.fixed.join('、')}</div>
          )}
          {cmp.new?.length > 0 && (
            <div className="text-xs text-muted-foreground">新增：{cmp.new.join('、')}</div>
          )}
          {cmp.removed?.length ? (
            <div className="text-xs text-muted-foreground">
              基线有、本次无：{cmp.removed.join('、')}
              <Badge variant="outline" className="ml-1 text-[9px]">含未通过项</Badge>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
