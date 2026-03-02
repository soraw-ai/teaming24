/**
 * Connected Nodes List - Rich display of connected Agentic Nodes.
 *
 * Shows:
 * - Node name and alias
 * - Wallet address (truncated)
 * - Agent ID
 * - Capability tags
 * - Description
 * - Connection type (LAN/WAN)
 * - Connection duration
 */

import { useState, useEffect } from 'react'
import { useNetworkStore, getNodeDisplayName } from '../../store/networkStore'
import {
  ArrowPathIcon,
  PencilIcon,
  CheckIcon,
  XMarkIcon,
  TrashIcon,
  GlobeAltIcon,
  WifiIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  WalletIcon,
  IdentificationIcon,
  ClipboardIcon,
  ClockIcon,
  SignalSlashIcon
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { notify } from '../../store/notificationStore'
import { formatDurationSecs } from '../../utils/format'
import { truncateId } from '../../utils/strings'

export default function ConnectedNodesList() {
  const { wanNodes, setNodeAlias, disconnectNode, removeNode, clearAllNodes, connectToWanNode } = useNetworkStore()
  const [isExpanded, setIsExpanded] = useState(true)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [aliasInput, setAliasInput] = useState('')
  const [expandedCards, setExpandedCards] = useState<Set<string>>(new Set())
  const [reconnectingId, setReconnectingId] = useState<string | null>(null)
  const [, setTick] = useState(0)

  // Update duration display every second
  useEffect(() => {
    const interval = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(interval)
  }, [])

  if (wanNodes.length === 0) {
    return null
  }

  // Sort nodes: connected (by connectedSince desc) first, then disconnected
  const sortedNodes = [...wanNodes].sort((a, b) => {
    const aConnected = a.status !== 'offline'
    const bConnected = b.status !== 'offline'

    // Connected nodes come first
    if (aConnected && !bConnected) return -1
    if (!aConnected && bConnected) return 1

    // Among connected nodes, newer connections first
    if (aConnected && bConnected) {
      return (b.connectedSince || 0) - (a.connectedSince || 0)
    }

    return 0
  })

  const toggleCardExpand = (nodeId: string) => {
    setExpandedCards(prev => {
      const next = new Set(prev)
      if (next.has(nodeId)) {
        next.delete(nodeId)
      } else {
        next.add(nodeId)
      }
      return next
    })
  }

  const handleStartEdit = (nodeId: string, currentAlias?: string) => {
    setEditingId(nodeId)
    setAliasInput(currentAlias || '')
  }

  const handleSaveAlias = (nodeId: string) => {
    setNodeAlias(nodeId, aliasInput)
    setEditingId(null)
    setAliasInput('')
  }

  const handleCancelEdit = () => {
    setEditingId(null)
    setAliasInput('')
  }

  const handleDisconnect = (nodeId: string) => {
    disconnectNode(nodeId)
  }

  const handleRemove = (nodeId: string) => {
    removeNode(nodeId)
  }

  const handleReconnect = async (nodeId: string) => {
    const node = wanNodes.find(n => n.id === nodeId)
    if (!node) return
    setReconnectingId(nodeId)
    try {
      await connectToWanNode(node.ip, node.port, '', node.alias)
    } finally {
      setReconnectingId(null)
    }
  }
  
  const copyToClipboard = (text: string, label: string) => {
    navigator.clipboard.writeText(text)
    notify.success('Copied', `${label} copied to clipboard`)
  }
  
  const connectedCount = wanNodes.filter(n => n.status !== 'offline').length

  return (
    <div className="rounded-xl bg-dark-surface border border-dark-border overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-dark-border">
        <div
          className="flex items-center gap-2 flex-1 cursor-pointer"
          onClick={() => setIsExpanded(!isExpanded)}
        >
          <GlobeAltIcon className="w-4 h-4 text-primary-400" />
          <h3 className="text-sm font-medium text-gray-300">Agentic Nodes</h3>
          <span className="px-1.5 py-0.5 rounded-full bg-green-500/20 text-green-400 text-xs font-medium">
            {connectedCount}
          </span>
          {wanNodes.length > connectedCount && (
            <span className="px-1.5 py-0.5 rounded-full bg-gray-500/20 text-gray-400 text-xs">
              +{wanNodes.length - connectedCount} offline
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => clearAllNodes()}
            className="p-1.5 text-gray-500 hover:text-red-400 hover:bg-red-500/20 rounded transition-colors"
            title="Clear all nodes"
          >
            <TrashIcon className="w-4 h-4" />
          </button>
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="p-1 text-gray-500 hover:bg-dark-hover rounded transition-colors"
          >
            {isExpanded ? (
              <ChevronUpIcon className="w-4 h-4" />
            ) : (
              <ChevronDownIcon className="w-4 h-4" />
            )}
          </button>
        </div>
      </div>

      {/* Node Cards */}
      {isExpanded && (
        <div className="max-h-[400px] overflow-y-auto thin-scrollbar p-3 space-y-2">
          {sortedNodes.map((node) => {
            const isEditing = editingId === node.id
            const displayName = getNodeDisplayName(node)
            const isLan = node.type === 'lan'
            const isCardExpanded = expandedCards.has(node.id)
            const isDisconnected = node.status === 'offline'

            return (
              <div
                key={node.id}
                className={clsx(
                  "rounded-lg border transition-all group",
                  isDisconnected
                    ? "bg-gray-500/5 border-gray-500/20 hover:border-gray-500/40 opacity-60"
                    : isLan
                      ? "bg-green-500/5 border-green-500/20 hover:border-green-500/40"
                      : "bg-orange-500/5 border-orange-500/20 hover:border-orange-500/40"
                )}
              >
                {/* Main Row */}
                <div className="px-3 py-2.5 relative">
                  <div className="flex items-start gap-3">
                    {/* Icon */}
                    <div className={clsx(
                      'w-10 h-10 rounded-lg flex items-center justify-center shrink-0',
                      isLan ? 'bg-green-500/20' : 'bg-orange-500/20'
                    )}>
                      {isLan ? (
                        <WifiIcon className="w-5 h-5 text-green-400" />
                      ) : (
                        <GlobeAltIcon className="w-5 h-5 text-orange-400" />
                      )}
                    </div>
                    
                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      {/* Name Row */}
                      <div className="flex items-center gap-2 mb-1">
                        {isEditing ? (
                          <input
                            type="text"
                            value={aliasInput}
                            onChange={(e) => setAliasInput(e.target.value)}
                            placeholder={node.name}
                            className="flex-1 px-2 py-0.5 bg-dark-bg border border-dark-border rounded text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-primary-500"
                            autoFocus
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') handleSaveAlias(node.id)
                              if (e.key === 'Escape') handleCancelEdit()
                            }}
                          />
                        ) : (
                          <span className="text-sm font-medium text-white truncate">
                            {displayName}
                          </span>
                        )}
                        <span className={clsx(
                          'text-[10px] px-1.5 py-0.5 rounded font-medium',
                          isLan ? 'bg-green-500/20 text-green-400' : 'bg-orange-500/20 text-orange-400'
                        )}>
                          {isLan ? 'LAN' : 'WAN'}
                        </span>
                        <span className={clsx(
                          'w-2 h-2 rounded-full',
                          node.status === 'online' ? 'bg-green-400' :
                          node.status === 'busy' ? 'bg-yellow-400' : 'bg-gray-400'
                        )} />
                      </div>
                      
                      {/* Address & Duration */}
                      <div className="flex items-center gap-2 text-xs text-gray-500 mb-1">
                        <span>{node.ip}:{node.port}</span>
                        {node.alias && (
                          <span className="text-gray-600">({node.name})</span>
                        )}
                        {node.connectedSince && (
                          <span className="flex items-center gap-1 text-green-400/70">
                            <ClockIcon className="w-3 h-3" />
                            {formatDurationSecs(Math.floor((Date.now() - node.connectedSince) / 1000))}
                          </span>
                        )}
                      </div>

                      {/* AN ID — canonical unique identifier */}
                      {(node.anId || node.remoteId) && (
                        <button
                          onClick={(e) => { e.stopPropagation(); copyToClipboard((node.anId || node.remoteId)!, 'AN ID'); }}
                          className="text-[9px] font-mono text-gray-500 hover:text-gray-300 truncate max-w-full mb-1 text-left transition-colors"
                          title={node.anId || node.remoteId}
                        >
                          AN: {truncateId(node.anId || node.remoteId || '')}
                        </button>
                      )}
                      
                      {/* Wallet & Agent ID */}
                      <div className="flex flex-wrap gap-2 text-[10px]">
                        {node.walletAddress && (
                          <button
                            onClick={(e) => { e.stopPropagation(); copyToClipboard(node.walletAddress!, 'Wallet address'); }}
                            className="flex items-center gap-1 px-1.5 py-0.5 bg-dark-bg rounded text-gray-400 hover:text-white hover:bg-dark-hover transition-colors"
                            title={node.walletAddress}
                          >
                            <WalletIcon className="w-3 h-3" />
                            <span>{truncateId(node.walletAddress)}</span>
                          </button>
                        )}
                        {node.agentId && (
                          <button
                            onClick={(e) => { e.stopPropagation(); copyToClipboard(node.agentId!, 'Agent ID'); }}
                            className="flex items-center gap-1 px-1.5 py-0.5 bg-dark-bg rounded text-gray-400 hover:text-white hover:bg-dark-hover transition-colors"
                            title={node.agentId}
                          >
                            <IdentificationIcon className="w-3 h-3" />
                            <span>{truncateId(node.agentId)}</span>
                          </button>
                        )}
                      </div>
                      
                      {/* Capability Tags */}
                      {(node.capability || (node.capabilities && node.capabilities.length > 0)) && (
                        <div className="flex flex-wrap gap-1 mt-2">
                          {node.capability && (
                            <span className="px-1.5 py-0.5 bg-primary-500/20 text-primary-400 rounded text-[10px] font-medium">
                              {node.capability}
                            </span>
                          )}
                          {node.capabilities?.slice(0, 3).map((cap, i) => (
                            <span 
                              key={i}
                              className="px-1.5 py-0.5 bg-dark-hover text-gray-400 rounded text-[10px]"
                              title={cap.description}
                            >
                              {cap.name}
                            </span>
                          ))}
                          {node.capabilities && node.capabilities.length > 3 && (
                            <span className="px-1.5 py-0.5 text-gray-500 text-[10px]">
                              +{node.capabilities.length - 3} more
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                    
                    {/* Edit save/cancel — inline only when editing */}
                    {isEditing && (
                      <div className="flex items-center gap-1 shrink-0">
                        <button
                          onClick={() => handleSaveAlias(node.id)}
                          className="p-1.5 text-green-400 hover:bg-green-500/20 rounded transition-colors"
                          title="Save"
                        >
                          <CheckIcon className="w-4 h-4" />
                        </button>
                        <button
                          onClick={handleCancelEdit}
                          className="p-1.5 text-gray-400 hover:bg-dark-hover rounded transition-colors"
                          title="Cancel"
                        >
                          <XMarkIcon className="w-4 h-4" />
                        </button>
                      </div>
                    )}
                  </div>

                  {/* Hover overlay actions */}
                  {!isEditing && (
                    <div className={clsx(
                      "absolute top-2 right-2 flex items-center gap-0.5",
                      "bg-dark-surface/95 border border-dark-border/60 rounded-lg px-1 py-0.5 shadow-sm",
                      "transition-opacity duration-150",
                      isCardExpanded ? "opacity-100" : "opacity-0 group-hover:opacity-100"
                    )}>
                      {isDisconnected ? (
                        <>
                          <button
                            onClick={() => handleStartEdit(node.id, node.alias)}
                            className="p-1.5 text-gray-500 hover:text-gray-300 hover:bg-dark-hover rounded transition-colors"
                            title="Edit name"
                          >
                            <PencilIcon className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => handleReconnect(node.id)}
                            disabled={reconnectingId === node.id}
                            className={clsx(
                              "p-1.5 rounded transition-colors",
                              reconnectingId === node.id
                                ? "text-gray-500 cursor-not-allowed"
                                : "text-gray-500 hover:text-primary-400 hover:bg-primary-500/20"
                            )}
                            title="Reconnect"
                          >
                            <ArrowPathIcon className={clsx("w-3.5 h-3.5", reconnectingId === node.id && "animate-spin")} />
                          </button>
                          <button
                            onClick={() => handleRemove(node.id)}
                            className="p-1.5 text-gray-500 hover:text-red-400 hover:bg-red-500/20 rounded transition-colors"
                            title="Remove from list"
                          >
                            <TrashIcon className="w-3.5 h-3.5" />
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            onClick={() => toggleCardExpand(node.id)}
                            className="p-1.5 text-gray-500 hover:text-gray-300 hover:bg-dark-hover rounded transition-colors"
                            title={isCardExpanded ? "Collapse" : "Expand"}
                          >
                            {isCardExpanded ? (
                              <ChevronUpIcon className="w-3.5 h-3.5" />
                            ) : (
                              <ChevronDownIcon className="w-3.5 h-3.5" />
                            )}
                          </button>
                          <button
                            onClick={() => handleStartEdit(node.id, node.alias)}
                            className="p-1.5 text-gray-500 hover:text-gray-300 hover:bg-dark-hover rounded transition-colors"
                            title="Edit name"
                          >
                            <PencilIcon className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => handleDisconnect(node.id)}
                            className="p-1.5 text-gray-500 hover:text-yellow-400 hover:bg-yellow-500/20 rounded transition-colors"
                            title="Disconnect (keep in list)"
                          >
                            <SignalSlashIcon className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => handleRemove(node.id)}
                            className="p-1.5 text-gray-500 hover:text-red-400 hover:bg-red-500/20 rounded transition-colors"
                            title="Disconnect and remove"
                          >
                            <TrashIcon className="w-3.5 h-3.5" />
                          </button>
                        </>
                      )}
                    </div>
                  )}
                </div>

                {/* Expanded Details */}
                {isCardExpanded && !isEditing && (
                  <div className="px-3 pb-3 pt-0 border-t border-dark-border/50 mt-2">
                    <div className="grid grid-cols-2 gap-3 text-xs mt-2">
                      {/* Description */}
                      {node.description && (
                        <div className="col-span-2">
                          <span className="text-gray-500 text-[10px] uppercase tracking-wider">Description</span>
                          <p className="text-gray-300 mt-0.5">{node.description}</p>
                        </div>
                      )}
                      
                      {/* Full AN ID */}
                      {(node.anId || node.remoteId) && (
                        <div className="col-span-2">
                          <span className="text-gray-500 text-[10px] uppercase tracking-wider">AN ID (Unique Identifier)</span>
                          <div className="flex items-center gap-2 mt-0.5">
                            <code className="text-gray-300 font-mono text-[11px] break-all">{node.anId || node.remoteId}</code>
                            <button
                              onClick={() => copyToClipboard((node.anId || node.remoteId)!, 'AN ID')}
                              className="p-1 text-gray-500 hover:text-white rounded transition-colors shrink-0"
                            >
                              <ClipboardIcon className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>
                      )}

                      {/* Full Wallet Address */}
                      {node.walletAddress && (
                        <div className="col-span-2">
                          <span className="text-gray-500 text-[10px] uppercase tracking-wider">Wallet Address</span>
                          <div className="flex items-center gap-2 mt-0.5">
                            <code className="text-gray-300 font-mono text-[11px] break-all">{node.walletAddress}</code>
                            <button
                              onClick={() => copyToClipboard(node.walletAddress!, 'Wallet')}
                              className="p-1 text-gray-500 hover:text-white rounded transition-colors shrink-0"
                            >
                              <ClipboardIcon className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>
                      )}
                      
                      {/* Full Agent ID */}
                      {node.agentId && (
                        <div className="col-span-2">
                          <span className="text-gray-500 text-[10px] uppercase tracking-wider">Agent ID</span>
                          <div className="flex items-center gap-2 mt-0.5">
                            <code className="text-gray-300 font-mono text-[11px] break-all">{node.agentId}</code>
                            <button
                              onClick={() => copyToClipboard(node.agentId!, 'Agent ID')}
                              className="p-1 text-gray-500 hover:text-white rounded transition-colors shrink-0"
                            >
                              <ClipboardIcon className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>
                      )}
                      
                      {/* Price */}
                      {node.price && (
                        <div>
                          <span className="text-gray-500 text-[10px] uppercase tracking-wider">Price</span>
                          <p className="text-primary-400 font-mono mt-0.5">{node.price}</p>
                        </div>
                      )}
                      
                      {/* Region */}
                      {node.region && (
                        <div>
                          <span className="text-gray-500 text-[10px] uppercase tracking-wider">Region</span>
                          <p className="text-gray-300 mt-0.5">{node.region}</p>
                        </div>
                      )}
                      
                      {/* All Capabilities */}
                      {node.capabilities && node.capabilities.length > 0 && (
                        <div className="col-span-2">
                          <span className="text-gray-500 text-[10px] uppercase tracking-wider">All Capabilities</span>
                          <div className="flex flex-wrap gap-1 mt-1">
                            {node.capabilities.map((cap, i) => (
                              <span 
                                key={i}
                                className="px-2 py-0.5 bg-dark-hover text-gray-300 rounded text-[10px]"
                                title={cap.description}
                              >
                                {cap.name}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
      
      {/* Collapsed summary */}
      {!isExpanded && (
        <div className="px-4 py-2 text-xs text-gray-500">
          {wanNodes.length} node{wanNodes.length !== 1 ? 's' : ''} connected
        </div>
      )}
    </div>
  )
}
