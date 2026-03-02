import { useEffect, useRef, useMemo } from 'react'
import { 
  TrashIcon,
  ArrowDownTrayIcon,
  CalendarIcon,
  ArrowsPointingOutIcon
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { useAgentStore, type LogEntry } from '../../store/agentStore'
import { useNetworkStore, getNodeDisplayName } from '../../store/networkStore'
import { formatDateTime, formatTimeCompact } from '../../utils/date'
import { LOCAL_COORDINATOR_NAME } from '../../utils/ids'

interface LogViewerProps {
  filter?: {
    agentId?: string
    taskId?: string
    level?: LogEntry['level']
  }
  maxHeight?: string
  /** Called when the user clicks the expand button. */
  onExpand?: () => void
}

interface LogGroup {
  date: string
  dateDisplay: string
  logs: LogEntry[]
}

interface AgentInfo {
  name: string
  role: string
  location: string
  roleColor: string
}

export default function LogViewer({ filter, maxHeight = '400px', onExpand }: LogViewerProps) {
  const { logs, clearLogs, agents, tasks } = useAgentStore()
  const { wanNodes } = useNetworkStore()
  const containerRef = useRef<HTMLDivElement>(null)
  const autoScrollRef = useRef(true)

  // Filter logs
  const filteredLogs = logs.filter(log => {
    if (filter?.agentId && log.agentId !== filter.agentId) return false
    if (filter?.taskId && log.taskId !== filter.taskId) return false
    if (filter?.level && log.level !== filter.level) return false
    return true
  })

  // Group logs by date
  const groupedLogs = useMemo(() => {
    const groups: LogGroup[] = []
    let currentDate = ''

    filteredLogs.forEach(log => {
      const date = new Date(log.timestamp)
      const dateStr = date.toISOString().split('T')[0] // YYYY-MM-DD

      if (dateStr !== currentDate) {
        currentDate = dateStr
        const today = new Date().toISOString().split('T')[0]
        const yesterday = new Date(Date.now() - 86400000).toISOString().split('T')[0]

        let dateDisplay = dateStr
        if (dateStr === today) {
          dateDisplay = 'Today'
        } else if (dateStr === yesterday) {
          dateDisplay = 'Yesterday'
        } else {
          // Format as "Jan 18, 2026"
          dateDisplay = date.toLocaleDateString('en-US', {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
          })
        }

        groups.push({
          date: dateStr,
          dateDisplay,
          logs: []
        })
      }

      groups[groups.length - 1].logs.push(log)
    })

    return groups
  }, [filteredLogs])

  // Auto-scroll to bottom
  useEffect(() => {
    if (autoScrollRef.current && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [filteredLogs])

  const handleScroll = () => {
    if (containerRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = containerRef.current
      autoScrollRef.current = scrollHeight - scrollTop - clientHeight < 50
    }
  }

  const getAgentInfo = (agentId?: string): AgentInfo | null => {
    if (!agentId) return null

    // Check local agents first
    const localAgent = agents.find(a => a.id === agentId)
    if (localAgent) {
      const roleMap: Record<string, { role: string; color: string }> = {
        'organizer': { role: 'Organizer', color: 'text-purple-400' },
        'coordinator': { role: LOCAL_COORDINATOR_NAME, color: 'text-blue-400' },
        'worker': { role: 'Worker', color: 'text-green-400' },
      }
      const roleInfo = roleMap[localAgent.type] || { role: localAgent.type, color: 'text-gray-400' }
      
      return {
        name: localAgent.name,
        role: roleInfo.role,
        location: 'Local',
        roleColor: roleInfo.color,
      }
    }

    // Check remote nodes (WAN connected)
    const remoteNode = wanNodes.find(n => n.id === agentId || n.remoteId === agentId)
    if (remoteNode) {
      // Use IP:port as location
      let location = remoteNode.endpoint || `${remoteNode.ip}:${remoteNode.port}`
      if (remoteNode.endpoint) {
        try {
          const url = new URL(remoteNode.endpoint)
          location = url.hostname
        } catch (e) {
          console.debug('Failed to parse endpoint URL:', e);
        }
      }

      return {
        name: getNodeDisplayName(remoteNode),
        role: `Remote ${remoteNode.type?.toUpperCase() || 'WAN'}`,
        location: location,
        roleColor: 'text-orange-400',
      }
    }

    // Unknown agent
    return {
      name: agentId.slice(0, 8),
      role: 'Unknown',
      location: '?',
      roleColor: 'text-gray-500',
    }
  }

  const getTaskName = (taskId?: string) => {
    if (!taskId) return null
    const task = tasks.find(t => t.id === taskId)
    return task?.name || taskId.slice(0, 8)
  }

  const exportLogs = () => {
    const content = filteredLogs.map(log => {
      const agentInfo = getAgentInfo(log.agentId)
      const agentStr = agentInfo 
        ? `[${agentInfo.name}] [${agentInfo.role}] [${agentInfo.location}]` 
        : ''
      return `[${formatDateTime(log.timestamp)}] [${log.level.toUpperCase()}] ${agentStr} ${log.message}`
    }).join('\n')
    
    const blob = new Blob([content], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `teaming24-logs-${new Date().toISOString().split('T')[0]}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-dark-border">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-medium text-gray-400">Logs</h3>
          <span className="text-xs text-gray-500">({filteredLogs.length})</span>
        </div>
        <div className="flex items-center gap-2">
          {onExpand && (
            <button
              onClick={onExpand}
              className="p-1.5 hover:bg-dark-hover rounded transition-colors"
              title="Expand logs"
            >
              <ArrowsPointingOutIcon className="w-4 h-4 text-gray-400" />
            </button>
          )}
          <button
            onClick={exportLogs}
            className="p-1.5 hover:bg-dark-hover rounded transition-colors"
            title="Export logs"
          >
            <ArrowDownTrayIcon className="w-4 h-4 text-gray-400" />
          </button>
          <button
            onClick={clearLogs}
            className="p-1.5 hover:bg-dark-hover rounded transition-colors"
            title="Clear logs"
          >
            <TrashIcon className="w-4 h-4 text-gray-400" />
          </button>
        </div>
      </div>

      {/* Log entries */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto thin-scrollbar font-mono text-xs"
        style={{ maxHeight }}
      >
        {filteredLogs.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-500">
            No logs to display
          </div>
        ) : (
          <div className="p-2">
            {groupedLogs.map((group) => (
              <div key={group.date}>
                {/* Date separator */}
                <div className="flex items-center gap-2 py-2 sticky top-0 bg-dark-surface/95 backdrop-blur-sm z-10">
                  <div className="flex-1 h-px bg-dark-border" />
                  <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-dark-hover text-gray-500">
                    <CalendarIcon className="w-3 h-3" />
                    <span className="text-[10px] font-medium">{group.dateDisplay}</span>
                    <span className="text-[10px] text-gray-600">({group.date})</span>
                  </div>
                  <div className="flex-1 h-px bg-dark-border" />
                </div>

                {/* Logs for this date */}
                <div className="space-y-0.5">
                  {group.logs.map((log) => {
                    const agentInfo = getAgentInfo(log.agentId)
                    
                    return (
                      <div
                        key={log.id}
                        className={clsx(
                          'flex items-start gap-2 px-2 py-1 rounded hover:bg-dark-hover/50',
                          log.level === 'error' && 'text-red-400',
                          log.level === 'warn' && 'text-yellow-400',
                          log.level === 'info' && 'text-gray-400',
                          log.level === 'debug' && 'text-gray-500',
                        )}
                      >
                        {/* Time */}
                        <span className="text-gray-600 shrink-0" title={formatDateTime(log.timestamp)}>
                          {formatTimeCompact(log.timestamp)}
                        </span>
                        
                        {/* Level */}
                        <span className={clsx(
                          'shrink-0 w-12 text-center uppercase font-medium',
                          log.level === 'error' && 'text-red-500',
                          log.level === 'warn' && 'text-yellow-500',
                          log.level === 'info' && 'text-blue-500',
                          log.level === 'debug' && 'text-gray-600',
                        )}>
                          {log.level}
                        </span>
                        
                        {/* Agent info: Name [Role] [Location] */}
                        {agentInfo && (
                          <span className="shrink-0 flex items-center gap-1">
                            <span className="text-gray-300">{agentInfo.name}</span>
                            <span className={clsx('px-1 rounded text-[10px]', agentInfo.roleColor)}>
                              [{agentInfo.role}]
                            </span>
                            <span className={clsx(
                              'px-1 rounded text-[10px]',
                              agentInfo.location === 'Local' ? 'text-cyan-400' : 'text-yellow-400'
                            )}>
                              [{agentInfo.location}]
                            </span>
                          </span>
                        )}
                        
                        {/* Task */}
                        {log.taskId && (
                          <span className="shrink-0 text-pink-400">
                            Task:{getTaskName(log.taskId)}
                          </span>
                        )}
                        
                        {/* Message */}
                        <span className="flex-1 break-all">{log.message}</span>
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
