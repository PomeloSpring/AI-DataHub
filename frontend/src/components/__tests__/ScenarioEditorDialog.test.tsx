/**
 * 共享场景编辑器（从 `AnalysisScenarios` 抽出 + 进化中心「追加到已有」复用）。
 *
 * 锁四件事：
 * 1. **追加不覆盖**：正文拼在原正文之后、触发词取并集，其余配置沿用原场景
 *    —— 进化中心处置「这类分析该怎么做」的提议时，最怕把既有场景正文整篇换掉；
 * 2. **追加必须有目标**：没选场景时提交按钮是死的，否则"追加"会静默变成新建，
 *    提议落到了一个和前文无关的新场景上；
 * 3. **主体范围必须显式声明**：未选主体又没勾「不限主体」时前端先拦，不让人白点一次；
 * 4. **编辑保真**：只改正文再保存，主体/启用/优先级不得被重置成默认值。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const { mockClient } = vi.hoisted(() => ({
  mockClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))
vi.mock('@/api/client', () => ({ default: mockClient }))
vi.mock('@/lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

import ScenarioEditorDialog, {
  splitTriggers, dedupeSubjects, type ScenarioFormOptions, type ScenarioItem,
} from '../ScenarioEditorDialog'
import { toast } from '@/lib/toast'

const ROW: ScenarioItem = {
  id: 1001,
  title: '官网 Web 端体验与流量分析',
  content: '## 适用问题\n这段原文只该出现在编辑弹窗里。',
  metadata: { triggers: ['官网', 'UV'], scenario: '官网流量', subjects: ['rum_event'], subject_all: false },
  related_tables: 'rum_event',
  priority: 60,
  is_active: 1,
}

const OPTIONS: ScenarioFormOptions = {
  subjects: [{ key: 'rum_event', name: 'Web 端 RUM 观测事件', model_name: 'ARMS 本体', kind: 'business' }],
  subjectsError: '',
  reloadSubjects: vi.fn(),
  reportTemplates: [],
  reportTemplatesError: '',
  reloadReportTemplates: vi.fn(),
}

beforeEach(() => {
  vi.clearAllMocks()
  mockClient.get.mockResolvedValue({ data: { items: [ROW], total: 1, page: 1, size: 200 } })
  mockClient.put.mockResolvedValue({ data: { id: ROW.id } })
  mockClient.post.mockResolvedValue({ data: { id: 2002 } })
})
afterEach(() => cleanup())

/** Radix Select：先点开触发器，再点选项（不能直接 fireEvent.change）。 */
async function pickAppendTarget(name: string) {
  await userEvent.click(screen.getByRole('combobox', { name: '选择要追加到的场景 *' }))
  fireEvent.click(await screen.findByRole('option', { name }))
}

