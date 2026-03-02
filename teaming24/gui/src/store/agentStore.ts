import { create } from 'zustand'
import { notify } from './notificationStore'
import { getApiBaseAbsolute } from '../utils/api'
import { COORDINATOR_ID, LOCAL_COORDINATOR_NAME, prefixedId } from '../utils/ids'
import { debugLog } from '../utils/debug'
import type { PendingApproval } from './chatStore'

const API_BASE = getApiBaseAbsolute()

export type AgentStatus = 'online' | 'offline' | 'busy' | 'error' | 'idle'
export type TaskStatus = 'pending' | 'running' | 'delegated' | 'completed' | 'failed' | 'cancelled'
export type MessageType = 'request' | 'response' | 'event' | 'error'

// Local agent roles
export type LocalAgentRole = 'organizer' | 'coordinator' | 'worker'

// Agent types: local agents with roles, or remote Agentic Nodes
export type AgentType = LocalAgentRole | 'agentic_node'

export interface AgentCapability {
  name: string
  description: string
}

export interface Agent {
  id: string
  name: string
  type: AgentType  // 'organizer' | 'coordinator' | 'worker' | 'agentic_node'
  endpoint?: string  // For Agentic Nodes
  status: AgentStatus
  capabilities: AgentCapability[]
  currentTask?: string
  lastSeen: number
  goal?: string
  backstory?: string
  model?: string
  tools?: string[]
  system_prompt?: string
  allow_delegation?: boolean
  metadata?: Record<string, unknown>
}

// Remote Agentic Node (AN) connected via AgentaNet
export interface AgenticNode {
  id: string
  name: string
  endpoint: string
  status: AgentStatus
  capabilities: AgentCapability[]
  lastSeen: number
  region?: string
  metadata?: Record<string, unknown>
}

export interface TaskStep {
  id: string
  agentId: string
  agentName?: string
  agentType?: string
  action: string
  status: TaskStatus
  input?: unknown
  output?: unknown
  observation?: unknown
  timestamp?: number
  duration?: number
  startTime?: number
  endTime?: number
  error?: string
  stepNumber?: number  // Global step counter from CrewAI
}

/** Task execution phases in lifecycle order */
export type TaskPhase =
  | 'received'      // Organizer received the request
  | 'routing'       // ANRouter selecting pool members
  | 'dispatching'   // Organizer dispatching to selected coordinators/pool members
  | 'executing'     // Workers actively executing subtasks
  | 'aggregating'   // Organizer aggregating results from workers
  | 'completed'     // Task done (success or failure)

/** Fine-grained progress tracking for tasks */
export interface TaskProgress {
  phase: TaskPhase
  percentage: number            // 0-100
  totalWorkers: number          // Total workers in roster
  completedWorkers: number      // Workers that finished
  activeWorkers: number         // Workers currently executing
  skippedWorkers: number        // Workers that were skipped
  phaseLabel: string            // Human-readable phase description
  currentAgent?: string
  currentAction?: string
  workerStatuses?: WorkerStatusSummary[]
}

export interface WorkerStatusSummary {
  name: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'timeout'
  action?: string
  detail?: string
  tool?: string
  stepCount?: number
  updatedAt?: number
  startedAt?: number
  lastHeartbeatAt?: number
  finishedAt?: number
  error?: string
  order?: number
}

export interface Task {
  id: string
  name: string
  description: string
  status: TaskStatus
  assignedAgents: string[]  // Legacy field for backward compatibility
  // New delegation tracking fields
  assignedTo?: string  // coordinator-1 or remote AN
  delegatedAgents?: string[]  // Workers that actually executed
  executingAgents?: string[]  // Agents currently working on this task
  steps: TaskStep[]
  createdAt: number
  startedAt?: number
  completedAt?: number
  result?: unknown
  error?: string
  // Fine-grained progress tracking
  progress?: TaskProgress
  // Cost tracking
  cost?: {
    inputTokens?: number
    outputTokens?: number
    totalTokens?: number
    duration?: number
    costUsd?: number
    x402Payment?: number
  }
  outputDir?: string  // Output directory for task results
  delegatedTo?: string  // Remote node ID when task is delegated
  origin?: 'local' | 'remote'  // Whether this task was initiated locally or received from a remote AN
  requesterId?: string  // ID of the remote requester (when origin='remote')
  poolMembers?: Array<{
    id: string
    name: string
    type: 'local' | 'remote'
    status: string
    capabilities: string[]
    ip?: string | null
    port?: number | null
    an_id?: string | null
  }>
  selectedMembers?: string[]  // IDs of pool members selected by ANRouter
  executionMode?: 'parallel' | 'sequential'
  metadata?: Record<string, unknown>
}

export interface AgentMessage {
  id: string
  timestamp: number
  fromAgent: string
  toAgent: string
  type: MessageType
  content: string
  metadata?: Record<string, unknown>
}

export interface LogEntry {
  id: string
  timestamp: number
  level: 'info' | 'warn' | 'error' | 'debug'
  agentId?: string
  taskId?: string
  message: string
  details?: unknown
}

interface AgentState {
  // Local agents (Organizer, Coordinator, Worker)
  agents: Agent[]
  // Remote Agentic Nodes connected via AgentaNet
  agenticNodes: AgenticNode[]
  tasks: Task[]
  messages: AgentMessage[]
  logs: LogEntry[]
  selectedAgentId: string | null
  selectedTaskId: string | null
  isLoadingTasks: boolean
  
  // Unread task tracking
  unreadTaskIds: Set<string>
  unreadTaskCount: number
  markTaskRead: (taskId: string) => void
  markAllTasksRead: () => void
  
  // Unread sandbox tracking
  unreadSandboxIds: Set<string>
  unreadSandboxCount: number
  markSandboxRead: (sandboxId: string) => void
  markAllSandboxesRead: () => void
  addUnreadSandbox: (sandboxId: string) => void

  // Dashboard approval (from tasks not started in chat)
  dashboardApproval: PendingApproval | null
  setDashboardApproval: (approval: PendingApproval | null) => void

  // Agent actions
  addAgent: (agent: Omit<Agent, 'id' | 'lastSeen'> & { id?: string }) => void
  updateAgent: (id: string, updates: Partial<Agent>) => void
  removeAgent: (id: string) => void
  setSelectedAgent: (id: string | null) => void
  
  // Agentic Node actions
  addAgenticNode: (node: Omit<AgenticNode, 'id' | 'lastSeen'>) => void
  updateAgenticNode: (id: string, updates: Partial<AgenticNode>) => void
  removeAgenticNode: (id: string) => void
  
