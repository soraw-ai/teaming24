import { useState, useEffect, useCallback } from 'react'
import {
  BoltIcon,
  ArrowPathIcon,
  SignalIcon,
  ChatBubbleLeftRightIcon,
  ClipboardDocumentListIcon,
  ExclamationTriangleIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { getApiBase } from '../../utils/api'
import { formatUptime } from '../../utils/format'

interface GatewayStatus {
  running: boolean
  uptime_seconds: number
  channels_active: number
  channel_adapters: string[]
  total_messages: number
  total_tasks: number
  errors: number
  sessions_active: number
}

export default function GatewayPanel() {
  const [status, setStatus] = useState<GatewayStatus | null>(null)
  const [restarting, setRestarting] = useState(false)

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/gateway/status`)
      if (res.ok) setStatus(await res.json())
    } catch (e) { console.warn('GatewayPanel error:', e); }
  }, [])

  useEffect(() => {
    fetchStatus()
    const timer = setInterval(fetchStatus, 10_000)
    return () => clearInterval(timer)
  }, [fetchStatus])

  const handleRestart = async () => {
    setRestarting(true)
    try {
      const res = await fetch(`${getApiBase()}/api/gateway/restart`, { method: 'POST' })
      if (res.ok) setStatus(await res.json())
    } catch (e) { console.warn('GatewayPanel error:', e); }
    setRestarting(false)
  }

  const channelLabel = (key: string) => {
    const [platform] = key.split(':')
    return platform.charAt(0).toUpperCase() + platform.slice(1)
  }

  return (
    <div className="rounded-xl bg-dark-surface border border-dark-border overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-dark-border">
        <div className="flex items-center gap-2">
          <BoltIcon className="w-5 h-5 text-primary-400" />
          <h3 className="text-sm font-semibold text-white">Gateway</h3>
          {status && (
            <span className={clsx(
              'px-2 py-0.5 rounded-full text-xs',
              status.running ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
            )}>
              {status.running ? 'Running' : 'Stopped'}
            </span>
          )}
        </div>
        <button
          onClick={handleRestart}
          disabled={restarting}
          className="flex items-center gap-1 px-2.5 py-1 text-xs text-gray-400 hover:text-gray-200 bg-dark-bg hover:bg-dark-hover rounded-lg transition-colors disabled:opacity-50"
        >
          <ArrowPathIcon className={clsx('w-3.5 h-3.5', restarting && 'animate-spin')} />
          {restarting ? 'Restarting...' : 'Restart'}
        </button>
      </div>

      {/* Stats */}
      {status ? (
        <div className="p-4 space-y-4">
          {/* Counters */}
          <div className="grid grid-cols-4 gap-3">
            <div className="p-3 bg-dark-bg rounded-lg text-center">
              <ChatBubbleLeftRightIcon className="w-5 h-5 mx-auto text-blue-400 mb-1" />
              <div className="text-lg font-semibold text-white">{status.total_messages}</div>
              <div className="text-xs text-gray-500">Messages</div>
            </div>
            <div className="p-3 bg-dark-bg rounded-lg text-center">
              <ClipboardDocumentListIcon className="w-5 h-5 mx-auto text-green-400 mb-1" />
              <div className="text-lg font-semibold text-white">{status.total_tasks}</div>
              <div className="text-xs text-gray-500">Tasks</div>
            </div>
            <div className="p-3 bg-dark-bg rounded-lg text-center">
              <SignalIcon className="w-5 h-5 mx-auto text-purple-400 mb-1" />
              <div className="text-lg font-semibold text-white">{status.sessions_active}</div>
              <div className="text-xs text-gray-500">Sessions</div>
            </div>
            <div className="p-3 bg-dark-bg rounded-lg text-center">
              <ExclamationTriangleIcon className="w-5 h-5 mx-auto text-orange-400 mb-1" />
              <div className="text-lg font-semibold text-white">{status.errors}</div>
              <div className="text-xs text-gray-500">Errors</div>
            </div>
          </div>

          {/* Channel adapters */}
          <div>
            <h4 className="text-xs font-medium text-gray-500 mb-2">
              Channel Adapters ({status.channels_active})
            </h4>
            <div className="flex flex-wrap gap-2">
              {status.channel_adapters.map(key => (
                <span key={key}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-dark-bg text-xs text-gray-300">
                  <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
                  {channelLabel(key)}
                </span>
              ))}
              {status.channel_adapters.length === 0 && (
                <span className="text-xs text-gray-500">No adapters active</span>
              )}
            </div>
          </div>

          {/* Uptime */}
          {status.running && (
            <div className="text-xs text-gray-500">
              Uptime: {formatUptime(status.uptime_seconds)}
            </div>
          )}
        </div>
      ) : (
        <div className="p-4 text-center text-gray-500 text-sm">
          Loading gateway status...
        </div>
      )}
    </div>
  )
}
