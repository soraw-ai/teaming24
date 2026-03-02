/**
 * Admin Store - Admin dashboard state management.
 */

import { create } from 'zustand'

interface Stats {
  users: { total: number; suspended: number }
  tokens: { total: number; active: number; revoked: number }
  nodes: { total: number; online: number; listed: number }
  audit_entries: number
  timestamp: number
}

interface AdminUser {
  id: string
  username: string
  email: string | null
  avatar_url: string | null
  is_admin: boolean
  is_suspended: boolean
  suspended_reason: string | null
  created_at: number
  last_login_at: number | null
  token_count: number
}

interface AdminToken {
  id: string
  user_id: string
  username: string
  node_id: string
  description: string | null
  created_at: number
  last_used_at: number | null
  expires_at: number | null
  is_active: boolean
}

interface AdminNode {
  id: string
  name: string
  description: string | null
  capability: string | null
  ip: string | null
  port: number | null
  region: string | null
  status: string
  is_listed: boolean
  last_seen: number | null
  registered_at: number
  owner_username: string
}

interface SystemSetting {
  key: string
  value: string | null
  description: string | null
  updated_at: number
}

interface DocPage {
  id: string
  slug: string
  title: string
  content: string | null
  category: string | null
  order: number
  is_published: boolean
  created_at: number
  updated_at: number
}

interface AdminState {
  stats: Stats | null
  users: AdminUser[]
  tokens: AdminToken[]
  nodes: AdminNode[]
  settings: SystemSetting[]
  docs: DocPage[]
  isLoading: boolean
  error: string | null
  
  fetchStats: () => Promise<void>
  fetchUsers: () => Promise<void>
  fetchTokens: () => Promise<void>
  fetchNodes: () => Promise<void>
  fetchSettings: () => Promise<void>
  fetchDocs: () => Promise<void>
  deleteUser: (userId: string) => Promise<boolean>
  deleteNode: (nodeId: string) => Promise<boolean>
  updateSetting: (key: string, value: string, description?: string) => Promise<boolean>
  deleteSetting: (key: string) => Promise<boolean>
  createDoc: (data: Partial<DocPage>) => Promise<DocPage | null>
  updateDoc: (slug: string, data: Partial<DocPage>) => Promise<boolean>
  deleteDoc: (slug: string) => Promise<boolean>
  clearError: () => void
}

export const useAdminStore = create<AdminState>()((set) => ({
  stats: null,
  users: [],
  tokens: [],
  nodes: [],
  settings: [],
  docs: [],
  isLoading: false,
  error: null,

  fetchStats: async () => {
    try {
      const res = await fetch('/api/admin/stats')
      if (!res.ok) throw new Error('Failed to fetch stats')
      const data = await res.json()
      set({ stats: data })
    } catch (e) {
      set({ error: (e as Error).message })
    }
  },

  fetchUsers: async () => {
    set({ isLoading: true })
    try {
      const res = await fetch('/api/admin/users?page_size=100')
      if (!res.ok) throw new Error('Failed to fetch users')
      const data = await res.json()
      set({ users: data.items ?? data, isLoading: false })
    } catch (e) {
      set({ error: (e as Error).message, isLoading: false })
    }
  },

  fetchTokens: async () => {
    set({ isLoading: true })
    try {
      const res = await fetch('/api/admin/tokens?page_size=100')
      if (!res.ok) throw new Error('Failed to fetch tokens')
      const data = await res.json()
      set({ tokens: data.items ?? data, isLoading: false })
    } catch (e) {
      set({ error: (e as Error).message, isLoading: false })
    }
  },

  fetchNodes: async () => {
    set({ isLoading: true })
    try {
      const res = await fetch('/api/admin/nodes?page_size=100')
      if (!res.ok) throw new Error('Failed to fetch nodes')
      const data = await res.json()
      set({ nodes: data.items ?? data, isLoading: false })
    } catch (e) {
      set({ error: (e as Error).message, isLoading: false })
    }
  },

  fetchSettings: async () => {
    set({ isLoading: true })
    try {
      const res = await fetch('/api/admin/settings')
      if (!res.ok) throw new Error('Failed to fetch settings')
      const data = await res.json()
      set({ settings: data, isLoading: false })
    } catch (e) {
      set({ error: (e as Error).message, isLoading: false })
    }
  },

  fetchDocs: async () => {
    set({ isLoading: true })
    try {
      const res = await fetch('/api/admin/docs')
      if (!res.ok) throw new Error('Failed to fetch docs')
      const data = await res.json()
      set({ docs: data, isLoading: false })
    } catch (e) {
      set({ error: (e as Error).message, isLoading: false })
    }
  },

  deleteUser: async (userId: string) => {
    try {
      const res = await fetch(`/api/admin/users/${userId}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Failed to delete user')
      set((state) => ({ users: state.users.filter((u) => u.id !== userId) }))
      return true
    } catch (e) {
      set({ error: (e as Error).message })
      return false
    }
  },

  deleteNode: async (nodeId: string) => {
    try {
      const res = await fetch(`/api/admin/nodes/${nodeId}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Failed to delete node')
      set((state) => ({ nodes: state.nodes.filter((n) => n.id !== nodeId) }))
      return true
    } catch (e) {
      set({ error: (e as Error).message })
      return false
    }
  },

  updateSetting: async (key: string, value: string, description?: string) => {
    try {
      const res = await fetch(`/api/admin/settings/${key}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value, description }),
      })
      if (!res.ok) throw new Error('Failed to update setting')
      const data = await res.json()
      set((state) => ({
        settings: state.settings.some((s) => s.key === key)
          ? state.settings.map((s) => (s.key === key ? data : s))
          : [...state.settings, data],
      }))
      return true
    } catch (e) {
      set({ error: (e as Error).message })
      return false
    }
  },

  deleteSetting: async (key: string) => {
    try {
      const res = await fetch(`/api/admin/settings/${key}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Failed to delete setting')
      set((state) => ({ settings: state.settings.filter((s) => s.key !== key) }))
      return true
    } catch (e) {
      set({ error: (e as Error).message })
      return false
    }
  },

  createDoc: async (data: Partial<DocPage>) => {
    try {
      const res = await fetch('/api/admin/docs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
      if (!res.ok) throw new Error('Failed to create doc')
      const doc = await res.json()
      set((state) => ({ docs: [...state.docs, doc] }))
      return doc
    } catch (e) {
      set({ error: (e as Error).message })
      return null
    }
  },

  updateDoc: async (slug: string, data: Partial<DocPage>) => {
    try {
      const res = await fetch(`/api/admin/docs/${slug}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
      if (!res.ok) throw new Error('Failed to update doc')
      const doc = await res.json()
      set((state) => ({
        docs: state.docs.map((d) => (d.slug === slug ? doc : d)),
      }))
      return true
    } catch (e) {
      set({ error: (e as Error).message })
      return false
    }
  },

  deleteDoc: async (slug: string) => {
    try {
      const res = await fetch(`/api/admin/docs/${slug}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Failed to delete doc')
      set((state) => ({ docs: state.docs.filter((d) => d.slug !== slug) }))
      return true
    } catch (e) {
      set({ error: (e as Error).message })
      return false
    }
  },

  clearError: () => set({ error: null }),
}))
