import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect } from 'vitest'

/**
 * 深色主色底上的文字选中态契约（globals.css 唯一口径）。
 *
 * 用户反馈：Chat 用户气泡（bg-primary + text-primary-foreground）里框选文字，
 * 浏览器默认的半透明蓝压在主题主色上看不出来，选中了哪一段无从确认。
 * 口径：拿这块面自己的墨色当**半透明薄纱**垫底、文字颜色不变 —— 只变底不翻转字色。
 * 上一版是不透明反色块，被反馈“太突兀”，所以 alpha 必须 < 1，不得改回实心。
 * 且**只允许这一处**定义：各气泡（Chat / 待发送问题 / AS-BOT 面板）靠工具类组合
 * 自动命中，组件里再各写一套 `selection:` 就会口径分裂。本文件钉住这两条。
 */
const css = readFileSync(resolve(process.cwd(), 'src/styles/globals.css'), 'utf-8')

/** 取规则块正文（第一个 `}` 之前） */
const blockOf = (selector: string): string => {
  const at = css.indexOf(selector + ' {')
  return at < 0 ? '' : css.slice(at, css.indexOf('}', at))
}

describe('深色主色底的选中态样式契约', () => {
  it('命中面齐备：元素自身与其后代的 ::selection 都定义（气泡内嵌徽章/附件同样生效）', () => {
    expect(css).toContain('.bg-primary.text-primary-foreground::selection')
    expect(css).toContain('.bg-primary.text-primary-foreground ::selection')
  })

  it('选中底是主题墨色的半透明薄纱（alpha 必须 < 1），文字颜色不翻转', () => {
    const body = blockOf('.bg-primary.text-primary-foreground ::selection')
    const veil = body.match(/background-color:\s*hsl\(var\(--primary-foreground\)\s*\/\s*([\d.]+)\)/)
    expect(veil, '选中底必须走主题墨色变量并带 alpha').not.toBeNull()
    expect(Number(veil![1])).toBeGreaterThan(0)
    // 不透明 = 硬贴一块反色块（已被反馈“太突兀”），不得回到那一版
    expect(Number(veil![1]), '选中底必须半透明，不得改回实心反色块').toBeLessThan(1)
    // 字色保持这块面的墨色：只变底，不翻转字色
    expect(body).toContain('color: hsl(var(--primary-foreground))')
    expect(body).not.toMatch(/#[0-9a-f]{3,8}\b/i)
    expect(body).not.toMatch(/\brgba?\(/)
  })

  it('唯一实现处：组件里不得另写 selection 变体/::selection（防第二套真值源）', () => {
    for (const rel of ['src/pages/Chat.tsx', 'src/components/asbot/AsBotPanel.tsx']) {
      const src = readFileSync(resolve(process.cwd(), rel), 'utf-8')
      expect(src, `${rel} 不应自带选中态样式`).not.toMatch(/::selection|selection:[a-z-]/)
    }
  })
})
