import { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import DecisionAdvicePanel from '@/components/decision/DecisionAdvicePanel';
import { decisionsApi, type DecisionOutcomeView, type DecisionParamBinding } from '@/api/decisions';

/**
 * 看板「决策建议」控件（计划 §四出口 4）。
 *
 * 三条纪律：
 * 1. **不自己判定**：结论只来自服务端引擎（`/query/trees/{key}/evaluate`），本组件
 *    不比较数值、不挑建议、不缓存判断（与 Chat 卡片/决策中心同一个投影口）；
 * 2. **不新建参数通道**：看板筛选只把「参数原值 + 绑定清单」交给服务端，由
 *    `semantics.param_plan` 翻译进 intent（前端不拼 filters）；
 * 3. **样式来自 `buildG2Spec('widget_decision')`**：`widget_style` 旋钮（背景/文字/
 *    边框/圆角/内边距/字号/控件高）在 DashboardChart 里解析一次，这里只消费解析
 *    结果，不硬编码色值 —— 否则字模与主题包挂了没反应（ui-vis-library-style §2）。
 */

export interface DecisionWidgetChrome {
  background: string;
  color: string;
  borderColor: string;
  borderWidth: number;
  borderStyle: string;
  borderRadius: number;
  padding: string | number;
  fontSize: number;
  height?: number;
}

/** 控件参数变化到这个值才重新求值：日期区间控件拖动期间会连续改值，别打爆库 */
const REFRESH_DEBOUNCE_MS = 400;

export default function DecisionWidget({
  chrome, treeKey, paramBindings, params, workspaceId, draft,
}: {
  chrome: DecisionWidgetChrome;
  treeKey: string;
  paramBindings: DecisionParamBinding[];
  params: Record<string, any>;
  workspaceId: number;
  draft?: boolean;
}) {
  const [outcome, setOutcome] = useState<DecisionOutcomeView | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);

  // 只送绑定清单里出现的参数键：别的图的参数不该影响这棵树（也不该多打一次库）
  const relevantParams = useMemo(() => {
    const picked: Record<string, any> = {};
    (paramBindings || []).forEach(b => {
      const key = String(b?.key || '');
      if (key) picked[key] = params?.[key];
    });
    return picked;
  }, [paramBindings, params]);
  // 请求只跟**内容**走（序列化后的筛选值/绑定/树/空间）：父级重绘会递来新的
  // 数组/对象字面量，拿引用当依赖会让看板每次刷新都重打一次库。
  const request = JSON.stringify({
    treeKey, workspaceId, params: relevantParams,
    bindings: (paramBindings || []).map(b => ({ key: b?.key, slot: b?.slot, dimension: b?.dimension || '' })),
  });
  const latest = useRef({ relevantParams, paramBindings });
  latest.current = { relevantParams, paramBindings };

  useEffect(() => {
    if (!treeKey) {
      setOutcome(null); setError(''); setLoading(false);
      return;
    }
    const id = ++seq.current;
    setLoading(true);
    setError('');
    const { relevantParams: values, paramBindings: bindings } = latest.current;
    const timer = setTimeout(() => {
      decisionsApi.evaluateOne(treeKey, workspaceId, '', {
        params: values, paramBindings: bindings || [],
      }).then(({ data }) => {
        if (seq.current !== id) return;   // 迟到的旧结果不能覆盖新筛选的结论
        setOutcome(data);
        setLoading(false);
      }).catch((err: any) => {
        if (seq.current !== id) return;
        setOutcome(null);
        setLoading(false);
        const status = err?.response?.status;
        const detail = err?.response?.data?.detail;
        const text = typeof detail === 'string' ? detail : (detail ? JSON.stringify(detail) : '');
        // 无权限要说得出来是权限问题（不是"没有建议"），否则人会把空卡当"没有风险"
        setError(status === 403
          ? `当前角色不可见决策建议${text ? `：${text}` : ''}`
          : `评估未完成${text ? `：${text}` : `（${err?.message || '请求失败'}）`}`);
      });
    }, draft ? 0 : REFRESH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [treeKey, workspaceId, request, draft]);

  const boxStyle: React.CSSProperties = {
    position: 'absolute', inset: 0, overflow: 'auto', boxSizing: 'border-box',
    display: 'flex', flexDirection: 'column', gap: 6,
    background: chrome.background,
    color: chrome.color,
    border: `${chrome.borderWidth}px ${chrome.borderStyle} ${chrome.borderColor}`,
    borderRadius: chrome.borderRadius,
    padding: chrome.padding,
    fontSize: chrome.fontSize,
    ...(chrome.height ? { height: chrome.height, maxHeight: '100%', margin: 'auto 0' } : {}),
  };

  if (!treeKey) {
    return (
      <div style={boxStyle} className="dh-decision-widget" role="status">
        未选择决策树：在属性面板里指定要展示哪棵树（控件只展示服务端算好的结论，不自己判定）。
      </div>
    );
  }

  return (
    <div style={boxStyle} className="dh-decision-widget">
      {loading && !outcome && (
        <span className="flex items-center gap-2 text-xs" style={{ color: chrome.color }}>
          <Loader2 className="h-4 w-4 animate-spin" /> 按当前筛选评估中…
        </span>
      )}
      {!!error && !loading && (
        <span role="alert" className="text-xs">{error}</span>
      )}
      {outcome && (
        <DecisionAdvicePanel advice={outcome} />
      )}
    </div>
  );
}
