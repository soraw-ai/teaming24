/**
 * Settings Store - Persistent user preferences
 * 
 * Settings are stored in SQLite database via /api/db/settings endpoints.
 * Defaults are used when no saved value exists.
 */

import { create } from 'zustand'
import { notify } from './notificationStore'
import { getApiBase } from '../utils/api'
import { debugLog } from '../utils/debug'
import { DEFAULT_LOCAL_NODE_DESCRIPTION } from '../constants/node'

// Default settings values
export const DEFAULT_SETTINGS = {
  // Appearance
  theme: 'dark' as 'dark' | 'light',
  sidebarCollapsed: false,
  compactMode: false,
  
  // Local Node
  localNodeName: 'Local Agentic Node',
  localNodeDescription: DEFAULT_LOCAL_NODE_DESCRIPTION,
  localNodeCapability: 'General Purpose',
  localNodeRegion: 'Local',
  
  // Network
  autoConnectOnStartup: true,
  autoJoinMarketplace: false,
  lanDiscoveryEnabled: true,
  lanDiscoverable: true,
  
  // AgentaNet Central Service
  agentanetCentralUrl: 'http://100.64.1.3:8080',
  agentanetToken: '',
  
  // LLM
  defaultLLMProvider: 'flock',
  defaultModel: 'gpt-5.2',
  openaiApiKey: '',
  anthropicApiKey: '',
  flockApiKey: '',
  localApiKey: 'local',
  openaiBaseUrl: 'https://api.openai.com/v1',
  anthropicBaseUrl: 'https://api.anthropic.com',
  flockBaseUrl: 'https://api.flock.io/v1',
  localBaseUrl: 'http://localhost:11434/v1',
  localCustomModel: 'llama3.1',
  temperature: 0.7,
  maxTokens: 4096,
  
  // CrewAI / Agent Settings
  // See: https://docs.crewai.com/concepts/crews
  crewaiVerbose: true,  // Enable verbose output for tracking
  crewaiProcess: 'hierarchical' as 'sequential' | 'hierarchical',
  crewaiMemory: false,
  agentMemoryEnabled: true,
  respectContextWindow: true,
  crewaiMaxRpm: 10,  // Max requests per minute
  agentScenario: 'product_team',  // Active agent scenario
  organizerModel: 'flock/gpt-5.2',
  coordinatorModel: 'flock/gpt-5.2',
  workerDefaultModel: 'flock/gpt-5.2',
  anRouterModel: 'flock/gpt-5.2',
  localAgentRouterModel: 'flock/gpt-5.2',
  
  // CrewAI Planning & Reasoning
  // Planning: Creates step-by-step plan before execution
  // https://docs.crewai.com/concepts/planning
  crewaiPlanning: false,  
  crewaiPlanningLlm: 'flock/gpt-5.2',  // LLM for planning (separate from main agent LLM)
  // Reasoning: Agents reflect and create plan before executing tasks
  // https://docs.crewai.com/concepts/agents#reasoning-agent
  crewaiReasoning: false,
  crewaiMaxReasoningAttempts: 3,
  
  // CrewAI Streaming (Real-time event streaming)
  // https://docs.crewai.com/concepts/event-listener
  crewaiStreaming: true,  // Enable real-time streaming via event listeners
  
  // Task idle timeout (seconds). 0 = disabled (keep waiting). Resets on step/keepalive.
  taskExecutionTimeout: 0,
  
  // Sandbox
  sandboxEnabled: true,
  sandboxAutoStart: false,
  sandboxTimeout: 300,
  
  // Task Output
  taskOutputEnabled: true,
  taskOutputDir: '~/.teaming24/outputs',
  
  // Notifications
  notificationsEnabled: true,
  soundEnabled: false,
  desktopNotifications: false,
  
  // Advanced
  debugMode: false,
  logLevel: 'INFO' as 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR',
}

export type Settings = typeof DEFAULT_SETTINGS

const SUPPORTED_PROVIDERS = new Set(['openai', 'anthropic', 'flock', 'local'])

function normalizeProvider(value: unknown): string {
  const normalized = String(value || '').trim().toLowerCase()
  return SUPPORTED_PROVIDERS.has(normalized) ? normalized : DEFAULT_SETTINGS.defaultLLMProvider
}

