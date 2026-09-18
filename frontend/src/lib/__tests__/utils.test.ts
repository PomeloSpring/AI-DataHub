import { describe, it, expect, vi } from 'vitest'
import { cn } from '../utils'
import {
  toObservabilityUTC, parseObservabilityTime, observabilityTimeToInput,
  observabilityTimeFromInput, observabilityTimeRange,
} from '@/api/observability'

describe('cn', () => {
  it('merges multiple class names', () => {
    expect(cn('foo', 'bar')).toBe('foo bar')
  })

  it('handles conditional classes', () => {
    expect(cn('foo', false && 'bar', 'baz')).toBe('foo baz')
  })

  it('handles undefined and null', () => {
    expect(cn('foo', undefined, null, 'bar')).toBe('foo bar')
  })

  it('merges tailwind classes correctly (deduplicates)', () => {
    // tailwind-merge should resolve conflicting classes
    expect(cn('p-4', 'p-2')).toBe('p-2')
  })

  it('merges conflicting margin classes', () => {
    expect(cn('m-4', 'm-2')).toBe('m-2')
  })

  it('handles empty input', () => {
    expect(cn()).toBe('')
  })

  it('handles array input', () => {
    expect(cn(['foo', 'bar'])).toBe('foo bar')
  })

  it('handles object input', () => {
    expect(cn({ foo: true, bar: false, baz: true })).toBe('foo baz')
  })

  it('preserves non-conflicting classes', () => {
    expect(cn('text-red-500', 'bg-blue-500', 'p-4')).toBe('text-red-500 bg-blue-500 p-4')
  })
})

describe('可观测时间转换', () => {
  it('将带时区时间转换为元库的 UTC DATETIME，保留毫秒', () => {
    expect(toObservabilityUTC(new Date('2026-09-17T17:39:02.728+08:00')))
      .toBe('2026-09-17 09:39:02.728')
  })

  it.each([
    '2026-09-17 09:39:02.728',
    '2026-09-17T09:39:02.728',
    '2026-09-17T09:39:02.728Z',
    '2026-09-17T17:39:02.728+08:00',
  ])('统一解析数据库或 ISO 时间：%s', value => {
    expect(parseObservabilityTime(value).toISOString()).toBe('2026-09-17T09:39:02.728Z')
  })

  it('本地输入与 UTC 请求时间双向转换，不改变用户选择的时刻', () => {
    const local = '2026-09-17T17:39'
    const utc = observabilityTimeFromInput(local)
    expect(parseObservabilityTime(utc).getTime()).toBe(new Date(2026, 8, 17, 17, 39).getTime())
    expect(observabilityTimeToInput(utc)).toBe(local)
    const date = parseObservabilityTime('2026-09-17 09:39:00.000')
    const input = observabilityTimeToInput('2026-09-17 09:39:00.000')
    expect(new Date(input).getHours()).toBe(date.getHours())
    expect(observabilityTimeFromInput(input)).toBe('2026-09-17 09:39:00.000')
  })

  it.each([1, 3, 24, 168])('近 %s 小时使用 UTC 边界，包含刚采集的记录', hours => {
    const now = new Date('2026-09-17T17:40:00.500+08:00')
    const range = observabilityTimeRange(hours, now)
    expect(range.end).toBe('2026-09-17 09:40:00.500')
    expect(parseObservabilityTime(range.end).getTime() - parseObservabilityTime(range.start).getTime())
      .toBe(hours * 3600 * 1000)
    expect('2026-09-17 09:39:02.728' >= range.start).toBe(true)
    expect('2026-09-17 09:39:02.728' <= range.end).toBe(true)
  })

  it('刷新相对时间时重新计算边界，不固定在首次进入页面的时刻', () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(new Date('2026-09-17T09:00:00Z'))
      const initial = observabilityTimeRange(1)
      vi.setSystemTime(new Date('2026-09-17T09:30:00Z'))
      const refreshed = observabilityTimeRange(1)
      expect(initial.end).toBe('2026-09-17 09:00:00.000')
      expect(refreshed).toEqual({ start: '2026-09-17 08:30:00.000', end: '2026-09-17 09:30:00.000' })
    } finally {
      vi.useRealTimers()
    }
  })

  it('全部时间不传过滤条件，空或无效输入不抛异常', () => {
    expect(observabilityTimeRange(0)).toEqual({ start: '', end: '' })
    for (const value of ['', 'invalid']) {
      expect(observabilityTimeFromInput(value)).toBe('')
      expect(observabilityTimeToInput(value)).toBe('')
    }
  })
})
