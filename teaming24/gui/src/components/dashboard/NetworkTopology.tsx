import { useEffect, useRef, useState, useCallback } from 'react'
import { useAgentStore, type Agent } from '../../store/agentStore'
import { useConfigStore } from '../../store/configStore'
import { useNetworkStore, type NodeInfo, getNodeDisplayName } from '../../store/networkStore'
import { DEFAULT_LOCAL_NODE_DESCRIPTION } from '../../constants/node'
import { ORGANIZER_ID, COORDINATOR_ID, LOCAL_COORDINATOR_NAME } from '../../utils/ids'
import { truncateId } from '../../utils/strings'
import { 
  MagnifyingGlassPlusIcon, 
  MagnifyingGlassMinusIcon,
  ArrowsPointingOutIcon,
  ArrowPathIcon,
  SignalSlashIcon,
} from '@heroicons/react/24/outline'

interface DraggableNode {
  id: string
  x: number
  y: number
  type: 'local-an' | 'remote-an'
  data: {
    name: string
    anId?: string            // Canonical AN identifier (wallet-suffix)
    status: string
    ip?: string
    port?: number
    type?: 'lan' | 'wan'
    capability?: string
    description?: string
    direction?: 'outbound' | 'inbound' | 'bidirectional'  // Connection direction relative to local node
  }
}

interface TooltipInfo {
  x: number
  y: number
  node: DraggableNode
  nodeType: 'local-an' | 'remote-an' | 'organizer' | 'coordinator' | 'worker'
  agentData?: Agent
  anData?: NodeInfo
}

type ConnectionDirection = 'outbound' | 'inbound' | 'bidirectional'

function endpointKey(ip?: string, port?: number): string | null {
  const cleanIp = typeof ip === 'string' ? ip.trim() : ''
  if (!cleanIp || typeof port !== 'number' || !Number.isFinite(port) || port <= 0) {
    return null
  }
  return `${cleanIp}:${port}`
}

function buildConnectionDirectionMap(wanNodes: NodeInfo[], inboundPeers: any[]): Map<string, ConnectionDirection> {
  const outbound = new Set<string>()
  const inbound = new Set<string>()

  for (const node of wanNodes) {
    if (node.status === 'offline') continue
    const key = endpointKey(node.ip, node.port)
    if (key) outbound.add(key)
  }

  for (const entry of inboundPeers) {
    const peerNode = entry?.node || entry
    if (!peerNode || peerNode.status === 'offline') continue
    const key = endpointKey(peerNode.ip, peerNode.port)
    if (key) inbound.add(key)
  }

  const snapshot = new Map<string, ConnectionDirection>()
  const endpoints = new Set([...outbound, ...inbound])
  for (const key of endpoints) {
    const hasOut = outbound.has(key)
    const hasIn = inbound.has(key)
    if (hasOut && hasIn) {
      snapshot.set(key, 'bidirectional')
    } else if (hasOut) {
      snapshot.set(key, 'outbound')
    } else {
      snapshot.set(key, 'inbound')
    }
  }
  return snapshot
}

