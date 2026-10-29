import { useCallback, useEffect, useMemo, useState } from 'react';
import { Megaphone, Plus, Edit, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { Spinner } from '@/components/ui/spinner';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { releaseNotesApi, type ReleaseNote } from '@/api/releaseNotes';
import { GUIDE_OPTIONS } from '@/lib/onboarding/guides';
import { toast } from '@/lib/toast';

/**
 * 版本公告管理（系统配置 → 版本公告）。
 *
 * 这里只维护**公告内容**：版本号、标题、摘要、分组更新项、关联引导、是否重大版本。
 * 引导的**步骤定义**在前端 lib/onboarding/guides.ts（与页面 data-tour 锚点同批演进），
 * 落库会让"库里的 selector"和"前端代码版本"错配，升级后引导点到空气，故不提供编辑入口。
 *
 * 勾选「重大版本」并发布会给全体用户重触发升级提示（顶栏红点 + 欢迎总览），
 * 是发布节奏的唯一开关。
 */

const SECTION_META = [
  { kind: 'feature', label: '新增', hint: '每行一条，写用户能感知的能力' },
  { kind: 'improvement', label: '优化', hint: '每行一条，已有功能改进了什么' },
  { kind: 'fix', label: '修复', hint: '每行一条，修掉了什么问题' },
] as const;

type SectionKind = typeof SECTION_META[number]['kind'];
/** 引导选择框用 'none' 表"不关联"(Radix Select 不允许空串值) */
const NO_GUIDE = 'none';

interface FormState {
  id: number | null;
  version: string;
  title: string;
  summary: string;
  guide_key: string;
  is_major: boolean;
  is_published: boolean;
  groups: Record<SectionKind, string>;
}

const emptyForm = (): FormState => ({
  id: null,
  version: '',
  title: '',
  summary: '',
  guide_key: NO_GUIDE,
  is_major: false,
  is_published: true,
  groups: { feature: '', improvement: '', fix: '' },
});

/** 文本域 → 分组数组(空行忽略) */
const toSections = (groups: Record<SectionKind, string>) =>
  SECTION_META
    .map(({ kind }) => ({ kind, items: groups[kind].split('\n').map(s => s.trim()).filter(Boolean) }))
    .filter(g => g.items.length > 0);

const splitLines = (note: ReleaseNote, kind: SectionKind) =>
  note.sections.filter(s => s.kind === kind).flatMap(s => s.items).join('\n');

export default function ReleaseNotesManager() {
  const [notes, setNotes] = useState<ReleaseNote[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [form, setForm] = useState<FormState | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ReleaseNote | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { data } = await releaseNotesApi.adminList();
      setNotes(data.items || []);
    } catch (e: any) {
      // 加载失败显式报错并可重试，不静默渲成空清单（no-silent-degradation）
      setError(e?.response?.data?.detail || '公告清单加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const openCreate = () => setForm(emptyForm());

  const openEdit = (note: ReleaseNote) => setForm({
    id: note.id,
    version: note.version,
    title: note.title,
    summary: note.summary || '',
    guide_key: note.guide_key || NO_GUIDE,
    is_major: !!note.is_major,
    is_published: !!note.is_published,
    groups: {
      feature: splitLines(note, 'feature'),
      improvement: splitLines(note, 'improvement'),
      fix: splitLines(note, 'fix'),
    },
  });

  const sections = useMemo(() => (form ? toSections(form.groups) : []), [form]);

  const handleSave = async () => {
    if (!form) return;
    const version = form.version.trim();
    const title = form.title.trim();
    if (!version || !title) { toast.error('版本号与标题都不能为空'); return; }
    if (!/^\d+\.\d+(?:\.\d+)?(?:-[0-9A-Za-z.]+)?$/.test(version)) {
      toast.error('版本号格式不正确，示例：1.2.0');
      return;
    }
    if (!sections.length) { toast.error('至少填写一条更新项（新增/优化/修复任一分组）'); return; }
    const payload = {
      version,
      title,
      summary: form.summary.trim(),
      sections,
      guide_key: form.guide_key === NO_GUIDE ? '' : form.guide_key,
      is_major: form.is_major ? 1 : 0,
      is_published: form.is_published ? 1 : 0,
    };
    setSaving(true);
    try {
      if (form.id) await releaseNotesApi.adminUpdate(form.id, payload);
      else await releaseNotesApi.adminCreate(payload);
      toast.success(form.id ? `已更新公告 v${version}` : `已保存公告 v${version}`);
      setForm(null);
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await releaseNotesApi.adminRemove(deleteTarget.id);
      toast.success(`已删除公告 v${deleteTarget.version}`);
      setDeleteTarget(null);
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '删除失败');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="flex items-center gap-2 text-lg font-semibold">
            <Megaphone className="h-5 w-5 text-primary" />版本公告
          </h3>
          <p className="text-sm text-muted-foreground">
            顶栏「版本更新公告」的数据源。勾选「重大版本」后发布会向全体用户提示一次升级亮点。
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>刷新</Button>
          <Button size="sm" onClick={openCreate}>
            <Plus className="mr-1.5 h-4 w-4" />新增公告
          </Button>
        </div>
      </div>

      {loading && <div className="flex justify-center py-10"><Spinner size={28} /></div>}
      {!loading && error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {error}
          <Button variant="link" size="sm" className="ml-2 h-auto p-0" onClick={load}>重试</Button>
        </div>
      )}
      {!loading && !error && !notes.length && (
        <p className="py-8 text-center text-sm text-muted-foreground">还没有公告，点「新增公告」发第一条</p>
      )}

      {!loading && !!notes.length && (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[110px]">版本</TableHead>
                <TableHead>标题</TableHead>
                <TableHead className="w-[210px]">更新项</TableHead>
                <TableHead className="w-[150px]">关联引导</TableHead>
                <TableHead className="w-[110px]">状态</TableHead>
                <TableHead className="w-[100px] text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {notes.map(n => (
                <TableRow key={n.id}>
                  <TableCell className="font-medium">v{n.version}</TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <span className="truncate">{n.title}</span>
                      {!!n.is_major && <Badge className="shrink-0">重大版本</Badge>}
                    </div>
                    {!!n.summary && <div className="mt-0.5 truncate text-xs text-muted-foreground">{n.summary}</div>}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {SECTION_META.map(({ kind, label }) => {
                      const count = n.sections.filter(s => s.kind === kind).reduce((m, s) => m + s.items.length, 0);
                      return count ? `${label} ${count}` : null;
                    }).filter(Boolean).join(' · ') || '—'}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {GUIDE_OPTIONS.find(o => o.id === n.guide_key)?.label || '未关联'}
                  </TableCell>
                  <TableCell>
                    {n.is_published
                      ? <Badge variant="secondary" className="font-normal">已发布</Badge>
                      : <Badge variant="outline" className="font-normal">草稿</Badge>}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button size="sm" variant="ghost" className="h-7 w-7 p-0" title="编辑" onClick={() => openEdit(n)}>
                      <Edit className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      size="sm" variant="ghost" className="h-7 w-7 p-0 text-destructive" title="删除"
                      onClick={() => setDeleteTarget(n)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* 新增/编辑公告 */}
      <Dialog open={!!form} onOpenChange={v => { if (!v) setForm(null); }}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{form?.id ? '编辑公告' : '新增公告'}</DialogTitle>
            <DialogDescription>
              只填有内容的分组，空分组不会展示给用户。「重大版本」会向全体用户提示一次，请谨慎勾选。
            </DialogDescription>
          </DialogHeader>
          {form && (
            <div className="space-y-4">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-[160px_1fr]">
                <div className="space-y-2">
                  <Label htmlFor="rn-version">版本号 *</Label>
                  <Input
                    id="rn-version" value={form.version}
                    onChange={e => setForm({ ...form, version: e.target.value })}
                    placeholder="如：1.2.0"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="rn-title">标题 *</Label>
                  <Input
                    id="rn-title" value={form.title}
                    onChange={e => setForm({ ...form, title: e.target.value })}
                    placeholder="如：新手引导与版本公告"
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="rn-summary">一句话摘要</Label>
                <Input
                  id="rn-summary" value={form.summary}
                  onChange={e => setForm({ ...form, summary: e.target.value })}
                  placeholder="给忙的人看的版本要点，显示在版本清单里"
                />
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>关联引导</Label>
                  <Select
                    value={form.guide_key}
                    onValueChange={v => setForm({ ...form, guide_key: v })}
                  >
                    <SelectTrigger><SelectValue placeholder="选择不关联" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NO_GUIDE}>不关联</SelectItem>
                      {GUIDE_OPTIONS.map(o => (
                        <SelectItem key={o.id} value={o.id}>{o.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    选一个前端已提供的引导，公告面板会给用户「查看新功能引导」按钮。
                  </p>
                </div>
                <div className="space-y-3 pt-0.5">
                  <div className="flex items-center gap-2">
                    <Switch
                      id="rn-major" checked={form.is_major}
                      onCheckedChange={v => setForm({ ...form, is_major: v })}
                    />
                    <Label htmlFor="rn-major">重大版本（升级时向全体用户提示）</Label>
                  </div>
                  <div className="flex items-center gap-2">
                    <Switch
                      id="rn-published" checked={form.is_published}
                      onCheckedChange={v => setForm({ ...form, is_published: v })}
                    />
                    <Label htmlFor="rn-published">发布（未发布仅本页可见）</Label>
                  </div>
                </div>
              </div>

              <div className="space-y-3">
                {SECTION_META.map(({ kind, label, hint }) => (
                  <div key={kind} className="space-y-1.5">
                    <Label htmlFor={`rn-${kind}`}>{label}项</Label>
                    <Textarea
                      id={`rn-${kind}`} rows={2} value={form.groups[kind]}
                      onChange={e => setForm({ ...form, groups: { ...form.groups, [kind]: e.target.value } })}
                      placeholder={hint}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setForm(null)} disabled={saving}>取消</Button>
            <Button onClick={handleSave} disabled={saving || !form}>
              {saving && <Spinner size={14} className="mr-2" />}
              {form?.id ? '保存修改' : '创建公告'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认: 公告是用户会看到的对外内容, 不能一点就删 */}
      <Dialog open={!!deleteTarget} onOpenChange={v => { if (!v) setDeleteTarget(null); }}>
        <DialogContent className="max-w-[420px]">
          <DialogHeader>
            <DialogTitle>删除公告 v{deleteTarget?.version}？</DialogTitle>
            <DialogDescription>
              删除后用户顶栏公告面板里不再显示这一版；已读状态留在各用户身上，不影响其它版本。此操作不可撤销。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)} disabled={deleting}>取消</Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
              {deleting && <Spinner size={14} className="mr-2" />}
              确认删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
