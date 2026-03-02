import { create } from 'zustand'
import { getApiBase, getApiBaseAbsolute } from '../utils/api'
import { debugLog } from '../utils/debug'
import { DEFAULT_LOCAL_NODE_DESCRIPTION } from '../constants/node'

/**
 * Configuration loaded from teaming24.yaml via /api/config endpoint.
 * All settings come from the unified config file - no hardcoded values.
 */
export interface AppConfig {
  // Server settings
  serverHost: string
  serverPort: number
  
  // API Settings
  apiBaseUrl: string
  apiPrefix: string
  
  // Local Agentic Node
  localNodeAnId: string            // Canonical unique ID (wallet + random suffix)
  localNodeName: string            // Human-readable display name
  localNodeWalletAddress: string   // Crypto wallet address
  localNodeHost: string
  localNodePort: number
  localNodeDescription: string
  localNodeCapability: string
  localNodeRegion: string
  
  // Discovery settings
  discoveryBroadcastPort: number
  discoveryBroadcastInterval: number
  discoveryNodeExpirySeconds: number
  discoveryMaxLanNodes: number
  discoveryMaxWanNodes: number
  
  // Connection settings
  connectionTimeout: number
  connectionRetryAttempts: number
  connectionKeepaliveInterval: number
  
  // Subscription settings
  subscriptionMaxQueueSize: number
  subscriptionKeepaliveInterval: number
  
  // Database settings
  databasePath: string
  
  // Marketplace settings
  marketplaceUrl: string
  marketplaceAutoRejoin: boolean
  agentanetCentralUrl: string
  autoOnline: boolean
  
  // Derived/computed
  fullApiUrl: string
  
  // Status
  isLoaded: boolean
  error: string | null
  /** Unix timestamp from backend for stale-config detection. */
  configVersion: number | null
  
  // Legacy aliases (for backward compatibility)
  localAnHost: string
  localAnPort: number
  localAnName: string
}

interface ConfigState extends AppConfig {
  // Actions
  loadConfig: () => Promise<void>
  /** Trigger a backend config reload (re-reads YAML + DB overrides). */
  reloadConfig: () => Promise<void>
  setConfig: (config: Partial<AppConfig>) => void
  getApiUrl: (path: string) => string
}

/**
 * Default configuration values.
 * These are only used as fallbacks if /api/config is unreachable.
 * The actual values should come from teaming24.yaml via the API.
 */
const defaultConfig: AppConfig = {
  // Server
  serverHost: '0.0.0.0',
  serverPort: 8000,
  
  // API
  apiBaseUrl: getApiBaseAbsolute(),
  apiPrefix: import.meta.env.VITE_API_PREFIX || '/api',
  
  // Local node
  localNodeAnId: '',
  localNodeName: '',
  localNodeWalletAddress: '',
  localNodeHost: '127.0.0.1',
  localNodePort: 8000,
  localNodeDescription: DEFAULT_LOCAL_NODE_DESCRIPTION,
  localNodeCapability: 'General Purpose',
  localNodeRegion: 'Local',
  
  // Discovery
  discoveryBroadcastPort: 54321,
  discoveryBroadcastInterval: 5,
  discoveryNodeExpirySeconds: 30,
  discoveryMaxLanNodes: 1000,
  discoveryMaxWanNodes: 100,
  
  // Connection
  connectionTimeout: 30,
  connectionRetryAttempts: 3,
  connectionKeepaliveInterval: 60,
  
  // Subscription
  subscriptionMaxQueueSize: 100,
  subscriptionKeepaliveInterval: 15,
  
  // Database
  databasePath: '~/.teaming24/data.db',
  
  // Marketplace
  marketplaceUrl: 'http://100.64.1.3:8080/api/marketplace',
  marketplaceAutoRejoin: true,
  agentanetCentralUrl: 'http://100.64.1.3:8080',
  
  // Network auto-connect
  autoOnline: true,
  
  // Derived
  fullApiUrl: getApiBaseAbsolute(),
  
  // Status
  isLoaded: false,
  error: null,
  configVersion: null,
  
  // Legacy aliases
  localAnHost: '127.0.0.1',
  localAnPort: 8000,
  localAnName: 'Local Agentic Node',
}

