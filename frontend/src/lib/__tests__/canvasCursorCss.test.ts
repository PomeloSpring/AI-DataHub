import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect } from 'vitest'

/**
 * 画布抓手光标 CSS 契约：globals.css 两条 cursor data URI 必须与工具栏手型按钮同源
 * （lucide Hand / HandGrab 路径，ISC），线稿双层描边=黑轮廓+白本体（用户拍板配色），
 * 24px、热点 12 12、原生关键字兜底。
 * 光标是超长单行 CSS，Grep 易漏报，故用读文件+解码+DOMParser 做结构断言。
 */
const css = readFileSync(resolve(process.cwd(), 'src/styles/globals.css'), 'utf-8')
const matches = [...css.matchAll(/cursor: url\("data:image\/svg\+xml,([^"]+)"\) 12 12, (grab|grabbing);/g)]

/** lucide v1.21.0 hand.mjs / hand-grab.mjs 的首条 path（同源锚点） */
const LUCIDE_HAND_FIRST = 'M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2'
const LUCIDE_GRAB_FIRST = 'M18 11.5V9a2 2 0 0 0-2-2a2 2 0 0 0-2 2v1.4'

function parseSvg(encoded: string): Document {
  return new DOMParser().parseFromString(decodeURIComponent(encoded), 'image/svg+xml')
}

describe('画布抓手光标 CSS 契约', () => {
  it('两条 cursor 定义齐备：grab 与 grabbing 各一，热点 12 12', () => {
    expect(matches.map(m => m[2])).toEqual(['grab', 'grabbing'])
  })

  it('SVG 合法、24px、双层描边=黑轮廓 3.5 + 白本体 1.5，路径数与 lucide 同源', () => {
    const expectPaths = [8, 10] // Hand 4 path × 2 层；HandGrab 5 path × 2 层
    matches.forEach((m, i) => {
      const doc = parseSvg(m[1])
      expect(doc.querySelector('parsererror')).toBeNull()
      const svg = doc.querySelector('svg')!
      expect(svg.getAttribute('width')).toBe('24')
      expect(svg.getAttribute('height')).toBe('24')
      expect(svg.getAttribute('fill')).toBe('none')
      const groups = doc.querySelectorAll('g')
      expect(groups.length).toBe(2)
      expect(groups[0].getAttribute('stroke')).toBe('black')
      expect(groups[0].getAttribute('stroke-width')).toBe('3.5')
      expect(groups[1].getAttribute('stroke')).toBe('white')
      expect(groups[1].getAttribute('stroke-width')).toBe('1.5')
      expect(doc.querySelectorAll('path').length).toBe(expectPaths[i])
      // 两层路径逐条相同（轮廓与本体同形）
      const a = [...groups[0].querySelectorAll('path')].map(p => p.getAttribute('d'))
      const b = [...groups[1].querySelectorAll('path')].map(p => p.getAttribute('d'))
      expect(a).toEqual(b)
    })
  })

  it('手形与工具栏 lucide 图标同源：grab=Hand、grabbing=HandGrab', () => {
    const grab = parseSvg(matches[0][1])
    const grabbing = parseSvg(matches[1][1])
    expect(grab.querySelectorAll('g')[1].querySelector('path')!.getAttribute('d')).toBe(LUCIDE_HAND_FIRST)
    expect(grabbing.querySelectorAll('g')[1].querySelector('path')!.getAttribute('d')).toBe(LUCIDE_GRAB_FIRST)
  })
})