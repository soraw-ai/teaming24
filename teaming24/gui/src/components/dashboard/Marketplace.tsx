import { useEffect, useMemo, useRef, useState } from 'react'
import { Dialog } from '@headlessui/react'
import { useNetworkStore, type NodeInfo } from '../../store/networkStore'
import { useSettingsStore } from '../../store/settingsStore'
import { getApiBase } from '../../utils/api'
import { reportUiError } from '../../utils/errorReporting'
import {
  ArrowPathIcon,
  BellAlertIcon,
  BellSlashIcon,
  ChevronRightIcon,
  ExclamationTriangleIcon,
  MagnifyingGlassIcon,
  ShoppingBagIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline'

type ConnectPhase = 'idle' | 'connecting' | 'handshaking' | 'hired' | 'failed'

interface ConnectFlowState {
  phase: ConnectPhase
  message: string
}

function nodeKey(node: NodeInfo): string {
  return node.remoteId || node.anId || `${node.ip}:${node.port}` || node.id
}

function getErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message
  return 'Unknown error'
}

function getConnectButtonLabel(phase: ConnectPhase, connected: boolean): string {
  if (connected) return 'Connected'
  if (phase === 'connecting') return 'Connecting...'
  if (phase === 'handshaking') return 'Handshaking...'
  if (phase === 'hired') return 'Hired'
  if (phase === 'failed') return 'Retry Connect'
  return 'Connect & Hire'
}

function isTerminalPhase(phase: ConnectPhase): boolean {
  return phase === 'hired' || phase === 'failed'
}

