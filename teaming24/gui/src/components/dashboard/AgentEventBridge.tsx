import { useEffect, useRef } from 'react'
import { useAgentStore } from '../../store/agentStore'
import type { TaskPhase, WorkerStatusSummary } from '../../store/agentStore'
import { useChatStore } from '../../store/chatStore'
import { useWalletStore } from '../../store/walletStore'
import { notify } from '../../store/notificationStore'
import { getApiBase, getApiBaseAbsolute } from '../../utils/api'
import { ORGANIZER_ID, COORDINATOR_ID, SYSTEM_ID } from '../../utils/ids'
import { toMilliseconds } from '../../utils/date'
import { formatNumberNoTrailingZeros } from '../../utils/format'
import { getPaymentTokenSymbol } from '../../config/payment'
import { debugLog, debugWarn } from '../../utils/debug'

/**
 * AgentEventBridge - Connects to backend SSE for real-time agent/task updates
 * 
 * Events handled:
 * - agents_init: Initial list of agents
 * - tasks_init: Initial list of tasks
 * - task_created: New task created
 * - task_started: Task started executing
 * - task_step: Agent performed a step
 * - task_completed: Task finished
 * - task_failed: Task failed
 */
export default function AgentEventBridge() {
  const eventSourceRef = useRef<EventSource | null>(null)
  const isFirstOpen = useRef(true)

  const toMs = toMilliseconds

  const normalizePhase = (phase?: string): TaskPhase => {
    if (!phase) return 'received'
    // Backward compatibility for older payload vocabulary.
    if (phase === 'planning') return 'routing'
    if (phase === 'delegating') return 'dispatching'
    if (phase === 'received' || phase === 'routing' || phase === 'dispatching' || phase === 'executing' || phase === 'aggregating' || phase === 'completed') {
      return phase
    }
    return 'received'
  }

  const resolveAgentId = (candidateId?: string, candidateName?: string) => {
    const state = useAgentStore.getState()
    if (candidateId && state.agents.some(a => a.id === candidateId)) return candidateId
    if (candidateName) {
      const byName = state.agents.find(a => a.name === candidateName)
      if (byName) return byName.id
    }
    return candidateId || candidateName || ORGANIZER_ID
  }

  const sanitizeStepText = (value: unknown): string => {
    const raw = typeof value === 'string' ? value : String(value ?? '')
    if (!raw) return ''
    return raw
      .replace(/\bundefined\b/gi, '')
      .replace(/\s+\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim()
  }

  const normalizeWorkerStatuses = (value: unknown): WorkerStatusSummary[] => {
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

  const mapServerProgress = (progress: Record<string, any> | undefined) => {
    if (!progress) return null
    return {
      phase: normalizePhase(progress.phase || 'received'),
      percentage: progress.percentage ?? 0,
      totalWorkers: progress.total_workers ?? 0,
      completedWorkers: progress.completed_workers ?? 0,
      activeWorkers: progress.active_workers ?? 0,
      skippedWorkers: progress.skipped_workers ?? 0,
      phaseLabel: progress.phase_label || progress.phase || 'Unknown',
      currentAgent: progress.current_agent || '',
      currentAction: progress.current_action || '',
      workerStatuses: normalizeWorkerStatuses(progress.worker_statuses ?? progress.workerStatuses),
    }
  }
  
  const addAgent = useAgentStore(s => s.addAgent)
  const updateAgent = useAgentStore(s => s.updateAgent)
  const createTask = useAgentStore(s => s.createTask)
  const updateTask = useAgentStore(s => s.updateTask)
  const addTaskStep = useAgentStore(s => s.addTaskStep)
  const updateTaskProgress = useAgentStore(s => s.updateTaskProgress)
  const addMessage = useAgentStore(s => s.addMessage)
  const addLog = useAgentStore(s => s.addLog)
  const addUnreadSandbox = useAgentStore(s => s.addUnreadSandbox)
  const setDashboardApproval = useAgentStore(s => s.setDashboardApproval)

  useEffect(() => {
    const apiBase = getApiBase()
    const url = `${apiBase}/api/agent/events`
    debugLog('[AgentEventBridge] Connecting to:', url)

    // Reset on each effect run so the very first onopen of this connection is correctly identified
    isFirstOpen.current = true

    const es = new EventSource(url)
    eventSourceRef.current = es

    const fetchSnapshot = async () => {
      try {
        const apiBase = getApiBaseAbsolute()
        const res = await fetch(`${apiBase}/api/state/snapshot`)
        if (!res.ok) return
        const snapshot = await res.json()
        useAgentStore.getState().applySnapshot(snapshot)
        debugLog('[AgentEventBridge] Snapshot applied after reconnect, event_seq=', snapshot.event_seq)
      } catch (e) {
        debugLog('[AgentEventBridge] Snapshot fetch failed:', e)
      }
    }

    es.onopen = () => {
      debugLog('[AgentEventBridge] SSE connection opened')
      if (!isFirstOpen.current) {
        // Reconnect: SSE buffer handles short gaps; snapshot reconciles long disconnects
        addLog({ level: 'info', message: 'SSE reconnected — applying state snapshot' })
        fetchSnapshot()
      } else {
        addLog({ level: 'info', message: 'SSE connection to agent events established' })
      }
      isFirstOpen.current = false
    }

    es.onerror = (err) => {
      console.error('[AgentEventBridge] SSE error:', err)
      addLog({ level: 'warn', message: 'SSE connection error - will auto-reconnect' })
    }

    es.onmessage = (evt) => {
      try {
        const rawData = JSON.parse(evt.data)
        
        // Debug: Log all events
        if (rawData.type !== 'keepalive') {
          debugLog('[AgentEventBridge] Event received:', rawData.type, rawData)
        }
        
        // Handle both formats:
        // 1. Direct: {type, task, ...}  (from task_manager listener)
        // 2. Wrapped: {type, data: {task}, timestamp} (from subscription_manager)
        // Handle both formats:
        // 1. Direct:  {type, task, agent_id, ...}       (from task_manager listener)
        // 2. Wrapped: {type, data:{task, agent_id}, ts}  (from subscription_manager)
        // Detect wrapping only when data is an object AND contains task or approval.
        const isWrapped =
          rawData.data !== null &&
          typeof rawData.data === 'object' &&
          !Array.isArray(rawData.data) &&
          ('task' in rawData.data || 'approval' in rawData.data)
        const data = isWrapped
          ? {
              ...rawData,
              task: rawData.data.task,
              approval: rawData.data.approval,
              agent_id: rawData.data.agent_id,
              agent_type: rawData.data.agent_type,
              is_delegation: rawData.data.is_delegation,
              pool_members: rawData.data.pool_members,
            }
          : rawData
        
        switch (data.type) {
          case 'agents_init':
            // Initialize agents from backend - pass id to prevent duplicates
            data.agents?.forEach((agent: any) => {
              addAgent({
                id: agent.id,  // Use backend-assigned ID (organizer-1, coordinator-1, worker-N)
                name: agent.name,
                type: agent.type,
                status: agent.status || 'online',
                model: agent.model,
                capabilities: agent.capabilities || [],
                goal: agent.goal || '',
                backstory: agent.backstory || '',
              })
            })
            addLog({
              level: 'info',
              message: `Agent pool initialized: ${data.agents?.length || 0} agents (${data.agents?.filter((a: any) => a.type === 'organizer').length || 0} organizer, ${data.agents?.filter((a: any) => a.type === 'coordinator').length || 0} coordinator, ${data.agents?.filter((a: any) => a.type === 'worker').length || 0} workers)`,
            })
            // Generate initial system messages for agent messages panel
            if (data.agents?.length > 0) {
              addMessage({
                fromAgent: SYSTEM_ID,
                toAgent: ORGANIZER_ID,
                type: 'event',
                content: `Agent pool online: ${data.agents.map((a: any) => a.name).join(', ')}`,
              })
            }
            break
            
          case 'tasks_init':
            // Initialize tasks from backend SSE — restore full state including
            // executing/delegated agents so topology & progress are accurate.
            debugLog('[AgentEventBridge] tasks_init received:', data.tasks?.length || 0, 'tasks')
            data.tasks?.forEach((task: any) => {
              const isRemote = task.metadata?.remote === true
              const taskLabel = task.name || task.prompt?.substring(0, 50) || task.description?.substring(0, 50) || 'Task'
              createTask({
                id: task.id,
                name: (isRemote ? '📡 ' : '') + taskLabel,
                description: task.prompt || task.description || '',
                assignedAgents: task.assigned_agents || [isRemote ? COORDINATOR_ID : ORGANIZER_ID],
                status: task.status,
                createdAt: toMs(task.created_at) || Date.now(),
              })
              // Apply extra fields after creation
              const extra: Record<string, any> = {}
              // Restore historical steps so TaskDetail/TaskFlow survives refresh.
              if (Array.isArray(task.steps) && task.steps.length > 0) {
                extra.steps = task.steps.map((s: any, idx: number) => ({
                  id: s.id || `${task.id}-step-${idx}`,
                  agentId: resolveAgentId(s.agent_id, s.agent),
                  action: s.action || 'Processing',
                  // Use the step's own status if available; for terminal tasks, default to
                  // 'completed' (the step ran) rather than propagating the task failure status
                  status: s.status || (task.status === 'running' ? 'running' : 'completed'),
                  input: s.thought,
                  output: s.content,
                  startTime: toMs(s.timestamp) || Date.now(),
                  stepNumber: s.step_number ?? (idx + 1),
                }))
              }
              if (task.executing_agents?.length) extra.executingAgents = task.executing_agents
              if (task.delegated_agents?.length) extra.delegatedAgents = task.delegated_agents
              if (task.assigned_to) extra.assignedTo = task.assigned_to
              if (task.delegated_to) extra.delegatedTo = task.delegated_to
              if (task.output_dir) extra.outputDir = task.output_dir
              if (task.pool_members?.length) extra.poolMembers = task.pool_members
              if (task.selected_members?.length) extra.selectedMembers = task.selected_members
              if (task.execution_mode) extra.executionMode = task.execution_mode
              if (task.result) extra.result = task.result
              if (task.error) extra.error = task.error
              if (isRemote) {
                extra.origin = 'remote'
                extra.requesterId = task.metadata?.requester_id
              }
              if (task.created_at) extra.createdAt = toMs(task.created_at)
              if (task.started_at) extra.startedAt = toMs(task.started_at)
              if (task.completed_at) extra.completedAt = toMs(task.completed_at)
              if (Object.keys(extra).length > 0) {
                updateTask(task.id, extra)
              }
              // Restore progress from server (if task has progress data)
              if (task.progress) {
                const mappedProgress = mapServerProgress(task.progress)
                if (mappedProgress) updateTaskProgress(task.id, mappedProgress)
              }
            })
            if (data.tasks?.length > 0) {
              addLog({
                level: 'info',
                message: `Loaded ${data.tasks.length} existing tasks from backend (${data.tasks.filter((t: any) => t.status === 'running').length} running, ${data.tasks.filter((t: any) => t.status === 'completed').length} completed)`,
              })
            }
            // ── Reconcile agent status from restored task state ──
            // agents_init always sends online/idle, so we fix up busy agents
            // for any task that is still running.
            {
              const runningTasks = (data.tasks || []).filter((t: any) => t.status === 'running')
              if (runningTasks.length > 0) {
                updateAgent(ORGANIZER_ID, { status: 'busy', currentTask: runningTasks[0].id })
                updateAgent(COORDINATOR_ID, { status: 'busy', currentTask: runningTasks[0].id })
                for (const t of runningTasks) {
                  const involved = [
                    ...(t.executing_agents || []),
                    ...(t.delegated_agents || []),
                  ]
                  for (const agId of involved) {
                    updateAgent(agId, { status: 'busy', currentTask: t.id })
                  }
                }
                debugLog('[AgentEventBridge] Reconciled agent status from running tasks:', runningTasks.length)
              }
            }
            break
            
          case 'task_created':
            debugLog('[AgentEventBridge] task_created event:', data)
            if (data.task) {
              debugLog('[AgentEventBridge] Creating task with id:', data.task.id)
              const isRemoteTask = data.task.remote === true
              // Use backend task ID to keep chat and dashboard in sync
              createTask({
                id: data.task.id,
                name: (isRemoteTask ? '📡 ' : '') + (data.task.prompt?.substring(0, 50) || 'Task'),
                description: data.task.prompt || '',
                assignedAgents: [isRemoteTask ? COORDINATOR_ID : ORGANIZER_ID],
                createdAt: toMs(data.task.created_at) || Date.now(),
                metadata: (data.task.metadata as Record<string, unknown> | undefined),
              })
              // Set origin after creation
              updateTask(data.task.id, {
                origin: isRemoteTask ? 'remote' : 'local',
                requesterId: data.task.requester_id || undefined,
                metadata: (data.task.metadata as Record<string, unknown> | undefined),
              })
              addLog({
                level: 'info',
                taskId: data.task.id,
                message: `Task created: ${data.task.id}`,
              })
              // Notify
              notify.info(
                'New Task',
                `"${data.task.prompt?.substring(0, 60) || 'Task'}" has been created`,
                { label: 'View Task', viewMode: 'dashboard' }
              )
            } else {
              debugWarn('[AgentEventBridge] task_created but no task data:', data)
            }
            break
            
          case 'task_started':
            if (data.task) {
              const isRemoteStarted = data.task.remote === true
              // Ensure the task exists in the store (defensive — task_created may not have arrived yet)
              const existingTask = useAgentStore.getState().tasks.find(t => t.id === data.task.id)
              if (!existingTask) {
                createTask({
                  id: data.task.id,
                  name: (isRemoteStarted ? '📡 ' : '') + (data.task.prompt || data.task.description || 'Task').substring(0, 50),
                  description: data.task.prompt || data.task.description || '',
                  assignedAgents: [isRemoteStarted ? COORDINATOR_ID : ORGANIZER_ID],
                  status: 'running',
                  metadata: (data.task.metadata as Record<string, unknown> | undefined),
                })
              }
              updateTask(data.task.id, {
                status: 'running',
                startedAt: toMs(data.task.started_at) || Date.now(),
                origin: isRemoteStarted ? 'remote' : (existingTask?.origin || 'local'),
                requesterId: data.task.requester_id || existingTask?.requesterId || undefined,
                metadata: (data.task.metadata as Record<string, unknown> | undefined) || existingTask?.metadata,
              })
              // Initialize progress from server data or defaults
              const serverStartProgress = data.task.progress
              updateTaskProgress(data.task.id, {
                ...(mapServerProgress(serverStartProgress) || {}),
                phase: normalizePhase(serverStartProgress?.phase),
                percentage: serverStartProgress?.percentage ?? 5,
                phaseLabel: serverStartProgress?.phase_label || (isRemoteStarted ? 'Task received by local team coordinator' : 'Task received by Organizer'),
              })
              // Update agent status
              if (!isRemoteStarted) {
                updateAgent(ORGANIZER_ID, { status: 'busy', currentTask: data.task.id })
              }
              updateAgent(COORDINATOR_ID, { status: 'busy', currentTask: data.task.id })
              addLog({
                level: 'info',
                agentId: ORGANIZER_ID,
                taskId: data.task.id,
                message: `📋 Organizer started task: "${data.task.prompt?.substring(0, 80) || data.task.id}"`,
              })
              addLog({
                level: 'info',
                agentId: COORDINATOR_ID,
                taskId: data.task.id,
                message: `🔄 local team coordinator received task assignment from Organizer`,
              })
              // Generate message: organizer -> coordinator
              addMessage({
                fromAgent: ORGANIZER_ID,
                toAgent: COORDINATOR_ID,
                type: 'request',
                content: `New task assigned: ${data.task.prompt?.substring(0, 150) || 'Processing'}`,
              })
            }
            break
            
          case 'task_step':
            if (data.task) {
              const step = data.task.steps?.[data.task.steps.length - 1]
              if (step) {
                // Ensure task exists in the store (defensive)
                const stepTask = useAgentStore.getState().tasks.find(t => t.id === data.task.id)
                if (!stepTask) {
                  createTask({
                    id: data.task.id,
                    name: 'Task',
                    description: '',
                    assignedAgents: [COORDINATOR_ID],
                    status: 'running',
                  })
                }
                
                // Add step to task
                const agentId = resolveAgentId(step.agent_id || data.agent_id, step.agent)
                const agentType = step.agent_type || data.agent_type || 'worker'
                const agentName = step.agent || agentId
                const action = step.action || 'Processing'
                const actionLower = action.toLowerCase()
                const content = sanitizeStepText(step.content || '')
                const isHeartbeatStep = actionLower === 'tool_heartbeat' || actionLower === 'worker_heartbeat'

                if (!isHeartbeatStep) {
                  addTaskStep(data.task.id, {
                    agentId,
                    agentName,
                    agentType,
                    action: `[${agentType}] ${action}`,
                    status: 'running',
                    input: step.thought,
                    output: content,
                    observation: step.observation,
                    timestamp: toMs(step.timestamp) || Date.now(),
                    duration: step.step_duration,
                    startTime: toMs(step.timestamp) || Date.now(),
                    stepNumber: step.step_number || 0,
                    // Include step duration from server if available
                    endTime: step.step_duration && step.timestamp
                      ? (toMs(step.timestamp)! + step.step_duration * 1000)
                      : undefined,
                  })
                }
                
                // Update agent status
                updateAgent(agentId, { status: 'busy', currentTask: data.task.id })
                
                // --- Apply server-side progress if available ---
                const taskId = data.task.id
                const serverProgress = data.task.progress
                if (serverProgress) {
                  updateTaskProgress(taskId, {
                    ...(mapServerProgress(serverProgress) || {}),
                    phase: normalizePhase(serverProgress.phase || 'executing'),
                    phaseLabel: serverProgress.phase_label || action,
                  })
                }

                // Sync executing/delegated agents and poolMembers from server
                // so topology and progress stay accurate for running tasks.
                const taskUpdates: Record<string, any> = {}
                if (data.task.executing_agents?.length) {
                  taskUpdates.executingAgents = data.task.executing_agents
                }
                if (data.task.delegated_agents?.length) {
                  taskUpdates.delegatedAgents = data.task.delegated_agents
                }
                // If fallback generic task was created from a late task_step, patch title/description.
                const currentTask = useAgentStore.getState().tasks.find(t => t.id === data.task.id)
                if (currentTask && (currentTask.name === 'Task' || !currentTask.description)) {
                  if (data.task.prompt) taskUpdates.description = data.task.prompt
                  if (data.task.prompt && currentTask.name === 'Task') {
                    taskUpdates.name = data.task.prompt.substring(0, 50)
                  }
                }
                if (data.pool_members) {
                  const existingTask = useAgentStore.getState().tasks.find(t => t.id === data.task.id)
                  if (!existingTask?.poolMembers || existingTask.poolMembers.length === 0) {
                    taskUpdates.poolMembers = data.pool_members
                  }
                }
                // Capture ANRouter's selected member IDs and execution mode
                if (step.selected_members?.length || data.selected_members?.length) {
                  taskUpdates.selectedMembers = step.selected_members || data.selected_members
                }
                const stepExecMode = step.execution_mode || data.execution_mode
                if (stepExecMode === 'parallel' || stepExecMode === 'sequential') {
                  taskUpdates.executionMode = stepExecMode
                }
                if (Object.keys(taskUpdates).length > 0) {
                  updateTask(data.task.id, taskUpdates)
                }

                // Generate rich inter-agent messages based on agent type + action
                if (agentType === 'organizer') {
                  // Organizer messages -> coordinator
                  if (actionLower === 'worker_roster') {
                    addMessage({
                      fromAgent: ORGANIZER_ID,
                      toAgent: COORDINATOR_ID,
                      type: 'event',
                      content: content || 'Worker available',
                    })
                  } else if (step.is_delegation || data.is_delegation || actionLower.includes('delegat')) {
                    addMessage({
                      fromAgent: ORGANIZER_ID,
                      toAgent: COORDINATOR_ID,
                      type: 'request',
                      content: `📤 Delegating: ${content.substring(0, 250) || 'Assigning task to team'}`,
                    })
                  } else if (actionLower.includes('plan') || actionLower.includes('receiv')) {
                    addMessage({
                      fromAgent: ORGANIZER_ID,
                      toAgent: COORDINATOR_ID,
                      type: 'event',
                      content: `📋 ${action}: ${content.substring(0, 250) || 'Analyzing task requirements'}`,
                    })
                  } else if (actionLower.includes('aggregat') || actionLower.includes('result') || actionLower.includes('complet')) {
                    addMessage({
                      fromAgent: ORGANIZER_ID,
                      toAgent: COORDINATOR_ID,
                      type: 'response',
                      content: `📊 ${action}: ${content.substring(0, 250) || 'Compiling results'}`,
                    })
                  } else {
                    addMessage({
                      fromAgent: ORGANIZER_ID,
                      toAgent: COORDINATOR_ID,
                      type: 'event',
                      content: `[${action}] ${content.substring(0, 250) || 'Processing...'}`,
                    })
                  }
                } else if (agentType === 'coordinator') {
                  // Coordinator messages
                  if (step.is_delegation || data.is_delegation || actionLower.includes('delegat') || actionLower.includes('assign')) {
                    addMessage({
                      fromAgent: COORDINATOR_ID,
                      toAgent: agentId,
                      type: 'request',
                      content: `🔄 Assigning to worker: ${content.substring(0, 250) || 'Task delegation'}`,
                    })
                  } else {
                    addMessage({
                      fromAgent: COORDINATOR_ID,
                      toAgent: ORGANIZER_ID,
                      type: 'event',
                      content: `[${action}] ${content.substring(0, 250) || 'Coordinating...'}`,
                    })
                  }
                } else {
                  // Worker messages -> coordinator
                  if (actionLower === 'worker_started') {
                    addMessage({
                      fromAgent: agentId,
                      toAgent: COORDINATOR_ID,
                      type: 'event',
                      content: content || `🔧 ${agentName} started working`,
                    })
                  } else if (actionLower === 'worker_completed') {
                    addMessage({
                      fromAgent: agentId,
                      toAgent: COORDINATOR_ID,
                      type: 'response',
                      content: content || `✅ ${agentName} completed`,
                    })
                    updateAgent(agentId, { status: 'idle', currentTask: undefined })
                  } else if (actionLower === 'worker_skipped') {
                    addMessage({
                      fromAgent: agentId,
                      toAgent: COORDINATOR_ID,
                      type: 'event',
                      content: content || `⏭️ ${agentName} was not assigned`,
                    })
                  } else if (actionLower.includes('result') || actionLower.includes('complet') || actionLower.includes('final')) {
                    addMessage({
                      fromAgent: agentId,
                      toAgent: COORDINATOR_ID,
                      type: 'response',
                      content: `✅ ${agentName} completed: ${content.substring(0, 250) || 'Work finished'}`,
                    })
                  } else if (actionLower.includes('think') || actionLower.includes('reason')) {
                    addMessage({
                      fromAgent: agentId,
                      toAgent: COORDINATOR_ID,
                      type: 'event',
                      content: `💭 ${agentName} thinking: ${(step.thought || content).substring(0, 250)}`,
                    })
                  } else if (actionLower.includes('tool') || actionLower.includes('shell') || actionLower.includes('code') || actionLower.includes('python') || actionLower.includes('browse')) {
                    addMessage({
                      fromAgent: agentId,
                      toAgent: COORDINATOR_ID,
                      type: 'event',
                      content: `🔧 ${agentName} using ${action}: ${content.substring(0, 200) || 'Executing...'}`,
                    })
                  } else {
                    // Generic worker step - still show it
                    addMessage({
                      fromAgent: agentId,
                      toAgent: COORDINATOR_ID,
                      type: 'event',
                      content: `👷 ${agentName} [${action}]: ${content.substring(0, 250) || 'Working...'}`,
                    })
                  }
                }
                
                addLog({
                  level: 'info',
                  agentId,
                  taskId: data.task.id,
                  message: `🔧 [${agentType}] ${agentName}: ${action}${content ? ' — ' + content.substring(0, 120) : ''}`,
                })
              }
            }
            break
            
          case 'task_completed':
            if (data.task) {
              if (useAgentStore.getState().dashboardApproval?.taskId === data.task.id) {
                setDashboardApproval(null)
              }
              // Map snake_case cost from backend to camelCase for frontend
              const rawCost = data.task.cost || data.task.token_usage || {}
              const cost = {
                inputTokens: rawCost.input_tokens ?? 0,
                outputTokens: rawCost.output_tokens ?? 0,
                totalTokens: rawCost.total_tokens ?? 0,
                duration: rawCost.duration ?? rawCost.elapsed_time ?? 0,
                costUsd: rawCost.cost_usd,
                x402Payment: rawCost.x402_payment,
              }
              // Extract delegated workers from task data
              const delegatedAgents = (data.task.delegated_agents || []).map((a: string) => resolveAgentId(a, a))
              const executingAgents = (data.task.executing_agents || []).map((a: string) => resolveAgentId(a, a))
              const assignedTo = data.task.assigned_to
              const outputDir = data.task.output_dir

              // Capture pool members for topology (if not already set)
              if (data.pool_members) {
                updateTask(data.task.id, { poolMembers: data.pool_members })
              }
              updateTask(data.task.id, {
                status: 'completed',
                completedAt: toMs(data.task.completed_at) || Date.now(),
                result: data.task.result,
                cost: (cost.totalTokens > 0 || (cost.duration ?? 0) > 0 || cost.costUsd != null) ? cost : undefined,
                delegatedAgents: delegatedAgents.length > 0 ? delegatedAgents : undefined,
                executingAgents: executingAgents.length > 0 ? executingAgents : undefined,
                assignedTo: assignedTo || undefined,
                outputDir: outputDir || undefined,
              })
              // Finalize progress to 100% — use server progress if available
              const completedProg = data.task.progress
              updateTaskProgress(data.task.id, {
                ...(mapServerProgress(completedProg) || {}),
                phase: 'completed',
                percentage: 100,
                activeWorkers: 0,
                totalWorkers: completedProg?.total_workers ?? 0,
                completedWorkers: completedProg?.completed_workers ?? completedProg?.total_workers ?? 0,
                skippedWorkers: completedProg?.skipped_workers ?? 0,
                phaseLabel: 'Completed',
              })
              // ── Reset ALL agents that were busy for this task ──
              // Reset Organizer and Coordinator first
              updateAgent(ORGANIZER_ID, { status: 'online', currentTask: undefined })
              updateAgent(COORDINATOR_ID, { status: 'online', currentTask: undefined })
              // Reset all delegated + executing agents
              const allInvolved = new Set([...delegatedAgents, ...executingAgents])
              allInvolved.forEach((wId: string) => {
                updateAgent(wId, { status: 'idle', currentTask: undefined })
              })
              // Also scan ALL agents and reset any still "busy" for this task
              const currentAgents = useAgentStore.getState().agents
              currentAgents.forEach(agent => {
                if (agent.status === 'busy' && agent.currentTask === data.task.id) {
                  const resetStatus = (agent.type === 'organizer' || agent.type === 'coordinator') ? 'online' : 'idle'
                  updateAgent(agent.id, { status: resetStatus, currentTask: undefined })
                }
              })

              addLog({
                level: 'info',
                taskId: data.task.id,
                message: `✅ Task completed successfully${cost.totalTokens > 0 ? ` — ${cost.totalTokens} tokens used${cost.duration != null ? `, ${formatNumberNoTrailingZeros(cost.duration, 1)}s elapsed` : ''}` : ''}`,
              })
              // ── Send notification for task completion ──
              const taskName = data.task.prompt?.substring(0, 60) || data.task.name || data.task.id
              notify.success(
                'Task Completed',
                `"${taskName}" finished${cost.duration != null ? ` in ${formatNumberNoTrailingZeros(cost.duration, 1)}s` : ''}`,
                { label: 'View Result', viewMode: 'dashboard' }
              )
              // Worker -> Coordinator result message
              if (delegatedAgents.length > 0) {
                addMessage({
                  fromAgent: delegatedAgents[delegatedAgents.length - 1],
                  toAgent: COORDINATOR_ID,
                  type: 'response',
                  content: `✅ Work completed. Returning results to local team coordinator.`,
                })
              }
              // Coordinator -> Organizer result message
              addMessage({
                fromAgent: COORDINATOR_ID,
                toAgent: ORGANIZER_ID,
                type: 'response',
                content: `Task completed: ${data.task.result ? String(data.task.result).substring(0, 200) : 'All workers finished successfully'}`,
              })
              // Organizer final summary
              addMessage({
                fromAgent: ORGANIZER_ID,
                toAgent: SYSTEM_ID,
                type: 'response',
                content: `📊 Final result delivered to user${cost.totalTokens > 0 ? ` (${cost.totalTokens.toLocaleString()} tokens${cost.duration != null ? `, ${formatNumberNoTrailingZeros(cost.duration, 1)}s` : ''})` : ''}`,
              })
              // Sync wallet after task completes (safety net if any wallet_transaction SSE was missed)
              const walletState = useWalletStore.getState()
              walletState.fetchBalance()
              walletState.fetchTransactions()
              walletState.fetchSummary()
              // Notify chatStore so the active chat session clears its task reference
              useChatStore.getState().onTaskCompleted(data.task.id)
            }
            break

          case 'task_failed':
            if (data.task) {
              if (useAgentStore.getState().dashboardApproval?.taskId === data.task.id) {
                setDashboardApproval(null)
              }
              // Ensure task exists (defensive — task_created may have been missed)
              const existingFailed = useAgentStore.getState().tasks.find(t => t.id === data.task.id)
              if (!existingFailed) {
                createTask({
                  id: data.task.id,
                  name: (data.task.prompt || data.task.description || 'Task').substring(0, 50),
                  description: data.task.prompt || data.task.description || '',
                  assignedAgents: [ORGANIZER_ID],
                  status: 'failed',
                  error: data.task.error,
                  completedAt: toMs(data.task.completed_at) || Date.now(),
                })
              }
              // Map cost even on failure
              const failCostRaw = data.task.cost || data.task.token_usage || {}
              const failCost = {
                inputTokens: failCostRaw.input_tokens ?? 0,
                outputTokens: failCostRaw.output_tokens ?? 0,
                totalTokens: failCostRaw.total_tokens ?? 0,
                duration: failCostRaw.duration ?? failCostRaw.elapsed_time ?? 0,
                costUsd: failCostRaw.cost_usd,
                x402Payment: failCostRaw.x402_payment,
              }
              updateTask(data.task.id, {
                status: 'failed',
                completedAt: toMs(data.task.completed_at) || Date.now(),
                error: data.task.error,
                cost: (failCost.totalTokens > 0 || (failCost.duration ?? 0) > 0 || failCost.costUsd != null) ? failCost : undefined,
              })
              // Use server-provided progress data for final state
              const failedProg = data.task.progress
              updateTaskProgress(data.task.id, {
                ...(mapServerProgress(failedProg) || {}),
                phase: 'completed',
                percentage: 100,
                activeWorkers: 0,
                totalWorkers: failedProg?.total_workers ?? 0,
                completedWorkers: failedProg?.completed_workers ?? 0,
                skippedWorkers: failedProg?.skipped_workers ?? 0,
                phaseLabel: `Failed${failedProg?.phase ? ` (at ${failedProg.phase})` : ''}`,
              })
              // ── Reset ALL agents — clear busy state on failure ──
              updateAgent(ORGANIZER_ID, { status: 'online', currentTask: undefined })
              updateAgent(COORDINATOR_ID, { status: 'online', currentTask: undefined })
              const failAgents = useAgentStore.getState().agents
              failAgents.forEach(agent => {
                if (agent.status === 'busy' && agent.currentTask === data.task.id) {
                  const rs = (agent.type === 'organizer' || agent.type === 'coordinator') ? 'online' : 'idle'
                  updateAgent(agent.id, { status: rs, currentTask: undefined })
                }
              })

              addLog({
                level: 'error',
                taskId: data.task.id,
                message: `❌ Task failed: ${data.task.error}`,
              })
              // ── Send notification for task failure ──
              const failTaskName = data.task.prompt?.substring(0, 60) || data.task.name || data.task.id
              notify.error(
                'Task Failed',
                `"${failTaskName}" failed: ${data.task.error?.substring(0, 80) || 'Unknown error'}`,
                { label: 'View Details', viewMode: 'dashboard' }
              )
              // Coordinator reports failure to Organizer
              addMessage({
                fromAgent: COORDINATOR_ID,
                toAgent: ORGANIZER_ID,
                type: 'error',
                content: `❌ Task execution failed: ${data.task.error?.substring(0, 200) || 'Unknown error'}`,
              })
              // Organizer reports to system
              addMessage({
                fromAgent: ORGANIZER_ID,
                toAgent: SYSTEM_ID,
                type: 'error',
                content: `Task failed. Error reported to user.`,
              })
              // Notify chatStore so the active chat session clears its task reference
              useChatStore.getState().onTaskCompleted(data.task.id)
              // Sync wallet after task fails (safety net)
              const walletState = useWalletStore.getState()
              walletState.fetchBalance()
              walletState.fetchTransactions()
              walletState.fetchSummary()
            }
            break

          case 'task_cancelled':
            if (data.task) {
              // Clear approval popup when task is cancelled (e.g. user cancelled while waiting)
              const currentApproval = useAgentStore.getState().dashboardApproval
              if (currentApproval?.taskId === data.task.id) {
                setDashboardApproval(null)
              }
              updateTask(data.task.id, {
                status: 'cancelled',
                completedAt: toMs(data.task.completed_at) || Date.now(),
              })
              updateTaskProgress(data.task.id, {
                ...(mapServerProgress(data.task.progress) || {}),
                phase: 'completed',
                percentage: 100,
                activeWorkers: 0,
                phaseLabel: 'Cancelled',
              })
              updateAgent(ORGANIZER_ID, { status: 'online', currentTask: undefined })
              updateAgent(COORDINATOR_ID, { status: 'online', currentTask: undefined })
              const cancelAgents = useAgentStore.getState().agents
              cancelAgents.forEach(agent => {
                if (agent.status === 'busy' && agent.currentTask === data.task.id) {
                  const rs = (agent.type === 'organizer' || agent.type === 'coordinator') ? 'online' : 'idle'
                  updateAgent(agent.id, { status: rs, currentTask: undefined })
                }
              })
              addLog({
                level: 'info',
                taskId: data.task.id,
                message: `🚫 Task cancelled: ${data.task.id}`,
              })
              useChatStore.getState().onTaskCompleted(data.task.id)
            }
            break

          case 'task_phase_change':
          case 'task_progress':
            if (data.task?.id && data.task?.progress) {
              const p = data.task.progress
              updateTaskProgress(data.task.id, {
                ...(mapServerProgress(p) || {}),
                phase: normalizePhase(p.phase),
                phaseLabel: p.phase_label || p.phase || 'Unknown',
              })
            }
            break
            
          case 'keepalive':
            // Ignore keepalive messages
            break
          
          // Sandbox events - log them for tracking
          case 'sandbox_registered':
            addLog({
              level: 'info',
              taskId: data.task_id,
              message: `📦 Sandbox environment started for task execution (${data.sandbox_id})`,
            })
            // Mark sandbox as unread for sidebar badge
            if (data.sandbox_id) {
              addUnreadSandbox(data.sandbox_id)
              notify.info(
                'New Sandbox',
                `Sandbox "${data.sandbox_id}" started${data.task_id ? ` for task ${data.task_id}` : ''}`,
                { label: 'View Sandbox', viewMode: 'sandbox' }
              )
            }
            break
            
          case 'sandbox_completed':
            addLog({
              level: 'info',
              taskId: data.task_id,
              message: `📦 Sandbox execution completed (${data.sandbox_id})`,
            })
            break
          
          case 'agent_started':
            addLog({
              level: 'info',
              agentId: data.agent_id,
              taskId: data.task_id,
              message: `🚀 Agent "${data.agent || data.agent_id}" started working`,
            })
            break
          
          case 'agent_completed':
            addLog({
              level: 'info',
              agentId: data.agent_id,
              taskId: data.task_id,
              message: `✔ Agent "${data.agent || data.agent_id}" completed work`,
            })
            break

          case 'task_delegated': {
            const delegatedTask = data.task
            if (delegatedTask) {
              updateTask(delegatedTask.id, {
                status: 'delegated',
                delegatedTo: delegatedTask.delegated_to,
              })
              addLog({
                level: 'info',
                taskId: delegatedTask.id,
                message: `📤 Task delegated to remote node: ${delegatedTask.delegated_to || 'unknown'}`,
              })
              addMessage({
                fromAgent: COORDINATOR_ID,
                toAgent: ORGANIZER_ID,
                type: 'event',
                content: `Task "${delegatedTask.description || delegatedTask.id}" delegated to node ${delegatedTask.delegated_to}`,
              })
            }
            break
          }

          case 'task_cost_update': {
            const costTask = data.task
            if (costTask) {
              updateTask(costTask.id, {
                cost: {
                  totalTokens: costTask.total_tokens,
                  inputTokens: costTask.input_tokens,
                  outputTokens: costTask.output_tokens,
                  duration: costTask.duration,
                },
              })
            }
            break
          }

          // Wallet transaction from payment gate
          case 'wallet_transaction': {
            // subscription_manager sends wrapped format: {type, data: {transaction, balance}, timestamp}
            const payload = data.data || data
            const txData = payload.transaction
            if (txData) {
              useWalletStore.getState().addTransaction(
                {
                  id: txData.id || `tx-${Date.now()}`,
                  timestamp: txData.timestamp || Date.now(),
                  type: txData.type || 'expense',
                  amount: txData.amount || 0,
                  taskId: txData.taskId ?? txData.task_id,
                  taskName: txData.taskName ?? txData.task_name,
                  description: txData.description || '',
                  txHash: txData.txHash ?? txData.tx_hash,
                  mode: txData.mode,
                  network: txData.network,
                  payer: txData.payer,
                  payee: txData.payee,
                },
                payload.balance,
              )
              // Notify user of payment event
              const amt = txData.amount != null ? `${formatNumberNoTrailingZeros(Number(txData.amount), 6)} ${getPaymentTokenSymbol()}` : ''
              const taskLabel = txData.taskName ?? txData.task_name ?? ''
              if (txData.type === 'expense') {
                notify.warning(
                  'Payment Sent',
                  `${amt} paid${taskLabel ? ` for "${taskLabel}"` : ''}`,
                  { label: 'View Wallet', viewMode: 'dashboard' }
                )
              } else if (txData.type === 'income') {
                notify.success(
                  'Payment Received',
                  `${amt} received${taskLabel ? ` for "${taskLabel}"` : ''}`,
                  { label: 'View Wallet', viewMode: 'dashboard' }
                )
              }
            }
            break
          }

          // Human-in-the-loop approval from Dashboard tasks (tasks not started via Chat)
          case 'approval_request': {
            // subscription_manager sends wrapped format: {type, data: {approval: {...}}, timestamp}
            const approvalPayload = data.data || data
            const approval = approvalPayload.approval
            if (approval?.id) {
              setDashboardApproval({
                id: approval.id,
                taskId: approval.task_id,
                title: approval.title || 'Approval needed',
                description: approval.description || '',
                options: approval.options || [
                  { id: 'approve', label: 'Approve', style: 'primary' },
                  { id: 'deny', label: 'Deny', style: 'danger' },
                ],
                type: approval.type,
                metadata: approval.metadata,
              })
              notify.warning(
                'Approval Required',
                approval.title || 'Agent is waiting for your decision',
                { label: 'Review', viewMode: 'dashboard' }
              )
            }
            break
          }

          case 'approval_resolved': {
            // subscription_manager sends wrapped format: {type, data: {approval_id, decision}, timestamp}
            const resolvedPayload = data.data || data
            const approvalId = resolvedPayload.approval_id
            const currentDashApproval = useAgentStore.getState().dashboardApproval
            if (!currentDashApproval || currentDashApproval.id === approvalId) {
              setDashboardApproval(null)
            }
            break
          }

          // Real-time agent status/capability sync
          case 'agent_updated': {
            // Single agent was updated (e.g. status change via PUT /api/agent/agents/:id)
            const agentData = data.data || data
            if (agentData.agent_id) {
              updateAgent(agentData.agent_id, {
                name: agentData.name,
                status: agentData.status || 'offline',
                model: agentData.model,
                capabilities: agentData.capabilities || [],
              })
              addLog({
                level: 'info',
                agentId: agentData.agent_id,
                message: `Agent "${agentData.name || agentData.agent_id}" updated → ${agentData.status}`,
              })
            }
            break
          }

          case 'pool_updated': {
            // Full Local Agent Workforce Pool refresh (worker went offline/online)
            const poolData = data.data || data
            const poolAgents = poolData.agents
            if (Array.isArray(poolAgents)) {
              poolAgents.forEach((agent: any) => {
                updateAgent(agent.id, {
                  name: agent.name,
                  status: agent.status || 'offline',
                  model: agent.model,
                  capabilities: agent.capabilities || [],
                })
              })
              addLog({
                level: 'info',
                message: `Local Agent Workforce Pool updated: ${poolData.online_worker_count}/${poolData.total_worker_count} workers online`,
              })
            }
            break
          }
            
          default:
            debugLog('Unknown agent event:', data.type)
        }
      } catch (e) {
        console.debug('Invalid SSE frame:', e);
      }
    }

    return () => {
      es.close()
      eventSourceRef.current = null
    }
  }, [addAgent, updateAgent, createTask, updateTask, addTaskStep, updateTaskProgress, addMessage, addLog, addUnreadSandbox, setDashboardApproval])

  // ── Periodic stale-agent cleanup (safety net) ──
  // If no task is running but agents are still "busy", reset them.
  useEffect(() => {
    const interval = setInterval(() => {
      const state = useAgentStore.getState()
      const hasRunning = state.tasks.some(t => ['running', 'delegated'].includes(t.status))
      if (hasRunning) return // Tasks are running or delegated, don't clear busy agents

      let changed = false
      state.agents.forEach(agent => {
        if (agent.status === 'busy') {
          const resetStatus = (agent.type === 'organizer' || agent.type === 'coordinator') ? 'online' : 'idle'
          updateAgent(agent.id, { status: resetStatus, currentTask: undefined })
          changed = true
        }
      })
      if (changed) {
        debugLog('[AgentEventBridge] Stale-agent cleanup: reset busy agents (no running tasks)')
      }
    }, 10_000) // Check every 10 seconds

    return () => clearInterval(interval)
  }, [updateAgent])

  return null
}