export default function NetworkTopology() {
  const { agents, messages } = useAgentStore()
  const { localAnHost, localAnPort, localAnName } = useConfigStore()
  const { status: networkStatus, lanNodes, wanNodes, inboundPeers } = useNetworkStore()
  
  // Live local node info (capabilities aggregated from workers)
  const [localNodeInfo, setLocalNodeInfo] = useState<{
    description: string
    capabilities: { name: string; description: string }[]
  } | null>(null)

  // Fetch live local node info from backend
  useEffect(() => {
    let cancelled = false
    const fetchInfo = async () => {
      try {
        const res = await fetch('/api/network/status')
        if (!res.ok) return
        const data = await res.json()
        if (!cancelled && data.local_node) {
          setLocalNodeInfo({
            description: data.local_node.description || '',
            capabilities: data.local_node.capabilities || [],
          })
        }
      } catch (e) { console.error('NetworkTopology: failed to fetch local node info:', e) }
    }
    fetchInfo()
    // Re-fetch periodically to pick up pool changes
    const interval = setInterval(fetchInfo, 15000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [])

  // Check if we're connected to AgentaNet
  const isNetworkOnline = networkStatus === 'online'
  const containerRef = useRef<HTMLDivElement>(null)
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 })
  const [nodes, setNodes] = useState<DraggableNode[]>([])
  
  // Dragging state
  const [dragging, setDragging] = useState<string | null>(null)
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 })
  const [hoveredNode, setHoveredNode] = useState<string | null>(null)
  
  // Tooltip state
  const [tooltip, setTooltip] = useState<TooltipInfo | null>(null)
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 })
  
  // Pan and Zoom state
  const [scale, setScale] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [isPanning, setIsPanning] = useState(false)
  const [panStart, setPanStart] = useState({ x: 0, y: 0 })
  const [pulseUntilByNodeId, setPulseUntilByNodeId] = useState<Record<string, number>>({})
  const topologySnapshotRef = useRef<Map<string, ConnectionDirection>>(new Map())

  const organizers = agents.filter(a => a.type === 'organizer')
  const coordinators = agents.filter(a => a.type === 'coordinator')
  const workers = agents.filter(a => a.type === 'worker')

  // Initialize and update node positions
  // This effect merges new nodes while preserving positions of existing ones
  useEffect(() => {
    if (dimensions.width === 0 || dimensions.height === 0) return
    
    const centerX = dimensions.width / 2
    const centerY = dimensions.height / 2

    setNodes(prevNodes => {
      // Create a map of existing node positions
      const existingPositions = new Map<string, { x: number; y: number }>()
      prevNodes.forEach(node => {
        existingPositions.set(node.id, { x: node.x, y: node.y })
      })
      
      const newNodes: DraggableNode[] = []

      // Local AN (center-left) - keep position if exists
      const localAnPos = existingPositions.get('local-an')
      newNodes.push({
        id: 'local-an',
        x: localAnPos?.x ?? centerX - 150,
        y: localAnPos?.y ?? centerY,
        type: 'local-an',
        data: {
          name: localAnName || 'Local AN',
          status: agents.some(a => a.status !== 'offline') ? 'online' : 'offline',
          ip: localAnHost,
          port: localAnPort,
          description: 'Local Agentic Node hosting Organizer, Coordinator, and Workers',
        }
      })

      // Merge outbound (WAN) and inbound peers into a single set of connected nodes.
      // When a remote AN exists in BOTH wanNodes and inboundPeers, mark as 'bidirectional'.
      const outboundEndpoints = new Map<string, NodeInfo>()
      const inboundEndpoints = new Map<string, NodeInfo>()

      // Collect outbound connections (we connected to them)
      for (const n of wanNodes) {
        outboundEndpoints.set(`${n.ip}:${n.port}`, n)
      }

      // Collect inbound connections (they connected to us)
      for (const p of inboundPeers) {
        const peerNode = p.node || p
        if (!peerNode || !peerNode.ip) continue
        const asNodeInfo: NodeInfo = {
          id: peerNode.id,
          name: peerNode.name || 'Unknown',
          ip: peerNode.ip,
          port: peerNode.port,
          role: peerNode.role || 'manager',
          status: peerNode.status || 'online',
          last_seen: peerNode.last_seen || Date.now(),
          type: 'wan',
          capability: peerNode.capability,
          capabilities: peerNode.capabilities,
          description: peerNode.description,
          region: peerNode.region,
          walletAddress: peerNode.wallet_address || peerNode.walletAddress,
          agentId: peerNode.agent_id || peerNode.agentId,
        }
        inboundEndpoints.set(`${peerNode.ip}:${peerNode.port}`, asNodeInfo)
      }

      // Determine direction for each unique endpoint
      const allEndpoints = new Set([...outboundEndpoints.keys(), ...inboundEndpoints.keys()])
      const connectedEntries: { node: NodeInfo; direction: 'outbound' | 'inbound' | 'bidirectional' }[] = []

      for (const key of allEndpoints) {
        const outNode = outboundEndpoints.get(key)
        const inNode = inboundEndpoints.get(key)
        const isBoth = !!outNode && !!inNode

        if (isBoth) {
          // Prefer outbound NodeInfo (richer data from our connect flow)
          connectedEntries.push({ node: outNode, direction: 'bidirectional' })
        } else if (outNode) {
          connectedEntries.push({ node: outNode, direction: 'outbound' })
        } else if (inNode) {
          connectedEntries.push({ node: inNode, direction: 'inbound' })
        }
      }

      const placeInRegion = (entry: { node: NodeInfo; direction: 'outbound' | 'inbound' | 'bidirectional' }, i: number, total: number, regionCenterX: number, regionCenterY: number) => {
        const { node, direction } = entry
        const existingPos = existingPositions.get(node.id)
        
        let x: number, y: number
        if (existingPos) {
          x = existingPos.x
          y = existingPos.y
        } else {
          const radius = 110
          const angle = total <= 1 ? 0 : (i / total) * Math.PI * 2
          x = regionCenterX + Math.cos(angle) * radius
          y = regionCenterY + Math.sin(angle) * radius
        }
        
        newNodes.push({
          id: node.id,
          x,
          y,
          type: 'remote-an',
          data: {
            name: getNodeDisplayName(node),
            anId: node.anId || node.remoteId,
            status: node.status,
            ip: node.ip,
            port: node.port,
            type: node.type,
            capability: node.capability,
            description: `Remote ${(node.type || 'wan').toUpperCase()} Node`,
            direction,
          }
        })
      }

      // Place connected nodes to the right of the local AN
      const regionX = centerX + 200
      connectedEntries.forEach((entry, i) => placeInRegion(entry, i, connectedEntries.length, regionX, centerY))

      return newNodes
    })
  }, [dimensions, wanNodes, inboundPeers, agents, localAnHost, localAnPort, localAnName])

  // Update dimensions with ResizeObserver for accurate sizing
  useEffect(() => {
    const updateDimensions = () => {
      if (containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect()
        setDimensions({
          width: rect.width,
          height: rect.height
        })
      }
    }
    
    updateDimensions()
    
    const resizeObserver = new ResizeObserver(() => {
      updateDimensions()
    })
    
    if (containerRef.current) {
      resizeObserver.observe(containerRef.current)
    }
    
    window.addEventListener('resize', updateDimensions)
    const timeoutId = setTimeout(updateDimensions, 100)
    
    return () => {
      resizeObserver.disconnect()
      window.removeEventListener('resize', updateDimensions)
      clearTimeout(timeoutId)
    }
  }, [])

  // Convert screen coordinates to canvas coordinates
  const screenToCanvas = useCallback((screenX: number, screenY: number) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return { x: 0, y: 0 }
    return {
      x: (screenX - rect.left - pan.x) / scale,
      y: (screenY - rect.top - pan.y) / scale
    }
  }, [pan, scale])

  // Wheel zoom: use a native non-passive listener so preventDefault works without console warnings.
  const scaleRef = useRef(scale)
  const panRef = useRef(pan)
  useEffect(() => {
    scaleRef.current = scale
  }, [scale])
  useEffect(() => {
    panRef.current = pan
  }, [pan])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = el.getBoundingClientRect()

      const mouseX = e.clientX - rect.left
      const mouseY = e.clientY - rect.top

      const currentScale = scaleRef.current
      const currentPan = panRef.current

      const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9
      const nextScale = Math.min(Math.max(0.2, currentScale * zoomFactor), 3)

      const scaleDiff = nextScale - currentScale
      const nextPanX = currentPan.x - (mouseX - currentPan.x) * (scaleDiff / currentScale)
      const nextPanY = currentPan.y - (mouseY - currentPan.y) * (scaleDiff / currentScale)

      setScale(nextScale)
      setPan({ x: nextPanX, y: nextPanY })
    }

    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  // Pan handlers
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    const target = e.target as SVGElement
    const nodeGroup = target.closest('[data-node-id]')
    
    if (nodeGroup) {
      const nodeId = nodeGroup.getAttribute('data-node-id')!
      const node = nodes.find(n => n.id === nodeId)
      if (!node) return

      const canvasPos = screenToCanvas(e.clientX, e.clientY)
      setDragging(nodeId)
      setDragOffset({
        x: canvasPos.x - node.x,
        y: canvasPos.y - node.y
      })
    } else {
      setIsPanning(true)
      setPanStart({ x: e.clientX - pan.x, y: e.clientY - pan.y })
    }
  }, [nodes, pan, screenToCanvas])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (rect) {
      setMousePos({ x: e.clientX - rect.left, y: e.clientY - rect.top })
    }

    if (isPanning) {
      setPan({
        x: e.clientX - panStart.x,
        y: e.clientY - panStart.y
      })
    } else if (dragging) {
      const canvasPos = screenToCanvas(e.clientX, e.clientY)
      setNodes(prev => prev.map(node =>
        node.id === dragging 
          ? { ...node, x: canvasPos.x - dragOffset.x, y: canvasPos.y - dragOffset.y } 
          : node
      ))
    }
  }, [isPanning, panStart, dragging, dragOffset, screenToCanvas])

  const handleMouseUp = useCallback(() => {
    setIsPanning(false)
    setDragging(null)
  }, [])

  // Tooltip handlers
  const handleNodeMouseEnter = useCallback((nodeId: string, nodeType: TooltipInfo['nodeType'], e: React.MouseEvent) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return

    const node = nodes.find(n => n.id === nodeId)
    if (!node && nodeType !== 'organizer' && nodeType !== 'coordinator' && nodeType !== 'worker') return

    let agentData: Agent | undefined
    let anData: NodeInfo | undefined

    if (nodeType === 'organizer') {
      agentData = organizers[0]
    } else if (nodeType === 'coordinator') {
      agentData = coordinators[0]
    } else if (nodeType === 'worker') {
      // Find specific worker by ID for per-worker tooltip
      agentData = workers.find(w => w.id === nodeId) || workers[0]
    } else if (nodeType === 'remote-an') {
      // Check outbound WAN nodes, then inbound peers, then LAN nodes
      anData = wanNodes.find(n => n.id === nodeId)
      if (!anData) {
        const inboundEntry = inboundPeers.find((p: any) => (p.node?.id || p.id) === nodeId)
        if (inboundEntry) {
          anData = inboundEntry.node || inboundEntry
        }
      }
      if (!anData) {
        anData = lanNodes.find(n => n.id === nodeId)
      }
    }

    setTooltip({
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
      node: node || {
        id: nodeId,
        x: 0,
        y: 0,
        type: 'local-an',
        data: { name: '', status: '' }
      },
      nodeType,
      agentData,
      anData,
    })
    setHoveredNode(nodeId)
  }, [nodes, organizers, coordinators, workers, lanNodes, wanNodes, inboundPeers])

  const handleNodeMouseLeave = useCallback(() => {
    setTooltip(null)
    setHoveredNode(null)
  }, [])

  // Zoom controls
  const zoomIn = () => setScale(s => Math.min(3, s * 1.2))
  const zoomOut = () => setScale(s => Math.max(0.2, s / 1.2))
  const resetView = () => {
    setScale(1)
    setPan({ x: 0, y: 0 })
  }
  const toggleFullscreen = async () => {
    const el = containerRef.current
    if (!el) return
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen()
      } else {
        await el.requestFullscreen()
      }
    } catch (e) { console.error('NetworkTopology: fullscreen toggle failed:', e) }
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'online': return '#22c55e'
      case 'busy': return '#eab308'
      case 'error': return '#ef4444'
      case 'idle': return '#3b82f6'
      default: return '#6b7280'
    }
  }

  const localNode = nodes.find(n => n.id === 'local-an')
  const remoteNodesList = nodes.filter(n => n.type === 'remote-an')
  const recentMessages = messages.slice(-20)
  const hasActiveMessages = recentMessages.some(m => Date.now() - m.timestamp < 8000)
  const isLocalOnline = agents.some(a => a.status !== 'offline')
  const isNodePulsing = (nodeId: string) => (pulseUntilByNodeId[nodeId] || 0) > Date.now()

  const pulseTopologyNodes = useCallback((nodeIds: string[]) => {
    if (nodeIds.length === 0) return
    const expiresAt = Date.now() + 1400
    setPulseUntilByNodeId(prev => {
      const next = { ...prev }
      for (const id of nodeIds) {
        next[id] = expiresAt
      }
      return next
    })
  }, [])

  useEffect(() => {
    if (Object.keys(pulseUntilByNodeId).length === 0) return
    const now = Date.now()
    const earliestExpiry = Math.min(...Object.values(pulseUntilByNodeId))
    const delay = Math.max(20, earliestExpiry - now + 8)
    const timer = window.setTimeout(() => {
      const ts = Date.now()
      setPulseUntilByNodeId(prev => {
        const next: Record<string, number> = {}
        for (const [nodeId, expiresAt] of Object.entries(prev)) {
          if (expiresAt > ts) next[nodeId] = expiresAt
        }
        return next
      })
    }, delay)
    return () => window.clearTimeout(timer)
  }, [pulseUntilByNodeId])

  useEffect(() => {
    const nextSnapshot = buildConnectionDirectionMap(wanNodes, inboundPeers)
    const prevSnapshot = topologySnapshotRef.current
    const changedEndpoints = new Set<string>()

    if (prevSnapshot.size > 0) {
      for (const [endpoint, direction] of nextSnapshot.entries()) {
        if (prevSnapshot.get(endpoint) !== direction) {
          changedEndpoints.add(endpoint)
        }
      }
      for (const endpoint of prevSnapshot.keys()) {
        if (!nextSnapshot.has(endpoint)) {
          changedEndpoints.add(endpoint)
        }
      }
    }

    topologySnapshotRef.current = nextSnapshot
    if (changedEndpoints.size === 0) return

    const idsToPulse = new Set<string>(['local-an'])
    for (const endpoint of changedEndpoints) {
      const matched = nodes.find(
        n => n.type === 'remote-an' && endpointKey(n.data.ip, n.data.port) === endpoint
      )
      if (matched) idsToPulse.add(matched.id)
    }
    pulseTopologyNodes(Array.from(idsToPulse))
  }, [wanNodes, inboundPeers, nodes, pulseTopologyNodes])

  return (
    <div 
      ref={containerRef} 
      className="relative w-full h-full min-h-[300px] overflow-hidden bg-[#0a0a0a]"
      style={{ 
        cursor: isPanning ? 'grabbing' : dragging ? 'grabbing' : 'grab',
        height: '100%',
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={() => { handleMouseUp(); handleNodeMouseLeave(); }}
    >
      {/* Grid background */}
      <svg width="100%" height="100%" className="absolute inset-0 pointer-events-none">
        <defs>
          <pattern id="grid" width={50 * scale} height={50 * scale} patternUnits="userSpaceOnUse"
            x={pan.x % (50 * scale)} y={pan.y % (50 * scale)}>
            <path d={`M ${50 * scale} 0 L 0 0 0 ${50 * scale}`} fill="none" stroke="#1a1a1a" strokeWidth="1"/>
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid)" />
      </svg>

      {/* Main canvas */}
      <svg 
        width="100%" 
        height="100%" 
        className="absolute inset-0"
        style={{ pointerEvents: 'none' }}
      >
        <defs>
          <linearGradient id="connectionGrad" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="#0ea5e9" />
            <stop offset="100%" stopColor="#f97316" />
          </linearGradient>
          <linearGradient id="connectionGradInbound" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="#06b6d4" />
            <stop offset="100%" stopColor="#0ea5e9" />
          </linearGradient>
          <filter id="glow">
            <feGaussianBlur stdDeviation="3" result="coloredBlur"/>
            <feMerge>
              <feMergeNode in="coloredBlur"/>
              <feMergeNode in="SourceGraphic"/>
            </feMerge>
          </filter>
          <marker id="arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#f97316" />
          </marker>
          <marker id="arrowBlue" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#0ea5e9" />
          </marker>
          <marker id="arrowPurple" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#7c3aed" />
          </marker>
          <marker id="arrowCyan" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#06b6d4" />
          </marker>
        </defs>

        <g transform={`translate(${pan.x}, ${pan.y}) scale(${scale})`} style={{ pointerEvents: 'all' }}>
          {/* Connection lines - direction determines arrow:
              Inbound:       remote → local (cyan)
              Outbound:      local → remote (orange)
              Bidirectional: two parallel offset lines, one in each direction */}
          {isNetworkOnline && localNode && remoteNodesList.map((remoteNode) => {
            const isOnline = remoteNode.data.status !== 'offline'
            const dir = remoteNode.data.direction || 'outbound'

            // Base endpoints: local right edge → remote left edge
            const localX = localNode.x + 100
            const localY = localNode.y
            const remoteX = remoteNode.x - 35
            const remoteY = remoteNode.y

            // For bidirectional, offset two lines vertically by ±6px so they don't overlap
            const lineOffset = dir === 'bidirectional' ? 6 : 0

            // Build the line segments to render
            const lines: {
              key: string
              x1: number; y1: number; x2: number; y2: number
              marker: string; color: string; particleColor: string
            }[] = []

            if (dir === 'outbound' || dir === 'bidirectional') {
              // Outbound: local → remote (orange arrow at remote end)
              // "I connected to them"
              lines.push({
                key: `out-${remoteNode.id}`,
                x1: localX, y1: localY - lineOffset,
                x2: remoteX, y2: remoteY - lineOffset,
                marker: 'url(#arrow)',
                color: isOnline
                  ? (hasActiveMessages ? 'url(#connectionGrad)' : '#f9731640')
                  : '#2a2a2a',
                particleColor: '#f97316',
              })
            }
            if (dir === 'inbound' || dir === 'bidirectional') {
              // Inbound: remote → local (cyan arrow at local end)
              // "They connected to me"
              lines.push({
                key: `in-${remoteNode.id}`,
                x1: remoteX, y1: remoteY + lineOffset,
                x2: localX, y2: localY + lineOffset,
                marker: 'url(#arrowCyan)',
                color: isOnline
                  ? (hasActiveMessages ? 'url(#connectionGradInbound)' : '#06b6d440')
                  : '#2a2a2a',
                particleColor: '#06b6d4',
              })
            }

            return (
              <g key={`conn-${remoteNode.id}`}>
                {lines.map(l => (
                  <g key={l.key}>
                    <line
                      x1={l.x1} y1={l.y1}
                      x2={l.x2} y2={l.y2}
                      stroke={l.color}
                      strokeWidth={isOnline ? 2 : 1}
                      strokeDasharray={isOnline ? undefined : '8,4'}
                      markerEnd={l.marker}
                      opacity={isOnline ? 0.8 : 0.3}
                    />
                    {isOnline && hasActiveMessages && (
                      <circle r={4} fill={l.particleColor} filter="url(#glow)">
                        <animateMotion
                          dur="2s"
                          repeatCount="indefinite"
                          path={`M${l.x1},${l.y1} L${l.x2},${l.y2}`}
                        />
                      </circle>
                    )}
                  </g>
                ))}
              </g>
            )
          })}

          {/* Local Agentic Node */}
          {localNode && (
            <g
              data-node-id="local-an"
              transform={`translate(${localNode.x - 100}, ${localNode.y - 95})`}
              style={{ cursor: dragging === 'local-an' ? 'grabbing' : 'grab', pointerEvents: 'all' }}
              onMouseEnter={(e) => handleNodeMouseEnter('local-an', 'local-an', e)}
              onMouseLeave={handleNodeMouseLeave}
            >
              {isNetworkOnline && isNodePulsing('local-an') && (
                <rect x={-14} y={-14} width={228} height={218} rx={22}
                  fill="none" stroke="#22d3ee" strokeWidth={2} opacity={0.45}>
                  <animate attributeName="opacity" values="0.55;0.12;0.55" dur="1s" repeatCount="indefinite" />
                </rect>
              )}
              {isLocalOnline && (
                <rect x={-6} y={-6} width={212} height={202} rx={18}
                  fill="none" stroke="#0ea5e9" strokeWidth={2} opacity={0.3} filter="url(#glow)" />
              )}
              
              <rect width={200} height={190} rx={12}
                fill="#111" stroke={isLocalOnline ? '#0ea5e9' : '#333'} strokeWidth={2} />
              
              {/* Header */}
              <rect width={200} height={30} rx={12} fill="#0ea5e9" opacity={0.12} />
              <rect y={18} width={200} height={12} fill="#0ea5e9" opacity={0.12} />
              <text x={100} y={20} textAnchor="middle" fill="#0ea5e9" fontSize={11} fontWeight="bold">
                Local Agentic Node
              </text>

              {/* Organizer */}
              <g 
                transform="translate(45, 60)"
                onMouseEnter={(e) => { e.stopPropagation(); handleNodeMouseEnter(ORGANIZER_ID, 'organizer', e); }}
                onMouseLeave={handleNodeMouseLeave}
                style={{ cursor: 'pointer' }}
              >
                <circle r={20} fill="#581c87" stroke={organizers[0]?.status !== 'offline' ? '#a855f7' : '#444'} strokeWidth={2} />
                <text y={5} textAnchor="middle" fill="white" fontSize={10} fontWeight="bold">
                  {organizers[0]?.name?.split(/[\s_-]+/).filter(Boolean).slice(0, 2).map(w => w[0]?.toUpperCase()).join('') || 'O'}
                </text>
                {organizers[0] && <circle cx={14} cy={-14} r={5} fill={getStatusColor(organizers[0].status)} />}
              </g>
              <text x={45} y={95} textAnchor="middle" fill="#a855f7" fontSize={9}>Organizer</text>

              {/* Arrow to remote */}
              <line x1={72} y1={60} x2={195} y2={60} stroke="#a855f7" strokeWidth={1.5} strokeDasharray="4,3" markerEnd="url(#arrow)" opacity={0.6} />

              {/* Line from Organizer (45,60) to Coordinator (100,130) */}
              <line x1={45} y1={80} x2={100} y2={108} stroke="#7c3aed" strokeWidth={1.5} strokeDasharray="4,3" markerEnd="url(#arrowPurple)" opacity={0.6} />

              {/* Coordinator */}
              <g 
                transform="translate(100, 130)"
                onMouseEnter={(e) => { e.stopPropagation(); handleNodeMouseEnter(COORDINATOR_ID, 'coordinator', e); }}
                onMouseLeave={handleNodeMouseLeave}
                style={{ cursor: 'pointer' }}
              >
                <circle r={22} fill="#1e3a8a" stroke={coordinators[0]?.status !== 'offline' ? '#3b82f6' : '#444'} strokeWidth={2} />
                <text y={6} textAnchor="middle" fill="white" fontSize={10} fontWeight="bold">
                  {coordinators[0]?.name?.split(/[\s_-]+/).filter(Boolean).slice(0, 2).map(w => w[0]?.toUpperCase()).join('') || 'C'}
                </text>
                {coordinators[0] && <circle cx={15} cy={-15} r={5} fill={getStatusColor(coordinators[0].status)} />}
              </g>
              <text x={100} y={165} textAnchor="middle" fill="#3b82f6" fontSize={8}>
                {coordinators[0]?.name || LOCAL_COORDINATOR_NAME}
              </text>

              {/* Workers -- rendered individually for per-worker hover */}
              {(() => {
                const maxWorkers = Math.min(workers.length, 4)
                const workerPositions: Array<{ wx: number; wy: number }> = []
                
                // Position workers in a compact grid within the box bounds
                // Box is 200 wide, workers area starts around x=140, ends at x=190
                // Workers area y: 105 to 165
                const centerWX = 155
                const centerWY = 130
                
                for (let i = 0; i < maxWorkers; i++) {
                  if (maxWorkers === 1) {
                    workerPositions.push({ wx: centerWX, wy: centerWY })
                  } else if (maxWorkers === 2) {
                    workerPositions.push({ wx: centerWX, wy: centerWY + (i === 0 ? -16 : 16) })
                  } else if (maxWorkers === 3) {
                    const positions = [
                      { wx: centerWX - 14, wy: centerWY - 14 },
                      { wx: centerWX + 14, wy: centerWY - 14 },
                      { wx: centerWX, wy: centerWY + 14 },
                    ]
                    workerPositions.push(positions[i])
                  } else {
                    // 4 workers in 2x2 grid
                    const col = i % 2
                    const row = Math.floor(i / 2)
                    workerPositions.push({
                      wx: centerWX + (col === 0 ? -14 : 14),
                      wy: centerWY + (row === 0 ? -14 : 14),
                    })
                  }
                }
                
                return workerPositions.map((pos, i) => {
                  const worker = workers[i]
                  return (
                    <g key={worker.id}>
                      {/* Individual C -> W connection line */}
                      <line
                        x1={122} y1={130}
                        x2={pos.wx - 12} y2={pos.wy}
                        stroke="#06b6d4" strokeWidth={1} strokeDasharray="3,2" opacity={0.5}
                      />
                      <g
                        transform={`translate(${pos.wx}, ${pos.wy})`}
                        onMouseEnter={(e) => { e.stopPropagation(); handleNodeMouseEnter(worker.id, 'worker', e); }}
                        onMouseLeave={handleNodeMouseLeave}
                        style={{ cursor: 'pointer' }}
                      >
                        <circle r={12} fill="#164e63" stroke={worker.status !== 'offline' ? '#06b6d4' : '#444'} strokeWidth={1.5} />
                        <text y={4} textAnchor="middle" fill="white" fontSize={8} fontWeight="bold">
                          {worker.name?.split(/[\s_-]+/).filter(Boolean).slice(0, 2).map(w => w[0]?.toUpperCase()).join('') || 'W'}
                        </text>
                        <circle cx={8} cy={-8} r={3} fill={getStatusColor(worker.status)} />
                      </g>
                      {i === 0 && (
                        <text x={centerWX} y={176} textAnchor="middle" fill="#06b6d4" fontSize={8}>Workers ({workers.length})</text>
                      )}
                    </g>
                  )
                })
              })()}
            </g>
          )}

          {/* Remote Agentic Nodes */}
          {isNetworkOnline && remoteNodesList.map((node) => {
            const isOnline = node.data.status !== 'offline'
            const dir = node.data.direction || 'outbound'
            // Visual style per direction:
            //   Bidirectional = green (mutual link), Inbound = cyan, Outbound = orange
            // Label meaning (from the perspective of the LOCAL node):
            //   Outbound: "I connected to them"  — local AN initiated
            //   Inbound:  "They connected to me" — remote AN initiated
            const nodeColor = dir === 'bidirectional' ? '#22c55e' : dir === 'inbound' ? '#06b6d4' : '#f97316'
            const nodeBgColor = dir === 'bidirectional' ? '#14532d' : dir === 'inbound' ? '#164e63' : '#7c2d12'
            const labelColor = dir === 'bidirectional' ? '#86efac' : dir === 'inbound' ? '#67e8f9' : '#fdba74'
            const dirLabel = dir === 'bidirectional' ? '⇄' : 'WAN'
            
            return (
              <g
                key={node.id}
                data-node-id={node.id}
                transform={`translate(${node.x}, ${node.y})`}
                onMouseEnter={(e) => handleNodeMouseEnter(node.id, 'remote-an', e)}
                onMouseLeave={handleNodeMouseLeave}
                style={{ cursor: dragging === node.id ? 'grabbing' : 'grab', pointerEvents: 'all' }}
              >
                {isNodePulsing(node.id) && (
                  <circle r={50} fill="none" stroke={nodeColor} strokeWidth={2} opacity={0.45}>
                    <animate attributeName="r" values="43;52;43" dur="1s" repeatCount="indefinite" />
                    <animate attributeName="opacity" values="0.55;0.1;0.55" dur="1s" repeatCount="indefinite" />
                  </circle>
                )}
                {isOnline && (
                  <circle r={42} fill="none" stroke={nodeColor} strokeWidth={2}
                    opacity={hoveredNode === node.id ? 0.6 : 0.2}
                    filter={hoveredNode === node.id ? 'url(#glow)' : undefined} />
                )}
                
                <circle r={35} fill="#111" stroke={isOnline ? nodeColor : '#444'} strokeWidth={2} />
                <circle r={26} fill={nodeBgColor} opacity={0.7} />
                
                <text y={-5} textAnchor="middle" fill="white" fontSize={10} fontWeight="bold">
                  {node.data.name?.split(/[\s_-]+/).filter(Boolean).slice(0, 2).map(w => w[0]?.toUpperCase()).join('') || 'AN'}
                </text>
                <text y={8} textAnchor="middle" fill={labelColor} fontSize={8}>
                  {dirLabel}
                </text>
                
                <circle cx={24} cy={-24} r={6} fill={getStatusColor(node.data.status)} />
                
                <text y={52} textAnchor="middle" fill="#aaa" fontSize={10}>
                  {node.data.name.length > 14 ? node.data.name.slice(0, 14) + '...' : node.data.name}
                </text>
                {node.data.anId && (
                  <text y={64} textAnchor="middle" fill="#666" fontSize={7} fontFamily="monospace">
                    {truncateId(node.data.anId, 16)}
                  </text>
                )}
              </g>
            )
          })}
        </g>
      </svg>

      {/* Tooltip */}
      {tooltip && (
        <NodeTooltip 
          tooltip={tooltip} 
          mousePos={mousePos}
          containerWidth={dimensions.width}
          containerHeight={dimensions.height}
          agents={agents}
          workers={workers}
          localNodeInfo={localNodeInfo}
        />
      )}

      {/* Zoom controls */}
      <div className="absolute top-3 right-3 flex flex-col gap-1 bg-dark-surface/90 rounded-lg p-1 border border-dark-border">
        <button onClick={zoomIn} className="p-2 hover:bg-dark-hover rounded transition-colors" title="Zoom In">
          <MagnifyingGlassPlusIcon className="w-4 h-4 text-gray-400" />
        </button>
        <button onClick={zoomOut} className="p-2 hover:bg-dark-hover rounded transition-colors" title="Zoom Out">
          <MagnifyingGlassMinusIcon className="w-4 h-4 text-gray-400" />
        </button>
        <div className="h-px bg-dark-border" />
        <button onClick={resetView} className="p-2 hover:bg-dark-hover rounded transition-colors" title="Reset View">
          <ArrowPathIcon className="w-4 h-4 text-gray-400" />
        </button>
        <button onClick={toggleFullscreen} className="p-2 hover:bg-dark-hover rounded transition-colors" title="Fullscreen">
          <ArrowsPointingOutIcon className="w-4 h-4 text-gray-400" />
        </button>
      </div>

      {/* Network status indicator */}
      <div className="absolute top-3 left-3 flex items-center gap-2">
        <div className="text-[10px] text-gray-500 bg-dark-bg/80 px-2 py-1 rounded">
          {Math.round(scale * 100)}%
        </div>
        {!isNetworkOnline && (
          <div className="flex items-center gap-1.5 bg-orange-500/20 text-orange-400 px-2 py-1 rounded text-[10px] font-medium">
            <SignalSlashIcon className="w-3 h-3" />
            <span>Offline - Local Only</span>
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="absolute bottom-3 left-3 flex flex-wrap items-center gap-3 text-[10px] text-gray-500 bg-dark-bg/90 px-3 py-2 rounded-lg border border-dark-border">
        <div className="flex items-center gap-1">
          <div className="w-3 h-3 rounded" style={{ backgroundColor: '#a855f7' }} />
          <span>Organizer</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="w-3 h-3 rounded" style={{ backgroundColor: '#3b82f6' }} />
          <span>{LOCAL_COORDINATOR_NAME}</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="w-3 h-3 rounded" style={{ backgroundColor: '#06b6d4' }} />
          <span>Worker</span>
        </div>
        {isNetworkOnline && (
          <>
            <div className="h-3 border-l border-dark-border ml-1" />
            <div className="flex items-center gap-1">
              <svg width="20" height="8"><line x1="0" y1="4" x2="16" y2="4" stroke="#f97316" strokeWidth="2" markerEnd="url(#legendArrowOrange)" /><defs><marker id="legendArrowOrange" markerWidth="4" markerHeight="4" refX="4" refY="2" orient="auto"><polygon points="0 0, 4 2, 0 4" fill="#f97316" /></marker></defs></svg>
              <span>Me → Them</span>
            </div>
            <div className="flex items-center gap-1">
              <svg width="20" height="8"><line x1="16" y1="4" x2="0" y2="4" stroke="#06b6d4" strokeWidth="2" markerEnd="url(#legendArrowCyan)" /><defs><marker id="legendArrowCyan" markerWidth="4" markerHeight="4" refX="4" refY="2" orient="auto"><polygon points="0 0, 4 2, 0 4" fill="#06b6d4" /></marker></defs></svg>
              <span>Them → Me</span>
            </div>
            <div className="flex items-center gap-1">
              <div className="w-3 h-3 rounded-full" style={{ backgroundColor: '#22c55e' }} />
              <span>Mutual ⇄</span>
            </div>
            <div className="flex items-center gap-1">
              <div className="w-3 h-3 rounded-full border border-cyan-300 animate-pulse" />
              <span>Topology Update</span>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// Tooltip Component
interface NodeTooltipProps {
  tooltip: TooltipInfo
  mousePos: { x: number; y: number }
  containerWidth: number
  containerHeight: number
  agents: Agent[]
  workers: Agent[]
  localNodeInfo: { description: string; capabilities: { name: string; description: string }[] } | null
}

function NodeTooltip({ tooltip, mousePos, containerWidth, containerHeight, agents, workers, localNodeInfo }: NodeTooltipProps) {
  const { localAnHost, localAnPort, localAnName } = useConfigStore()
  
  const padding = 12
  const tooltipWidth = 280
  const tooltipHeight = 200
  
  // Calculate position to avoid overflow
  let x = mousePos.x + padding
  let y = mousePos.y + padding
  
  if (x + tooltipWidth > containerWidth - padding) {
    x = mousePos.x - tooltipWidth - padding
  }
  if (y + tooltipHeight > containerHeight - padding) {
    y = containerHeight - tooltipHeight - padding
  }
  if (y < padding) {
    y = padding
  }

  const localIp = `${localAnHost}:${localAnPort}`

  const getNodeInfo = () => {
    const { nodeType, agentData, anData } = tooltip

    if (nodeType === 'local-an') {
      return {
        name: localAnName || 'Local Agentic Node',
        ip: localIp,
        description: localNodeInfo?.description || DEFAULT_LOCAL_NODE_DESCRIPTION,
        capabilities: localNodeInfo?.capabilities || [
          { name: 'task_orchestration', description: 'Orchestrates tasks across the network' },
          { name: 'worker_management', description: 'Manages local worker agents' },
        ],
        status: agents.some(a => a.status !== 'offline') ? 'online' : 'offline',
        color: '#0ea5e9',
      }
    }

    if (nodeType === 'organizer' && agentData) {
      return {
        name: agentData.name,
        ip: `${localAnHost} (Local)`,
        description: 'Initiates tasks and assigns them to remote Coordinators',
        capabilities: agentData.capabilities,
        status: agentData.status,
        color: '#a855f7',
      }
    }

    if (nodeType === 'coordinator' && agentData) {
      return {
        name: agentData.name,
        ip: `${localAnHost} (Local)`,
        description: 'Receives tasks and coordinates local Workers',
        capabilities: agentData.capabilities,
        status: agentData.status,
        color: '#3b82f6',
      }
    }

    if (nodeType === 'worker') {
      // Find specific worker by ID
      const specificWorker = agentData || workers.find(w => w.id === tooltip.node?.id)
      if (specificWorker) {
        return {
          name: specificWorker.name,
          ip: `${localAnHost} (Local)`,
          description: specificWorker.currentTask ? `Working on: ${specificWorker.currentTask}` : 'Waiting for task assignment',
          capabilities: specificWorker.capabilities.slice(0, 4),
          status: specificWorker.status,
          color: '#06b6d4',
        }
      }
      return {
        name: `Workers (${workers.length})`,
        ip: `${localAnHost} (Local)`,
        description: 'Execute assigned subtasks from local team coordinator',
        capabilities: workers.flatMap(w => w.capabilities).slice(0, 4),
        status: workers.some(w => w.status === 'busy') ? 'busy' : 
                workers.some(w => w.status === 'online') ? 'online' : 'offline',
        color: '#06b6d4',
      }
    }

    if (nodeType === 'remote-an' && anData) {
      const displayName = getNodeDisplayName(anData)
      // Prefer the full capabilities array (from network handshake) over the single capability string
      const caps = anData.capabilities && anData.capabilities.length > 0
        ? anData.capabilities
        : anData.capability
          ? [{ name: anData.capability, description: '' }]
          : []
      return {
        name: displayName,
        originalName: anData.alias ? anData.name : undefined, // Show original name if alias is set
        anId: anData.anId || anData.remoteId,
        ip: `${anData.ip}:${anData.port}`,
        description: anData.description || `Remote ${anData.type === 'lan' ? 'LAN' : 'WAN'} Node`,
        capabilities: caps,
        status: anData.status,
        color: anData.type === 'lan' ? '#22c55e' : '#f97316',
        type: anData.type,
      }
    }

    return null
  }

  const info = getNodeInfo()
  if (!info) return null

  const statusColors: Record<string, string> = {
    online: '#22c55e',
    busy: '#eab308',
    idle: '#3b82f6',
    error: '#ef4444',
    offline: '#6b7280',
  }

  return (
    <div
      className="absolute z-50 pointer-events-none"
      style={{ left: x, top: y }}
    >
      <div 
        className="bg-dark-surface/95 backdrop-blur-sm border border-dark-border rounded-xl shadow-2xl overflow-hidden"
        style={{ width: tooltipWidth }}
      >
        {/* Header */}
        <div 
          className="px-4 py-3 border-b border-dark-border"
          style={{ backgroundColor: `${info.color}15` }}
        >
          <div className="flex items-center justify-between">
            <div>
              <h4 className="font-semibold text-white text-sm">{info.name}</h4>
              {'originalName' in info && info.originalName && (
                <p className="text-xs text-gray-500">({info.originalName})</p>
              )}
              {'anId' in info && info.anId && (
                <p className="text-[9px] font-mono text-gray-500 truncate" title={String(info.anId)}>
                  AN: {truncateId(String(info.anId), 20)}
                </p>
              )}
            </div>
            <div className="flex items-center gap-1.5">
              <div 
                className="w-2 h-2 rounded-full" 
                style={{ backgroundColor: statusColors[info.status] || '#6b7280' }}
              />
              <span className="text-xs text-gray-400 capitalize">{info.status}</span>
            </div>
          </div>
        </div>

        {/* Content */}
        <div className="p-4 space-y-3">
          {/* IP / Endpoint */}
          <div>
            <p className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Address</p>
            <p className="text-xs text-gray-300 font-mono">{info.ip}</p>
          </div>

          {/* Type (for remote AN) */}
          {'type' in info && info.type && (
            <div>
              <p className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Connection Type</p>
              <p className="text-xs text-gray-300 uppercase">{info.type}</p>
            </div>
          )}

          {/* Description */}
          <div>
            <p className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Description</p>
            <p className="text-xs text-gray-400">{info.description}</p>
          </div>

          {/* Capabilities */}
          {info.capabilities && info.capabilities.length > 0 && (
            <div>
              <p className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">
                Capabilities ({info.capabilities.length})
              </p>
              <div className="flex flex-wrap gap-1.5 max-h-24 overflow-y-auto">
                {info.capabilities.map((cap, i) => (
                  <span
                    key={i}
                    className="px-2 py-0.5 rounded text-[10px] bg-dark-hover text-gray-300"
                    title={cap.description}
                  >
                    {cap.name.replace(/_/g, ' ')}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
