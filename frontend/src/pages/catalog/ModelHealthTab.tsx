/**
 * ModelHealthTab — 「建模体检」：把本模型的建模缺口聚成一张可逐条定位的清单。
 *
 * 与「建模待办」的分工（2026-11-20 用户确认拆分）：
 *   体检 = 本模型对象级（不可取数 / 列漂移 / 孤儿关系 / 缺 key），每条都能定位回对象画布；
 *   待办 = 跨模型（源本体里未收编的资产），在左侧「跨模型」分区。
 *
 * 判定不另造第二套：绑定与漂移都读部署绑定层 `ontologyApi.sites`
 * 的 `contract_objects` / `property_drift`（与对象画布同一份事实）；孤儿关系按 doc 里的
 * objects/links 比对。**读不到绑定层时只说“无法判定 + 重试”，绝不把读不到当成“全都未绑表”**
 * —— 假告警比不显示危害更大（会把人工引向错误的修复方向）。
 */
import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, Info, RotateCw } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ontologyApi, type ContractObjectRow, type DraftObjectDiff, type PropertyDriftEntry } from '@/api/ontology';
import type { DocObjectIssue } from '@/lib/ontologyObjects';
import { useTranslation } from 'react-i18next';
import type { HubObject, InspTab } from './ObjectHubTab';

type Severity = 'block' | 'warn';

interface HealthItem {
  id: string;
  label: string;
  hint?: string;
  /** 有对象 key 才能定位回画布（缺 key 的 doc 元素无法定位，只能提示去 JSON 补） */
  objectKey?: string;
  tab?: InspTab;
}

interface HealthGroup {
  key: string;
  sev: Severity;
  title: string;
  items: HealthItem[];
}