describe('场景编辑器 — 追加到已有场景', () => {
  it('正文拼在原正文之后、触发词取并集，其余配置沿用原场景', async () => {
    render(
      <ScenarioEditorDialog
        open
        onOpenChange={vi.fn()}
        options={OPTIONS}
        appendMode
        prefill={{ title: '返利口径', content: '返利按确认收货月份计提。', triggers: ['返利', 'UV'] }}
      />,
    )

    await pickAppendTarget(ROW.title)
    await userEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(mockClient.put).toHaveBeenCalledTimes(1))
    const [url, payload] = mockClient.put.mock.calls[0] as [string, any]
    expect(url).toBe(`/admin/knowledge/${ROW.id}`)
    // 原文在前、提议在后：人读起来是"补了一段"，不是"换了一篇"
    expect(payload.content).toBe(`${ROW.content}\n\n返利按确认收货月份计提。`)
    // 并集去重（UV 已在原触发词里，只多进"返利"）
    expect(payload.metadata.triggers).toEqual(['官网', 'UV', '返利'])
    // 其余配置沿用原场景，不因追加被重置
    expect(payload.title).toBe(ROW.title)
    expect(payload.priority).toBe(60)
    expect(payload.is_active).toBe(1)
    expect(payload.metadata.subjects).toEqual(['rum_event'])
    expect(payload.metadata.subject_all).toBe(false)
    expect(toast.success).toHaveBeenCalledWith('已追加到所选场景')
    // 追加走 PUT，不得悄悄新建
    expect(mockClient.post).not.toHaveBeenCalled()
  })

  it('没选目标场景时保存是死的：说明为什么会拼到"未选"的场景上', async () => {
    render(
      <ScenarioEditorDialog
        open onOpenChange={vi.fn()} options={OPTIONS} appendMode
        prefill={{ content: '补一段', triggers: ['返利'] }}
      />,
    )

    expect(await screen.findByText(/正文会拼到所选场景正文之后/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
  })

  it('场景清单拉失败时显式报错并可重试，不当成"没有场景"', async () => {
    mockClient.get.mockRejectedValue({
      response: { status: 502, data: { detail: '场景清单读取失败：timeout' } },
    })
    render(
      <ScenarioEditorDialog
        open onOpenChange={vi.fn()} options={OPTIONS} appendMode
        prefill={{ content: '补一段' }}
      />,
    )

    expect(await screen.findByText('场景清单读取失败：timeout')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })
})

describe('场景编辑器 — 新建与编辑', () => {
  it('新建：未声明主体就先拦下，不让人白点一次保存', async () => {
    render(
      <ScenarioEditorDialog
        open onOpenChange={vi.fn()} options={OPTIONS}
        prefill={{ title: '返利分析', content: '按月份分组看返利' }}
      />,
    )

    expect(await screen.findByText('提议正文已预填，请检查后再保存')).toBeInTheDocument()
    // 标题/正文由提议预填，只补触发词
    fireEvent.change(screen.getByPlaceholderText('如: 销售额, GMV, 成交金额'),
      { target: { value: '返利' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      '请至少选择一个主体，或勾选「不限主体」——未声明主体的场景对所有角色都不会命中'))
    expect(mockClient.post).not.toHaveBeenCalled()
  })

  it('新建：勾「不限主体」是显式扩权，勾了就能提交', async () => {
    render(
      <ScenarioEditorDialog
        open onOpenChange={vi.fn()} options={OPTIONS}
        prefill={{ title: '返利分析', content: '按月份分组看返利', triggers: ['返利'] }}
      />,
    )

    // 开关与 Label 没有 htmlFor 关联（按文本查不到），第一个开关就是「不限主体」
    fireEvent.click(screen.getAllByRole('switch')[0])
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(mockClient.post).toHaveBeenCalledTimes(1))
    const [, payload] = mockClient.post.mock.calls[0] as [string, any]
    expect(payload.metadata.subject_all).toBe(true)
    expect(payload.metadata.subjects).toEqual([])
    expect(toast.success).toHaveBeenCalledWith('已创建')
  })

  it('编辑：只改正文再保存，主体/启用/优先级保持原值', async () => {
    render(
      <ScenarioEditorDialog open onOpenChange={vi.fn()} options={OPTIONS} item={ROW} />,
    )

    // display value 会被 RTL 归一化（换行变空格），所以用正则取正文框
    fireEvent.change(screen.getByDisplayValue(/这段原文只该出现在编辑弹窗里/),
      { target: { value: `${ROW.content}\n\n补一段口径。` } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(mockClient.put).toHaveBeenCalledTimes(1))
    const [, payload] = mockClient.put.mock.calls[0] as [string, any]
    expect(payload.content).toContain('补一段口径。')
    expect(payload.metadata.subjects).toEqual(['rum_event'])
    expect(payload.metadata.triggers).toEqual(['官网', 'UV'])
    expect(payload.priority).toBe(60)
    expect(payload.is_active).toBe(1)
  })
})

describe('splitTriggers', () => {
  it('中英文逗号都算分隔，去空保留原顺序（去重由追加合并时再做）', () => {
    expect(splitTriggers('返利, GMV，成交,, 返利 ')).toEqual(['返利', 'GMV', '成交', '返利'])
  })
})

describe('dedupeSubjects — 同一对象 key 跨两层本体', () => {
  it('按 key 归并、模型名合并；空 key 丢掉（不收看不到主体的选项）', () => {
    // 真库就有 user/role 同时出现在「联耀业务本体」与「AS-BOT 系统能力本体」
    const out = dedupeSubjects([
      { key: 'user', name: 'User', model_name: '联耀业务本体', kind: 'business' },
      { key: 'role', name: 'Role', model_name: '联耀业务本体', kind: 'business' },
      { key: 'user', name: '用户', model_name: 'AS-BOT 系统能力本体', kind: 'system' },
      { key: 'role', name: '角色', model_name: 'AS-BOT 系统能力本体', kind: 'system' },
      { key: '', name: '无 key', model_name: '某个本体', kind: 'business' },
    ])

    // 撞车的 React key 会让徽标错位，也会让人在两个等价项之间瞎猜
    expect(out.map(s => s.key)).toEqual(['user', 'role'])
    expect(new Set(out.map(s => s.key)).size).toBe(out.length)
    expect(out[0].model_name).toBe('联耀业务本体 / AS-BOT 系统能力本体')
  })
})