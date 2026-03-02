/**
 * Authentication Store for AgentaNet Central.
 */

import { create } from 'zustand'

const DEFAULT_TOKEN_LIMIT = 5

interface User {
  id: string
  username: string
  email: string | null
  avatar_url: string | null
  is_admin: boolean
  created_at: number
  token_max_per_user?: number
}

interface Token {
  id: string
  node_id: string
  description: string | null
  created_at: number
  last_used_at: number | null
  expires_at: number | null
  is_active: boolean
  plain_token?: string  // Only present on creation
}

interface AuthState {
  user: User | null
  tokens: Token[]
  tokenLimit: number
  isLoading: boolean
  error: string | null
  
  // Actions
  login: (username: string) => Promise<boolean>
  logout: () => Promise<void>
  fetchUser: () => Promise<void>
  fetchTokens: () => Promise<void>
  createToken: (nodeId: string, description?: string) => Promise<Token | null>
  refreshToken: (tokenId: string) => Promise<Token | null>
  revokeToken: (tokenId: string) => Promise<boolean>
  clearError: () => void
}

export const useAuthStore = create<AuthState>()((set, get) => ({
  user: null,
  tokens: [],
  tokenLimit: DEFAULT_TOKEN_LIMIT,
  isLoading: false,
  error: null,

  login: async (username: string) => {
    set({ isLoading: true, error: null })
    try {
      const res = await fetch('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username }),
      })
      
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.error?.message || data.detail || 'Login failed')
      }
      
      const data = await res.json()
      set({
        user: {
          id: data.user_id,
          username: data.username,
          email: data.email,
          avatar_url: data.avatar_url,
          is_admin: data.is_admin || false,
          created_at: Date.now() / 1000,
          token_max_per_user: data.token_max_per_user,
        },
        tokenLimit:
          typeof data.token_max_per_user === 'number' && data.token_max_per_user > 0
            ? data.token_max_per_user
            : DEFAULT_TOKEN_LIMIT,
        isLoading: false,
      })
      
      // Fetch tokens after login
      get().fetchTokens()
      return true
    } catch (e) {
      console.error('Login failed:', e)
      set({ error: (e as Error).message, isLoading: false })
      return false
    }
  },

  logout: async () => {
    try {
      await fetch('/auth/logout', { method: 'POST' })
    } catch (e) {
      console.error('Logout request failed:', e)
    }
    set({ user: null, tokens: [], tokenLimit: DEFAULT_TOKEN_LIMIT })
  },

  fetchUser: async () => {
    try {
      const res = await fetch('/api/user/me')
      if (!res.ok) {
        set({ user: null })
        return
      }
      const data = await res.json()
      set({
        user: data,
        tokenLimit:
          typeof data.token_max_per_user === 'number' && data.token_max_per_user > 0
            ? data.token_max_per_user
            : DEFAULT_TOKEN_LIMIT,
      })
      get().fetchTokens()
    } catch (e) {
      console.error('Fetch user failed:', e)
      set({ user: null, tokenLimit: DEFAULT_TOKEN_LIMIT })
    }
  },

  fetchTokens: async () => {
    try {
      const res = await fetch('/api/tokens')
      if (!res.ok) return
      const data = await res.json()
      set({ tokens: data })
    } catch (e) {
      console.error('Fetch tokens failed:', e)
    }
  },

  createToken: async (nodeId: string, description?: string) => {
    set({ isLoading: true, error: null })
    try {
      const res = await fetch('/api/tokens', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node_id: nodeId, description }),
      })
      
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.error?.message || data.detail || 'Failed to create token')
      }
      
      const token = await res.json()
      set((state) => ({
        tokens: [...state.tokens, token],
        isLoading: false,
      }))
      return token
    } catch (e) {
      console.error('Create token failed:', e)
      set({ error: (e as Error).message, isLoading: false })
      return null
    }
  },

  refreshToken: async (tokenId: string) => {
    set({ isLoading: true, error: null })
    try {
      const res = await fetch(`/api/tokens/${tokenId}/refresh`, {
        method: 'POST',
      })
      
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.error?.message || data.detail || 'Failed to refresh token')
      }
      
      const token = await res.json()
      set((state) => ({
        tokens: state.tokens.map((t) => (t.id === tokenId ? token : t)),
        isLoading: false,
      }))
      return token
    } catch (e) {
      console.error(`Refresh token failed (${tokenId}):`, e)
      set({ error: (e as Error).message, isLoading: false })
      return null
    }
  },

  revokeToken: async (tokenId: string) => {
    set({ isLoading: true, error: null })
    try {
      const res = await fetch(`/api/tokens/${tokenId}`, {
        method: 'DELETE',
      })
      
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.error?.message || data.detail || 'Failed to revoke token')
      }
      
      set((state) => ({
        tokens: state.tokens.filter((t) => t.id !== tokenId),
        isLoading: false,
      }))
      return true
    } catch (e) {
      console.error(`Revoke token failed (${tokenId}):`, e)
      set({ error: (e as Error).message, isLoading: false })
      return false
    }
  },

  clearError: () => set({ error: null }),
}))
