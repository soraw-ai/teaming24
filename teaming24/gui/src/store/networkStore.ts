/**
 * Network Store - Manages local node's connection status to AgentaNet.
 * 
 * Online: Local node is part of AgentaNet, can receive remote connections
 * Offline: Local node is disconnected, no remote agentic connections allowed
 */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { notify } from './notificationStore'
import { getApiBase } from '../utils/api'
import { useSettingsStore } from './settingsStore'

export type NetworkStatus = 'online' | 'offline' | 'connecting' | 'disconnecting'

export interface NodeInfo {
  id: string          // Unique ID: generated as {type}:{ip}:{port} for uniqueness
  remoteId?: string   // Original ID from remote node (an_id)
  anId?: string       // The canonical AN identifier (wallet-suffix)
  name: string
  alias?: string      // User-defined display name
  ip: string
  port: number
  role: string
  status: 'online' | 'offline' | 'busy'
  last_seen: number
  connectedSince?: number  // Timestamp when connection was established
  type: 'lan' | 'wan'
  capability?: string
  capabilities?: { name: string; description: string }[]
  price?: string
  region?: string
  endpoint?: string
  // Extended info
  walletAddress?: string
  agentId?: string
  description?: string
}

// Historical connection record for reconnection
export interface ConnectionHistory {
  id: string          // Same as NodeInfo id (type:ip:port)
  name: string        // Original name from node
  alias?: string      // User-defined name
  ip: string
  port: number
  lastConnected: number // Timestamp of last successful connection
  connectCount: number  // Number of times connected
}

// Per-connection session record
export interface ConnectionSession {
  sessionId: string
  nodeId?: string
  name: string
  alias?: string
  ip: string
  port: number
  direction: 'outbound' | 'inbound'
  startedAt: number
  endedAt: number
  durationSeconds: number
  reason: string
}

// Local node's marketplace listing info
export interface MarketplaceListing {
  name: string
  description: string
  capability: string
  price: string
  capabilities?: { name: string; description: string }[]
}

// Generate unique node ID based on connection info
export function generateNodeId(type: 'lan' | 'wan', ip: string, port: number): string {
  return `${type}:${ip}:${port}`
}

// Helper to get display name (alias if set, otherwise original name)
export function getNodeDisplayName(node: NodeInfo): string {
  return node.alias || node.name
}

// Check if a node with same connection exists
export function findExistingNode(nodes: NodeInfo[], ip: string, port: number): NodeInfo | undefined {
  return nodes.find(n => n.ip === ip && n.port === port)
}

function normalizeMarketplaceNode(node: any): NodeInfo {
  const now = Date.now()
  const ip = typeof node?.ip === 'string' ? node.ip : ''
  const port = typeof node?.port === 'number' ? node.port : 0
  const remoteId = typeof node?.id === 'string' ? node.id : undefined
  const anId =
    typeof node?.an_id === 'string' && node.an_id.trim()
      ? node.an_id
      : typeof node?.agent_id === 'string' && node.agent_id.trim()
        ? node.agent_id
        : remoteId
  return {
    id: String(remoteId || `marketplace:${ip}:${port || now}`),
    name: String(node?.name || 'Unknown Node'),
    ip,
    port,
    role: String(node?.role || 'worker'),
    status: (node?.status === 'busy' || node?.status === 'offline' ? node.status : 'online') as 'online' | 'offline' | 'busy',
    last_seen: typeof node?.last_seen === 'number' ? node.last_seen : now,
    type: 'wan',
    capability: typeof node?.capability === 'string' ? node.capability : undefined,
    capabilities: Array.isArray(node?.capabilities) ? node.capabilities : undefined,
    price: typeof node?.price === 'string' ? node.price : undefined,
    region: typeof node?.region === 'string' ? node.region : undefined,
    description: typeof node?.description === 'string' ? node.description : undefined,
    remoteId,
    anId,
    walletAddress: typeof node?.wallet_address === 'string' ? node.wallet_address : undefined,
  }
}

interface NetworkState {
  // Status
  status: NetworkStatus
  lastStatusChange: number | null
  connectedSince: number | null
  
  // Connection info
  nodeId: string | null
  nodeName: string
  peerCount: number
  
  // Discovery
  isDiscovering: boolean
  isDiscoverable: boolean  // Whether this node can be discovered by others
  lanNodes: NodeInfo[]
  wanNodes: NodeInfo[]
  inboundPeers: any[]  // [{ node: NodeInfo, connected_since?: number }]
  
  // Connection History (persisted)
  connectionHistory: ConnectionHistory[]
  connectionSessions: ConnectionSession[]
  
  // Marketplace Listing (persisted)
  isListedOnMarketplace: boolean
  marketplaceListing: MarketplaceListing | null
  
  // Actions
  goOnline: () => Promise<boolean>
  goOffline: () => Promise<boolean>
  setStatus: (status: NetworkStatus) => void
  updatePeerCount: (count: number) => void
  
