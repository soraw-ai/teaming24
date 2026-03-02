/**
 * WSEventBridge — Connects the WebSocket store to the existing Zustand stores.
 *
 * On mount, initiates the WS connection and subscribes to all events that
 * the SSE-based AgentEventBridge and NetworkEventBridge handle.  Dispatches
 * events to the same agentStore / networkStore / walletStore actions so the
 * dashboard renders identically regardless of transport.
 *
 * This runs alongside the SSE bridges. Duplicate events (when both SSE and WS
 * receive the same broadcast) are de-duped in addTaskStep using semantic signatures.
 */

import { useEffect, useRef } from 'react'
import { useWSStore } from '../../store/wsStore'
import { useAgentStore, type TaskPhase, type WorkerStatusSummary } from '../../store/agentStore'
import { useChatStore } from '../../store/chatStore'
import { ORGANIZER_ID, COORDINATOR_ID } from '../../utils/ids'
import { toMilliseconds } from '../../utils/date'
import { debugLog } from '../../utils/debug'

function normalizePhase(phase?: string): TaskPhase {
  if (!phase) return 'received'
  if (phase === 'planning') return 'routing'
  if (phase === 'delegating') return 'dispatching'
  if (['received', 'routing', 'dispatching', 'executing', 'aggregating', 'completed'].includes(phase)) {
    return phase as TaskPhase
  }
  return 'received'
}

