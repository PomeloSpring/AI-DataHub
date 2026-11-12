import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CheckCircle2, Loader2, Send, TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  contentApi, contentError, VERSIONING_HINTS,
  type ArticleVersioning, type PromotePreview, type PromoteResult,
} from '@/api/content';

const NONE = '__none__';

/**
 * 「提交到内容管理审核」弹框（报告中心 → 内容中心的提升通道）。
 *
 * 四条不能省的口径，都是"做错就会让人以为成功了"的那类：
 *
 * 1. **归集预判先演一遍**。打开弹框就调 `GET /content/promotions/match`，把服务端算出来的
 *    结论原样显示：「将追加到《X》第 N 期之后」还是「将新建一篇」。归集键（报表侧
 *    `series_key`）只有服务端一份，前端不猜标题、不拼系列 —— 实拍过"提交后才发现同主题
 *    被拆成两条内容"。
 * 2. **权限不另造一套**。入口是否可点由 `article:manage` 决定（与端点同一个码），这里不再
 *    判"是不是本人报告"：那是服务端的事，前端判了就会有两份答案。
 * 3. **半成功要说清**。门禁没过时草稿确实已存进内容中心：回执分两段显示
 *    （已存草稿 / 未进审核 + 逐条原因），不报成"提交失败"，也不报成"已提交"。
 * 4. **失败原样端出来**。报表正文读不到、快照失效、不属于本人、条目正在审核中…… 服务端
 *    已经把原因写成人话，这里不再转述成一句"操作失败"。
 *
 * 只有业务标识进响应体：回执里没有报表 id / 条目 id / revision id，跳转用 slug。
 */
