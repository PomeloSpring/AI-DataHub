/**
 * 评测概览：capability × layer 覆盖矩阵 + 双口径门禁结论。
 *
 * 这块是"测试集内容是否符合要求"的判定视图 —— 不数用例条数，而是看每个能力契约
 * 在哪一层有没有被守住。空格就是缺口，点一下直达"在该层该契约下新增用例"。
 *
 * 三条不能含糊的呈现纪律：
 *  1. 发布结论只看 release_ok（相对未回退 AND 达绝对下限）。历史上这里只看
 *     "未回退"，导致 35.7% 的层显示"可以发布"。
 *  2. L1（mock 目录）与 L2（真实本体）的结论**分栏并列，禁止合并成一个总分**，
 *     否则规则回归的全绿会被当成平台能力的全绿。
 *  3. 「无运行」「未知」不是通过，必须显式呈现。
 */
import { useMemo } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { AlertTriangle, Plus, ShieldCheck } from 'lucide-react';
import {
  GateBadge, SUITE_LABEL, pct,
  type CapabilityMeta, type CoverageCell, type PrecheckPayload, type SuiteMeta,
} from './shared';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

interface Props {
  suites: SuiteMeta[];
  capabilities: CapabilityMeta[];
  cells: CoverageCell[];
  precheck: PrecheckPayload | null;
  onCreateAt: (suite: string, capability: string) => void;
  onRefresh: () => void;
}

/** 一格的颜色语义：缺口(虚线) / 未跑(灰) / 通过(绿) / 有失败(红)。 */
function cellStyle(c: CoverageCell | undefined): string {
  if (!c || c.scored === 0) return 'border-dashed border-border text-muted-foreground';
  if (c.accuracy == null) return 'bg-muted/40 text-muted-foreground';
  if (c.accuracy >= 1) return 'bg-green-50 dark:bg-green-950/40 text-green-700 dark:text-green-400';
  return 'bg-red-50 dark:bg-red-950/40 text-destructive';
}

