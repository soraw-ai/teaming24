import { useState, useEffect, useCallback } from 'react'
import {
  ChatBubbleLeftRightIcon,
  ArrowPathIcon,
  CheckCircleIcon,
  XCircleIcon,
  KeyIcon,
} from '@heroicons/react/24/outline'
import { getApiBase } from '../../utils/api'

interface ChannelInfo {
  id: string
  enabled: boolean
  connected: boolean
  has_token: boolean
}

const CHANNEL_META: Record<string, { label: string; color: string; icon: string }> = {
  telegram: { label: 'Telegram', color: 'text-blue-400', icon: '✈' },
  slack:    { label: 'Slack',    color: 'text-green-400', icon: '#' },
  discord:  { label: 'Discord',  color: 'text-indigo-400', icon: '🎮' },
  webchat:  { label: 'WebChat',  color: 'text-amber-400', icon: '💬' },
}

export default function ChannelsPanel() {
  const [channels, setChannels] = useState<ChannelInfo[]>([])
  const [loading, setLoading] = useState(false)

  const fetchChannels = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`${getApiBase()}/api/channels`)
      if (res.ok) {
        const data = await res.json()
        setChannels(data.channels || [])
      }
    } catch (e) { console.warn('ChannelsPanel error:', e); }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { fetchChannels() }, [fetchChannels])

  const enabledCount = channels.filter(c => c.enabled).length
  const connectedCount = channels.filter(c => c.connected).length

  return (
    <div className="bg-dark-surface border border-dark-border rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <ChatBubbleLeftRightIcon className="w-5 h-5 text-cyan-400" />
          <h3 className="text-sm font-semibold text-white">Channels</h3>
          <span className="text-xs text-gray-500">
            {connectedCount}/{enabledCount} active
          </span>
        </div>
        <button onClick={fetchChannels} className="p-1.5 text-gray-400 hover:text-gray-200 transition-colors">
          <ArrowPathIcon className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {channels.length === 0 ? (
        <p className="text-xs text-gray-500 text-center py-6">
          No channels configured. Add bot tokens in Settings → Channels.
        </p>
      ) : (
        <div className="space-y-2">
          {channels.map(ch => {
            const meta = CHANNEL_META[ch.id] || { label: ch.id, color: 'text-gray-400', icon: '?' }
            return (
              <div key={ch.id} className="flex items-center justify-between p-3 bg-dark-bg rounded-lg border border-dark-border">
                <div className="flex items-center gap-3">
                  <span className="text-lg w-6 text-center">{meta.icon}</span>
                  <div>
                    <span className={`text-sm font-medium ${meta.color}`}>{meta.label}</span>
                    <div className="flex items-center gap-2 mt-0.5">
                      {!ch.has_token && ch.id !== 'webchat' && (
                        <span className="flex items-center gap-1 text-xs text-yellow-500">
                          <KeyIcon className="w-3 h-3" /> No token
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {ch.enabled ? (
                    ch.connected ? (
                      <span className="flex items-center gap-1 text-xs text-green-400">
                        <CheckCircleIcon className="w-4 h-4" /> Connected
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-xs text-amber-400">
                        <span className="w-2 h-2 rounded-full bg-amber-400" /> Enabled
                      </span>
                    )
                  ) : (
                    <span className="flex items-center gap-1 text-xs text-gray-500">
                      <XCircleIcon className="w-4 h-4" /> Disabled
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
      <p className="text-xs text-gray-500 mt-3">
        Configure bot tokens and enable channels in Settings → Channels
      </p>
    </div>
  )
}
