/**
 * 模型能力标签 —— 从已有列**派生**，不另存一份标签真值源。
 *
 * 为什么要派生而不是新列存标签：多模态/思考/单价本来就各有列（supports_vision、
 * supports_thinking、input_price_per_1k…），再存一份 tags 就是同一概念两处落地，
 * 改一处漏一处（历史缺陷：capability_rank 空单元格就是因为前端把"读不到"当"没值"渲染成空白）。
 *
 * 口径：
 *  - 能力评级由 capability_rank 映射（1=最强）；未定档（>90 或缺失）**如实显示**，
 *    不猜一个档位糊过去。
 *  - 成本只报实际登记的单价；没登记就写「未定价」，绝不按模型名猜价格。
 */

export interface LlmModelTagSource {
  capability_rank?: number | null;
  supports_thinking?: number | boolean | null;
  supports_vision?: number | boolean | null;
  input_price_per_1k?: number | string | null;
  output_price_per_1k?: number | string | null;
  currency?: string | null;
}

export type ModelTagTone = 'grade' | 'vision' | 'thinking' | 'cost' | 'unknown';

export interface ModelTag {
  key: string;
  label: string;
  tone: ModelTagTone;
  title: string;
}

/** 未定档阈值：迁移里默认值就是 99，>90 一律视为"没定过"（不是"最弱"）。 */
const UNRANKED = 90;

/**
 * 标签配色一律走主题变量（与 UI 美化主题变量规范同口径，禁裸 hex）：
 * 评级用主色，能力面用图表色板（同一主题内彼此区分且不靠单一色相传达——旁边都有文字标签）。
 */
export const MODEL_TAG_TONE_CLASS: Record<ModelTagTone, string> = {
  grade: 'border-primary/40 bg-primary/10 text-primary font-medium',
  vision: 'border-chart-4/50 bg-chart-4/10 text-foreground',
  thinking: 'border-chart-2/50 bg-chart-2/10 text-foreground',
  cost: 'border-chart-3/50 bg-chart-3/10 text-foreground',
  unknown: 'border-border bg-muted/60 text-muted-foreground',
};

const truthy = (v: unknown) => v === 1 || v === true || v === '1';

const num = (v: unknown): number | null => {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

/** 能力评级：S 旗舰 / A 强 / B 均衡 / C 轻快；未定档如实回「未定档」。 */
export function capabilityGrade(rank?: number | null): { label: string; ranked: boolean } {
  const n = num(rank);
  if (n === null || n > UNRANKED) return { label: '未定档', ranked: false };
  if (n <= 2) return { label: 'S 级', ranked: true };
  if (n <= 5) return { label: 'A 级', ranked: true };
  if (n <= 8) return { label: 'B 级', ranked: true };
  return { label: 'C 级', ranked: true };
}

/** 成本文案：有单价就报单价（每 1K token），没登记就说没登记。 */
export function costLabel(row: LlmModelTagSource): { label: string; priced: boolean } {
  const inp = num(row.input_price_per_1k);
  const out = num(row.output_price_per_1k);
  if (inp === null && out === null) return { label: '未定价', priced: false };
  const cur = row.currency || 'USD';
  const parts: string[] = [];
  if (inp !== null) parts.push(`入 ${inp}`);
  if (out !== null) parts.push(`出 ${out}`);
  return { label: `${cur} ${parts.join(' / ')} · 每 1K tok`, priced: true };
}

/** 一行模型的标签清单（顺序固定：评级 → 多模态 → 思考 → 成本）。 */
export function modelCapabilityTags(row: LlmModelTagSource): ModelTag[] {
  const tags: ModelTag[] = [];
  const grade = capabilityGrade(row.capability_rank);
  tags.push({
    key: 'grade',
    label: grade.label,
    tone: grade.ranked ? 'grade' : 'unknown',
    title: grade.ranked
      ? `能力序 ${row.capability_rank}（1=最强，数字越大越弱/越省）派生的评级`
      : '能力序未登记：按未定档沉底显示，请在「编辑」里填能力序，不要凭名字猜档位',
  });
  tags.push({
    key: 'vision',
    label: truthy(row.supports_vision) ? '多模态' : '纯文本',
    tone: truthy(row.supports_vision) ? 'vision' : 'unknown',
    title: truthy(row.supports_vision)
      ? '支持图片输入（supports_vision）'
      : '不接受图片输入：上传截图后选它会答非所问',
  });
  tags.push({
    key: 'thinking',
    label: truthy(row.supports_thinking) ? '思考链' : '直答',
    tone: truthy(row.supports_thinking) ? 'thinking' : 'unknown',
    title: truthy(row.supports_thinking)
      ? '会先输出推理再作答（supports_thinking）'
      : '不输出推理段（直答），时延更低',
  });
  const cost = costLabel(row);
  tags.push({
    key: 'cost',
    label: cost.label,
    tone: cost.priced ? 'cost' : 'unknown',
    title: cost.priced
      ? '模型中心登记的单价（用于成本估算）'
      : '未登记单价：成本页里这类模型只能按通道口径估算，请在「编辑」里补价格',
  });
  return tags;
}
