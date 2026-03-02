import { UserIcon, CpuChipIcon, ChevronDownIcon, ChevronRightIcon, ClipboardIcon, CheckIcon, ClipboardDocumentIcon, PencilIcon, DocumentIcon } from '@heroicons/react/24/outline'
import clsx from 'clsx'
import ReactMarkdown from 'react-markdown'
import { Light as SyntaxHighlighter } from 'react-syntax-highlighter'
import { atomOneDark as oneDark } from 'react-syntax-highlighter/dist/esm/styles/hljs'
// Import only common languages to reduce bundle size
import javascript from 'react-syntax-highlighter/dist/esm/languages/hljs/javascript'
import typescript from 'react-syntax-highlighter/dist/esm/languages/hljs/typescript'
import python from 'react-syntax-highlighter/dist/esm/languages/hljs/python'
import bash from 'react-syntax-highlighter/dist/esm/languages/hljs/bash'
import json from 'react-syntax-highlighter/dist/esm/languages/hljs/json'
import css from 'react-syntax-highlighter/dist/esm/languages/hljs/css'
import sql from 'react-syntax-highlighter/dist/esm/languages/hljs/sql'
import yaml from 'react-syntax-highlighter/dist/esm/languages/hljs/yaml'
import markdown from 'react-syntax-highlighter/dist/esm/languages/hljs/markdown'
import xml from 'react-syntax-highlighter/dist/esm/languages/hljs/xml'

// Register languages
SyntaxHighlighter.registerLanguage('javascript', javascript)
SyntaxHighlighter.registerLanguage('js', javascript)
SyntaxHighlighter.registerLanguage('typescript', typescript)
SyntaxHighlighter.registerLanguage('ts', typescript)
SyntaxHighlighter.registerLanguage('tsx', typescript)
SyntaxHighlighter.registerLanguage('jsx', javascript)
SyntaxHighlighter.registerLanguage('python', python)
SyntaxHighlighter.registerLanguage('py', python)
SyntaxHighlighter.registerLanguage('bash', bash)
SyntaxHighlighter.registerLanguage('shell', bash)
SyntaxHighlighter.registerLanguage('sh', bash)
SyntaxHighlighter.registerLanguage('json', json)
SyntaxHighlighter.registerLanguage('css', css)
SyntaxHighlighter.registerLanguage('sql', sql)
SyntaxHighlighter.registerLanguage('yaml', yaml)
SyntaxHighlighter.registerLanguage('yml', yaml)
SyntaxHighlighter.registerLanguage('markdown', markdown)
SyntaxHighlighter.registerLanguage('md', markdown)
SyntaxHighlighter.registerLanguage('xml', xml)
SyntaxHighlighter.registerLanguage('html', xml)
import remarkGfm from 'remark-gfm'
import React, { useState, useCallback, useEffect, useRef } from 'react'
import { AgentStep, type MessageAttachment } from '../store/chatStore'
import { useConfigStore } from '../store/configStore'
import type { WorkerStatusSummary } from '../store/agentStore'
import { formatDurationSecs, formatTokenCount, formatNumberNoTrailingZeros, formatDurationFromTimestamps } from '../utils/format'

/** Safely render children: convert plain objects (e.g. event objects) to string to avoid "Objects are not valid as a React child" */
function safeChildren(children: React.ReactNode): React.ReactNode {
  return React.Children.map(children, (child) => {
    if (child == null || typeof child === 'string' || typeof child === 'number' || React.isValidElement(child)) {
      return child
    }
    return String(child)
  })
}

function humanizeStepAction(action: string): string {
  const raw = String(action || '').trim()
  if (!raw) return ''
  const lower = raw.toLowerCase()
  const alias: Record<string, string> = {
    local_done: 'local complete',
    local_start: 'local start',
    workers_selected: 'workers selected',
    waiting_remote: 'waiting on remote',
    remote_progress: 'remote status',
    remote_completed: 'remote complete',
    remote_failed: 'remote failed',
    tool_start: 'tool start',
    tool_call: 'tool result',
    tool_heartbeat: 'tool active',
    worker_heartbeat: 'worker active',
  }
  return alias[lower] || lower.replace(/_/g, ' ')
}

