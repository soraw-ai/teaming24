import { useRef, useEffect, useState, useCallback, useMemo } from 'react'
import {
  PaperAirplaneIcon,
  StopIcon,
  ChevronDownIcon,
  BoltIcon,
  ChatBubbleLeftRightIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { fetchAgentMemoryStatus, type AgentMemoryStatus } from '../api/memory'
import { buildSessionContextMessages, useChatStore, type PendingApproval } from '../store/chatStore'
import { useConfigStore } from '../store/configStore'
import { useAgentStore, type TaskPhase, type WorkerStatusSummary } from '../store/agentStore'
import { useWalletStore } from '../store/walletStore'
import { getPaymentTokenSymbol } from '../config/payment'
import { notify } from '../store/notificationStore'
import { debugLog, debugWarn } from '../utils/debug'
import { ORGANIZER_ID, COORDINATOR_ID } from '../utils/ids'
import AgentMemoryIndicator from './AgentMemoryIndicator'
import MessageBubble from './MessageBubble'
import EmptyState from './EmptyState'

// Dot pulse loading component
function DotPulse() {
  return (
    <div className="dot-pulse text-primary-400">
      <span></span>
      <span></span>
      <span></span>
    </div>
  )
}

function sanitizeStepText(value: unknown): string {
  const raw = typeof value === 'string' ? value : String(value ?? '')
  if (!raw) return ''
  return raw
    .replace(/\bundefined\b/gi, '')
    .replace(/\s+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function filterWorkerStatusesBySelected(
  workerStatuses: WorkerStatusSummary[],
  selectedKeys: Set<string>,
  hasWorkerSelection: boolean,
): WorkerStatusSummary[] {
  if (!hasWorkerSelection) return workerStatuses
  return workerStatuses.filter((w) => selectedKeys.has(w.name))
}

function filterStepsBySelected<T extends { agent?: string }>(
  steps: T[] | undefined,
  selectedKeys: Set<string>,
  hasWorkerSelection: boolean,
): T[] | undefined {
  if (!steps?.length) return steps
  if (!hasWorkerSelection) return steps
  return steps.filter((step) => {
    const agent = step.agent ?? ''
    const namePart = agent.split(' (')[0]?.trim() ?? ''
    const typePart = agent.match(/\((\w+)\)/)?.[1] ?? ''
    if (['organizer', 'router', 'coordinator'].includes(typePart)) return true
    return selectedKeys.has(namePart) || selectedKeys.has(agent)
  })
}

function normalizeWorkerStatuses(value: unknown): WorkerStatusSummary[] {
  if (!Array.isArray(value)) return []
  const allowed = new Set<WorkerStatusSummary['status']>(['pending', 'running', 'completed', 'failed', 'skipped', 'timeout'])
  const toMs = (input: unknown) => (typeof input === 'number' && Number.isFinite(input) && input > 0 ? (input > 1e12 ? input : input * 1000) : 0)
  return value
    .map((raw) => {
      if (!raw || typeof raw !== 'object') return null
      const item = raw as Record<string, unknown>
      const name = String(item.name || '').trim()
      if (!name) return null
      const rawStatus = String(item.status || 'pending').toLowerCase() as WorkerStatusSummary['status']
      return {
        name,
        status: allowed.has(rawStatus) ? rawStatus : 'pending',
        action: typeof item.action === 'string' ? item.action : '',
        detail: sanitizeStepText(item.detail),
        tool: typeof item.tool === 'string' ? item.tool : '',
        stepCount: typeof item.step_count === 'number'
          ? item.step_count
          : (typeof item.stepCount === 'number' ? item.stepCount : 0),
        updatedAt: typeof item.updated_at === 'number'
          ? toMs(item.updated_at)
          : toMs(item.updatedAt),
        startedAt: typeof item.started_at === 'number'
          ? toMs(item.started_at)
          : toMs(item.startedAt),
        lastHeartbeatAt: typeof item.last_heartbeat_at === 'number'
          ? toMs(item.last_heartbeat_at)
          : toMs(item.lastHeartbeatAt),
        finishedAt: typeof item.finished_at === 'number'
          ? toMs(item.finished_at)
          : toMs(item.finishedAt),
        error: typeof item.error === 'string' ? item.error : '',
        order: typeof item.order === 'number' ? item.order : 0,
      }
    })
    .filter(Boolean) as WorkerStatusSummary[]
}

export function ApprovalCard({
  approval,
  onResolve,
}: {
  approval: PendingApproval
  onResolve: (decision: string, budget?: number) => void | Promise<void>
}) {
  const [budgetInput, setBudgetInput] = useState('')
  const allowBudget = approval.metadata?.allow_budget === true

  return (
    <div className="max-w-lg mx-auto my-4 animate-in fade-in slide-in-from-bottom-2">
      <div className="rounded-lg border border-amber-500/40 bg-gradient-to-b from-amber-500/10 to-dark-card overflow-hidden shadow-lg">
        <div className="px-4 py-3 border-b border-amber-500/20 bg-amber-500/10">
          <div className="flex items-center gap-2">
            <span className="text-lg">⏸</span>
            <span className="text-amber-300 font-semibold text-sm">{approval.title}</span>
          </div>
        </div>
        <div className="px-4 py-3">
          <pre className="text-xs text-gray-300 whitespace-pre-wrap font-mono bg-dark-bg/50 rounded p-2 mb-3 max-h-32 overflow-y-auto">
            {approval.description}
          </pre>
          {allowBudget && (
            <div className="mb-3">
              <label className="text-xs text-gray-400 block mb-1">
                Budget ({getPaymentTokenSymbol()}) — auto-approve future dispatches within limit
              </label>
              <input
                type="number"
                min="0"
                step="0.001"
                placeholder="e.g. 10"
                value={budgetInput}
                onChange={(e) => setBudgetInput(e.target.value)}
                className="w-full px-2 py-1.5 rounded bg-dark-bg text-gray-200 text-xs border border-dark-border focus:border-amber-500/50 focus:outline-none"
              />
            </div>
          )}
          <div className="flex items-center gap-2 justify-end">
            {(approval.options || [{ id: 'approve', label: 'Approve', style: 'primary' }, { id: 'deny', label: 'Deny', style: 'danger' }]).map(opt => (
              <button
                key={opt.id}
                onClick={async () => {
                  const budget = allowBudget && budgetInput ? parseFloat(budgetInput) : undefined
                  await onResolve(opt.id, budget)
                }}
                className={clsx(
                  'px-3 py-1.5 rounded text-xs font-medium transition-colors',
                  opt.style === 'primary' && 'bg-emerald-600 hover:bg-emerald-500 text-white',
                  opt.style === 'secondary' && 'bg-dark-hover hover:bg-dark-border text-gray-300',
                  opt.style === 'danger' && 'bg-red-600/80 hover:bg-red-500 text-white',
                  !opt.style && 'bg-dark-hover hover:bg-dark-border text-gray-300',
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

export default function ChatView() {
  const [input, setInput] = useState('')
  const [chatMode, setChatMode] = useState<'agent' | 'chat'>('agent')
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  const [memoryStatus, setMemoryStatus] = useState<AgentMemoryStatus | null>(null)
  const [memoryNotice, setMemoryNotice] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const messagesContainerRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const abortControllerRef = useRef<AbortController | null>(null)
  const unmountedRef = useRef(false)
  const taskCompletedRef = useRef(false)
  const memoryNoticeTimerRef = useRef<number | null>(null)
  const lastCompactedAtRef = useRef(0)

  // Abort SSE on unmount — task continues in backend, but frontend stops listening
  useEffect(() => {
    unmountedRef.current = false
    return () => {
      unmountedRef.current = true
      if (memoryNoticeTimerRef.current) {
        window.clearTimeout(memoryNoticeTimerRef.current)
        memoryNoticeTimerRef.current = null
      }
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
        abortControllerRef.current = null
      }
    }
  }, [])
  
  const {
    sessions,
    activeSessionId,
    isStreaming,
    createSession,
    addMessage,
    updateMessage,
    updateMessageMeta,
    addStepToMessage,
    setStreaming,
    setCurrentTaskId,
    setSessionTask,
    setSessionApproval,
    syncSessionToDB,
    syncMessageToDB,
    loadSessionsFromDB,
    truncateMessagesFrom,
  } = useChatStore()

  const { getApiUrl } = useConfigStore()
  const currentTaskId = useChatStore(s => s.currentTaskId)
  const currentTask = useAgentStore(s =>
    currentTaskId ? s.tasks.find(t => t.id === currentTaskId) : null
  )
  const agents = useAgentStore(s => s.agents)
  const taskProgress = currentTask?.progress

  // Build set of selected agent names/ids — only show these in chat (hide unselected workers)
  const selectedAgentKeys = useMemo(() => {
    const keys = new Set<string>()
    const delegated = currentTask?.delegatedAgents ?? []
    const executing = currentTask?.executingAgents ?? []
    for (const id of [...delegated, ...executing]) {
      keys.add(id)
      const a = agents.find(ag => ag.id === id)
      if (a?.name) keys.add(a.name)
    }
    // Always show organizer, router, coordinator
    keys.add(ORGANIZER_ID)
    keys.add(COORDINATOR_ID)
    keys.add('Organizer')
    keys.add('organizer')
    keys.add('ANRouter')
    keys.add('router')
    keys.add('Coordinator')
    keys.add('coordinator')
    return keys
  }, [currentTask?.delegatedAgents, currentTask?.executingAgents, agents])

  const hasWorkerSelection =
    (currentTask?.delegatedAgents?.length ?? 0) > 0 ||
    (currentTask?.executingAgents?.length ?? 0) > 0

  // Poll task status when running — improves round 2+ state update timing
  const updateTaskProgress = useAgentStore(s => s.updateTaskProgress)
  const updateTask = useAgentStore(s => s.updateTask)
  useEffect(() => {
    if (!currentTaskId || !getApiUrl) return
    const status = currentTask?.status
    if (status !== 'running' && status !== 'delegated') return

    const poll = async () => {
      try {
        const resp = await fetch(getApiUrl(`/api/agent/tasks/${currentTaskId}/status`))
        if (!resp.ok) return
        const data = await resp.json()
        if (data.status === 'not_found') return
        const prog = data.progress
        if (prog && typeof prog === 'object') {
          updateTaskProgress(currentTaskId, {
            phase: prog.phase,
            percentage: prog.percentage ?? 0,
            totalWorkers: prog.total_workers ?? prog.totalWorkers ?? 0,
            completedWorkers: prog.completed_workers ?? prog.completedWorkers ?? 0,
            activeWorkers: prog.active_workers ?? prog.activeWorkers ?? 0,
            skippedWorkers: prog.skipped_workers ?? prog.skippedWorkers ?? 0,
            phaseLabel: prog.phase_label ?? prog.phaseLabel ?? '',
            currentAgent: prog.current_agent ?? prog.currentAgent ?? '',
            currentAction: prog.current_action ?? prog.currentAction ?? '',
            workerStatuses: normalizeWorkerStatuses(prog.worker_statuses ?? prog.workerStatuses ?? []),
          })
        }
        if (data.status && data.status !== status) {
          updateTask(currentTaskId, { status: data.status })
        }
      } catch {
        // ignore
      }
    }

    const t = setInterval(poll, 2000)
    poll()
    return () => clearInterval(t)
  }, [currentTaskId, currentTask?.status, getApiUrl, updateTaskProgress, updateTask])
  const effectiveMemoryAgentId = (() => {
    const raw = currentTask?.metadata?.memory_agent_id
    if (typeof raw === 'string' && raw.trim()) {
      return raw.trim()
    }
    return ORGANIZER_ID
  })()
  const memoryRefreshIntervalMs = (
    isStreaming || ['pending', 'running', 'delegated'].includes(String(currentTask?.status || ''))
  ) ? 2500 : 10000

  const activeSession = sessions.find((s) => s.id === activeSessionId)
  const messages = activeSession?.messages || []
  const pendingApproval = activeSession?.pendingApproval || null
  const setPendingApproval = useCallback((approval: PendingApproval | null) => {
    if (activeSessionId) setSessionApproval(activeSessionId, approval)
  }, [activeSessionId, setSessionApproval])

  // Load persisted sessions from database on mount
  useEffect(() => {
    loadSessionsFromDB()
  }, [loadSessionsFromDB])

  const refreshMemoryStatus = useCallback((delayMs = 0) => {
    const run = async () => {
      try {
        const nextStatus = await fetchAgentMemoryStatus(getApiUrl, {
          agentId: effectiveMemoryAgentId,
          sessionId: activeSessionId,
        })
        setMemoryStatus(nextStatus)

        const previousCompactedAt = lastCompactedAtRef.current
        if (nextStatus.lastCompactedAt > 0) {
          lastCompactedAtRef.current = nextStatus.lastCompactedAt
        }
        if (nextStatus.isCompacting || nextStatus.lastCompactedAt > previousCompactedAt) {
          setMemoryNotice('Automatically compacting context')
          if (memoryNoticeTimerRef.current) {
            window.clearTimeout(memoryNoticeTimerRef.current)
          }
          memoryNoticeTimerRef.current = window.setTimeout(() => {
            setMemoryNotice('')
            memoryNoticeTimerRef.current = null
          }, nextStatus.isCompacting ? 5000 : 3500)
        } else if (!nextStatus.isCompacting && !nextStatus.recentlyCompacted) {
          setMemoryNotice('')
        }
      } catch (error) {
        debugWarn('[ChatView] Failed to refresh memory status:', error)
      }
    }

    if (delayMs > 0) {
      window.setTimeout(() => {
        void run()
      }, delayMs)
      return
    }
    void run()
  }, [activeSessionId, effectiveMemoryAgentId, getApiUrl])

  useEffect(() => {
    refreshMemoryStatus()
    const intervalId = window.setInterval(() => {
      refreshMemoryStatus()
    }, memoryRefreshIntervalMs)
    return () => window.clearInterval(intervalId)
  }, [memoryRefreshIntervalMs, refreshMemoryStatus])

  // Auto-reconnect to in-progress tasks on mount / page reload.
  // Primary: use persisted activeTaskId. Fallback: find last task message and ask backend.
  const reconnectAttempted = useRef(false)
  useEffect(() => {
    if (reconnectAttempted.current || isStreaming) return
    if (!activeSession) return

    const { activeTaskId, activeMessageId } = activeSession

    // Primary path: persisted task IDs
    if (activeTaskId && activeMessageId) {
      reconnectAttempted.current = true
      reconnectToTask(activeSession.id, activeTaskId, activeMessageId)
      return () => { reconnectAttempted.current = false }
    }

    // Fallback: find most recent assistant message with a taskId and check backend
    const lastTaskMsg = [...(activeSession.messages || [])]
      .reverse()
      .find(m => m.role === 'assistant' && m.taskId)
    const fallbackTaskId = lastTaskMsg?.taskId
    if (fallbackTaskId) {
      reconnectAttempted.current = true
      ;(async () => {
        try {
          const resp = await fetch(getApiUrl(`/agent/tasks/${fallbackTaskId}/status`))
          if (resp.ok) {
            const data = await resp.json()
            const status = String(data.status || data.task?.status || '').toLowerCase()
            const reconnectable = new Set(['pending', 'running', 'delegated'])
            if (reconnectable.has(status)) {
              setSessionTask(activeSession.id, fallbackTaskId, lastTaskMsg!.id)
              reconnectToTask(activeSession.id, fallbackTaskId, lastTaskMsg!.id)
            }
          }
        } catch {
          // Backend unreachable — no reconnect
        }
      })()
      return () => { reconnectAttempted.current = false }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSession?.id, activeSession?.activeTaskId])

  // Smart auto-scroll: only follow output when user is near the bottom.
  // This allows users to scroll up and read history during streaming
  // without being yanked back to the bottom on every update.
  const userScrolledUp = useRef(false)
  const scrollRafId = useRef<number | null>(null)

  const isNearBottom = useCallback(() => {
    const container = messagesContainerRef.current
    if (!container) return true
    const threshold = 150 // px from bottom
    return container.scrollHeight - container.scrollTop - container.clientHeight < threshold
  }, [])

  const scrollToBottom = useCallback(() => {
    // Debounce via rAF — at most one scroll per animation frame
    if (scrollRafId.current) return
    scrollRafId.current = requestAnimationFrame(() => {
      scrollRafId.current = null
      const container = messagesContainerRef.current
      if (container) {
        // Use instant scroll to avoid jittery "smooth" fighting
        container.scrollTop = container.scrollHeight
      }
    })
  }, [])

  // Track user scroll intent and show/hide scroll button
  useEffect(() => {
    const container = messagesContainerRef.current
    if (!container) return

    const handleScroll = () => {
      const nearBottom = isNearBottom()
      userScrolledUp.current = !nearBottom
      setShowScrollBtn(!nearBottom)
    }

    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => container.removeEventListener('scroll', handleScroll)
  }, [isNearBottom])

  // Auto-scroll only when the user has NOT scrolled up.
  // During streaming the user can freely scroll up; we will not pull them back.
  useEffect(() => {
    if (!userScrolledUp.current) {
      scrollToBottom()
    }
  }, [messages, scrollToBottom])

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 200) + 'px'
    }
  }, [input])

  const handleSend = async (overrideContent?: string, overrideMode?: 'agent' | 'chat') => {
    const messageContent = overrideContent !== undefined ? overrideContent : input.trim()
    if (!messageContent || isStreaming) return
    setPendingApproval(null)

    // Create session if none exists
    let sessionId = activeSessionId
    if (!sessionId) {
      createSession()
      sessionId = useChatStore.getState().activeSessionId
    }

    if (!sessionId) return

    const userMessage = messageContent
    const currentMode = overrideMode ?? chatMode

    // Add user message
    addMessage(sessionId, {
      role: 'user',
      content: userMessage,
    })
    if (overrideContent === undefined) {
      setInput('')
      // Reset textarea height
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto'
      }
    }

    // Start streaming response
    setStreaming(true)
    taskCompletedRef.current = false
    abortControllerRef.current = new AbortController()
    
    try {
      // Add placeholder for assistant message.
      // isTask starts false — upgraded to true when task_started arrives.
      const assistantMessageId = addMessage(sessionId, {
        role: 'assistant',
        content: '',
        isTask: false,
      })

      const currentSession = useChatStore.getState().sessions.find((s) => s.id === sessionId)

      const apiUrl = getApiUrl('/chat/agent')
      
      const requestPayload = {
        session_id: sessionId,
        messages: buildSessionContextMessages(currentSession, { excludeMessageId: assistantMessageId }),
        mode: currentMode,
      }
      const controller = abortControllerRef.current
      if (!controller) throw new Error('request controller missing')
      const doRequest = () => fetch(apiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestPayload),
        signal: controller.signal,
      })

      let response = await doRequest()
      if (!response.ok && response.status >= 500) {
        await new Promise(resolve => setTimeout(resolve, 500))
        response = await doRequest()
      }

      if (!response.ok) {
        let serverMessage = `HTTP ${response.status}`
        try {
          const errJson = await response.json()
          serverMessage = errJson?.detail || errJson?.error || serverMessage
        } catch (e) {
          console.debug('Response not JSON, trying text:', e);
          try {
            const errText = await response.text()
            if (errText) serverMessage = errText.slice(0, 200)
          } catch (e2) { console.warn('Failed to parse error text:', e2); }
        }
        throw new Error(serverMessage)
      }

      await consumeSSEStream(response, sessionId, assistantMessageId)
    } catch (error) {
      handleStreamError(error as Error, sessionId)
    } finally {
      finalizeStream(sessionId)
    }
  }

  /**
   * Shared SSE event consumer — used by both handleSend (new task)
   * and reconnectToTask (returning to an in-progress task).
   */
  const consumeSSEStream = async (
    response: Response,
    sessionId: string,
    assistantMessageId: string,
  ) => {
    const reader = response.body?.getReader()
    const decoder = new TextDecoder()
    let fullContent = ''
    let buffer = ''
    let streamFlushTimer: ReturnType<typeof setTimeout> | null = null

    if (!reader) return

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        // Guard against runaway buffer from malformed / non-terminating streams.
        if (buffer.length > 2_000_000) {
          debugWarn('[ChatView] SSE buffer overflow (>2 MB), closing stream')
          reader.cancel()
          break
        }
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') continue
          
          try {
            const parsed = JSON.parse(data)
            
            if (parsed.type === 'task_started') {
              setCurrentTaskId(parsed.task_id)
              setSessionTask(sessionId, parsed.task_id, assistantMessageId)
              updateMessageMeta(sessionId, assistantMessageId, { taskId: parsed.task_id, isTask: true })
              updateMessage(sessionId, assistantMessageId, '🤔 Analyzing request...')
              
            } else if (parsed.type === 'approval_request') {
              // Human-in-the-loop: agent is waiting for user decision
              // Use sessionId from stream (not activeSessionId) — user may have switched sessions
              const approval = parsed.approval ?? parsed
              if (!approval?.id) {
                debugWarn('[ChatView] approval_request received with missing id, ignoring')
              } else {
              setSessionApproval(sessionId, {
                id: approval.id,
                taskId: approval.task_id ?? '',
                title: approval.title || 'Approval needed',
                description: approval.description || '',
                options: approval.options || [
                  { id: 'approve', label: 'Approve', style: 'primary' },
                  { id: 'deny', label: 'Deny', style: 'danger' },
                ],
                type: approval.type,
                metadata: approval.metadata,
              })
              addStepToMessage(sessionId, assistantMessageId, {
                agent: 'Organizer (organizer)',
                action: 'approval_request',
                content: `⏸ ${approval.title ?? 'Approval needed'}`,
              })
              }

            } else if (parsed.type === 'approval_resolved') {
              setSessionApproval(sessionId, null)

            } else if (parsed.type === 'heartbeat') {
              const currentMsgs = useChatStore.getState().sessions.find(s => s.id === sessionId)?.messages || []
              const currentMsg = currentMsgs.find(m => m.id === assistantMessageId)
              if (!fullContent && (!currentMsg?.steps || currentMsg.steps.length === 0)) {
                const dots = '.'.repeat((parsed.count % 3) + 1)
                const msg = (parsed.message ?? 'Processing your request') + dots
                updateMessage(sessionId, assistantMessageId, msg)
              }
              
            } else if (parsed.type === 'step') {
              const agentName = parsed.agent || 'Organizer'
              const agentType = parsed.agent_type || 'organizer'
              const action = parsed.action || 'thinking'
              const actionLower = String(action).toLowerCase()
              const isDelegation = parsed.is_delegation
              const isHeartbeatStep = actionLower === 'tool_heartbeat' || actionLower === 'worker_heartbeat'
              
              const thought = (parsed.thought != null && parsed.thought !== '') ? String(parsed.thought) : ''
              const content = (parsed.content != null && parsed.content !== '') ? sanitizeStepText(parsed.content) : ''
              let stepContent = ''
              
              if (thought) {
                stepContent = `💭 ${thought}`
                if (content && content !== thought && !thought.includes(content)) {
                  stepContent += `\n📝 ${content}`
                }
              } else {
                stepContent = content
              }
              
              const obs = parsed.observation
              if (obs != null && obs !== '' && String(obs) !== 'undefined') {
                stepContent += `\n👁️ Observation: ${sanitizeStepText(obs)}`
              }
              
              if (parsed.token_usage) {
                const usage = parsed.token_usage
                if (typeof usage === 'object') {
                  const total = usage.total_tokens || (usage.prompt_tokens || 0) + (usage.completion_tokens || 0)
                  if (total > 0) {
                    stepContent += `\n📊 Tokens: ${total}`
                  }
                }
              }
              
              let phasePrefix = ''
              if (isDelegation) {
                if (agentType === 'organizer') {
                  phasePrefix = '📤 Delegating to local team coordinator...\n'
                } else if (agentType === 'coordinator') {
                  phasePrefix = '📋 Assigning to Worker...\n'
                }
              } else if (action.toLowerCase().includes('tool') || parsed.uses_sandbox) {
                phasePrefix = '🔧 Using tool...\n'
              }
              
              const renderedStepContent = sanitizeStepText(phasePrefix + stepContent)
              if (renderedStepContent && !isHeartbeatStep) {
                addStepToMessage(sessionId, assistantMessageId, {
                  agent: `${agentName} (${agentType})`,
                  action: action,
                  content: renderedStepContent,
                })
              }

              // Update task progress for progress bar (from SSE step payload)
              const prog = parsed.progress as Record<string, unknown> | undefined
              if (prog && (parsed.task_id || useChatStore.getState().currentTaskId)) {
                const tid = (parsed.task_id || useChatStore.getState().currentTaskId) as string
                useAgentStore.getState().updateTaskProgress(tid, {
                  phase: ((prog.phase as string) || 'executing') as TaskPhase,
                  percentage: (prog.percentage as number) ?? 0,
                  totalWorkers: (prog.total_workers as number) ?? 0,
                  completedWorkers: (prog.completed_workers as number) ?? 0,
                  activeWorkers: (prog.active_workers as number) ?? 0,
                  skippedWorkers: (prog.skipped_workers as number) ?? 0,
                  phaseLabel: (prog.phase_label as string) || '',
                  currentAgent: (prog.current_agent as string) || '',
                  currentAction: (prog.current_action as string) || '',
                  workerStatuses: normalizeWorkerStatuses((prog.worker_statuses as unknown) ?? (prog.workerStatuses as unknown)),
                })
              }

              // Refresh wallet store when any payment step arrives
              if (action.startsWith('payment_')) {
                const walletState = useWalletStore.getState()
                walletState.fetchBalance()
                walletState.fetchTransactions()
              }
              
              if (!fullContent) {
                updateMessage(sessionId, assistantMessageId, '')
              }
              
            } else if (parsed.type === 'workflow') {
              const wfAgent = parsed.agent || 'System'
              const wfType = parsed.agent_type || 'organizer'
              const wfAction = parsed.action || 'workflow'
              const wfContent = sanitizeStepText(parsed.content || '')
              if (wfContent) {
                addStepToMessage(sessionId, assistantMessageId, {
                  agent: `${wfAgent} (${wfType})`,
                  action: wfAction,
                  content: sanitizeStepText(`🔄 ${wfContent}`),
                })
              }

              // Update task progress for workflow steps
              const wfProg = parsed.progress as Record<string, unknown> | undefined
              if (wfProg && (parsed.task_id || useChatStore.getState().currentTaskId)) {
                const tid = (parsed.task_id || useChatStore.getState().currentTaskId) as string
                useAgentStore.getState().updateTaskProgress(tid, {
                  phase: ((wfProg.phase as string) || 'executing') as TaskPhase,
                  percentage: (wfProg.percentage as number) ?? 0,
                  totalWorkers: (wfProg.total_workers as number) ?? 0,
                  completedWorkers: (wfProg.completed_workers as number) ?? 0,
                  activeWorkers: (wfProg.active_workers as number) ?? 0,
                  skippedWorkers: (wfProg.skipped_workers as number) ?? 0,
                  phaseLabel: (wfProg.phase_label as string) || '',
                  currentAgent: (wfProg.current_agent as string) || '',
                  currentAction: (wfProg.current_action as string) || '',
                  workerStatuses: normalizeWorkerStatuses((wfProg.worker_statuses as unknown) ?? (wfProg.workerStatuses as unknown)),
                })
              }
              
              if (!fullContent) {
                updateMessage(sessionId, assistantMessageId, '')
              }
              
            } else if (parsed.type === 'stream_start') {
              fullContent = ''
              updateMessage(sessionId, assistantMessageId, '')
              
            } else if (parsed.type === 'stream') {
              fullContent += parsed.content || ''
              if (!streamFlushTimer) {
                streamFlushTimer = window.setTimeout(() => {
                  streamFlushTimer = null
                  updateMessage(sessionId, assistantMessageId, fullContent)
                }, 80)
              }
              
            } else if (parsed.type === 'output_saved') {
              const output = parsed.output
              if (output?.files?.length > 0) {
                const outputInfo = [
                  '\n\n---',
                  '📁 **Files saved to:** `' + output.output_dir + '`',
                  '',
                  ...output.files.map((f: any) => {
                    let info = `• \`${f.filename}\` (${f.language})`
                    if (f.run_command) {
                      info += `\n  Run: \`${f.run_command}\``
                    }
                    return info
                  }),
                ].join('\n')
                fullContent += outputInfo
                updateMessage(sessionId, assistantMessageId, fullContent)
                // Store multimodal attachments for inline rendering
                const tid = useChatStore.getState().currentTaskId || parsed.task_id
                const attachments = (output.files || []).map((f: any) => ({
                  type: /\.(png|jpg|jpeg|gif|webp|svg)$/i.test(f.filename || '') ? 'image' as const : 'file' as const,
                  filename: f.filename || '',
                  filepath: f.filepath,
                  language: f.language,
                  runCommand: f.run_command,
                  url: tid ? getApiUrl(`/api/agent/outputs/${tid}/file?name=${encodeURIComponent(f.filename || '')}`) : undefined,
                }))
                updateMessageMeta(sessionId, assistantMessageId, { attachments })
              }
              
            } else if (parsed.type === 'result') {
              taskCompletedRef.current = true
              if (streamFlushTimer) {
                clearTimeout(streamFlushTimer)
                streamFlushTimer = null
              }
              if (parsed.content) {
                fullContent = parsed.content
                updateMessage(sessionId, assistantMessageId, fullContent)
              }
              const meta: any = {
                cost: {
                  inputTokens: parsed.cost?.input_tokens || 0,
                  outputTokens: parsed.cost?.output_tokens || 0,
                  totalTokens: parsed.cost?.total_tokens || 0,
                  duration: parsed.duration,
                  costUsd: parsed.cost?.cost_usd,
                  x402Payment: parsed.cost?.x402_payment,
                },
              }
              if (parsed.output?.output_dir) {
                meta.output = {
                  dir: parsed.output.output_dir,
                  files: parsed.output.files || [],
                }
                const files = parsed.output.files || []
                const tid = useChatStore.getState().currentTaskId || parsed.task_id
                meta.attachments = files.map((f: any) => ({
                  type: /\.(png|jpg|jpeg|gif|webp|svg)$/i.test(f.filename || '') ? 'image' as const : 'file' as const,
                  filename: f.filename || '',
                  filepath: f.filepath,
                  language: f.language,
                  runCommand: f.run_command,
                  url: tid ? getApiUrl(`/api/agent/outputs/${tid}/file?name=${encodeURIComponent(f.filename || '')}`) : undefined,
                }))
              }
              updateMessageMeta(sessionId, assistantMessageId, meta)
              
            } else if (parsed.type === 'cancelled') {
              taskCompletedRef.current = true
              setSessionApproval(sessionId, null)
              const cancelMsg = parsed.error || 'Task cancelled by user'
              updateMessage(sessionId, assistantMessageId,
                (fullContent || '') + (fullContent ? '\n\n' : '') + `_${cancelMsg}_`)

            } else if (parsed.type === 'error') {
              taskCompletedRef.current = true
              setSessionApproval(sessionId, null)
              const errMsg = parsed.error || 'Unknown error'
              updateMessage(sessionId, assistantMessageId, `❌ Error: ${errMsg}`)
              notify.error('Task Failed', errMsg.length > 120 ? errMsg.slice(0, 120) + '…' : errMsg)

            } else if (parsed.type === 'reconnect_done') {
              debugLog('[ChatView] Reconnect stream complete, status:', parsed.status)
              if (parsed.status === 'completed' || parsed.status === 'failed' || parsed.status === 'cancelled') {
                taskCompletedRef.current = true
                setSessionApproval(sessionId, null)
              }
              
            } else if (parsed.content) {
              fullContent += parsed.content
              updateMessage(sessionId, assistantMessageId, fullContent)
            }
          } catch (parseErr) {
            debugWarn('[ChatView] Failed to parse SSE chunk:', parseErr)
          }
        }
      }
    } finally {
      if (streamFlushTimer) {
        clearTimeout(streamFlushTimer)
        streamFlushTimer = null
      }
      if (fullContent) {
        updateMessage(sessionId, assistantMessageId, fullContent)
      }
    }
  }

  /**
   * Reconnect to a running or recently-completed task.
   * Called on mount when the active session has an in-progress task.
   */
  const reconnectToTask = useCallback(async (
    sessionId: string,
    taskId: string,
    messageId: string,
  ) => {
    debugLog('[ChatView] Reconnecting to task', taskId)
    setStreaming(true)
    setCurrentTaskId(taskId)
    abortControllerRef.current = new AbortController()

    try {
      // Check for any pending approvals from before reconnect
      try {
        const approvalResp = await fetch(getApiUrl(`/agent/tasks/${taskId}/approvals/pending`))
        if (approvalResp.ok) {
          const approvalData = await approvalResp.json()
          const pending = approvalData.approvals?.[0]
          if (pending) {
            setSessionApproval(sessionId, {
              id: pending.id,
              taskId: pending.task_id ?? '',
              title: pending.title || 'Approval needed',
              description: pending.description || '',
              options: pending.options || [
                { id: 'approve', label: 'Approve', style: 'primary' },
                { id: 'deny', label: 'Deny', style: 'danger' },
              ],
              type: pending.type,
              metadata: pending.metadata,
            })
          }
        }
      } catch { /* non-critical */ }

      const response = await fetch(
        getApiUrl(`/chat/tasks/${taskId}/reconnect`),
        { signal: abortControllerRef.current.signal },
      )

      if (!response.ok) {
        debugWarn('[ChatView] Reconnect failed:', response.status)
        return
      }

      await consumeSSEStream(response, sessionId, messageId)
    } catch (error) {
      if ((error as Error).name !== 'AbortError' && !unmountedRef.current) {
        debugWarn('[ChatView] Reconnect error:', error)
      }
    } finally {
      finalizeStream(sessionId)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [getApiUrl])

  const handleStreamError = (error: Error, sessionId: string) => {
    const isAbort = error.name === 'AbortError'
    const isUnmount = unmountedRef.current
    const isNetworkError = error.name === 'TypeError' || /network|fetch|failed to fetch/i.test(error.message || '')

    if (isAbort || isUnmount || isNetworkError) {
      debugLog(
        isUnmount ? 'Component unmounted — SSE paused, will reconnect on return'
          : isNetworkError ? 'Network error (likely page refresh) — task continues on backend'
          : 'Request aborted',
      )
      if (isAbort && !isUnmount && !isNetworkError) {
        const currentMessages = useChatStore.getState().sessions.find(s => s.id === sessionId)?.messages || []
        const lastMessage = currentMessages[currentMessages.length - 1]
        if (lastMessage && lastMessage.role === 'assistant') {
          updateMessage(sessionId, lastMessage.id,
            lastMessage.content || 'Request interrupted (manual stop). You can resend the message to continue.')
        }
      }
      taskCompletedRef.current = false
      return
    }
    console.error('Chat error:', error)
    const currentMessages = useChatStore.getState().sessions.find(s => s.id === sessionId)?.messages || []
    const lastMessage = currentMessages[currentMessages.length - 1]
    if (lastMessage && lastMessage.role === 'assistant' && !lastMessage.content) {
      const errText = error?.message || 'unknown error'
      updateMessage(
        sessionId,
        lastMessage.id,
        `Sorry, request failed: ${errText}. Please try again.`
      )
      notify.error('Request Failed', errText.length > 100 ? errText.slice(0, 100) + '…' : errText)
    }
  }

  const finalizeStream = (sessionId: string) => {
    setStreaming(false)
    const finishedTaskId = useChatStore.getState().currentTaskId
    const taskDidComplete = taskCompletedRef.current

    if (taskDidComplete) {
      setCurrentTaskId(null)
      setSessionTask(sessionId, null, null)
      setSessionApproval(sessionId, null)
    }
    abortControllerRef.current = null
    refreshMemoryStatus()
    refreshMemoryStatus(1500)
    refreshMemoryStatus(3500)

    if (finishedTaskId) {
      setTimeout(() => {
        try {
          const state = useAgentStore.getState()
          const task = state.tasks.find(t => t.id === finishedTaskId)
          if (task && (task.status === 'completed' || task.status === 'failed')) {
            state.agents.forEach(agent => {
              if (agent.status === 'busy' && agent.currentTask === finishedTaskId) {
                const rs = (agent.type === 'organizer' || agent.type === 'coordinator') ? 'online' : 'idle'
                state.updateAgent(agent.id, { status: rs, currentTask: undefined })
              }
            })
          }
        } catch (e) {
          console.warn('Non-critical agent store update failed:', e)
        }
      }, 3000)
    }
    
    if (sessionId) {
      const finalMessages = useChatStore.getState().sessions.find(s => s.id === sessionId)?.messages || []
      syncSessionToDB(sessionId)
      const newMessagesCount = finalMessages.length
      if (newMessagesCount >= 2) {
        const userMsg = finalMessages[newMessagesCount - 2]
        const assistantMsg = finalMessages[newMessagesCount - 1]
        if (userMsg?.role === 'user') {
          syncMessageToDB(sessionId, userMsg)
        }
        if (assistantMsg?.role === 'assistant') {
          syncMessageToDB(sessionId, assistantMsg)
        }
      }
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleStop = async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
    setStreaming(false)
    const taskId = useChatStore.getState().currentTaskId
    if (taskId) {
      try {
        await fetch(getApiUrl(`/agent/tasks/${taskId}/cancel`), { method: 'POST' })
      } catch { /* non-critical */ }
    }
    setCurrentTaskId(null)
    if (activeSessionId) setSessionTask(activeSessionId, null, null)
  }

  const handleEditAndResend = async (userMessageId: string, newContent: string) => {
    // 1. Abort SSE stream
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    // 2. Cancel backend task if running
    const taskId = useChatStore.getState().currentTaskId
    if (taskId) {
      try {
        await fetch(getApiUrl(`/agent/tasks/${taskId}/cancel`), { method: 'POST' })
      } catch { /* non-critical */ }
      setCurrentTaskId(null)
      if (activeSessionId) setSessionTask(activeSessionId, null, null)
      setStreaming(false)
    }
    // 3. Remove edited message + everything after it from session
    if (activeSessionId) {
      truncateMessagesFrom(activeSessionId, userMessageId)
    }
    // 4. Resend with new content
    await handleSend(newContent)
  }

  return (
    <div className="flex flex-col h-full">
      {/* Messages Container */}
      <div className="flex-1 overflow-hidden relative">
        <div 
          ref={messagesContainerRef}
          className="h-full overflow-y-auto thin-scrollbar"
        >
          {messages.length === 0 ? (
            <EmptyState
              onSuggestionSelect={(prompt) => {
                void handleSend(prompt, 'agent')
              }}
            />
          ) : (
            <div className="max-w-4xl mx-auto px-4 py-6 space-y-6">
              {messages.map((message, index) => (
                <MessageBubble
                  key={message.id}
                  role={message.role}
                  content={message.content}
                  isStreaming={isStreaming && index === messages.length - 1 && message.role === 'assistant'}
                  steps={filterStepsBySelected(message.steps, selectedAgentKeys, hasWorkerSelection)}
                  cost={message.cost}
                  attachments={message.attachments}
                  taskProgress={
                    isStreaming && index === messages.length - 1 && message.role === 'assistant' && taskProgress
                      ? {
                          phase: taskProgress.phase,
                          percentage: taskProgress.percentage ?? 0,
                          phaseLabel: taskProgress.phaseLabel ?? '',
                          completedWorkers: taskProgress.completedWorkers ?? 0,
                          totalWorkers: taskProgress.totalWorkers ?? 0,
                          activeWorkers: taskProgress.activeWorkers ?? 0,
                          currentAgent: taskProgress.currentAgent ?? '',
                          currentAction: taskProgress.currentAction ?? '',
                          workerStatuses: filterWorkerStatusesBySelected(
                            taskProgress.workerStatuses ?? [],
                            selectedAgentKeys,
                            hasWorkerSelection,
                          ),
                        }
                      : undefined
                  }
                  onEditSubmit={
                    message.role === 'user'
                      ? (newContent) => handleEditAndResend(message.id, newContent)
                      : undefined
                  }
                />
              ))}
              {/* Loading indicator when waiting for first response */}
              {isStreaming && messages.length > 0 && 
               messages[messages.length - 1].role === 'assistant' && 
               !messages[messages.length - 1].content && (
                <div className="flex items-center gap-3 text-gray-400 py-2">
                  <DotPulse />
                  <span className="text-sm">Thinking...</span>
                </div>
              )}
              {/* Human-in-the-loop approval card */}
              {pendingApproval && (
                <ApprovalCard
                  approval={pendingApproval}
                  onResolve={async (decision: string, budget?: number) => {
                    if (pendingApproval?.id) {
                      try {
                        const body: { decision: string; budget?: number } = { decision }
                        if (budget != null && budget > 0) body.budget = budget
                        await fetch(getApiUrl(`/agent/approvals/${pendingApproval.id}/resolve`), {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify(body),
                        })
                      } catch (e) {
                        console.error('Failed to resolve approval:', e)
                      }
                    }
                    setPendingApproval(null)
                  }}
                />
              )}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>
        
        {/* Scroll to bottom button */}
        <button
          onClick={() => { userScrolledUp.current = false; scrollToBottom() }}
          className={`scroll-bottom-btn ${showScrollBtn ? 'visible' : ''}`}
          title="Scroll to bottom"
        >
          <ChevronDownIcon className="w-5 h-5 text-gray-400" />
        </button>
      </div>

      {/* Input Area */}
      <div className="border-t border-dark-border bg-dark-surface/50 backdrop-blur p-4">
        <div className="max-w-4xl mx-auto">
          {/* Mode Chips */}
          <div className="flex items-center justify-between gap-3 mb-2">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setChatMode('agent')}
                disabled={isStreaming}
                className={clsx(
                  'flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium transition-all duration-150 disabled:opacity-50',
                  chatMode === 'agent'
                    ? 'bg-primary-600 text-white'
                    : 'bg-dark-hover text-gray-400 hover:text-gray-300',
                )}
                title="Run with multi-agent framework"
              >
                <BoltIcon className="w-3.5 h-3.5" />
                Agent
              </button>
              <button
                onClick={() => setChatMode('chat')}
                disabled={isStreaming}
                className={clsx(
                  'flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium transition-all duration-150 disabled:opacity-50',
                  chatMode === 'chat'
                    ? 'bg-dark-border text-gray-200'
                    : 'bg-dark-hover text-gray-400 hover:text-gray-300',
                )}
                title="Direct LLM chat — no agents"
              >
                <ChatBubbleLeftRightIcon className="w-3.5 h-3.5" />
                Chat
              </button>
            </div>
            <div className="flex items-center gap-3">
              {memoryNotice && (
                <span className="text-[11px] font-medium text-amber-300">
                  {memoryNotice}
                </span>
              )}
              <AgentMemoryIndicator status={memoryStatus} />
            </div>
          </div>

          {/* Input Box */}
          <div className="flex items-end gap-3 bg-dark-bg border border-dark-border rounded-2xl px-4 py-3 transition-colors focus-within:border-primary-500/50">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={chatMode === 'agent' ? 'Describe a task for agents to execute...' : 'Ask a question...'}
              className="flex-1 bg-transparent resize-none outline-none text-gray-200 placeholder-gray-500 max-h-[200px] leading-6"
              rows={1}
              disabled={isStreaming}
            />
            
            {isStreaming ? (
              <button
                onClick={handleStop}
                className="p-2.5 bg-gray-800 hover:bg-red-600 text-white rounded-lg transition-all duration-200 group"
                title="Stop generating"
              >
                <StopIcon className="w-5 h-5 group-hover:scale-110 transition-transform" />
              </button>
            ) : (
              <button
                onClick={() => handleSend()}
                disabled={!input.trim()}
                className="p-2.5 text-white rounded-lg transition-all duration-200 disabled:bg-gray-800/50 disabled:cursor-not-allowed bg-gray-800 hover:bg-primary-600"
                title="Send"
              >
                <PaperAirplaneIcon className="w-5 h-5" />
              </button>
            )}
          </div>
          
          <p className="text-xs text-gray-500 text-center mt-2">
            Press Enter to send, Shift+Enter for new line
          </p>
        </div>
      </div>
    </div>
  )
}