export const useConfigStore = create<ConfigState>()((set, get) => ({
  ...defaultConfig,

  loadConfig: async () => {
    try {
      // Try to load config from backend
      const baseUrl = getApiBase()
      const response = await fetch(`${baseUrl}/api/config`)
      
      if (!response.ok) {
        throw new Error(`Failed to load config: ${response.status}`)
      }
      
      const data = await response.json()
      const pick = <T,>(value: T | null | undefined, fallback: T): T => value ?? fallback
      
      set({
        // Server settings
        serverHost: pick(data.server_host, defaultConfig.serverHost),
        serverPort: pick(data.server_port, defaultConfig.serverPort),
        
        // API settings
        apiBaseUrl: pick(data.api_base_url, defaultConfig.apiBaseUrl),
        apiPrefix: pick(data.api_prefix, defaultConfig.apiPrefix),
        
        // Local node settings
        localNodeAnId: pick(data.local_node_an_id, ''),
        localNodeName: pick(data.local_node_name, defaultConfig.localNodeName),
        localNodeWalletAddress: pick(data.local_node_wallet_address, ''),
        localNodeHost: pick(data.local_node_host, defaultConfig.localNodeHost),
        localNodePort: pick(data.local_node_port, defaultConfig.localNodePort),
        localNodeDescription: pick(data.local_node_description, defaultConfig.localNodeDescription),
        localNodeCapability: pick(data.local_node_capability, defaultConfig.localNodeCapability),
        localNodeRegion: pick(data.local_node_region, defaultConfig.localNodeRegion),
        
        // Discovery settings
        discoveryBroadcastPort: pick(data.discovery_broadcast_port, defaultConfig.discoveryBroadcastPort),
        discoveryBroadcastInterval: pick(data.discovery_broadcast_interval, defaultConfig.discoveryBroadcastInterval),
        discoveryNodeExpirySeconds: pick(data.discovery_node_expiry_seconds, defaultConfig.discoveryNodeExpirySeconds),
        discoveryMaxLanNodes: pick(data.discovery_max_lan_nodes, defaultConfig.discoveryMaxLanNodes),
        discoveryMaxWanNodes: pick(data.discovery_max_wan_nodes, defaultConfig.discoveryMaxWanNodes),
        
        // Connection settings
        connectionTimeout: pick(data.connection_timeout, defaultConfig.connectionTimeout),
        connectionRetryAttempts: pick(data.connection_retry_attempts, defaultConfig.connectionRetryAttempts),
        connectionKeepaliveInterval: pick(data.connection_keepalive_interval, defaultConfig.connectionKeepaliveInterval),
        
        // Subscription settings
        subscriptionMaxQueueSize: pick(data.subscription_max_queue_size, defaultConfig.subscriptionMaxQueueSize),
        subscriptionKeepaliveInterval: pick(data.subscription_keepalive_interval, defaultConfig.subscriptionKeepaliveInterval),
        
        // Database settings
        databasePath: pick(data.database_path, defaultConfig.databasePath),
        
        // Marketplace settings
        marketplaceUrl: pick(data.marketplace_url, defaultConfig.marketplaceUrl),
        marketplaceAutoRejoin: data.marketplace_auto_rejoin ?? defaultConfig.marketplaceAutoRejoin,
        agentanetCentralUrl: pick(data.agentanet_central_url, defaultConfig.agentanetCentralUrl),
        
        // Network auto-connect
        autoOnline: data.auto_online ?? defaultConfig.autoOnline,
        
        // Derived
        fullApiUrl: pick(data.api_base_url, defaultConfig.fullApiUrl),
        
        // Status
        isLoaded: true,
        error: null,
        configVersion: data.config_version ?? null,
        
        // Legacy aliases for backward compatibility
        localAnHost: data.local_node_host ?? data.agentanet_local_host ?? defaultConfig.localAnHost,
        localAnPort: data.local_node_port ?? data.agentanet_local_port ?? defaultConfig.localAnPort,
        localAnName: data.local_node_name ?? data.agentanet_local_name ?? defaultConfig.localAnName,
      })
      
      debugLog('Configuration loaded from server (teaming24.yaml)')
    } catch (error) {
      console.warn('Failed to load config from server, using defaults:', error)
      // Keep using defaults but mark as loaded
      set({
        isLoaded: true,
        error: error instanceof Error ? error.message : 'Unknown error',
      })
    }
  },

  reloadConfig: async () => {
    try {
      const baseUrl = getApiBase()
      const res = await fetch(`${baseUrl}/api/config/reload`, { method: 'POST' })
      if (!res.ok) throw new Error(`Reload failed: ${res.status}`)
      // After backend reload, re-fetch the fresh config
      await get().loadConfig()
    } catch (error) {
      console.warn('Config reload failed:', error)
    }
  },

  setConfig: (config) => {
    set((state) => ({
      ...state,
      ...config,
      fullApiUrl: config.apiBaseUrl || state.apiBaseUrl,
    }))
  },

  getApiUrl: (path: string) => {
    const state = get()
    // If path already starts with http, return as-is
    if (path.startsWith('http')) {
      return path
    }
    // Build full URL
    const base = state.apiBaseUrl || ''
    const prefix = state.apiPrefix || '/api'
    const cleanPath = path.startsWith('/') ? path : `/${path}`
    
    // If path already includes prefix, don't add it again
    if (cleanPath.startsWith(prefix)) {
      return `${base}${cleanPath}`
    }
    
    return `${base}${prefix}${cleanPath}`
  },
}))

// Auto-load config on startup
if (typeof window !== 'undefined') {
  // Delay loading to ensure the app is initialized
  setTimeout(() => {
    useConfigStore.getState().loadConfig()
  }, 100)
}
