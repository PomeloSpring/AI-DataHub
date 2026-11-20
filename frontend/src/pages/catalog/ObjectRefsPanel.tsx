/**
 * 对象检查器「指标」/「维度」页签 —— 该对象挂接的口径清单（就地可编辑）。
 *
 * 口径的**唯一权威仍在字典行**（`adh_metrics` / `adh_dimensions`，本体文档只表达引用关系，
 * 见 ontology-modeling §3）；但这不等于“字典只能在指标中心页改”：建模时最高频的动作就是
 * 「看看这个对象挂了哪些口径 / 就地补一条、改个别名」。为此逼人跳去另一个菜单再自己
 * 重新筛出这个对象，纯属绕路，且容易改错行。
 *
 * 因此本面板**复用字典的同一套编辑弹窗与同一写接口**（MetricFormDialog /
 * EditDimensionDialog / CreateDimensionDialog），保存后立即重拉：
 * 不存在第二套写入口，也没有“这里改了不生效”的副本。
 *
 * `section` 只有一个值（指标 or 维度）：二者在检查器里是**两个页签**，不在一列里上下堆叠 ——
 * 「这个对象挂了哪些指标」「挂了哪些维度」是两次独立判断，合成一栏只是把两件事挤成一次滚动。
 * 按段懒加载：切到哪个页签才请求哪一段。
 *
 * 加载失败必须分段显式暴露（no-silent-degradation）：不得把失败伪装成“这个对象没有挂口径”。
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, BarChart3, CheckCircle2, Layers, Pencil, Plus, RefreshCw } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { metricsApi } from '@/api/metrics';
import { aggLabel } from '@/lib/dictLabels';
import { useTranslation } from 'react-i18next';
import MetricFormDialog, { type MetricRecord } from './MetricFormDialog';
import {
  CreateDimensionDialog,
  EditDimensionDialog,
  type DictDimension,
} from './DimensionsDictTab';

/** 页签种类：指标 / 维度（各占一个检查器页签） */
export type ObjectRefSection = 'metrics' | 'dimensions';

/** 单段加载状态 */
interface LoadState<T> {
  rows: T[];
  total: number;
  loading: boolean;
  error: string;
}
const emptyState = <T,>(): LoadState<T> => ({ rows: [], total: 0, loading: true, error: '' });

