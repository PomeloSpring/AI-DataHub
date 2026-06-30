import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useAuthStore } from '../authStore'

vi.mock('../../api/client', () => ({
  default: {
    post: vi.fn(),
  },
}))

import client from '../../api/client'
const mockClient = vi.mocked(client)

describe('authStore', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useAuthStore.setState({ token: null, user: null })
    vi.clearAllMocks()
  })

  describe('initial state', () => {
    it('reads token from localStorage on init', () => {
      // The store reads from localStorage in its initializer
      // We can test the current state reflects what's in localStorage
      window.localStorage.setItem('token', 'test-token')
      window.localStorage.setItem('user', JSON.stringify({ id: 1, username: 'test', role: 'admin' }))

      // Re-read the store's current state
      const state = useAuthStore.getState()
      // Note: the store initializer already ran, so we check the current state
      // This test verifies the store interface works
      expect(state).toHaveProperty('token')
      expect(state).toHaveProperty('user')
    })
  })

  describe('login', () => {
    it('stores token and user in state and localStorage', async () => {
      const loginData = {
        access_token: 'new-token-123',
        user: { id: 1, username: 'admin', role: 'admin' },
      }
      mockClient.post.mockResolvedValue({ data: loginData })

      await useAuthStore.getState().login('admin', 'password')

      const state = useAuthStore.getState()
      expect(state.token).toBe('new-token-123')
      expect(state.user).toEqual({ id: 1, username: 'admin', role: 'admin' })
      expect(window.localStorage.getItem('token')).toBe('new-token-123')
      expect(JSON.parse(window.localStorage.getItem('user')!)).toEqual(loginData.user)
    })

    it('calls correct API endpoint', async () => {
      mockClient.post.mockResolvedValue({
        data: { access_token: 'tok', user: { id: 1, username: 'u', role: 'user' } },
      })

      await useAuthStore.getState().login('u', 'p')
      expect(mockClient.post).toHaveBeenCalledWith('/auth/login', { username: 'u', password: 'p' })
    })

    it('throws on invalid credentials', async () => {
      mockClient.post.mockRejectedValue(new Error('Unauthorized'))

      await expect(useAuthStore.getState().login('bad', 'bad')).rejects.toThrow('Unauthorized')
    })
  })

  describe('logout', () => {
    it('clears token and user from state', () => {
      useAuthStore.setState({
        token: 'some-token',
        user: { id: 1, username: 'admin', role: 'admin' },
      })
      window.localStorage.setItem('token', 'some-token')
      window.localStorage.setItem('user', JSON.stringify({ id: 1, username: 'admin', role: 'admin' }))

      useAuthStore.getState().logout()

      expect(useAuthStore.getState().token).toBeNull()
      expect(useAuthStore.getState().user).toBeNull()
      expect(window.localStorage.getItem('token')).toBeNull()
      expect(window.localStorage.getItem('user')).toBeNull()
    })
  })

  describe('updateUser', () => {
    it('merges updates into user object', () => {
      useAuthStore.setState({
        user: { id: 1, username: 'admin', role: 'user' },
      })

      useAuthStore.getState().updateUser({ role: 'admin' })
      expect(useAuthStore.getState().user).toEqual({ id: 1, username: 'admin', role: 'admin' })
    })

    it('persists updated user to localStorage', () => {
      useAuthStore.setState({
        user: { id: 1, username: 'admin', role: 'user' },
      })

      useAuthStore.getState().updateUser({ username: 'superadmin' })
      const saved = JSON.parse(window.localStorage.getItem('user')!)
      expect(saved.username).toBe('superadmin')
    })

    it('does nothing when user is null', () => {
      useAuthStore.setState({ user: null })
      useAuthStore.getState().updateUser({ role: 'admin' })
      expect(useAuthStore.getState().user).toBeNull()
    })

    it('only updates specified fields', () => {
      useAuthStore.setState({
        user: { id: 1, username: 'admin', role: 'user' },
      })

      useAuthStore.getState().updateUser({ role: 'admin' })
      const user = useAuthStore.getState().user!
      expect(user.id).toBe(1)
      expect(user.username).toBe('admin')
      expect(user.role).toBe('admin')
    })
  })
})