  // Task actions
  createTask: (task: Omit<Task, 'id' | 'steps' | 'status' | 'createdAt'> & { id?: string; status?: TaskStatus; createdAt?: number }) => string
  updateTask: (id: string, updates: Partial<Task>) => void
  addTaskStep: (taskId: string, step: Omit<TaskStep, 'id'>) => void
  updateTaskStep: (taskId: string, stepId: string, updates: Partial<TaskStep>) => void
  updateTaskProgress: (taskId: string, progress: Partial<TaskProgress>) => void
  setSelectedTask: (id: string | null) => void
  deleteTask: (id: string) => Promise<void>
  clearAllTasks: () => Promise<void>
  archiveTask: (id: string) => void
  
  // Database sync actions
  loadTasksFromDB: () => Promise<void>
  saveTaskToDB: (task: Task) => Promise<void>
  applySnapshot: (snapshot: { tasks: any[] }) => void
  
  // Message actions
  addMessage: (message: Omit<AgentMessage, 'id' | 'timestamp'>) => void
  clearMessages: () => void
  
  // Log actions
  addLog: (log: Omit<LogEntry, 'id' | 'timestamp'>) => void
  clearLogs: () => void
  
  // Get all (agents + agentic nodes for unified view)
  getAllAgents: () => (Agent | AgenticNode)[]
  
  // Agent DB persistence
  loadAgentsFromDB: () => Promise<void>
  saveAgentToDB: (agent: Agent) => Promise<void>
  deleteAgentFromDB: (agentId: string) => Promise<void>
  updateAgentInDB: (agentId: string, updates: Partial<Agent>) => Promise<void>
  
  // Demo data
  loadDemoData: () => void
}

const _taskPersistTimers = new Map<string, ReturnType<typeof setTimeout>>()

function toEpochMs(value: unknown): number | undefined {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return undefined
  return value > 1e12 ? value : value * 1000
}

const VALID_AGENT_TYPES = new Set<AgentType>(['organizer', 'coordinator', 'worker', 'agentic_node'])
const VALID_AGENT_STATUSES = new Set<AgentStatus>(['online', 'offline', 'busy', 'error', 'idle'])
const VALID_TASK_STATUSES = new Set<TaskStatus>(['pending', 'running', 'delegated', 'completed', 'failed', 'cancelled'])
const VALID_TASK_PHASES = new Set<TaskPhase>(['received', 'routing', 'dispatching', 'executing', 'aggregating', 'completed'])

function normalizeAgentType(value: unknown, fallback: AgentType = 'worker'): AgentType {
  if (typeof value === 'string' && VALID_AGENT_TYPES.has(value as AgentType)) {
    return value as AgentType
  }
  return fallback
}

function normalizeAgentStatus(value: unknown, fallback: AgentStatus = 'offline'): AgentStatus {
  if (typeof value === 'string' && VALID_AGENT_STATUSES.has(value as AgentStatus)) {
    return value as AgentStatus
  }
  return fallback
}

function normalizeTaskStatus(value: unknown, fallback: TaskStatus): TaskStatus {
  if (typeof value === 'string' && VALID_TASK_STATUSES.has(value as TaskStatus)) {
    return value as TaskStatus
  }
  return fallback
}

function normalizeTaskPhase(value: unknown, fallback: TaskPhase = 'received'): TaskPhase {
  if (typeof value === 'string' && VALID_TASK_PHASES.has(value as TaskPhase)) {
    return value as TaskPhase
  }
  return fallback
}

const TASK_PHASE_ORDER: TaskPhase[] = [
  'received',
  'routing',
  'dispatching',
  'executing',
  'aggregating',
  'completed',
]

const TASK_PHASE_RANK: Record<TaskPhase, number> = TASK_PHASE_ORDER.reduce((acc, phase, index) => {
  acc[phase] = index
  return acc
}, {} as Record<TaskPhase, number>)

function normalizeWorkerStatuses(value: unknown): WorkerStatusSummary[] {
  if (!Array.isArray(value)) return []
  const allowed = new Set<WorkerStatusSummary['status']>(['pending', 'running', 'completed', 'failed', 'skipped', 'timeout'])
  return value
    .map((raw, index) => {
      if (!raw || typeof raw !== 'object') return null
      const item = raw as Record<string, unknown>
      const name = String(item.name || '').trim()
      if (!name) return null
      const rawStatus = String(item.status || 'pending').trim().toLowerCase() as WorkerStatusSummary['status']
      return {
        name,
        status: allowed.has(rawStatus) ? rawStatus : 'pending',
        action: typeof item.action === 'string' ? item.action : undefined,
        detail: typeof item.detail === 'string' ? item.detail : undefined,
        tool: typeof item.tool === 'string' ? item.tool : undefined,
        stepCount: typeof item.step_count === 'number'
          ? item.step_count
          : (typeof item.stepCount === 'number' ? item.stepCount : undefined),
        updatedAt: toEpochMs(item.updated_at) ?? toEpochMs(item.updatedAt),
        startedAt: toEpochMs(item.started_at) ?? toEpochMs(item.startedAt),
        lastHeartbeatAt: toEpochMs(item.last_heartbeat_at) ?? toEpochMs(item.lastHeartbeatAt),
        finishedAt: toEpochMs(item.finished_at) ?? toEpochMs(item.finishedAt),
        error: typeof item.error === 'string' ? item.error : undefined,
        order: typeof item.order === 'number' ? item.order : index,
      }
    })
    .filter(Boolean) as WorkerStatusSummary[]
}

function normalizeTaskProgressPayload(raw: Record<string, unknown> | null | undefined): TaskProgress | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  return {
    phase: normalizeTaskPhase((raw.phase as string) || 'received'),
    percentage: (raw.percentage as number) ?? 0,
    totalWorkers: (raw.total_workers as number) ?? (raw.totalWorkers as number) ?? 0,
    completedWorkers: (raw.completed_workers as number) ?? (raw.completedWorkers as number) ?? 0,
    activeWorkers: (raw.active_workers as number) ?? (raw.activeWorkers as number) ?? 0,
    skippedWorkers: (raw.skipped_workers as number) ?? (raw.skippedWorkers as number) ?? 0,
    phaseLabel: (raw.phase_label as string) || (raw.phaseLabel as string) || (raw.phase as string) || 'Unknown',
    currentAgent: (raw.current_agent as string) || (raw.currentAgent as string) || '',
    currentAction: (raw.current_action as string) || (raw.currentAction as string) || '',
    workerStatuses: normalizeWorkerStatuses(raw.worker_statuses ?? raw.workerStatuses),
  }
}

function normalizeTaskMetadata(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  return value as Record<string, unknown>
}

