// 「用最新数据重新生成」的两类结局回显：版式（layout_result）与证据（evidence_summary）。
// 关键约束（no-silent-degradation §1）：没沿用原版式要给原因与出路，证据口径要按服务端判定说话，
// 不得再把「尚未完成证据校验、不可对外发布」当常量印在所有报告上（那是全仓写死的假降级）。
import { describe, it, expect } from 'vitest'
import { evidenceNote, layoutBadge, layoutNote } from '../reportLayout'

describe('版式回显文案', () => {
  it('沿用原版式：无徽章、无警告', () => {
    const preserved = { has_layout: true, status: 'preserved', format: 'markdown', reasons: [] }
    expect(layoutBadge(preserved)).toBe('')
    expect(layoutNote(preserved)).toBe('')
  })

  it('未沿用原版式：徽章 + 带原因的说明（原因一条来自服务端就印一条）', () => {
    const deviated = {
      has_layout: true, status: 'fact_only', format: 'markdown',
      reasons: ['缺少图表：china_map（省份/页面浏览量）', '缺少章节：核心发现'],
    }
    expect(layoutBadge(deviated)).toBe('未按原版式')
    const note = layoutNote(deviated)
    expect(note).toContain('未能沿用上一版报告的版式')
    expect(note).toContain('china_map')
    expect(note).toContain('缺少章节：核心发现')
    expect(note).toContain('切回上一版')
  })

  it('版式未达成（新口径 rejected）：徽章 + 判失败，不得说“样式退回平台事实报告”', () => {
    const rejected = {
      has_layout: true, status: 'rejected', format: 'html',
      reasons: ['正文不是整份 HTML 文档（上一版是 HTML 报告）'],
    }
    expect(layoutBadge(rejected)).toBe('未按原版式')
    const note = layoutNote(rejected)
    expect(note).toContain('已按失败处理')
    // 那句是旧降级口径的话：平台不再擅自改产事实表格，页面也不得再说它长在那
    expect(note).not.toContain('退回平台事实报告')
    expect(note).toContain('切回上一版')
  })

  it('没有版式信息（旧服务端/非重跑产物）不编造结论', () => {
    expect(layoutBadge(undefined)).toBe('')
    expect(layoutNote(undefined)).toBe('')
    // 有原因字段但状态不是 fact_only → 不外露半句话（避免"没事也报警"）
    expect(layoutNote({ status: '', reasons: ['x'] })).toBe('')
  })

  it('原因为空也要成句（服务端只标状态时不留冒号尾巴）', () => {
    expect(layoutNote({ status: 'fact_only', reasons: [] })).not.toMatch(/：\s*。/)
  })
})

describe('证据口径回显', () => {
  it('ready 且未截断：不打扰（证据成立是默认预期，不该占一行）', () => {
    expect(evidenceNote({
      validation_status: 'governed', query_count: 3, sources: ['run_semantic_query'],
      completeness: 'as_queried', reasons: [],
    })).toBe('')
  })

  it('结果被截断：说清是样本、并带上已鉴权取数条数', () => {
    const note = evidenceNote({
      validation_status: 'governed', query_count: 2, completeness: 'sample_only', reasons: [],
    })
    expect(note).toContain('2 条已鉴权取数')
    expect(note).toContain('仅代表样本')
    // 那句没人当真的套话不得回来
    expect(note).not.toContain('不可对外发布')
  })

  it('证据不成立：逐条印服务端原因，不替用户猜', () => {
    const note = evidenceNote({
      validation_status: 'unverified', reasons: ['正文为空（模型未交稿且无取数结果可落）'],
    })
    expect(note).toContain('证据不完整')
    expect(note).toContain('正文为空')
  })

  it('旧服务端没下发证据摘要：不编造结论', () => {
    expect(evidenceNote(undefined)).toBe('')
    expect(evidenceNote({})).toBe('')
  })
})