  // Discovery Actions
  startDiscovery: (opts?: { silent?: boolean }) => Promise<void>
  stopDiscovery: () => Promise<void>
  setDiscoverable: (discoverable: boolean, opts?: { silent?: boolean }) => Promise<void>
  fetchLanNodes: () => Promise<void>
  triggerBroadcast: () => Promise<void>  // Manually trigger a broadcast scan
  fetchInboundPeers: () => Promise<void>
  connectToLanNode: (node: NodeInfo, alias?: string) => Promise<void>
  connectToWanNode: (ip: string, port: number, password?: string, alias?: string) => Promise<void>
  searchNodes: (query: string) => Promise<NodeInfo[]>
  fetchMarketplace: (opts?: { search?: string; capability?: string }) => Promise<NodeInfo[]>
  
  // Node Management
  setNodeAlias: (nodeId: string, alias: string) => void
  disconnectNode: (nodeId: string) => void
  removeNode: (nodeId: string) => void
  clearAllNodes: () => void
  disconnectAllNodes: () => Promise<void>
  
  // History Management
  addToHistory: (node: NodeInfo) => void
  removeFromHistory: (id: string) => void
  clearHistory: () => void
  reconnectFromHistory: (history: ConnectionHistory) => Promise<void>
  addSession: (session: ConnectionSession) => void
  clearSessions: () => Promise<void>
  fetchSessions: () => Promise<void>
  
  // Marketplace Listing
  joinMarketplace: (listing: MarketplaceListing) => Promise<boolean>
  leaveMarketplace: () => Promise<boolean>
  updateMarketplaceListing: (listing: Partial<MarketplaceListing>) => Promise<boolean>
  
  // Sync live connection state from backend (for page reload recovery)
  syncPeersFromBackend: () => Promise<void>
  
  // Event Handling
  handleSSEMessage: (event: any) => void
}

