/**
 * Inbound Peers List - shows who connected to this node.
 */
import { useEffect, useState } from 'react'
import { WifiIcon, ArrowPathIcon } from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { useNetworkStore, type NodeInfo, getNodeDisplayName } from '../../store/networkStore'
import { formatDurationSecs } from '../../utils/format'
import { truncateId } from '../../utils/strings'

export default function InboundPeersList() {
  const { status, inboundPeers, fetchInboundPeers } = useNetworkStore()
  const [refreshing, setRefreshing] = useState(false)

  const isOnline = status === 'online'

  // Fetch inbound peers on mount + periodically (every 10s) for robustness.
  // SSE events handle real-time updates, but polling ensures we don't miss
  // connections that happened before the component mounted or if SSE reconnects.
  useEffect(() => {
    if (!isOnline) return
    fetchInboundPeers()
    const interval = setInterval(() => fetchInboundPeers(), 10_000)
    return () => clearInterval(interval)
  }, [isOnline, fetchInboundPeers])

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      await fetchInboundPeers()
    } finally {
      setRefreshing(false)
    }
  }

  if (!isOnline) return null

  return (
    <div className="rounded-xl bg-dark-surface border border-dark-border overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-dark-border">
        <div className="flex items-center gap-2">
          <WifiIcon className="w-4 h-4 text-primary-400" />
          <h3 className="text-sm font-medium text-gray-300">Connected To Me</h3>
          <span className="px-1.5 py-0.5 rounded-full bg-primary-500/20 text-primary-400 text-xs font-medium">
            {inboundPeers.length}
          </span>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="p-1 hover:bg-dark-hover rounded transition-colors disabled:opacity-50"
          title="Refresh"
        >
          <ArrowPathIcon className={clsx('w-4 h-4 text-gray-400', refreshing && 'animate-spin')} />
        </button>
      </div>

      <div className="max-h-[400px] overflow-y-auto thin-scrollbar p-3 space-y-2">
        {inboundPeers.length === 0 ? (
          <div className="text-center py-8 text-gray-500 text-sm">
            No inbound connections yet
          </div>
        ) : (
          inboundPeers.map((p: any) => {
            const node: NodeInfo = p.node || p
            const connectedSinceSec: number | undefined = p.connected_since
            const displayName = getNodeDisplayName(node)
            return (
              <div
                key={node.id}
                className={clsx(
                  'rounded-lg border px-3 py-2.5 transition-all',
                  'bg-primary-500/5 border-primary-500/20'
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-white truncate">{displayName}</div>
                    {(node.anId || node.agentId || node.id) && (
                      <div className="text-[9px] font-mono text-gray-500 truncate" title={node.anId || node.agentId || node.id}>
                        AN: {truncateId(node.anId || node.agentId || node.id, 20)}
                      </div>
                    )}
                    <div className="text-xs text-gray-500 truncate">
                      {node.ip}:{node.port}
                      {connectedSinceSec ? (
                        <span className="ml-2 text-gray-600">• {formatDurationSecs(Math.max(0, Math.floor((Date.now() / 1000) - connectedSinceSec)))}</span>
                      ) : null}
                    </div>
                  </div>
                  <span className="px-2 py-1 rounded-lg text-xs font-medium bg-green-500/20 text-green-400">
                    Online
                  </span>
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
