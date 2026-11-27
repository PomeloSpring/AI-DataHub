/**
 * ObjectHubTab — 模型工作区「对象」主画布。
 *
 * 形态（2026-11-19 按 docs/demo/ontology-modeling-redesign.html 换掉旧的「左对 象卡片 + 右宽详情」）：
 *   主画布 = 对象表格（搜索 / 绑定筛选 / 行尾编辑），右侧 = 常驻窄检查器
 *   （概览 · 属性 · 关系 · 指标 · 维度）；
 *   新建对象是表格上方的内联行，编辑对象把检查器整块切成表单 —— 全程不弹居中对 话框。
 *
 * 职责边界（memory「对象Link页面展示规范」）：本页以属性与关系(Link)为主；
 * 「指标」/「维度」两个页签各自列出该对象挂接的口径，并允许就地编辑——
 * 两者是两次独立判断（谁挂了这个对象的指标、谁挂了它的维度），故**分页签**不合成一列；
 * 口径正文的唯一权威仍是字典行（`adh_metrics`/`adh_dimensions`），这里复用字典 的
 * 同一套编辑弹窗与同一写接口，不在此处表达第二份口径。
 */
import { useState, useEffect, useMemo } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { AlertTriangle, Boxes, Layers, Link2, Pencil, Search, X } from 'lucide-react';
import { normalizeDocObjects, type DocObjectIssue } from '@/lib/ontologyObjects';
import { ObjectEditPanel, ObjectNewRow } from './ModelAssetTabs';
import ObjectRefsPanel from './ObjectRefsPanel';
import { ontologyApi, type ContractObjectRow, type DraftObjectDiff, type PropertyDriftEntry } from '@/api/ontology';
import { t } from '@/i18n/runtime';
import { useTranslation } from 'react-i18next';

/** 草案视图下每个对象的状态标记（由服务端 draft_diff 算好，前端不再自己比） */
const DRAFT_STATE_META: Record<string, { label: string; cls: string }> = {
  added: { label: t('k_2cd9e6ce81'), cls: 'text-green-600 border-green-500/40' },
  changed: { label: t('k_c9c77517fe'), cls: 'text-blue-600 border-blue-500/40' },
  removed: { label: t('k_3755f56f2f'), cls: 'text-red-600 border-red-500/40' },
  unchanged: { label: '', cls: '' },
};

export interface HubObject {
  key: string;
  display_name?: string;
  aliases?: string[];
  description?: string;
  primary_table?: string;
  /** 业务本体路由指针（路由索引层）：指向目标源本体/源对象 + 源内过滤提示 */
  route?: {
    mode?: string; source_ontology?: string; datasource_name?: string; object_key?: string;
    filter_hints?: { dimension?: string; examples?: string[] }[];
  };
  properties?: { column?: string; name?: string; type?: string; is_key?: boolean; description?: string; enum?: string[] }[];
  links?: { type?: string; target?: string; cardinality?: string; description?: string }[];
  execution_binding?: { query_mode?: string; size_class?: string; physical_table?: string; bind_kind?: string; dataset_ref?: string };
}

/** 部署绑定层读取状态（对象是否可取数的唯一事实源）+ 属性列漂移（同一次读取带回） */
interface BindState {
  loading: boolean;
  error: string;
  rows: Record<string, ContractObjectRow>;
  /** object key(小写) → 漂移属性；"表还在但列改名"在页面上唯一的可见出口 */
  drift: Record<string, PropertyDriftEntry[]>;
}
const BIND_INIT: BindState = { loading: true, error: '', rows: {}, drift: {} };

/** 检查器页签 */
export type InspTab = 'overview' | 'props' | 'links' | 'metrics' | 'dims';
/** 表格绑定筛选档 */
type BindFilter = 'all' | 'unbound' | 'drift';