export default function ReportPromoteDialog({ open, reportId, reportTitle, onClose }: {
  open: boolean;
  reportId: number;
  reportTitle: string;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [preview, setPreview] = useState<PromotePreview | null>(null);
  const [category, setCategory] = useState(NONE);
  const [title, setTitle] = useState('');
  const [summary, setSummary] = useState('');
  const [versioning, setVersioning] = useState<ArticleVersioning>('keep_history');
  const [submitReview, setSubmitReview] = useState(true);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<PromoteResult | null>(null);

  const loadMatch = useCallback(() => {
    if (!open || !reportId) return;
    setLoading(true);
    setError('');
    contentApi.promoteMatch(reportId)
      .then(res => {
        const p = res.data;
        setPreview(p);
        // 建议值可改：标题/摘要预填，栏目留空逼人确认（缺栏目是提交门禁的硬阻断项）
        setTitle(t => t || p.title || reportTitle);
        setSummary(s => s || p.summary);
        // 策略默认留历史期次：选哪个都写在下面的人话提示里，不预选 latest_only
        // （那等于替读者做了“看不见历史”的决定，而且与归集预判无关）
      })
      .catch(err => setError(contentError(err)))
      .finally(() => setLoading(false));
  }, [open, reportId, reportTitle]);

  useEffect(loadMatch, [loadMatch]);

  useEffect(() => {
    if (!open) {
      setPreview(null); setCategory(NONE); setTitle(''); setSummary('');
      setVersioning('keep_history'); setSubmitReview(true); setError(''); setResult(null);
    }
  }, [open]);

  const submit = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await contentApi.promoteFromReport({
        report_id: reportId,
        category: category === NONE ? '' : category,
        title: title.trim(),
        summary: summary.trim(),
        versioning,
        submit: submitReview,
      });
      setResult(res.data);
    } catch (err) {
      // 服务端原因原样显示（正文取不到/快照失效/条目正在审核中/正文含内部标识）
      setError(contentError(err));
    } finally {
      setBusy(false);
    }
  };

  /** 提升结果分三种说法，都不许含糊：进了审核 / 只存了草稿 / 存了草稿但门禁没过 */
  const outcomeText = (r: PromoteResult) => {
    if (r.gate_blocked?.length) return '草稿已保存到内容中心，但未提交审核';
    if (!r.submitted && r.status === 'draft') return '已存为草稿，未提交审核';
    return r.created ? '已新建内容草稿并提交审核' : '已作为新一期追加到原内容并提交审核';
  };

  return (
    <Dialog open={open} onOpenChange={v => { if (!v) onClose(); }}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>提交到内容管理审核</DialogTitle>
          <DialogDescription>
            把这份报告的正文复制成站内内容的一个新版本，交给内容团队走「审核 → 发布」。
            不搬报表快照：数据权限以后收紧也不会让已发布的内容读不到。
          </DialogDescription>
        </DialogHeader>

        {error && (
          <div role="alert" className="flex items-start gap-2 rounded-xl border border-destructive/40 bg-destructive/5 p-3 text-sm">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <span className="min-w-0 flex-1 whitespace-pre-line">{error}</span>
          </div>
        )}

        {result ? (
          <div className="space-y-3 text-sm">
            <p className="flex items-center gap-2 font-medium">
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />{outcomeText(result)}
            </p>
            <p className="rounded-lg border bg-muted/30 p-3 text-xs leading-5 whitespace-pre-line">
              {result.message}
            </p>
            {!!result.gate_blocked?.length && (
              <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-xs leading-5">
                <p className="mb-1 font-medium">提交审核被门禁拦下，逐条原因：</p>
                <ul className="list-disc space-y-0.5 pl-4">
                  {result.gate_blocked.map(g => <li key={g}>{g}</li>)}
                </ul>
                <p className="mt-1 text-muted-foreground">
                  草稿已经留在内容中心里了，改完这些再提交即可（本次没有删除任何东西）。
                </p>
              </div>
            )}
            {!!result.warnings?.length && (
              <div className="rounded-lg border p-3 text-xs leading-5">
                <p className="mb-1 font-medium">软提醒（审核人需逐条确认）：</p>
                <ul className="list-disc space-y-0.5 pl-4">
                  {result.warnings.map(w => <li key={w.code + w.message}>{w.message}</li>)}
                </ul>
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline"
                onClick={() => navigate(`/content/preview/${encodeURIComponent(result.slug)}`)}>
                到内容中心看这一篇
              </Button>
              <Button size="sm" variant="ghost" onClick={onClose}>关闭</Button>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            {loading && (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />正在判断会追加到哪一篇…
              </p>
            )}

            {/* 归集预判：点下去之前就要看见结果，这是本弹框存在的核心理由 */}
            {preview && (
              <div className="rounded-lg border bg-muted/30 p-3 text-xs leading-5">
                {preview.matched ? (
                  <p>
                    这份报告的历次版本已提升过：本次将作为<strong>新一期</strong>追加到
                    《{preview.matched.title}》（当前已发布 {preview.matched.release_count} 期，
                    状态 {preview.matched.status}）。
                  </p>
                ) : (
                  <p>这份报告还没提升过：本次将<strong>新建一篇</strong>内容草稿。</p>
                )}
                <p className="mt-1 text-muted-foreground">{preview.reason}</p>
                <p className="mt-1 text-muted-foreground">
                  落地形态：{preview.kind_label}（正文 {preview.body_chars} 字）。
                  期次名由发布人在发布时给，这里不预先命名。
                </p>
              </div>
            )}

            <div className="space-y-1.5">
              <Label>栏目{preview?.matched ? '（追加到已有条目时沿用原栏目）' : '（必选）'}</Label>
              <Select value={category} onValueChange={setCategory} disabled={!!preview?.matched}>
                <SelectTrigger aria-label="选择内容栏目"><SelectValue placeholder="选择栏目" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>请选择…</SelectItem>
                  {(preview?.categories ?? []).map(c => (
                    <SelectItem key={c.key} value={c.key}>{c.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {!preview?.matched && (
                <p className="text-[11px] text-muted-foreground">
                  栏目清单来自「内容中心 → 栏目与标签」；没选栏目会被提交门禁硬拦。
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="rp-title">内容标题</Label>
              <Input id="rp-title" value={title} onChange={e => setTitle(e.target.value)}
                placeholder={reportTitle} />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="rp-summary">摘要（卡片与搜索结果只显示这段）</Label>
              <Textarea id="rp-summary" rows={3} value={summary} onChange={e => setSummary(e.target.value)}
                placeholder="留空则用服务端从正文里抽的建议摘要" />
            </div>

            <div className="space-y-1.5">
              <Label>版本策略（历史期次给不给读者看）</Label>
              <Select value={versioning} onValueChange={v => setVersioning(v as ArticleVersioning)}>
                <SelectTrigger aria-label="选择版本策略"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(preview?.versioning_options.length
                    ? preview.versioning_options
                    : [{ value: 'keep_history' as ArticleVersioning, label: '保留历史期次' },
                       { value: 'latest_only' as ArticleVersioning, label: '只保留最新版本' }]
                  ).map(o => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">{VERSIONING_HINTS[versioning]}。</p>
            </div>

            <label className="flex items-center gap-2 text-sm">
              <Switch checked={submitReview} onCheckedChange={setSubmitReview} />
              保存后立即提交内容审核（不勾 = 只存草稿，回头在「内容中心 → 我的草稿」提交）
            </label>
          </div>
        )}

        {!result && (
          <DialogFooter className="gap-2 sm:space-x-0">
            <Button variant="ghost" onClick={onClose} disabled={busy}>取消</Button>
            <Button onClick={submit} disabled={busy || loading || (!!preview && !preview.matched && category === NONE)}>
              <Send className="mr-1.5 h-4 w-4" />{busy ? '提交中' : '提交到内容管理'}
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