function sanitizeStepContent(value: unknown): string {
  const raw = typeof value === 'string' ? value : String(value ?? '')
  if (!raw) return ''
  return raw
    .replace(/\bundefined\b/gi, '')
    .replace(/\s+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function workerStatusMeta(worker: WorkerStatusSummary): string {
  const parts: string[] = []
  if (worker.startedAt) {
    const endTs = worker.finishedAt || (worker.status === 'running' ? Date.now() : undefined)
    parts.push(formatDurationFromTimestamps(worker.startedAt, endTs))
  }
  if (worker.status === 'running' && worker.lastHeartbeatAt) {
    const deltaMs = Math.max(0, Date.now() - worker.lastHeartbeatAt)
    if (deltaMs >= 1000) {
      parts.push(`hb ${formatDurationFromTimestamps(worker.lastHeartbeatAt)}`)
    }
  }
  if (typeof worker.stepCount === 'number' && worker.stepCount > 0) {
    parts.push(`${worker.stepCount} step${worker.stepCount === 1 ? '' : 's'}`)
  }
  return parts.join(' · ')
}

interface TaskProgressDisplay {
  phase: string
  percentage: number
  phaseLabel: string
  completedWorkers?: number
  totalWorkers?: number
  activeWorkers?: number
  currentAgent?: string
  currentAction?: string
  workerStatuses?: WorkerStatusSummary[]
}

interface MessageBubbleProps {
  role: 'user' | 'assistant' | 'system'
  content: string
  isStreaming?: boolean
  steps?: AgentStep[]
  taskProgress?: TaskProgressDisplay
  cost?: {
    inputTokens?: number
    outputTokens?: number
    totalTokens?: number
    duration?: number
  }
  attachments?: MessageAttachment[]
  onEditSubmit?: (newContent: string) => void
}

/**
 * Code block component with syntax highlighting, language label, and copy functionality.
 */
function CodeBlock({ language, children }: { language: string; children: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(children)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [children])

  return (
    <div className="relative group">
      {/* Language label */}
      {language && (
        <span className="code-lang-label">{language}</span>
      )}
      {/* Copy button */}
      <button
        onClick={handleCopy}
        className="absolute right-2 top-2 p-1.5 rounded bg-dark-hover/80 opacity-0 group-hover:opacity-100 transition-opacity z-10"
        title={copied ? 'Copied!' : 'Copy code'}
      >
        {copied ? (
          <CheckIcon className="w-4 h-4 text-green-400" />
        ) : (
          <ClipboardIcon className="w-4 h-4 text-gray-400" />
        )}
      </button>
      <SyntaxHighlighter
        style={oneDark}
        language={language || 'text'}
        PreTag="div"
        customStyle={{
          margin: 0,
          borderRadius: '0.5rem',
          padding: language ? '2rem 1rem 1rem' : '1rem',
          fontSize: '0.8125rem',
          lineHeight: '1.5',
        }}
      >
        {children}
      </SyntaxHighlighter>
    </div>
  )
}

/**
 * Action button with tooltip
 */
function ActionButton({ 
  icon, 
  label, 
  onClick 
}: { 
  icon: React.ReactNode
  label: string
  onClick: () => void 
}) {
  return (
    <button
      onClick={onClick}
      className="msg-action-btn action-btn"
      title={label}
    >
      {icon}
      <span className="action-tooltip">{label}</span>
    </button>
  )
}

/**
 * Typewriter text component for streaming effect
 * Displays text character by character with animation
 */
function TypewriterText({ 
  text, 
  isActive, 
  className,
  speed = 10 
}: { 
  text: string
  isActive: boolean
  className?: string
  speed?: number
}) {
  const [displayedText, setDisplayedText] = useState('')
  const [isTyping, setIsTyping] = useState(false)
  const prevTextRef = useRef('')
  
  useEffect(() => {
    // If text changed and we're active, animate the new part
    if (isActive && text !== prevTextRef.current) {
      if (!text.startsWith(prevTextRef.current)) {
        setDisplayedText('')
        prevTextRef.current = ''
      }
      const newContent = text.slice(prevTextRef.current.length)
      
      if (newContent.length > 0) {
        setIsTyping(true)
        let i = 0
        const timer = setInterval(() => {
          if (i < newContent.length) {
            setDisplayedText(prev => prev + newContent[i])
            i++
          } else {
            clearInterval(timer)
            setIsTyping(false)
          }
        }, speed)
        
        return () => clearInterval(timer)
      }
    } else if (!isActive) {
      // Not streaming - show full text immediately
      setDisplayedText(text)
    }
    
    prevTextRef.current = text
  }, [text, isActive, speed])
  
  // Reset when text is cleared
  useEffect(() => {
    if (!text) {
      setDisplayedText('')
      prevTextRef.current = ''
    }
  }, [text])
  
  return (
    <span className={className}>
      {displayedText}
      {isTyping && <span className="typing-cursor-inline">|</span>}
    </span>
  )
}

export default function MessageBubble({ role, content, isStreaming, steps, taskProgress, cost, attachments, onEditSubmit }: MessageBubbleProps) {
  const { getApiUrl } = useConfigStore()
  const isUser = role === 'user'
  // Track user's explicit collapse preference (null = use default behavior)
  const [userCollapsed, setUserCollapsed] = useState<boolean | null>(null)
  const [expandedDetailGroups, setExpandedDetailGroups] = useState<Record<string, boolean>>({})
  const [copied, setCopied] = useState(false)
  const [showFullOutput, setShowFullOutput] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [editContent, setEditContent] = useState('')

  const handleEditSubmit = () => {
    if (editContent.trim() && onEditSubmit) {
      onEditSubmit(editContent.trim())
      setIsEditing(false)
    }
  }
  const hasSteps = steps && steps.length > 0
  const stepsContainerRef = useRef<HTMLDivElement>(null)
  const prevStepsLengthRef = useRef(0)
  
  // Determine if steps should be shown:
  // - If user explicitly toggled, respect that
  // - Otherwise, show during streaming, collapse after
  const showSteps = userCollapsed !== null 
    ? !userCollapsed 
    : isStreaming
  
  // Smart auto-scroll for steps container: only scroll if user is near the bottom
  useEffect(() => {
    const container = stepsContainerRef.current
    if (!container || !steps || steps.length <= prevStepsLengthRef.current || !showSteps) {
      prevStepsLengthRef.current = steps?.length || 0
      return
    }
    // Only scroll if user is near the bottom of the steps container
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 80
    if (nearBottom) {
      container.scrollTop = container.scrollHeight
    }
    prevStepsLengthRef.current = steps?.length || 0
  }, [steps, showSteps])
  
  // Reset user preference when streaming stops (so next stream auto-expands)
  useEffect(() => {
    if (!isStreaming && userCollapsed === false) {
      // Keep expanded if user explicitly opened it, collapse if they didn't interact
    }
  }, [isStreaming, userCollapsed])
  
  const handleToggleSteps = useCallback(() => {
    setUserCollapsed(prev => {
      // If null (default), toggle based on current display
      if (prev === null) {
        return isStreaming ? true : false // Collapse if streaming, expand if not
      }
      return !prev // Toggle user preference
    })
  }, [isStreaming])

  const handleCopyMessage = useCallback(() => {
    navigator.clipboard.writeText(content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [content])

  return (
    <div className={clsx('flex gap-4 fade-in group', isUser && 'flex-row-reverse')}>
      {/* Avatar */}
      <div
        className={clsx(
          'w-8 h-8 rounded-lg flex items-center justify-center shrink-0',
          isUser
            ? 'bg-primary-600'
            : 'bg-gradient-to-br from-purple-500 to-pink-500'
        )}
      >
        {isUser ? (
          <UserIcon className="w-5 h-5 text-white" />
        ) : (
          <CpuChipIcon className="w-5 h-5 text-white" />
        )}
      </div>

      {/* Message Content */}
      <div
        className={clsx(
          'flex-1 max-w-[80%] rounded-2xl px-4 py-3',
          isUser
            ? 'bg-primary-600 text-white ml-auto'
            : 'bg-dark-surface border border-dark-border'
        )}
      >
        {isUser ? (
          <div>
            {isEditing ? (
              <div className="space-y-2">
                <textarea
                  autoFocus
                  value={editContent}
                  onChange={e => setEditContent(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleEditSubmit() }
                    if (e.key === 'Escape') setIsEditing(false)
                  }}
                  className="w-full bg-dark-bg border border-primary-500/50 rounded-lg px-3 py-2 text-gray-200 resize-none outline-none text-sm leading-6"
                  rows={Math.max(2, editContent.split('\n').length)}
                />
                <div className="flex justify-end gap-2">
                  <button onClick={() => setIsEditing(false)}
                    className="px-2 py-1 text-xs text-gray-400 hover:text-gray-200">Cancel</button>
                  <button onClick={handleEditSubmit}
                    className="px-3 py-1 text-xs bg-primary-600 hover:bg-primary-500 text-white rounded">Resend</button>
                </div>
              </div>
            ) : (
              <p className="whitespace-pre-wrap">{typeof content === 'string' ? content : String(content ?? '')}</p>
            )}
            {/* User message actions - show on hover */}
            {!isEditing && (
              <div className="flex justify-end gap-1 mt-2 opacity-0 group-hover:opacity-100 transition-opacity">
                {onEditSubmit && !isStreaming && (
                  <ActionButton
                    icon={<PencilIcon className="w-4 h-4" />}
                    label="Edit"
                    onClick={() => { setEditContent(content); setIsEditing(true) }}
                  />
                )}
                <ActionButton
                  icon={copied ? <CheckIcon className="w-4 h-4 text-green-400" /> : <ClipboardDocumentIcon className="w-4 h-4" />}
                  label={copied ? 'Copied!' : 'Copy'}
                  onClick={handleCopyMessage}
                />
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {/* Task progress bar — real-time phase/percentage when streaming */}
            {isStreaming && taskProgress && (
              <div className="mb-3 rounded-lg bg-dark-bg/60 border border-dark-border p-2">
                <div className="flex items-center justify-between text-xs mb-1">
                  <span className="text-gray-400 truncate">{taskProgress.phaseLabel || taskProgress.phase || 'Processing...'}</span>
                  <span className="text-gray-300 tabular-nums shrink-0">{taskProgress.percentage}%</span>
                </div>
                <div className="h-1.5 bg-dark-surface rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary-500 rounded-full transition-all duration-500"
                    style={{ width: `${Math.min(100, Math.max(0, taskProgress.percentage))}%` }}
                  />
                </div>
                {taskProgress.totalWorkers != null && taskProgress.totalWorkers > 0 && (
                  <p className="text-[10px] text-gray-500 mt-1">
                    {taskProgress.completedWorkers ?? 0}/{taskProgress.totalWorkers} workers completed
                    {typeof taskProgress.activeWorkers === 'number' ? ` · ${taskProgress.activeWorkers} active` : ''}
                  </p>
                )}
                {(taskProgress.currentAgent || taskProgress.currentAction) && (
                  <p className="text-[10px] text-gray-500 mt-1 truncate">
                    {taskProgress.currentAgent ? `Active: ${taskProgress.currentAgent}` : 'Active'}
                    {taskProgress.currentAction ? ` · ${humanizeStepAction(taskProgress.currentAction)}` : ''}
                  </p>
                )}
                {Array.isArray(taskProgress.workerStatuses) && taskProgress.workerStatuses.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {taskProgress.workerStatuses.slice(0, 6).map((worker) => {
                      const meta = workerStatusMeta(worker)
                      const statusClass =
                        worker.status === 'completed' ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/20' :
                        worker.status === 'running' ? 'bg-blue-500/15 text-blue-300 border-blue-500/20' :
                        worker.status === 'failed' || worker.status === 'timeout' ? 'bg-red-500/15 text-red-300 border-red-500/20' :
                        worker.status === 'skipped' ? 'bg-gray-500/15 text-gray-300 border-gray-500/20' :
                        'bg-amber-500/15 text-amber-300 border-amber-500/20'
                      return (
                        <div key={worker.name} className="flex items-center gap-2 text-[10px] min-w-0">
                          <span className={clsx('px-1.5 py-0.5 rounded border shrink-0 uppercase tracking-wide', statusClass)}>
                            {worker.status}
                          </span>
                          <span className="text-gray-300 truncate max-w-[160px]">{worker.name}</span>
                          <span className="text-gray-500 truncate">
                            {worker.detail || (worker.action ? humanizeStepAction(worker.action) : '')}
                            {meta ? ` · ${meta}` : ''}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )}
            {/* Agent Steps Toggle - Collapsible thinking section */}
            {hasSteps && (
              <div className="thinking-wrap mb-3">
                <button
                  onClick={handleToggleSteps}
                  className={clsx(
                    'thinking-toggle',
                    !showSteps && 'collapsed',
                    isStreaming && 'streaming'
                  )}
                >
                  {showSteps ? (
                    <ChevronDownIcon className="w-4 h-4 transition-transform" />
                  ) : (
                    <ChevronRightIcon className="w-4 h-4 transition-transform" />
                  )}
                  <span>
                    {isStreaming ? (
                      <span className="flex items-center gap-2">
                        <span className="thinking-dot-pulse" />
                        <span>Working... ({steps.length} steps)</span>
                      </span>
                    ) : (
                      `${steps.length} steps completed`
                    )}
                  </span>
                  {/* Agent count summary when collapsed */}
                  {!showSteps && (
                    <span className="text-[10px] text-gray-600 ml-2">
                      {[...new Set(steps.map(s => s.agent).filter(Boolean))].length} agents
                    </span>
                  )}
                </button>
                
                {/* Steps Display — grouped by agent with timeline */}
                {showSteps && (
                  <div 
                    ref={stepsContainerRef}
                    className={clsx(
                      'thinking-content mt-2 max-h-[400px] overflow-y-auto thin-scrollbar',
                      isStreaming && 'streaming'
                    )}
                  >
                    {/* Group steps into stable agent blocks so async updates append under one agent */}
                    {(() => {
                      // Build agent groups keyed by agent identity, preserving first-seen order.
                      type AgentGroup = { agent: string; steps: (AgentStep & { idx: number })[] }
                      const groups: AgentGroup[] = []
                      const groupIndexByAgent = new Map<string, number>()
                      steps.forEach((step, idx) => {
                        const agentKey = step.agent || 'Unknown'
                        const existingIndex = groupIndexByAgent.get(agentKey)
                        if (existingIndex != null) {
                          groups[existingIndex].steps.push({ ...step, idx })
                        } else {
                          groupIndexByAgent.set(agentKey, groups.length)
                          groups.push({ agent: agentKey, steps: [{ ...step, idx }] })
                        }
                      })

                      const activeGroupIndex = groups.findIndex(group =>
                        group.steps.some(step => step.idx === steps.length - 1)
                      )

                      return groups.map((group, gIdx) => {
                        const agentLower = group.agent.toLowerCase()
                        const agentColor = agentLower.includes('organizer') ? 'text-purple-400' :
                          agentLower.includes('router') ? 'text-indigo-400' :
                          agentLower.includes('coordinator') ? 'text-blue-400' :
                          agentLower.includes('worker') ? 'text-green-400' :
                          (agentLower.includes('node') || agentLower.includes('remote')) ? 'text-orange-400' :
                          'text-gray-400'
                        const agentBg = agentColor.replace('text-', 'bg-').replace('-400', '-500/10')
                        // Get initials
                        const initials = group.agent
                          .split(/[\s_-]+/)
                          .filter(Boolean)
                          .slice(0, 2)
                          .map(w => w[0]?.toUpperCase() || '')
                          .join('') || group.agent.substring(0, 2).toUpperCase()
                        const isLastGroup = gIdx === activeGroupIndex
                        const detailGroupKey = `${group.agent}-${gIdx}`
                        const isCurrentStep = (step: AgentStep & { idx: number }) =>
                          step.idx === steps.length - 1 && isStreaming && isLastGroup
                        const isDetailHeavyStep = (step: AgentStep & { idx: number }) => {
                          const actionLower = step.action?.toLowerCase() || ''
                          const contentLower = step.content.toLowerCase()
                          if (actionLower === 'tool_heartbeat' || actionLower === 'worker_heartbeat') return true
                          if (actionLower === 'tool_start' || actionLower === 'tool_call') return true
                          if (actionLower === 'tool_input_retry') return true
                          if (actionLower === 'remote_progress') return true
                          if (contentLower.includes('observation:')) return true
                          if (contentLower.length > 220) return true
                          return false
                        }
                        const visibleSteps = group.steps.filter(step => !isDetailHeavyStep(step) || isCurrentStep(step))
                        const hiddenDetailSteps = group.steps.filter(step => isDetailHeavyStep(step) && !isCurrentStep(step))
                        if (visibleSteps.length === 0 && hiddenDetailSteps.length > 0) {
                          visibleSteps.push(hiddenDetailSteps[hiddenDetailSteps.length - 1])
                          hiddenDetailSteps.pop()
                        }
                        const showDetailSteps = !!expandedDetailGroups[detailGroupKey]

                        return (
                          <div key={`grp-${gIdx}`} className="mb-3 last:mb-0">
                            {/* Agent header bar */}
                            <div className="flex items-center gap-2 mb-1.5">
                              <span className={clsx(
                                'inline-flex items-center justify-center w-5 h-5 rounded text-[10px] font-bold shrink-0',
                                agentBg, agentColor
                              )}>
                                {initials}
                              </span>
                              <span className={clsx('text-xs font-medium truncate max-w-[180px]', agentColor)}>
                                {group.agent}
                              </span>
                              <span className="text-[10px] text-gray-600">
                                {group.steps.length === 1 ? '1 step' : `${group.steps.length} steps`}
                              </span>
                              {/* Vertical line connector between groups */}
                            </div>
                            {/* Steps within this agent group */}
                            <div className="pl-3 ml-2.5 border-l border-dark-border space-y-1.5">
                              {visibleSteps.map(step => {
                      const contentLower = step.content.toLowerCase()
                      const actionLower = step.action?.toLowerCase() || ''
                      const isDelegation = contentLower.includes('delegating') || contentLower.includes('assigning') || actionLower.includes('delegat')
                      const isTool = actionLower.includes('tool') || contentLower.includes('using tool')
                      const isComplete = actionLower.includes('complete') || step.content.includes('✅')
                                const isRetry = actionLower === 'tool_input_retry'
                                const isPayment = actionLower.startsWith('payment_')
                                const isHandoff = actionLower === 'handoff'
                                const isAnStatusSummary = actionLower === 'an_status_summary'
                                const isLast = step.idx === steps.length - 1
                                const isCurrent = isLast && isStreaming && isLastGroup
                                const isFinished = !isCurrent

                                const pastTenseMap: Record<string, string> = {
                                  'analyzing': 'analyzed', 'routing': 'routed', 'planning': 'planned',
                                  'dispatching': 'dispatched', 'delegating': 'delegated', 'tool_call': 'tool used',
                                  'thinking': 'thought', 'processing': 'processed', 'executing': 'executed',
                                  'aggregating': 'aggregated', 'receiving': 'received', 'reviewing': 'reviewed',
                                  'completing': 'completed',
                                  'tool_input_retry': 'retried',
                                  'handoff': 'handed off',
                                  'payment_processing': 'payment processed',
                                  'payment_approved': 'payment approved',
                                  'payment_error': 'payment failed',
                                  'an_status_summary': 'AN summary',
                                }
                                const displayAction = isFinished && step.action
                                  ? (pastTenseMap[step.action.toLowerCase()] || humanizeStepAction(step.action))
                                  : humanizeStepAction(step.action)

                      const getStepIcon = () => {
                                  if (isRetry) return '↻'
                                  if (isPayment) return '💳'
                                  if (isHandoff) return '🔀'
                                  if (isFinished && isComplete) return '✅'
                                  if (isFinished) return '✓'
                        if (isDelegation) return '📤'
                        if (isTool) return '🔧'
                        return '▸'
                      }
                      
                                // Trim emoji prefixes and clean content for display.
                                // Exclude ASCII digits (0-9), # and * from the strip set
                                // because Unicode classifies them as Extended_Pictographic
                                // (they form keycap emoji sequences), but here they're real text.
                                const rawContent = sanitizeStepContent(step.content)
                                const cleanContent = rawContent
                                  .replace(/^(?:(?![0-9#*])[\p{Extended_Pictographic}]|[\uFE0F\u200D▸])+\s*/u, '')
                                  .replace(/^(Thought:\s*)/i, '')
                                  .trim()
                                const isLongContent = cleanContent.length > 200
                                const truncatedContent = isLongContent
                                  ? cleanContent.slice(0, 150) + '...'
                                  : cleanContent

                                // AN status summary: per-AN success/failure for user tracking
                                if (isAnStatusSummary) {
                                  return (
                                    <div key={step.id} className="pl-3 my-1">
                                      <div className="rounded-md border border-cyan-500/30 bg-cyan-500/5 px-3 py-2">
                                        <div className="flex items-center gap-2 mb-1">
                                          <span className="text-sm">📊</span>
                                          <span className="text-[11px] font-semibold uppercase tracking-wide text-cyan-300">
                                            AN Execution Summary
                                          </span>
                                        </div>
                                        <pre className="text-xs leading-relaxed text-gray-200/90 whitespace-pre-wrap font-sans">
                                          {cleanContent}
                                        </pre>
                                      </div>
                                    </div>
                                  )
                                }

                                // Handoff steps: render as a distinct notification block
                                if (isHandoff) {
                                  return (
                                    <div key={step.id} className="pl-3 my-1">
                                      <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2">
                                        <div className="flex items-center gap-2">
                                          <span className="text-sm">🔀</span>
                                          <span className="text-[11px] font-semibold uppercase tracking-wide text-amber-300">
                                            Sequential Handoff
                                          </span>
                                          <span className="text-[10px] px-1.5 py-0.5 rounded font-medium ml-auto bg-amber-500/20 text-amber-400">
                                            {displayAction}
                                          </span>
                                        </div>
                                        <p className="text-xs leading-relaxed text-amber-200/80 mt-1">
                                          {truncatedContent}
                                        </p>
                                      </div>
                                    </div>
                                  )
                                }

                                // Payment steps: render as a highlighted block
                                if (isPayment) {
                                  const isPayError = actionLower === 'payment_error'
                                  const isPayApproved = actionLower === 'payment_approved'
                                  const borderColor = isPayError ? 'border-red-500/40' : isPayApproved ? 'border-emerald-500/30' : 'border-purple-500/30'
                                  const bgColor = isPayError ? 'bg-red-500/10' : isPayApproved ? 'bg-emerald-500/10' : 'bg-purple-500/10'
                                  const titleColor = isPayError ? 'text-red-300' : 'text-purple-300'
                                  const textColor = isPayError ? 'text-red-200/80' : isPayApproved ? 'text-emerald-200/80' : 'text-purple-200/80'
                                  const badgeClass = isPayError
                                    ? 'bg-red-500/20 text-red-400'
                                    : isPayApproved
                                      ? 'bg-emerald-500/20 text-emerald-400'
                                      : 'bg-purple-500/20 text-purple-400'
                      
                      return (
                        <div key={step.id} className={clsx(
                                      'pl-3 my-1',
                                      isCurrent && 'animate-pulse',
                        )}>
                                      <div className={clsx('rounded-md border px-3 py-2', borderColor, bgColor)}>
                          <div className="flex items-center gap-2 mb-1">
                                          <span className="text-sm">{isPayError ? '❌' : '💳'}</span>
                                          <span className={clsx('text-[11px] font-semibold uppercase tracking-wide', titleColor)}>
                                            x402 Payment
                                          </span>
                                          <span className={clsx('text-[10px] px-1.5 py-0.5 rounded font-medium ml-auto', badgeClass)}>
                                            {displayAction}
                                          </span>
                                        </div>
                                        <p className={clsx('text-xs leading-relaxed', textColor)}>
                                          {truncatedContent}
                                        </p>
                                      </div>
                                    </div>
                                  )
                                }

                                return (
                                  <div key={step.id} className={clsx(
                                    'pl-3',
                                    isCurrent && 'animate-pulse',
                                    isRetry && 'opacity-40'
                                  )}>
                                    {/* Step line: icon + action tag + inline content */}
                                    <div className="flex items-start gap-1.5">
                            <span className={clsx(
                                        'text-xs shrink-0 mt-0.5',
                                        isRetry ? 'text-yellow-600' :
                                        isFinished ? 'text-gray-600' : 'text-gray-400'
                            )}>
                              {getStepIcon()}
                            </span>
                                      <div className="flex-1 min-w-0">
                                        {displayAction && displayAction !== 'thinking' && (
                                          <span className={clsx(
                                            'text-[10px] px-1 py-0.5 rounded font-medium mr-1.5 shrink-0',
                                            isRetry ? 'bg-yellow-500/10 text-yellow-600' :
                                            isFinished ? 'bg-dark-hover text-gray-600' :
                                            isDelegation ? 'bg-blue-500/20 text-blue-400' :
                                            isTool ? 'bg-green-500/20 text-green-400' :
                                            isComplete ? 'bg-emerald-500/20 text-emerald-400' :
                                            'bg-dark-hover text-gray-500'
                                          )}>
                                            {displayAction}
                              </span>
                            )}
                                        {isLongContent && isFinished ? (
                                          <details className="group">
                                            <summary className={clsx(
                                              'text-xs leading-relaxed cursor-pointer select-none',
                                              isRetry ? 'text-yellow-700' : 'text-gray-600'
                                            )}>
                                              {truncatedContent}
                                            </summary>
                                            <pre className="text-xs text-gray-500 whitespace-pre-wrap break-words mt-1 pl-2 border-l border-dark-border max-h-48 overflow-y-auto">
                                              {cleanContent}
                                            </pre>
                                          </details>
                                        ) : (
                                          <span className={clsx(
                                            'text-xs leading-relaxed whitespace-pre-wrap break-words inline-block min-w-0 align-top',
                                            isRetry ? 'text-yellow-700' :
                                            isFinished ? 'text-gray-600' : 'text-gray-400'
                                          )}>
                                            {isCurrent ? (
                                              <TypewriterText text={truncatedContent} isActive={true} speed={10} />
                                            ) : (
                                              truncatedContent
                                            )}
                                          </span>
                                        )}
                              </div>
                          </div>
                        </div>
                      )
                    })}
                              {hiddenDetailSteps.length > 0 && (
                                <div className="pt-1">
                                  <button
                                    onClick={() => setExpandedDetailGroups((prev) => ({
                                      ...prev,
                                      [detailGroupKey]: !prev[detailGroupKey],
                                    }))}
                                    className="text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
                                  >
                                    {showDetailSteps ? 'Hide' : 'Show'} {hiddenDetailSteps.length} detailed update{hiddenDetailSteps.length === 1 ? '' : 's'}
                                  </button>
                                  {showDetailSteps && (
                                    <div className="mt-1 pl-3 border-l border-dark-border/80 space-y-1">
                                      {hiddenDetailSteps.map((step) => {
                                        const rawContent = sanitizeStepContent(step.content)
                                        const compactContent = rawContent.length > 180
                                          ? `${rawContent.slice(0, 180)}...`
                                          : rawContent
                                        const displayAction = humanizeStepAction(step.action)
                                        return (
                                          <div key={`detail-${step.id}`} className="flex items-start gap-1.5 text-[10px] text-gray-500">
                                            <span className="shrink-0 text-gray-600">•</span>
                                            <div className="min-w-0">
                                              {displayAction && (
                                                <span className="mr-1 rounded bg-dark-hover px-1 py-0.5 text-[9px] text-gray-500">
                                                  {displayAction}
                                                </span>
                                              )}
                                              <span className="whitespace-pre-wrap break-words">{compactContent}</span>
                                            </div>
                                          </div>
                                        )
                                      })}
                                    </div>
                                  )}
                                </div>
                              )}
                            </div>
                          </div>
                        )
                      })
                    })()}
                    {/* Typing indicator at the end when streaming */}
                    {isStreaming && (
                      <div className="flex items-center gap-2 text-purple-400/60 text-xs mt-2 pl-7">
                        <div className="typing-dots">
                          <span></span>
                          <span></span>
                          <span></span>
                        </div>
                        <span>Agent is working...</span>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Task Result Card -- richer display when message has task metadata */}
            {cost && (cost.totalTokens || cost.duration) && !isStreaming && (
              <div className="rounded-lg border border-dark-border bg-dark-bg/50 p-3 mb-2">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-500/20 text-green-400 font-medium">
                      completed
                    </span>
                    {cost.duration ? (
                      <span className="text-[10px] text-gray-500">
                        {formatDurationSecs(cost.duration)}
                      </span>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-2 text-[10px] text-gray-500">
                    {cost.totalTokens ? (
                      <span>
                        <span className="text-blue-400">{formatTokenCount(cost.inputTokens || 0)}</span>
                        <span className="text-gray-600"> → </span>
                        <span className="text-green-400">{formatTokenCount(cost.outputTokens || 0)}</span>
                        <span> tokens</span>
                      </span>
                    ) : null}
                  </div>
                </div>
                {/* Agent pipeline summary */}
                {hasSteps && (
                  <div className="flex items-center gap-1 text-[10px] text-gray-500 flex-wrap">
                    {(() => {
                      const agentNames = [...new Set(steps.map(s => s.agent).filter(Boolean))]
                      // Detect execution mode from step content (ANRouter decision step)
                      const modeStep = steps.find(s =>
                        s.action?.toLowerCase().includes('decision') &&
                        (s.content?.toLowerCase().includes('mode') || s.content?.toLowerCase().includes('parallel') || s.content?.toLowerCase().includes('sequential'))
                      )
                      const modeContent = modeStep?.content?.toLowerCase() || ''
                      const execMode = modeContent.includes('parallel') ? 'parallel' : modeContent.includes('sequential') ? 'sequential' : null
                      return (
                        <>
                          {agentNames.map((name, i) => {
                            const nl = name?.toLowerCase() || ''
                            const isOrganizer = nl.includes('organizer')
                            const isRouter = nl.includes('router') || nl.includes('anrouter')
                            const isCoordinator = nl.includes('coordinator')
                            const isRemote = nl.includes('remote') || nl.includes('agentic node') || nl.includes('node (')
                            const isWorker = !isOrganizer && !isRouter && !isCoordinator && !isRemote
                            return (
                              <span key={name} className="flex items-center gap-1">
                                {i > 0 && <span className="text-gray-600">→</span>}
                                <span className={clsx(
                                  'px-1 py-0.5 rounded',
                                  isOrganizer && 'bg-purple-500/10 text-purple-400',
                                  isRouter && 'bg-indigo-500/10 text-indigo-400',
                                  isCoordinator && 'bg-blue-500/10 text-blue-400',
                                  isRemote && 'bg-emerald-500/10 text-emerald-400',
                                  isWorker && 'bg-green-500/10 text-green-400',
                                )}>{name}</span>
                              </span>
                            )
                          })}
                          {execMode && (
                            <span className="px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400 ml-1">
                              {execMode === 'parallel' ? '⫘ parallel' : '⟹ sequential'}
                            </span>
                          )}
                        </>
                      )
                    })()}
                  </div>
                )}
              </div>
            )}

            {/* Main Content - Markdown with syntax highlighting */}
            {(() => {
              const isTaskResult = !isStreaming && content && cost && (cost.totalTokens || cost.duration)
              const markdownContent = (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    p: ({ children }) => <p>{safeChildren(children)}</p>,
                    pre: ({ children }) => <>{children}</>,
                    img: ({ src, alt }) => {
                      let resolvedSrc = src || ''
                      if (resolvedSrc.startsWith('/api') && getApiUrl) {
                        resolvedSrc = getApiUrl(resolvedSrc)
                      } else if (resolvedSrc && !resolvedSrc.includes('/') && !resolvedSrc.startsWith('http') && attachments?.length) {
                        const match = attachments.find(a => (a.filename || '').toLowerCase() === resolvedSrc.toLowerCase())
                        if (match?.url) resolvedSrc = match.url
                      }
                      return <img src={resolvedSrc} alt={alt || ''} className="max-w-full rounded-lg border border-dark-border" />
                    },
                    code: ({ className, children, ...props }) => {
                      const match = /language-(\w+)/.exec(className || '')
                      const codeString = String(children).replace(/\n$/, '')
                      if (match) {
                        return <CodeBlock language={match[1]}>{codeString}</CodeBlock>
                      }
                      return (
                        <code className="bg-dark-bg px-1.5 py-0.5 rounded text-primary-400" {...props}>
                          {safeChildren(children)}
                        </code>
                      )
                    },
                    a: ({ children, href }) => (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-primary-400 hover:text-primary-300 underline"
                      >
                        {safeChildren(children)}
                      </a>
                    ),
                    table: ({ children }) => (
                      <div className="overflow-x-auto">
                        <table className="min-w-full border-collapse border border-dark-border">
                          {children}
                        </table>
                      </div>
                    ),
                    th: ({ children }) => (
                      <th className="border border-dark-border bg-dark-bg px-3 py-2 text-left text-sm font-medium">
                        {children}
                      </th>
                    ),
                    td: ({ children }) => (
                      <td className="border border-dark-border px-3 py-2 text-sm">
                        {children}
                      </td>
                    ),
                    blockquote: ({ children }) => (
                      <blockquote className="border-l-4 border-primary-500/50 pl-4 italic text-gray-400">
                        {children}
                      </blockquote>
                    ),
                    ul: ({ children }) => (
                      <ul className="list-disc list-inside space-y-1">
                        {children}
                      </ul>
                    ),
                    ol: ({ children }) => (
                      <ol className="list-decimal list-inside space-y-1">
                        {children}
                      </ol>
                    ),
                  }}
                >
                  {typeof content === 'string' ? content : String(content ?? '')}
                </ReactMarkdown>
              )

              if (isTaskResult) {
                const OUTPUT_PREVIEW_LIMIT = 3000
                const isLong = content.length > OUTPUT_PREVIEW_LIMIT
                const previewContent = isLong ? content.slice(0, OUTPUT_PREVIEW_LIMIT) + '\n\n...' : content
                const previewMarkdown = (
                  <ReactMarkdown
                    components={{
                      p: ({ children }) => <p>{safeChildren(children)}</p>,
                      code: ({ className, children }) => {
                        const lang = className?.replace('language-', '') || ''
                        const code = String(children).replace(/\n$/, '')
                        return lang ? <CodeBlock language={lang}>{code}</CodeBlock> : <code className="bg-dark-bg/50 px-1 rounded text-sm">{safeChildren(children)}</code>
                      },
                    }}
                  >
                    {showFullOutput ? content : previewContent}
                  </ReactMarkdown>
                )
                return (
                  <div className="rounded-lg border border-emerald-500/30 bg-gradient-to-b from-emerald-500/5 to-transparent overflow-hidden">
                    <div className="flex items-center justify-between px-4 py-2 border-b border-emerald-500/20 bg-emerald-500/10">
                      <span className="text-emerald-400 text-xs font-semibold tracking-wide uppercase">Final Output</span>
                      {isLong && (
                        <button
                          onClick={() => setShowFullOutput(!showFullOutput)}
                          className="text-[10px] text-emerald-400/70 hover:text-emerald-300 transition-colors"
                        >
                          {showFullOutput ? '▲ Collapse' : '▼ Show full output'}
                        </button>
                      )}
                    </div>
                    <div className={clsx(
                      'px-4 py-3 prose prose-invert prose-sm max-w-none',
                      !showFullOutput && isLong && 'max-h-[400px] overflow-y-auto'
                    )}>
                      {previewMarkdown}
                    </div>
                  </div>
                )
              }

              return (
                <div className={clsx('prose prose-invert prose-sm max-w-none', isStreaming && !content && 'typing-cursor')}>
                  {content ? markdownContent : <span className="text-gray-400">Thinking...</span>}
                  {isStreaming && content && <span className="typing-cursor" />}
                </div>
              )
            })()}

            {/* Multimodal attachments (files, images) */}
            {attachments && attachments.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {attachments.map((a, i) => {
                  if (!a.url) return null
                  return a.type === 'image' ? (
                    <a
                      key={i}
                      href={a.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block rounded-lg overflow-hidden border border-dark-border hover:border-primary-500/50 transition-colors max-w-xs"
                    >
                      <img src={a.url} alt={a.filename || 'attachment'} className="max-h-48 object-contain" />
                      <div className="px-2 py-1 text-[10px] text-gray-500 truncate">{a.filename}</div>
                    </a>
                  ) : (
                    <a
                      key={i}
                      href={a.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-dark-surface border border-dark-border hover:border-primary-500/30 transition-colors text-sm"
                    >
                      <DocumentIcon className="w-4 h-4 text-gray-400" />
                      <span className="text-gray-300">{a.filename || 'File'}</span>
                      {a.language && <span className="text-[10px] text-gray-500">({a.language})</span>}
                    </a>
                  )
                })}
              </div>
            )}

            {/* Actions and Cost Info */}
            {!isStreaming && content && (
              <div className="flex items-center justify-between gap-4 pt-2 border-t border-dark-border/50">
                {/* Action buttons */}
                <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                  <ActionButton
                    icon={copied ? <CheckIcon className="w-4 h-4 text-green-400" /> : <ClipboardDocumentIcon className="w-4 h-4" />}
                    label={copied ? 'Copied!' : 'Copy'}
                    onClick={handleCopyMessage}
                  />
                </div>
                
                {/* Token usage and duration */}
                {(cost?.totalTokens || cost?.duration) && (
                  <div className="flex items-center gap-3 text-xs text-gray-500">
                    {cost.totalTokens ? (
                      <span title={`${cost.inputTokens || 0} input + ${cost.outputTokens || 0} output = ${cost.totalTokens} total tokens`}>
                        <span className="text-blue-400">{formatTokenCount(cost.inputTokens || 0)}</span>
                        <span className="text-gray-600"> → </span>
                        <span className="text-green-400">{formatTokenCount(cost.outputTokens || 0)}</span>
                        <span className="text-gray-500"> tokens</span>
                      </span>
                    ) : null}
                    {cost.duration ? (
                      <span className="text-gray-400" title={`${formatNumberNoTrailingZeros(cost.duration, 2)} seconds`}>
                        {formatDurationSecs(cost.duration)}
                      </span>
                    ) : null}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