export default function ModelHealthTab({ modelId, objects, issues = [], draftStates, modelKind, viewMode = 'live', onLocate, onSummary }: {
  modelId: number;
  objects: HubObject[];
  issues?: DocObjectIssue[];
  /** 草案视图的逐对象状态：草案新增对象未绑表是预期（发布时级联登记），不能算硬阻断 */
  draftStates?: Record<string, DraftObjectDiff>;
  modelKind?: string;
  viewMode?: 'live' | 'draft';
  onLocate?: (key: string, tab?: InspTab) => void;
  /** 上报问题数给左侧入口徽标（本页签未挂载时由调用方保留上一次的值） */
  onSummary?: (n: number) => void;
}) {
  const { t } = useTranslation('ui');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [rows, setRows] = useState<Record<string, ContractObjectRow>>({});
  const [drift, setDrift] = useState<Record<string, PropertyDriftEntry[]>>({});
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    ontologyApi.sites(modelId)
      .then(({ data }) => {
        if (cancelled) return;
        const nextRows: Record<string, ContractObjectRow> = {};
        for (const r of (data?.contract_objects || [])) nextRows[String(r.object_key)] = r;
        const nextDrift: Record<string, PropertyDriftEntry[]> = {};
        for (const [k, v] of Object.entries(data?.property_drift || {})) {
          nextDrift[String(k).toLowerCase()] = v || [];
        }
        setRows(nextRows);
        setDrift(nextDrift);
        setLoading(false);
      })
      .catch((e: any) => {
        if (cancelled) return;
        setError(String(e?.response?.statusText || e?.message || ''));
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [modelId, nonce]);

  const contractOf = (key: string) => {
    const hit = rows[key];
    if (hit) return hit;
    const lower = String(key || '').toLowerCase();
    const k = Object.keys(rows).find((x) => x.toLowerCase() === lower);
    return k ? rows[k] : undefined;
  };
  const driftOf = (key: string) => drift[String(key || '').toLowerCase()] || [];

  /** 绑定层读失败时，绑定/漂移两类不参与判定（宁可不报，也不假报「全都未绑表」） */
  const canJudgeBind = !loading && !error;

  const objKeys = useMemo(
    () => new Set(objects.map((o) => String(o.key ?? '').toLowerCase()).filter(Boolean)),
    [objects]);

  const groups = useMemo<HealthGroup[]>(() => {
    const out: HealthGroup[] = [];

    // 1) doc 缺 key 的对象：不依赖绑定层，且发布前必须修（画布上看不见也定位不了）
    if (issues.length) {
      out.push({
        key: 'nokey',
        sev: 'block',
        title: t('k_93ca73e0fe', { p0: issues.length }),
        items: issues.map((e, i) => ({
          id: `nokey:${e.index ?? i}`,
          label: e.label || `#${e.index ?? i}`,
          hint: e.hint ? `${e.hint} · ${t('k_b7b6c6b570')}` : t('k_b7b6c6b570'),
        })),
      });
    }

    // 2) 未绑物理表且不走路由的对象：不可取数。
    //   草案新增对象还没发布，未绑表是预期（发布级联会登记），单列一组 warn，
    //   不与真缺口混在一起 —— 否则会让人去追一个“等发布就好”的问题。
    if (canJudgeBind) {
      const unbound = objects.filter((o) => {
        if (o.route?.source_ontology) return false;
        const c = contractOf(o.key);
        // 系统本体内置对象（平台元数据表）不适用业务绑定口径：不列为「未绑表」缺口
        if (c?.binding_source === 'platform') return false;
        return !c?.physical_table;
      });
      const hard = unbound.filter((o) => draftStates?.[String(o.key).toLowerCase()]?.state !== 'added');
      const soft = unbound.filter((o) => draftStates?.[String(o.key).toLowerCase()]?.state === 'added');
      if (hard.length) {
        out.push({
          key: 'unbound',
          sev: 'block',
          title: t('k_5b3967db70', { p0: hard.length }),
          items: hard.map((o) => ({
            id: `unbound:${o.key}`,
            label: o.display_name || o.key,
            hint: `${t('k_854d1c06c9')} · ${modelKind === 'source' ? t('k_7ffd848033') : t('k_da0a0a64d0')}`,
            objectKey: o.key,
            tab: 'overview',
          })),
        });
      }
      if (soft.length) {
        out.push({
          key: 'unbound-draft',
          sev: 'warn',
          title: t('k_a1b90bf909', { p0: soft.length }),
          items: soft.map((o) => ({
            id: `unbound-draft:${o.key}`,
            label: o.display_name || o.key,
            hint: t('k_a95087fd16'),
            objectKey: o.key,
            tab: 'overview',
          })),
        });
      }

      // 3) 列漂移：列不存在（定义要改）与列元数据未同步（要先同步）分开——不是同一件事
      const badItems: HealthItem[] = [];
      const unverItems: HealthItem[] = [];
      for (const o of objects) {
        for (const d of driftOf(o.key)) {
          const item: HealthItem = {
            id: `${d.verdict === 'unverified' ? 'unver' : 'drift'}:${o.key}:${d.property}`,
            label: `${o.display_name || o.key} · ${d.property}`,
            hint: d.verdict === 'unverified'
              ? t('k_7eb22f2e8c', { p0: d.column })
              : t('k_863abb4a59', { p0: d.column, p1: d.table }),
            objectKey: o.key,
            tab: 'props',
          };
          (d.verdict === 'unverified' ? unverItems : badItems).push(item);
        }
      }
      if (badItems.length) {
        out.push({ key: 'drift', sev: 'block', title: t('k_1bdca5d787', { p0: badItems.length }), items: badItems });
      }
      if (unverItems.length) {
        out.push({ key: 'unver', sev: 'warn', title: t('k_0ccaf6f31f', { p0: unverItems.length }), items: unverItems });
      }
    }

    // 4) 孤儿关系：目标对象不在本模型 —— 激活校验会拒绝（告警文案 k_286d6ce2dd 已这么写）
    const orphanItems: HealthItem[] = [];
    for (const o of objects) {
      for (const l of (o.links || [])) {
        if (!l.target || objKeys.has(String(l.target).toLowerCase())) continue;
        orphanItems.push({
          id: `orphan:${o.key}:${l.type || ''}:${l.target}`,
          label: `${o.display_name || o.key} → ${l.target}`,
          hint: t('k_9af4ad27a9'),
          objectKey: o.key,
          tab: 'links',
        });
      }
    }
    if (orphanItems.length) {
      out.push({ key: 'orphan', sev: 'block', title: t('k_944963433c', { p0: orphanItems.length }), items: orphanItems });
    }

    return out;
  }, [issues, objects, canJudgeBind, draftStates, objKeys, modelKind, rows, drift]); // eslint-disable-line react-hooks/exhaustive-deps

  const total = groups.reduce((n, g) => n + g.items.length, 0);
  const blockCount = groups.filter((g) => g.sev === 'block').reduce((n, g) => n + g.items.length, 0);

  useEffect(() => { onSummary?.(total); }, [total]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 p-1">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline" className={`h-5 px-1.5 text-[11px] ${total ? 'text-amber-600 border-amber-500/40' : 'text-green-600 border-green-500/40'}`}>
          {t('k_9706ceac00', { p0: total })}
        </Badge>
        {blockCount > 0 && (
          <Badge variant="outline" className="h-5 px-1.5 text-[11px] text-red-600 border-red-500/40">
            {t('k_6461b6151c', { p0: blockCount })}
          </Badge>
        )}
        <span className="text-[11px] text-muted-foreground">{t('k_edc412f4d0')}</span>
        <span className="ml-auto" />
        <Button size="sm" variant="ghost" className="h-7 px-2 text-[11px]"
          onClick={() => setNonce((n) => n + 1)}>
          <RotateCw className="h-3 w-3 mr-1" />{t('k_38108eaa1d')}
        </Button>
      </div>

      {viewMode === 'draft' && (
        <div className="rounded-md border border-dashed px-2 py-1.5 text-[11px] text-muted-foreground">
          {t('k_0027575e20')}
        </div>
      )}

      {loading && <div className="px-1 text-xs text-muted-foreground">{t('k_300ee3dee4')}</div>}

      {!!error && (
        <div className="rounded-md border border-red-500/30 bg-red-500/5 px-2 py-1.5 text-[11px] text-red-600">
          <AlertTriangle className="h-3 w-3 mr-1 inline" />
          {t('k_a42b175b4e', { p0: error })}
          <button className="ml-2 underline underline-offset-2" onClick={() => setNonce((n) => n + 1)}>
            {t('k_e2d53a6d3a')}
          </button>
        </div>
      )}

      <div className="min-h-0 flex-1 space-y-2 overflow-auto">
        {groups.map((g) => (
          <div key={g.key} className="rounded-lg border">
            <div className="flex flex-wrap items-center gap-2 border-b px-2.5 py-1.5">
              <span className="text-xs font-medium">{g.title}</span>
              {g.sev === 'block' ? (
                <Badge variant="outline" className="h-4 px-1 text-[10px] text-red-600 border-red-500/40">
                  {t('k_8132462a71')}
                </Badge>
              ) : (
                <Badge variant="outline" className="h-4 px-1 text-[10px] text-amber-600 border-amber-500/40">
                  {t('k_7f81476a9d', { p0: g.items.length })}
                </Badge>
              )}
            </div>
            {g.items.map((it) => (
              <div key={it.id} className="flex items-start gap-2 border-b px-2.5 py-1.5 text-xs last:border-0">
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{it.label}</div>
                  {it.hint && <div className="mt-0.5 text-[11px] text-muted-foreground">{it.hint}</div>}
                </div>
                {it.objectKey && onLocate ? (
                  <Button size="sm" variant="outline" className="h-6 shrink-0 px-2 text-[11px]"
                    onClick={() => onLocate(it.objectKey!, it.tab)}>
                    {t('k_a10fa763d2')}
                  </Button>
                ) : (
                  <span className="shrink-0 text-[11px] text-muted-foreground">—</span>
                )}
              </div>
            ))}
          </div>
        ))}

        {!loading && !error && !total && (
          <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
            <CheckCircle2 className="h-4 w-4 text-green-600" />{t('k_be99cd1634')}
          </div>
        )}

        {/* 字典绑定悬空这条没有只读接口可列清单：显式说明未接入，不静默省略也不编造条数 */}
        <div className="flex items-start gap-1.5 rounded-md border border-dashed px-2 py-1.5 text-[11px] text-muted-foreground">
          <Info className="h-3 w-3 mt-0.5 shrink-0" />{t('k_89eb33e3c5')}
        </div>
      </div>
    </div>
  );
}