function sanitizeSettingsPatch(input: Partial<Settings>): Partial<Settings> {
  const next: Partial<Settings> = { ...input }

  if ('defaultLLMProvider' in next) {
    next.defaultLLMProvider = normalizeProvider(next.defaultLLMProvider)
  }
  if ('defaultModel' in next) {
    const model = String(next.defaultModel || '').trim()
    next.defaultModel = model || DEFAULT_SETTINGS.defaultModel
  }
  if ('crewaiPlanningLlm' in next) {
    const model = String(next.crewaiPlanningLlm || '').trim()
    next.crewaiPlanningLlm = model || DEFAULT_SETTINGS.crewaiPlanningLlm
  }
  for (const key of ['organizerModel', 'coordinatorModel', 'workerDefaultModel', 'anRouterModel', 'localAgentRouterModel'] as const) {
    if (key in next) {
      const model = String(next[key] || '').trim()
      next[key] = model || DEFAULT_SETTINGS[key]
    }
  }
  return next
}

interface SettingsState extends Settings {
  // Status
  isLoaded: boolean
  isSaving: boolean
  
  // Actions
  loadSettings: () => Promise<void>
  saveSetting: <K extends keyof Settings>(key: K, value: Settings[K]) => Promise<void>
  saveSettings: (settings: Partial<Settings>) => Promise<void>
  resetToDefaults: () => Promise<void>
  getSetting: <K extends keyof Settings>(key: K) => Settings[K]
}

export const useSettingsStore = create<SettingsState>()((set, get) => ({
  // Initialize with defaults
  ...DEFAULT_SETTINGS,
  isLoaded: false,
  isSaving: false,

  loadSettings: async () => {
    try {
      const apiBase = getApiBase()
      const response = await fetch(`${apiBase}/api/db/settings`)
      
      if (!response.ok) {
        throw new Error('Failed to load settings')
      }
      
      const data = await response.json()
      const savedSettings = data.settings || {}
      
      // Merge saved settings with defaults
      const mergedSettings: Partial<Settings> = {}
      for (const key of Object.keys(DEFAULT_SETTINGS) as (keyof Settings)[]) {
        if (key in savedSettings) {
          mergedSettings[key] = savedSettings[key]
        }
      }
      
      const sanitized = sanitizeSettingsPatch(mergedSettings)
      set({
        ...sanitized,
        isLoaded: true,
      })
      
      debugLog('Settings loaded from database')
    } catch (error) {
      console.warn('Failed to load settings, using defaults:', error)
      set({ isLoaded: true })
    }
  },

  saveSetting: async (key, value) => {
    set({ isSaving: true })
    
    try {
      const apiBase = getApiBase()
      const sanitizedPatch = sanitizeSettingsPatch({ [key]: value } as Partial<Settings>)
      const sanitizedValue = (sanitizedPatch[key] ?? value) as Settings[typeof key]
      const response = await fetch(`${apiBase}/api/db/settings/${key}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: sanitizedValue }),
      })
      
      if (!response.ok) {
        throw new Error('Failed to save setting')
      }
      
      // Update local state
      set({ [key]: sanitizedValue } as Partial<SettingsState>)
      
    } catch (error) {
      console.error('Failed to save setting:', error)
      notify.error('Settings', `Failed to save ${key}`)
    } finally {
      set({ isSaving: false })
    }
  },

  saveSettings: async (settings) => {
    set({ isSaving: true })
    
    try {
      const apiBase = getApiBase()
      const sanitized = sanitizeSettingsPatch(settings)

      const batchResponse = await fetch(`${apiBase}/api/db/settings/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: sanitized }),
      })
      if (!batchResponse.ok) {
        throw new Error(`Failed to save settings batch: ${batchResponse.status}`)
      }
      
      // Update local state
      set(sanitized as Partial<SettingsState>)
      
      notify.success('Settings', 'Settings saved')
    } catch (error) {
      console.error('Failed to save settings:', error)
      notify.error('Settings', 'Failed to save settings')
    } finally {
      set({ isSaving: false })
    }
  },

  resetToDefaults: async () => {
    set({ isSaving: true })
    
    try {
      const apiBase = getApiBase()
      const response = await fetch(`${apiBase}/api/db/settings/reset`, {
        method: 'POST',
      })
      
      if (!response.ok) {
        throw new Error('Failed to reset settings')
      }
      
      // Reset local state to defaults
      set({
        ...DEFAULT_SETTINGS,
        isLoaded: true,
        isSaving: false,
      })
      
      notify.success('Settings', 'All settings reset to defaults')
    } catch (error) {
      console.error('Failed to reset settings:', error)
      notify.error('Settings', 'Failed to reset settings')
      set({ isSaving: false })
    }
  },

  getSetting: (key) => {
    return get()[key]
  },
}))

// Auto-load settings on startup
if (typeof window !== 'undefined') {
  setTimeout(() => {
    useSettingsStore.getState().loadSettings()
  }, 200)
}