export const useNetworkStore = create<NetworkState>()(
  persist(
    (set, get) => ({
      // Initial state
      status: 'offline',
      lastStatusChange: null,
      connectedSince: null,
      nodeId: null,
      nodeName: 'Local Node',
      peerCount: 0,
      
      isDiscovering: false,
      isDiscoverable: true,
      lanNodes: [],
      wanNodes: [],
      inboundPeers: [],
      connectionHistory: [],
      connectionSessions: [],
      isListedOnMarketplace: false,
      marketplaceListing: null,

      goOnline: async () => {
        const current = get().status
        if (current === 'online' || current === 'connecting') {
          return current === 'online'
        }

        set({ status: 'connecting' })

        try {
          const apiBase = getApiBase()

          const now = Date.now()
          set({
            status: 'online',
            lastStatusChange: now,
            connectedSince: now,
          })

          notify.success('AgentaNet', 'Connected to network')

          // Start UDP service and enable LAN visibility so other nodes can find us.
          // This does NOT enable active scanning (Scan toggle stays off).
          try {
            const apiBase2 = getApiBase()
            // Start the UDP listener service (required for receiving broadcasts)
            await fetch(`${apiBase2}/api/network/lan/start`, { method: 'POST' })

            // Apply LAN visibility preference (broadcasts our presence)
            const settings = useSettingsStore.getState()
            if (settings.lanDiscoverable) {
              await get().setDiscoverable(true, { silent: true })
            }
          } catch (e) {
            console.error('Failed to start UDP service on goOnline:', e)
          }

          // Check marketplace status and restore listing if needed
          try {
            const statusRes = await fetch(`${apiBase}/api/network/marketplace/status`)
            if (!statusRes.ok) {
              throw new Error('Failed to get marketplace status')
            }
            const statusData = await statusRes.json()
            const settings = useSettingsStore.getState()
            const centralEnabled = statusData.central_enabled !== false
            const centralConfigured = statusData.central_configured !== false

            if (centralEnabled && !centralConfigured) {
              set({ isListedOnMarketplace: false })
              if (settings.autoJoinMarketplace && get().marketplaceListing) {
                notify.warning('Agentic Node Marketplace', 'Link AgentaNet Central URL + Token first before auto-join')
              }
              return true
            }
            
            if (statusData.listed) {
              // Marketplace says we're listed
              if (statusData.listing) {
                // Backend has our listing data - use it
                set({ 
                  isListedOnMarketplace: true,
                  marketplaceListing: statusData.listing
                })
              } else if (get().marketplaceListing) {
                // Backend says listed but no data - re-register with saved config
                const listing = get().marketplaceListing!
                const joinRes = await fetch(`${apiBase}/api/network/marketplace/join`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify(listing)
                })
                
                if (joinRes.ok) {
                  const joinData = await joinRes.json()
                  set({
                    isListedOnMarketplace: true,
                    marketplaceListing: joinData.listing || listing,
                  })
                } else {
                  const err = await joinRes.json().catch(() => ({}))
                  const msg = err.detail || err.error?.message || 'Failed to re-register listing'
                  notify.warning('Agentic Node Marketplace', msg)
                }
              } else {
                // Listed but no config anywhere - just mark as listed
                // User can update their listing later
                set({ isListedOnMarketplace: true })
              }
              notify.info('Agentic Node Marketplace', 'Listed on Agentic Node Marketplace')
            } else if (settings.autoJoinMarketplace && get().marketplaceListing) {
              // Not listed but have saved config - auto rejoin
              const listing = get().marketplaceListing!
              const joinRes = await fetch(`${apiBase}/api/network/marketplace/join`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(listing)
              })
              
              if (joinRes.ok) {
                const joinData = await joinRes.json()
                set({
                  isListedOnMarketplace: true,
                  marketplaceListing: joinData.listing || listing,
                })
                notify.info('Agentic Node Marketplace', 'Auto-rejoined Agentic Node Marketplace')
              } else {
                const err = await joinRes.json().catch(() => ({}))
                const msg = err.detail || err.error?.message || 'Auto-rejoin failed'
                notify.warning('Agentic Node Marketplace', msg)
              }
            }
          } catch (e) {
            console.error('Failed to check/restore marketplace status:', e)
          }
          
          return true
        } catch (error) {
          console.error('Failed to go online:', error)
          set({ status: 'offline' })
          return false
        }
      },

      goOffline: async () => {
        set({ status: 'disconnecting' })

        const apiBase = getApiBase()
        let leaveSucceeded = true

        // Disconnect all connected nodes and save to history
        const { wanNodes } = get()
        if (wanNodes.length > 0) {
          // Add current connections to history before disconnecting
          wanNodes.forEach(node => get().addToHistory(node))

          // Send disconnect notification to all nodes
          await get().disconnectAllNodes()
        }

        // Leave marketplace if listed (does not erase saved listing config)
        if (get().isListedOnMarketplace) {
          try {
            const res = await fetch(`${apiBase}/api/network/marketplace/leave`, { method: 'POST' })
            if (!res.ok) {
              leaveSucceeded = false
              const err = await res.json().catch(() => ({}))
              const msg = err.detail || err.error?.message || 'Failed to leave marketplace'
              notify.warning('Agentic Node Marketplace', msg)
            }
          } catch (e) {
            console.error('Failed to leave marketplace:', e)
            leaveSucceeded = false
            notify.warning('Agentic Node Marketplace', 'Failed to sync unlist with central service')
          }
        }

        // Stop UDP discovery service (listener + any scan activity) when going offline.
        try {
          await fetch(`${apiBase}/api/network/lan/stop`, { method: 'POST' })
        } catch (e) {
          console.error('Failed to stop LAN discovery service:', e)
        }

        // Cleanup local state (keep isDiscoverable preference for next time)
        set({
          status: 'offline',
          lastStatusChange: Date.now(),
          connectedSince: null,
          peerCount: 0,
          isDiscovering: false,
          inboundPeers: [],
          isListedOnMarketplace: leaveSucceeded ? false : get().isListedOnMarketplace,
          lanNodes: [],
          wanNodes: []
        })
        notify.info('AgentaNet', 'Disconnected from network')
        return true
      },

      setStatus: (status) => {
        set({ status, lastStatusChange: Date.now() })
      },

      updatePeerCount: (count) => {
        set({ peerCount: count })
      },
      
      startDiscovery: async (opts?: { silent?: boolean }) => {
        try {
          const apiBase = getApiBase()
          // scan/start: sets is_scanning=True on backend, starts UDP if needed, fires discover broadcast
          await fetch(`${apiBase}/api/network/lan/scan/start`, { method: 'POST' })
          set({ isDiscovering: true })
          if (!opts?.silent) {
            notify.success('LAN Discovery', 'Started scanning local network')
          }
          // Fetch initial LAN nodes; retry after delays so UDP responses have time to arrive
          get().fetchLanNodes()
          setTimeout(() => get().fetchLanNodes(), 600)
          setTimeout(() => get().fetchLanNodes(), 1500)
        } catch (error) {
          console.error('Failed to start discovery:', error)
          if (!opts?.silent) {
            notify.error('LAN Discovery', 'Failed to start discovery')
          }
        }
      },

      stopDiscovery: async () => {
        try {
          const apiBase = getApiBase()
          // scan/stop: sets is_scanning=False; stops UDP only if LAN Visible is also off
          await fetch(`${apiBase}/api/network/lan/scan/stop`, { method: 'POST' })
          set({ isDiscovering: false })
          notify.info('LAN Discovery', 'Stopped scanning')
        } catch (error) {
          console.error('Failed to stop discovery:', error)
        }
      },
      
      setDiscoverable: async (discoverable, opts) => {
        try {
          const apiBase = getApiBase()
          // LAN Visible: starts UDP listener and responds to discover (independent of Scan)
          await fetch(`${apiBase}/api/network/lan/discoverable`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ discoverable })
          })
          set({ isDiscoverable: discoverable })
          if (!opts?.silent) {
            notify.info('LAN Visibility', discoverable ? 'Now visible to LAN' : 'Now hidden from LAN')
          }

          // If we just hid ourselves and we're not scanning, stop the UDP discovery service to save resources.
          if (!discoverable && !get().isDiscovering) {
            try {
              await fetch(`${apiBase}/api/network/lan/stop`, { method: 'POST' })
            } catch (e) {
              console.error('Failed to stop LAN discovery service after hiding:', e)
            }
          }
        } catch (error) {
          console.error('Failed to set discoverable:', error)
          if (!opts?.silent) {
            notify.error('LAN Visibility', 'Failed to update visibility')
          }
        }
      },
      
      fetchLanNodes: async () => {
        try {
          const apiBase = getApiBase()
          const res = await fetch(`${apiBase}/api/network/lan/nodes`)
          if (!res.ok) {
            console.error('Failed to fetch LAN nodes:', res.status)
            return
          }
          const data = await res.json()
          const rawNodes = Array.isArray(data?.nodes) ? data.nodes : []
          const processedNodes: NodeInfo[] = rawNodes.map((n: any) => ({
            ...n,
            id: generateNodeId('lan', n.ip ?? '', n.port ?? 0),
            remoteId: n.id,
            anId: n.an_id || n.agent_id || n.id,
            type: 'lan' as const,
            last_seen: n.last_seen || Date.now(),
          }))
          set({ lanNodes: processedNodes })
        } catch (error) {
          console.error('Failed to fetch LAN nodes:', error)
        }
      },
      
      triggerBroadcast: async () => {
        try {
          const apiBase = getApiBase()
          // Trigger immediate broadcast
          await fetch(`${apiBase}/api/network/lan/broadcast`, { method: 'POST' })
          // Then fetch nodes after a short delay to let responses come in
          setTimeout(() => {
            get().fetchLanNodes()
          }, 500)
        } catch (error) {
          console.error('Failed to trigger broadcast:', error)
        }
      },

      fetchInboundPeers: async () => {
        try {
          const apiBase = getApiBase()
          const res = await fetch(`${apiBase}/api/network/inbound`)
          const data = await res.json()
          set({ inboundPeers: data.peers || [] })
        } catch (error) {
          console.error('Failed to fetch inbound peers:', error)
        }
      },

      syncPeersFromBackend: async () => {
        // Restore outbound + inbound connections and LAN discovery state from backend.
        // Called on page load / new tab so UI matches real backend state after refresh.
        try {
          const apiBase = getApiBase()
          const [outRes, inRes, lanStatusRes] = await Promise.all([
            fetch(`${apiBase}/api/network/outbound`),
            fetch(`${apiBase}/api/network/inbound`),
            fetch(`${apiBase}/api/network/lan/status`).catch(() => null),
          ])
          if (lanStatusRes?.ok) {
            try {
              const lanStatus = await lanStatusRes.json()
              // is_scanning = Scan ON; fallback to running for older backends
              const backendScanning = lanStatus.is_scanning ?? lanStatus.running ?? false
              set({
                isDiscovering: backendScanning,
                isDiscoverable: lanStatus.discoverable !== false,
              })
              // If scan was active (e.g. page refresh without backend restart), reload node list
              if (backendScanning) {
                get().fetchLanNodes()
              }
            } catch {
              // ignore
            }
          }
          const outData = await outRes.json()
          const inData = await inRes.json()

          const backendPeers: NodeInfo[] = (outData.peers || []).map((p: any) => {
            const n = p.node
            const uniqueId = generateNodeId('wan', n.ip, n.port)
            return {
              ...n,
              id: uniqueId,
              remoteId: n.id,
              anId: n.an_id || n.agent_id || n.id,
              type: 'wan' as const,
              status: n.status || 'online',
              last_seen: Date.now(),
              connectedSince: p.connected_since ? Math.floor(p.connected_since * 1000) : Date.now(),
            } as NodeInfo
          })

          // Merge: keep user aliases from persisted state, update status from backend
          const persisted = get().wanNodes
          const merged: NodeInfo[] = []
          const seenIds = new Set<string>()

          for (const bp of backendPeers) {
            seenIds.add(bp.id)
            const existing = persisted.find(p => p.id === bp.id || (p.ip === bp.ip && p.port === bp.port))
            merged.push({
              ...bp,
              alias: existing?.alias, // Preserve user-set alias
              connectedSince: existing?.connectedSince || bp.connectedSince,
            })
          }

          // Mark previously persisted nodes that are no longer in backend as offline
          for (const p of persisted) {
            if (!seenIds.has(p.id) && !backendPeers.some(bp => bp.ip === p.ip && bp.port === p.port)) {
              merged.push({ ...p, status: 'offline' })
            }
          }

          // Only update state if something actually changed
          const newInbound = inData.peers || []
          const wanChanged =
            persisted.length !== merged.length ||
            merged.some((n, i) => {
              const p = persisted[i]
              return !p || p.id !== n.id || p.status !== n.status
            })
          const inboundChanged =
            get().inboundPeers.length !== newInbound.length ||
            newInbound.some((p: any, i: number) => {
              const prev = get().inboundPeers[i]
              return !prev || (prev as any).id !== p.id
            })
          const newStatus = backendPeers.length > 0 || newInbound.length > 0 ? 'online' : get().status

          if (wanChanged || inboundChanged || get().status !== newStatus) {
            set({
              wanNodes: merged,
              inboundPeers: newInbound,
              status: newStatus,
            })
          }
        } catch (error) {
          console.error('Failed to sync peers from backend:', error)
        }
      },

      
      connectToLanNode: async (node, alias) => {
        const { wanNodes } = get()
        
        // Check for existing connection
        const existing = findExistingNode(wanNodes, node.ip, node.port)
        if (existing && existing.status !== 'offline') {
          notify.warning('Already Connected', `Node at ${node.ip}:${node.port} is already connected`)
          return
        }
        
        try {
          const apiBase = getApiBase()
          const res = await fetch(`${apiBase}/api/network/connect`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ip: node.ip, port: node.port, password: '' })
          })
          
          if (!res.ok) throw new Error('Connection failed')
          
          const connectedNode = await res.json()
          const displayName = alias || connectedNode.name || node.name
          
          // Generate unique ID based on connection info
          const uniqueId = existing?.id || generateNodeId('wan', node.ip, node.port)
          
          // Build the node with all required fields
          const now = Date.now()
          const newNode: NodeInfo = {
            ...connectedNode,
            id: uniqueId,
            remoteId: connectedNode.id, // Keep original remote ID
            anId: connectedNode.an_id || connectedNode.agent_id || connectedNode.id,
            ip: node.ip,
            port: node.port,
            type: 'lan',  // Keep type as 'lan' to indicate LAN origin
            alias: alias || existing?.alias || undefined,
            name: connectedNode.name || node.name,
            last_seen: now,
            connectedSince: now,
          }

          // Add/update in wanNodes (connected nodes), keep lanNodes for discovery display
          if (existing) {
            set(state => ({
              wanNodes: state.wanNodes.map(n => (n.id === existing.id ? { ...n, ...newNode, status: 'online' as const } : n))
            }))
          } else {
            set(state => ({
              wanNodes: [...state.wanNodes, newNode]
            }))
          }
          notify.success('LAN Connect', `Connected to ${displayName}`)
        } catch (error) {
          console.error('Failed to connect to LAN node:', error)
          notify.error('LAN Connect', `Failed to connect to ${node.name}`)
          throw error
        }
      },
      
      connectToWanNode: async (ip, port, password, alias) => {
        const { wanNodes, lanNodes } = get()
        
        // Check for existing connection
        const existing = findExistingNode(wanNodes, ip, port)
        if (existing && existing.status !== 'offline') {
          notify.warning('Already Connected', `Node at ${ip}:${port} is already connected as "${getNodeDisplayName(existing)}"`)
          return
        }
        
        try {
          const apiBase = getApiBase()
          const res = await fetch(`${apiBase}/api/network/connect`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ip, port, password })
          })
          
          if (!res.ok) throw new Error('Connection failed')
          
          const remoteNode = await res.json()
          const displayName = alias || remoteNode.name
          
          // Generate unique ID based on connection info
          const uniqueId = existing?.id || generateNodeId('wan', ip, port)
          // Prefer LAN representation if this endpoint is discovered on LAN.
          const isLanDiscovered = lanNodes.some(n => n.ip === ip && n.port === port)
          const nodeType: 'lan' | 'wan' = existing?.type || (isLanDiscovered ? 'lan' : 'wan')
          
          // Build the node with all required fields
          const now = Date.now()
          const newNode: NodeInfo = {
            ...remoteNode,
            id: uniqueId,
            remoteId: remoteNode.id, // Keep original remote ID (an_id)
            anId: remoteNode.an_id || remoteNode.agent_id || remoteNode.id,
            ip,
            port,
            type: nodeType,
            alias: alias || existing?.alias || undefined,
            last_seen: now,
            connectedSince: now,
          }

          if (existing) {
            set(state => ({
              wanNodes: state.wanNodes.map(n => (n.id === existing.id ? { ...n, ...newNode, status: 'online' as const } : n))
            }))
          } else {
            set(state => ({
              wanNodes: [...state.wanNodes, newNode]
            }))
          }
          notify.success('Connected', `Connected to ${displayName}`)
        } catch (error) {
          console.error('Failed to connect:', error)
          notify.error('Connection Failed', 'Failed to connect to node')
          throw error
        }
      },
      
      searchNodes: async (query) => {
        return get().fetchMarketplace({ search: query })
      },
      
      fetchMarketplace: async (opts) => {
        const apiBase = getApiBase()
        const params = new URLSearchParams()
        if (opts?.search?.trim()) {
          params.set('search', opts.search.trim())
        }
        if (opts?.capability?.trim()) {
          params.set('capability', opts.capability.trim())
        }
        const query = params.toString()
        const res = await fetch(`${apiBase}/api/network/marketplace${query ? `?${query}` : ''}`)
        if (!res.ok) {
          throw new Error('Failed to fetch marketplace')
        }
        const data = await res.json()
        return (data.nodes || []).map(normalizeMarketplaceNode)
      },
      
      setNodeAlias: (nodeId, alias) => {
        set(state => ({
          wanNodes: state.wanNodes.map(node =>
            node.id === nodeId ? { ...node, alias: alias.trim() || undefined } : node
          ),
          lanNodes: state.lanNodes.map(node =>
            node.id === nodeId ? { ...node, alias: alias.trim() || undefined } : node
          )
        }))
        notify.success('Node Alias', alias ? `Alias set to "${alias}"` : 'Alias removed')
      },
      
      disconnectNode: (nodeId) => {
        const node = get().wanNodes.find(n => n.id === nodeId)
        if (node) {
          // Record a session if we have timing info
          if (node.connectedSince) {
            const endedAtMs = Date.now()
            const durationSeconds = Math.max(0, Math.floor((endedAtMs - node.connectedSince) / 1000))
            get().addSession({
              sessionId: `sess-${node.id}-${endedAtMs}`,
              nodeId: node.remoteId || node.id,
              name: node.name,
              alias: node.alias,
              ip: node.ip,
              port: node.port,
              direction: 'outbound',
              startedAt: node.connectedSince,
              endedAt: endedAtMs,
              durationSeconds,
              reason: 'user_disconnect',
            })
          }

          // Save to history before disconnecting
          get().addToHistory(node)

          // Send disconnect notification to the node
          const apiBase = getApiBase()
          fetch(`${apiBase}/api/network/disconnect`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ip: node.ip, port: node.port })
          }).catch(e => console.error('Failed to send disconnect:', e))

          // Mark as disconnected but keep in list (status: 'offline')
          set(state => ({
            wanNodes: state.wanNodes.map(n =>
              n.id === nodeId
                ? { ...n, status: 'offline' as const, connectedSince: undefined }
                : n
            )
          }))
          notify.info('Disconnected', `Disconnected from ${node.alias || node.name}`)
        }
      },

      removeNode: (nodeId) => {
        const node = get().wanNodes.find(n => n.id === nodeId)
        if (node) {
          // If still connected, disconnect first
          if (node.status !== 'offline') {
            const apiBase = getApiBase()
            fetch(`${apiBase}/api/network/disconnect`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ ip: node.ip, port: node.port })
            }).catch(e => console.error('Failed to send disconnect:', e))
          }

          // Remove from list
          set(state => ({
            wanNodes: state.wanNodes.filter(n => n.id !== nodeId)
          }))
          notify.info('Removed', `Removed ${node.alias || node.name} from list`)
        }
      },
      
      clearAllNodes: () => {
        const { wanNodes } = get()
        if (wanNodes.length === 0) return
        const apiBase = getApiBase()
        // Fire-and-forget disconnect for any still-connected nodes
        wanNodes.forEach(node => {
          if (node.status !== 'offline') {
            fetch(`${apiBase}/api/network/disconnect`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ ip: node.ip, port: node.port })
            }).catch(e => console.error('Failed to disconnect:', e))
          }
        })
        set({ wanNodes: [] })
        notify.info('Cleared', `Removed all ${wanNodes.length} node(s) from the list`)
      },

      disconnectAllNodes: async () => {
        const { wanNodes } = get()
        if (wanNodes.length === 0) return
        
        const apiBase = getApiBase()

        // Record sessions for all currently connected nodes
        wanNodes.forEach(node => {
          if (node.status !== 'offline' && node.connectedSince) {
            const endedAtMs = Date.now()
            const durationSeconds = Math.max(0, Math.floor((endedAtMs - node.connectedSince) / 1000))
            get().addSession({
              sessionId: `sess-${node.id}-${endedAtMs}`,
              nodeId: node.remoteId || node.id,
              name: node.name,
              alias: node.alias,
              ip: node.ip,
              port: node.port,
              direction: 'outbound',
              startedAt: node.connectedSince,
              endedAt: endedAtMs,
              durationSeconds,
              reason: 'disconnect_all',
            })
          }
        })
        
        // Send disconnect notification to all nodes in parallel
        await Promise.all(
          wanNodes.map(node =>
            fetch(`${apiBase}/api/network/disconnect`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ ip: node.ip, port: node.port })
            }).catch(e => console.error(`Failed to disconnect ${node.ip}:${node.port}:`, e))
          )
        )
        
        notify.info('Disconnected', `Disconnected from ${wanNodes.length} node(s)`)
      },
      
      // History Management
      addToHistory: (node) => {
        // Also save to backend database
        const apiBase = getApiBase()
        fetch(`${apiBase}/api/db/history`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            id: node.id,
            name: node.name,
            alias: node.alias,
            ip: node.ip,
            port: node.port,
            wallet_address: node.walletAddress,
            agent_id: node.agentId,
            capability: node.capability,
            description: node.description,
            capabilities: node.capabilities
          })
        }).catch(e => console.error('Failed to sync history to DB:', e))
        
        set(state => {
          const existingIndex = state.connectionHistory.findIndex(h => h.id === node.id)
          
          const historyEntry: ConnectionHistory = {
            id: node.id,
            name: node.name,
            alias: node.alias,
            ip: node.ip,
            port: node.port,
            lastConnected: Date.now(),
            connectCount: existingIndex >= 0 
              ? state.connectionHistory[existingIndex].connectCount + 1 
              : 1
          }
          
          if (existingIndex >= 0) {
            // Update existing entry
            const newHistory = [...state.connectionHistory]
            newHistory[existingIndex] = historyEntry
            return { connectionHistory: newHistory }
          } else {
            // Add new entry (keep max 20)
            return {
              connectionHistory: [historyEntry, ...state.connectionHistory].slice(0, 20)
            }
          }
        })
      },
      
      removeFromHistory: (id) => {
        set(state => ({
          connectionHistory: state.connectionHistory.filter(h => h.id !== id)
        }))
      },
      
      clearHistory: () => {
        set({ connectionHistory: [] })
        notify.info('History', 'Connection history cleared')
      },
      
      reconnectFromHistory: async (history) => {
        // Use the existing connect function
        await get().connectToWanNode(history.ip, history.port, '', history.alias)
      },

      addSession: (session) => {
        const apiBase = getApiBase()
        fetch(`${apiBase}/api/db/sessions`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            session_id: session.sessionId,
            node_id: session.nodeId,
            name: session.name,
            alias: session.alias,
            ip: session.ip,
            port: session.port,
            direction: session.direction,
            started_at: session.startedAt / 1000,
            ended_at: session.endedAt / 1000,
            duration_seconds: session.durationSeconds,
            reason: session.reason,
            metadata: {},
            created_at: Date.now() / 1000,
          })
        }).catch(e => console.error('Failed to sync session to DB:', e))

        set(state => ({
          connectionSessions: [session, ...state.connectionSessions].slice(0, 500)
        }))
      },

      fetchSessions: async () => {
        try {
          const apiBase = getApiBase()
          const res = await fetch(`${apiBase}/api/db/sessions?limit=200`)
          const data = await res.json()
          const sessions = (data.sessions || []).map((s: any) => ({
            sessionId: s.session_id,
            nodeId: s.node_id || undefined,
            name: s.name,
            alias: s.alias || undefined,
            ip: s.ip,
            port: s.port,
            direction: (s.direction === 'inbound' ? 'inbound' : 'outbound') as 'inbound' | 'outbound',
            startedAt: Math.floor((s.started_at || 0) * 1000),
            endedAt: Math.floor((s.ended_at || 0) * 1000),
            durationSeconds: s.duration_seconds || 0,
            reason: s.reason || 'unknown',
          })) as ConnectionSession[]
          set({ connectionSessions: sessions })
        } catch (e) {
          console.error('Failed to fetch sessions:', e)
        }
      },

      clearSessions: async () => {
        const apiBase = getApiBase()
        try {
          await fetch(`${apiBase}/api/db/sessions`, { method: 'DELETE' })
        } catch (e) {
          console.error('Failed to clear sessions in DB:', e)
        }
        set({ connectionSessions: [] })
        notify.info('Sessions', 'Session history cleared')
      },
      
      // Marketplace Listing
      joinMarketplace: async (listing) => {
        const apiBase = getApiBase()
        try {
          const res = await fetch(`${apiBase}/api/network/marketplace/join`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(listing)
          })
          
          if (!res.ok) {
            const error = await res.json()
            throw new Error(error.detail || error.error?.message || 'Failed to join marketplace')
          }
          const data = await res.json()
          
          set({
            isListedOnMarketplace: true,
            marketplaceListing: data.listing || listing,
          })
          notify.success('Agentic Node Marketplace', 'Successfully listed on Agentic Node Marketplace')
          return true
        } catch (error) {
          console.error('Failed to join marketplace:', error)
          notify.error('Agentic Node Marketplace', `Failed to join: ${error instanceof Error ? error.message : 'Unknown error'}`)
          return false
        }
      },
      
      leaveMarketplace: async () => {
        const apiBase = getApiBase()
        try {
          const res = await fetch(`${apiBase}/api/network/marketplace/leave`, {
            method: 'POST'
          })
          
          if (!res.ok) {
            const err = await res.json().catch(() => ({}))
            throw new Error(err.detail || err.error?.message || 'Failed to leave marketplace')
          }
          
          set({ isListedOnMarketplace: false })
          notify.info('Agentic Node Marketplace', 'Removed from Agentic Node Marketplace')
          return true
        } catch (error) {
          console.error('Failed to leave marketplace:', error)
          notify.error('Agentic Node Marketplace', 'Failed to leave Agentic Node Marketplace')
          return false
        }
      },
      
      updateMarketplaceListing: async (updates) => {
        const current = get().marketplaceListing
        if (!current) return false
        
        const newListing = { ...current, ...updates }
        const apiBase = getApiBase()
        
        try {
          const res = await fetch(`${apiBase}/api/network/marketplace/update`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(newListing)
          })
          
          if (!res.ok) {
            const err = await res.json().catch(() => ({}))
            throw new Error(err.detail || err.error?.message || 'Failed to update listing')
          }
          const data = await res.json()
          
          set({ marketplaceListing: data.listing || newListing })
          notify.success('Agentic Node Marketplace', 'Listing updated')
          return true
        } catch (error) {
          console.error('Failed to update marketplace listing:', error)
          notify.error('Agentic Node Marketplace', 'Failed to update listing')
          return false
        }
      },
      
      handleSSEMessage: (event) => {
        // Handle incoming SSE events
        if (event.type === 'node_discovered') {
          const node = event.data
          if (!node?.ip || !node?.port) return
          const uniqueId = generateNodeId('lan', node.ip, node.port)
          
          set(state => {
            // If we already have this node as a WAN entry by endpoint, prefer LAN for display.
            const updatedWanNodes = state.wanNodes.map(wn =>
              wn.ip === node.ip && wn.port === node.port
                ? { ...wn, type: 'lan' as const, last_seen: Date.now() }
                : wn
            )

            // Check if exists by IP:port (not by original ID)
            const exists = state.lanNodes.some(n => n.ip === node.ip && n.port === node.port)
            if (exists) {
              // Update existing node's last_seen
              return {
                lanNodes: state.lanNodes.map(n =>
                  n.ip === node.ip && n.port === node.port
                    ? { ...n, last_seen: Date.now(), status: node.status }
                    : n
                ),
                wanNodes: updatedWanNodes
              }
            }
            
            // Add new node with unique ID
            const newNode: NodeInfo = {
              ...node,
              id: uniqueId,
              remoteId: node.id,
              anId: node.an_id || node.agent_id || node.id,
              type: 'lan' as const,
              last_seen: Date.now(),
            }
            
            return {
              lanNodes: [...state.lanNodes, newNode],
              wanNodes: updatedWanNodes
            }
          })
        }
        
        // Handle remote node disconnect notification
        if (event.type === 'node_disconnected' || event.type === 'peer_disconnected') {
          const { ip, port, nodeId, connected_since, peer } = event.data || {}
          
          set(state => {
            // Find the node by IP:port or nodeId
            const node = state.wanNodes.find(n => 
              (ip && port && n.ip === ip && n.port === port) || 
              (nodeId && (n.id === nodeId || n.remoteId === nodeId))
            )

            // If this refers to an inbound peer, record a session and remove from "Connected To Me".
            let inboundPeers = state.inboundPeers
            if (nodeId) {
              const entry = state.inboundPeers.find((p: any) => (p.node?.id || p.id) === nodeId)
              const pNode = peer || entry?.node
              const startedAtSec = connected_since ?? entry?.connected_since
              if (pNode && startedAtSec) {
                const startedAtMs = Math.floor(startedAtSec * 1000)
                const endedAtMs = Date.now()
                const durationSeconds = Math.max(0, Math.floor((endedAtMs - startedAtMs) / 1000))
                get().addSession({
                  sessionId: `sess-${nodeId}-${endedAtMs}`,
                  nodeId,
                  name: pNode.name,
                  alias: pNode.alias,
                  ip: pNode.ip,
                  port: pNode.port,
                  direction: 'inbound',
                  startedAt: startedAtMs,
                  endedAt: endedAtMs,
                  durationSeconds,
                  reason: event.data?.reason || 'peer_disconnected',
                })
              }

              inboundPeers = state.inboundPeers.filter((p: any) => (p.node?.id || p.id) !== nodeId)
            }
            
            if (!node) {
              return { ...state, inboundPeers }
            }

            // Mark offline but keep in list for reconnect
            if (node.connectedSince) {
              const endedAtMs = Date.now()
              const durationSeconds = Math.max(0, Math.floor((endedAtMs - node.connectedSince) / 1000))
              get().addSession({
                sessionId: `sess-${node.id}-${endedAtMs}`,
                nodeId: node.remoteId || node.id,
                name: node.name,
                alias: node.alias,
                ip: node.ip,
                port: node.port,
                direction: 'outbound',
                startedAt: node.connectedSince,
                endedAt: endedAtMs,
                durationSeconds,
                reason: event.data?.reason || 'peer_disconnected',
              })
            }
            get().addToHistory(node)
            notify.warning('Node Disconnected', `${getNodeDisplayName(node)} has disconnected`)
            return {
              wanNodes: state.wanNodes.map(n =>
                n.id === node.id ? { ...n, status: 'offline' as const, connectedSince: undefined, last_seen: Date.now() } : n
              ),
              inboundPeers
            }
          })
        }
        
        // Handle node status changes
        if (event.type === 'node_status_changed') {
          const { nodeId, status, ip, port } = event.data || {}
          if (status && (nodeId || (ip && port))) {
            set(state => ({
              wanNodes: state.wanNodes.map(n =>
                (nodeId && (n.id === nodeId || n.remoteId === nodeId)) ||
                (!nodeId && ip && port && n.ip === ip && n.port === port)
                  ? { ...n, status, last_seen: Date.now() }
                  : n
              ),
              // "Connected To Me" only shows active inbound links; remove if marked offline.
              inboundPeers: state.inboundPeers
                .filter((p: any) => {
                  if (status !== 'offline') return true
                  const pNode = p.node || p
                  return !(
                    (nodeId && pNode?.id === nodeId) ||
                    (!nodeId && ip && port && pNode?.ip === ip && pNode?.port === port)
                  )
                })
                .map((p: any) => {
                  const pNode = p.node || p
                  if ((nodeId && pNode?.id === nodeId) || (!nodeId && ip && port && pNode?.ip === ip && pNode?.port === port)) {
                    return { ...p, node: { ...pNode, status } }
                  }
                  return p
                })
            }))
          }
        }

        if (event.type === 'inbound_peer_connected') {
          const payload = event.data || {}
          set(state => {
            const node = payload.node
            if (!node) return state
            const exists = state.inboundPeers.some((p: any) => (p.node?.id || p.id) === node.id)
            const entry = { node, connected_since: payload.connected_since }
            return {
              inboundPeers: exists
                ? state.inboundPeers.map((p: any) => ((p.node?.id || p.id) === node.id ? entry : p))
                : [entry, ...state.inboundPeers]
            }
          })
        }
      }
    }),
    {
      name: 'teaming24-network',
      partialize: (state) => ({
        nodeName: state.nodeName,
        wanNodes: state.wanNodes, // Persist connected peers across page reloads
        connectionHistory: state.connectionHistory,
        connectionSessions: state.connectionSessions,
        marketplaceListing: state.marketplaceListing, // Persist marketplace listing config
        isDiscoverable: state.isDiscoverable, // Show last LAN Visible state until backend sync
        isDiscovering: state.isDiscovering,  // Persist Scan toggle — backend sync (is_scanning) overrides on load
        // Note: isListedOnMarketplace is NOT persisted - must re-join after restart
      }),
    }
  )
)