export default function ObjectRefsPanel({
  section,
  objectKey,
  modelId,
  objects = [],
  canEdit = false,
}: {
  /** 本页签展示哪一段口径 */
  section: ObjectRefSection;
  /** 当前对象 key（= 字典行的 bound_object_key） */
  objectKey: string;
  /** 模型 id：编辑/新建弹窗据此限制归属对象候选与口径词表作用域 */
  modelId?: number;
  /** 本模型对象清单：弹窗里的「归属对象」候选（引用选择器，不手填 key） */
  objects?: { key: string; display_name: string }[];
  /** 是否有字典写权限（无权限只读展示，不给假按钮） */
  canEdit?: boolean;
}) {
  const { t } = useTranslation('ui');
  const isMetrics = section === 'metrics';
  const [metrics, setMetrics] = useState<LoadState<MetricRecord>>(emptyState<MetricRecord>());
  const [dims, setDims] = useState<LoadState<DictDimension>>(emptyState<DictDimension>());
  // metric: null = 新建（归属对象预填当前对象）；非 null = 编辑该行
  const [metricForm, setMetricForm] = useState<{ metric: MetricRecord | null } | null>(null);
  const [editDim, setEditDim] = useState<DictDimension | null>(null);
  const [createDim, setCreateDim] = useState(false);

  const loadMetrics = useCallback(async () => {
    setMetrics((s) => ({ ...s, loading: true, error: '' }));
    try {
      // 服务端按 bound_object_key 下推过滤（与指标中心页同一接口）；
      // size=字典页步长的上限，超出一页时显式标注“仅显示前 N 条”，不静默截断。
      const { data } = await metricsApi.list({ bound_object_key: objectKey, size: 200 });
      const rows = Array.isArray(data?.items) ? data.items : [];
      setMetrics({ rows, total: Number(data?.total ?? rows.length), loading: false, error: '' });
    } catch (e: any) {
      const msg = String(e?.response?.data?.detail || e?.message || t('k_974e748456'));
      // 失败要能区分于“确实没有挂指标”：清空列表但保留错因与重试口
      setMetrics({ rows: [], total: 0, loading: false, error: msg });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [objectKey]);

  const loadDims = useCallback(async () => {
    setDims((s) => ({ ...s, loading: true, error: '' }));
    try {
      // is_active='all'：停用行也要看得见（否则“停用了”在建模视角等于“消失了”）
      const { data } = await metricsApi.listAllDimensions({
        bound_object_key: objectKey, is_active: 'all',
      });
      const rows = Array.isArray(data?.items) ? data.items : [];
      setDims({ rows, total: Number(data?.total ?? rows.length), loading: false, error: '' });
    } catch (e: any) {
      const msg = String(e?.response?.data?.detail || e?.message || t('k_974e748456'));
      setDims({ rows: [], total: 0, loading: false, error: msg });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [objectKey]);

  // 按段懒加载：只有当前页签的那一段会请求（另一段不白白占一次往返）
  useEffect(() => {
    setMetricForm(null);
    setEditDim(null);
    setCreateDim(false);
    if (isMetrics) {
      setMetrics(emptyState<MetricRecord>());
      void loadMetrics();
    } else {
      setDims(emptyState<DictDimension>());
      void loadDims();
    }
  }, [isMetrics, loadMetrics, loadDims]);

  const objectNameMap = useMemo(
    () => Object.fromEntries(objects.map((o) => [o.key, o.display_name] as const)),
    [objects]);

  /** 指标行：业务名 + 口径标识（聚合方式/别名）；停用行必须可见 */
  const metricRow = (m: MetricRecord) => (
    <div key={m.id} className="flex items-start gap-2 border-b px-2 py-1.5 last:border-0">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-[13px] font-medium">{m.display_name || m.name}</span>
          {!!m.certified && (
            <span title={t('k_d39325b9c2')}><CheckCircle2 className="h-3 w-3 text-green-500" /></span>
          )}
          {m.is_active === 0 && (
            <Badge variant="outline" className="h-4 shrink-0 px-1 py-0 text-[10px] text-muted-foreground">
              {t('k_6c7dcbb73a')}
            </Badge>
          )}
        </div>
        <div className="truncate text-[11px] text-muted-foreground">
          {m.name} · {aggLabel(m.calculation_type)}
          {m.unit ? ` · ${m.unit}` : ''}
        </div>
      </div>
      {canEdit && (
        <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0"
          aria-label={t('k_82cf6c30af', { p0: m.display_name || m.name })}
          title={t('k_a7f814c0a4')}
          onClick={() => setMetricForm({ metric: m })}>
          <Pencil className="h-3.5 w-3.5" />
        </Button>
      )}
    </div>
  );

  /** 维度行：业务名 + 物理列；枚举/别名等明细留给编辑弹窗（列表要克制） */
  const dimRow = (d: DictDimension) => (
    <div key={d.id} className="flex items-start gap-2 border-b px-2 py-1.5 last:border-0">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-[13px] font-medium">{d.name}</span>
          {d.category && (
            <Badge variant="outline" className="h-4 shrink-0 px-1 py-0 text-[10px]">{d.category}</Badge>
          )}
          {d.is_active === 0 && (
            <Badge variant="outline" className="h-4 shrink-0 px-1 py-0 text-[10px] text-muted-foreground">
              {t('k_6c7dcbb73a')}
            </Badge>
          )}
        </div>
        <div className="truncate font-mono text-[11px] text-muted-foreground"
          title={d.target_table ? `${d.target_table}.${d.target_column}` : ''}>
          {d.target_table && d.target_column
            ? `${d.target_table}.${d.target_column}`
            : t('k_46a9e40651')}
        </div>
      </div>
      {canEdit && (
        <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0"
          aria-label={t('k_8f6c081f66', { p0: d.name })}
          title={t('k_a7f814c0a4')}
          onClick={() => setEditDim(d)}>
          <Pencil className="h-3.5 w-3.5" />
        </Button>
      )}
    </div>
  );

  /** 本段骨架：标题（含计数）+ 新建入口 + 加载中/失败/空态/清单 */
  const renderSection = <T,>(opts: {
    title: string;
    icon: ReactNode;
    state: LoadState<T>;
    onRetry: () => void;
    onCreate?: () => void;
    createLabel?: string;
    empty: string;
    renderRow: (row: T) => ReactNode;
  }) => (
    <section className="space-y-1">
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground">
          {opts.icon}{opts.title}
        </div>
        {canEdit && opts.onCreate && (
          <Button variant="ghost" size="sm" className="ml-auto h-6 px-1.5 text-[11px]"
            onClick={opts.onCreate}>
            <Plus className="mr-1 h-3 w-3" />{opts.createLabel}
          </Button>
        )}
      </div>
      {opts.state.loading ? (
        <div className="rounded-md border px-2 py-2 text-xs text-muted-foreground">{t('k_26b5bd4947')}</div>
      ) : opts.state.error ? (
        <div className="rounded-md border border-red-500/30 bg-red-500/5 px-2 py-1.5 text-[11px] text-red-500">
          <AlertTriangle className="mr-1 inline h-3 w-3" />
          {opts.state.error}
          <Button variant="outline" size="sm" className="ml-2 h-6 px-1.5 text-[11px]"
            onClick={opts.onRetry}>
            <RefreshCw className="mr-1 h-3 w-3" />{t('k_e2d53a6d3a')}
          </Button>
        </div>
      ) : opts.state.rows.length ? (
        <>
          <div className="rounded-md border">{opts.state.rows.map(opts.renderRow)}</div>
          {opts.state.total > opts.state.rows.length && (
            <div className="text-[11px] text-amber-600">
              {t('k_3ce6789930', { p0: opts.state.rows.length, p1: opts.state.total })}
            </div>
          )}
        </>
      ) : (
        <div className="rounded-md border px-2 py-2 text-xs text-muted-foreground">{opts.empty}</div>
      )}
    </section>
  );

  return (
    <div className="space-y-3">
      <p className="text-[11px] leading-relaxed text-muted-foreground">{t('k_47b74896c1')}</p>

      {isMetrics
        ? renderSection({
          title: t('k_96de09bfbe', { p0: metrics.rows.length }),
          icon: <BarChart3 className="h-3.5 w-3.5" />,
          state: metrics,
          onRetry: () => void loadMetrics(),
          onCreate: () => setMetricForm({ metric: null }),
          createLabel: t('k_e4f7015bed'),
          empty: t('k_2d80e162b1'),
          renderRow: metricRow,
        })
        : renderSection({
          title: t('k_1e4c87df1b', { p0: dims.rows.length }),
          icon: <Layers className="h-3.5 w-3.5" />,
          state: dims,
          onRetry: () => void loadDims(),
          onCreate: () => setCreateDim(true),
          createLabel: t('k_3e373012a2'),
          empty: t('k_fa1de5eea3'),
          renderRow: dimRow,
        })}

      <Button asChild size="sm" variant="outline" className="h-7 w-full text-xs">
        <Link to="/data/metrics">{t('k_d97fc9c541')}</Link>
      </Button>

      {/* 口径表单：与指标中心/维度字典页同一份组件与接口，保存后重拉当前段 */}
      {isMetrics && metricForm && (
        <MetricFormDialog
          metric={metricForm.metric}
          modelId={modelId}
          objects={objects}
          defaultObjectKey={objectKey}
          onClose={() => setMetricForm(null)}
          onSaved={() => {
            setMetricForm(null);
            void loadMetrics();
          }}
        />
      )}
      {!isMetrics && editDim && (
        <EditDimensionDialog
          dimension={editDim}
          onClose={() => setEditDim(null)}
          onSaved={() => { setEditDim(null); void loadDims(); }}
        />
      )}
      {!isMetrics && createDim && (
        <CreateDimensionDialog
          objectNames={objectNameMap}
          defaultObjectKey={objectKey}
          onClose={() => setCreateDim(false)}
          onSaved={() => { setCreateDim(false); void loadDims(); }}
        />
      )}
    </div>
  );
}