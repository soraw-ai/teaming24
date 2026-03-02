import { useState } from 'react'
import { createPortal } from 'react-dom'
import { 
  ArchiveBoxIcon,
  ChevronRightIcon,
  TrashIcon,
  UserGroupIcon,
  CloudIcon,
  ArrowPathIcon,
  SignalIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import type { Task } from '../../store/agentStore'
import { useAgentStore } from '../../store/agentStore'
import { taskStatusConfig } from '../../utils/statusConfig'
import { formatDateTime } from '../../utils/date'
import { getApiBase } from '../../utils/api'
import { ORGANIZER_ID, COORDINATOR_ID, LOCAL_COORDINATOR_NAME } from '../../utils/ids'
import { applyArchiveWithUndo, queueTaskDeleteWithUndo } from '../../utils/taskUndo'
import { reportUiError } from '../../utils/errorReporting'

interface TaskCardProps {
  task: Task
  isSelected: boolean
  onClick: () => void
  onDelete?: () => void
  onArchive?: () => void
}

const statusConfig = taskStatusConfig

export default function TaskCard({ task: taskProp, isSelected, onClick, onDelete, onArchive }: TaskCardProps) {
  const {
    agents,
    tasks: allTasks,
    deleteTask,
    archiveTask,
    updateTask,
    unreadTaskIds,
    createTask,
  } = useAgentStore()
  // Subscribe to live task from store for real-time updates
  const task = allTasks.find(t => t.id === taskProp.id) || taskProp
  const isUnread = unreadTaskIds.has(task.id)
  const [showActions, setShowActions] = useState(false)
  const [confirmAction, setConfirmAction] = useState<'delete' | 'archive' | 'retry' | null>(null)
  const [isRetrying, setIsRetrying] = useState(false)
  
  const status = statusConfig[task.status as keyof typeof statusConfig]
    || statusConfig.pending
    || { label: 'Pending', color: 'text-gray-400', bgColor: 'bg-gray-500/20', icon: ArrowPathIcon }
  const StatusIcon = status.icon

  // Phase-based progress from real-time tracking
  const progress = task.progress?.percentage ?? 0
  const phaseLabel = task.progress?.phaseLabel ?? ''
  const totalWorkers = task.progress?.totalWorkers ?? 0
  const completedWorkers = task.progress?.completedWorkers ?? 0

  // Get executing agents info
  const executingAgentIds = task.executingAgents || task.delegatedAgents || []
  const assignedTo = task.assignedTo
  const isRemoteAN = assignedTo?.startsWith('remote-') || assignedTo?.includes('an-')

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation()
    setConfirmAction('delete')
  }

  const handleArchive = (e: React.MouseEvent) => {
    e.stopPropagation()
    setConfirmAction('archive')
  }

  const handleRetry = (e: React.MouseEvent) => {
    e.stopPropagation()
    setConfirmAction('retry')
  }

  const executeConfirm = async () => {
    if (confirmAction === 'delete') {
      if (onDelete) {
        onDelete()
      } else {
        queueTaskDeleteWithUndo(task, { deleteTask })
      }
    } else if (confirmAction === 'archive') {
      if (onArchive) {
        onArchive()
      } else {
        applyArchiveWithUndo(task, { archiveTask, updateTask })
      }
    } else if (confirmAction === 'retry') {
      setIsRetrying(true)
      try {
        const apiBase = getApiBase()
        const res = await fetch(`${apiBase}/api/agent/execute`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task: task.description || task.name }),
        })
        if (res.ok) {
          const data = await res.json()
          createTask({
            id: data.task_id,
            name: `[Retry] ${task.name}`,
            description: task.description,
            assignedAgents: [ORGANIZER_ID],
          })
        }
      } catch (err) {
        reportUiError({
          source: 'TaskCard',
          title: 'Retry Failed',
          userMessage: `Failed to retry "${task.name}".`,
          error: err,
        })
      } finally {
        setIsRetrying(false)
      }
    }
    setConfirmAction(null)
  }

  const confirmLabels: Record<string, { title: string; message: string; label: string; color: string }> = {
    delete: {
      title: 'Delete Task',
      message: `Delete "${task.name}"? This cannot be undone.`,
      label: 'Delete',
      color: 'bg-red-500 hover:bg-red-600',
    },
    archive: {
      title: 'Archive Task',
      message: `Archive "${task.name}"? It will be marked as cancelled.`,
      label: 'Archive',
      color: 'bg-yellow-600 hover:bg-yellow-700',
    },
    retry: {
      title: 'Retry Task',
      message: `Re-run this task with the original prompt? A new task will be created.`,
      label: isRetrying ? 'Retrying...' : 'Retry',
      color: 'bg-blue-500 hover:bg-blue-600',
    },
  }

  return (
    <>
      <div
        onClick={onClick}
        onMouseEnter={() => setShowActions(true)}
        onMouseLeave={() => setShowActions(false)}
        className={clsx(
          'p-4 rounded-xl border cursor-pointer transition-all relative overflow-hidden min-w-0',
          isSelected
            ? 'border-primary-500 bg-primary-500/10'
            : task.origin === 'remote'
            ? 'border-cyan-500/30 bg-cyan-500/5 hover:border-cyan-500/50'
            : 'border-dark-border bg-dark-surface hover:border-dark-hover'
        )}
      >
        {/* Quick Action Buttons (visible on hover) */}
        {showActions && (
          <div className="absolute top-2 right-2 flex items-center gap-1 z-10">
            {/* Retry - only for completed/failed tasks */}
            {(task.status === 'completed' || task.status === 'failed') && (
              <button
                onClick={handleRetry}
                className="p-1.5 rounded-lg bg-dark-bg/80 backdrop-blur-sm hover:bg-blue-500/20 transition-colors"
                title="Retry task"
              >
                <ArrowPathIcon className="w-4 h-4 text-blue-400" />
              </button>
            )}
            {task.status !== 'cancelled' && (
              <button
                onClick={handleArchive}
                className="p-1.5 rounded-lg bg-dark-bg/80 backdrop-blur-sm hover:bg-yellow-500/20 transition-colors"
                title="Archive task"
              >
                <ArchiveBoxIcon className="w-4 h-4 text-yellow-400" />
              </button>
            )}
            <button
              onClick={handleDelete}
              className="p-1.5 rounded-lg bg-dark-bg/80 backdrop-blur-sm hover:bg-red-500/20 transition-colors"
              title="Delete task"
            >
              <TrashIcon className="w-4 h-4 text-red-400" />
            </button>
          </div>
        )}
        
        {/* Unread indicator (red dot) */}
        {isUnread && (
          <div className="absolute top-2 left-2 w-2.5 h-2.5 rounded-full bg-red-500 z-10" title="New / Unread" />
        )}

        {/* Header */}
        <div className="flex items-start justify-between mb-2">
          <div className="flex-1 min-w-0 pr-16">
            <div className="flex items-center gap-1.5">
              {task.origin === 'remote' && (
                <SignalIcon className="w-4 h-4 text-cyan-400 shrink-0" title="Received from remote AN" />
              )}
              <h3 className="font-medium text-white truncate" title={task.name}>{task.name}</h3>
            </div>
            <p
              className="text-sm text-gray-500 line-clamp-2 break-all"
              title={task.description}
            >
              {task.description}
            </p>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            {task.origin === 'remote' && (
              <span className="px-1.5 py-0.5 text-[10px] font-medium bg-cyan-500/20 text-cyan-400 rounded">
                Remote
              </span>
            )}
            <div className={clsx('flex items-center gap-1 px-2 py-1 rounded-full', status?.bgColor ?? 'bg-gray-500/20', status?.color ?? 'text-gray-400')}>
              <StatusIcon className="w-3.5 h-3.5" />
              <span className="text-xs font-medium">{status.label}</span>
            </div>
          </div>
        </div>

        {/* Phase-based Progress Bar with smooth animation */}
        {task.status === 'running' && (
          <div className="mb-3">
            <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
              <span className="truncate flex items-center gap-1.5">
                <span className="relative flex h-2 w-2 shrink-0">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-primary-500" />
                </span>
                {phaseLabel || 'Starting...'}
              </span>
              <span className="shrink-0 ml-2 tabular-nums">
                {totalWorkers > 0
                  ? `${completedWorkers}/${totalWorkers} workers · ${progress}%`
                  : `${progress}%`}
              </span>
            </div>
            <div className="h-2 bg-dark-bg rounded-full overflow-hidden">
              <div
                className={clsx(
                  "h-full rounded-full relative overflow-hidden",
                  progress < 25 ? "bg-yellow-500" :
                  progress < 80 ? "bg-primary-500" :
                  "bg-green-500"
                )}
                style={{
                  width: `${Math.max(progress, 2)}%`,
                  transition: 'width 0.8s cubic-bezier(0.4, 0, 0.2, 1)',
                }}
              >
                {/* Animated stripe overlay */}
                <div
                  className="absolute inset-0 opacity-30"
                  style={{
                    backgroundImage: 'linear-gradient(45deg, rgba(255,255,255,0.15) 25%, transparent 25%, transparent 50%, rgba(255,255,255,0.15) 50%, rgba(255,255,255,0.15) 75%, transparent 75%, transparent)',
                    backgroundSize: '16px 16px',
                    animation: 'progress-stripe 0.6s linear infinite',
                  }}
                />
              </div>
            </div>
          </div>
        )}

        {/* Assigned To (Coordinator or Remote AN) */}
        {assignedTo && (
          <div className="flex items-center gap-2 mb-2 min-w-0">
            {isRemoteAN ? (
              <>
                <CloudIcon className="w-4 h-4 text-blue-400 shrink-0" />
                <span className="text-xs text-blue-400 shrink-0">Remote AN:</span>
                <span className="text-xs text-gray-300 truncate" title={assignedTo}>{assignedTo}</span>
              </>
            ) : (
              <>
                <UserGroupIcon className="w-4 h-4 text-purple-400 shrink-0" />
                <span className="text-xs text-purple-400 shrink-0">Assigned:</span>
                <span
                  className="text-xs text-gray-300 truncate"
                  title={assignedTo === COORDINATOR_ID ? LOCAL_COORDINATOR_NAME : assignedTo}
                >
                  {assignedTo === COORDINATOR_ID ? LOCAL_COORDINATOR_NAME : assignedTo}
                </span>
              </>
            )}
          </div>
        )}

        {/* Executing Workers */}
        {executingAgentIds.length > 0 && (
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs text-gray-500">Workers:</span>
            <div className="flex -space-x-2">
              {executingAgentIds.slice(0, 4).map((agentId, index) => {
                const agent = agents.find(a => a.id === agentId)
                const displayName = agent?.name || agentId
                return (
                  <div
                    key={agentId}
                    className="w-6 h-6 rounded-full bg-gradient-to-br from-green-500 to-teal-500 border-2 border-dark-surface flex items-center justify-center"
                    style={{ zIndex: executingAgentIds.length - index }}
                    title={displayName}
                  >
                    <span className="text-[10px] text-white font-medium">
                      {displayName.slice(0, 1).toUpperCase()}
                    </span>
                  </div>
                )
              })}
              {executingAgentIds.length > 4 && (
                <div className="w-6 h-6 rounded-full bg-dark-hover border-2 border-dark-surface flex items-center justify-center">
                  <span className="text-[10px] text-gray-400">+{executingAgentIds.length - 4}</span>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Legacy Assigned Agents (fallback, exclude organizer) */}
        {!assignedTo && !executingAgentIds.length && task.assignedAgents.length > 0 && (
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs text-gray-500">Agents:</span>
            <div className="flex -space-x-2">
              {task.assignedAgents
                .filter(id => !id.toLowerCase().includes('organizer'))
                .slice(0, 4)
                .map((agentId, index) => (
                  <div
                    key={agentId}
                    className="w-6 h-6 rounded-full bg-gradient-to-br from-primary-500 to-purple-500 border-2 border-dark-surface flex items-center justify-center"
                    style={{ zIndex: task.assignedAgents.length - index }}
                  >
                    <span className="text-[10px] text-white font-medium">
                      {agentId.slice(0, 1).toUpperCase()}
                    </span>
                  </div>
                ))}
              {task.assignedAgents.filter(id => !id.toLowerCase().includes('organizer')).length > 4 && (
                <div className="w-6 h-6 rounded-full bg-dark-hover border-2 border-dark-surface flex items-center justify-center">
                  <span className="text-[10px] text-gray-400">
                    +{task.assignedAgents.filter(id => !id.toLowerCase().includes('organizer')).length - 4}
                  </span>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-between text-xs text-gray-500">
          <span className="font-mono">{formatDateTime(task.createdAt)}</span>
          <ChevronRightIcon className="w-4 h-4" />
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
                {confirmLabels[confirmAction].title}
              </h3>
              <p className="text-sm text-gray-400">
                {confirmLabels[confirmAction].message}
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
                disabled={isRetrying}
                className={clsx(
                  'flex-1 px-4 py-2 text-white rounded-lg transition-colors disabled:opacity-50',
                  confirmLabels[confirmAction].color
                )}
              >
                {confirmLabels[confirmAction].label}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </>
  )
}
