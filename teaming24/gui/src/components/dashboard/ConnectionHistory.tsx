/**
 * Connection History (Sessions) - Per-connection session history grouped by node.
 *
 * Default view shows currently connected sessions; disconnected sessions are grouped by node.
 */

import { useEffect, useMemo, useState } from 'react'
import { useNetworkStore, type ConnectionSession, getNodeDisplayName } from '../../store/networkStore'
import { ClockIcon, ChevronDownIcon, ChevronUpIcon, TrashIcon } from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { formatDateTime } from '../../utils/date'
import { formatDurationSecs } from '../../utils/format'

export default function ConnectionHistory() {
  const {
    status,
    wanNodes,
    inboundPeers,
    connectionSessions,
    fetchSessions,
    clearSessions,
  } = useNetworkStore()

  const [tab, setTab] = useState<'connected' | 'history'>('connected')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const isOnline = status === 'online'

  useEffect(() => {
    fetchSessions()
  }, [fetchSessions])

  // Active connections: outbound WAN nodes + inbound peers that connected to us.
  // When the same endpoint is both outbound & inbound, show as 'bidirectional'.
  const activeConnections = useMemo(() => {
    type Dir = 'outbound' | 'inbound' | 'bidirectional'
    const itemMap = new Map<string, { id: string; name: string; ip: string; port: number; direction: Dir; since: number }>()

    // Outbound connections (we connected to them)
    for (const n of wanNodes) {
      if (n.status === 'offline' || !n.connectedSince) continue
      const key = `${n.ip}:${n.port}`
      itemMap.set(key, {
        id: n.id,
        name: getNodeDisplayName(n),
        ip: n.ip,
        port: n.port,
        direction: 'outbound',
        since: n.connectedSince,
      })
    }

    // Inbound connections (they connected to us)
    for (const p of inboundPeers) {
      const node = p.node || p
      if (!node) continue
      const key = `${node.ip}:${node.port}`
      const connSince = p.connected_since
        ? (p.connected_since > 1e12 ? p.connected_since : Math.floor(p.connected_since * 1000))
        : Date.now()
      const existing = itemMap.get(key)
      if (existing) {
        // Same endpoint already tracked as outbound → mark bidirectional
        existing.direction = 'bidirectional'
        existing.since = Math.min(existing.since, connSince)
      } else {
        itemMap.set(key, {
          id: node.id,
          name: getNodeDisplayName(node),
          ip: node.ip,
          port: node.port,
          direction: 'inbound',
          since: connSince,
        })
      }
    }

    return [...itemMap.values()].sort((a, b) => b.since - a.since)
  }, [wanNodes, inboundPeers])

  const grouped = useMemo(() => {
    const groups = new Map<string, { key: string; title: string; sessions: ConnectionSession[] }>()
    for (const s of connectionSessions) {
      const key = `${s.ip}:${s.port}`
      const title = s.alias || s.name || key
      const existing = groups.get(key)
      if (existing) {
        existing.sessions.push(s)
      } else {
        groups.set(key, { key, title, sessions: [s] })
      }
    }
    return [...groups.values()].map(g => ({
      ...g,
      sessions: [...g.sessions].sort((a, b) => b.endedAt - a.endedAt),
    }))
  }, [connectionSessions])

  if (!isOnline && grouped.length === 0) return null

  return (
    <div className="rounded-xl bg-dark-surface border border-dark-border overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 border-b border-dark-border">
        <div className="flex items-center gap-2">
          <ClockIcon className="w-4 h-4 text-gray-400" />
          <h3 className="text-sm font-medium text-gray-300">Connection Sessions</h3>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center rounded-lg bg-dark-bg border border-dark-border overflow-hidden">
            <button
              onClick={() => setTab('connected')}
              className={clsx(
                'px-2.5 py-1 text-xs font-medium transition-colors',
                tab === 'connected' ? 'text-primary-300 bg-primary-500/10' : 'text-gray-500 hover:text-gray-300'
              )}
            >
              Connected
            </button>
            <button
              onClick={() => setTab('history')}
              className={clsx(
                'px-2.5 py-1 text-xs font-medium transition-colors',
                tab === 'history' ? 'text-primary-300 bg-primary-500/10' : 'text-gray-500 hover:text-gray-300'
              )}
            >
              History
            </button>
          </div>
          <button
            onClick={clearSessions}
            className="p-1 text-gray-500 hover:text-red-400 hover:bg-red-500/20 rounded transition-colors"
            title="Clear session history"
          >
            <TrashIcon className="w-4 h-4" />
          </button>
        </div>
      </div>

      {tab === 'connected' ? (
        <div className="max-h-[240px] overflow-y-auto thin-scrollbar divide-y divide-dark-border">
          {activeConnections.length === 0 ? (
            <div className="p-4 text-center text-sm text-gray-500">No active connections</div>
          ) : (
            <>
              {activeConnections.map(c => (
                <div key={c.id} className="px-4 py-2.5">
                  <div className="flex items-center justify-between">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-gray-200 truncate">{c.name}</span>
                        <span className={clsx(
                          'text-[10px] px-1.5 py-0.5 rounded font-medium',
                          c.direction === 'bidirectional'
                            ? 'bg-green-500/20 text-green-400'
                            : c.direction === 'inbound'
                              ? 'bg-cyan-500/20 text-cyan-400'
                              : 'bg-orange-500/20 text-orange-400'
                        )}>
                          {c.direction === 'bidirectional' ? '⇄' : c.direction === 'inbound' ? '→ me' : 'me →'}
                        </span>
                      </div>
                      <div className="text-xs text-gray-500 truncate">{c.ip}:{c.port}</div>
                    </div>
                    <div className="text-xs text-green-400">
                      {formatDurationSecs(Math.floor((Date.now() - c.since) / 1000))}
                    </div>
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      ) : (
        <div className="max-h-[240px] overflow-y-auto thin-scrollbar divide-y divide-dark-border">
          {grouped.length === 0 ? (
            <div className="p-4 text-center text-sm text-gray-500">No session history</div>
          ) : (
            grouped.map(g => {
              const isOpen = expanded.has(g.key)
              const latest = g.sessions[0]
              return (
                <div key={g.key}>
                  <button
                    onClick={() => {
                      setExpanded(prev => {
                        const next = new Set(prev)
                        if (next.has(g.key)) next.delete(g.key)
                        else next.add(g.key)
                        return next
                      })
                    }}
                    className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-dark-hover/30 transition-colors"
                  >
                    <div className="min-w-0 text-left">
                      <div className="text-sm text-gray-300 truncate">{g.title}</div>
                      {latest ? (
                        <div className="text-xs text-gray-600 truncate">
                          {formatDateTime(latest.endedAt)} • {formatDurationSecs(latest.durationSeconds)}
                        </div>
                      ) : null}
                    </div>
                    {isOpen ? (
                      <ChevronUpIcon className="w-4 h-4 text-gray-500" />
                    ) : (
                      <ChevronDownIcon className="w-4 h-4 text-gray-500" />
                    )}
                  </button>

                  {isOpen && (
                    <div className="px-4 pb-3 space-y-2">
                      {g.sessions.slice(0, 20).map(s => (
                        <div key={s.sessionId} className="flex items-center justify-between text-xs">
                          <div className="text-gray-500">{formatDateTime(s.endedAt)}</div>
                          <div className="text-gray-400">{formatDurationSecs(s.durationSeconds)}</div>
                        </div>
                      ))}
                      {g.sessions.length > 20 && (
                        <div className="text-[10px] text-gray-600">+{g.sessions.length - 20} more</div>
                      )}
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}
