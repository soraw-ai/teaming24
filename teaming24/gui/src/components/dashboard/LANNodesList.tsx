import { useState, useEffect } from 'react'
import { useNetworkStore, NodeInfo } from '../../store/networkStore'
import {
  ComputerDesktopIcon,
  ArrowPathIcon,
  SignalIcon,
  LinkIcon,
  PencilIcon,
  CheckIcon,
  XMarkIcon,
  CheckCircleIcon
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { formatDurationSecs } from '../../utils/format'

export default function LANNodesList() {
  const {
    isDiscovering,
    lanNodes,
    wanNodes,
    fetchLanNodes,
    triggerBroadcast,
    connectToLanNode
  } = useNetworkStore()

  const [connectingId, setConnectingId] = useState<string | null>(null)
  const [editingNodeId, setEditingNodeId] = useState<string | null>(null)
  const [aliasInput, setAliasInput] = useState('')
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [, setTick] = useState(0)

  // Check if a LAN node is already connected (not just in list, but actively connected)
  const getConnectedNode = (node: NodeInfo): NodeInfo | undefined => {
    return wanNodes.find(wn => wn.ip === node.ip && wn.port === node.port && wn.status !== 'offline')
  }

  // Update duration display every second
  useEffect(() => {
    const interval = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(interval)
  }, [])
  
  // When SCAN turns on: trigger broadcast immediately + start polling
  useEffect(() => {
    if (!isDiscovering) return
    triggerBroadcast()
    fetchLanNodes()
    const interval = setInterval(fetchLanNodes, 3000)
    return () => clearInterval(interval)
  }, [isDiscovering, fetchLanNodes, triggerBroadcast])

  // Handle refresh button - trigger broadcast and fetch
  const handleRefresh = async () => {
    setIsRefreshing(true)
    await triggerBroadcast()
    setTimeout(() => {
      fetchLanNodes()
      setIsRefreshing(false)
    }, 800)
  }
  
  const handleStartEdit = (node: NodeInfo) => {
    setEditingNodeId(node.id)
    setAliasInput('')
  }
  
  const handleCancelEdit = () => {
    setEditingNodeId(null)
    setAliasInput('')
  }
  
  const handleConnect = async (node: NodeInfo, alias?: string) => {
    setConnectingId(node.id)
    setEditingNodeId(null)
    try {
      await connectToLanNode(node, alias)
    } finally {
      setConnectingId(null)
    }
  }
  
  if (!isDiscovering) {
    return null
  }
  
  return (
    <div className="rounded-xl bg-dark-surface border border-dark-border overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-dark-border">
        <div className="flex items-center gap-2">
          <SignalIcon className="w-4 h-4 text-green-400" />
          <h3 className="text-sm font-medium text-gray-300">LAN Nodes</h3>
          <span className="px-1.5 py-0.5 rounded-full bg-green-500/20 text-green-400 text-xs">
            {lanNodes.length}
          </span>
        </div>
        <button
          onClick={handleRefresh}
          disabled={isRefreshing}
          className="p-1 hover:bg-dark-hover rounded transition-colors disabled:opacity-50"
          title="Broadcast & Refresh"
        >
          <ArrowPathIcon className={`w-4 h-4 text-gray-400 ${isRefreshing ? 'animate-spin' : ''}`} />
        </button>
      </div>
      
      {/* Node List */}
      <div className="max-h-[240px] overflow-y-auto thin-scrollbar">
        {lanNodes.length === 0 ? (
          <div className="p-4 text-center">
            <ComputerDesktopIcon className="w-8 h-8 mx-auto text-gray-600 mb-2" />
            <p className="text-sm text-gray-500">Scanning for nodes...</p>
            <p className="text-xs text-gray-600 mt-1">
              Nodes on your local network will appear here
            </p>
          </div>
        ) : (
          <div className="divide-y divide-dark-border">
            {lanNodes.map((node) => {
              const knownNode = wanNodes.find(wn => wn.ip === node.ip && wn.port === node.port)
              const connectedNode = getConnectedNode(node)
              const isConnected = !!connectedNode
              const isReconnectable = !!knownNode && knownNode.status === 'offline'

              return (
                <div key={node.id} className={clsx(
                  "px-4 py-3 transition-colors",
                  isConnected ? "bg-green-500/5" : "hover:bg-dark-hover/50"
                )}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className={clsx(
                        'w-8 h-8 rounded-lg flex items-center justify-center shrink-0',
                        isConnected ? 'bg-green-500/20' :
                        node.status === 'online' ? 'bg-green-500/20' : 'bg-gray-500/20'
                      )}>
                        {isConnected ? (
                          <CheckCircleIcon className="w-4 h-4 text-green-400" />
                        ) : (
                          <ComputerDesktopIcon className={clsx(
                            'w-4 h-4',
                            node.status === 'online' ? 'text-green-400' : 'text-gray-400'
                          )} />
                        )}
                      </div>
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-gray-200 truncate">
                          {connectedNode?.alias || knownNode?.alias || node.name}
                        </p>
                        <p className="text-xs text-gray-500 truncate">
                          {node.ip}:{node.port}
                          {node.role && <span className="ml-2 text-gray-600">• {node.role}</span>}
                        </p>
                      </div>
                    </div>

                    <div className="flex items-center gap-1 shrink-0 ml-2">
                      {isConnected ? (
                        // Show connected status with duration
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-gray-500">
                            {connectedNode.connectedSince ? formatDurationSecs(Math.floor((Date.now() - connectedNode.connectedSince) / 1000)) : ''}
                          </span>
                          <span className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium bg-green-500/20 text-green-400">
                            <CheckCircleIcon className="w-3 h-3" />
                            Connected
                          </span>
                        </div>
                      ) : editingNodeId !== node.id ? (
                        <>
                          <button
                            onClick={() => handleStartEdit(node)}
                            disabled={connectingId === node.id}
                            className="p-1.5 text-gray-500 hover:text-gray-300 hover:bg-dark-hover rounded transition-colors"
                            title="Set display name"
                          >
                            <PencilIcon className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => handleConnect(node)}
                            disabled={connectingId === node.id}
                            className={clsx(
                              'flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition-colors',
                              connectingId === node.id
                                ? 'bg-gray-700 text-gray-400 cursor-not-allowed'
                                : 'bg-primary-600/20 text-primary-400 hover:bg-primary-600/30'
                            )}
                          >
                            {connectingId === node.id ? (
                              <>
                                <ArrowPathIcon className="w-3 h-3 animate-spin" />
                                <span>{isReconnectable ? 'Reconnecting' : 'Connecting'}</span>
                              </>
                            ) : (
                              <>
                                {isReconnectable ? (
                                  <ArrowPathIcon className="w-3 h-3" />
                                ) : (
                                  <LinkIcon className="w-3 h-3" />
                                )}
                                <span>{isReconnectable ? 'Reconnect' : 'Connect'}</span>
                              </>
                            )}
                          </button>
                        </>
                      ) : null}
                    </div>
                  </div>

                  {/* Alias input row - only show if not connected */}
                  {editingNodeId === node.id && !isConnected && (
                    <div className="mt-2 flex items-center gap-2">
                      <input
                        type="text"
                        value={aliasInput}
                        onChange={(e) => setAliasInput(e.target.value)}
                        placeholder="Display name (optional)"
                        className="flex-1 px-2 py-1 bg-dark-bg border border-dark-border rounded text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-primary-500"
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleConnect(node, aliasInput)
                          if (e.key === 'Escape') handleCancelEdit()
                        }}
                      />
                      <button
                        onClick={() => handleConnect(node, aliasInput)}
                        className="flex items-center gap-1 px-2.5 py-1 bg-primary-600 hover:bg-primary-700 text-white rounded text-xs font-medium transition-colors"
                      >
                        <CheckIcon className="w-3 h-3" />
                        Connect
                      </button>
                      <button
                        onClick={handleCancelEdit}
                        className="p-1 text-gray-400 hover:text-gray-200 hover:bg-dark-hover rounded transition-colors"
                      >
                        <XMarkIcon className="w-4 h-4" />
                      </button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
