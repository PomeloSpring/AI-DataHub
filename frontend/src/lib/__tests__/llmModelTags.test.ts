// 模型能力标签的派生口径（模型中心那一列标签的唯一实现）。
//
// 两条硬要求：
//  1. 标签只从已登记的列派生（capability_rank / supports_vision / supports_thinking / 单价），
//     不另存一份 tags —— 否则改一处漏一处；
//  2. **读不到就如实说读不到**：capability_rank 缺失（旧后端未返回该列）必须显示「未定档」，
//     不能渲成空白单元格（历史缺陷：空白被读成"这模型没档位"而不是"服务端没给数据"）。
import { describe, it, expect } from 'vitest'
import { capabilityGrade, costLabel, modelCapabilityTags, MODEL_TAG_TONE_CLASS } from '../llmModelTags'

describe('能力评级（由 capability_rank 派生）', () => {
  it('档位映射：1-2=S，3-5=A，6-8=B，9+=C', () => {
    expect(capabilityGrade(1).label).toBe('S 级')
    expect(capabilityGrade(2).label).toBe('S 级')
    expect(capabilityGrade(3).label).toBe('A 级')
    expect(capabilityGrade(5).label).toBe('A 级')
    expect(capabilityGrade(6).label).toBe('B 级')
    expect(capabilityGrade(8).label).toBe('B 级')
    expect(capabilityGrade(9).label).toBe('C 级')
  })

  it('未定档（99 / 缺失 / 空串）不猜档位', () => {
    for (const v of [99, undefined, null, '']) {
      expect(capabilityGrade(v as any)).toEqual({ label: '未定档', ranked: false })
    }
  })
})

describe('成本文案', () => {
  it('没登记单价就写未定价，绝不按模型名猜价', () => {
    expect(costLabel({})).toEqual({ label: '未定价', priced: false })
  })
  it('登记了就报实际单价与币种', () => {
    const r = costLabel({ input_price_per_1k: 0.0024, output_price_per_1k: 0.0096, currency: 'CNY' })
    expect(r.priced).toBe(true)
    expect(r.label).toContain('CNY')
    expect(r.label).toContain('入 0.0024')
    expect(r.label).toContain('出 0.0096')
  })
})

describe('标签清单', () => {
  it('顺序固定：评级 → 多模态 → 思考 → 成本；缺失列如实标 unknown 档', () => {
    const tags = modelCapabilityTags({})
    expect(tags.map(t => t.key)).toEqual(['grade', 'vision', 'thinking', 'cost'])
    // 全空数据时四个标签都不得装作"已确认的能力"
    expect(tags.every(t => t.tone === 'unknown')).toBe(true)
    expect(tags.map(t => t.label)).toEqual(['未定档', '纯文本', '直答', '未定价'])
  })

  it('实测值优先于默认值：0 一律是"不支持"，不是"没数据"', () => {
    const tags = modelCapabilityTags({ capability_rank: 4, supports_vision: 1, supports_thinking: 0 })
    const byKey = Object.fromEntries(tags.map(t => [t.key, t]))
    expect(byKey.grade.label).toBe('A 级')
    expect(byKey.vision.label).toBe('多模态')
    expect(byKey.thinking.label).toBe('直答')
  })

  it('每个标签都带 title（鼠标停上去要说清依据与下一步）', () => {
    for (const t of modelCapabilityTags({})) {
      expect(t.title.length).toBeGreaterThan(8)
    }
  })

  it('配色只用主题变量，不得出现裸 hex（UI 主题变量规范）', () => {
    const all = Object.values(MODEL_TAG_TONE_CLASS).join(' ')
    expect(all).not.toMatch(/#[0-9a-fA-F]{3,8}/)
    expect(all).toMatch(/text-primary/)
    expect(all).toMatch(/bg-muted/)
  })
})
