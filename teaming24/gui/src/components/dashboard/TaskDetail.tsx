import { lazy, Suspense, useEffect, useState, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { 
  XMarkIcon, 
  CheckIcon,
  PlayIcon,
  ClockIcon,
  XCircleIcon,
  ArrowPathIcon,
  ArrowUpRightIcon,
  StopIcon,
  FolderIcon,
  CurrencyDollarIcon,
  TrashIcon,
  ArchiveBoxIcon,
  ExclamationTriangleIcon,
  ChevronUpIcon,
  ChevronDownIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Task, TaskStep, TaskStatus, WorkerStatusSummary } from '../../store/agentStore'
import { useAgentStore } from '../../store/agentStore'
import { getApiBase } from '../../utils/api'
import { formatDateTime } from '../../utils/date'
import { formatTokenCount, formatDurationSecs, formatDurationFromTimestamps } from '../../utils/format'
import TaskPhaseRail from './TaskPhaseRail'
import { applyArchiveWithUndo, queueTaskDeleteWithUndo } from '../../utils/taskUndo'
import { reportUiError } from '../../utils/errorReporting'

const LazyTaskTopology = lazy(() => import('./TaskTopology'))

// Confirmation Dialog Component
function ConfirmDialog({ 
  isOpen, 
  title, 
  message, 
  confirmLabel = 'Delete',
  confirmColor = 'red',
  onConfirm, 
  onCancel 
}: {
  isOpen: boolean
  title: string
  message: string
  confirmLabel?: string
  confirmColor?: 'red' | 'yellow' | 'blue'
  onConfirm: () => void
  onCancel: () => void
}) {
  if (!isOpen) return null
  
  const colorClasses = {
    red: 'bg-red-500 hover:bg-red-600 text-white',
    yellow: 'bg-yellow-500 hover:bg-yellow-600 text-white',
    blue: 'bg-blue-500 hover:bg-blue-600 text-white',
  }
  
  return createPortal(
    <div className="fixed inset-0 z-[99999] flex items-center justify-center bg-black/60 backdrop-blur-md">
      <div className="w-full max-w-sm bg-dark-surface border border-dark-border rounded-xl shadow-2xl p-6 animate-fade-in">
        <div className="flex items-center gap-3 mb-4">
          <div className="p-2 rounded-full bg-red-500/20">
            <ExclamationTriangleIcon className="w-6 h-6 text-red-400" />
          </div>
          <h3 className="text-lg font-semibold text-white">{title}</h3>
        </div>
        <p className="text-gray-400 mb-6">{message}</p>
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-gray-400 hover:text-white hover:bg-dark-hover rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={clsx('px-4 py-2 rounded-lg transition-colors', colorClasses[confirmColor])}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}

// Task interface now includes all delegation and cost fields in agentStore.ts
// No need for ExtendedTask anymore

interface TaskDetailProps {
  task: Task
  onClose: () => void
}

const stepStatusConfig: Record<TaskStatus, { color: string; icon: typeof CheckIcon }> = {
  pending: { color: 'text-gray-400', icon: ClockIcon },
  running: { color: 'text-blue-400', icon: PlayIcon },
  delegated: { color: 'text-purple-400', icon: ArrowUpRightIcon },
  completed: { color: 'text-green-400', icon: CheckIcon },
  failed: { color: 'text-red-400', icon: XCircleIcon },
  cancelled: { color: 'text-yellow-400', icon: StopIcon },
}

interface TimelineEntry {
  step: TaskStep
  duplicateCount: number
}

function normalizeTimelineAction(action: string): string {
  const match = String(action || '').match(/^\[(\w+)\]\s*(.*)$/)
  return (match?.[2] || action || '').trim().toLowerCase()
}

function normalizeTimelineText(value: unknown): string {
  if (value == null) return ''
  const raw = typeof value === 'string' ? value : JSON.stringify(value)
  return raw
    .replace(/\bundefined\b/gi, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
}

function timelineStepTs(step: TaskStep): number {
  return step.startTime ?? step.timestamp ?? 0
}

function extractRemoteTaskToken(text: string): string {
  const match = text.match(/\[(task_[^\]]+)\]/i)
  return (match?.[1] || '').trim().toLowerCase()
}

function remoteTimelineSemanticKey(step: TaskStep): string {
  const action = normalizeTimelineAction(step.action)
  if (!action.startsWith('remote_')) return ''

  const raw = normalizeTimelineText(step.output ?? step.observation)
  const text = raw.replace(/\[(task_[^\]]+)\]/gi, '').trim()
  const pctMatch = text.match(/(\d+)%/)
  const pct = pctMatch ? Number.parseInt(pctMatch[1], 10) : NaN
  const pctBucket = Number.isFinite(pct) ? (pct >= 100 ? 100 : Math.floor(pct / 10) * 10) : -1

  let labelKey = text
  if (text.includes('remote task completed')) labelKey = 'remote-task-completed'
  else if (text.includes('remote task failed')) labelKey = 'remote-task-failed'
  else if (text.includes('executing with') && text.includes('workers')) labelKey = 'executing-workers'
  else if (text.includes('polling remote node')) labelKey = 'polling'
  else if (text.includes('connecting to remote stream')) labelKey = 'stream-connecting'
  else if (text.includes('live remote stream connected')) labelKey = 'stream-connected'

  const actionGroup = action === 'remote_completed' || action === 'remote_failed' || action === 'remote_done'
    ? action
    : 'remote_progress'
  return `${actionGroup}|${labelKey}|${pctBucket}`
}

function shouldCompactTimelineSteps(prev: TaskStep, next: TaskStep): boolean {
  if ((prev.agentId || '') !== (next.agentId || '')) return false
  const prevAction = normalizeTimelineAction(prev.action)
  const nextAction = normalizeTimelineAction(next.action)
  const isHeartbeatPair = (
    (prevAction === 'tool_heartbeat' || prevAction === 'worker_heartbeat') &&
    prevAction === nextAction
  )
  if (prevAction !== nextAction) {
    const prevRemoteKey = remoteTimelineSemanticKey(prev)
    const nextRemoteKey = remoteTimelineSemanticKey(next)
    if (!prevRemoteKey || prevRemoteKey !== nextRemoteKey) return false
  }
  const prevNo = prev.stepNumber
  const nextNo = next.stepNumber
  if (
    typeof prevNo === 'number' &&
    typeof nextNo === 'number' &&
    prevNo >= 1 &&
    nextNo >= 1 &&
    prevNo !== nextNo
  ) {
    return false
  }
  const prevTs = timelineStepTs(prev)
  const nextTs = timelineStepTs(next)
  if (!isHeartbeatPair && prevTs > 0 && nextTs > 0 && Math.abs(prevTs - nextTs) > 5000) return false
  const prevRemoteKey = remoteTimelineSemanticKey(prev)
  const nextRemoteKey = remoteTimelineSemanticKey(next)
  if (prevRemoteKey && prevRemoteKey === nextRemoteKey) return true
  return normalizeTimelineText(prev.output ?? prev.observation) === normalizeTimelineText(next.output ?? next.observation)
}

function StepItem({ step, agents, isLatest, duplicateCount = 1 }: { step: TaskStep; agents: { id: string; name: string }[]; isLatest?: boolean; duplicateCount?: number }) {
  const status = stepStatusConfig[step.status] || stepStatusConfig.pending
  const StatusIcon = status?.icon || ClockIcon
  const agent = agents.find(a => a.id === step.agentId)

  // Parse agent type from bracketed action format: "[type] action"
  const actionMatch = step.action?.match(/^\[(\w+)\]\s*(.*)$/)
  const agentType = actionMatch?.[1] || ''
  const displayActionRaw = actionMatch?.[2] || step.action
  const actionAlias: Record<string, string> = {
    local_done: 'Local Complete',
    waiting_remote: 'Waiting on Remote',
    workers_selected: 'Workers Selected',
    remote_done: 'Remote Completed',
    remote_progress: 'Remote Status',
    remote_completed: 'Remote Completed',
    remote_failed: 'Remote Failed',
  }
  const displayAction = actionAlias[displayActionRaw.toLowerCase()] || displayActionRaw
  const hasObservation = step.observation != null && step.observation !== step.output

  const typeColorMap: Record<string, string> = {
    organizer: 'text-purple-400 bg-purple-500/10',
    router: 'text-indigo-400 bg-indigo-500/10',
    coordinator: 'text-blue-400 bg-blue-500/10',
    remote: 'text-cyan-400 bg-cyan-500/10',
    worker: 'text-green-400 bg-green-500/10',
  }
  const typeColor = typeColorMap[agentType] || 'text-gray-400 bg-gray-500/10'
  const stepTs = step.startTime || step.timestamp
  const stepTimeLabel = stepTs ? new Date(stepTs).toLocaleTimeString() : null

  return (
    <div className={clsx(
      'flex items-start gap-3 p-3 rounded-lg',
      isLatest ? 'bg-primary-500/10 border border-primary-500/30' : 'bg-dark-bg'
    )}>
      <div className="flex flex-col items-center gap-0.5">
        <div className={clsx('', status.color)}>
          <StatusIcon className="w-4 h-4" />
        </div>
        {step.stepNumber != null && (
          <span className="text-[9px] text-gray-600 font-mono">#{step.stepNumber}</span>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-1.5 min-w-0">
            {agentType && (
              <span className={clsx('text-[10px] px-1 py-0.5 rounded font-medium shrink-0', typeColor)}>
                {agentType}
              </span>
            )}
            <span className="font-medium text-gray-200 truncate text-sm">{displayAction}</span>
            {duplicateCount > 1 && (
              <span className="text-[10px] px-1 py-0.5 rounded bg-amber-500/15 text-amber-300 shrink-0">
                x{duplicateCount}
              </span>
            )}
          </div>
          <div className="shrink-0 ml-2 text-right">
            {stepTimeLabel && <div className="text-[10px] text-gray-600">{stepTimeLabel}</div>}
            <span className="text-xs text-gray-500">
              {formatDurationFromTimestamps(step.startTime ?? 0, step.endTime)}
            </span>
          </div>
        </div>
        <div className="text-xs text-gray-500 mb-1">
          {agent?.name || step.agentId}
        </div>
        
        {step.input ? (
          <div className="mb-1">
            <pre className="text-xs text-gray-500 italic bg-dark-surface p-1.5 rounded overflow-x-auto max-h-20 max-w-full whitespace-pre-wrap break-words">
              💭 {typeof step.input === 'string' ? step.input : JSON.stringify(step.input, null, 2)}
            </pre>
          </div>
        ) : null}
        
        {step.output ? (
          <div className="mb-1 rounded bg-dark-surface p-2 overflow-y-auto max-h-48 max-w-full">
            {typeof step.output === 'string' ? (
              <div className="prose prose-invert prose-sm max-w-none text-gray-300 [&_h1]:text-base [&_h2]:text-sm [&_h3]:text-sm [&_p]:text-sm [&_li]:text-sm [&_code]:text-xs [&_pre]:text-xs">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {step.output}
                </ReactMarkdown>
              </div>
            ) : (
              <pre className="text-xs whitespace-pre-wrap break-words m-0 text-gray-400">
                {JSON.stringify(step.output, null, 2)}
              </pre>
            )}
          </div>
        ) : null}

        {hasObservation && (
          <div className="mb-1 rounded bg-dark-surface p-2 overflow-y-auto max-h-32 max-w-full">
            <p className="text-[10px] text-gray-500 mb-1">Observation</p>
            {typeof step.observation === 'string' ? (
              <pre className="text-xs whitespace-pre-wrap break-words m-0 text-gray-400">
                {step.observation}
              </pre>
            ) : (
              <pre className="text-xs whitespace-pre-wrap break-words m-0 text-gray-400">
                {JSON.stringify(step.observation, null, 2)}
              </pre>
            )}
          </div>
        )}
        
        {step.error && (
          <div className="text-xs text-red-400 bg-red-500/10 p-2 rounded">
            {step.error}
          </div>
        )}
      </div>
    </div>
  )
}

function workerStatusMeta(worker: WorkerStatusSummary): string {
  const parts: string[] = []
  if (worker?.startedAt) {
    const endTs = worker.finishedAt || (worker.status === 'running' ? Date.now() : undefined)
    parts.push(formatDurationFromTimestamps(worker.startedAt, endTs))
  }
  if (worker?.status === 'running' && worker?.lastHeartbeatAt) {
    const deltaMs = Math.max(0, Date.now() - worker.lastHeartbeatAt)
    if (deltaMs >= 1000) {
      parts.push(`hb ${formatDurationFromTimestamps(worker.lastHeartbeatAt)}`)
    }
  }
  if (typeof worker?.stepCount === 'number' && worker.stepCount > 0) {
    parts.push(`${worker.stepCount} step${worker.stepCount === 1 ? '' : 's'}`)
  }
  return parts.join(' · ')
}

export default function TaskDetail({ task: taskProp, onClose }: TaskDetailProps) {
  // Subscribe to the live task from the store so it auto-updates on SSE events.
  const { agents, tasks: allTasks, deleteTask, archiveTask, updateTask } = useAgentStore()
  const task = allTasks.find(t => t.id === taskProp.id) || taskProp
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [showArchiveConfirm, setShowArchiveConfirm] = useState(false)
  const [showRetryConfirm, setShowRetryConfirm] = useState(false)
  const [isRetrying, setIsRetrying] = useState(false)
  const [showAdvancedTopology, setShowAdvancedTopology] = useState(false)
  const [stepsSortDesc, setStepsSortDesc] = useState(true) // default: newest first
  const [isReplaying, setIsReplaying] = useState(false)
  const [replayIndex, setReplayIndex] = useState(0)
  const [replaySpeedMs, setReplaySpeedMs] = useState(900)

  // Sort steps by startTime (fallback to array order)
  const sortedSteps = useMemo(() => {
    const steps = [...task.steps]
    steps.sort((a, b) => {
      const ta = a.startTime ?? 0
      const tb = b.startTime ?? 0
      return stepsSortDesc ? (tb - ta) : (ta - tb)
    })
    return steps
  }, [task.steps, stepsSortDesc])

  const replaySteps = useMemo(() => {
    const steps = [...task.steps]
    steps.sort((a, b) => {
      const ta = a.startTime || a.timestamp || 0
      const tb = b.startTime || b.timestamp || 0
      return ta - tb
    })
    return steps
  }, [task.steps])
  const hasReplayCursor = replaySteps.length > 0 && (isReplaying || replayIndex > 0)
  const replayStepId = hasReplayCursor
    ? replaySteps[Math.min(replayIndex, replaySteps.length - 1)]?.id
    : undefined
  const stepsForRender = hasReplayCursor ? replaySteps : sortedSteps
  const timelineEntries = useMemo<TimelineEntry[]>(() => {
    if (hasReplayCursor) {
      return stepsForRender.map(step => ({ step, duplicateCount: 1 }))
    }

    // Drop terminal-echo progress entries when a corresponding completed/failed
    // remote event arrives at almost the same moment.
    const filtered = stepsForRender.filter((step) => {
      const action = normalizeTimelineAction(step.action)
      if (action !== 'remote_progress') return true
      const text = normalizeTimelineText(step.output)
      const terminalLike = text.includes('remote task completed') || text.includes('state=completed')
      if (!terminalLike) return true

      const token = extractRemoteTaskToken(text)
      const ts = timelineStepTs(step)
      return !stepsForRender.some((other) => {
        if (other === step) return false
        const otherAction = normalizeTimelineAction(other.action)
        if (otherAction !== 'remote_done' && otherAction !== 'remote_completed' && otherAction !== 'remote_failed') return false
        if ((other.agentId || '') !== (step.agentId || '')) return false
        const otherTs = timelineStepTs(other)
        if (ts > 0 && otherTs > 0 && Math.abs(otherTs - ts) > 5000) return false
        if (!token) return true
        const otherToken = extractRemoteTaskToken(normalizeTimelineText(other.output))
        return !otherToken || otherToken === token
      })
    })

    const compacted: TimelineEntry[] = []
    for (const step of filtered) {
      const prev = compacted[compacted.length - 1]
      if (prev && shouldCompactTimelineSteps(prev.step, step)) {
        prev.duplicateCount += 1
        continue
      }
      compacted.push({ step, duplicateCount: 1 })
    }
    return compacted
  }, [hasReplayCursor, stepsForRender])

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !showDeleteConfirm && !showArchiveConfirm && !showRetryConfirm) onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose, showDeleteConfirm, showArchiveConfirm, showRetryConfirm])

  useEffect(() => {
    // Reset playback state when switching to a different task.
    setIsReplaying(false)
    setReplayIndex(0)
    setShowAdvancedTopology(false)
  }, [task.id])

  useEffect(() => {
    if (replayIndex >= replaySteps.length && replaySteps.length > 0) {
      setReplayIndex(replaySteps.length - 1)
      setIsReplaying(false)
    }
    if (replaySteps.length === 0) {
      setReplayIndex(0)
      setIsReplaying(false)
    }
  }, [replayIndex, replaySteps.length])

  useEffect(() => {
    if (!isReplaying || replaySteps.length === 0) return
    const timer = window.setInterval(() => {
      setReplayIndex((prev) => {
        if (prev >= replaySteps.length - 1) {
          setIsReplaying(false)
          return prev
        }
        return prev + 1
      })
    }, replaySpeedMs)
    return () => window.clearInterval(timer)
  }, [isReplaying, replaySteps.length, replaySpeedMs])

  const handleDelete = async () => {
    queueTaskDeleteWithUndo(task, { deleteTask })
    setShowDeleteConfirm(false)
    onClose()
  }

  const handleArchive = () => {
    applyArchiveWithUndo(task, { archiveTask, updateTask })
    setShowArchiveConfirm(false)
    onClose()
  }

  const handleRetry = async () => {
    setIsRetrying(true)
    try {
      const apiBase = getApiBase()
      await fetch(`${apiBase}/api/agent/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task: task.description || task.name }),
      })
      setShowRetryConfirm(false)
      onClose()
    } catch (error) {
      reportUiError({
        source: 'TaskDetail',
        title: 'Retry Failed',
        userMessage: `Failed to retry "${task.name}".`,
        error,
      })
    } finally {
      setIsRetrying(false)
    }
  }

  const handleReplayToggle = () => {
    if (replaySteps.length === 0) return
    if (!isReplaying && replayIndex >= replaySteps.length - 1) {
      setReplayIndex(0)
    }
    setIsReplaying(prev => !prev)
  }

  const handleReplayReset = () => {
    setIsReplaying(false)
    setReplayIndex(0)
  }

  return createPortal(
    // Modal overlay
    <div 
      className="fixed inset-0 z-[99999] flex items-center justify-center bg-black/60 backdrop-blur-md"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      {/* Floating popup - wider layout for phase rail + timeline + topology */}
      <div className="w-full h-full sm:w-[min(960px,96vw)] sm:max-h-[88vh] sm:h-auto flex flex-col bg-dark-surface border border-dark-border rounded-none sm:rounded-xl shadow-2xl overflow-hidden animate-fade-in">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-dark-border shrink-0">
          <div className="min-w-0 flex-1 mr-2">
            <h2 className="font-semibold text-white truncate">{task.name}</h2>
            <p className="text-sm text-gray-500 truncate">{task.description}</p>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-dark-hover rounded-lg transition-colors shrink-0"
          >
            <XMarkIcon className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        {/* Controls */}
        <div className="flex items-center justify-between p-4 border-b border-dark-border shrink-0">
          <div className="flex items-center gap-2">
            {task.status === 'pending' && (
              <button className="flex items-center gap-2 px-3 py-2 bg-green-500/20 text-green-400 rounded-lg hover:bg-green-500/30 transition-colors">
                <PlayIcon className="w-4 h-4" />
                <span className="text-sm">Start</span>
              </button>
            )}
            {task.status === 'running' && (
              <button className="flex items-center gap-2 px-3 py-2 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30 transition-colors">
                <StopIcon className="w-4 h-4" />
                <span className="text-sm">Cancel</span>
              </button>
            )}
            {(task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') && (
              <button 
                onClick={() => setShowRetryConfirm(true)}
                className="flex items-center gap-2 px-3 py-2 bg-dark-hover text-gray-400 rounded-lg hover:bg-dark-border transition-colors"
              >
                <ArrowPathIcon className="w-4 h-4" />
                <span className="text-sm">Retry</span>
              </button>
            )}
          </div>
          
          {/* Delete & Archive buttons */}
          <div className="flex items-center gap-2">
            {task.status !== 'cancelled' && (
              <button 
                onClick={() => setShowArchiveConfirm(true)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 text-yellow-400 hover:bg-yellow-500/20 rounded-lg transition-colors"
                title="Archive task"
              >
                <ArchiveBoxIcon className="w-4 h-4" />
                <span className="text-xs">Archive</span>
              </button>
            )}
            <button 
              onClick={() => setShowDeleteConfirm(true)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 text-red-400 hover:bg-red-500/20 rounded-lg transition-colors"
              title="Delete task"
            >
              <TrashIcon className="w-4 h-4" />
              <span className="text-xs">Delete</span>
            </button>
          </div>
        </div>

        {/* Scrollable content area */}
        <div className="flex-1 overflow-y-auto thin-scrollbar">
          {/* Error banner — prominent when task failed */}
          {task.status === 'failed' && task.error && (
            <div className="m-4 p-4 rounded-lg bg-red-500/15 border border-red-500/40">
              <div className="flex items-start gap-2">
                <XCircleIcon className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-medium text-red-400 mb-1">Task Failed</p>
                  <p className="text-sm text-red-300 break-words">{task.error}</p>
                </div>
              </div>
            </div>
          )}
          {/* Phase Rail (primary task-flow view) */}
          <div className="p-4 border-b border-dark-border">
            <TaskPhaseRail task={task} />
          </div>

          {/* Info */}
          <div className="p-4 border-b border-dark-border">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-gray-500">Status</p>
                <p className={clsx(
                  'font-medium capitalize',
                  task.status === 'completed' && 'text-green-400',
                  task.status === 'failed' && 'text-red-400',
                  task.status === 'running' && 'text-blue-400',
                  task.status === 'pending' && 'text-gray-400',
                  task.status === 'cancelled' && 'text-yellow-400',
                )}>
                  {task.status === 'cancelled' ? 'Archived' : task.status}
                </p>
              </div>
              <div>
                <p className="text-gray-500">Origin</p>
                <p className={clsx(
                  'font-medium',
                  task.origin === 'remote' ? 'text-cyan-400' : 'text-gray-200'
                )}>
                  {task.origin === 'remote' ? '📡 Remote' : '💻 Local'}
                  {task.requesterId && <span className="text-xs text-gray-500 ml-1">from {task.requesterId}</span>}
                </p>
              </div>
              <div>
                <p className="text-gray-500">Created</p>
                <p className="text-gray-200 text-xs font-mono">{formatDateTime(task.createdAt)}</p>
              </div>
              <div>
                <p className="text-gray-500">Started</p>
                <p className="text-gray-200 text-xs font-mono">{formatDateTime(task.startedAt)}</p>
              </div>
              {task.completedAt && (
                <div>
                  <p className="text-gray-500">Completed</p>
                  <p className="text-gray-200 text-xs font-mono">{formatDateTime(task.completedAt)}</p>
                </div>
              )}
            </div>
          </div>

          {task.progress?.workerStatuses && task.progress.workerStatuses.length > 0 && (
            <div className="p-4 border-b border-dark-border">
              <div className="flex items-center justify-between gap-3 mb-3">
                <div>
                  <h3 className="text-sm font-medium text-gray-300">Worker Status</h3>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Live local worker state for this task.
                  </p>
                </div>
                {task.executionMode && (
                  <span className="text-[10px] px-2 py-1 rounded border border-dark-border bg-dark-bg text-gray-400 uppercase tracking-wide">
                    {task.executionMode}
                  </span>
                )}
              </div>
              <div className="space-y-2">
                {task.progress.workerStatuses.map((worker) => {
                  const meta = workerStatusMeta(worker)
                  const badgeClass =
                    worker.status === 'completed' ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/20' :
                    worker.status === 'running' ? 'bg-blue-500/15 text-blue-300 border-blue-500/20' :
                    worker.status === 'failed' || worker.status === 'timeout' ? 'bg-red-500/15 text-red-300 border-red-500/20' :
                    worker.status === 'skipped' ? 'bg-gray-500/15 text-gray-300 border-gray-500/20' :
                    'bg-amber-500/15 text-amber-300 border-amber-500/20'
                  return (
                    <div key={worker.name} className="rounded-lg border border-dark-border bg-dark-bg px-3 py-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className={clsx('text-[10px] px-1.5 py-0.5 rounded border uppercase tracking-wide shrink-0', badgeClass)}>
                          {worker.status}
                        </span>
                        <span className="text-sm text-gray-200 truncate">{worker.name}</span>
                        {typeof worker.stepCount === 'number' && worker.stepCount > 0 && (
                          <span className="text-[10px] text-gray-500 ml-auto shrink-0">
                            {meta || `${worker.stepCount} step${worker.stepCount === 1 ? '' : 's'}`}
                          </span>
                        )}
                      </div>
                      {(worker.detail || worker.action || worker.error) && (
                        <div className="mt-1 text-xs text-gray-500 break-words">
                          {worker.error || worker.detail || worker.action}
                        </div>
                      )}
                      {!worker.error && meta && !(typeof worker.stepCount === 'number' && worker.stepCount > 0) && (
                        <div className="mt-1 text-[10px] text-gray-600">
                          {meta}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Task Flow Topology — advanced, optional */}
          <div className="p-4 border-b border-dark-border">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-medium text-gray-300">Topology (Advanced)</h3>
                <p className="text-xs text-gray-500 mt-0.5">
                  Deep node graph for routing and return paths.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowAdvancedTopology(v => !v)}
                className="px-2.5 py-1.5 rounded-lg border border-dark-border bg-dark-bg text-xs text-gray-300 hover:text-white hover:border-primary-500/40 transition-colors"
              >
                {showAdvancedTopology ? 'Hide Graph' : 'Show Graph'}
              </button>
            </div>
            {showAdvancedTopology ? (
              <div className="mt-3">
                <Suspense
                  fallback={
                    <div className="rounded-lg border border-dark-border bg-dark-bg/50 px-3 py-6 text-center text-xs text-gray-500">
                      Loading topology graph...
                    </div>
                  }
                >
                  <LazyTaskTopology task={task} />
                </Suspense>
              </div>
            ) : (
              <div className="mt-3 rounded-lg border border-dashed border-dark-border bg-dark-bg/40 px-3 py-2 text-xs text-gray-500">
                Focus mode keeps this hidden by default. Open it when you need node-level routing diagnostics.
              </div>
            )}
          </div>

          {/* Cost & Output Info */}
          {(task.cost || task.outputDir) && (
            <div className="p-4 border-b border-dark-border">
              <div className="grid grid-cols-1 gap-3">
                {task.cost && (
                  <div className="flex items-start gap-2">
                    <CurrencyDollarIcon className="w-4 h-4 text-yellow-400 mt-0.5" />
                    <div className="text-sm">
                      <p className="text-gray-400">Token Usage</p>
                      <p className="text-gray-200">
                        {formatTokenCount(task.cost.totalTokens ?? 0)} tokens
                        {(task.cost.inputTokens != null || task.cost.outputTokens != null) && (
                          <span className="text-gray-500 text-xs ml-1">
                            ({formatTokenCount(task.cost.inputTokens ?? 0)} in / {formatTokenCount(task.cost.outputTokens ?? 0)} out)
                          </span>
                        )}
                      </p>
                      {task.cost.duration != null && task.cost.duration > 0 && (
                        <p className="text-gray-500 text-xs">
                          Duration: {formatDurationSecs(task.cost.duration)}
                        </p>
                      )}
                    </div>
                  </div>
                )}
                {task.outputDir && (
                  <div className="flex items-start gap-2">
                    <FolderIcon className="w-4 h-4 text-blue-400 mt-0.5" />
                    <div className="text-sm min-w-0 flex-1">
                      <p className="text-gray-400">Output Directory</p>
                      <p className="text-gray-200 font-mono text-xs break-all">{task.outputDir}</p>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Timeline */}
          <div className="p-4">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
              <h3 className="text-sm font-medium text-gray-300">
                Timeline ({timelineEntries.length}{timelineEntries.length !== task.steps.length ? ` / ${task.steps.length}` : ''})
              </h3>
              <div className="flex items-center gap-1.5">
                {task.steps.length > 1 && (
                  <button
                    onClick={() => setStepsSortDesc(d => !d)}
                    disabled={hasReplayCursor || isReplaying}
                    className="flex items-center gap-1 text-[10px] text-gray-500 hover:text-gray-300 transition-colors px-1.5 py-0.5 rounded hover:bg-dark-hover disabled:opacity-40 disabled:cursor-not-allowed"
                    title={stepsSortDesc ? 'Newest first' : 'Oldest first'}
                  >
                    {stepsSortDesc ? (
                      <><ChevronDownIcon className="w-3 h-3" /> Newest</>
                    ) : (
                      <><ChevronUpIcon className="w-3 h-3" /> Oldest</>
                    )}
                  </button>
                )}
                {replaySteps.length > 0 && (
                  <>
                    <button
                      type="button"
                      onClick={handleReplayToggle}
                      className="flex items-center gap-1 text-[10px] px-2 py-1 rounded border border-dark-border bg-dark-bg text-gray-300 hover:text-white hover:border-primary-500/40 transition-colors"
                    >
                      <ArrowPathIcon className={clsx('w-3 h-3', isReplaying && 'animate-spin')} />
                      {isReplaying ? 'Pause Replay' : (hasReplayCursor ? 'Resume Replay' : 'Start Replay')}
                    </button>
                    <button
                      type="button"
                      onClick={handleReplayReset}
                      disabled={!hasReplayCursor && !isReplaying}
                      className="text-[10px] px-2 py-1 rounded border border-dark-border bg-dark-bg text-gray-400 hover:text-gray-200 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      Reset
                    </button>
                  </>
                )}
              </div>
            </div>
            {replaySteps.length > 1 && (
              <div className="mb-3 rounded-lg border border-dark-border bg-dark-bg/40 px-3 py-2">
                <div className="flex flex-wrap items-center gap-3 text-[11px] text-gray-500 mb-2">
                  <span>Playback</span>
                  <span className="text-gray-400">
                    {Math.min(replayIndex + 1, replaySteps.length)}/{replaySteps.length}
                  </span>
                  <label className="flex items-center gap-1">
                    <span className="text-gray-500">speed</span>
                    <select
                      value={replaySpeedMs}
                      onChange={(e) => setReplaySpeedMs(Number(e.target.value))}
                      className="bg-dark-surface border border-dark-border rounded px-1.5 py-0.5 text-[11px] text-gray-300"
                    >
                      <option value={1300}>0.75x</option>
                      <option value={900}>1x</option>
                      <option value={650}>1.5x</option>
                      <option value={420}>2x</option>
                    </select>
                  </label>
                </div>
                <input
                  type="range"
                  min={0}
                  max={Math.max(0, replaySteps.length - 1)}
                  value={Math.min(replayIndex, Math.max(0, replaySteps.length - 1))}
                  onChange={(e) => {
                    setReplayIndex(Number(e.target.value))
                    setIsReplaying(false)
                  }}
                  className="w-full accent-primary-500"
                />
              </div>
            )}
            <div className="space-y-2">
              {timelineEntries.length === 0 ? (
                <p className="text-gray-500 text-sm">No steps recorded yet</p>
              ) : (
                timelineEntries.map(({ step, duplicateCount }, idx) => (
                  <StepItem
                    key={step.id || `step-${idx}`}
                    step={step}
                    agents={agents}
                    duplicateCount={duplicateCount}
                    isLatest={
                      replayStepId
                        ? step.id === replayStepId
                        : task.status === 'running' && idx === (stepsSortDesc ? 0 : timelineEntries.length - 1)
                    }
                  />
                ))
              )}
            </div>
          </div>
        </div>

        {/* Result / Error */}
        {(task.result || task.error) && (
          <div className="p-4 border-t border-dark-border shrink-0 overflow-hidden">
            {task.error ? (
              <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 overflow-hidden">
                <p className="text-sm font-medium text-red-400 mb-1">Error</p>
                <p className="text-sm text-red-300 break-words">{task.error}</p>
              </div>
            ) : task.result ? (
              <div className="p-3 rounded-lg bg-green-500/10 border border-green-500/20 overflow-hidden">
                <p className="text-sm font-medium text-green-400 mb-1">Result</p>
                <div className="text-sm text-green-300 overflow-x-auto max-h-48 overflow-y-auto prose prose-invert prose-sm max-w-none prose-p:text-green-300 prose-li:text-green-300 prose-headings:text-green-200 prose-code:text-green-200 prose-strong:text-green-200">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {typeof task.result === 'string' ? task.result : JSON.stringify(task.result, null, 2)}
                  </ReactMarkdown>
                </div>
              </div>
            ) : null}
          </div>
        )}
      </div>
      
      {/* Delete Confirmation Dialog */}
      <ConfirmDialog
        isOpen={showDeleteConfirm}
        title="Delete Task"
        message={`Are you sure you want to delete "${task.name}"? This action cannot be undone.`}
        confirmLabel="Delete"
        confirmColor="red"
        onConfirm={handleDelete}
        onCancel={() => setShowDeleteConfirm(false)}
      />
      
      {/* Archive Confirmation Dialog */}
      <ConfirmDialog
        isOpen={showArchiveConfirm}
        title="Archive Task"
        message={`Are you sure you want to archive "${task.name}"? It will be marked as cancelled.`}
        confirmLabel="Archive"
        confirmColor="yellow"
        onConfirm={handleArchive}
        onCancel={() => setShowArchiveConfirm(false)}
      />
      
      {/* Retry Confirmation Dialog */}
      <ConfirmDialog
        isOpen={showRetryConfirm}
        title="Retry Task"
        message={`Re-run this task with the original prompt? A new task will be created.`}
        confirmLabel={isRetrying ? 'Retrying...' : 'Retry'}
        confirmColor="blue"
        onConfirm={handleRetry}
        onCancel={() => setShowRetryConfirm(false)}
      />
    </div>,
    document.body
  )
}
