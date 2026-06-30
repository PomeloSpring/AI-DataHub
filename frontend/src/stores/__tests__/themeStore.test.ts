import { describe, it, expect, beforeEach } from 'vitest'
import { useThemeStore, applyTheme, IS_DARK, type ThemeId } from '../themeStore'

describe('themeStore', () => {
  beforeEach(() => {
    // Reset store to default
    useThemeStore.setState({ theme: 'dark', isDark: true })
    document.documentElement.className = ''
    window.localStorage.clear()
  })

  describe('IS_DARK mapping', () => {
    it('marks dark themes correctly', () => {
      expect(IS_DARK.dark).toBe(true)
      expect(IS_DARK.tech).toBe(true)
      expect(IS_DARK.finance).toBe(true)
      expect(IS_DARK.glass).toBe(true)
      expect(IS_DARK.ainative).toBe(true)
    })

    it('marks light themes correctly', () => {
      expect(IS_DARK.light).toBe(false)
      expect(IS_DARK.bento).toBe(false)
      expect(IS_DARK.medical).toBe(false)
    })

    it('covers all 8 themes', () => {
      const themes: ThemeId[] = ['dark', 'light', 'tech', 'finance', 'bento', 'glass', 'ainative', 'medical']
      themes.forEach(t => {
        expect(IS_DARK).toHaveProperty(t)
        expect(typeof IS_DARK[t]).toBe('boolean')
      })
    })
  })

  describe('setTheme', () => {
    it('updates theme and isDark for dark theme', () => {
      useThemeStore.getState().setTheme('tech')
      const state = useThemeStore.getState()
      expect(state.theme).toBe('tech')
      expect(state.isDark).toBe(true)
    })

    it('updates theme and isDark for light theme', () => {
      useThemeStore.getState().setTheme('light')
      const state = useThemeStore.getState()
      expect(state.theme).toBe('light')
      expect(state.isDark).toBe(false)
    })

    it('handles all 8 themes', () => {
      const themes: ThemeId[] = ['dark', 'light', 'tech', 'finance', 'bento', 'glass', 'ainative', 'medical']
      for (const t of themes) {
        useThemeStore.getState().setTheme(t)
        expect(useThemeStore.getState().theme).toBe(t)
        expect(useThemeStore.getState().isDark).toBe(IS_DARK[t])
      }
    })
  })

  describe('toggle', () => {
    it('switches from dark to light', () => {
      useThemeStore.setState({ theme: 'dark', isDark: true })
      useThemeStore.getState().toggle()
      expect(useThemeStore.getState().theme).toBe('light')
      expect(useThemeStore.getState().isDark).toBe(false)
    })

    it('switches from light to dark', () => {
      useThemeStore.setState({ theme: 'light', isDark: false })
      useThemeStore.getState().toggle()
      expect(useThemeStore.getState().theme).toBe('dark')
      expect(useThemeStore.getState().isDark).toBe(true)
    })

    it('toggling from non-dark theme goes to dark', () => {
      useThemeStore.setState({ theme: 'tech', isDark: true })
      useThemeStore.getState().toggle()
      // toggle uses: cur === 'dark' ? 'light' : 'dark'
      // tech !== 'dark', so next = 'dark'
      expect(useThemeStore.getState().theme).toBe('dark')
      expect(useThemeStore.getState().isDark).toBe(true)
    })
  })
})

describe('applyTheme', () => {
  beforeEach(() => {
    document.documentElement.className = ''
  })

  it('does not add class for dark theme (default)', () => {
    applyTheme('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(document.documentElement.classList.contains('light')).toBe(false)
  })

  it('adds light class for light theme', () => {
    applyTheme('light')
    expect(document.documentElement.classList.contains('light')).toBe(true)
  })

  it('adds tech class for tech theme', () => {
    applyTheme('tech')
    expect(document.documentElement.classList.contains('tech')).toBe(true)
  })

  it('adds ainative class for ainative theme', () => {
    applyTheme('ainative')
    expect(document.documentElement.classList.contains('ainative')).toBe(true)
  })

  it('removes previous theme class when switching', () => {
    applyTheme('light')
    expect(document.documentElement.classList.contains('light')).toBe(true)

    applyTheme('tech')
    expect(document.documentElement.classList.contains('light')).toBe(false)
    expect(document.documentElement.classList.contains('tech')).toBe(true)
  })

  it('removes all theme classes when switching to dark', () => {
    applyTheme('glass')
    expect(document.documentElement.classList.contains('glass')).toBe(true)

    applyTheme('dark')
    expect(document.documentElement.classList.contains('glass')).toBe(false)
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('handles all 8 themes', () => {
    const themes: ThemeId[] = ['dark', 'light', 'tech', 'finance', 'bento', 'glass', 'ainative', 'medical']
    for (const t of themes) {
      applyTheme(t)
      if (t === 'dark') {
        // dark is default, no class added
        expect(document.documentElement.className).toBe('')
      } else {
        expect(document.documentElement.classList.contains(t)).toBe(true)
      }
    }
  })
})