function mapBackendTaskRecord(raw: Record<string, unknown>): Task {
  const safeStatus = normalizeTaskStatus(raw.status, 'completed')
  const metadata = normalizeTaskMetadata(raw.metadata)
  const progressFromMetadata = normalizeTaskProgressPayload((metadata?.progress as Record<string, unknown> | undefined) || null)

  return {
    id: raw.id as string,
    name: raw.name as string || raw.prompt as string || 'Unnamed Task',
    description: raw.description as string || raw.prompt as string || '',
    status: safeStatus,
    assignedAgents: (raw.delegated_agents as string[]) || [],
    steps: normalizeTaskSteps(raw.steps, safeStatus),
    createdAt: typeof raw.created_at === 'number' && raw.created_at > 0
      ? (raw.created_at > 1e12 ? raw.created_at : raw.created_at * 1000)
      : Date.now(),
    startedAt: typeof raw.started_at === 'number' && raw.started_at > 0
      ? (raw.started_at > 1e12 ? raw.started_at : raw.started_at * 1000)
      : undefined,
    completedAt: typeof raw.completed_at === 'number' && raw.completed_at > 0
      ? (raw.completed_at > 1e12 ? raw.completed_at : raw.completed_at * 1000)
      : undefined,
    result: raw.result as unknown,
    error: raw.error as string | undefined,
    assignedTo: raw.assigned_to as string | undefined,
    delegatedAgents: raw.delegated_agents as string[] | undefined,
    delegatedTo: (raw.delegated_to as string | undefined) ?? (metadata?.delegated_to as string | undefined),
    outputDir: raw.output_dir as string | undefined,
    cost: raw.cost as Record<string, unknown> | undefined,
    executingAgents: raw.executing_agents as string[] | undefined,
    origin: (raw.origin as Task['origin'] | undefined) ?? (metadata?.origin as Task['origin'] | undefined),
    requesterId: (raw.requester_id as string | undefined) ?? (metadata?.requester_id as string | undefined),
    poolMembers: (raw.pool_members as Task['poolMembers'] | undefined) ?? (metadata?.pool_members as Task['poolMembers'] | undefined),
    selectedMembers: (raw.selected_members as string[] | undefined) ?? (metadata?.selected_members as string[] | undefined),
    executionMode: (raw.execution_mode as 'parallel' | 'sequential' | undefined)
      ?? (metadata?.execution_mode as 'parallel' | 'sequential' | undefined),
    progress: normalizeTaskProgressPayload((raw.progress as Record<string, unknown> | undefined) || null) || progressFromMetadata,
    metadata,
  }
}

function mergeTaskProgressState(prev: TaskProgress, updates: Partial<TaskProgress>): TaskProgress {
  const requestedPhase = normalizeTaskPhase(updates.phase, prev.phase)
  const prevRank = TASK_PHASE_RANK[prev.phase] ?? 0
  const nextRank = TASK_PHASE_RANK[requestedPhase] ?? prevRank
  const phaseRegressed = nextRank < prevRank
  const acceptedPhase = phaseRegressed ? prev.phase : requestedPhase

  const requestedPct = typeof updates.percentage === 'number' && Number.isFinite(updates.percentage)
    ? Math.max(0, Math.min(100, updates.percentage))
    : prev.percentage
  const percentage = phaseRegressed
    ? prev.percentage
    : Math.max(prev.percentage, requestedPct)

  const totalWorkers = typeof updates.totalWorkers === 'number'
    ? Math.max(prev.totalWorkers, updates.totalWorkers)
    : prev.totalWorkers
  const completedWorkers = typeof updates.completedWorkers === 'number'
    ? Math.max(prev.completedWorkers, updates.completedWorkers)
    : prev.completedWorkers
  const skippedWorkers = typeof updates.skippedWorkers === 'number'
    ? Math.max(prev.skippedWorkers, updates.skippedWorkers)
    : prev.skippedWorkers

  return {
    ...prev,
    ...updates,
    phase: acceptedPhase,
    percentage,
    totalWorkers,
    completedWorkers,
    skippedWorkers,
    phaseLabel: phaseRegressed
      ? prev.phaseLabel
      : (typeof updates.phaseLabel === 'string' && updates.phaseLabel.trim()
          ? updates.phaseLabel
          : prev.phaseLabel),
    currentAgent: phaseRegressed
      ? prev.currentAgent
      : (typeof updates.currentAgent === 'string' ? updates.currentAgent : prev.currentAgent),
    currentAction: phaseRegressed
      ? prev.currentAction
      : (typeof updates.currentAction === 'string' ? updates.currentAction : prev.currentAction),
    workerStatuses: Array.isArray(updates.workerStatuses)
      ? updates.workerStatuses
      : prev.workerStatuses,
  }
}

