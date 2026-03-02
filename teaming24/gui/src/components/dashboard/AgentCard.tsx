import { useState } from 'react'
import { createPortal } from 'react-dom'
import { 
  GlobeAltIcon,
  PlayIcon,
  StopIcon,
  TrashIcon,
  CpuChipIcon,
  WrenchScrewdriverIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import type { Agent, AgentType } from '../../store/agentStore'
import { useAgentStore } from '../../store/agentStore'
import { LOCAL_COORDINATOR_NAME } from '../../utils/ids'
import { agentStatusConfig } from '../../utils/statusConfig'

interface AgentCardProps {
  agent: Agent
  isSelected: boolean
  onClick: () => void
}

const statusConfig = agentStatusConfig

const typeConfig: Record<AgentType, { 
  label: string
  shortLabel: string
  bgColor: string
  iconColor: string
}> = {
  organizer: { 
    label: 'Organizer',
    shortLabel: 'Organizer',
    bgColor: 'bg-purple-500/20',
    iconColor: 'text-purple-400'
  },
  coordinator: { 
    label: LOCAL_COORDINATOR_NAME,
    shortLabel: LOCAL_COORDINATOR_NAME,
    bgColor: 'bg-blue-500/20',
    iconColor: 'text-blue-400'
  },
  worker: { 
    label: 'Worker',
    shortLabel: 'Worker',
    bgColor: 'bg-green-500/20',
    iconColor: 'text-green-400'
  },
  agentic_node: { 
    label: 'Remote Agentic Node',
    shortLabel: 'Remote AN',
    bgColor: 'bg-orange-500/20',
    iconColor: 'text-orange-400'
  },
}

export default function AgentCard({ agent: agentProp, isSelected, onClick }: AgentCardProps) {
  const { agents: allAgents, updateAgent, removeAgent, addLog, deleteAgentFromDB } = useAgentStore()
  // Subscribe to live agent from store for real-time status updates
  const agent = allAgents.find(a => a.id === agentProp.id) || agentProp
  const [showActions, setShowActions] = useState(false)
  const [confirmAction, setConfirmAction] = useState<'delete' | 'offline' | null>(null)

  const status = statusConfig[agent.status as keyof typeof statusConfig] || statusConfig.offline
  const isAgenticNode = agent.type === 'agentic_node'
  const isWorker = agent.type === 'worker'
  const nodeLinkType = isAgenticNode ? String(agent.metadata?.type || '') : ''
  const typeInfo = (() => {
    if (!isAgenticNode) {
      return typeConfig[agent.type as keyof typeof typeConfig] || typeConfig.worker
    }
    if (nodeLinkType === 'lan') {
      return {
        ...typeConfig.agentic_node,
        label: 'LAN Agentic Node',
        shortLabel: 'LAN AN',
        bgColor: 'bg-green-500/20',
        iconColor: 'text-green-400',
      }
    }
    if (nodeLinkType === 'wan') {
      return {
        ...typeConfig.agentic_node,
        label: 'WAN Agentic Node',
        shortLabel: 'WAN AN',
        bgColor: 'bg-orange-500/20',
        iconColor: 'text-orange-400',
      }
    }
    return typeConfig.agentic_node
  })()
  const safeTypeInfo = typeInfo || typeConfig.worker
  const StatusIcon = status.icon

  const formatLastSeen = (timestamp: number) => {
    const diff = Date.now() - timestamp
    if (diff < 60000) return 'Just now'
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
    return new Date(timestamp).toLocaleDateString()
  }

  const handleToggleStatus = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (agent.status === 'offline') {
      updateAgent(agent.id, { status: 'idle' })
      addLog({ level: 'info', agentId: agent.id, message: `${agent.name} brought online` })
    } else {
      // Needs confirmation for going offline
      setConfirmAction('offline')
    }
  }

  const handleDeleteClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    setConfirmAction('delete')
  }

  const executeConfirm = () => {
    if (confirmAction === 'delete') {
      removeAgent(agent.id)
      deleteAgentFromDB(agent.id)
      addLog({ level: 'warn', agentId: agent.id, message: `${agent.name} deleted from agent pool` })
    } else if (confirmAction === 'offline') {
      updateAgent(agent.id, { status: 'offline', currentTask: undefined })
      addLog({ level: 'warn', agentId: agent.id, message: `${agent.name} taken offline` })
    }
    setConfirmAction(null)
  }

  return (
    <>
      <div
        onClick={onClick}
        onMouseEnter={() => setShowActions(true)}
        onMouseLeave={() => setShowActions(false)}
        className={clsx(
          'p-4 rounded-xl border cursor-pointer transition-all relative',
          isSelected
            ? 'border-primary-500 bg-primary-500/10'
            : 'border-dark-border bg-dark-surface hover:border-dark-hover'
        )}
      >
        {/* Quick Action Buttons (visible on hover for workers) */}
        {showActions && isWorker && !isAgenticNode && (
          <div className="absolute top-2 right-2 flex items-center gap-1 z-10">
            {agent.status === 'offline' ? (
              <button
                onClick={handleToggleStatus}
                className="p-1.5 rounded-lg bg-dark-bg/80 backdrop-blur-sm hover:bg-green-500/20 transition-colors"
                title="Bring online"
              >
                <PlayIcon className="w-4 h-4 text-green-400" />
              </button>
            ) : (
              <button
                onClick={handleToggleStatus}
                className="p-1.5 rounded-lg bg-dark-bg/80 backdrop-blur-sm hover:bg-red-500/20 transition-colors"
                title="Take offline"
              >
                <StopIcon className="w-4 h-4 text-red-400" />
              </button>
            )}
            <button
              onClick={handleDeleteClick}
              className="p-1.5 rounded-lg bg-dark-bg/80 backdrop-blur-sm hover:bg-red-500/20 transition-colors"
              title="Delete agent"
            >
              <TrashIcon className="w-4 h-4 text-red-400" />
            </button>
          </div>
        )}

        {/* Header */}
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-3">
            {/* Agent avatar: initials from name with type-colored background */}
            <div className={clsx(
              'w-10 h-10 rounded-lg flex items-center justify-center relative',
              safeTypeInfo.bgColor,
              agent.status === 'busy' && 'ring-2 ring-blue-400/50 animate-pulse'
            )}>
              <span className={clsx('text-sm font-bold', safeTypeInfo.iconColor)}>
                {agent.name
                  .split(/[\s_-]+/)
                  .filter(Boolean)
                  .slice(0, 2)
                  .map(w => w[0]?.toUpperCase() || '')
                  .join('')
                  || agent.name.substring(0, 2).toUpperCase()}
              </span>
              {/* Status dot overlay */}
              <span className={clsx(
                'absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-dark-surface',
                agent.status === 'online' && 'bg-green-400',
                agent.status === 'idle' && 'bg-gray-400',
                agent.status === 'busy' && 'bg-blue-400',
                agent.status === 'error' && 'bg-red-400',
                agent.status === 'offline' && 'bg-gray-600',
              )} />
            </div>
            <div>
              <h3 className="font-medium text-white">{agent.name}</h3>
              <p className="text-xs text-gray-500">
                {isAgenticNode ? (
                  <span className="flex items-center gap-1">
                    <GlobeAltIcon className="w-3 h-3" />
                    {nodeLinkType === 'lan' ? 'via LAN' : nodeLinkType === 'wan' ? 'via WAN' : 'via AgentaNet'}
                  </span>
                ) : (
                  safeTypeInfo.label
                )}
              </p>
            </div>
          </div>
          <div className={clsx('flex items-center gap-1', status.color)}>
            <StatusIcon className="w-4 h-4" />
            <span className="text-xs">{status.label}</span>
          </div>
        </div>

        {/* Type Badge */}
        <div className="mb-3">
          <span className={clsx(
            'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium',
            safeTypeInfo.bgColor, safeTypeInfo.iconColor
          )}>
            {safeTypeInfo.shortLabel}
          </span>
          {isAgenticNode && agent.metadata?.region ? (
            <span className="ml-2 text-xs text-gray-500">
              {String(agent.metadata.region)}
            </span>
          ) : null}
        </div>

        {/* Current Task */}
        {agent.currentTask && (
          <div className="mb-3 p-2 rounded-lg bg-dark-bg">
            <p className="text-xs text-gray-400">Current Task</p>
            <p className="text-sm text-gray-200 truncate">{agent.currentTask}</p>
          </div>
        )}

        {/* Capabilities */}
        {agent.capabilities.length > 0 && (
          <div className="flex flex-wrap gap-1 mb-3">
            {agent.capabilities.slice(0, 3).map((cap) => (
              <span
                key={cap.name}
                className="px-2 py-0.5 text-xs rounded-full bg-dark-bg text-gray-400"
              >
                {cap.name}
              </span>
            ))}
            {agent.capabilities.length > 3 && (
              <span className="px-2 py-0.5 text-xs rounded-full bg-dark-bg text-gray-500">
                +{agent.capabilities.length - 3}
              </span>
            )}
          </div>
        )}

        {/* Model & Tools compact row */}
        {(agent.model || (agent.tools && agent.tools.length > 0)) && (
          <div className="flex items-center gap-3 mb-3 text-xs text-gray-500">
            {agent.model && (
              <span className="flex items-center gap-1" title={agent.model}>
                <CpuChipIcon className="w-3 h-3" />
                {agent.model.split('/').pop()}
              </span>
            )}
            {agent.tools && agent.tools.length > 0 && (
              <span className="flex items-center gap-1">
                <WrenchScrewdriverIcon className="w-3 h-3" />
                {agent.tools.length} tool{agent.tools.length !== 1 ? 's' : ''}
              </span>
            )}
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-between text-xs text-gray-500">
          <span>ID: {agent.id.slice(0, 10)}</span>
          <span>Last seen: {formatLastSeen(agent.lastSeen)}</span>
        </div>
      </div>

      {/* Confirmation Dialog — portal to escape stacking context */}
      {confirmAction && createPortal(
        <div
          className="fixed inset-0 z-[99999] flex items-center justify-center bg-black/60 backdrop-blur-md"
          onClick={(e) => { e.stopPropagation(); setConfirmAction(null) }}
        >
          <div
            className="w-full max-w-sm mx-4 bg-dark-surface border border-dark-border rounded-xl shadow-2xl overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="p-5">
              <h3 className="text-lg font-semibold text-white mb-2">
                {confirmAction === 'delete' ? 'Delete Agent' : 'Take Offline'}
              </h3>
              <p className="text-sm text-gray-400">
                {confirmAction === 'delete'
                  ? `Delete "${agent.name}" from the agent pool? This cannot be undone.`
                  : `Take "${agent.name}" offline? It will no longer be assigned tasks.`
                }
              </p>
            </div>
            <div className="flex gap-3 px-5 py-4 border-t border-dark-border bg-dark-bg/50">
              <button
                onClick={(e) => { e.stopPropagation(); setConfirmAction(null) }}
                className="flex-1 px-4 py-2 text-gray-400 hover:text-white hover:bg-dark-hover rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); executeConfirm() }}
                className={clsx(
                  'flex-1 px-4 py-2 text-white rounded-lg transition-colors',
                  confirmAction === 'delete'
                    ? 'bg-red-500 hover:bg-red-600'
                    : 'bg-yellow-600 hover:bg-yellow-700'
                )}
              >
                {confirmAction === 'delete' ? 'Delete' : 'Take Offline'}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </>
  )
}