function resolveAgentId(payload: Record<string, unknown>, step: Record<string, unknown>): string {
  const state = useAgentStore.getState()
  const candidateId = (step.agent_id ?? payload.agent_id) as string | undefined
  const candidateName = (step.agent ?? payload.agent) as string | undefined
  if (candidateId && state.agents.some(a => a.id === candidateId)) return candidateId
  if (candidateName) {
    const byName = state.agents.find(a => a.name === candidateName)
    if (byName) return byName.id
  }
  return candidateId || candidateName || ORGANIZER_ID
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

export default function WSEventBridge() {
  const connect = useWSStore((s) => s.connect)
  const subscribe = useWSStore((s) => s.subscribe)
  const mounted = useRef(false)

  useEffect(() => {
    if (mounted.current) return
    mounted.current = true

    connect()

    const unsubs: (() => void)[] = []
    const toMs = toMilliseconds

    // Agent / task events → agentStore (payload format matches SSE broadcast: { task, agent_id, agent_type, ... })
    unsubs.push(
      subscribe('task_step', (payload: Record<string, unknown>) => {
        debugLog('[WSBridge] task_step', payload)
        const taskData = payload.task as Record<string, unknown> | undefined
        if (!taskData) return
        const taskId = taskData.id as string
        if (!taskId) return
        const steps = taskData.steps as Array<Record<string, unknown>> | undefined
        const step = steps?.[steps.length - 1]
        if (!step) return

        const store = useAgentStore.getState()
        if (typeof store.addTaskStep !== 'function') return

        // Ensure task exists (defensive — task_created may have been missed)
        const exists = store.tasks.find((t: { id: string }) => t.id === taskId)
        if (!exists) {
          store.createTask({
            id: taskId,
            name: 'Task',
            description: '',
            assignedAgents: [COORDINATOR_ID],
            status: 'running',
          })
        }

        const agentId = resolveAgentId(payload, step)
        const agentType = (step.agent_type ?? payload.agent_type ?? 'worker') as string
        const action = (step.action ?? 'Processing') as string
        const actionLower = String(action).toLowerCase()
        const content = sanitizeStepText(step.content ?? '')
        const isHeartbeatStep = actionLower === 'tool_heartbeat' || actionLower === 'worker_heartbeat'

        if (!isHeartbeatStep) {
          store.addTaskStep(taskId, {
            agentId,
            agentName: (step.agent as string | undefined),
            agentType,
            action: `[${agentType}] ${action}`,
            status: 'running',
            input: step.thought,
            output: content,
            observation: step.observation,
            timestamp: toMs(step.timestamp as number) || Date.now(),
            duration: (step.step_duration as number | undefined),
            startTime: toMs(step.timestamp as number) || Date.now(),
            stepNumber: (step.step_number as number) ?? 0,
            endTime: step.step_duration && step.timestamp
              ? (toMs(step.timestamp as number)! + (step.step_duration as number) * 1000)
              : undefined,
          })
        }

        // Apply progress if available
        const prog = taskData.progress as Record<string, unknown> | undefined
        if (prog && typeof store.updateTaskProgress === 'function') {
          store.updateTaskProgress(taskId, {
            phase: normalizePhase((prog.phase as string) || 'executing'),
            percentage: (prog.percentage as number) ?? 0,
            totalWorkers: (prog.total_workers as number) ?? 0,
            completedWorkers: (prog.completed_workers as number) ?? 0,
            activeWorkers: (prog.active_workers as number) ?? 0,
            skippedWorkers: (prog.skipped_workers as number) ?? 0,
            phaseLabel: (prog.phase_label as string) || action,
            currentAgent: (prog.current_agent as string) || '',
            currentAction: (prog.current_action as string) || '',
            workerStatuses: normalizeWorkerStatuses((prog.worker_statuses as unknown) ?? (prog.workerStatuses as unknown)),
          })
        }
      }),
    )

    unsubs.push(
      subscribe('task_completed', (payload: Record<string, unknown>) => {
        debugLog('[WSBridge] task_completed', payload)
        const taskData = payload.task as Record<string, unknown> | undefined
        if (!taskData?.id) return
        const store = useAgentStore.getState()
        const taskId = taskData.id as string
        const exists = store.tasks.find((t: { id: string }) => t.id === taskId)
        if (!exists) {
          store.createTask({
            id: taskId,
            name: ((taskData.prompt ?? taskData.description ?? 'Task') as string).substring(0, 50),
            description: (taskData.prompt ?? taskData.description ?? '') as string,
            assignedAgents: [COORDINATOR_ID],
            status: 'completed',
            result: taskData.result,
            completedAt: toMilliseconds(taskData.completed_at as number) || Date.now(),
          })
        } else {
          store.updateTask(taskId, {
            status: 'completed',
            result: taskData.result,
            completedAt: toMilliseconds(taskData.completed_at as number),
          })
        }
        const prog = taskData.progress as Record<string, unknown> | undefined
        if (prog) {
          store.updateTaskProgress(taskId, {
            phase: 'completed',
            percentage: 100,
            totalWorkers: (prog.total_workers as number) ?? 0,
            completedWorkers: (prog.completed_workers as number) ?? (prog.total_workers as number) ?? 0,
            activeWorkers: 0,
            skippedWorkers: (prog.skipped_workers as number) ?? 0,
            phaseLabel: 'Completed',
            currentAgent: (prog.current_agent as string) || '',
            currentAction: (prog.current_action as string) || '',
            workerStatuses: normalizeWorkerStatuses((prog.worker_statuses as unknown) ?? (prog.workerStatuses as unknown)),
          })
        }
        useChatStore.getState().onTaskCompleted?.(taskId)
      }),
    )

    unsubs.push(
      subscribe('task_failed', (payload: Record<string, unknown>) => {
        debugLog('[WSBridge] task_failed', payload)
        const taskData = payload.task as Record<string, unknown> | undefined
        if (!taskData?.id) return
        const store = useAgentStore.getState()
        const taskId = taskData.id as string
        const existing = store.tasks.find((t: { id: string }) => t.id === taskId)
        if (!existing) {
          store.createTask({
            id: taskId,
            name: ((taskData.prompt ?? taskData.description ?? 'Task') as string).substring(0, 50),
            description: (taskData.prompt ?? taskData.description ?? '') as string,
            assignedAgents: [ORGANIZER_ID],
            status: 'failed',
            error: taskData.error as string,
            completedAt: toMilliseconds(taskData.completed_at as number) || Date.now(),
          })
        }
        store.updateTask(taskId, {
          status: 'failed',
          error: taskData.error as string,
          completedAt: toMilliseconds(taskData.completed_at as number),
        })
        const prog = taskData.progress as Record<string, unknown> | undefined
        if (prog) {
          store.updateTaskProgress(taskId, {
            phase: 'completed',
            percentage: 100,
            totalWorkers: (prog.total_workers as number) ?? 0,
            completedWorkers: (prog.completed_workers as number) ?? 0,
            activeWorkers: 0,
            skippedWorkers: (prog.skipped_workers as number) ?? 0,
            phaseLabel: 'Failed',
            currentAgent: (prog.current_agent as string) || '',
            currentAction: (prog.current_action as string) || '',
            workerStatuses: normalizeWorkerStatuses((prog.worker_statuses as unknown) ?? (prog.workerStatuses as unknown)),
          })
        }
        useChatStore.getState().onTaskCompleted?.(taskId)
      }),
    )

    // Network events → networkStore
    unsubs.push(
      subscribe('network_status', (payload) => {
        debugLog('[WSBridge] network_status', payload)
      }),
    )

    unsubs.push(
      subscribe('peer_connected', (payload) => {
        debugLog('[WSBridge] peer_connected', payload)
      }),
    )

    unsubs.push(
      subscribe('peer_disconnected', (payload) => {
        debugLog('[WSBridge] peer_disconnected', payload)
      }),
    )

    // Session events (new)
    unsubs.push(
      subscribe('session_created', (payload) => {
        debugLog('[WSBridge] session_created', payload)
      }),
    )

    unsubs.push(
      subscribe('session_reset', (payload) => {
        debugLog('[WSBridge] session_reset', payload)
      }),
    )

    // Channel events (new)
    unsubs.push(
      subscribe('channel_status', (payload) => {
        debugLog('[WSBridge] channel_status', payload)
      }),
    )

    return () => {
      unsubs.forEach((u) => u())
    }
  }, [connect, subscribe])

  return null
}