function sanitizeStepValue(value: unknown): unknown {
  if (typeof value !== 'string') return value
  return value
    .replace(/\bundefined\b/gi, '')
    .replace(/\s+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function normalizeTaskStep(raw: Record<string, unknown>, fallbackStatus: TaskStatus, index: number): TaskStep {
  const rawAgentId = (raw.agentId ?? raw.agent_id ?? raw.agent) as string | undefined
  const rawAgentName = (raw.agentName ?? raw.agent_name ?? raw.agent) as string | undefined
  const rawStepNumber = (raw.stepNumber ?? raw.step_number) as number | undefined
  const rawTimestamp = (raw.timestamp as number | undefined)
  const startMs = toEpochMs(raw.startTime) ?? toEpochMs(raw.started_at) ?? toEpochMs(rawTimestamp)
  const endMs = toEpochMs(raw.endTime) ?? toEpochMs(raw.completed_at)
  const durationSec = (raw.duration ?? raw.step_duration) as number | undefined
  const computedEndMs = endMs ?? (
    typeof durationSec === 'number' && startMs
      ? startMs + durationSec * 1000
      : undefined
  )
  const safeStepNumber = typeof rawStepNumber === 'number' && Number.isFinite(rawStepNumber)
    ? rawStepNumber
    : index + 1
  const stepId = String(
    raw.id
    ?? raw.step_id
    ?? `${rawAgentId || rawAgentName || 'agent'}-${safeStepNumber}-${rawTimestamp || startMs || index}`
  )

  const thought = sanitizeStepValue(raw.thought ?? raw.input)
  const observation = sanitizeStepValue(raw.observation)
  const output = sanitizeStepValue(raw.output ?? raw.content ?? observation)
  const status = normalizeTaskStatus(raw.status, fallbackStatus)
  const normalizedAction = String(sanitizeStepValue(raw.action) || 'Processing')

  return {
    id: stepId,
    agentId: rawAgentId || rawAgentName || 'unknown',
    agentName: rawAgentName,
    agentType: (raw.agentType ?? raw.agent_type) as string | undefined,
    action: normalizedAction,
    status,
    input: thought,
    output,
    observation,
    timestamp: rawTimestamp,
    duration: typeof durationSec === 'number' ? durationSec : undefined,
    startTime: startMs,
    endTime: computedEndMs,
    error: (raw.error as string | undefined),
    stepNumber: safeStepNumber,
  }
}

function normalizeActionForDedupe(action: string): string {
  const match = String(action || '').match(/^\[(\w+)\]\s*(.*)$/)
  return (match?.[2] || action || '').trim().toLowerCase()
}

function normalizeContentForDedupe(value: unknown): string {
  if (value == null) return ''
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  return text.replace(/\s+/g, ' ').trim().toLowerCase().slice(0, 320)
}

function getStepTimestamp(step: TaskStep): number {
  return step.startTime ?? step.timestamp ?? 0
}

function stepFingerprint(step: TaskStep): string {
  const actionKey = normalizeActionForDedupe(step.action)
  const outputKey = normalizeContentForDedupe(step.output ?? step.observation)
  const typeKey = String(step.agentType || '').toLowerCase()
  return [
    String(step.agentId || '').toLowerCase(),
    typeKey,
    actionKey,
    outputKey,
  ].join('|')
}

function isLikelyDuplicateStep(existing: TaskStep, incoming: TaskStep): boolean {
  if (existing.id === incoming.id) return true

  const existingNo = existing.stepNumber
  const incomingNo = incoming.stepNumber
  if (
    typeof existingNo === 'number' &&
    typeof incomingNo === 'number' &&
    existingNo >= 1 &&
    incomingNo >= 1 &&
    existingNo === incomingNo &&
    existing.agentId === incoming.agentId
  ) {
    return true
  }

  const existingTs = getStepTimestamp(existing)
  const incomingTs = getStepTimestamp(incoming)
  if (existingTs > 0 && incomingTs > 0 && Math.abs(existingTs - incomingTs) > 4000) {
    return false
  }

  return stepFingerprint(existing) === stepFingerprint(incoming)
}

function normalizeTaskSteps(rawSteps: unknown, fallbackStatus: TaskStatus): TaskStep[] {
  if (!Array.isArray(rawSteps)) return []
  return rawSteps.map((raw, index) => normalizeTaskStep((raw || {}) as Record<string, unknown>, fallbackStatus, index))
}

function toDbTaskPayload(task: Task): Record<string, unknown> {
  const serializedSteps = (task.steps || []).map((s) => ({
    id: s.id,
    agent_id: s.agentId,
    agent_name: s.agentName,
    agent_type: s.agentType,
    action: s.action,
    status: s.status,
    input: s.input,
    output: s.output,
    content: s.output,
    thought: s.input,
    observation: s.observation,
    timestamp: s.timestamp,
    step_number: s.stepNumber,
    duration: s.duration,
    started_at: s.startTime ? s.startTime / 1000 : undefined,
    completed_at: s.endTime ? s.endTime / 1000 : undefined,
    error: s.error,
  }))

  const metadata: Record<string, unknown> = {
    ...(task.metadata || {}),
  }
  if (task.origin !== undefined) metadata.origin = task.origin
  if (task.requesterId !== undefined) metadata.requester_id = task.requesterId
  if (task.delegatedTo !== undefined) metadata.delegated_to = task.delegatedTo
  if (task.poolMembers !== undefined) metadata.pool_members = task.poolMembers
  if (task.selectedMembers !== undefined) metadata.selected_members = task.selectedMembers
  if (task.executionMode !== undefined) metadata.execution_mode = task.executionMode
  if (task.progress) {
    metadata.progress = {
      phase: task.progress.phase,
      percentage: task.progress.percentage,
      total_workers: task.progress.totalWorkers,
      completed_workers: task.progress.completedWorkers,
      active_workers: task.progress.activeWorkers,
      skipped_workers: task.progress.skippedWorkers,
      phase_label: task.progress.phaseLabel,
      current_agent: task.progress.currentAgent,
      current_action: task.progress.currentAction,
      worker_statuses: task.progress.workerStatuses,
    }
  }

  return {
    id: task.id,
    name: task.name,
    description: task.description,
    status: task.status,
    task_type: task.origin === 'remote' ? 'remote' : 'local',
    assigned_to: task.assignedTo || COORDINATOR_ID,
    delegated_agents: task.delegatedAgents || task.assignedAgents,
    executing_agents: task.executingAgents || [],
    steps: serializedSteps,
    result: typeof task.result === 'string' ? task.result : JSON.stringify(task.result),
    error: task.error,
    cost: task.cost,
    output_dir: task.outputDir,
    created_at: task.createdAt / 1000,
    started_at: task.startedAt ? task.startedAt / 1000 : null,
    completed_at: task.completedAt ? task.completedAt / 1000 : null,
    metadata,
  }
}

function scheduleTaskPersist(get: () => AgentState, taskId: string, delayMs = 400): void {
  const existingTimer = _taskPersistTimers.get(taskId)
  if (existingTimer) clearTimeout(existingTimer)

  const timer = setTimeout(async () => {
    _taskPersistTimers.delete(taskId)
    const task = get().tasks.find(t => t.id === taskId)
    if (!task) return
    try {
      await fetch(`${API_BASE}/api/db/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(toDbTaskPayload(task)),
      })
    } catch (error) {
      console.error('[AgentStore] Failed to persist task snapshot:', error)
    }
  }, delayMs)

  _taskPersistTimers.set(taskId, timer)
}


// Demo data for showcasing the UI - only loaded when explicitly called
// All demo data is prefixed with [Demo] for clarity
const createDemoData = () => {
  const now = Date.now()
  
  // Demo local agents with roles - marked with [Demo] prefix
  const agents: Agent[] = [
    {
      id: 'demo-organizer-1',
      name: '[Demo] Main Organizer',
      type: 'organizer',
      status: 'online',
      capabilities: [
        { name: 'task_planning', description: 'Plans and distributes tasks' },
        { name: 'resource_allocation', description: 'Allocates resources to tasks' },
      ],
      lastSeen: now,
    },
    {
      id: 'demo-coordinator-1',
      name: `[Demo] ${LOCAL_COORDINATOR_NAME}`,
      type: 'coordinator',
      status: 'busy',
      currentTask: 'Coordinating code review',
      capabilities: [
        { name: 'agent_coordination', description: 'Coordinates between agents' },
        { name: 'progress_tracking', description: 'Tracks task progress' },
      ],
      lastSeen: now - 5000,
    },
    {
      id: 'demo-worker-1',
      name: '[Demo] Code Worker',
      type: 'worker',
      status: 'busy',
      currentTask: 'Reviewing auth module',
      capabilities: [
        { name: 'code_review', description: 'Reviews code for quality' },
        { name: 'bug_detection', description: 'Detects potential bugs' },
      ],
      lastSeen: now - 10000,
    },
    {
      id: 'demo-worker-2',
      name: '[Demo] Test Worker',
      type: 'worker',
      status: 'idle',
      capabilities: [
        { name: 'test_generation', description: 'Generates unit tests' },
        { name: 'test_execution', description: 'Executes test suites' },
      ],
      lastSeen: now - 30000,
    },
  ]

  // Demo Remote Agentic Nodes - marked with [Demo] prefix
  const agenticNodes: AgenticNode[] = [
    {
      id: 'demo-an-1',
      name: '[Demo] Documentation AN',
      endpoint: 'https://docs.agentanet.io',
      status: 'online',
      region: 'US-West',
      capabilities: [
        { name: 'doc_generation', description: 'Generates documentation' },
        { name: 'api_docs', description: 'Creates API documentation' },
      ],
      lastSeen: now - 60000,
    },
    {
      id: 'demo-an-2',
      name: '[Demo] Security Scanner AN',
      endpoint: 'https://security.agentanet.io',
      status: 'online',
      region: 'EU-Central',
      capabilities: [
        { name: 'vulnerability_scan', description: 'Scans for vulnerabilities' },
        { name: 'security_audit', description: 'Performs security audits' },
      ],
      lastSeen: now - 120000,
    },
    {
      id: 'demo-an-3',
      name: '[Demo] ML Training AN',
      endpoint: 'https://ml.agentanet.io',
      status: 'offline',
      region: 'Asia-Pacific',
      capabilities: [
        { name: 'model_training', description: 'Trains ML models' },
        { name: 'inference', description: 'Runs model inference' },
      ],
      lastSeen: now - 3600000,
    },
  ]

  // Demo tasks - marked with [Demo] prefix for clarity
  // These are only loaded when loadDemoData() is explicitly called
  const tasks: Task[] = [
    {
      id: 'demo-task-1',
      name: '[Demo] Review Authentication Module',
      description: '[Demo] Security review on the auth module',
      status: 'running',
      assignedAgents: ['demo-coordinator-1', 'demo-worker-1', 'demo-an-2'],
      steps: [
        {
          id: 'demo-step-1',
          agentId: 'demo-worker-1',
          action: 'Analyze code structure',
          status: 'completed',
          startTime: now - 120000,
          endTime: now - 60000,
          output: { files_analyzed: 15, issues_found: 3 },
        },
        {
          id: 'demo-step-2',
          agentId: 'demo-an-2',
          action: 'Security vulnerability scan',
          status: 'running',
          startTime: now - 30000,
        },
      ],
      createdAt: now - 300000,
      startedAt: now - 120000,
    },
    {
      id: 'demo-task-2',
      name: '[Demo] Generate API Documentation',
      description: '[Demo] Create docs for new REST endpoints',
      status: 'pending',
      assignedAgents: ['demo-an-1'],
      steps: [],
      createdAt: now - 60000,
    },
    {
      id: 'demo-task-3',
      name: '[Demo] Code Quality Audit',
      description: '[Demo] Complete code quality audit',
      status: 'completed',
      assignedAgents: ['demo-organizer-1', 'demo-worker-1'],
      steps: [
        {
          id: 'demo-step-3',
          agentId: 'demo-worker-1',
          action: 'Run linting and analysis',
          status: 'completed',
          startTime: now - 7200000,
          endTime: now - 3600000,
          output: { warnings: 12, errors: 0, score: 95 },
        },
      ],
      createdAt: now - 86400000,
      startedAt: now - 7200000,
      completedAt: now - 3600000,
      result: { status: 'passed', score: 95 },
    },
  ]

  // Demo messages between agents
  const messages: AgentMessage[] = [
    {
      id: 'demo-msg-1',
      timestamp: now - 120000,
      fromAgent: 'demo-organizer-1',
      toAgent: 'demo-coordinator-1',
      type: 'request',
      content: '[Demo] Start code review task, coordinate with workers',
    },
    {
      id: 'demo-msg-2',
      timestamp: now - 115000,
      fromAgent: 'demo-coordinator-1',
      toAgent: 'demo-worker-1',
      type: 'request',
      content: '[Demo] Begin analyzing auth module code',
    },
    {
      id: 'demo-msg-3',
      timestamp: now - 60000,
      fromAgent: 'demo-worker-1',
      toAgent: 'demo-coordinator-1',
      type: 'response',
      content: '[Demo] Code analysis complete: Found 3 issues',
      metadata: { issues: ['SQL injection risk', 'Missing validation', 'Weak password policy'] },
    },
    {
      id: 'demo-msg-4',
      timestamp: now - 55000,
      fromAgent: 'demo-coordinator-1',
      toAgent: 'demo-an-2',
      type: 'request',
      content: '[Demo] Run security scan on flagged files',
    },
  ]

  // Demo log entries
  const logs: LogEntry[] = [
    { id: 'demo-log-1', timestamp: now - 300000, level: 'info', message: '[Demo] AgentaNet connection established' },
    { id: 'demo-log-2', timestamp: now - 120000, level: 'info', agentId: 'demo-organizer-1', message: '[Demo] Organizer started task planning' },
    { id: 'demo-log-3', timestamp: now - 115000, level: 'info', agentId: 'demo-coordinator-1', taskId: 'demo-task-1', message: `[Demo] ${LOCAL_COORDINATOR_NAME} assigned workers` },
    { id: 'demo-log-4', timestamp: now - 60000, level: 'info', agentId: 'demo-worker-1', message: '[Demo] Code analysis completed' },
    { id: 'demo-log-5', timestamp: now - 55000, level: 'warn', agentId: 'demo-worker-1', message: '[Demo] Found potential security issues' },
    { id: 'demo-log-6', timestamp: now - 30000, level: 'info', agentId: 'demo-an-2', message: '[Demo] Security scan in progress' },
  ]

  return { agents, agenticNodes, tasks, messages, logs }
}

export const useAgentStore = create<AgentState>()((set, get) => ({
  agents: [],
  agenticNodes: [],
  tasks: [],
  messages: [],
  logs: [],
  selectedAgentId: null,
  selectedTaskId: null,
  isLoadingTasks: false,
  
  // Unread task tracking
  unreadTaskIds: new Set<string>(),
  unreadTaskCount: 0,
  
  markTaskRead: (taskId: string) => {
    const ids = new Set(get().unreadTaskIds)
    ids.delete(taskId)
    set({ unreadTaskIds: ids, unreadTaskCount: ids.size })
  },
  
  markAllTasksRead: () => {
    set({ unreadTaskIds: new Set<string>(), unreadTaskCount: 0 })
  },
  
  // Unread sandbox tracking
  unreadSandboxIds: new Set<string>(),
  unreadSandboxCount: 0,
  
  addUnreadSandbox: (sandboxId: string) => {
    const ids = new Set(get().unreadSandboxIds)
    ids.add(sandboxId)
    set({ unreadSandboxIds: ids, unreadSandboxCount: ids.size })
  },
  
  markSandboxRead: (sandboxId: string) => {
    const ids = new Set(get().unreadSandboxIds)
    ids.delete(sandboxId)
    set({ unreadSandboxIds: ids, unreadSandboxCount: ids.size })
  },
  
  markAllSandboxesRead: () => {
    set({ unreadSandboxIds: new Set<string>(), unreadSandboxCount: 0 })
  },

  // Dashboard approval state
  dashboardApproval: null,
  setDashboardApproval: (approval) => set({ dashboardApproval: approval }),

  // Agent actions (local)
  addAgent: (agent) => {
    const safeType = normalizeAgentType(agent.type)
    const safeStatus = normalizeAgentStatus(agent.status)

    // Check if agent with same id or same name already exists
    const existingById = agent.id ? get().agents.find(a => a.id === agent.id) : null
    const existingByName = get().agents.find(a => a.name === agent.name)
    const existing = existingById || existingByName
    if (existing) {
      // Update existing agent instead
      get().updateAgent(existing.id, { ...agent, type: safeType, status: safeStatus, lastSeen: Date.now() })
      return
    }

    const newAgent: Agent = {
      ...agent,
      type: safeType,
      status: safeStatus,
      id: agent.id || prefixedId('agent', 12),
      lastSeen: Date.now(),
    }
    set((state) => ({ agents: [...state.agents, newAgent] }))
    get().addLog({ level: 'info', agentId: newAgent.id, message: `${newAgent.type} "${newAgent.name}" registered` })
  },

  updateAgent: (id, updates) => {
    const safeUpdates: Partial<Agent> = { ...updates }
    if (Object.prototype.hasOwnProperty.call(safeUpdates, 'type')) {
      safeUpdates.type = normalizeAgentType(safeUpdates.type, 'worker')
    }
    if (Object.prototype.hasOwnProperty.call(safeUpdates, 'status')) {
      safeUpdates.status = normalizeAgentStatus(safeUpdates.status, 'offline')
    }

    set((state) => ({
      agents: state.agents.map((a) =>
        a.id === id ? { ...a, ...safeUpdates, lastSeen: Date.now() } : a
      ),
    }))
  },

  removeAgent: (id) => {
    const agent = get().agents.find((a) => a.id === id)
    set((state) => ({
      agents: state.agents.filter((a) => a.id !== id),
      selectedAgentId: state.selectedAgentId === id ? null : state.selectedAgentId,
    }))
    if (agent) {
      get().addLog({ level: 'info', agentId: id, message: `${agent.type} "${agent.name}" removed` })
    }
  },

  setSelectedAgent: (id) => set({ selectedAgentId: id }),

  // Agentic Node actions
  addAgenticNode: (node) => {
    const newNode: AgenticNode = {
      ...node,
      status: normalizeAgentStatus(node.status, 'offline'),
      id: prefixedId('an', 12),
      lastSeen: Date.now(),
    }
    set((state) => ({ agenticNodes: [...state.agenticNodes, newNode] }))
    get().addLog({ level: 'info', message: `Remote Agentic Node "${newNode.name}" joined via AgentaNet` })
    
    // Send notification
    notify.info(
      'New Remote AN Connected',
      `${newNode.name} joined via AgentaNet`,
      { label: 'View in Dashboard', viewMode: 'dashboard' }
    )
  },

  updateAgenticNode: (id, updates) => {
    const safeUpdates: Partial<AgenticNode> = { ...updates }
    if (Object.prototype.hasOwnProperty.call(safeUpdates, 'status')) {
      safeUpdates.status = normalizeAgentStatus(safeUpdates.status, 'offline')
    }
    set((state) => ({
      agenticNodes: state.agenticNodes.map((n) =>
        n.id === id ? { ...n, ...safeUpdates, lastSeen: Date.now() } : n
      ),
    }))
  },

  removeAgenticNode: (id) => {
    const node = get().agenticNodes.find((n) => n.id === id)
    set((state) => ({
      agenticNodes: state.agenticNodes.filter((n) => n.id !== id),
      selectedAgentId: state.selectedAgentId === id ? null : state.selectedAgentId,
    }))
    if (node) {
      get().addLog({ level: 'info', message: `Remote Agentic Node "${node.name}" disconnected from AgentaNet` })
    }
  },

  // Task actions
  createTask: (task) => {
    // Use provided ID (from backend) or generate new one
    const taskId = task.id || prefixedId('task', 12)
    const safeStatus = normalizeTaskStatus(task.status, 'pending')
    const newTask: Task = {
      ...task,
      id: taskId,
      status: safeStatus,
      steps: [],
      createdAt: task.createdAt || Date.now(),
      assignedAgents: task.assignedAgents || [],
    }
    // Check if task with this ID already exists
    const existing = get().tasks.find(t => t.id === taskId)
    if (existing) {
      // Update existing task instead of creating duplicate
      get().updateTask(taskId, { ...task, status: normalizeTaskStatus(task.status, existing.status || 'pending') })
      return taskId
    }
    set((state) => ({ tasks: [newTask, ...state.tasks] }))
    scheduleTaskPersist(get, newTask.id, 0)
    // Mark as unread
    const ids = new Set(get().unreadTaskIds)
    ids.add(newTask.id)
    set({ unreadTaskIds: ids, unreadTaskCount: ids.size })
    get().addLog({ level: 'info', taskId: newTask.id, message: `Task "${newTask.name}" created` })
    return newTask.id
  },

  updateTask: (id, updates) => {
    const task = get().tasks.find(t => t.id === id)
    const wasRunning = task?.status === 'running' || task?.status === 'delegated'
    const safeUpdates: Partial<Task> = { ...updates }
    if (Object.prototype.hasOwnProperty.call(safeUpdates, 'status')) {
      safeUpdates.status = normalizeTaskStatus(safeUpdates.status, task?.status || 'pending')
    }
    
    set((state) => ({
      tasks: state.tasks.map((t) => (t.id === id ? { ...t, ...safeUpdates } : t)),
    }))
    scheduleTaskPersist(get, id)
    
    // Mark task as unread when it completes or fails (notification handled by AgentEventBridge)
    if (wasRunning && (safeUpdates.status === 'completed' || safeUpdates.status === 'failed')) {
      const ids = new Set(get().unreadTaskIds)
      ids.add(id)
      set({ unreadTaskIds: ids, unreadTaskCount: ids.size })
    }
  },

  addTaskStep: (taskId, step) => {
    if (!taskId) {
      debugLog('[AgentStore] addTaskStep skipped: empty taskId')
      return
    }
    const existingTask = get().tasks.find(t => t.id === taskId)
    const hasServerStepNo = typeof step.stepNumber === 'number' && Number.isFinite(step.stepNumber)
    const normalizeIndex = hasServerStepNo
      ? Math.max(0, Number(step.stepNumber) - 1)
      : (existingTask?.steps.length ?? 0)
    const newStep: TaskStep = normalizeTaskStep(
      { ...step, id: (step as TaskStep).id || prefixedId('step', 12) } as Record<string, unknown>,
      (step.status as TaskStatus | undefined) || 'running',
      normalizeIndex,
    )
    if (existingTask?.steps?.some(s => isLikelyDuplicateStep(s, newStep))) {
      debugLog(`[AgentStore] addTaskStep skipped: duplicate signature for task ${taskId}`)
      return
    }
    set((state) => ({
      tasks: state.tasks.map((t) =>
        t.id === taskId ? { ...t, steps: [...t.steps, newStep] } : t
      ),
    }))
    scheduleTaskPersist(get, taskId)
  },

  updateTaskStep: (taskId, stepId, updates) => {
    set((state) => ({
      tasks: state.tasks.map((t) =>
        t.id === taskId
          ? { ...t, steps: t.steps.map((s) => (s.id === stepId ? { ...s, ...updates } : s)) }
          : t
      ),
    }))
    scheduleTaskPersist(get, taskId)
  },

  updateTaskProgress: (taskId, progressUpdates) => {
    set((state) => ({
      tasks: state.tasks.map((t) => {
        if (t.id !== taskId) return t
        const prev = t.progress || {
          phase: 'received' as TaskPhase,
          percentage: 0,
          totalWorkers: 0,
          completedWorkers: 0,
          activeWorkers: 0,
          skippedWorkers: 0,
          phaseLabel: 'Received',
          workerStatuses: [],
        }
        return { ...t, progress: mergeTaskProgressState(prev, progressUpdates) }
      }),
    }))
    scheduleTaskPersist(get, taskId)
  },

  setSelectedTask: (id) => {
    set({ selectedTaskId: id })
    // Mark task as read when viewed
    if (id) {
      get().markTaskRead(id)
    }
  },

  deleteTask: async (id: string) => {
    const pendingTimer = _taskPersistTimers.get(id)
    if (pendingTimer) {
      clearTimeout(pendingTimer)
      _taskPersistTimers.delete(id)
    }

    // Delete from store
    set((state) => ({
      tasks: state.tasks.filter(t => t.id !== id),
      selectedTaskId: state.selectedTaskId === id ? null : state.selectedTaskId,
    }))
    
    // Delete from database
    try {
      await fetch(`${API_BASE}/api/db/tasks/${id}`, { method: 'DELETE' })
      debugLog(`[AgentStore] Task ${id} deleted from database`)
    } catch (error) {
      console.error('[AgentStore] Failed to delete task from DB:', error)
    }
    
    get().addLog({ level: 'info', taskId: id, message: `Task deleted` })
  },

  clearAllTasks: async () => {
    for (const timer of _taskPersistTimers.values()) clearTimeout(timer)
    _taskPersistTimers.clear()
    try {
      await fetch(`${API_BASE}/api/db/tasks`, { method: 'DELETE' })
      set({ tasks: [], selectedTaskId: null })
      debugLog('[AgentStore] All tasks cleared')
    } catch (error) {
      console.error('[AgentStore] Failed to clear tasks:', error)
    }
  },

  archiveTask: (id: string) => {
    // Mark task as archived (we use 'cancelled' status for archived tasks)
    set((state) => ({
      tasks: state.tasks.map(t => 
        t.id === id ? { ...t, status: 'cancelled' as TaskStatus } : t
      ),
    }))
    scheduleTaskPersist(get, id)
    get().addLog({ level: 'info', taskId: id, message: `Task archived` })
  },

  // Database sync actions
  loadTasksFromDB: async () => {
    const isFirstLoad = get().tasks.length === 0
    if (isFirstLoad) set({ isLoadingTasks: true })
    try {
      const response = await fetch(`${API_BASE}/api/db/tasks`)
      if (!response.ok) throw new Error('Failed to fetch tasks')
      const data = await response.json()
      const dbTasks: Task[] = (data.tasks || []).map((t: Record<string, unknown>) => mapBackendTaskRecord(t))
      
      const dbById = new Map(dbTasks.map(t => [t.id, t]))
      const inMemById = new Map(get().tasks.map(t => [t.id, t]))

      // Merge DB + in-memory: for tasks in both, prefer the version with
      // more steps so that SSE-accumulated steps aren't lost by a stale DB
      // record. Also preserve in-memory-only fields (progress, poolMembers).
      const mergedById = new Map<string, Task>()
      for (const [id, dbT] of dbById) {
        const memT = inMemById.get(id)
        if (memT) {
          const useMemSteps = (memT.steps?.length || 0) >= (dbT.steps?.length || 0)
          mergedById.set(id, {
            ...dbT,
            steps: useMemSteps ? memT.steps : dbT.steps,
            progress: memT.progress || dbT.progress,
            metadata: memT.metadata || dbT.metadata,
            poolMembers: memT.poolMembers || dbT.poolMembers,
            executingAgents: memT.executingAgents || dbT.executingAgents,
            delegatedAgents: (memT.delegatedAgents !== undefined ? memT.delegatedAgents : dbT.delegatedAgents),
          })
        } else {
          mergedById.set(id, dbT)
        }
      }
      // Keep in-memory tasks that have no DB entry (active or recently added)
      for (const [id, memT] of inMemById) {
        if (!mergedById.has(id)) {
          mergedById.set(id, memT)
        }
      }
      const mergedTasks = [...mergedById.values()]
        .sort((a, b) => (b.createdAt ?? 0) - (a.createdAt ?? 0))

      // Skip state update if nothing changed (prevents unnecessary re-renders)
      const prev = get().tasks
      const changed =
        prev.length !== mergedTasks.length ||
        prev.some((t, i) => {
          const m = mergedTasks[i]
          return !m || t.id !== m.id || t.status !== m.status || t.steps.length !== m.steps.length
        })
      if (changed) {
        set({ tasks: mergedTasks })
        debugLog(`[AgentStore] Synced ${dbTasks.length} tasks from database`)
      }
      if (isFirstLoad) set({ isLoadingTasks: false })
    } catch (error) {
      console.error('[AgentStore] Failed to load tasks from DB:', error)
      if (isFirstLoad) set({ isLoadingTasks: false })
    }
  },

  saveTaskToDB: async (task: Task) => {
    try {
      const dbTask = toDbTaskPayload(task)
      
      await fetch(`${API_BASE}/api/db/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(dbTask),
      })
      debugLog(`[AgentStore] Task ${task.id} saved to database`)
    } catch (error) {
      console.error('[AgentStore] Failed to save task to DB:', error)
    }
  },

  applySnapshot: (snapshot: { tasks: any[] }) => {
    const current = get().tasks
    const snapshotById = new Map(snapshot.tasks.map((t: any) => [t.id, t]))

    // Map a snapshot task (snake_case from server) to the frontend Task shape
    const mapSnap = (t: any): Task => mapBackendTaskRecord(t as Record<string, unknown>)

    // Merge: for tasks in both, take richer steps but preserve in-memory-only fields
    const merged = current.map((t) => {
      const snap = snapshotById.get(t.id)
      if (!snap) return t
      const snapTask = mapSnap(snap)
      const useMemSteps = (t.steps?.length || 0) >= (snapTask.steps?.length || 0)
      return {
        ...(useMemSteps ? t : snapTask),
        steps: useMemSteps ? t.steps : snapTask.steps,
        progress: t.progress || snapTask.progress,
        metadata: t.metadata || snapTask.metadata,
        poolMembers: t.poolMembers || snapTask.poolMembers,
        executingAgents: t.executingAgents || snapTask.executingAgents,
        selectedMembers: t.selectedMembers || snapTask.selectedMembers,
      }
    })

    // Add snapshot tasks that are not yet in memory
    for (const [id, snap] of snapshotById) {
      if (!merged.find((t) => t.id === id)) {
        merged.push(mapSnap(snap))
      }
    }

    merged.sort((a, b) => (b.createdAt ?? 0) - (a.createdAt ?? 0))
    set({ tasks: merged })
  },

  // Message actions
  addMessage: (message) => {
    const newMessage: AgentMessage = {
      ...message,
      id: prefixedId('msg', 12),
      timestamp: Date.now(),
    }
    set((state) => ({ messages: [...state.messages, newMessage].slice(-500) }))
  },

  clearMessages: () => set({ messages: [] }),

  // Log actions
  addLog: (log) => {
    const newLog: LogEntry = {
      ...log,
      id: prefixedId('log', 12),
      timestamp: Date.now(),
    }
    set((state) => ({ logs: [...state.logs, newLog].slice(-1000) }))
  },

  clearLogs: () => set({ logs: [] }),

  // Get all agents (local + agentic nodes) for unified display
  getAllAgents: () => {
    const state = get()
    const localAgents = state.agents
    const remoteNodes: Agent[] = state.agenticNodes.map(n => ({
      id: n.id,
      name: n.name,
      type: 'agentic_node' as AgentType,
      endpoint: n.endpoint,
      status: normalizeAgentStatus(n.status, 'offline'),
      capabilities: n.capabilities,
      lastSeen: n.lastSeen,
      metadata: { ...n.metadata, region: n.region },
    }))
    return [...localAgents, ...remoteNodes]
  },

  // Agent DB persistence — batched merge in one set() to avoid N re-renders
  loadAgentsFromDB: async () => {
    try {
      const response = await fetch(`${API_BASE}/api/agent/agents`)
      if (!response.ok) throw new Error('Failed to fetch agents')
      const data = await response.json()
      const dbAgents: Agent[] = (data.agents || []).map((a: Record<string, unknown>) => ({
        id: a.id as string,
        name: a.name as string || 'Agent',
        type: normalizeAgentType(a.type, 'worker'),
        status: normalizeAgentStatus(a.status, 'offline'),
        capabilities: (a.capabilities as AgentCapability[]) || [],
        endpoint: a.endpoint as string | undefined,
        lastSeen: Date.now(),
        goal: a.goal as string | undefined,
        backstory: a.backstory as string | undefined,
        model: a.model as string | undefined,
        tools: a.tools as string[] | undefined,
        system_prompt: a.system_prompt as string | undefined,
        allow_delegation: a.allow_delegation as boolean | undefined,
        metadata: a.metadata as Record<string, unknown> | undefined,
      }))

      set((state) => {
        const byId = new Map(state.agents.map(a => [a.id, a]))
        const byName = new Map(state.agents.map(a => [a.name, a]))
        let changed = false

        // Update existing agents and collect truly new ones
        const updated = state.agents.map(existing => {
          const dbMatch = dbAgents.find(d => d.id === existing.id || d.name === existing.name)
          if (!dbMatch) return existing
          const merged = {
            ...existing,
            capabilities: dbMatch.capabilities,
            goal: dbMatch.goal,
            backstory: dbMatch.backstory,
            model: dbMatch.model,
            tools: dbMatch.tools,
            system_prompt: dbMatch.system_prompt,
            allow_delegation: dbMatch.allow_delegation,
          }
          if (JSON.stringify(merged) !== JSON.stringify(existing)) {
            changed = true
            return merged
          }
          return existing
        })

        const newAgents = dbAgents.filter(d => !byId.has(d.id) && !byName.has(d.name))
        if (newAgents.length > 0) changed = true

        if (!changed) return state
        return { agents: [...updated, ...newAgents] }
      })
      debugLog(`[AgentStore] Synced ${dbAgents.length} agents from API`)
    } catch (error) {
      console.error('[AgentStore] Failed to load agents from DB:', error)
    }
  },

  saveAgentToDB: async (agent: Agent) => {
    try {
      await fetch(`${API_BASE}/api/agent/agents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: agent.id,
          name: agent.name,
          type: agent.type,
          status: agent.status,
          capabilities: agent.capabilities,
          endpoint: agent.endpoint,
          model: agent.model,
          goal: agent.goal,
          backstory: agent.backstory,
          tools: agent.tools,
          system_prompt: agent.system_prompt,
          allow_delegation: agent.allow_delegation,
        }),
      })
      debugLog(`[AgentStore] Agent ${agent.id} saved to database`)
    } catch (error) {
      console.error('[AgentStore] Failed to save agent to DB:', error)
    }
  },

  deleteAgentFromDB: async (agentId: string) => {
    try {
      await fetch(`${API_BASE}/api/agent/agents/${agentId}`, { method: 'DELETE' })
      debugLog(`[AgentStore] Agent ${agentId} deleted from database`)
    } catch (error) {
      console.error('[AgentStore] Failed to delete agent from DB:', error)
    }
  },

  updateAgentInDB: async (agentId: string, updates: Partial<Agent>) => {
    try {
      await fetch(`${API_BASE}/api/agent/agents/${agentId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      })
      debugLog(`[AgentStore] Agent ${agentId} updated in database`)
    } catch (error) {
      console.error('[AgentStore] Failed to update agent in DB:', error)
    }
  },

  // Load demo data
  loadDemoData: () => {
    const demo = createDemoData()
    set({
      agents: demo.agents,
      agenticNodes: demo.agenticNodes,
      tasks: demo.tasks,
      messages: demo.messages,
      logs: demo.logs,
    })
    
    // Send notification about demo data
    notify.info(
      'Demo Data Loaded',
      `${demo.agents.length} agents, ${demo.agenticNodes.length} remote ANs, ${demo.tasks.length} tasks`,
      { label: 'Explore Dashboard', viewMode: 'dashboard' }
    )
  },
}))
