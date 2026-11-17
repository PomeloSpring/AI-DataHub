import { useState, useEffect } from 'react';
import { t , t as tr } from '@/i18n/runtime';
import {
  Plus, Edit2, Trash2, Eye, Search, Sparkles, BookOpen, Lock, FileCode, History, RotateCcw,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Spinner } from '@/components/ui/spinner';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { skillApi, type Skill, type SkillUpsert, type SkillVersion } from '@/api/skill';
import { useTranslation } from 'react-i18next';

// ── Category Options ───────────────────────────────────────────────

const CATEGORIES = [
  { value: 'nl2sql', label: 'NL2SQL' },
  { value: 'analysis', label: t('k_6450d84327') },
  { value: 'chart', label: t('k_90253967f2') },
  { value: 'correction', label: t('k_c7f9710b19') },
  { value: 'prediction', label: t('k_09d557c31f') },
  { value: 'custom', label: t('k_c493338e8c') },
];

const catLabel = (c: string) => CATEGORIES.find((x) => x.value === c)?.label || c || tr('k_b28f13ea3e');

// ── Main Component ─────────────────────────────────────────────────

export default function SkillsManager() {
  const { t } = useTranslation('ui');
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [filterBuiltin, setFilterBuiltin] = useState<'all' | 'builtin' | 'custom'>('all');

  const [viewing, setViewing] = useState<Skill | null>(null);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Skill | null>(null);
  const [deleting, setDeleting] = useState<Skill | null>(null);

  // 版本历史(SKILL.md 快照; 保存时自动生成, 保留最近 20 份)
  const [versionsFor, setVersionsFor] = useState<Skill | null>(null);
  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [loadingVersions, setLoadingVersions] = useState(false);

  const openVersions = async (s: Skill) => {
    setVersionsFor(s);
    setLoadingVersions(true);
    try {
      const { data } = await skillApi.versions(s.name);
      setVersions(data?.versions || []);
    } catch {
      setVersions([]);
    } finally {
      setLoadingVersions(false);
    }
  };

  const handleRollback = async (v: SkillVersion) => {
    if (!versionsFor) return;
    try {
      await skillApi.rollback(versionsFor.name, v.version);
      toast.success(t('k_6bab5e3d2f', { p0: v.version }));
      setVersionsFor(null);
      loadSkills();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || t('k_22d7b7389c'));
    }
  };

  useEffect(() => {
    loadSkills();
  }, []);

  const loadSkills = async () => {
    setLoading(true);
    try {
      const { data } = await skillApi.list();
      setSkills(Array.isArray(data) ? data : []);
    } catch (error: any) {
      // 接口无数据 / 未命中(404) 不弹错,仅展示空状态;只有真正的服务端错误才提示
      const status = error?.response?.status;
      console.error('Failed to load skills:', error);
      setSkills([]);
      if (status && status >= 500) {
        toast.error(t('k_4a844e2925'));
      }
    } finally {
      setLoading(false);
    }
  };

  const openView = async (name: string) => {
    try {
      const { data } = await skillApi.get(name);
      setViewing(data);
    } catch (error: any) {
      toast.error(error.response?.data?.detail || t('k_bcd0ba34d4'));
    }
  };

  const handleDelete = async () => {
    if (!deleting) return;
    try {
      await skillApi.delete(deleting.name);
      toast.success(t('k_3d2de756cc', { p0: deleting.display_name || deleting.name }));
      setDeleting(null);
      loadSkills();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || t('k_acf0664a54'));
    }
  };

  const filtered = skills.filter((s) => {
    if (filterBuiltin === 'builtin' && !s.is_builtin) return false;
    if (filterBuiltin === 'custom' && s.is_builtin) return false;
    const kw = search.trim().toLowerCase();
    if (!kw) return true;
    return (
      s.name.toLowerCase().includes(kw) ||
      (s.display_name || '').toLowerCase().includes(kw) ||
      (s.description || '').toLowerCase().includes(kw)
    );
  });

  const builtinCount = skills.filter((s) => s.is_builtin).length;
  const customCount = skills.length - builtinCount;

  return (
    <div className="p-6 w-full">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">{t("k_d9377286b9")}</h1>
          <p className="text-muted-foreground mt-1">
            {t('k_df340e4aa9', { p0: builtinCount, p1: customCount })}</p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus className="h-4 w-4 mr-2" />
          {t('k_8c339e83ee')}
        </Button>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3 mb-4">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            className="pl-9"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("k_56f2534e01")}
          />
        </div>
        <div className="flex items-center gap-1">
          {(['all', 'builtin', 'custom'] as const).map((k) => (
            <Button
              key={k}
              size="sm"
              variant={filterBuiltin === k ? 'default' : 'outline'}
              onClick={() => setFilterBuiltin(k)}
            >
              {k === 'all' ? t('k_778fc8f994') : k === 'builtin' ? t('k_09ceea7644') : t('k_c493338e8c')}
            </Button>
          ))}
        </div>
      </div>

      {/* List */}
      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner size={32} />
        </div>
      ) : filtered.length === 0 ? (
        <div className="border rounded-lg p-12 text-center">
          <Sparkles className="h-12 w-12 mx-auto mb-4 text-muted-foreground" />
          <h3 className="text-lg font-medium mb-2">{t("k_6386d83f7c")}</h3>
          <p className="text-muted-foreground mb-4">{t("k_5f61b651bb")}</p>
          <Button onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4 mr-2" />
            {t('k_8c339e83ee')}
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {filtered.map((s) => (
            <div key={s.name} className="border rounded-lg p-4 flex flex-col gap-2 hover:bg-muted/30 transition-colors">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold truncate">{s.display_name || s.name}</span>
                    {s.is_builtin ? (
                      <Badge className="bg-indigo-100 text-indigo-800">
                        <Lock className="h-3 w-3 mr-1" />{t('k_09ceea7644')}
                      </Badge>
                    ) : (
                      <Badge variant="secondary">{t("k_c493338e8c")}</Badge>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground font-mono truncate">{s.name}</div>
                </div>
                {s.format === 'skill_md' && (
                  <FileCode className="h-4 w-4 text-muted-foreground shrink-0" aria-label="SKILL.md" />
                )}
              </div>

              <p className="text-sm text-muted-foreground line-clamp-2 flex-1">
                {s.description || t('k_d496aef938')}
              </p>

              <div className="flex items-center gap-2">
                <Badge variant="outline">{catLabel(s.category)}</Badge>
              </div>

              <div className="flex items-center justify-end gap-1 pt-1">
                <Button variant="ghost" size="sm" onClick={() => openView(s.name)} title={t("k_f7acefd2d4")}>
                  <Eye className="h-4 w-4" />
                </Button>
                {!s.is_builtin && (
                  <>
                    <Button variant="ghost" size="sm" onClick={() => setEditing(s)} title={t("k_a7f814c0a4")}>
                      <Edit2 className="h-4 w-4" />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => openVersions(s)} title={t("k_8770418ba3")}>
                      <History className="h-4 w-4" />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setDeleting(s)} title={t("k_3755f56f2f")}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* View Dialog */}
      {viewing && (
        <Dialog open onOpenChange={(v) => { if (!v) setViewing(null); }}>
          <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <BookOpen className="h-5 w-5" />
                {viewing.display_name || viewing.name}
                {viewing.is_builtin && <Badge className="bg-indigo-100 text-indigo-800">{t("k_9dcaae2f26")}</Badge>}
              </DialogTitle>
              <DialogDescription className="font-mono text-xs">{viewing.name}</DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              {viewing.description && (
                <div>
                  <div className="text-sm font-medium mb-1">{t("k_412f54dc38")}</div>
                  <p className="text-sm text-muted-foreground">{viewing.description}</p>
                </div>
              )}
              <div>
                <div className="text-sm font-medium mb-1">{t('k_353fbc6b08')}</div>
                <pre className="whitespace-pre-wrap break-words text-xs bg-muted/50 rounded-lg p-3 max-h-[50vh] overflow-y-auto">
                  {viewing.markdown || viewing.system_prompt || t('k_37ec4ea998')}
                </pre>
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setViewing(null)}>{t("k_6c14bd7f6f")}</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

      {/* Create / Edit Dialog */}
      {(creating || editing) && (
        <SkillFormDialog
          skill={editing}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={() => { setCreating(false); setEditing(null); loadSkills(); }}
        />
      )}

      {/* Delete Confirmation */}
      <Dialog open={!!deleting} onOpenChange={() => setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("k_3c06abe116")}</DialogTitle>
            <DialogDescription>
              {t('k_0514b05550', { p0: deleting?.display_name || deleting?.name })}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleting(null)}>{t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={handleDelete}>{t("k_3755f56f2f")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 版本历史 Dialog */}
      <Dialog open={!!versionsFor} onOpenChange={(o) => !o && setVersionsFor(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t('k_5b7a1ad6bf', { p0: versionsFor?.display_name || versionsFor?.name })}</DialogTitle>
            <DialogDescription>
              {t('k_701d41c46e')}
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[50vh] overflow-y-auto divide-y">
            {loadingVersions && <p className="p-4 text-sm text-muted-foreground">{t("k_300ee3dee4")}</p>}
            {!loadingVersions && versions.length === 0 && (
              <p className="p-4 text-sm text-muted-foreground">{t("k_1e8e7e0723")}</p>
            )}
            {versions.map((v, idx) => (
              <div key={v.version} className="flex items-start gap-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">
                    {v.version.replace(/(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/, '$1-$2-$3 $4:$5:$6')} UTC
                    {idx === 0 && <Badge variant="outline" className="ml-2 text-[10px]">{t("k_b11bc1299e")}</Badge>}
                  </div>
                  <pre className="mt-1 whitespace-pre-wrap break-all text-[11px] text-muted-foreground font-mono max-h-20 overflow-hidden">
                    {v.preview}
                  </pre>
                </div>
                <Button variant="outline" size="sm" onClick={() => handleRollback(v)} title={t("k_5fad9becdd")}>
                  <RotateCcw className="h-3.5 w-3.5 mr-1" />{t('k_d00b485b26')}
                </Button>
              </div>
            ))}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setVersionsFor(null)}>{t("k_6c14bd7f6f")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Create / Edit Dialog ───────────────────────────────────────────

function SkillFormDialog({
  skill,
  onClose,
  onSaved,
}: {
  skill: Skill | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation('ui');
  const isEdit = !!skill;
  const [form, setForm] = useState<SkillUpsert>({
    name: skill?.name || '',
    display_name: skill?.display_name || '',
    description: skill?.description || '',
    category: skill?.category || 'custom',
    system_prompt: '',
  });
  const [saving, setSaving] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);

  useEffect(() => {
    if (!skill) return;
    // 拉取详情填充正文
    setLoadingDetail(true);
    skillApi.get(skill.name)
      .then(({ data }) => {
        setForm((prev) => ({
          ...prev,
          display_name: data.display_name || prev.display_name,
          description: data.description || prev.description,
          category: data.category || prev.category,
          system_prompt: data.markdown || data.system_prompt || '',
        }));
      })
      .catch(() => toast.error(t('k_12667a8886')))
      .finally(() => setLoadingDetail(false));
  }, [skill]);

  const handleSave = async () => {
    if (!form.name.trim()) {
      toast.error(t('k_137733f469'));
      return;
    }
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(form.name)) {
      toast.error(t('k_198c84800e'));
      return;
    }
    setSaving(true);
    try {
      if (isEdit && skill) {
        await skillApi.update(skill.name, form);
        toast.success(t('k_cb4f51ec77'));
      } else {
        await skillApi.create(form);
        toast.success(t('k_e3dcc867f8'));
      }
      onSaved();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || t('k_6de920b4e4'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? t('k_50b11c408f') : t('k_8c339e83ee')}</DialogTitle>
          <DialogDescription>
            {t('k_f015ad1c59')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <Label>{t('k_d5384042de')}</Label>
            <Input
              value={form.name}
              disabled={isEdit}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="my-custom-skill"
              className="font-mono"
            />
            <p className="text-xs text-muted-foreground">{t("k_75646b8ef8")}</p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label>{t("k_c10bbf5ddd")}</Label>
              <Input
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                placeholder={t("k_05337a0778")}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("k_435c5259e4")}</Label>
              <Select
                value={form.category}
                onValueChange={(v) => setForm({ ...form, category: v })}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {CATEGORIES.map((c) => (
                    <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-2">
            <Label>{t("k_412f54dc38")}</Label>
            <Textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder={t("k_e846e17ee3")}
              rows={2}
            />
          </div>
          <div className="space-y-2">
            <Label>{t('k_fb68f06a1a')}</Label>
            <Textarea
              value={form.system_prompt}
              onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
              placeholder={t('k_92a179d415')}
              rows={12}
              className="font-mono text-xs"
              disabled={loadingDetail}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t("k_4d0b4688c7")}</Button>
          <Button onClick={handleSave} disabled={saving || loadingDetail}>
            {saving ? t('k_2a33020e5b') : t('k_fadf24dbc5')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
