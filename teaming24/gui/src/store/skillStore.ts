import { create } from 'zustand'
import { getApiBaseAbsolute } from '../utils/api'
import { debugLog } from '../utils/debug'

const API_BASE = getApiBaseAbsolute()

export interface SkillRequirements {
  tools: string[]
  env: string[]
  bins: string[]
}

export interface Skill {
  id: string
  name: string
  description: string
  instructions: string
  category: string
  tags: string[]
  author: string
  version: string
  license: string
  compatibility: string
  requires: SkillRequirements
  enabled: boolean
  source: string
  file_path: string
  created_at: number
  updated_at: number
}

interface SkillState {
  skills: Skill[]
  loading: boolean
  selectedSkillId: string | null

  loadSkills: () => Promise<void>
  getSkill: (id: string) => Promise<Skill | null>
  createSkill: (data: Partial<Skill>) => Promise<string | null>
  updateSkill: (id: string, updates: Partial<Skill>) => Promise<void>
  deleteSkill: (id: string) => Promise<void>
  setSelectedSkill: (id: string | null) => void

  getAgentSkillIds: (agentId: string) => Promise<string[]>
  assignSkillsToAgent: (agentId: string, skillIds: string[]) => Promise<void>
}

export const useSkillStore = create<SkillState>()((set, get) => ({
  skills: [],
  loading: false,
  selectedSkillId: null,

  loadSkills: async () => {
    const isFirstLoad = get().skills.length === 0
    if (isFirstLoad) set({ loading: true })
    try {
      const res = await fetch(`${API_BASE}/api/skills`)
      if (res.ok) {
        const data = await res.json()
        const incoming: Skill[] = (data.skills || []).map((s: Record<string, unknown>) => ({
          id: s.id as string,
          name: s.name as string || '',
          description: s.description as string || '',
          instructions: s.instructions as string || '',
          category: s.category as string || 'general',
          tags: (s.tags as string[]) || [],
          author: s.author as string || '',
          version: s.version as string || '1.0.0',
          license: s.license as string || '',
          compatibility: s.compatibility as string || '',
          requires: (s.requires as SkillRequirements) || { tools: [], env: [], bins: [] },
          enabled: s.enabled !== false,
          source: s.source as string || '',
          file_path: s.file_path as string || '',
          created_at: ((s.created_at as number) || 0) * 1000,
          updated_at: ((s.updated_at as number) || 0) * 1000,
        }))

        // Only update if something actually changed
        const prev = get().skills
        const changed =
          prev.length !== incoming.length ||
          incoming.some((s, i) => {
            const p = prev[i]
            return !p || p.id !== s.id || p.name !== s.name || p.enabled !== s.enabled ||
              p.updated_at !== s.updated_at || p.description !== s.description
          })
        if (changed) {
          set({ skills: incoming })
          debugLog(`[SkillStore] Synced ${incoming.length} skills`)
        }
      } else {
        console.error(`[SkillStore] Failed to load skills: HTTP ${res.status}`)
      }
    } catch (err) {
      console.error('[SkillStore] Failed to load skills:', err)
    } finally {
      if (isFirstLoad) set({ loading: false })
    }
  },

  getSkill: async (id: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/skills/${id}`)
      if (res.ok) {
        return await res.json() as Skill
      }
    } catch (err) {
      console.error('[SkillStore] Failed to get skill:', err)
    }
    return null
  },

  createSkill: async (data: Partial<Skill>) => {
    try {
      const res = await fetch(`${API_BASE}/api/skills`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
      if (res.ok) {
        const result = await res.json()
        await get().loadSkills()
        return result.id as string
      }
    } catch (err) {
      console.error('[SkillStore] Failed to create skill:', err)
    }
    return null
  },

  updateSkill: async (id: string, updates: Partial<Skill>) => {
    try {
      await fetch(`${API_BASE}/api/skills/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      })
      await get().loadSkills()
    } catch (err) {
      console.error('[SkillStore] Failed to update skill:', err)
    }
  },

  deleteSkill: async (id: string) => {
    try {
      await fetch(`${API_BASE}/api/skills/${id}`, { method: 'DELETE' })
      set(state => ({
        skills: state.skills.filter(s => s.id !== id),
        selectedSkillId: state.selectedSkillId === id ? null : state.selectedSkillId,
      }))
    } catch (err) {
      console.error('[SkillStore] Failed to delete skill:', err)
    }
  },

  setSelectedSkill: (id) => set({ selectedSkillId: id }),

  getAgentSkillIds: async (agentId: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/agent/agents/${agentId}/skills`)
      if (res.ok) {
        const data = await res.json()
        return (data.skill_ids || []) as string[]
      }
    } catch (err) {
      console.error('[SkillStore] Failed to get agent skills:', err)
    }
    return []
  },

  assignSkillsToAgent: async (agentId: string, skillIds: string[]) => {
    try {
      await fetch(`${API_BASE}/api/agent/agents/${agentId}/skills`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skill_ids: skillIds }),
      })
      debugLog(`[SkillStore] Assigned ${skillIds.length} skills to agent ${agentId}`)
    } catch (err) {
      console.error('[SkillStore] Failed to assign skills:', err)
    }
  },
}))
