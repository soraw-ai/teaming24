/**
 * Sandbox Monitor - Real-time monitoring for AN Sandbox operations.
 * 
 * Features:
 * - Sandbox list grouped by: Active Tasks, Local Agents, Completed
 * - Real-time metrics (CPU, memory, disk)
 * - Command execution logs with full datetime
 * - Event stream
 * - Completed sandbox history
 */

import { useState, useEffect, useRef, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { getApiBase } from '../utils/api'
import {
  CpuChipIcon,
  ServerIcon,
  CircleStackIcon,
  PlayIcon,
  StopIcon,
  ArrowPathIcon,
  CommandLineIcon,
  ClockIcon,
  CheckCircleIcon,
  ExclamationCircleIcon,
  FolderIcon,
  UserIcon,
  ArchiveBoxIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ChevronUpIcon,
  TrashIcon,
  ArrowsPointingOutIcon,
  ArrowsPointingInIcon,
  BeakerIcon,
  GlobeAltIcon,
  CodeBracketIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { isDemoId, demoId } from '../utils/ids'
import { debugLog, debugWarn } from '../utils/debug'
import { formatNumberNoTrailingZeros } from '../utils/format'


// ============================================================================
// Types
// ============================================================================

interface SandboxInfo {
  id: string
  name: string
  state: 'running' | 'paused' | 'stopped' | 'error' | 'completed' | 'disconnected' | 'stale' | 'pending'
  runtime: string
  // Grouping info
  taskId?: string
  taskName?: string
  agentId?: string
  agentName?: string
  // Metadata
  role?: string
  created?: number
  completed?: number
  duration?: number
  lastHeartbeat?: number
  // VNC/CDP URLs for live view
  vncUrl?: string
  cdpUrl?: string
  apiUrl?: string
  // Error tracking
  fetchError?: string
  // Pending state (optimistic UI)
  isPending?: boolean
}

interface SandboxMetrics {
  timestamp: number
  cpu_pct: number
  mem_pct: number
  mem_used_mb: number
  disk_pct: number
}

interface SandboxEvent {
  type: 'command' | 'output' | 'error' | 'metric' | 'info'
  timestamp: number
  data: Record<string, unknown>
}

// ============================================================================
// Date/Time Formatting
// ============================================================================

import { formatDateTimeFromUnix } from '../utils/date'
import { formatDurationMs } from '../utils/format'

// ============================================================================
// Metric Card
// ============================================================================

function MetricCard({
  icon: Icon,
  label,
  value,
  unit,
  color,
}: {
  icon: React.ElementType
  label: string
  value: number
  unit: string
  color: 'blue' | 'green' | 'yellow' | 'red'
}) {
  const colorClasses = {
    blue: 'text-blue-400 bg-blue-500/10',
    green: 'text-green-400 bg-green-500/10',
    yellow: 'text-yellow-400 bg-yellow-500/10',
    red: 'text-red-400 bg-red-500/10',
  }

  return (
    <div className="bg-dark-surface border border-dark-border rounded-lg p-4">
      <div className="flex items-center gap-3">
        <div className={clsx('p-2 rounded-lg', colorClasses[color])}>
          <Icon className="w-5 h-5" />
        </div>
        <div>
          <p className="text-sm text-gray-400">{label}</p>
          <p className="text-xl font-semibold text-white">
            {formatNumberNoTrailingZeros(value, 1)}<span className="text-sm text-gray-400 ml-1">{unit}</span>
          </p>
        </div>
      </div>
      {/* Progress bar */}
      <div className="mt-3 h-1.5 bg-dark-border rounded-full overflow-hidden">
        <div
          className={clsx('h-full rounded-full transition-all duration-300', {
            'bg-blue-500': color === 'blue',
            'bg-green-500': color === 'green',
            'bg-yellow-500': color === 'yellow',
            'bg-red-500': color === 'red',
          })}
          style={{ width: `${Math.min(value, 100)}%` }}
        />
      </div>
    </div>
  )
}

// ============================================================================
// Event Log (with full datetime)
// ============================================================================

function EventLog({ events, autoScroll = true, heightClass = 'h-80' }: { events: SandboxEvent[], autoScroll?: boolean, heightClass?: string }) {
  const logRef = useRef<HTMLDivElement>(null)
  const [userScrolled, setUserScrolled] = useState(false)
  const [sortNewestFirst, setSortNewestFirst] = useState(true)
  const [filter, setFilter] = useState<'all' | 'info' | 'command' | 'output' | 'error' | 'workers'>('all')

  const filteredEvents = useMemo(() => {
    const workerTools = new Set(['worker_started', 'worker_completed', 'worker_skipped', 'worker_roster', 'worker_heartbeat'])
    const next = events.filter((event) => {
      if (filter === 'all') return true
      if (filter === 'workers') {
        return workerTools.has(String(event.data?.tool || ''))
      }
      return event.type === filter
    })
    next.sort((a, b) => sortNewestFirst ? b.timestamp - a.timestamp : a.timestamp - b.timestamp)
    return next
  }, [events, filter, sortNewestFirst])

  // Auto-scroll only when: autoScroll is true AND user hasn't manually scrolled
  useEffect(() => {
    if (logRef.current && autoScroll && !userScrolled) {
      logRef.current.scrollTop = sortNewestFirst ? 0 : logRef.current.scrollHeight
    }
  }, [filteredEvents, autoScroll, sortNewestFirst, userScrolled])

  // Reset userScrolled when switching sandboxes
  useEffect(() => {
    setUserScrolled(false)
  }, [events.length])

  // Detect manual scroll
  const handleScroll = () => {
    if (!logRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = logRef.current
    const isAtAnchor = sortNewestFirst
      ? scrollTop < 20
      : scrollHeight - scrollTop - clientHeight < 20
    if (!isAtAnchor) {
      setUserScrolled(true)
    }
  }

  const getEventIcon = (type: string, tool?: string) => {
    // Use tool field to distinguish worker lifecycle events
    if (tool === 'worker_started') return <PlayIcon className="w-4 h-4 text-yellow-400" />
    if (tool === 'worker_completed') return <CheckCircleIcon className="w-4 h-4 text-green-400" />
    if (tool === 'worker_skipped') return <ChevronRightIcon className="w-4 h-4 text-gray-500" />
    if (tool === 'worker_roster') return <UserIcon className="w-4 h-4 text-blue-300" />
    switch (type) {
      case 'command':
        return <CommandLineIcon className="w-4 h-4 text-blue-400" />
      case 'output':
        return <CheckCircleIcon className="w-4 h-4 text-green-400" />
      case 'error':
        return <ExclamationCircleIcon className="w-4 h-4 text-red-400" />
      case 'info':
        return <ClockIcon className="w-4 h-4 text-primary-400" />
      default:
        return <ClockIcon className="w-4 h-4 text-gray-400" />
    }
  }

  const getInfoTextClass = (tool: string) => {
    if (tool === 'worker_started') return 'text-yellow-400'
    if (tool === 'worker_completed') return 'text-green-400'
    if (tool === 'worker_skipped') return 'text-gray-500'
    if (tool === 'worker_roster') return 'text-blue-300'
    return 'text-primary-400'
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 flex-wrap">
          {(['all', 'workers', 'command', 'output', 'error', 'info'] as const).map((option) => (
            <button
              key={option}
              onClick={() => { setFilter(option); setUserScrolled(false) }}
              className={clsx(
                'px-2 py-1 rounded text-[10px] uppercase tracking-wide transition-colors',
                filter === option
                  ? 'bg-primary-500/20 text-primary-300'
                  : 'bg-dark-bg text-gray-500 hover:text-gray-300 hover:bg-dark-border'
              )}
            >
              {option}
            </button>
          ))}
        </div>
        <button
          onClick={() => { setSortNewestFirst((prev) => !prev); setUserScrolled(false) }}
          className="px-2 py-1 rounded text-[10px] uppercase tracking-wide bg-dark-bg text-gray-400 hover:text-gray-200 hover:bg-dark-border transition-colors"
        >
          {sortNewestFirst ? 'Newest First' : 'Oldest First'}
        </button>
      </div>
      <div
        ref={logRef}
        onScroll={handleScroll}
        className={`bg-dark-bg border border-dark-border rounded-lg p-3 ${heightClass} overflow-y-auto font-mono text-sm`}
      >
      {filteredEvents.length === 0 ? (
        <p className="text-gray-500 text-center py-4">No events yet</p>
      ) : (
        filteredEvents.map((event, i) => (
          <div key={i} className="flex items-start gap-2 py-1 border-b border-dark-border last:border-0">
            {getEventIcon(event.type, event.data?.tool as string | undefined)}
            <span className="text-gray-500 text-xs whitespace-nowrap">
              {formatDateTimeFromUnix(event.timestamp)}
            </span>
            <span className="text-gray-300 flex-1">
              {event.type === 'command' && event.data && (
                <span>
                  <span className="text-blue-400">$</span> {String(event.data.cmd || event.data.command || '')}
                  {event.data.agent ? (
                    <span className="text-gray-500 ml-2 text-xs">({String(event.data.agent)})</span>
                  ) : null}
                  {event.data.status === 'completed' && (
                    <span className="text-green-400 ml-2">✓</span>
                  )}
                </span>
              )}
              {event.type === 'output' && event.data && (
                <span className={event.data.stream === 'stderr' ? 'text-red-400' : 'text-green-400'}>
                  {String(event.data.text || event.data.output || '')}
                </span>
              )}
              {event.type === 'error' && (
                <span className="text-red-400">
                  {String(event.data?.message || event.data?.error || 'Unknown error')}
                </span>
              )}
              {event.type === 'info' && event.data && (() => {
                const toolName = String(event.data.tool || '')
                const workerLifecycleTools = ['worker_started', 'worker_completed', 'worker_skipped', 'worker_roster']
                return (
                  <span className={getInfoTextClass(toolName)}>
                    {String(event.data.message || event.data.cmd || event.data.tool || '')}
                    {/* Show agent tag only for non-worker-lifecycle events (they already include agent in message) */}
                    {event.data.agent && !workerLifecycleTools.includes(toolName) ? (
                      <span className="text-gray-500 ml-2 text-xs">({String(event.data.agent)})</span>
                    ) : null}
                  </span>
                )
              })()}
              {/* Fallback for unknown event types */}
              {!['command', 'output', 'error', 'info', 'heartbeat'].includes(event.type) && (
                <span className="text-gray-400">
                  {event.data ? JSON.stringify(event.data).slice(0, 100) : event.type}
                </span>
              )}
            </span>
          </div>
        ))
      )}
      </div>
    </div>
  )
}

// ============================================================================
// Sandbox Card
// ============================================================================

function SandboxCard({
  sandbox,
  selected,
  onSelect,
  onDelete,
  onStop,
  showTaskInfo = false,
}: {
  sandbox: SandboxInfo
  selected: boolean
  onSelect: () => void
  onDelete?: (id: string) => void
  onStop?: (id: string) => void
  showTaskInfo?: boolean
}) {
  const stateColors: Record<string, string> = {
    running: 'bg-green-500',
    paused: 'bg-yellow-500',
    stopped: 'bg-gray-500',
    error: 'bg-red-500',
    completed: 'bg-blue-500',
    disconnected: 'bg-orange-500',
    stale: 'bg-red-500',
    pending: 'bg-primary-500',
  }

  const roleColors: Record<string, string> = {
    research: 'text-blue-400',
    development: 'text-green-400',
    analysis: 'text-purple-400',
    demo: 'text-yellow-400',
    'browser-automation': 'text-yellow-400',
  }

  const isDocker = sandbox.runtime === 'docker' || sandbox.runtime === 'sandbox'
  const isOpenHands = sandbox.runtime === 'openhands'
  const isError = sandbox.state === 'error' || sandbox.state === 'stale' || sandbox.fetchError
  const isPending = sandbox.state === 'pending' || sandbox.isPending
  const isRunning = sandbox.state === 'running'
  const isDemo = sandbox.role === 'demo' || isDemoId(sandbox.id) || sandbox.name?.toLowerCase().includes('[demo]')

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (onDelete) {
      onDelete(sandbox.id)
    }
  }
  
  const handleStop = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (onStop) {
      onStop(sandbox.id)
    }
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect?.() }}
      className={clsx(
        'w-full text-left p-3 rounded-lg border transition-colors cursor-pointer',
        isPending
          ? 'bg-primary-500/5 border-primary-500/50 animate-pulse'
          : selected
          ? 'bg-primary-500/10 border-primary-500'
          : isError
          ? 'bg-red-500/5 border-red-500/30 hover:border-red-500/50'
          : 'bg-dark-surface border-dark-border hover:border-gray-600'
      )}
    >
      <div className="flex items-center gap-3">
        {isPending ? (
          <ArrowPathIcon className="w-5 h-5 text-primary-400 animate-spin" />
        ) : (
          <ServerIcon className={clsx('w-5 h-5', isError ? 'text-red-400' : isDocker ? 'text-blue-400' : 'text-gray-400')} />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p className={clsx('font-medium truncate', isPending ? 'text-primary-300' : isError ? 'text-red-300' : 'text-white')}>
              {sandbox.name || sandbox.id}
            </p>
            {isPending && (
              <span className="px-1.5 py-0.5 text-[10px] font-medium bg-primary-500/20 text-primary-400 rounded">
                Starting...
              </span>
            )}
            {isDemo && (
              <span className="px-1.5 py-0.5 text-[10px] font-medium bg-yellow-500/20 text-yellow-400 rounded">
                Demo
              </span>
            )}
            {!isPending && isDocker && !isError && !isDemo && (
              <span className="px-1.5 py-0.5 text-[10px] font-medium bg-blue-500/20 text-blue-400 rounded">
                Docker
              </span>
            )}
            {!isPending && isOpenHands && !isError && !isDemo && (
              <span className="px-1.5 py-0.5 text-[10px] font-medium bg-purple-500/20 text-purple-400 rounded">
                OpenHands
              </span>
            )}
            {isError && (
              <span className="px-1.5 py-0.5 text-[10px] font-medium bg-red-500/20 text-red-400 rounded">
                Stale
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 text-xs flex-wrap">
            {isPending && (
              <span className="text-primary-400">Initializing container...</span>
            )}
            {!isPending && isError && sandbox.fetchError && (
              <span className="text-red-400 truncate">{sandbox.fetchError}</span>
            )}
            {!isPending && !isError && sandbox.role && (
              <span className={clsx('capitalize', roleColors[sandbox.role] || 'text-gray-400')}>
                {sandbox.role}
              </span>
            )}
            {!isPending && !isError && showTaskInfo && sandbox.taskName && (
              <span className="text-purple-400">• {sandbox.taskName}</span>
            )}
            {!isPending && !isError && sandbox.agentName && (
              <span className="text-blue-400">• {sandbox.agentName}</span>
            )}
          </div>
          {sandbox.state === 'completed' && sandbox.duration && (
            <p className="text-xs text-gray-500 mt-1">
              Duration: {formatDurationMs(sandbox.duration)}
            </p>
          )}
        </div>
        {/* Action buttons for different states */}
        <div className="flex items-center gap-1">
          {/* Stop button for running sandboxes */}
          {isRunning && onStop && (
            <button
              onClick={handleStop}
              className="p-1 hover:bg-red-500/20 rounded transition-colors"
              title="Stop sandbox"
            >
              <StopIcon className="w-4 h-4 text-red-400" />
            </button>
          )}
          {/* Delete button for stale/error sandboxes */}
          {isError && onDelete && (
            <button
              onClick={handleDelete}
              className="p-1 hover:bg-red-500/20 rounded transition-colors"
              title="Remove stale record"
            >
              <ExclamationCircleIcon className="w-4 h-4 text-red-400" />
            </button>
          )}
          {/* Delete button for stopped/completed sandboxes */}
          {!isRunning && !isError && !isPending && onDelete && (
            <button
              onClick={handleDelete}
              className="p-1 hover:bg-gray-500/20 rounded transition-colors opacity-60 hover:opacity-100"
              title="Delete sandbox"
            >
              <TrashIcon className="w-3.5 h-3.5 text-gray-400" />
            </button>
          )}
          {/* State indicator */}
          <div className={clsx('w-2 h-2 rounded-full', stateColors[sandbox.state])} />
        </div>
      </div>
    </div>
  )
}

// ============================================================================
// Collapsible Group
// ============================================================================

function SandboxGroup({
  title,
  icon: Icon,
  count,
  children,
  defaultExpanded = true,
  color = 'text-gray-400',
}: {
  title: string
  icon: React.ElementType
  count: number
  children: React.ReactNode
  defaultExpanded?: boolean
  color?: string
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)

  return (
    <div className="mb-3">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-2 py-1 text-sm hover:bg-dark-hover rounded transition-colors"
      >
        {expanded ? (
          <ChevronDownIcon className="w-4 h-4 text-gray-500" />
        ) : (
          <ChevronRightIcon className="w-4 h-4 text-gray-500" />
        )}
        <Icon className={clsx('w-4 h-4', color)} />
        <span className={clsx('font-medium', color)}>{title}</span>
        <span className="ml-auto px-1.5 py-0.5 rounded-full bg-dark-hover text-xs text-gray-400">
          {count}
        </span>
      </button>
      {expanded && <div className="mt-2 space-y-2">{children}</div>}
    </div>
  )
}

// ============================================================================
// Completed Sandbox Summary
// ============================================================================

function CompletedSummary({ 
  sandbox,
  onDelete,
}: { 
  sandbox: SandboxInfo
  onDelete?: (id: string) => void
}) {
  const isStale = sandbox.state === 'stale' || sandbox.state === 'error' || !!sandbox.fetchError
  
  return (
    <div className={clsx(
      "p-4 border rounded-lg",
      isStale 
        ? "bg-red-500/5 border-red-500/30" 
        : "bg-dark-surface border-dark-border"
    )}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-gray-400">
          {isStale ? 'Stale Sandbox Record' : 'Completed Sandbox Summary'}
        </h3>
        {onDelete && (
          <button
            onClick={() => onDelete(sandbox.id)}
            className={clsx(
              "flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors",
              isStale
                ? "bg-red-500/20 hover:bg-red-500/30 text-red-400"
                : "bg-dark-hover hover:bg-dark-border text-gray-400"
            )}
          >
            <TrashIcon className="w-3 h-3" />
            Remove
          </button>
        )}
      </div>
      
      {isStale && sandbox.fetchError && (
        <div className="mb-4 p-2 bg-red-500/10 rounded text-xs text-red-400">
          {sandbox.fetchError}
        </div>
      )}
      
      <div className="grid grid-cols-2 gap-4 text-sm">
        <div>
          <p className="text-gray-500">Name</p>
          <p className="text-white">{sandbox.name || sandbox.id}</p>
        </div>
        <div>
          <p className="text-gray-500">Runtime</p>
          <p className="text-white">{sandbox.runtime}</p>
        </div>
        {sandbox.taskName && (
          <div>
            <p className="text-gray-500">Task</p>
            <p className="text-purple-400">{sandbox.taskName}</p>
          </div>
        )}
        {sandbox.agentName && (
          <div>
            <p className="text-gray-500">Agent</p>
            <p className="text-blue-400">{sandbox.agentName}</p>
          </div>
        )}
        {sandbox.created && (
          <div>
            <p className="text-gray-500">Started</p>
            <p className="text-white">{formatDateTimeFromUnix(sandbox.created)}</p>
          </div>
        )}
        {sandbox.completed && (
          <div>
            <p className="text-gray-500">Completed</p>
            <p className="text-white">{formatDateTimeFromUnix(sandbox.completed)}</p>
          </div>
        )}
        {sandbox.duration && (
          <div>
            <p className="text-gray-500">Duration</p>
            <p className="text-white">{formatDurationMs(sandbox.duration)}</p>
          </div>
        )}
      </div>
    </div>
  )
}

// ============================================================================
// Main Component
// ============================================================================

interface BrowserScreenshot {
  data: string  // Base64 encoded
  width: number
  height: number
  timestamp: number
}

// ============================================================================
// Confirmation Dialog
// ============================================================================

function ConfirmDialog({
  open,
  title,
  message,
  confirmText = 'Delete',
  cancelText = 'Cancel',
  danger = true,
  onConfirm,
  onCancel,
}: {
  open: boolean
  title: string
  message: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-[99999] flex items-center justify-center">
      {/* Backdrop */}
      <div 
        className="absolute inset-0 bg-black/60 backdrop-blur-md"
        onClick={onCancel}
      />
      
      {/* Dialog */}
      <div className="relative bg-dark-surface border border-dark-border rounded-xl shadow-2xl w-full max-w-md mx-4 p-6 animate-in fade-in zoom-in-95 duration-200">
        <div className="flex items-start gap-4">
          {danger && (
            <div className="flex-shrink-0 p-2 bg-red-500/20 rounded-full">
              <ExclamationCircleIcon className="w-6 h-6 text-red-400" />
            </div>
          )}
          <div className="flex-1 min-w-0">
            <h3 className="text-lg font-semibold text-white">{title}</h3>
            <p className="mt-2 text-sm text-gray-400">{message}</p>
          </div>
        </div>
        
        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm font-medium text-gray-300 bg-dark-hover hover:bg-dark-border rounded-lg transition-colors"
          >
            {cancelText}
          </button>
          <button
            onClick={onConfirm}
            className={clsx(
              "px-4 py-2 text-sm font-medium text-white rounded-lg transition-colors",
              danger
                ? "bg-red-500 hover:bg-red-600"
                : "bg-primary-500 hover:bg-primary-600"
            )}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}

// ============================================================================
// Fullscreen Panel Modal (reusable for Event History, Logs, etc.)
// ============================================================================

function FullscreenPanel({
  title,
  onClose,
  children,
}: {
  title: string
  onClose: () => void
  children: React.ReactNode
}) {
  // Close on Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  // Render via portal so the modal escapes any parent stacking context
  // (e.g. the Views container with z-0) and always appears above the Sidebar.
  return createPortal(
    <div className="fixed inset-0 z-[99999] flex items-center justify-center bg-black/60 backdrop-blur-md">
      <div className="w-[90vw] h-[85vh] bg-dark-surface border border-dark-border rounded-xl shadow-2xl flex flex-col animate-fade-in">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-dark-border shrink-0">
          <h2 className="text-lg font-semibold text-white">{title}</h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-dark-hover rounded-lg transition-colors text-gray-400 hover:text-white"
            title="Close (Esc)"
          >
            <ArrowsPointingInIcon className="w-5 h-5" />
          </button>
        </div>
        {/* Content */}
        <div className="flex-1 overflow-hidden p-4">
          {children}
        </div>
      </div>
    </div>,
    document.body
  )
}


export default function SandboxMonitor() {
  const [sandboxes, setSandboxes] = useState<SandboxInfo[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [metrics, setMetrics] = useState<SandboxMetrics | null>(null)
  const [events, setEvents] = useState<SandboxEvent[]>([])
  const [screenshot, setScreenshot] = useState<BrowserScreenshot | null>(null)
  const [loading, setLoading] = useState(false)
  const [apiError, setApiError] = useState<string | null>(null)
  const [showDemoMenu, setShowDemoMenu] = useState(false)
  const [showDemoSandboxes, setShowDemoSandboxes] = useState(false)  // Hide demo data by default
  const [expandedPanel, setExpandedPanel] = useState<'events' | null>(null)
  const demoPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Cleanup demo polling on unmount
  useEffect(() => {
    return () => {
      if (demoPollRef.current) clearInterval(demoPollRef.current)
    }
  }, [])
  
  // Helper to check if sandbox is demo data
  const isDemoSandbox = (s: SandboxInfo) => 
    s.role === 'demo' || 
    isDemoId(s.id) || 
    s.name?.toLowerCase().includes('[demo]')
  
  // Demo options
  interface DemoOption {
    id: string
    name: string
    description: string
    icon: React.ComponentType<{ className?: string }>
    type: 'quick' | 'script'
    script?: string
    args?: string[]
  }
  
  const demoOptions: DemoOption[] = [
    {
      id: 'quick',
      name: 'Quick Demo',
      description: 'Simulated sandbox (no Docker)',
      icon: BeakerIcon,
      type: 'quick',
    },
    {
      id: 'sandbox',
      name: 'Sandbox Demo',
      description: 'Shell, files, code, metrics',
      icon: CodeBracketIcon,
      type: 'script',
      script: 'sandbox_demo.py',
    },
    {
      id: 'browser',
      name: 'Browser Demo',
      description: 'Browser automation with VNC',
      icon: GlobeAltIcon,
      type: 'script',
      script: 'browser_automation_demo.py',
      args: ['--hot'],
    },
  ]
  
  // VNC fullscreen state
  const [vncFullscreen, setVncFullscreen] = useState(false)
  const vncContainerRef = useRef<HTMLDivElement>(null)
  
  // Confirmation dialog state
  const [deleteConfirm, setDeleteConfirm] = useState<{
    open: boolean
    sandboxId: string | null
    sandboxName: string
    isHot: boolean
  }>({
    open: false,
    sandboxId: null,
    sandboxName: '',
    isHot: false,
  })

  // Centralized API base URL (empty string uses Vite proxy in dev)
  const apiBase = getApiBase()

  // Group sandboxes
  // Only truly finished sandboxes go to the archive (completed / stopped).
  // "disconnected" means the heartbeat timed out — the sandbox may still be
  // running, so it stays in the active list (shown with a warning badge).
  const checkIsPending = (s: SandboxInfo) => s.isPending || s.state === 'pending'
  const checkIsStale = (s: SandboxInfo) => s.state === 'stale' || s.state === 'error' || !!s.fetchError
  const checkIsArchived = (s: SandboxInfo) => ['completed', 'stopped'].includes(s.state)
  const checkIsActive = (s: SandboxInfo) => !checkIsPending(s) && !checkIsStale(s) && !checkIsArchived(s)
  
  // Filter out demo sandboxes if not showing them
  const filteredSandboxes = showDemoSandboxes 
    ? sandboxes 
    : sandboxes.filter(s => !isDemoSandbox(s))
  
  // Count demo sandboxes for UI indicator
  const demoCount = sandboxes.filter(isDemoSandbox).length
  
  const pendingSandboxes = filteredSandboxes.filter(checkIsPending)
  const staleSandboxes = filteredSandboxes.filter(s => !checkIsPending(s) && checkIsStale(s))
  const archivedSandboxes = filteredSandboxes.filter(s => !checkIsPending(s) && !checkIsStale(s) && checkIsArchived(s))
  
  const taskSandboxes = filteredSandboxes.filter(s => s.taskId && checkIsActive(s))
  const agentSandboxes = filteredSandboxes.filter(s => s.agentId && !s.taskId && checkIsActive(s))
  const otherActiveSandboxes = filteredSandboxes.filter(s => !s.taskId && !s.agentId && checkIsActive(s))

  // Group by task
  const taskGroups = taskSandboxes.reduce((acc, s) => {
    const key = s.taskId || 'unknown'
    if (!acc[key]) acc[key] = { name: s.taskName || key, sandboxes: [] }
    acc[key].sandboxes.push(s)
    return acc
  }, {} as Record<string, { name: string; sandboxes: SandboxInfo[] }>)

  // Group by agent
  const agentGroups = agentSandboxes.reduce((acc, s) => {
    const key = s.agentId || 'unknown'
    if (!acc[key]) acc[key] = { name: s.agentName || key, sandboxes: [] }
    acc[key].sandboxes.push(s)
    return acc
  }, {} as Record<string, { name: string; sandboxes: SandboxInfo[] }>)

  // Fetch sandbox list
  const fetchSandboxes = async () => {
    try {
      const res = await fetch(`${apiBase}/api/sandbox`)
      
      // Log for debugging
      debugLog('[SandboxMonitor] Fetch response:', res.status, res.headers.get('content-type'))
      
      // Check if response is OK first
      if (!res.ok) {
        const text = await res.text()
        console.error('[SandboxMonitor] API error:', res.status, text)
        // Parse proxy/backend error for a clean message
        if (res.status === 503) {
          try {
            const errJson = JSON.parse(text)
            if (errJson.error === 'Backend unavailable') {
              setApiError('Backend server not running. Start with: uv run python -m teaming24.server.cli')
              return
            }
          } catch (e) { console.debug('Response not JSON, falling through:', e); }
        }
        setApiError(`API error ${res.status}: ${text.substring(0, 100)}`)
        return
      }
      
      // Check if response is JSON
      const contentType = res.headers.get('content-type')
      if (!contentType || !contentType.includes('application/json')) {
        // Got HTML or other non-JSON response (likely backend not running)
        const text = await res.text()
        console.error('[SandboxMonitor] Non-JSON response:', text.substring(0, 200))
        if (text.includes('<!doctype') || text.includes('<!DOCTYPE')) {
          setApiError('Backend server not running. Start with: uv run python -m teaming24.server.cli')
        } else {
          setApiError(`Unexpected response type: ${contentType || 'none'}`)
        }
        return
      }
      
      const data = await res.json()
      const realSandboxes: SandboxInfo[] = (data.sandboxes || []).map((s: SandboxInfo) => ({
        ...s,
        isPending: false, // Real sandboxes are never pending
      }))
      debugLog('[SandboxMonitor] Fetched sandboxes:', realSandboxes.length, realSandboxes.map((s: SandboxInfo) => `${s.id}:${s.state}`))
      
      // Merge with pending sandboxes
      setSandboxes(prev => {
        const pendingFromPrev = prev.filter(s => s.isPending)
        const realIds = new Set(realSandboxes.map(s => s.id))
        
        // Keep pending sandboxes that don't have a matching real sandbox yet
        const stillPending = pendingFromPrev.filter(p => !realIds.has(p.id))
        
        // Log transition from pending to real
        const transitioned = pendingFromPrev.filter(p => realIds.has(p.id))
        if (transitioned.length > 0) {
          debugLog('[SandboxMonitor] Pending -> Real:', transitioned.map(s => s.id))
        }
        
        // Merge: pending first (that aren't registered yet), then real sandboxes
        return [...stillPending, ...realSandboxes]
      })
      setApiError(null)
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err)
      console.error('[SandboxMonitor] Fetch error:', errorMsg)
      // Only show error if it's not a network error during initial load
      if (errorMsg.includes('Failed to fetch') || errorMsg.includes('NetworkError')) {
        setApiError('Cannot connect to backend. Is the server running?')
      } else {
        setApiError(`Failed to fetch sandboxes: ${errorMsg}`)
      }
    }
  }

  // Show delete confirmation dialog
  const confirmDeleteSandbox = (id: string) => {
    const sandbox = sandboxes.find(s => s.id === id)
    if (!sandbox) return
    
    // Check if it's a hot (running) sandbox
    const isHot = sandbox.state === 'running' || sandbox.state === 'paused'
    
    setDeleteConfirm({
      open: true,
      sandboxId: id,
      sandboxName: sandbox.name || sandbox.id,
      isHot,
    })
  }
  
  // Perform the actual deletion
  const executeSandboxDelete = async () => {
    const { sandboxId } = deleteConfirm
    if (!sandboxId) return
    
    // Close dialog first
    setDeleteConfirm({ open: false, sandboxId: null, sandboxName: '', isHot: false })
    
    try {
      const res = await fetch(`${apiBase}/api/sandbox/${sandboxId}`, { 
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cleanup: true })  // Request full cleanup
      })
      if (res.ok || res.status === 404) {
        // Remove from local state regardless of server response
        setSandboxes(prev => prev.filter(s => s.id !== sandboxId))
        if (selectedId === sandboxId) {
          setSelectedId(null)
        }
      }
    } catch (err) {
      // Still remove from UI even if delete fails
      setSandboxes(prev => prev.filter(s => s.id !== sandboxId))
      if (selectedId === sandboxId) {
        setSelectedId(null)
      }
      console.error('Failed to delete sandbox:', err)
    }
  }
  
  // Cancel deletion
  const cancelDelete = () => {
    setDeleteConfirm({ open: false, sandboxId: null, sandboxName: '', isHot: false })
  }
  
  // Legacy function for backwards compatibility (now shows confirmation)
  const deleteSandbox = (id: string) => {
    confirmDeleteSandbox(id)
  }
  
  // Stop a running sandbox (also stops Docker container)
  const stopSandbox = async (id: string) => {
    try {
      // Use dedicated stop endpoint that also handles Docker
      const res = await fetch(`${apiBase}/api/sandbox/${id}/stop`, {
        method: 'POST',
      })
      
      if (res.ok) {
        const data = await res.json()
        // Update local state immediately
        setSandboxes(prev => prev.map(s => 
          s.id === id ? { ...s, state: 'stopped' as const, duration: data.duration_ms } : s
        ))
        debugLog(`[Sandbox] Stopped: ${id}`, data)
      } else {
        console.error(`[Sandbox] Failed to stop: ${res.status}`)
      }
    } catch (err) {
      console.error('[Sandbox] Stop error:', err)
    }
  }

  // Create quick demo sandbox (simulated, no Docker)
  const createQuickDemo = async () => {
    if (apiError) return
    setLoading(true)
    setShowDemoMenu(false)
    try {
      const res = await fetch(`${apiBase}/api/sandbox/demo`, { method: 'POST' })
      
      const contentType = res.headers.get('content-type')
      if (!contentType || !contentType.includes('application/json')) {
        setApiError('Backend server not running. Start with: uv run python -m teaming24.server.cli')
        return
      }
      
      if (!res.ok) {
        const error = await res.json().catch((e: unknown) => { console.warn('Failed to parse demo error response:', e); return { error: res.statusText }; })
        setApiError(error.message || error.error || `Failed to create demo: ${res.status}`)
        return
      }
      
      const data = await res.json()
      await fetchSandboxes()
      setSelectedId(data.id)
      setApiError(null)
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err)
      setApiError(`Failed to create demo sandbox: ${errorMsg}`)
      console.error('Failed to create demo sandbox:', err)
    } finally {
      setLoading(false)
    }
  }
  
  // Run example script
  const runDemoScript = async (script: string, args: string[] = []) => {
    if (apiError) return
    setLoading(true)
    setShowDemoMenu(false)
    
    // Generate a unique demo ID that will be used by the backend
    const sandboxDemoId = demoId()
    const demoInfo = demoOptions.find(d => d.script === script)
    
    // Immediately add pending sandbox for instant feedback
    const pendingSandbox: SandboxInfo = {
      id: sandboxDemoId, // Use the same ID that will be used by the real sandbox
      name: demoInfo?.name || script.replace('.py', ''),
      state: 'pending',
      runtime: 'docker',
      role: 'demo',
      created: Date.now(),
      isPending: true,
    }
    setSandboxes(prev => [pendingSandbox, ...prev.filter(s => s.id !== sandboxDemoId)])
    setSelectedId(sandboxDemoId)
    
    try {
      const res = await fetch(`${apiBase}/api/demo/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ script, args, demo_id: sandboxDemoId }),
      })
      
      const contentType = res.headers.get('content-type')
      
      if (!res.ok) {
        // Remove pending sandbox on error
        setSandboxes(prev => prev.filter(s => s.id !== sandboxDemoId))
        setSelectedId(null)
        
        let errorDetail = `Failed to run demo: ${res.status}`
        if (contentType?.includes('application/json')) {
          const error = await res.json().catch((e: unknown) => { console.warn('Failed to parse run-demo error response:', e); return {}; })
          errorDetail = error.detail || error.message || error.error || errorDetail
        }
        setApiError(errorDetail)
        console.error('[Demo] Run failed:', errorDetail)
        return
      }
      
      if (!contentType || !contentType.includes('application/json')) {
        setSandboxes(prev => prev.filter(s => s.id !== sandboxDemoId))
        setSelectedId(null)
        setApiError('Backend server not running. Start with: uv run python -m teaming24.server.cli')
        return
      }
      
      const data = await res.json()
      debugLog('[Demo] Started:', data)
      setApiError(null)
      
      // Poll for the sandbox to register and update state.
      // Clear any prior poll and store in ref for cleanup on unmount.
      if (demoPollRef.current) clearInterval(demoPollRef.current)
      let pollCount = 0
      demoPollRef.current = setInterval(async () => {
        pollCount++
        debugLog(`[Demo] Polling for sandbox ${sandboxDemoId} (attempt ${pollCount})`)
        await fetchSandboxes()
        
        if (pollCount >= 20) {
          if (demoPollRef.current) clearInterval(demoPollRef.current)
          demoPollRef.current = null
        }
      }, 500)
    } catch (err) {
      // Remove pending sandbox on error
      setSandboxes(prev => prev.filter(s => s.id !== sandboxDemoId))
      setSelectedId(null)
      
      const errorMsg = err instanceof Error ? err.message : String(err)
      if (errorMsg.includes('Failed to fetch') || errorMsg.includes('NetworkError')) {
        setApiError('Cannot connect to backend. Is the server running?')
      } else {
        setApiError(`Failed to run demo script: ${errorMsg}`)
      }
      console.error('[Demo] Error:', err)
    } finally {
      setLoading(false)
    }
  }
  
  // Handle demo option click
  const handleDemoOption = (option: DemoOption) => {
    if (option.type === 'quick') {
      createQuickDemo()
    } else if (option.type === 'script' && option.script) {
      runDemoScript(option.script, option.args || [])
    }
  }

  // Subscribe to metrics stream (only for active sandboxes)
  useEffect(() => {
    if (!selectedId || apiError) return
    
    // Don't subscribe for stale sandboxes
    const sandbox = sandboxes.find(s => s.id === selectedId)
    if (sandbox?.fetchError || sandbox?.state === 'stale') return

    const eventSource = new EventSource(`${apiBase}/api/sandbox/${selectedId}/metrics`)
    let errorCount = 0
    const maxErrors = 3
    
    eventSource.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        setMetrics(data)
        errorCount = 0 // Reset on success
      } catch (err) {
        console.error('Failed to parse metrics:', err)
      }
    }

    eventSource.onerror = () => {
      errorCount++
      if (errorCount >= maxErrors) {
        eventSource.close()
        // Mark sandbox as having fetch error
        setSandboxes(prev => prev.map(s => 
          s.id === selectedId ? { ...s, fetchError: 'Cannot connect to sandbox' } : s
        ))
      }
    }

    return () => eventSource.close()
  }, [selectedId, apiError])

  // Subscribe to events stream (only for active sandboxes)
  useEffect(() => {
    if (!selectedId || apiError) return
    
    // Don't subscribe for stale sandboxes
    const sandbox = sandboxes.find(s => s.id === selectedId)
    if (sandbox?.fetchError || sandbox?.state === 'stale') return

    const eventSource = new EventSource(`${apiBase}/api/sandbox/${selectedId}/events`)
    let errorCount = 0
    const maxErrors = 3
    
    eventSource.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        // Skip heartbeat events - they're just for keepalive
        if (data.type === 'heartbeat') {
          errorCount = 0 // Reset on success
          return
        }
        setEvents(prev => [...prev.slice(-99), data])
        errorCount = 0 // Reset on success
      } catch (err) {
        console.error('Failed to parse event:', err)
      }
    }

    eventSource.onerror = () => {
      errorCount++
      if (errorCount >= maxErrors) {
        eventSource.close()
      }
    }

    return () => eventSource.close()
  }, [selectedId, apiError])

  // Poll for screenshots — only for sandboxes that actually have browser/desktop
  // capability.  A sandbox supports screenshots when it has a cdpUrl (Chrome
  // DevTools Protocol) registered at creation time, indicating an attached
  // browser instance that can produce screenshots.  Shell-only sandboxes and
  // OpenHands sandboxes never upload screenshots, so polling them just creates
  // noisy 404 logs on the backend.
  useEffect(() => {
    if (!selectedId || apiError) {
      setScreenshot(null)
      return
    }
    
    const sandbox = sandboxes.find(s => s.id === selectedId)
    if (!sandbox) {
      setScreenshot(null)
      return
    }

    // ---- Guard: only poll sandboxes that can produce screenshots ----

    // Not active? No screenshots.
    if (sandbox.fetchError || sandbox.isPending ||
        ['stale', 'completed', 'stopped', 'error', 'disconnected', 'pending'].includes(sandbox.state)) {
      setScreenshot(null)
      return
    }

    // VNC available → use VNC live view instead of screenshot polling
    if (sandbox.vncUrl) {
      setScreenshot(null)
      return
    }

    // Only poll if sandbox has browser capability (cdpUrl set at registration)
    // Sandboxes without cdpUrl are shell-only and never produce screenshots.
    if (!sandbox.cdpUrl) {
      setScreenshot(null)
      return
    }

    // OpenHands sandboxes don't expose the screenshot API
    if (sandbox.runtime === 'openhands') {
      setScreenshot(null)
      return
    }

    let stopped = false

    const fetchScreenshot = async () => {
      if (stopped) return
      try {
        const res = await fetch(`${apiBase}/api/sandbox/${selectedId}/screenshot`)
        if (res.ok) {
          const data = await res.json()
          setScreenshot(data)
        } else {
          // 404 or other error — just clear, don't spam retries
          setScreenshot(null)
        }
      } catch (e) {
        console.warn('Failed to fetch screenshot:', e);
        setScreenshot(null)
      }
    }

    // Fetch immediately, then poll every 2 seconds
    fetchScreenshot()
    const interval = setInterval(fetchScreenshot, 2000)
    return () => { stopped = true; clearInterval(interval) }
  }, [selectedId, apiError, sandboxes])

  // Initial fetch and real-time updates via SSE
  useEffect(() => {
    fetchSandboxes()
    
    // Subscribe to real-time sandbox list updates
    const eventSource = new EventSource(`${apiBase}/api/sandbox/stream`)
    
    eventSource.onopen = () => {
      debugLog('[SSE] Connected to sandbox stream')
    }
    
    eventSource.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        debugLog('[SSE] Received event:', data.type, data.sandbox_id)
        
        // On any update event, refresh the sandbox list immediately
        if (data.type === 'registered' || data.type === 'deleted' || data.type === 'state_changed') {
          // Small delay to ensure backend has fully processed
          setTimeout(async () => {
            debugLog('[SSE] Refreshing sandbox list after:', data.type)
            await fetchSandboxes()
            
            // Auto-select newly registered sandbox
            if (data.type === 'registered' && data.sandbox_id) {
              debugLog('[SSE] Auto-selecting new sandbox:', data.sandbox_id)
              setSelectedId(data.sandbox_id)
            }
          }, 100)
        }
      } catch (err) {
        console.error('[SSE] Failed to parse event:', err)
      }
    }
    
    eventSource.onerror = (err) => {
      debugWarn('[SSE] Stream error, falling back to polling:', err)
    }
    
    // Fallback polling (longer interval since we have SSE)
    const interval = setInterval(fetchSandboxes, 10000)
    
    return () => {
      debugLog('[SSE] Closing connection')
      eventSource.close()
      clearInterval(interval)
    }
  }, [])

  // Clear data on sandbox change
  useEffect(() => {
    setEvents([])
    setMetrics(null)
    setScreenshot(null)
    setVncFullscreen(false)
  }, [selectedId])
  
  // ESC key to exit VNC fullscreen
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && vncFullscreen) {
        setVncFullscreen(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [vncFullscreen])

  const selectedSandbox = sandboxes.find(s => s.id === selectedId)
  // Only truly finished sandboxes show the CompletedSummary view.
  // Disconnected sandboxes may still be running (heartbeat timeout).
  const isFinished = selectedSandbox?.state === 'completed' || 
                     selectedSandbox?.state === 'stopped' || 
                     selectedSandbox?.state === 'stale' ||
                     selectedSandbox?.state === 'error' ||
                     !!selectedSandbox?.fetchError

  return (
    <div className="flex h-full">
      {/* Delete Confirmation Dialog */}
      <ConfirmDialog
        open={deleteConfirm.open}
        title="Delete Sandbox?"
        message={
          deleteConfirm.isHot
            ? `This will stop the running container and delete all files for "${deleteConfirm.sandboxName}". This action cannot be undone.`
            : `This will remove the sandbox record "${deleteConfirm.sandboxName}" from the list.`
        }
        confirmText={deleteConfirm.isHot ? "Stop & Delete" : "Delete"}
        onConfirm={executeSandboxDelete}
        onCancel={cancelDelete}
        danger
      />
      
      {/* Sidebar - Sandbox List */}
      <aside className="w-72 border-r border-dark-border bg-dark-surface flex flex-col">
        <div className="p-4 border-b border-dark-border">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <ServerIcon className="w-5 h-5 text-primary-400" />
              Sandboxes
            </h2>
            {/* Toggle demo visibility - only show when demo mode is enabled */}
            {import.meta.env.VITE_ENABLE_DEMO === 'true' && demoCount > 0 && (
              <button
                onClick={() => setShowDemoSandboxes(!showDemoSandboxes)}
                className={clsx(
                  "text-xs px-2 py-1 rounded transition-colors",
                  showDemoSandboxes
                    ? "bg-yellow-500/20 text-yellow-400 hover:bg-yellow-500/30"
                    : "bg-dark-hover text-gray-500 hover:text-gray-400"
                )}
                title={showDemoSandboxes ? "Hide demo sandboxes" : "Show demo sandboxes"}
              >
                {showDemoSandboxes ? `Demo (${demoCount})` : `+${demoCount} Demo`}
              </button>
            )}
          </div>
          <p className="text-xs text-gray-500 mt-1">
            {filteredSandboxes.filter(s => s.state === 'running').length} running • {archivedSandboxes.length} archived
          </p>
        </div>

        <div className="flex-1 overflow-y-auto p-3">
          {/* API Error Banner */}
          {apiError && (
            <div className="mb-3 p-3 bg-red-500/10 border border-red-500/30 rounded-lg">
              <div className="flex items-start gap-2">
                <ExclamationCircleIcon className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-red-300 font-medium">Connection Error</p>
                  <p className="text-xs text-red-400 mt-1">{apiError}</p>
                </div>
              </div>
            </div>
          )}
          
          {filteredSandboxes.length === 0 && !apiError ? (
            <p className="text-gray-500 text-sm text-center py-4">
              {demoCount > 0 ? `${demoCount} demo sandbox(es) hidden` : 'No sandboxes'}
            </p>
          ) : (
            <>
              {/* Pending Sandboxes (Starting up) */}
              {pendingSandboxes.length > 0 && (
                <SandboxGroup
                  title="Starting"
                  icon={ArrowPathIcon}
                  count={pendingSandboxes.length}
                  color="text-primary-400"
                >
                  {pendingSandboxes.map(sandbox => (
                    <SandboxCard
                      key={sandbox.id}
                      sandbox={sandbox}
                      selected={sandbox.id === selectedId}
                      onSelect={() => setSelectedId(sandbox.id)}
                      showTaskInfo
                    />
                  ))}
                </SandboxGroup>
              )}
              
              {/* Stale/Error Sandboxes */}
              {staleSandboxes.length > 0 && (
                <SandboxGroup
                  title="Stale Records"
                  icon={ExclamationCircleIcon}
                  count={staleSandboxes.length}
                  color="text-red-400"
                >
                  <p className="text-xs text-gray-500 px-2 mb-2">
                    These records may be from previous sessions. Click to remove.
                  </p>
                  {staleSandboxes.map(sandbox => (
                    <SandboxCard
                      key={sandbox.id}
                      sandbox={sandbox}
                      selected={sandbox.id === selectedId}
                      onSelect={() => setSelectedId(sandbox.id)}
                      onDelete={deleteSandbox}
                      onStop={stopSandbox}
                      showTaskInfo
                    />
                  ))}
                </SandboxGroup>
              )}
              
              {/* By Task */}
              {Object.keys(taskGroups).length > 0 && (
                <SandboxGroup
                  title="By Task"
                  icon={FolderIcon}
                  count={taskSandboxes.length}
                  color="text-purple-400"
                >
                  {Object.entries(taskGroups).map(([taskId, group]) => (
                    <div key={taskId} className="mb-2">
                      <p className="text-xs text-purple-400 px-2 mb-1 truncate">{group.name}</p>
                      <div className="space-y-1">
                        {group.sandboxes.map(sandbox => (
                          <SandboxCard
                            key={sandbox.id}
                            sandbox={sandbox}
                            selected={sandbox.id === selectedId}
                            onSelect={() => setSelectedId(sandbox.id)}
                            onDelete={deleteSandbox}
                            onStop={stopSandbox}
                          />
                        ))}
                      </div>
                    </div>
                  ))}
                </SandboxGroup>
              )}

              {/* By Agent */}
              {Object.keys(agentGroups).length > 0 && (
                <SandboxGroup
                  title="By Agent"
                  icon={UserIcon}
                  count={agentSandboxes.length}
                  color="text-blue-400"
                >
                  {Object.entries(agentGroups).map(([agentId, group]) => (
                    <div key={agentId} className="mb-2">
                      <p className="text-xs text-blue-400 px-2 mb-1 truncate">{group.name}</p>
                      <div className="space-y-1">
                        {group.sandboxes.map(sandbox => (
                          <SandboxCard
                            key={sandbox.id}
                            sandbox={sandbox}
                            selected={sandbox.id === selectedId}
                            onSelect={() => setSelectedId(sandbox.id)}
                            onDelete={deleteSandbox}
                            onStop={stopSandbox}
                          />
                        ))}
                      </div>
                    </div>
                  ))}
                </SandboxGroup>
              )}

              {/* Other Active */}
              {otherActiveSandboxes.length > 0 && (
                <SandboxGroup
                  title="Active"
                  icon={ServerIcon}
                  count={otherActiveSandboxes.length}
                  color="text-green-400"
                >
                  {otherActiveSandboxes.map(sandbox => (
                    <SandboxCard
                      key={sandbox.id}
                      sandbox={sandbox}
                      selected={sandbox.id === selectedId}
                      onSelect={() => setSelectedId(sandbox.id)}
                      onDelete={deleteSandbox}
                      onStop={stopSandbox}
                      showTaskInfo
                    />
                  ))}
                </SandboxGroup>
              )}

              {/* Archived (completed / stopped only) */}
              {archivedSandboxes.length > 0 && (
                <SandboxGroup
                  title="Archived"
                  icon={ArchiveBoxIcon}
                  count={archivedSandboxes.length}
                  defaultExpanded={false}
                  color="text-gray-400"
                >
                  {archivedSandboxes.map(sandbox => (
                    <SandboxCard
                      key={sandbox.id}
                      sandbox={sandbox}
                      selected={sandbox.id === selectedId}
                      onSelect={() => setSelectedId(sandbox.id)}
                      onDelete={deleteSandbox}
                      showTaskInfo
                    />
                  ))}
                </SandboxGroup>
              )}
            </>
          )}
        </div>

        <div className="p-3 border-t border-dark-border space-y-2">
          {/* Demo menu dropdown - only show in demo mode */}
          {import.meta.env.VITE_ENABLE_DEMO === 'true' && (
          <div className="relative">
            <button
              onClick={() => setShowDemoMenu(!showDemoMenu)}
              disabled={loading || !!apiError}
              className={clsx(
                "w-full flex items-center justify-center gap-2 px-4 py-2 text-white rounded-lg transition-colors",
                apiError
                  ? "bg-gray-600 cursor-not-allowed opacity-50"
                  : "bg-primary-500 hover:bg-primary-600 disabled:opacity-50"
              )}
            >
              {loading ? (
                <ArrowPathIcon className="w-4 h-4 animate-spin" />
              ) : (
                <PlayIcon className="w-4 h-4" />
              )}
              Run Demo
              {showDemoMenu ? (
                <ChevronUpIcon className="w-4 h-4" />
              ) : (
                <ChevronDownIcon className="w-4 h-4" />
              )}
            </button>
            
            {/* Dropdown menu */}
            {showDemoMenu && !apiError && (
              <div className="absolute bottom-full left-0 right-0 mb-1 bg-dark-surface border border-dark-border rounded-lg shadow-lg overflow-hidden z-10">
                {demoOptions.map((option) => (
                  <button
                    key={option.id}
                    onClick={() => handleDemoOption(option)}
                    className="w-full flex items-start gap-3 px-3 py-2.5 hover:bg-dark-hover transition-colors text-left"
                  >
                    <option.icon className="w-5 h-5 text-primary-400 mt-0.5 shrink-0" />
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-white">{option.name}</div>
                      <div className="text-xs text-gray-500">{option.description}</div>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
          )}
          {apiError && (
            <button
              onClick={fetchSandboxes}
              className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-dark-hover hover:bg-dark-border text-gray-300 rounded-lg transition-colors text-sm"
            >
              <ArrowPathIcon className="w-4 h-4" />
              Retry Connection
            </button>
          )}
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col min-w-0 bg-dark-bg">
        {!selectedSandbox ? (
          <>
            {/* Empty state when no sandbox selected */}
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center">
                <ServerIcon className="w-16 h-16 text-gray-600 mx-auto mb-4" />
                <h3 className="text-xl font-medium text-gray-400">Select a Sandbox</h3>
                <p className="text-gray-500 mt-2">Choose a sandbox from the list to view monitoring</p>
              </div>
            </div>
          </>
        ) : (
          <>
            {/* Toolbar */}
            <div className="px-6 py-2 border-b border-dark-border bg-dark-bg/50">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div>
                    <span className="text-sm font-medium text-white">
                      {selectedSandbox.name || selectedSandbox.id}
                    </span>
                    <span className="text-xs text-gray-500 ml-2">
                      {selectedSandbox.runtime}
                      {selectedSandbox.role && ` • ${selectedSandbox.role}`}
                      {selectedSandbox.agentId && ` • Agent: ${selectedSandbox.agentName || selectedSandbox.agentId}`}
                    </span>
                  </div>
                  <span
                    className={clsx(
                      'px-2 py-0.5 rounded-full text-xs font-medium',
                      selectedSandbox.state === 'running' && 'bg-green-500/20 text-green-400',
                      selectedSandbox.state === 'paused' && 'bg-yellow-500/20 text-yellow-400',
                      selectedSandbox.state === 'stopped' && 'bg-gray-500/20 text-gray-400',
                      selectedSandbox.state === 'error' && 'bg-red-500/20 text-red-400',
                      selectedSandbox.state === 'completed' && 'bg-blue-500/20 text-blue-400',
                      selectedSandbox.state === 'disconnected' && 'bg-orange-500/20 text-orange-400'
                    )}
                  >
                    {selectedSandbox.state}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {/* Actions for running sandboxes */}
                  {selectedSandbox.state === 'running' && (
                    <button 
                      onClick={() => stopSandbox(selectedSandbox.id)}
                      className="p-1.5 hover:bg-red-500/20 rounded-lg transition-colors" 
                      title="Stop sandbox"
                    >
                      <StopIcon className="w-4 h-4 text-red-400" />
                    </button>
                  )}
                  {/* Delete for non-running sandboxes */}
                  {selectedSandbox.state !== 'running' && (
                    <button 
                      onClick={() => confirmDeleteSandbox(selectedSandbox.id)}
                      className="p-1.5 hover:bg-red-500/20 rounded-lg transition-colors" 
                      title="Delete sandbox"
                    >
                      <TrashIcon className="w-4 h-4 text-red-400" />
                    </button>
                  )}
                  <button
                    onClick={fetchSandboxes}
                    className="p-1.5 hover:bg-dark-border rounded-lg transition-colors"
                    title="Refresh"
                  >
                    <ArrowPathIcon className="w-4 h-4 text-gray-400" />
                  </button>
                </div>
              </div>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto p-6">
              {isFinished ? (
                /* Completed/Stale Sandbox View */
                <div className="space-y-6">
                  <CompletedSummary sandbox={selectedSandbox} onDelete={deleteSandbox} />
                  
                  {!selectedSandbox.fetchError && (
                    <div>
                      <div className="flex items-center justify-between mb-4">
                        <h2 className="text-lg font-semibold text-white">Event History</h2>
                        <button
                          onClick={() => setExpandedPanel('events')}
                          className="p-1.5 hover:bg-dark-border rounded-lg transition-colors"
                          title="Expand"
                        >
                          <ArrowsPointingOutIcon className="w-4 h-4 text-gray-400" />
                        </button>
                      </div>
                      <EventLog events={events} autoScroll={false} heightClass="h-96" />
                    </div>
                  )}
                </div>
              ) : (
                /* Active Sandbox View */
                <div className="space-y-6">
                  {/* VNC Live View - Primary */}
                  {selectedSandbox.vncUrl && !vncFullscreen && (
                    <div ref={vncContainerRef}>
                      <div className="flex items-center justify-between mb-4">
                        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                          <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                          Browser Live View
                          <span className="text-xs font-normal text-gray-500">(VNC Stream)</span>
                        </h2>
                        <div className="flex items-center gap-3">
                          <a
                            href={selectedSandbox.vncUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-primary-400 hover:text-primary-300 transition-colors"
                          >
                            Open in new tab →
                          </a>
                          <button
                            onClick={() => setVncFullscreen(true)}
                            className="p-2 hover:bg-dark-border rounded-lg transition-colors"
                            title="Fullscreen"
                          >
                            <ArrowsPointingOutIcon className="w-5 h-5 text-gray-400" />
                          </button>
                        </div>
                      </div>
                      <div className="relative rounded-lg overflow-hidden border border-dark-border bg-black">
                        <iframe
                          src={selectedSandbox.vncUrl}
                          className="w-full border-0 h-[500px]"
                          title="VNC Live View"
                          allow="clipboard-read; clipboard-write"
                        />
                      </div>
                    </div>
                  )}
                  {/* VNC Fullscreen — portal to escape stacking context */}
                  {selectedSandbox.vncUrl && vncFullscreen && createPortal(
                    <div className="fixed inset-0 z-[99999] bg-black flex flex-col">
                      <div className="flex items-center justify-between p-4 bg-dark-surface border-b border-dark-border">
                        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                          <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                          Browser Live View
                          <span className="text-xs font-normal text-gray-500">(VNC Stream)</span>
                        </h2>
                        <div className="flex items-center gap-3">
                          <a
                            href={selectedSandbox.vncUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-primary-400 hover:text-primary-300 transition-colors"
                          >
                            Open in new tab →
                          </a>
                          <button
                            onClick={() => setVncFullscreen(false)}
                            className="p-2 hover:bg-dark-border rounded-lg transition-colors"
                            title="Exit fullscreen"
                          >
                            <ArrowsPointingInIcon className="w-5 h-5 text-gray-400" />
                          </button>
                        </div>
                      </div>
                      <div className="flex-1 rounded-none border-0 relative overflow-hidden bg-black">
                        <iframe
                          src={selectedSandbox.vncUrl}
                          className="w-full h-full border-0"
                          title="VNC Live View"
                          allow="clipboard-read; clipboard-write"
                        />
                      </div>
                      <div className="p-2 bg-dark-surface border-t border-dark-border text-center">
                        <span className="text-xs text-gray-500">Press ESC or click the button to exit fullscreen</span>
                      </div>
                    </div>,
                    document.body
                  )}
                  
                  {/* Fallback to Screenshot if no VNC */}
                  {!selectedSandbox.vncUrl && screenshot && (
                    <div>
                      <div className="flex items-center justify-between mb-4">
                        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                          <span className="w-2 h-2 bg-yellow-500 rounded-full animate-pulse" />
                          Browser Live View
                          <span className="text-xs font-normal text-gray-500">(Screenshot)</span>
                        </h2>
                        <span className="text-xs text-gray-500">
                          {screenshot.width}x{screenshot.height} • Updated {formatDateTimeFromUnix(screenshot.timestamp)}
                        </span>
                      </div>
                      <div className="relative rounded-lg overflow-hidden border border-dark-border bg-black">
                        <img
                          src={`data:image/png;base64,${screenshot.data}`}
                          alt="Browser screenshot"
                          className="w-full h-auto max-h-[500px] object-contain"
                        />
                      </div>
                    </div>
                  )}

                  {/* Metrics */}
                  <div>
                    <h2 className="text-lg font-semibold text-white mb-4">System Metrics</h2>
                    <div className="grid grid-cols-4 gap-4">
                      <MetricCard
                        icon={CpuChipIcon}
                        label="CPU Usage"
                        value={metrics?.cpu_pct ?? 0}
                        unit="%"
                        color="blue"
                      />
                      <MetricCard
                        icon={ServerIcon}
                        label="Memory"
                        value={metrics?.mem_pct ?? 0}
                        unit="%"
                        color="green"
                      />
                      <MetricCard
                        icon={CircleStackIcon}
                        label="Memory Used"
                        value={metrics?.mem_used_mb ?? 0}
                        unit="MB"
                        color="yellow"
                      />
                      <MetricCard
                        icon={CircleStackIcon}
                        label="Disk Usage"
                        value={metrics?.disk_pct ?? 0}
                        unit="%"
                        color="red"
                      />
                    </div>
                  </div>

                  {/* Event Log */}
                  <div>
                    <div className="flex items-center justify-between mb-4">
                      <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                        Event Log
                        {selectedSandbox.state === 'running' && (
                          <span className="text-xs font-normal text-gray-500">(auto-scrolling)</span>
                        )}
                      </h2>
                      <button
                        onClick={() => setExpandedPanel('events')}
                        className="p-1.5 hover:bg-dark-border rounded-lg transition-colors"
                        title="Expand"
                      >
                        <ArrowsPointingOutIcon className="w-4 h-4 text-gray-400" />
                      </button>
                    </div>
                    <EventLog events={events} autoScroll={selectedSandbox.state === 'running'} heightClass="h-96" />
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </main>

      {/* Fullscreen Event Log Modal */}
      {expandedPanel === 'events' && (
        <FullscreenPanel title="Event Log" onClose={() => setExpandedPanel(null)}>
          <EventLog
            events={events}
            autoScroll={selectedSandbox?.state === 'running'}
            heightClass="h-full"
          />
        </FullscreenPanel>
      )}
    </div>
  )
}