export default function ObjectHubTab({ modelId, objects, issues = [], jsonContent, onSave, canEdit = false,
  viewMode = 'live', draftStates, draftHint, modelKind, creating = false, onCreateDone, onCreateCancel,
  focus }: {
  modelId: number;
  objects: HubObject[];
  /** doc 里无法定位身份的元素（已由上层归一剔除），必须在页面上显式提示，不得静默当作“无对象” */
  issues?: DocObjectIssue[];
  /** 编辑对象需的当前 canonical doc + 存草案回调（2026-10-27 开放页面内编辑）。
   *  未传（如只读展示场景）则不渲染编辑入口。 */
  jsonContent?: string;
  /** opts.stay=true：内联新建保存后不跳「变更对比」，留在本页继续补属性/路由 */
  onSave?: (next: string, opts?: { stay?: boolean }) => void;
  /** 是否可编辑（上层按 editable + ontology:save 权限裁决） */
  canEdit?: boolean;
  /** 当前渲染的是生效版还是草案正文（草案视图下对象带状态标记） */
  viewMode?: 'live' | 'draft';
  /** key(小写) → 草案状态；仅草案视图使用 */
  draftStates?: Record<string, DraftObjectDiff>;
  /** 草案视图下的引导文案（如“此处编辑会写入草案”） */
  draftHint?: string;
  /** 模型 kind（源本体不展示「同构站点部署」页，引导文案需随形态区分） */
  modelKind?: string;
  /** 内联新建对象行是否展开（点页头「＋ 新建对象」置真，建完/取消由调用方收起） */
  creating?: boolean;
  onCreateDone?: (key: string) => void;
  onCreateCancel?: () => void;
  /** 体检清单「定位」：seq 变化即选中该对象并打开指定页签（清掉搜索/筛选，避免目标行被筛掉） */
  focus?: { key: string; tab?: InspTab; seq: number };
}) {
  const { t } = useTranslation('ui');
  const readOnly = modelKind === 'system' || !canEdit;
  const canEditHub = !readOnly && !!onSave && jsonContent != null;
  const [selKey, setSelKey] = useState<string>('');
  // 就地编辑中的对象 key：检查器本身切成编辑表单，不再弹居中对话框（2026-11-19 拍板）
  const [editingKey, setEditingKey] = useState<string>('');
  // 检查器是否展开（设计稿 ✕ 收起：点表格行再展开，不靠“总是显示第一个对象”硬撑版面）
  const [inspOpen, setInspOpen] = useState(true);
  // 检查器页签：概览是默认落点（绑定/影响面先看，属性与关系按需切）
  const [inspTab, setInspTab] = useState<InspTab>('overview');
  // 表格搜索与绑定筛选（对象多时靠这两档收敛，不把整份清单怼在脸上）
  const [kw, setKw] = useState('');
  const [bindFilter, setBindFilter] = useState<BindFilter>('all');

  // 切对象必须退出编辑态：否则 A 对象的表单会被套到 B 上（保存写错行）
  const selectObject = (k: string) => {
    setEditingKey('');
    setSelKey(k);
    setInspOpen(true);
  };

  useEffect(() => {
    if (!canEditHub) setEditingKey('');
  }, [canEditHub]);

  // 切模型重置画布状态：否则旧模型的选中行/编辑态会套到新模型同名对象上（保存写错行）
  useEffect(() => {
    setEditingKey('');
    setSelKey('');
    setKw('');
    setBindFilter('all');
    setInspTab('overview');
    setInspOpen(true);
  }, [modelId]);

  // 体检「定位」：按 seq 响应（同一对象反复定位也能再触发），并清掉可能把目标行筛掉的筛选条件
  useEffect(() => {
    if (!focus) return;
    setKw('');
    setBindFilter('all');
    setEditingKey('');
    setSelKey(focus.key);
    setInspTab(focus.tab || 'overview');
    setInspOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.seq]);

  // 上界保护：即便调用方未做归一，也不会因元素缺 key / 非数组而在渲染期崩溃
  const list = useMemo(
    () => normalizeDocObjects(objects).objects as HubObject[],
    [objects]);

  /**
   * 对象绑定状态 = 部署绑定层事实（v0.5 B6b：物理绑定唯一在 `adh_ontology_bindings`，
   * doc 里的 `execution_binding` 在 save_draft 时被剥离、`primary_table` 对新建对象也不存在）。
   * 历史缺陷：徽标拿 doc 字段当裁决 → 对象明明有生效绑定（可取数）却常年显示「未绑表」，
   * 假告警把人工引向错误的修复方向（去补 doc 字段，而真正缺的是字典 target_table 一类错配）。
   */
  const [bind, setBind] = useState<BindState>(BIND_INIT);
  useEffect(() => {
    let cancelled = false;
    setBind(BIND_INIT);
    ontologyApi.sites(modelId)
      .then(({ data }) => {
        if (cancelled) return;
        const rows: Record<string, ContractObjectRow> = {};
        for (const r of (data?.contract_objects || [])) rows[String(r.object_key)] = r;
        const drift: Record<string, PropertyDriftEntry[]> = {};
        for (const [k, v] of Object.entries(data?.property_drift || {})) {
          drift[String(k).toLowerCase()] = v || [];
        }
        setBind({ loading: false, error: '', rows, drift });
      })
      .catch((e: any) => {
        if (cancelled) return;
        // 读不到绑定层时不得回退到 doc 字段“猜一个绑定了”，必须显式未知 + 原因
        setBind({ loading: false, error: String(e?.response?.statusText || e?.message || t('k_eb0c1382c1')), rows: {}, drift: {} });
      });
    return () => { cancelled = true; };
  }, [modelId]);

  const contractOf = (key: string): ContractObjectRow | undefined => {
    const rows = bind.rows;
    return rows[key] || rows[Object.keys(rows).find((k) => k.toLowerCase() === String(key).toLowerCase()) || ''];
  };

  const bindHint = viewMode === 'draft'
    ? t('k_7d27452eb0')
    : modelKind === 'source'
      ? t('k_dd7767afe2')
      : t('k_da0a0a64d0');

  /** 该对象的属性列漂移清单（服务端实时判，不靠前端猜列名） */
  const driftOf = (key: string): PropertyDriftEntry[] =>
    bind.drift[String(key ?? '').toLowerCase()] || [];

  const obj = useMemo(
    () => (inspOpen ? list.find((o) => o.key === selKey) || list[0] || null : null),
    [list, selKey, inspOpen]);

  const objKeys = useMemo(
    () => new Set(list.map((o) => String(o.key ?? '').toLowerCase()).filter(Boolean)),
    [list]);
  const orphanLinks = (obj?.links || []).filter(
    (l) => l.target && !objKeys.has(String(l.target).toLowerCase()));

  /** 表格行过滤：搜索（业务名 / key）+ 绑定状态（未绑表 / 列待修） */
  const filtered = useMemo(() => {
    const k = kw.trim().toLowerCase();
    return list.filter((o) => {
      if (k && !String(o.display_name || '').toLowerCase().includes(k)
        && !String(o.key || '').toLowerCase().includes(k)) return false;
      if (bindFilter === 'drift') return driftOf(o.key).length > 0;
      if (bindFilter === 'unbound') {
        // 路由对象靠源本体对象的物理绑定取数，既不算“已绑本表”也不算缺陷，排除在未绑筛之外
        if (o.route?.source_ontology) return false;
        // 系统本体内置对象（平台元数据表）同样不适用业务绑定口径，不列为「未绑表」
        const row = contractOf(o.key);
        if (row?.binding_source === 'platform') return false;
        return !row?.physical_table;
      }
      return true;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list, kw, bindFilter, bind.rows, bind.drift]);

  const draftStateOf = (key: string) => {
    const ds = draftStates?.[String(key ?? '').toLowerCase()];
    const dsState = viewMode === 'draft' ? ds?.state : undefined;
    return { st: dsState && dsState !== 'unchanged' ? DRAFT_STATE_META[dsState] : null, delta: ds?.delta };
  };

  /** 绑定徽标：未绑/已绑/未知/加载中/路由，全部读部署绑定层 */
  const bindBadge = (o: HubObject) => {
    if (o.route?.source_ontology) {
      return (
        <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-green-600 border-green-500/30"
          title={t('k_f1be533989', { p0: o.route.source_ontology, p1: o.route.object_key ? ' · ' + o.route.object_key : '' })}>
          route
        </Badge>
      );
    }
    if (bind.loading) {
      return <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-muted-foreground border-border"
        title={t("k_2f2d8627fb")}>…</Badge>;
    }
    if (bind.error) {
      return <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-red-500 border-red-500/30"
        title={t('k_77d41e766a', { p0: bind.error })}>{t("k_5c067c3c3a")}</Badge>;
    }
    const c = contractOf(o.key);
    if (c?.binding_source === 'platform') {
      // 系统本体：对象随项目版本发布，不存在“部署绑定/可取数”这回事（历史绑定行已退役）
      return <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-sky-600 border-sky-500/30"
        title={t('k_ac1b38d68a')}>{t('k_7bec3656c9')}</Badge>;
    }
    if (c?.physical_table) {
      return <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-green-600 border-green-500/30"
        title={t('k_eb00b37218', { p0: c.datasource || t('k_a44b739c55'), p1: c.bind_kind || 'primary' })}>bound</Badge>;
    }
    return <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-amber-500 border-amber-500/30"
      title={bindHint}>{t("k_f059a1e4bf")}</Badge>;
  };

  /** 漂移徽标：列不存在（定义要改）与列元数据未同步（要先同步）不是同一件事，分开计数 */
  const driftBadges = (o: HubObject) => {
    const dr = driftOf(o.key);
    if (!dr.length) return null;
    const unver = dr.filter((d) => d.verdict === 'unverified').length;
    const missing = dr.length - unver;
    return (
      <>
        {missing > 0 && (
          <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-red-500 border-red-500/30 shrink-0"
            title={t('k_0d48d341a5', { p0: dr.filter((d) => d.verdict !== 'unverified').map((d) => `${d.property}(${d.column})`).join('、') })}>
            {t('k_6791baa85b', { p0: missing })}
          </Badge>
        )}
        {unver > 0 && (
          <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-amber-500 border-amber-500/30 shrink-0"
            title={t('k_c1d46e565d', { p0: unver })}>
            {t('k_1488962e5c', { p0: unver })}
          </Badge>
        )}
      </>
    );
  };

  /** 物理绑定明细（物理表 + 数据源同一行；含漂移归因，不把两条不同故障说成一句） */
  const bindDetail = (o: HubObject) => {
    const c = contractOf(o.key);
    const dr = driftOf(o.key);
    const unv = dr.filter((d) => d.verdict === 'unverified');
    const bad = dr.filter((d) => d.verdict !== 'unverified');
    const platform = c?.binding_source === 'platform';
    if (c && (c.physical_table || platform)) {
      return (
        <div className="break-words text-xs text-muted-foreground">
          {c.physical_table ? t('k_90448b1471', { p0: c.physical_table }) : (c.object_name || '-')}
          {platform ? t('k_2b67f2ac1e') : null}
          {c.datasource ? <> {t('k_5e9e5ee725', { p0: c.datasource })}</> : null}
          {c.bind_kind === 'dataset' ? t('k_e49adfe7c7') : ''}
          {c.column_count ? t('k_a1d2f4fd41', { p0: c.column_count }) : ''}
          {c.sync_state === 'drifted' ? t('k_ce21714f0b') : ''}
          {dr.length > 0 && (
            <div className="text-amber-600 mt-1 flex items-start gap-1">
              <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
              <span>
                {bad.length > 0 && t('k_a5104b3dec', {
                  p0: bad.length,
                  p1: bad.slice(0, 3).map((d) => `${d.property}→${d.column}`).join(t('k_b505660a97')) + (bad.length > 3 ? t('k_11ccee3ac8') : ''),
                })}
                {bad.length > 0 && unv.length > 0 ? ' ' : ''}
                {unv.length > 0 && t('k_f42836272f', {
                  p0: unv.length,
                  p1: unv.slice(0, 3).map((d) => `${d.property}→${d.column}`).join(t('k_b505660a97')) + (unv.length > 3 ? t('k_11ccee3ac8') : ''),
                  p2: unv[0]?.table || t('k_9b93c54366'),
                })}
              </span>
            </div>
          )}
        </div>
      );
    }
    if (o.execution_binding?.physical_table) {
      // 调用方显式传入的绑定快照（非本页自取）保留展示，但不参与徽标裁决
      return (
        <div className="text-xs text-muted-foreground">
          {t('k_d039f4564c', { p0: o.execution_binding.physical_table || o.primary_table || '-' })}
          {o.execution_binding.query_mode && <> · {o.execution_binding.query_mode}</>}
          {o.execution_binding.bind_kind === 'dataset' && o.execution_binding.dataset_ref &&
            <> {t('k_149a1caef7', { p0: o.execution_binding.dataset_ref })}</>}
        </div>
      );
    }
    if (bind.loading) return <div className="text-xs text-muted-foreground">{t('k_104875140a')}</div>;
    if (bind.error) return <div className="text-xs text-red-500/80">{t('k_40ae01a569', { p0: bind.error })}</div>;
    return (
      <div className="text-xs text-amber-600 flex items-start gap-1">
        <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />{t('k_6a285a8f45', { p0: bindHint })}
      </div>
    );
  };

  const inspTabs: { k: InspTab; label: string }[] = [
    { k: 'overview', label: t('k_5060421d15') },
    { k: 'props', label: t('k_24d67862f8') },
    { k: 'links', label: t('k_eefd831645') },
    // 指标/维度分两个页签：建模时「这个对象挂了哪些指标」「挂了哪些维度」是两次独立判断，
    // 合成一列只是把两件事挤成一次滚动（用户反馈）。
    { k: 'metrics', label: t('k_1dfc810a7b') },
    { k: 'dims', label: t('k_72fa9ecdc4') },
  ];

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {modelKind === 'system' && (
        <div className="rounded-md border border-sky-500/30 bg-sky-500/5 px-2 py-1.5 text-[11px] text-sky-700">
          {t('k_system_ontology_readonly')}
        </div>
      )}
      {/* 内联新建行：就地展开在画布顶部（设计稿 .inline-new），不弹居中对话框 */}
      {canEditHub && creating && jsonContent && onSave && onCreateDone && onCreateCancel && (
        <ObjectNewRow
          jsonContent={jsonContent}
          onSave={onSave}
          modelKind={modelKind}
          onDone={(key) => { selectObject(key); onCreateDone(key); }}
          onCancel={onCreateCancel}
        />
      )}

      <div className="flex min-h-0 flex-1 gap-3">
        {/* ── 主画布：工具条 + 对象表格 ── */}
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          {issues.length > 0 && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-2 py-1.5 text-[11px] text-amber-600">
              <AlertTriangle className="h-3 w-3 mr-1 inline" />
              {t('k_e216c26667', { p0: issues.length, p1: issues.map((e) => `${e.label}${e.hint}`).join('; ') })}
              <div className="mt-0.5 text-amber-600/80">{t("k_7e5b0f1568")}</div>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input className="h-8 w-56 pl-7 text-xs" placeholder={t('k_27db02f61b')}
                aria-label={t('k_27db02f61b')} value={kw}
                onChange={(e) => setKw(e.target.value)} />
            </div>
            <Select value={bindFilter} onValueChange={(v) => setBindFilter(v as BindFilter)}>
              <SelectTrigger className="h-8 w-32 text-xs" aria-label={t('k_6aed7c48be')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t('k_e3b5218039')}</SelectItem>
                <SelectItem value="unbound">{t('k_f1538380a0')}</SelectItem>
                <SelectItem value="drift">{t('k_9771179ce0')}</SelectItem>
              </SelectContent>
            </Select>
            <span className="text-[11px] text-muted-foreground">
              {t('k_af21e8b47a', { p0: list.length })}
              {(kw.trim() || bindFilter !== 'all') && <> · {t('k_1005da93b5', { p0: filtered.length })}</>}
            </span>
            {viewMode === 'draft' && (
              <Badge variant="outline" className="text-[10px] px-1.5 h-4 border-amber-500/40 text-amber-600">
                {t("k_bbcb968bf3")}
              </Badge>
            )}
          </div>

          {viewMode === 'draft' && draftHint && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-2 py-1.5 text-[11px] text-amber-700">
              {draftHint}
            </div>
          )}

          {!list.length ? (
            <div className="flex flex-1 items-center justify-center rounded-lg border text-sm text-muted-foreground">
              <Boxes className="h-8 w-8 mr-2 opacity-40" />
              {issues.length > 0 ? t('k_d54610fd36') : t('k_4b7be4ab87')}
            </div>
          ) : (
            <div className="min-h-0 flex-1 overflow-auto rounded-lg border">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10 bg-muted/60 backdrop-blur">
                  <tr className="text-[11px] font-medium text-muted-foreground">
                    <th className="px-3 py-2 text-left">{t('k_539cb3b0a6')}</th>
                    <th className="w-[132px] px-2 py-2 text-left">{t('k_6aed7c48be')}</th>
                    <th className="w-14 px-2 py-2 text-right">{t('k_24d67862f8')}</th>
                    <th className="w-14 px-2 py-2 text-right">{t('k_eefd831645')}</th>
                    <th className="w-20 px-2 py-2 text-left">{t('k_bbcb968bf3')}</th>
                    <th className="w-16 px-2 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((o) => {
                    const active = obj?.key === o.key;
                    const { st, delta } = draftStateOf(o.key);
                    return (
                      <tr key={o.key}
                        onClick={() => selectObject(o.key)}
                        className={`cursor-pointer border-t transition-colors
                          ${active ? 'bg-primary/5' : 'hover:bg-muted/40'}`}>
                        <td className="px-3 py-1.5">
                          {/* 名称本身是按钮：整行可点，但键盘焦点与可访问名落在对象上 */}
                          <button type="button" className="block w-full text-left"
                            onClick={(e) => { e.stopPropagation(); selectObject(o.key); }}>
                            <span className="flex items-center gap-1.5">
                              <span className="truncate font-medium">{o.display_name || o.key}</span>
                              {o.route?.source_ontology && (
                                <Badge variant="outline" className="h-4 shrink-0 px-1 py-0 text-[10px] text-green-600 border-green-500/30">
                                  route
                                </Badge>
                              )}
                            </span>
                            <span className="block truncate font-mono text-[11px] text-muted-foreground">{o.key}</span>
                          </button>
                        </td>
                        <td className="px-2 py-1.5">
                          <span className="flex flex-wrap items-center gap-1">
                            {bindBadge(o)}
                            {driftBadges(o)}
                          </span>
                        </td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-muted-foreground">
                          {(o.properties || []).length}
                        </td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-muted-foreground">
                          {(o.links || []).length}
                        </td>
                        <td className="px-2 py-1.5">
                          {st ? (
                            <Badge variant="outline" className={`h-4 px-1 py-0 text-[10px] ${st.cls}`}
                              title={delta ? [delta.properties_added?.length ? `+${delta.properties_added.length}` : '',
                                delta.properties_removed?.length ? `-${delta.properties_removed.length}` : '',
                                delta.links_added?.length ? t('k_c57c604bef', { p0: delta.links_added.length }) : '']
                                .filter(Boolean).join(' ') : undefined}>
                              {st.label}
                            </Badge>
                          ) : null}
                        </td>
                        <td className="px-2 py-1.5" onClick={(e) => e.stopPropagation()}>
                          {canEditHub && (
                            <Button size="sm" variant="ghost" className="h-6 px-2 text-[11px]"
                              onClick={() => { selectObject(o.key); setEditingKey(o.key); }}>
                              <Pencil className="h-3 w-3 mr-1" />{t('k_a7f814c0a4')}
                            </Button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                  {!filtered.length && (
                    <tr>
                      <td colSpan={6} className="px-3 py-6 text-center text-xs text-muted-foreground">
                        {t('k_1005da93b5', { p0: 0 })}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* ── 右侧检查器：概览 / 属性 / 关系 / 指标 / 维度（就地编辑时代替为表单） ── */}
        {obj && (
          <aside className={`flex min-h-0 shrink-0 flex-col border-l pl-3
            ${editingKey === obj.key ? 'w-[520px]' : 'w-[380px]'}`}>
            {canEditHub && editingKey === obj.key && jsonContent && onSave ? (
              <div className="min-h-0 flex-1 overflow-auto pr-0.5">
                <ObjectEditPanel
                  key={obj.key}
                  jsonContent={jsonContent}
                  onSave={(next) => { onSave(next); setEditingKey(''); }}
                  onCancel={() => setEditingKey('')}
                  target={obj}
                  modelKind={modelKind}
                  modelId={modelId}
                  boundTable={contractOf(obj.key)?.physical_table || ''}
                  drift={driftOf(obj.key)}
                />
              </div>
            ) : (
              <>
                <div className="flex items-start gap-1.5 pb-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <h3 className="truncate text-sm font-semibold">{obj.display_name || obj.key}</h3>
                      {(() => {
                        const { st } = draftStateOf(obj.key);
                        return st ? (
                          <Badge variant="outline" className={`h-4 px-1 py-0 text-[10px] ${st.cls}`}>{st.label}</Badge>
                        ) : null;
                      })()}
                      {(obj.aliases || []).map((a) => (
                        <Badge key={a} variant="secondary" className="text-[10px] px-1 py-0 h-4">{a}</Badge>
                      ))}
                    </div>
                    <div className="truncate font-mono text-[11px] text-muted-foreground">adh:obj:{obj.key}</div>
                  </div>
                  {canEditHub && (
                    <Button size="sm" variant="outline" className="h-7 shrink-0 px-2 text-xs"
                      onClick={() => { setInspTab('overview'); setEditingKey(obj.key); }}>
                      <Pencil className="h-3 w-3 mr-1" />{t('k_b7eb43c06d')}
                    </Button>
                  )}
                  <Button size="icon" variant="ghost" className="h-7 w-7 shrink-0"
                    title={t('k_bbe3025be1')} aria-label={t('k_bbe3025be1')}
                    onClick={() => { setEditingKey(''); setInspOpen(false); }}>
                    <X className="h-3.5 w-3.5" />
                  </Button>
                </div>

                <div className="flex shrink-0 gap-1 border-b pb-1.5">
                  {inspTabs.map((x) => (
                    <button key={x.k} onClick={() => setInspTab(x.k)}
                      className={`rounded-md px-2 py-1 text-xs transition-colors
                        ${inspTab === x.k ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:bg-muted'}`}>
                      {x.label}
                    </button>
                  ))}
                </div>

                <div className="min-h-0 flex-1 space-y-3 overflow-auto pt-2 pr-0.5">
                  {inspTab === 'overview' && (
                    <>
                      {obj.description && (
                        <p className="text-xs text-muted-foreground">{obj.description}</p>
                      )}

                      <section>
                        <div className="mb-1 text-[11px] font-medium text-muted-foreground">{t('k_6aed7c48be')}</div>
                        {/* 物理表 / 数据源 / 漂移同属这一行（bindDetail 一处渲染，避免同一表名在页面上重复出现） */}
                        <div className="mb-1.5">{bindDetail(obj)}</div>
                        <dl className="grid grid-cols-[64px_1fr] gap-x-2 gap-y-1 text-xs">
                          <dt className="text-muted-foreground">{t('k_854e1d4f9a')}</dt>
                          <dd className="text-muted-foreground">
                            {obj.route?.source_ontology
                              ? `${t('k_8ce9b56b15')} → ${obj.route.source_ontology}`
                              : contractOf(obj.key)?.bind_kind === 'dataset'
                                ? 'dataset'
                                : contractOf(obj.key)?.physical_table ? t('k_8ce9b56b15') : '—'}
                          </dd>
                          {contractOf(obj.key)?.allowed_sites?.length ? (<>
                            <dt className="text-muted-foreground">{t('k_236ed6c03b')}</dt>
                            <dd className="text-muted-foreground">{contractOf(obj.key)!.allowed_sites.join('、')}</dd>
                          </>) : null}
                        </dl>
                        {obj.route?.source_ontology && (
                          <div className="mt-1 text-[11px] text-muted-foreground">
                            {t('k_007128c7cd', {
                              p0: obj.route.source_ontology,
                              p1: obj.route.object_key ? t('k_31d0f1f3e0', { p0: obj.route.object_key }) : '',
                              p2: obj.route.datasource_name ? t('k_0bfb0f0d36', { p0: obj.route.datasource_name }) : '',
                              p3: (obj.route.filter_hints || []).length > 0 &&
                                t('k_b841a13976', {
                                  p0: obj.route.filter_hints!.map((h) => `${h.dimension}(${(h.examples || []).join('/')})`).join(t('k_c4530db333')),
                                }),
                            })}
                          </div>
                        )}
                      </section>

                      <section>
                        <div className="mb-1 text-[11px] font-medium text-muted-foreground">{t('k_d21cbda6e9')}</div>
                        <dl className="grid grid-cols-[64px_1fr] gap-x-2 gap-y-1 text-xs">
                          <dt className="text-muted-foreground">{t('k_eefd831645')}</dt>
                          <dd className="text-muted-foreground">{(obj.links || []).length}</dd>
                          <dt className="text-muted-foreground">{t('k_24d67862f8')}</dt>
                          <dd className="text-muted-foreground">{(obj.properties || []).length}</dd>
                          <dt className="text-muted-foreground">{t('k_236ed6c03b')}</dt>
                          <dd className="text-muted-foreground">
                            {contractOf(obj.key)?.allowed_sites?.length
                              ? contractOf(obj.key)!.allowed_sites.join('、')
                              : t('k_449330e6f5')}
                          </dd>
                        </dl>
                        {orphanLinks.length > 0 && (
                          <div className="mt-1 text-[11px] text-amber-600">{t('k_286d6ce2dd', { p0: orphanLinks.length })}</div>
                        )}
                      </section>
                    </>
                  )}

                  {inspTab === 'props' && (
                    <section>
                      <div className="mb-1 flex items-center gap-2 text-[11px] font-medium text-muted-foreground">
                        <Layers className="h-3.5 w-3.5" />{t('k_83489a8e2f', { p0: (obj.properties || []).length })}
                      </div>
                      <div className="rounded-md border">
                        {(() => {
                          const dr = driftOf(obj.key);
                          return (obj.properties || []).map((p, i) => {
                            const d = dr.find((x) => x.property && x.property === (p.name || p.column));
                            return (
                              <div key={p.column || i} className="flex items-center gap-2 border-b px-2 py-1.5 text-[13px] last:border-0">
                                <span className="truncate">{p.name || p.column}</span>
                                <span className={`truncate font-mono text-xs ${d ? 'text-red-500' : 'text-muted-foreground'}`}>{p.column}</span>
                                <span className="ml-auto flex items-center gap-1">
                                  {d && (
                                    <Badge variant="outline" className="h-4 px-1 text-[10px] text-red-500 border-red-500/40"
                                      title={d.verdict === 'unverified'
                                        ? t('k_7eb22f2e8c', { p0: d.column })
                                        : t('k_863abb4a59', { p0: d.column, p1: d.table })}>
                                      {d.verdict === 'unverified' ? t('k_a9a5e3e6f1') : t('k_79d66eb6b3')}
                                    </Badge>
                                  )}
                                  {p.is_key && <Badge variant="outline" className="h-4 px-1 text-[10px]">PK</Badge>}
                                  {p.enum?.length ? <Badge variant="outline" className="h-4 px-1 text-[10px]">{t('k_c85885de70', { p0: p.enum.length })}</Badge> : null}
                                </span>
                              </div>
                            );
                          });
                        })()}
                        {!(obj.properties || []).length && (
                          <div className="p-3 text-xs text-muted-foreground">{t("k_7c1acf8680")}</div>
                        )}
                      </div>
                    </section>
                  )}

                  {inspTab === 'links' && (
                    <section>
                      <div className="mb-1 flex items-center gap-2 text-[11px] font-medium text-muted-foreground">
                        <Link2 className="h-3.5 w-3.5" />{t('k_73286542d3', { p0: (obj.links || []).length })}
                      </div>
                      <div className="rounded-md border">
                        {(obj.links || []).map((l, i) => {
                          const orphan = l.target && !objKeys.has(String(l.target).toLowerCase());
                          return (
                            <div key={i} className={`flex items-center gap-2 border-b px-2 py-1.5 text-[13px] last:border-0 ${orphan ? 'text-amber-500' : ''}`}>
                              <span>{l.type || 'references'}</span>
                              <button className="underline decoration-dotted underline-offset-2"
                                onClick={() => l.target && selectObject(String(l.target))}>{l.target}</button>
                              {l.cardinality && <span className="text-xs text-muted-foreground">{l.cardinality}</span>}
                              {orphan && <span title={t("k_02efe42ae7")}><AlertTriangle className="h-3.5 w-3.5 inline" /></span>}
                            </div>
                          );
                        })}
                        {!(obj.links || []).length && <div className="p-3 text-xs text-muted-foreground">{t("k_d1db64962d")}</div>}
                      </div>
                      {orphanLinks.length > 0 && (
                        <div className="mt-1 text-[11px] text-amber-500">{t('k_286d6ce2dd', { p0: orphanLinks.length })}</div>
                      )}
                    </section>
                  )}

                  {inspTab === 'metrics' && (
                    <ObjectRefsPanel
                      section="metrics"
                      objectKey={String(obj.key)}
                      modelId={modelId}
                      /* 归属对象候选 = 本模型对象（选项显示业务名，见 ui-resource-display） */
                      objects={list.map((o) => ({
                        key: String(o.key),
                        display_name: o.display_name || String(o.key),
                      }))}
                      /* 写权限沿用本页编辑闸（字典写入当前无独立权限码，与指标中心页同口径） */
                      canEdit={!readOnly}
                    />
                  )}

                  {inspTab === 'dims' && (
                    <ObjectRefsPanel
                      section="dimensions"
                      objectKey={String(obj.key)}
                      modelId={modelId}
                      objects={list.map((o) => ({
                        key: String(o.key),
                        display_name: o.display_name || String(o.key),
                      }))}
                      canEdit={!readOnly}
                    />
                  )}
                </div>
              </>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}