export default function EvalOverview({
  suites, capabilities, cells, precheck, onCreateAt, onRefresh,
}: Props) {
  const { t } = useTranslation('ui');
  const byKey = useMemo(() => {
    const m = new Map<string, CoverageCell>();
    for (const c of cells) m.set(`${c.suite}|${c.capability}`, c);
    return m;
  }, [cells]);

  const gaps = cells.filter(c => c.scored === 0);
  const defects = cells.reduce((n, c) => n + (c.known_defect || 0), 0);
  const retired = cells.reduce((n, c) => n + (c.retired || 0), 0);

  return (
    <div className="space-y-3">
      {/* 门禁结论：红字必须是红字，不做"看起来能用"的绿 */}
      <div className="border rounded-md p-3 space-y-2">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4" />
          <b className="text-sm">{t("k_b78c41ddd9")}</b>
          <Badge variant="outline" className="text-[10px]">
            {t('k_92fb906896')}
          </Badge>
          <div className="flex-1" />
          <Button variant="outline" size="sm" className="h-7 text-xs" onClick={onRefresh}>
            {t('k_38108eaa1d')}
          </Button>
        </div>
        {!precheck && <p className="text-xs text-muted-foreground">{t("k_300ee3dee4")}</p>}
        {precheck && (
          <>
            <div className={`text-sm font-medium ${precheck.release_ok ? 'text-green-600' : 'text-destructive'}`}>
              {precheck.verdict}
            </div>
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/40">
                  <TableHead className="text-xs">{t("k_0e8e7b44a1")}</TableHead>
                  <TableHead className="text-xs">{t("k_648c5a6b11")}</TableHead>
                  <TableHead className="text-xs">{t("k_1c368ce472")}</TableHead>
                  <TableHead className="text-xs">{t("k_8dc159502e")}</TableHead>
                  <TableHead className="text-xs">{t("k_6ab9c882d2")}</TableHead>
                  <TableHead className="text-xs">{t("k_a0d14adcf1")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {suites.map(s => {
                  const v = precheck.suites?.[s.suite];
                  return (
                    <TableRow key={s.suite} className="align-top">
                      <TableHead className="text-xs p-2 font-normal">
                        <div>{s.label}</div>
                        {s.scope_note && (
                          <div className="text-[10px] text-muted-foreground font-normal max-w-[280px]">
                            {s.scope_note}
                          </div>
                        )}
                      </TableHead>
                      <TableCell className="text-xs p-2">
                        {v?.has_run ? `#${v.run_id}` : '—'}
                      </TableCell>
                      <TableCell className="text-xs p-2">
                        {v?.has_run ? `${v.passed}/${v.total}` : '—'}
                      </TableCell>
                      <TableCell className="text-xs p-2">
                        {v?.has_run && (
                          <>
                            {pct(v.accuracy)}
                            {/* 旧分母快照与判定分母不同就并列显示：两个数字必须互相解释得了 */}
                            {v.run_total_snapshot !== v.total && (
                              <span
                                className="ml-1 text-[10px] text-muted-foreground"
                                title={tr('k_c93c98170c', { p0: v.run_total_snapshot, p1: pct(v.run_accuracy_snapshot) })}
                              >
                                {t('k_162701ea30', { p0: v.run_total_snapshot, p1: pct(v.run_accuracy_snapshot) })}</span>
                            )}
                          </>
                        )}
                        {!v?.has_run && <span className="text-muted-foreground">{t('k_aa2d42c580')}</span>}
                      </TableCell>
                      <TableCell className="text-xs p-2 text-muted-foreground">
                        {v ? `${v.retired_count ?? 0} / ${v.known_defect_count ?? 0}` : '—'}
                      </TableCell>
                      <TableCell className="text-xs p-2">
                        {v && <GateBadge v={v} />}
                        {v?.breaches?.length ? (
                          <ul className="mt-1 space-y-0.5">
                            {v.breaches.map((b, i) => (
                              <li key={i} className="text-[10px] text-destructive">
                                [{b.capability_label || t('k_16a20243f9')}] {b.reason}
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </>
        )}
      </div>

      {/* 覆盖矩阵 */}
      <div className="border rounded-md p-3 space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <b className="text-sm">{t('k_e751840f4c')}</b>
          <Badge variant="outline" className="text-[10px]">{t('k_9120757c5d', { p0: cells.length })}</Badge>
          {gaps.length > 0 && (
            <Badge variant="destructive" className="text-[10px]">
              <AlertTriangle className="h-3 w-3 mr-0.5" />{gaps.length} {tr('k_2894c3ec67')}{' '}</Badge>
          )}
          {defects > 0 && (
            <Badge variant="outline" className="text-[10px] text-amber-600 border-amber-500/40">
              {t('k_e8c9f3a96f', { p0: defects })}</Badge>
          )}
          {retired > 0 && (
            <Badge variant="outline" className="text-[10px]">
              {t('k_0d1831d4f7', { p0: retired })}</Badge>
          )}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="bg-muted/40 text-left">
                <th className="p-2 sticky left-0 bg-muted/40">{t("k_b13ab1ec69")}</th>
                {suites.map(s => (
                  <th key={s.suite} className="p-2 min-w-[120px]" title={s.scope_note}>
                    {SUITE_LABEL[s.suite] || s.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {capabilities.map(cap => (
                <tr key={cap.key} className="border-t">
                  <td className="p-2 font-medium sticky left-0 bg-background">
                    {cap.label}
                    <span className="ml-1 font-mono text-[10px] text-muted-foreground">{cap.key}</span>
                  </td>
                  {suites.map(s => {
                    const c = byKey.get(`${s.suite}|${cap.key}`);
                    return (
                      <td key={s.suite} className="p-1">
                        <button
                          type="button"
                          onClick={() => onCreateAt(s.suite, cap.key)}
                          title={c
                            ? t('k_a39d8cacef', { p0: c.scored, p1: c.known_defect, p2: c.retired })
                            : t('k_428dae9069')}
                          className={`w-full h-full min-h-[38px] rounded border px-1.5 py-1 text-left transition-colors hover:border-primary ${cellStyle(c)}`}
                        >
                          {!c || c.scored === 0 ? (
                            <span className="flex items-center gap-1 text-[11px]">
                              <Plus className="h-3 w-3" />{t('k_48432c1d5c')}
                            </span>
                          ) : (
                            <>
                              <div className="font-medium">{pct(c.accuracy)}</div>
                              <div className="text-[10px] opacity-80">
                                {c.scored} {tr('k_cc1bacb511')}{' '}{c.known_defect > 0 && t('k_58bd85630b', { p0: c.known_defect })}
                                {c.accuracy == null && t('k_9c83f02252')}
                              </div>
                            </>
                          )}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-[11px] text-muted-foreground">
          {tr('k_7c024aa162')}<b>{t("k_8a4fe63710")}</b>{tr('k_ad55b26b7a')}{' '}</p>
      </div>
    </div>
  );
}
