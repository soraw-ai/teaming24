import { useEffect, useRef } from 'react'
import clsx from 'clsx'
import { useAgentStore, type AgentMessage } from '../../store/agentStore'
import { SYSTEM_ID } from '../../utils/ids'
import { formatDateTime } from '../../utils/date'

interface MessageFlowProps {
  maxHeight?: string
}

export default function MessageFlow({ maxHeight = '300px' }: MessageFlowProps) {
  const { messages, agents } = useAgentStore()
  const containerRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [messages])

  const getAgentName = (agentId: string) => {
    if (agentId === SYSTEM_ID) return 'System'
    const agent = agents.find(a => a.id === agentId)
    return agent?.name || agentId
  }

  const getMessageTypeStyle = (type: AgentMessage['type']) => {
    switch (type) {
      case 'request':
        return 'border-l-blue-500 bg-blue-500/5'
      case 'response':
        return 'border-l-green-500 bg-green-500/5'
      case 'event':
        return 'border-l-purple-500 bg-purple-500/5'
      case 'error':
        return 'border-l-red-500 bg-red-500/5'
      default:
        return 'border-l-gray-500 bg-gray-500/5'
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-dark-border">
        <h3 className="text-sm font-medium text-gray-400">Agent Messages</h3>
        <span className="text-xs text-gray-500">{messages.length} messages</span>
      </div>

      {/* Messages */}
      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto thin-scrollbar p-3 space-y-2"
        style={{ maxHeight }}
      >
        {messages.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-500 text-sm">
            No messages yet
          </div>
        ) : (
          messages.map((msg) => (
            <div
              key={msg.id}
              className={clsx(
                'p-3 rounded-lg border-l-2',
                getMessageTypeStyle(msg.type)
              )}
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2 text-xs">
                  <span className="text-primary-400 font-medium">
                    {getAgentName(msg.fromAgent)}
                  </span>
                  <span className="text-gray-500">→</span>
                  <span className="text-green-400 font-medium">
                    {getAgentName(msg.toAgent)}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className={clsx(
                    'px-1.5 py-0.5 rounded text-[10px] uppercase font-medium',
                    msg.type === 'request' && 'bg-blue-500/20 text-blue-400',
                    msg.type === 'response' && 'bg-green-500/20 text-green-400',
                    msg.type === 'event' && 'bg-purple-500/20 text-purple-400',
                    msg.type === 'error' && 'bg-red-500/20 text-red-400',
                  )}>
                    {msg.type}
                  </span>
                  <span className="text-xs text-gray-500">
                    {formatDateTime(msg.timestamp)}
                  </span>
                </div>
              </div>
              <p className="text-sm text-gray-300">{msg.content}</p>
              {msg.metadata && Object.keys(msg.metadata).length > 0 && (
                <details className="mt-2">
                  <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-400">
                    Metadata
                  </summary>
                  <pre className="mt-1 text-xs text-gray-500 bg-dark-bg p-2 rounded overflow-x-auto">
                    {JSON.stringify(msg.metadata, null, 2)}
                  </pre>
                </details>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