export default function Marketplace() {
  const { fetchMarketplace, connectToWanNode, wanNodes } = useNetworkStore()
  const { agentanetCentralUrl, agentanetToken } = useSettingsStore()
  const [nodes, setNodes] = useState<NodeInfo[]>([])
  const [search, setSearch] = useState('')
  const [activeSearch, setActiveSearch] = useState('')
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useState(true)
  const [autoRefreshMs, setAutoRefreshMs] = useState(5000)
  const [joinSoundEnabled, setJoinSoundEnabled] = useState(false)
  const [localAnId, setLocalAnId] = useState<string | null>(null)
  const [localNodeId, setLocalNodeId] = useState<string | null>(null)
  const [localIpPort, setLocalIpPort] = useState<{ ip: string; port: number } | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [selectedNode, setSelectedNode] = useState<NodeInfo | null>(null)
  const [detailNode, setDetailNode] = useState<NodeInfo | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null)
  const [connectFlowById, setConnectFlowById] = useState<Record<string, ConnectFlowState>>({})
  const [newNodeUntilById, setNewNodeUntilById] = useState<Record<string, number>>({})
  const centralLinked = Boolean((agentanetCentralUrl || '').trim() && (agentanetToken || '').trim())
  const requestSeqRef = useRef(0)
  const detailReqRef = useRef(0)
  const bootstrappedRef = useRef(false)
  const nodesRef = useRef<NodeInfo[]>([])
  const connectTimersRef = useRef<Map<string, number>>(new Map())
  const audioCtxRef = useRef<AudioContext | null>(null)
  const apiBase = getApiBase()

  const playJoinSound = () => {
    if (!joinSoundEnabled) return
    try {
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!Ctx) return
      if (!audioCtxRef.current) {
        audioCtxRef.current = new Ctx()
      }
      const ctx = audioCtxRef.current
      const now = ctx.currentTime

      const gain = ctx.createGain()
      gain.gain.value = 0.04
      gain.connect(ctx.destination)

      const osc = ctx.createOscillator()
      osc.type = 'triangle'
      osc.frequency.setValueAtTime(660, now)
      osc.frequency.linearRampToValueAtTime(880, now + 0.12)
      osc.connect(gain)
      osc.start(now)
      osc.stop(now + 0.14)
    } catch (e) {
      console.warn('[Marketplace] Failed to play join sound:', e)
    }
  }

  const clearConnectTimer = (id: string) => {
    const timer = connectTimersRef.current.get(id)
    if (timer) {
      window.clearTimeout(timer)
      connectTimersRef.current.delete(id)
    }
  }

  const setConnectFlow = (id: string, phase: ConnectPhase, message: string, clearAfterMs?: number) => {
    clearConnectTimer(id)
    setConnectFlowById((prev) => ({ ...prev, [id]: { phase, message } }))
    if (clearAfterMs && clearAfterMs > 0) {
      const timer = window.setTimeout(() => {
        setConnectFlowById((prev) => {
          const next = { ...prev }
          delete next[id]
          return next
        })
        connectTimersRef.current.delete(id)
      }, clearAfterMs)
      connectTimersRef.current.set(id, timer)
    }
  }

  const fetchLocalIdentity = async () => {
    try {
      const res = await fetch(`${apiBase}/api/network/status`)
      if (!res.ok) return
      const data = await res.json()
      const local = data?.local_node || {}
      const anId = typeof local?.an_id === 'string' ? local.an_id.trim() : ''
      const nodeId = typeof local?.id === 'string' ? local.id.trim() : ''
      const ip = typeof local?.ip === 'string' ? local.ip.trim() : ''
      const port = typeof local?.port === 'number' ? local.port : 0
      if (anId) setLocalAnId(anId)
      if (nodeId) setLocalNodeId(nodeId)
      if (ip && port) setLocalIpPort({ ip, port })
    } catch (e) {
      console.warn('[Marketplace] Failed to fetch local identity:', e)
    }
  }

  const shouldExcludeNode = (node: NodeInfo): boolean => {
    if (localAnId && node.anId && node.anId === localAnId) return true
    if (localNodeId && (node.remoteId === localNodeId || node.id === localNodeId)) return true
    if (localIpPort && node.ip === localIpPort.ip && node.port === localIpPort.port) return true
    return false
  }

  const loadMarketplace = async (opts?: { silent?: boolean; searchOverride?: string }) => {
    const silent = Boolean(opts?.silent)
    const searchValue = String(opts?.searchOverride ?? activeSearch).trim()
    const reqSeq = ++requestSeqRef.current
    if (!silent) {
      if (!bootstrappedRef.current) {
        setLoading(true)
      } else {
        setRefreshing(true)
      }
    }
    try {
      const data = await fetchMarketplace(searchValue ? { search: searchValue } : undefined)
      if (reqSeq !== requestSeqRef.current) return
      const filtered = data.filter((node) => !shouldExcludeNode(node))
      const prevNodes = nodesRef.current
      const prevKeys = new Set(prevNodes.map(nodeKey))
      const newNodes = filtered.filter((node) => !prevKeys.has(nodeKey(node)))

      nodesRef.current = filtered
      setNodes(filtered)
      setLastUpdatedAt(Date.now())
      setError(null)

      if (bootstrappedRef.current && newNodes.length > 0) {
        const until = Date.now() + 3000
        setNewNodeUntilById((prev) => {
          const next = { ...prev }
          for (const node of newNodes) {
            next[nodeKey(node)] = until
          }
          return next
        })
        if (document.visibilityState === 'visible') {
          playJoinSound()
        }
      }
    } catch (e) {
      if (reqSeq !== requestSeqRef.current) return
      if (!silent) {
        setNodes([])
        const message = getErrorMessage(e)
        setError(`Failed to load marketplace: ${message}`)
        reportUiError({
          source: 'Marketplace',
          title: 'Marketplace Sync Failed',
          userMessage: `Failed to refresh marketplace (${message}).`,
          error: e,
        })
      }
    } finally {
      if (!silent) {
        bootstrappedRef.current = true
        setLoading(false)
        setRefreshing(false)
      }
    }
  }

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    const query = search.trim()
    setActiveSearch(query)
    await loadMarketplace({ searchOverride: query })
  }

  const handleConnectAndHire = async (node: NodeInfo) => {
    if (!node.ip || !node.port) return
    const key = nodeKey(node)
    setConnectFlow(key, 'connecting', 'Opening secure channel...')
    try {
      await connectToWanNode(node.ip, node.port, '')
      setConnectFlow(key, 'handshaking', 'Verifying node identity and capabilities...')
      try {
        const encodedNodeId = encodeURIComponent(node.remoteId || node.id)
        await fetch(`${apiBase}/api/network/marketplace/node/${encodedNodeId}`)
      } catch (e) {
        console.warn('[Marketplace] Node handshake metadata fetch failed:', e)
      }
      await new Promise((resolve) => window.setTimeout(resolve, 280))
      setConnectFlow(key, 'hired', 'Connection established. Ready to delegate.', 2500)
      void loadMarketplace({ silent: true })
    } catch (e) {
      const message = getErrorMessage(e)
      setConnectFlow(key, 'failed', `Connection failed: ${message}`, 5500)
      reportUiError({
        source: 'Marketplace',
        title: 'Connect & Hire Failed',
        userMessage: `Could not connect to ${node.name} (${message}).`,
        error: e,
      })
    }
  }

  const mergeDetailNode = (base: NodeInfo, raw: any): NodeInfo => {
    const rawId = typeof raw?.id === 'string' ? raw.id : (base.remoteId || base.id)
    const parsedCaps = Array.isArray(raw?.capabilities)
      ? raw.capabilities
          .map((c: any) => ({
            name: String(c?.name || '').trim(),
            description: String(c?.description || '').trim(),
          }))
          .filter((c: { name: string; description: string }) => Boolean(c.name))
      : []
    return {
      ...base,
      name: typeof raw?.name === 'string' && raw.name.trim() ? raw.name : base.name,
      remoteId: rawId,
      anId:
        typeof raw?.an_id === 'string' && raw.an_id.trim()
          ? raw.an_id
          : base.anId || rawId,
      walletAddress:
        typeof raw?.wallet_address === 'string' && raw.wallet_address.trim()
          ? raw.wallet_address
          : base.walletAddress,
      description:
        typeof raw?.description === 'string'
          ? raw.description
          : base.description,
      capability:
        typeof raw?.capability === 'string' && raw.capability.trim()
          ? raw.capability
          : base.capability,
      capabilities: parsedCaps.length > 0 ? parsedCaps : base.capabilities,
      price:
        typeof raw?.price === 'string' && raw.price.trim()
          ? raw.price
          : base.price,
      region:
        typeof raw?.region === 'string' && raw.region.trim()
          ? raw.region
          : base.region,
      ip:
        typeof raw?.ip === 'string' && raw.ip.trim()
          ? raw.ip
          : base.ip,
      port: typeof raw?.port === 'number' ? raw.port : base.port,
      status:
        raw?.status === 'online' || raw?.status === 'busy' || raw?.status === 'offline'
          ? raw.status
          : base.status,
      last_seen:
        typeof raw?.last_seen === 'number' ? raw.last_seen : base.last_seen,
    }
  }

  const openNodeDetail = async (node: NodeInfo) => {
    setDetailOpen(true)
    setSelectedNode(node)
    setDetailNode(node)
    setDetailLoading(true)
    const seq = ++detailReqRef.current

    try {
      const nodeId = encodeURIComponent(node.remoteId || node.id)
      const res = await fetch(`${apiBase}/api/network/marketplace/node/${nodeId}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const payload = await res.json()
      if (seq !== detailReqRef.current) return
      if (payload?.found && payload?.node) {
        setDetailNode(mergeDetailNode(node, payload.node))
      }
    } catch (e) {
      reportUiError({
        source: 'Marketplace',
        title: 'Node Detail Load Failed',
        userMessage: `Failed to load details for "${node.name}".`,
        error: e,
      })
    } finally {
      if (seq === detailReqRef.current) {
        setDetailLoading(false)
      }
    }
  }

  const closeDetail = () => {
    setDetailOpen(false)
    setSelectedNode(null)
    setDetailNode(null)
    setDetailLoading(false)
  }

  useEffect(() => {
    void fetchLocalIdentity()
    void loadMarketplace()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!bootstrappedRef.current) return
    void loadMarketplace({ silent: true })
  }, [localAnId, localNodeId, localIpPort])

  useEffect(() => {
    if (!autoRefreshEnabled) return
    const interval = window.setInterval(() => {
      if (document.visibilityState !== 'visible') return
      void loadMarketplace({ silent: true })
    }, autoRefreshMs)
    return () => window.clearInterval(interval)
  }, [autoRefreshEnabled, autoRefreshMs, activeSearch])

  useEffect(() => {
    const pruneInterval = window.setInterval(() => {
      const now = Date.now()
      setNewNodeUntilById((prev) => {
        let changed = false
        const next: Record<string, number> = {}
        for (const [key, until] of Object.entries(prev)) {
          if (until > now) {
            next[key] = until
          } else {
            changed = true
          }
        }
        return changed ? next : prev
      })
    }, 1000)
    return () => window.clearInterval(pruneInterval)
  }, [])

  useEffect(() => {
    if (!detailOpen || !selectedNode) return
    const selectedKey = nodeKey(selectedNode)
    const match = nodes.find((node) => nodeKey(node) === selectedKey)
    if (match) setSelectedNode(match)
  }, [detailOpen, selectedNode, nodes])

  useEffect(() => {
    return () => {
      for (const timer of connectTimersRef.current.values()) {
        window.clearTimeout(timer)
      }
      connectTimersRef.current.clear()
      if (audioCtxRef.current) {
        void audioCtxRef.current.close().catch(() => {})
      }
    }
  }, [])

  const activeDetailNode = detailNode || selectedNode
  const detailCapabilities = (
    activeDetailNode?.capabilities && activeDetailNode.capabilities.length > 0
      ? activeDetailNode.capabilities
      : (activeDetailNode?.capability ? [{ name: activeDetailNode.capability, description: '' }] : [])
  )
  const detailIsConnected = Boolean(
    activeDetailNode && wanNodes.some(
      n => n.ip === activeDetailNode.ip && n.port === activeDetailNode.port && n.status !== 'offline'
    )
  )
  const detailCanConnect = Boolean(activeDetailNode?.ip && activeDetailNode?.port)
  const activeDetailKey = activeDetailNode ? nodeKey(activeDetailNode) : null
  const detailFlow = activeDetailKey ? connectFlowById[activeDetailKey] : undefined
  const connectedCount = useMemo(
    () => wanNodes.filter((node) => node.status !== 'offline').length,
    [wanNodes],
  )

  return (
    <div className="h-full min-h-0 flex flex-col bg-dark-bg overflow-x-hidden">
      <div className="p-4 border-b border-dark-border flex flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-2 min-w-0">
            <ShoppingBagIcon className="w-5 h-5 text-primary-400 mt-0.5 shrink-0" />
            <div className="min-w-0">
              <h2 className="text-lg font-semibold text-white truncate">Agentic Node Marketplace</h2>
              <p className="text-xs text-gray-500">
                Browse nodes and hire directly
                <span className="ml-2 text-gray-600">
                  {lastUpdatedAt ? `Last sync ${new Date(lastUpdatedAt).toLocaleTimeString()}` : 'Not synced yet'}
                </span>
              </p>
            </div>
          </div>

          <div className="flex items-center gap-1.5 text-[11px]">
            <span className="px-2 py-1 rounded-full bg-dark-surface border border-dark-border text-gray-300">
              {nodes.length} nodes
            </span>
            <span className="px-2 py-1 rounded-full bg-dark-surface border border-dark-border text-gray-400">
              connected {connectedCount}
            </span>
            <span className="px-2 py-1 rounded-full bg-dark-surface border border-dark-border text-gray-500">
              {autoRefreshEnabled ? `auto ${Math.round(autoRefreshMs / 1000)}s` : 'manual'}
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <form onSubmit={handleSearch} className="relative min-w-[220px] flex-1">
            <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full pl-9 pr-4 py-2 bg-dark-surface border border-dark-border rounded-lg text-sm text-white focus:border-primary-500 outline-none placeholder-gray-500"
              placeholder="Search agentic nodes..."
            />
          </form>

          <button
            type="button"
            onClick={() => void loadMarketplace()}
            className="shrink-0 p-2 bg-dark-surface border border-dark-border rounded-lg text-gray-300 hover:text-white hover:border-primary-500/50 transition-colors"
            title="Refresh marketplace"
            aria-label="Refresh marketplace"
          >
            <ArrowPathIcon className={`w-4 h-4 ${(refreshing || loading) ? 'animate-spin text-primary-400' : ''}`} />
          </button>

          <button
            type="button"
            onClick={() => setAutoRefreshEnabled((v) => !v)}
            className={`shrink-0 px-2.5 py-2 text-xs rounded-lg border transition-colors ${
              autoRefreshEnabled
                ? 'bg-primary-500/15 border-primary-500/30 text-primary-300'
                : 'bg-dark-surface border-dark-border text-gray-400 hover:text-gray-200'
            }`}
            title="Toggle auto refresh"
          >
            Auto
          </button>

          <select
            value={autoRefreshMs}
            onChange={(e) => setAutoRefreshMs(Number(e.target.value))}
            disabled={!autoRefreshEnabled}
            className="shrink-0 px-2 py-2 text-xs rounded-lg border border-dark-border bg-dark-surface text-gray-300 disabled:opacity-40"
            aria-label="Auto refresh interval"
          >
            <option value={5000}>5s</option>
            <option value={15000}>15s</option>
            <option value={30000}>30s</option>
          </select>

          <button
            type="button"
            onClick={() => setJoinSoundEnabled((v) => !v)}
            className={`shrink-0 p-2 rounded-lg border transition-colors ${
              joinSoundEnabled
                ? 'bg-emerald-500/15 border-emerald-500/30 text-emerald-300'
                : 'bg-dark-surface border-dark-border text-gray-400 hover:text-gray-200'
            }`}
            title={joinSoundEnabled ? 'Join sound on' : 'Join sound off'}
          >
            {joinSoundEnabled ? <BellAlertIcon className="w-4 h-4" /> : <BellSlashIcon className="w-4 h-4" />}
          </button>

        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 sm:p-5">
        {!centralLinked && nodes.length > 0 && (
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
            <ExclamationTriangleIcon className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="break-words">
              Register an API key on AgentaNet Central first, then configure it in local Settings to enable Connect.
            </span>
          </div>
        )}
        {error && (
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            <ExclamationTriangleIcon className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="break-words">{error}</span>
          </div>
        )}
        {loading ? (
          <div className="flex justify-center py-10 text-gray-500">Loading...</div>
        ) : nodes.length === 0 ? (
          <div className="max-w-xl mx-auto flex flex-col items-center py-10 text-gray-500 gap-2 text-center">
            <div className="text-gray-400">No agentic nodes found</div>
            {!centralLinked && (
              <div className="text-xs text-gray-600 leading-relaxed">
                Search uses Central URL from config. Link Token in Settings to list your node.
              </div>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {nodes.map((node, index) => {
              const key = nodeKey(node)
              const isConnected = wanNodes.some(
                n => n.ip === node.ip && n.port === node.port && n.status !== 'offline'
              )
              const connectFlow = connectFlowById[key]
              const phase = connectFlow?.phase || 'idle'
              const isBusyConnect = phase === 'connecting' || phase === 'handshaking'
              const canConnect = Boolean(node.ip && node.port)
              const capabilityNames = (node.capabilities || [])
                .map(c => String(c?.name || '').trim())
                .filter(Boolean)
              const visibleCapabilities = capabilityNames.slice(0, 3)
              const overflowCapabilityCount = Math.max(0, capabilityNames.length - visibleCapabilities.length)
              const isFresh = (newNodeUntilById[key] || 0) > Date.now()

              return (
                <div
                  key={key}
                  className={`bg-dark-surface border rounded-xl p-4 transition-colors group h-full flex flex-col gap-3 cursor-pointer marketplace-card-in motion-reduce:animate-none ${
                    isFresh
                      ? 'border-emerald-500/60 marketplace-new-node'
                      : 'border-dark-border hover:border-primary-500/50'
                  }`}
                  role="button"
                  tabIndex={0}
                  onClick={() => { void openNodeDetail(node) }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      void openNodeDetail(node)
                    }
                  }}
                  style={{ animationDelay: `${Math.min(index, 12) * 35}ms` }}
                >
                  <div className="flex justify-between items-start gap-2">
                    <h3
                      className="font-medium text-white group-hover:text-primary-400 transition-colors truncate min-w-0"
                      title={node.name}
                    >
                      {node.name}
                    </h3>
                    <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                      node.status === 'online' ? 'bg-green-500/10 text-green-400'
                        : node.status === 'busy' ? 'bg-yellow-500/10 text-yellow-400'
                          : 'bg-gray-700 text-gray-400'
                    }`}>
                      {node.status.toUpperCase()}
                    </span>
                  </div>

                  <div className="grid grid-cols-[72px,minmax(0,1fr)] gap-y-2 gap-x-2 text-sm text-gray-400">
                    <span>AN ID:</span>
                    <span className="text-gray-300 font-mono text-xs sm:text-sm truncate text-right" title={node.anId || node.remoteId || node.id}>
                      {node.anId || node.remoteId || node.id}
                    </span>

                    <span>Capability:</span>
                    <span className="text-gray-300 truncate text-right" title={node.capability || '-'}>
                      {node.capability || '-'}
                    </span>

                    <span>Price:</span>
                    <span className="text-primary-400 font-mono truncate text-right" title={node.price || 'Free'}>
                      {node.price || 'Free'}
                    </span>
                  </div>

                  {visibleCapabilities.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {visibleCapabilities.map(cap => (
                        <span
                          key={cap}
                          title={cap}
                          className="px-2 py-0.5 rounded bg-dark-bg border border-dark-border text-xs text-gray-300 max-w-full truncate"
                        >
                          {cap}
                        </span>
                      ))}
                      {overflowCapabilityCount > 0 && (
                        <span className="px-2 py-0.5 rounded bg-primary-500/10 border border-primary-500/20 text-xs text-primary-300">
                          +{overflowCapabilityCount}
                        </span>
                      )}
                    </div>
                  )}

                  {node.description && (
                    <p className="text-xs text-gray-500 leading-relaxed line-clamp-2 break-words" title={node.description}>
                      {node.description}
                    </p>
                  )}

                  <div className="mt-auto pt-1">
                    <button
                      disabled={!canConnect || isBusyConnect || isConnected || phase === 'hired'}
                      onClick={(e) => {
                        e.stopPropagation()
                        void handleConnectAndHire(node)
                      }}
                      className={`w-full py-2 rounded-lg text-sm font-medium transition-colors border disabled:opacity-50 disabled:cursor-not-allowed ${
                        phase === 'failed'
                          ? 'bg-red-600/10 hover:bg-red-600/20 text-red-300 border-red-600/30 hover:border-red-600/40'
                          : 'bg-primary-600/10 hover:bg-primary-600/20 text-primary-400 border-primary-600/20 hover:border-primary-600/40'
                      }`}
                    >
                      {getConnectButtonLabel(phase, isConnected)}
                    </button>
                    {connectFlow?.message && (
                      <p className={`mt-1 text-[10px] leading-relaxed ${
                        phase === 'failed' ? 'text-red-300' : isTerminalPhase(phase) ? 'text-green-300' : 'text-gray-500'
                      }`}>
                        {connectFlow.message}
                      </p>
                    )}
                    <button
                      type="button"
                      className="mt-1.5 text-[11px] text-gray-500 hover:text-gray-300 transition-colors inline-flex items-center gap-1"
                      onClick={(e) => {
                        e.stopPropagation()
                        void openNodeDetail(node)
                      }}
                    >
                      View details <ChevronRightIcon className="w-3 h-3" />
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <Dialog open={detailOpen} onClose={closeDetail} className="relative z-[999999]">
        <div className="fixed inset-0 bg-black/70" aria-hidden="true" />
        <div className="fixed inset-0 flex justify-end">
          <Dialog.Panel className="h-full w-full sm:w-[520px] bg-dark-surface border-l border-dark-border shadow-2xl flex flex-col marketplace-drawer-in motion-reduce:animate-none">
            <div className="px-5 sm:px-6 py-4 border-b border-dark-border flex items-start justify-between gap-3">
              <div className="min-w-0">
                <Dialog.Title className="text-lg font-semibold text-white truncate" title={activeDetailNode?.name || 'Agentic Node'}>
                  {activeDetailNode?.name || 'Agentic Node'}
                </Dialog.Title>
                <p className="text-xs text-gray-500 mt-1">Detailed profile and capability overview</p>
              </div>
              <button
                onClick={closeDetail}
                className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-dark-hover transition-colors"
                aria-label="Close details"
              >
                <XMarkIcon className="w-5 h-5" />
              </button>
            </div>

            <div className="p-5 sm:p-6 overflow-y-auto space-y-4 flex-1">
              {detailLoading && (
                <div className="text-xs text-primary-300 bg-primary-500/10 border border-primary-500/20 rounded-lg px-3 py-2">
                  Loading latest node details...
                </div>
              )}

              {detailFlow && (
                <div className="rounded-lg border border-dark-border bg-dark-bg p-3">
                  <div className="text-xs text-gray-500 mb-2">Connect & Hire status</div>
                  <div className="flex items-center gap-2 mb-2">
                    {(['connecting', 'handshaking', 'hired'] as ConnectPhase[]).map((step) => {
                      const active = detailFlow.phase === step
                      const done = detailFlow.phase === 'hired' || (detailFlow.phase === 'handshaking' && step === 'connecting')
                      return (
                        <span
                          key={step}
                          className={`px-2 py-0.5 rounded text-[10px] border ${
                            active ? 'border-primary-500/40 bg-primary-500/15 text-primary-300'
                              : done ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                                : 'border-dark-border bg-dark-surface text-gray-500'
                          }`}
                        >
                          {step}
                        </span>
                      )
                    })}
                  </div>
                  <p className={`text-xs ${detailFlow.phase === 'failed' ? 'text-red-300' : 'text-gray-400'}`}>
                    {detailFlow.message}
                  </p>
                </div>
              )}

              {activeDetailNode && (
                <>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
                    <div className="bg-dark-bg border border-dark-border rounded-lg p-3 min-w-0">
                      <div className="text-xs text-gray-500 mb-1">AN ID</div>
                      <div className="font-mono text-gray-200 break-all">
                        {activeDetailNode.anId || activeDetailNode.remoteId || activeDetailNode.id}
                      </div>
                    </div>
                    <div className="bg-dark-bg border border-dark-border rounded-lg p-3 min-w-0">
                      <div className="text-xs text-gray-500 mb-1">Status</div>
                      <div className={`font-medium ${
                        activeDetailNode.status === 'online'
                          ? 'text-green-400'
                          : activeDetailNode.status === 'busy'
                            ? 'text-yellow-400'
                            : 'text-gray-400'
                      }`}>
                        {activeDetailNode.status.toUpperCase()}
                      </div>
                    </div>
                    <div className="bg-dark-bg border border-dark-border rounded-lg p-3 min-w-0">
                      <div className="text-xs text-gray-500 mb-1">Endpoint</div>
                      <div className="font-mono text-gray-200 break-all">
                        {activeDetailNode.ip && activeDetailNode.port ? `${activeDetailNode.ip}:${activeDetailNode.port}` : '-'}
                      </div>
                    </div>
                    <div className="bg-dark-bg border border-dark-border rounded-lg p-3 min-w-0">
                      <div className="text-xs text-gray-500 mb-1">Region / Price</div>
                      <div className="text-gray-200 break-words">
                        {(activeDetailNode.region || 'Unknown')} / {activeDetailNode.price || 'Free'}
                      </div>
                    </div>
                  </div>

                  <div className="bg-dark-bg border border-dark-border rounded-lg p-3">
                    <div className="text-xs text-gray-500 mb-2">Description</div>
                    <p className="text-sm text-gray-200 leading-relaxed whitespace-pre-wrap break-words">
                      {activeDetailNode.description?.trim() || 'No description provided.'}
                    </p>
                  </div>

                  <div className="bg-dark-bg border border-dark-border rounded-lg p-3">
                    <div className="text-xs text-gray-500 mb-2">Capabilities ({detailCapabilities.length})</div>
                    {detailCapabilities.length > 0 ? (
                      <div className="space-y-2">
                        {detailCapabilities.map(cap => (
                          <div key={`${cap.name}-${cap.description}`} className="rounded-md border border-dark-border bg-dark-surface px-3 py-2">
                            <div className="text-sm text-white break-words">{cap.name}</div>
                            {cap.description && (
                              <div className="text-xs text-gray-500 mt-1 break-words">{cap.description}</div>
                            )}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-xs text-gray-500">No capabilities declared.</div>
                    )}
                  </div>
                </>
              )}
            </div>

            <div className="px-5 sm:px-6 py-4 border-t border-dark-border flex flex-col sm:flex-row items-stretch sm:items-center justify-end gap-2 sm:gap-3">
              <button
                onClick={closeDetail}
                className="px-4 py-2 bg-dark-hover hover:bg-dark-border text-gray-200 rounded-lg text-sm transition-colors"
              >
                Close
              </button>
              <button
                disabled={!activeDetailNode || !detailCanConnect || detailIsConnected || detailFlow?.phase === 'connecting' || detailFlow?.phase === 'handshaking' || detailFlow?.phase === 'hired'}
                onClick={() => { if (activeDetailNode) void handleConnectAndHire(activeDetailNode) }}
                className={`px-4 py-2 border rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                  detailFlow?.phase === 'failed'
                    ? 'bg-red-600/20 hover:bg-red-600/30 border-red-600/30 text-red-300'
                    : 'bg-primary-600/20 hover:bg-primary-600/30 border-primary-600/30 text-primary-300'
                }`}
              >
                {getConnectButtonLabel(detailFlow?.phase || 'idle', detailIsConnected)}
              </button>
            </div>
          </Dialog.Panel>
        </div>
      </Dialog>

    </div>
  )
}
