/**
 * TaskTopology — Real-time task flow visualization.
 *
 * Renders an SVG-based topology for a single task.
 *
 * Local tasks (from chat):
 *   Organizer → ANRouter → [SELECTED pool members only]
 *                         → local team coordinator → Workers
 *                         → Remote AN 1  (HTTP)
 *                         → Remote AN 2  (HTTP)
 *
 *   ANRouter selects from the Agentic Node Workforce Pool (local team coordinator + Remote ANs).
 *   Only selected members appear in the topology.
 *   Local workers are assigned by the Coordinator internally, NOT by ANRouter.
 *
 * Remote tasks (received via HTTP from another AN):
 *   Remote Requester → local team coordinator → Workers
 *
 * Each node displays its current status with animated indicators.
 * Connection lines show the task flow direction.
 *
 * Supports fullscreen mode with zoom/pan.
 */

import { useMemo, useState, useRef, useCallback, useEffect } from 'react'
import { createPortal } from 'react-dom'
import clsx from 'clsx'
import type { Task, Agent, TaskStep } from '../../store/agentStore'
import { useAgentStore } from '../../store/agentStore'
import { COORDINATOR_ID, LOCAL_COORDINATOR_NAME } from '../../utils/ids'
import {
  ArrowsPointingOutIcon,
  ArrowsPointingInIcon,
  PlusIcon,
  MinusIcon,
  ArrowPathIcon,
} from '@heroicons/react/24/outline'

// --- Types ---

type NodeStatus = 'idle' | 'selected' | 'executing' | 'completed' | 'failed' | 'pending'

interface TopoNode {
  id: string
  label: string
  type: 'organizer' | 'router' | 'coordinator' | 'remote' | 'worker' | 'refine'
  status: NodeStatus
  round?: number
  detail?: string          // e.g. ip:port, capability
  anId?: string            // canonical AN identifier (shown as small text)
  x: number
  y: number
  h?: number               // computed height (defaults to NODE_H)
}

interface TopoEdge {
  from: string
  to: string
  status: 'idle' | 'active' | 'done' | 'error' | 'return'
  label?: string
  curved?: 'left' | 'right'  // return-flow edges curve to one side
}

// --- Constants ---

const NODE_W = 148
const NODE_H = 42
const ROW_GAP = 82
const COL_GAP = 16
const BADGE_R = 6
const NODE_MARGIN = 12

// --- Color palette ---

const palette: Record<string, { bg: string; border: string; text: string; badge: string }> = {
  organizer:   { bg: '#6d28d9', border: '#7c3aed', text: '#e9d5ff', badge: '#a78bfa' },
  router:      { bg: '#1e40af', border: '#3b82f6', text: '#bfdbfe', badge: '#60a5fa' },
  coordinator: { bg: '#0e7490', border: '#06b6d4', text: '#cffafe', badge: '#22d3ee' },
  remote:      { bg: '#065f46', border: '#10b981', text: '#d1fae5', badge: '#34d399' },
  worker:      { bg: '#374151', border: '#6b7280', text: '#d1d5db', badge: '#9ca3af' },
  refine:      { bg: '#7c2d12', border: '#ea580c', text: '#fed7aa', badge: '#fb923c' },
}

const statusColor: Record<NodeStatus, string> = {
  idle:      '#4b5563',
  selected:  '#f59e0b',
  pending:   '#f59e0b',
  executing: '#3b82f6',
  completed: '#10b981',
  failed:    '#ef4444',
}

const edgeColor: Record<string, string> = {
  idle:   '#94a3b8',   // lighter slate for visibility
  active: '#60a5fa',
  done:   '#34d399',
  error:  '#f87171',
  return: '#a78bfa',
}

const ROUND_ACCENTS = ['#f59e0b', '#ec4899', '#8b5cf6', '#14b8a6', '#84cc16']

function getRoundAccent(round?: number): string | null {
  if (!round || round <= 1) return null
  return ROUND_ACCENTS[(round - 2) % ROUND_ACCENTS.length]
}

interface NodeOffset {
  x: number
  y: number
}

function applyNodeOffsets(nodes: TopoNode[], offsets: Record<string, NodeOffset>): TopoNode[] {
  return nodes.map((n) => {
    const o = offsets[n.id]
    if (!o) return n
    return { ...n, x: n.x + o.x, y: n.y + o.y }
  })
}

/**
 * Resolve accidental node collisions from dynamic topology growth.
 * Keeps Y mostly stable and nudges X first, then Y as fallback.
 */
function resolveNodeOverlaps(nodes: TopoNode[]): TopoNode[] {
  if (nodes.length <= 1) return nodes
  const laid = nodes.map(n => ({ ...n }))
  const maxIter = 12

  const overlaps = (a: TopoNode, b: TopoNode): boolean => {
    const ah = a.h || NODE_H
    const bh = b.h || NODE_H
    const dx = Math.abs(a.x - b.x)
    const dy = Math.abs(a.y - b.y)
    return dx < (NODE_W + NODE_MARGIN) && dy < ((ah + bh) / 2 + NODE_MARGIN)
  }

  for (let it = 0; it < maxIter; it += 1) {
    let moved = false
    for (let i = 0; i < laid.length; i += 1) {
      for (let j = i + 1; j < laid.length; j += 1) {
        const a = laid[i]
        const b = laid[j]
        if (!overlaps(a, b)) continue

        const dir = a.x <= b.x ? 1 : -1
        a.x -= 8 * dir
        b.x += 8 * dir
        if (Math.abs(a.x - b.x) < NODE_W / 2) {
          a.y -= 6
          b.y += 6
        }
        moved = true
      }
    }
    if (!moved) break
  }
  return laid
}

// --- Helper: derive topology from task data ---

function resolveAgentRef(ref: string, agentNames: Map<string, string>): string {
  if (!ref) return ref
  if (agentNames.has(ref)) return ref
  for (const [id, name] of agentNames.entries()) {
    if (name === ref) return id
  }
  return ref
}

function extractRoundNumber(step: Pick<TaskStep, 'action' | 'input' | 'output'>): number | null {
  const action = String(step.action || '').toLowerCase()
  if (!action) return null
  const roundishAction = (
    action.includes('round') ||
    action.includes('refine') ||
    action === 'local_refine'
  )
  if (!roundishAction) return null

  const sourceText = [
    typeof step.output === 'string' ? step.output : String(step.output ?? ''),
    typeof step.input === 'string' ? step.input : String(step.input ?? ''),
    action,
  ].join(' ')
  const match = sourceText.match(/round\s*(\d+)/i)
  if (!match) return null
  const parsed = Number.parseInt(match[1], 10)
  return Number.isFinite(parsed) && parsed >= 1 ? parsed : null
}

function getRefinementRounds(steps: TaskStep[]): number[] {
  const rounds = new Set<number>()
  for (const step of steps) {
    const round = extractRoundNumber(step)
    if (round && round > 1) rounds.add(round)
  }
  return [...rounds].sort((a, b) => a - b)
}

function buildTopology(task: Task, agentNames: Map<string, string>, agents: Agent[]): { nodes: TopoNode[]; edges: TopoEdge[] } {
  const isRemoteTask = task.origin === 'remote'
  return isRemoteTask
    ? buildRemoteTaskTopology(task, agentNames, agents)
    : buildLocalTaskTopology(task, agentNames, agents)
}

/**
 * Determine which remote ANs from the pool were actually **selected** by the
 * ANRouter.
 *
 * Uses the definitive `task.selectedMembers` list emitted by the backend's
 * ``routing_decision`` step.  Falls back to heuristic matching from steps
 * only when that data is not yet available (e.g. still routing).
 */
function getSelectedRemoteIds(
  task: Task,
  remoteANs: Array<{ id: string; name: string; an_id?: string | null; ip?: string | null; port?: number | null }>,
): Set<string> {
  const selected = new Set<string>()

  // ── Primary: use definitive selectedMembers from ANRouter ──
  // Always return when selectedMembers is available — even if no remote ANs matched
  // (e.g. only local coordinator was selected). This prevents the fallback heuristic
  // from incorrectly adding remote ANs and inflating the selected count.
  if (task.selectedMembers?.length) {
    const memberSet = new Set(task.selectedMembers)
    for (const an of remoteANs) {
      if (memberSet.has(an.id) || (an.an_id && memberSet.has(an.an_id))) {
        selected.add(an.id)
      }
    }
    return selected
  }

  // ── Fallback: infer from task delegation data / steps ──
  const matchAN = (ref: string | undefined | null) => {
    if (!ref) return
    for (const an of remoteANs) {
      if (ref === an.id || ref === an.name || ref === an.an_id) {
        selected.add(an.id)
      }
    }
  }

  matchAN(task.delegatedTo)

  for (const s of task.steps) {
    const action = s.action?.toLowerCase() || ''
    const isDispatch = action.includes('dispatch') || action.includes('delegat') || action.includes('receiving')
    const agentId = s.agentId || ''
    const output = String(s.output ?? '')

    matchAN(agentId)

    if (isDispatch && output) {
      for (const an of remoteANs) {
        if (output.includes(an.id)) {
          selected.add(an.id)
        } else if (an.ip && an.port && output.includes(`${an.ip}:${an.port}`)) {
          selected.add(an.id)
        } else if (an.an_id && output.includes(an.an_id)) {
          selected.add(an.id)
        }
      }
    }
  }

  for (const ref of [...(task.executingAgents || []), ...(task.delegatedAgents || [])]) {
    matchAN(ref)
  }

  return selected
}

/**
 * Whether the local team coordinator was selected by the ANRouter.
 * Uses `task.selectedMembers` if available, else infers from plan/steps.
 *
 * IMPORTANT: the fallback must NOT assume selection just because the task is
 * "running" — that would produce a premature "selected: 1/1" before the
 * routing decision arrives.
 */
function isLocalCoordinatorSelected(task: Task): boolean {
  if (task.selectedMembers?.length) {
    return task.selectedMembers.includes(COORDINATOR_ID)
  }
  // Fallback: only if concrete evidence exists (local_start/local_done steps,
  // or workers have been delegated/are executing)
  const hasLocalSteps = task.steps.some(s => {
    const action = s.action?.toLowerCase() || ''
    return action.includes('local_start') || action.includes('local_done')
  })
  if (hasLocalSteps) return true
  // Workers being delegated or executing implies the coordinator was selected
  if ((task.delegatedAgents || []).length > 0) return true
  if ((task.executingAgents || []).length > 0) return true
  return false
}

/**
 * Build a positive set of known worker agent IDs and names from the store.
 *
 * Only agents with `type === 'worker'` are included. This avoids the fragile
 * negative-matching approach (trying to exclude organizer/router/coordinator
 * patterns) that breaks when backend steps reference entities not registered
 * in the agent store (e.g. "ANRouter").
 */
function buildKnownWorkerSet(agents: Agent[]): Set<string> {
  const workers = new Set<string>()
  for (const a of agents) {
    if (a.type === 'worker') {
      workers.add(a.id)
      workers.add(a.name)
    }
  }
  return workers
}

type RemoteExecutionStatus = 'selected' | 'executing' | 'completed' | 'failed'

function stepMentionsRemoteNode(
  step: Pick<TaskStep, 'action' | 'agentId' | 'agentName' | 'output' | 'observation'>,
  an: { id: string; name: string; an_id?: string | null; ip?: string | null; port?: number | null },
): boolean {
  const refs = [step.agentId, step.agentName].filter(Boolean).map(v => String(v))
  if (refs.includes(an.id) || refs.includes(an.name) || (an.an_id && refs.includes(an.an_id))) {
    return true
  }
  const anAddr = an.ip ? `${an.ip}:${an.port}` : ''
  const text = `${String(step.output ?? '')}\n${String(step.observation ?? '')}`
  if (text.includes(an.id) || text.includes(an.name)) return true
  if (an.an_id && text.includes(an.an_id)) return true
  if (anAddr && text.includes(anAddr)) return true
  return false
}

function getRemoteExecutionStatus(
  task: Task,
  an: { id: string; name: string; an_id?: string | null; ip?: string | null; port?: number | null },
): RemoteExecutionStatus {
  for (let idx = task.steps.length - 1; idx >= 0; idx -= 1) {
    const step = task.steps[idx]
    if (!stepMentionsRemoteNode(step, an)) continue
    const action = String(step.action || '').toLowerCase()
    const detail = `${String(step.output ?? '')}\n${String(step.observation ?? '')}`.toLowerCase()
    if (action === 'remote_failed' || detail.includes('state=failed')) {
      return 'failed'
    }
    if (
      action === 'remote_completed' ||
      action === 'remote_done' ||
      detail.includes('state=completed')
    ) {
      return 'completed'
    }
    if (action === 'remote_progress') {
      return 'executing'
    }
    if (action.includes('dispatch') || action.includes('delegat')) {
      return 'selected'
    }
  }
  return 'selected'
}

/**
 * Topology for LOCAL tasks (initiated from chat).
 *
 * Complete task flow:
 *
 *   Forward (dispatch):
 *     Organizer → ANRouter → [selected pool members only]
 *       - If local team coordinator selected → delegate → Worker Agents
 *       - Remote ANs → HTTP delegation
 *
 *   Return (results):
 *     Worker Agents → local team coordinator → Organizer
 *     Remote ANs → Organizer
 *
 * Layout (top → bottom):
 *   Row 0:  Organizer
 *   Row 1:  ANRouter
 *   Row 2:  [local team coordinator if selected]  +  [selected Remote ANs]
 *   Row 3:  [local Workers]  (children of Coordinator, assigned by native adapter)
 */
function buildLocalTaskTopology(task: Task, agentNames: Map<string, string>, agents: Agent[]): { nodes: TopoNode[]; edges: TopoEdge[] } {
  const nodes: TopoNode[] = []
  const edges: TopoEdge[] = []

  // --- Derive task lifecycle state ---
  const status = task.status
  const phase = task.progress?.phase || 'received'
  const isActive = status === 'running' || status === 'delegated'
  const isDone = status === 'completed'
  const isFailed = status === 'failed'
  const isTerminal = isDone || isFailed || status === 'cancelled'

  const pastPlanning = isActive && phase !== 'received'
  const pastDelegating = isActive && !['received', 'routing', 'planning'].includes(phase)
  const inExecuting = isActive && ['executing', 'aggregating'].includes(phase)
  const inAggregating = isActive && phase === 'aggregating'

  const hasDelegationSteps = task.steps.some(
    s => s.action?.toLowerCase().includes('delegat') || s.action?.toLowerCase().includes('assign')
  )
  const hasAnySteps = task.steps.length > 0

  // --- Pool members ---
  const poolMembers = task.poolMembers || []
  const hasPoolData = poolMembers.length > 0
  const remoteANs = poolMembers.filter(m => m.type === 'remote')
  const localPoolMember = poolMembers.find(m => m.type === 'local')
  const totalPoolCount = hasPoolData ? poolMembers.length : 0

  // --- Only show SELECTED members (ANRouter decision), not the entire pool ---
  const selectedRemoteIds = getSelectedRemoteIds(task, remoteANs)
  const selectedRemoteANs = remoteANs.filter(an => selectedRemoteIds.has(an.id))
  const localSelected = isLocalCoordinatorSelected(task)

  // --- Determine local workers ---
  // Use positive matching: only include agents whose type is 'worker' in the store.
  // This prevents non-worker entities (ANRouter, Organizer, Coordinator) that appear
  // in steps from being mistakenly displayed as worker children.
  const knownWorkers = buildKnownWorkerSet(agents)

  const isKnownWorker = (resolved: string): boolean => {
    if (knownWorkers.has(resolved)) return true
    const name = agentNames.get(resolved) || ''
    return name !== '' && knownWorkers.has(name)
  }

  const workerIdSet = new Set<string>()
  const addIfWorker = (id: string) => {
    const resolved = resolveAgentRef(id, agentNames)
    if (isKnownWorker(resolved)) {
      workerIdSet.add(resolved)
    }
  }
  task.delegatedAgents?.forEach(addIfWorker)
  task.executingAgents?.forEach(addIfWorker)
  task.steps.forEach(s => { if (s.agentId) addIfWorker(s.agentId) })
  task.assignedAgents?.forEach(addIfWorker)

  // When the coordinator is selected and actively executing but no workers
  // have appeared in steps yet, pre-populate from the known workers in the
  // agent store so the topology shows them as "pending" immediately.
  if (localSelected && workerIdSet.size === 0 && (pastDelegating || inExecuting || isTerminal)) {
    for (const a of agents) {
      if (a.type === 'worker') workerIdSet.add(a.id)
    }
  }
  const workerIds = [...workerIdSet]

  // --- Layout ---
  const isSequential = task.executionMode === 'sequential'
  const row2Count = (localSelected ? 1 : 0) + selectedRemoteANs.length
  const workerCount = workerIds.length

  // Sequential: all pool members stacked in one column; Parallel: spread across columns
  const maxCols = isSequential ? Math.max(workerCount, 1) : Math.max(row2Count, workerCount, 1)
  const refineRounds = getRefinementRounds(task.steps)
  const hasRefineSteps = refineRounds.length > 0
  /** When in round 2+, main flow (round 1) is done — show per-branch status, not "executing" */
  const mainFlowInRefine = hasRefineSteps
  const branchMemberCount = Math.max(row2Count, 1)
  const memberSpan = NODE_W + Math.max(0, branchMemberCount - 1) * (NODE_W + COL_GAP)
  const workerSpan = localSelected && workerCount > 0
    ? NODE_W + Math.max(0, workerCount - 1) * (NODE_W + COL_GAP)
    : 0
  const refineBranchWidth = Math.max(memberSpan, workerSpan, NODE_W * 1.75)
  const refineBranchGap = hasRefineSteps ? refineBranchWidth + NODE_W : 0
  const baseSvgW = Math.max(maxCols * (NODE_W + COL_GAP), 280)
  const svgW = hasRefineSteps
    ? Math.max(baseSvgW + refineRounds.length * refineBranchGap, 520)
    : baseSvgW
  const centerX = svgW / 2

  // --- Row 0: Organizer ---
  const orgStatus: NodeStatus =
    isFailed ? 'failed'
    : isDone ? 'completed'
    : inAggregating ? 'executing'
    : isActive ? 'executing'
    : status === 'pending' ? 'pending'
    : 'idle'
  nodes.push({
    id: 'organizer', label: 'Organizer', type: 'organizer',
    status: orgStatus, x: centerX, y: 30,
  })

  // --- Row 1: ANRouter ---
  const routerStatus: NodeStatus =
    isTerminal ? 'completed'
    : (pastPlanning || hasDelegationSteps || hasAnySteps) ? 'completed'
    : isActive ? 'executing'
    : 'idle'

  const hasSelectedData = !!task.selectedMembers?.length
  const totalSelected = (localSelected ? 1 : 0) + selectedRemoteANs.length
  const routerDetail = hasSelectedData && hasPoolData
    ? `${isSequential ? 'sequential' : 'parallel'}: ${totalSelected}/${totalPoolCount}`
    : hasPoolData
      ? `pool: ${totalPoolCount}`
      : isActive ? 'routing…' : undefined

  nodes.push({
    id: 'router', label: 'ANRouter', type: 'router',
    status: routerStatus,
    detail: routerDetail,
    x: centerX, y: 30 + ROW_GAP,
  })
  edges.push({
    from: 'organizer', to: 'router',
    status: isTerminal || pastPlanning || hasAnySteps ? 'done' : isActive ? 'active' : 'idle',
    label: 'route()',
  })

  // --- Pool members: Row 2+ (parallel = same row; sequential = stacked rows) ---
  // Build ordered list: [local coordinator, ...remote ANs]
  type PoolMemberSpec = { kind: 'local' } | { kind: 'remote'; an: typeof selectedRemoteANs[number]; idx: number }
  const orderedMembers: PoolMemberSpec[] = []
  if (localSelected) orderedMembers.push({ kind: 'local' })
  selectedRemoteANs.forEach((an, i) => orderedMembers.push({ kind: 'remote', an, idx: i }))

  // Detect duplicate remote AN names to disambiguate labels
  const remoteNameCounts = new Map<string, number>()
  selectedRemoteANs.forEach(an => {
    remoteNameCounts.set(an.name, (remoteNameCounts.get(an.name) || 0) + 1)
  })

  // Track where coordinator ends up (for workers below it)
  let coordNodeX = centerX
  let prevMemberId: string = 'router'  // for sequential chaining

  // Sequential: incremental display — only CURRENT step gets "executing", prior steps "completed", rest "selected"
  const completedWorkers = task.progress?.completedWorkers ?? 0
  const currentStepIdx = isSequential ? completedWorkers : -1

  orderedMembers.forEach((spec, memberIdx) => {
    const memberY = isSequential
      ? 30 + ROW_GAP * (2 + memberIdx)  // stacked vertically
      : 30 + ROW_GAP * 2                // same horizontal row
    const memberX = isSequential
      ? centerX
      : centerX - ((row2Count - 1) * (NODE_W + COL_GAP)) / 2 + memberIdx * (NODE_W + COL_GAP)

    if (spec.kind === 'local') {
      coordNodeX = memberX
      const coordName = agentNames.get(COORDINATOR_ID) || LOCAL_COORDINATOR_NAME
      const coordHasWork = (
        task.executingAgents?.includes(COORDINATOR_ID) ||
        task.steps.some(s => s.agentId === COORDINATOR_ID) ||
        workerIds.length > 0
      )
      const coordStatus: NodeStatus = mainFlowInRefine
        ? (isFailed ? 'failed'
          : isDone ? 'completed'
          : coordHasWork ? 'completed'
          : 'pending')
        : isSequential
          ? (isFailed ? 'failed'
            : isDone ? 'completed'
            : memberIdx < currentStepIdx ? 'completed'
            : memberIdx === currentStepIdx && inExecuting ? 'executing'
            : memberIdx === currentStepIdx ? 'executing'
            : (pastDelegating || hasDelegationSteps) ? 'selected'
            : isActive ? 'selected'
            : 'idle')
          : (isFailed ? 'failed'
            : isDone ? 'completed'
            : coordHasWork && inExecuting ? 'executing'
            : coordHasWork ? 'executing'
            : (pastDelegating || hasDelegationSteps) ? 'selected'
            : isActive ? 'selected'
            : 'idle')
      const coordAnId = localPoolMember?.an_id || undefined
      const coordExtraLines = coordAnId ? 1 : 0
      nodes.push({
        id: 'coordinator', label: coordName, type: 'coordinator',
        status: coordStatus, anId: coordAnId,
        x: memberX, y: memberY,
        h: NODE_H + coordExtraLines * 10,
      })
      const edgeFromStatus = mainFlowInRefine
        ? (coordHasWork || isTerminal ? 'done' : 'idle')
        : isSequential
          ? (isTerminal ? 'done'
            : memberIdx < currentStepIdx ? 'done'
            : memberIdx === currentStepIdx && inExecuting ? 'active'
            : memberIdx === currentStepIdx ? 'active'
            : 'idle')
          : (isTerminal ? 'done'
            : coordHasWork ? 'active'
            : (pastDelegating || isActive) ? 'active'
            : 'idle')
      edges.push({
        from: prevMemberId, to: 'coordinator',
        status: edgeFromStatus,
        label: isSequential && memberIdx > 0 ? `step ${memberIdx + 1}` : 'local',
      })
      prevMemberId = 'coordinator'
    } else {
      const { an, idx } = spec
      const anId = `remote-${an.id || idx}`
      const anAddr = an.ip ? `${an.ip}:${an.port}` : ''
      const isDelegated = (
        task.delegatedTo === an.id ||
        task.delegatedTo === an.an_id ||
        task.steps.some(s => {
          if (!s.action?.toLowerCase().includes('delegat')) return false
          const out = s.output?.toString() || ''
          if (out.includes(an.id)) return true
          if (anAddr && out.includes(anAddr)) return true
          if (an.an_id && out.includes(an.an_id)) return true
          return out.includes(an.name)
        })
      )
      const remoteExecStatus = getRemoteExecutionStatus(task, an)
      const anStatus: NodeStatus = mainFlowInRefine
        ? (remoteExecStatus === 'failed' ? 'failed'
          : remoteExecStatus === 'completed' ? 'completed'
          : remoteExecStatus === 'executing' ? 'executing'
          : isDelegated ? 'completed'
          : 'pending')
        : isSequential
          ? (remoteExecStatus === 'failed' ? 'failed'
            : remoteExecStatus === 'completed' ? 'completed'
            : remoteExecStatus === 'executing' ? 'executing'
            : isFailed && memberIdx <= currentStepIdx ? 'failed'
            : isDone ? 'completed'
            : memberIdx < currentStepIdx ? 'completed'
            : memberIdx === currentStepIdx && isActive ? 'executing'
            : (pastDelegating || hasDelegationSteps) ? 'selected'
            : 'selected')
          : (remoteExecStatus === 'failed' ? 'failed'
            : remoteExecStatus === 'completed' ? 'completed'
            : remoteExecStatus === 'executing' ? 'executing'
            : isFailed && isDelegated ? 'failed'
            : isDone && isDelegated ? 'completed'
            : isDone ? 'completed'
            : isDelegated && isActive ? 'executing'
            : isActive ? 'executing'
            : 'selected')
      const remAnId = an.an_id || an.id || undefined
      const remDetail = an.ip ? `${an.ip}:${an.port}` : undefined
      const hasDuplicateName = (remoteNameCounts.get(an.name) || 0) > 1
      const remLabel = hasDuplicateName && anAddr ? anAddr : an.name
      const remExtraLines = (remAnId ? 1 : 0) + (remDetail && !hasDuplicateName ? 1 : 0)
      nodes.push({
        id: anId, label: remLabel, type: 'remote',
        status: anStatus, detail: hasDuplicateName ? undefined : remDetail, anId: remAnId,
        x: memberX, y: memberY,
        h: NODE_H + remExtraLines * 10,
      })
      const edgeToStatus = mainFlowInRefine
        ? (remoteExecStatus === 'failed' ? 'error'
          : remoteExecStatus === 'completed' || isDelegated ? 'done'
          : remoteExecStatus === 'executing' ? 'active'
          : 'idle')
        : isSequential
          ? (remoteExecStatus === 'failed' ? 'error'
            : remoteExecStatus === 'completed' ? 'done'
            : remoteExecStatus === 'executing' ? 'active'
            : isTerminal ? 'done'
            : memberIdx < currentStepIdx ? 'done'
            : memberIdx === currentStepIdx && isActive ? 'active'
            : memberIdx === currentStepIdx ? 'active'
            : 'idle')
          : (remoteExecStatus === 'failed' ? 'error'
            : remoteExecStatus === 'completed' ? 'done'
            : remoteExecStatus === 'executing' ? 'active'
            : isTerminal && isDelegated ? 'done'
            : isTerminal ? 'done'
            : isDelegated && isActive ? 'active'
            : isActive ? 'active'
            : 'idle')
      edges.push({
        from: prevMemberId, to: anId,
        status: edgeToStatus,
        label: isSequential && memberIdx > 0 ? `step ${memberIdx + 1}` : 'HTTP',
      })
      if (!isSequential && (isTerminal || inAggregating || isDelegated)) {
        // Parallel: return results directly to Organizer
        edges.push({
          from: anId, to: 'organizer',
          status: 'return',
          label: 'results',
          curved: 'right',
        })
      }
      prevMemberId = anId
    }
  })

  // Sequential: add a single return edge from last member to Organizer
  if (isSequential && orderedMembers.length > 0 && (isTerminal || inAggregating)) {
    edges.push({
      from: prevMemberId, to: 'organizer',
      status: 'return',
      label: 'results',
      curved: 'right',
    })
  }

  // --- Refine phase branches (round 2+) ---
  if (hasRefineSteps && orderedMembers.length > 0) {
    const latestRefineRound = refineRounds[refineRounds.length - 1]
    const branchStartCenterX = centerX + baseSvgW / 2 + refineBranchGap / 2
    const baseMemberY = 30 + ROW_GAP * 2

    refineRounds.forEach((round, roundIndex) => {
      const branchCenterX = branchStartCenterX + roundIndex * refineBranchGap
      const roundComplete = isTerminal || round < latestRefineRound
      const roundActive = !isTerminal && round === latestRefineRound
      const refineNodeId = `refine-round-${round}`

      nodes.push({
        id: refineNodeId,
        label: `Round ${round}`,
        type: 'refine',
        status: roundComplete ? 'completed' : roundActive ? 'executing' : 'selected',
        detail: 'refine branch',
        round,
        x: branchCenterX,
        y: 30 + ROW_GAP,
      })
      edges.push({
        from: roundIndex === 0 ? 'organizer' : `refine-round-${refineRounds[roundIndex - 1]}`,
        to: refineNodeId,
        status: roundComplete ? 'done' : 'active',
        label: roundIndex === 0 ? 'refine' : 'next',
      })

      const branchMembers = orderedMembers.length
      const branchStartX = branchCenterX - ((branchMembers - 1) * (NODE_W + COL_GAP)) / 2
      orderedMembers.forEach((spec, memberIdx) => {
        const memberX = branchStartX + memberIdx * (NODE_W + COL_GAP)
        if (spec.kind === 'local') {
          const nodeId = `coordinator-r${round}`
          nodes.push({
            id: nodeId,
            label: agentNames.get(COORDINATOR_ID) || LOCAL_COORDINATOR_NAME,
            type: 'coordinator',
            status: roundComplete ? 'completed' : roundActive && inExecuting ? 'executing' : 'selected',
            round,
            x: memberX,
            y: baseMemberY,
            anId: localPoolMember?.an_id || undefined,
            h: localPoolMember?.an_id ? NODE_H + 10 : NODE_H,
          })
          edges.push({
            from: refineNodeId,
            to: nodeId,
            status: roundComplete ? 'done' : 'active',
            label: memberIdx === 0 ? 'dispatch' : undefined,
          })

          if (workerIds.length > 0) {
            const row3Y = baseMemberY + ROW_GAP
            const workerStartX = memberX - ((workerIds.length - 1) * (NODE_W + COL_GAP)) / 2
            workerIds.forEach((wId, workerIdx) => {
              const stableId = `worker-${wId.replace(/[^a-zA-Z0-9_-]/g, '_')}-r${round}`
              const wName = agentNames.get(wId) || wId
              nodes.push({
                id: stableId,
                label: wName,
                type: 'worker',
                status: roundComplete ? 'completed' : roundActive && inExecuting ? 'executing' : 'pending',
                round,
                x: workerStartX + workerIdx * (NODE_W + COL_GAP),
                y: row3Y,
              })
              edges.push({
                from: nodeId,
                to: stableId,
                status: roundComplete ? 'done' : roundActive ? 'active' : 'idle',
                label: workerIdx === 0 ? 'delegate' : undefined,
              })
            })
            if (roundComplete || inAggregating) {
              const firstWorkerId = `worker-${workerIds[0].replace(/[^a-zA-Z0-9_-]/g, '_')}-r${round}`
              edges.push({
                from: firstWorkerId,
                to: nodeId,
                status: 'return',
                label: 'results',
                curved: 'left',
              })
            }
          }

          if (roundComplete || inAggregating) {
            edges.push({
              from: nodeId,
              to: 'organizer',
              status: 'return',
              label: `R${round}`,
              curved: 'left',
            })
          }
        } else {
          const { an, idx } = spec
          const nodeId = `remote-${an.id || idx}-r${round}`
          const anAddr = an.ip ? `${an.ip}:${an.port}` : ''
          const hasDuplicateName = (remoteNameCounts.get(an.name) || 0) > 1
          nodes.push({
            id: nodeId,
            label: hasDuplicateName && anAddr ? anAddr : an.name,
            type: 'remote',
            status: roundComplete ? 'completed' : roundActive ? 'executing' : 'selected',
            round,
            detail: hasDuplicateName ? undefined : (an.ip ? `${an.ip}:${an.port}` : undefined),
            anId: an.an_id || an.id || undefined,
            x: memberX,
            y: baseMemberY,
            h: NODE_H + (((an.an_id || an.id) ? 1 : 0) + (an.ip && !hasDuplicateName ? 1 : 0)) * 10,
          })
          edges.push({
            from: refineNodeId,
            to: nodeId,
            status: roundComplete ? 'done' : 'active',
            label: memberIdx === 0 && !localSelected ? 'HTTP' : undefined,
          })
          if (roundComplete || inAggregating) {
            edges.push({
              from: nodeId,
              to: 'organizer',
              status: 'return',
              label: `R${round}`,
              curved: 'right',
            })
          }
        }
      })
    })
  }

  // --- Workers: children of Coordinator ---
  // Incremental: show ALL selected workers (from delegated/executing/steps/pre-populate).
  // Never remove workers once shown — only add. Use stable IDs (worker-{wId}) for React keys.
  const lastRow2Y = isSequential && orderedMembers.length > 0
    ? 30 + ROW_GAP * (1 + orderedMembers.length)
    : 30 + ROW_GAP * 2
  if (localSelected && workerIds.length > 0) {
    const row3Y = lastRow2Y + ROW_GAP

    // Show all workers — no filter. When Coordinator assigns 1,2,3 they all appear.
    const workerStartX = coordNodeX - ((workerIds.length - 1) * (NODE_W + COL_GAP)) / 2
    const workerStatusByName = new Map<string, 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'timeout'>()
    for (const ws of task.progress?.workerStatuses ?? []) {
      workerStatusByName.set(ws.name, ws.status)
    }
    workerIds.forEach((wId, i) => {
      const wName = agentNames.get(wId) || wId
      const wExecuting = (task.executingAgents || []).some(id => resolveAgentRef(id, agentNames) === wId)
      const wHasSteps = task.steps.some(s => resolveAgentRef(s.agentId, agentNames) === wId)
      const wInDelegated = (task.delegatedAgents || []).some(id => resolveAgentRef(id, agentNames) === wId)
      const wCompleted = (wInDelegated || wHasSteps) && !wExecuting
      const wProgStatus = workerStatusByName.get(wName)
      const wStatus: NodeStatus = mainFlowInRefine
        ? (isFailed && (wProgStatus === 'failed' || wProgStatus === 'timeout') ? 'failed'
          : wProgStatus === 'failed' || wProgStatus === 'timeout' ? 'failed'
          : wCompleted || wProgStatus === 'completed' ? 'completed'
          : wProgStatus === 'skipped' ? 'pending'
          : wInDelegated || wHasSteps ? 'completed'
          : 'pending')
        : (isFailed ? 'failed'
          : isDone || wCompleted ? 'completed'
          : wExecuting ? 'executing'
          : wHasSteps ? 'executing'
          : wInDelegated ? 'selected'
          : 'pending')
      const stableId = `worker-${wId.replace(/[^a-zA-Z0-9_-]/g, '_')}`
      nodes.push({
        id: stableId, label: wName, type: 'worker',
        status: wStatus, x: workerStartX + i * (NODE_W + COL_GAP), y: row3Y,
      })
      const edgeStatus = mainFlowInRefine
        ? (wCompleted || wProgStatus === 'completed' || wHasSteps ? 'done' : 'idle')
        : (wExecuting ? 'active'
          : wCompleted || isDone ? 'done'
          : wHasSteps ? 'active'
          : 'idle')
      edges.push({
        from: 'coordinator', to: stableId,
        status: edgeStatus,
        label: i === 0 ? 'delegate' : undefined,
      })
    })

    const anyWorkerDone = workerIds.some(wId => {
      const wHasSteps = task.steps.some(s => resolveAgentRef(s.agentId, agentNames) === wId)
      return wHasSteps
    })
    if (anyWorkerDone || inAggregating || isTerminal) {
      const firstWorkerId = workerIds[0] ? `worker-${workerIds[0].replace(/[^a-zA-Z0-9_-]/g, '_')}` : 'worker-0'
      edges.push({
        from: firstWorkerId, to: 'coordinator',
        status: 'return',
        label: 'results',
        curved: 'left',
      })
    }
  }

  // Return: Coordinator → Organizer (aggregated results) — parallel mode only
  // Sequential mode already adds a single return edge from the last member above.
  if (localSelected && !isSequential && (inAggregating || isTerminal)) {
    edges.push({
      from: 'coordinator', to: 'organizer',
      status: 'return',
      label: 'aggregate',
      curved: 'left',
    })
  }

  return { nodes, edges }
}

/**
 * Topology for REMOTE tasks (received from external AN):
 *   Remote Requester → local team coordinator → Workers
 *
 * No Organizer or ANRouter — the task was received via HTTP and
 * goes directly to the local Coordinator for execution.
 */
function buildRemoteTaskTopology(task: Task, agentNames: Map<string, string>, agents: Agent[]): { nodes: TopoNode[]; edges: TopoEdge[] } {
  const nodes: TopoNode[] = []
  const edges: TopoEdge[] = []

  const status = task.status
  const phase = task.progress?.phase || 'received'
  const isActive = status === 'running' || status === 'delegated'
  const isDone = status === 'completed'
  const isFailed = status === 'failed'
  const isTerminal = isDone || isFailed || status === 'cancelled'
  const inExecuting = isActive && ['executing', 'aggregating'].includes(phase)
  const hasAnySteps = task.steps.length > 0

  // Use the same positive worker matching as local topology (type === 'worker' in agent store)
  const knownWorkers = buildKnownWorkerSet(agents)
  const isKnownWorker = (resolved: string): boolean => {
    if (knownWorkers.has(resolved)) return true
    const name = agentNames.get(resolved) || ''
    return name !== '' && knownWorkers.has(name)
  }

  const workerIdSet = new Set<string>()
  const addIfWorker = (id: string) => {
    const resolved = resolveAgentRef(id, agentNames)
    if (isKnownWorker(resolved)) workerIdSet.add(resolved)
  }
  task.delegatedAgents?.forEach(addIfWorker)
  task.executingAgents?.forEach(addIfWorker)
  task.steps.forEach(s => { if (s.agentId) addIfWorker(s.agentId) })
  task.assignedAgents?.forEach(addIfWorker)

  // Pre-populate workers from agent store when coordinator is executing but no worker steps yet
  if (workerIdSet.size === 0 && (inExecuting || isTerminal)) {
    for (const a of agents) {
      if (a.type === 'worker') workerIdSet.add(a.id)
    }
  }
  const workerIds = [...workerIdSet]

  // Layout
  const workerCount = workerIds.length
  const maxCols = Math.max(workerCount, 1)
  const svgW = Math.max(maxCols * (NODE_W + COL_GAP), 280)
  const centerX = svgW / 2

  // --- Row 0: Remote Requester ---
  const requesterLabel = task.requesterId
    ? `Remote AN (${task.requesterId.slice(-6)})`
    : 'Remote AN'
  const reqStatus: NodeStatus =
    isTerminal ? 'completed'
    : isActive ? 'completed'
    : 'idle'
  nodes.push({
    id: 'requester', label: requesterLabel, type: 'remote',
    status: reqStatus, x: centerX, y: 30,
    detail: 'Requester',
  })

  // --- Row 1: local team coordinator ---
  const coordName = agentNames.get(COORDINATOR_ID) || LOCAL_COORDINATOR_NAME
  const coordHasWork = hasAnySteps || workerIds.length > 0
  const coordStatus: NodeStatus =
    isFailed ? 'failed'
    : isDone ? 'completed'
    : coordHasWork && inExecuting ? 'executing'
    : coordHasWork ? 'executing'
    : isActive ? 'executing'
    : 'idle'
  nodes.push({
    id: 'coordinator', label: coordName, type: 'coordinator',
    status: coordStatus,
    x: centerX, y: 30 + ROW_GAP,
  })
  edges.push({
    from: 'requester', to: 'coordinator',
    status: isTerminal ? 'done' : isActive ? 'active' : 'idle',
    label: 'HTTP task',
  })

  // --- Row 2: Workers (stable IDs for incremental display) ---
  if (workerIds.length > 0) {
    const row2Y = 30 + ROW_GAP * 2
    const workerStartX = centerX - ((workerIds.length - 1) * (NODE_W + COL_GAP)) / 2
    workerIds.forEach((wId, i) => {
      const wName = agentNames.get(wId) || wId
      const wExecuting = (task.executingAgents || []).some(id => resolveAgentRef(id, agentNames) === wId)
      const wHasSteps = task.steps.some(s => resolveAgentRef(s.agentId, agentNames) === wId)
      const wInDelegated = (task.delegatedAgents || []).some(id => resolveAgentRef(id, agentNames) === wId)
      const wCompleted = (wInDelegated || wHasSteps) && !wExecuting
      const wStatus: NodeStatus =
        isFailed ? 'failed'
        : isDone || wCompleted ? 'completed'
        : wExecuting ? 'executing'
        : wHasSteps ? 'executing'
        : 'selected'
      const stableId = `worker-${wId.replace(/[^a-zA-Z0-9_-]/g, '_')}`
      nodes.push({
        id: stableId, label: wName, type: 'worker',
        status: wStatus, x: workerStartX + i * (NODE_W + COL_GAP), y: row2Y,
      })
      edges.push({
        from: 'coordinator', to: stableId,
        status:
          wExecuting ? 'active'
          : wCompleted || isDone ? 'done'
          : wHasSteps ? 'active'
          : 'idle',
      })
    })
  }

  return { nodes, edges }
}

// --- SVG Components ---

function TopoEdgeLine({ edge, nodes }: { edge: TopoEdge; nodes: TopoNode[] }) {
  const from = nodes.find(n => n.id === edge.from)
  const to = nodes.find(n => n.id === edge.to)
  if (!from || !to) return null

  const fromH = from.h || NODE_H
  const toH = to.h || NODE_H
  const color = edgeColor[edge.status] || edgeColor.idle
  const isActive = edge.status === 'active'

  const rectAnchor = (
    cx: number,
    cy: number,
    halfW: number,
    halfH: number,
    targetX: number,
    targetY: number,
  ) => {
    const dx = targetX - cx
    const dy = targetY - cy
    if (dx === 0 && dy === 0) return { x: cx, y: cy }
    const tx = halfW / Math.max(Math.abs(dx), 1e-6)
    const ty = halfH / Math.max(Math.abs(dy), 1e-6)
    const t = Math.min(tx, ty)
    return { x: cx + dx * t, y: cy + dy * t }
  }

  // --- Return-flow edges (curved, going upward) ---
  if (edge.curved) {
    // "from" is lower, "to" is higher — results flow upward
    const fromPt = rectAnchor(from.x, from.y, NODE_W / 2, fromH / 2, to.x, to.y)
    const toPt = rectAnchor(to.x, to.y, NODE_W / 2, toH / 2, from.x, from.y)
    const x1 = fromPt.x
    const y1 = fromPt.y
    const x2 = toPt.x
    const y2 = toPt.y
    const curveOffset = edge.curved === 'left' ? -60 : 60
    const cx = Math.min(x1, x2) + (x1 + x2) / 2 * 0 + curveOffset
    const cy = (y1 + y2) / 2
    const d = `M${x1},${y1} Q${cx},${cy} ${x2},${y2}`

    // Arrow direction: toward "to" (upward)
    const angle = Math.atan2(y2 - cy, x2 - cx)
    const ax = x2 - 8 * Math.cos(angle)
    const ay = y2 - 8 * Math.sin(angle)
    const perpX = 4 * Math.cos(angle + Math.PI / 2)
    const perpY = 4 * Math.sin(angle + Math.PI / 2)

    return (
      <g>
        <path
          d={d}
          stroke={color}
          strokeWidth={1}
          strokeDasharray="4 3"
          fill="none"
          opacity={0.9}
        />
        <polygon
          points={`${x2},${y2} ${ax + perpX},${ay + perpY} ${ax - perpX},${ay - perpY}`}
          fill={color}
          opacity={0.9}
        />
        {edge.label && (
          <text
            x={cx + (edge.curved === 'left' ? -6 : 6)}
            y={cy}
            fill="#7c3aed"
            fontSize={8}
            textAnchor={edge.curved === 'left' ? 'end' : 'start'}
            opacity={0.7}
          >
            {edge.label}
          </text>
        )}
      </g>
    )
  }

  // --- Forward-flow edges (straight; supports vertical and horizontal) ---
  const fromPt = rectAnchor(from.x, from.y, NODE_W / 2, fromH / 2, to.x, to.y)
  const toPt = rectAnchor(to.x, to.y, NODE_W / 2, toH / 2, from.x, from.y)
  const x1 = fromPt.x
  const y1 = fromPt.y
  const x2 = toPt.x
  const y2 = toPt.y
  const angle = Math.atan2(y2 - y1, x2 - x1)
  const ax = x2 - 8 * Math.cos(angle)
  const ay = y2 - 8 * Math.sin(angle)
  const perpX = 4 * Math.cos(angle + Math.PI / 2)
  const perpY = 4 * Math.sin(angle + Math.PI / 2)

  return (
    <g>
      <line
        x1={x1} y1={y1} x2={x2} y2={y2}
        stroke={color}
        strokeWidth={isActive ? 2 : 1}
        strokeDasharray={edge.status === 'idle' ? '4 4' : undefined}
        opacity={edge.status === 'idle' ? 0.8 : 1}
      />
      {/* Arrow */}
      <polygon
        points={`${x2},${y2} ${ax + perpX},${ay + perpY} ${ax - perpX},${ay - perpY}`}
        fill={color}
        opacity={edge.status === 'idle' ? 0.8 : 1}
      />
      {/* Animated dot for active edges */}
      {isActive && (
        <circle r={3} fill={color}>
          <animateMotion
            dur="1.5s"
            repeatCount="indefinite"
            path={`M${x1},${y1} L${x2},${y2}`}
          />
        </circle>
      )}
      {/* Edge label */}
      {edge.label && (
        <text
          x={(x1 + x2) / 2 + 8}
          y={(y1 + y2) / 2}
          fill="#6b7280"
          fontSize={9}
          textAnchor="start"
        >
          {edge.label}
        </text>
      )}
    </g>
  )
}

function TopoNodeBox({
  node,
  onPointerDown,
}: {
  node: TopoNode
  onPointerDown?: (nodeId: string, e: React.PointerEvent<SVGGElement>) => void
}) {
  const p = palette[node.type] || palette.worker
  const sColor = statusColor[node.status] || statusColor.idle
  const isActive = node.status === 'executing'
  const roundAccent = getRoundAccent(node.round)

  const hasAnId = !!node.anId
  const hasDetail = !!node.detail
  const nodeH = node.h || NODE_H

  const x = node.x - NODE_W / 2
  const y = node.y - nodeH / 2
  const clipId = `topo-node-clip-${node.id.replace(/[^a-zA-Z0-9_-]/g, '_')}`

  // Type abbreviation fallback
  const abbrFallback: Record<string, string> = {
    organizer: 'O', router: 'R', coordinator: 'C', remote: 'AN', worker: 'W', refine: 'Rf',
  }
  // Derive initials from node label (name) — e.g. "Code Developer" → "CD"
  const initials = node.label
    .split(/[\s_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map(w => w[0]?.toUpperCase() || '')
    .join('')
  const abbr = initials || abbrFallback[node.type] || '?'

  // Truncate an_id for display: show first 6 + last 6 chars
  const truncAnId = node.anId && node.anId.length > 16
    ? `${node.anId.slice(0, 8)}…${node.anId.slice(-6)}`
    : node.anId
  const truncName = node.label.length > 18 ? `${node.label.slice(0, 17)}…` : node.label
  const truncDetail = node.detail && node.detail.length > 22
    ? `${node.detail.slice(0, 21)}…`
    : node.detail

  // Count extra text lines below the name (anId and/or detail)
  const extraLines = (hasAnId ? 1 : 0) + (hasDetail ? 1 : 0)

  // Vertical offset: push name up when extra lines exist
  const nameY = node.y - extraLines * 5

  const isWorker = node.type === 'worker'

  return (
    <g
      onPointerDown={(e) => onPointerDown?.(node.id, e)}
      style={{ cursor: onPointerDown ? 'grab' : 'default' }}
    >
      <defs>
        <clipPath id={clipId}>
          <rect x={x + 32} y={y + 4} width={NODE_W - 44} height={nodeH - 8} rx={4} />
        </clipPath>
      </defs>
      {/* Fade-in animation for dynamically added worker nodes */}
      {isWorker && (
        <animate attributeName="opacity" from="0" to="1" dur="0.4s" fill="freeze" />
      )}
      {/* Background rect */}
      <rect
        x={x} y={y} width={NODE_W} height={nodeH} rx={8}
        fill={p.bg} fillOpacity={0.6}
        stroke={isActive ? sColor : p.border} strokeWidth={isActive ? 2 : 1}
        strokeOpacity={0.8}
      />
      {roundAccent && (
        <>
          <rect
            x={x + 1} y={y + 1} width={NODE_W - 2} height={4} rx={6}
            fill={roundAccent}
            opacity={0.9}
          />
          <rect
            x={x + NODE_W - 34} y={y + 6} width={24} height={12} rx={6}
            fill={roundAccent}
            opacity={0.18}
            stroke={roundAccent}
            strokeWidth={0.8}
          />
          <text
            x={x + NODE_W - 22} y={y + 12}
            fill={roundAccent}
            fontSize={7.5}
            fontWeight="bold"
            textAnchor="middle"
            dominantBaseline="central"
          >
            {`R${node.round}`}
          </text>
        </>
      )}

      {/* Pulsing glow for active */}
      {isActive && (
        <rect
          x={x - 2} y={y - 2} width={NODE_W + 4} height={nodeH + 4} rx={10}
          fill="none" stroke={sColor} strokeWidth={1.5}
          opacity={0.5}
        >
          <animate attributeName="opacity" values="0.5;0.15;0.5" dur="1.5s" repeatCount="indefinite" />
        </rect>
      )}

      {/* Type badge (left) */}
      <circle
        cx={x + 16} cy={node.y}
        r={BADGE_R + 4} fill={p.bg} fillOpacity={0.8}
        stroke={p.border} strokeWidth={1}
      />
      <text
        x={x + 16} y={node.y + 1}
        fill={p.text} fontSize={9} fontWeight="bold"
        textAnchor="middle" dominantBaseline="central"
      >
        {abbr}
      </text>

      <g clipPath={`url(#${clipId})`}>
        {/* Name */}
        <text
          x={x + 32} y={nameY}
          fill={p.text} fontSize={11} fontWeight="500"
          dominantBaseline="central"
        >
          {truncName}
        </text>
        <title>{node.label}</title>

        {/* AN ID (small muted text under name) */}
        {hasAnId && (
          <text
            x={x + 32} y={nameY + 12}
            fill="#9ca3af" fontSize={7}
            dominantBaseline="central"
            fontFamily="monospace"
          >
            {truncAnId}
          </text>
        )}

        {/* Detail (ip:port, etc) */}
        {hasDetail && (
          <text
            x={x + 32} y={nameY + (hasAnId ? 22 : 12)}
            fill="#6b7280" fontSize={8}
            dominantBaseline="central"
          >
            {truncDetail}
          </text>
        )}
      </g>

      {/* Status dot (right side) */}
      <circle
        cx={x + NODE_W - 12} cy={node.y}
        r={4} fill={sColor}
      />
      {isActive && (
        <circle
          cx={x + NODE_W - 12} cy={node.y}
          r={4} fill={sColor}
        >
          <animate attributeName="r" values="4;7;4" dur="1.2s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="0.8;0.2;0.8" dur="1.2s" repeatCount="indefinite" />
        </circle>
      )}
    </g>
  )
}

// --- Legend ---

function Legend() {
  const items: { color: string; label: string; dashed?: boolean }[] = [
    { color: statusColor.pending, label: 'Pending' },
    { color: statusColor.selected, label: 'Selected' },
    { color: statusColor.executing, label: 'Executing' },
    { color: statusColor.completed, label: 'Completed' },
    { color: statusColor.failed, label: 'Failed' },
    { color: palette.refine.border, label: 'Refine' },
    { color: edgeColor.return, label: 'Return flow', dashed: true },
  ]

  return (
    <div className="flex flex-wrap gap-3 px-1 mt-2">
      {items.map(({ color, label, dashed }) => (
        <div key={label} className="flex items-center gap-1.5">
          {dashed ? (
            <svg width="12" height="8" className="inline-block">
              <line x1="0" y1="4" x2="12" y2="4" stroke={color} strokeWidth="2" strokeDasharray="2 2" />
            </svg>
          ) : (
            <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
          )}
          <span className="text-[10px] text-gray-500">{label}</span>
        </div>
      ))}
    </div>
  )
}

// --- SVG Viewport with zoom/pan ---

interface SvgViewportProps {
  nodes: TopoNode[]
  edges: TopoEdge[]
  nodeOffsets: Record<string, NodeOffset>
  onNodeOffsetChange: (nodeId: string, offset: NodeOffset) => void
  /** Natural (unscaled) viewBox dimensions */
  vbMinX: number
  vbW: number
  vbH: number
  /** If true the component is rendered inside the fullscreen portal */
  isFullscreen?: boolean
}

function SvgViewport({
  nodes,
  edges,
  nodeOffsets,
  onNodeOffsetChange,
  vbMinX,
  vbW,
  vbH,
  isFullscreen,
}: SvgViewportProps) {
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const dragging = useRef(false)
  const dragPointerId = useRef<number | null>(null)
  const draggingNodeId = useRef<string | null>(null)
  const nodeDragStart = useRef({ x: 0, y: 0 })
  const nodeStartOffset = useRef<NodeOffset>({ x: 0, y: 0 })
  const lastPoint = useRef({ x: 0, y: 0 })
  const containerRef = useRef<HTMLDivElement>(null)

  const clampZoom = (z: number) => Math.min(Math.max(z, 0.3), 3)

  const handleWheel = useCallback((e: WheelEvent) => {
    e.preventDefault()
    const delta = e.deltaY > 0 ? -0.1 : 0.1
    setZoom(z => clampZoom(z + delta))
  }, [])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    el.addEventListener('wheel', handleWheel, { passive: false })
    return () => el.removeEventListener('wheel', handleWheel)
  }, [handleWheel])

  const handlePointerDown = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return
    if (draggingNodeId.current) return
    dragging.current = true
    dragPointerId.current = e.pointerId
    lastPoint.current = { x: e.clientX, y: e.clientY }
    e.currentTarget.setPointerCapture(e.pointerId)
  }, [])

  const handleNodePointerDown = useCallback((
    nodeId: string,
    e: React.PointerEvent<SVGGElement>,
  ) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return
    e.stopPropagation()
    draggingNodeId.current = nodeId
    dragPointerId.current = e.pointerId
    nodeDragStart.current = { x: e.clientX, y: e.clientY }
    nodeStartOffset.current = nodeOffsets[nodeId] || { x: 0, y: 0 }
    try {
      const svg = e.currentTarget.ownerSVGElement
      if (svg) svg.setPointerCapture(e.pointerId)
    } catch {
      // no-op
    }
  }, [nodeOffsets])

  const handlePointerMove = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    if (draggingNodeId.current && dragPointerId.current === e.pointerId) {
      const dx = (e.clientX - nodeDragStart.current.x) / zoom
      const dy = (e.clientY - nodeDragStart.current.y) / zoom
      onNodeOffsetChange(draggingNodeId.current, {
        x: nodeStartOffset.current.x + dx,
        y: nodeStartOffset.current.y + dy,
      })
      return
    }
    if (!dragging.current || dragPointerId.current !== e.pointerId) return
    const dx = e.clientX - lastPoint.current.x
    const dy = e.clientY - lastPoint.current.y
    lastPoint.current = { x: e.clientX, y: e.clientY }
    setPan(p => ({ x: p.x + dx / zoom, y: p.y + dy / zoom }))
  }, [onNodeOffsetChange, zoom])

  const endDrag = useCallback((e?: React.PointerEvent<SVGSVGElement>) => {
    if (e && dragPointerId.current !== null && dragPointerId.current === e.pointerId) {
      try {
        e.currentTarget.releasePointerCapture(e.pointerId)
      } catch {
        // no-op
      }
    }
    draggingNodeId.current = null
    dragging.current = false
    dragPointerId.current = null
  }, [])

  const resetView = useCallback(() => { setZoom(1); setPan({ x: 0, y: 0 }) }, [])

  // Adjusted viewBox accounting for pan and zoom
  const scaledW = vbW / zoom
  const scaledH = vbH / zoom
  const vbX = vbMinX + (vbW - scaledW) / 2 - pan.x
  const vbY = (vbH - scaledH) / 2 - pan.y

  const maxHeight = isFullscreen ? undefined : 320

  return (
    <div ref={containerRef} className="relative group">
      {/* Zoom controls (visible on hover or in fullscreen) */}
      <div className={clsx(
        'absolute top-2 right-2 z-10 flex gap-1',
        isFullscreen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 transition-opacity',
      )}>
        <button
          onClick={() => setZoom(z => clampZoom(z + 0.15))}
          className="p-1 rounded bg-dark-bg/80 border border-dark-border text-gray-400 hover:text-white hover:bg-dark-hover"
          title="Zoom in"
        >
          <PlusIcon className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={() => setZoom(z => clampZoom(z - 0.15))}
          className="p-1 rounded bg-dark-bg/80 border border-dark-border text-gray-400 hover:text-white hover:bg-dark-hover"
          title="Zoom out"
        >
          <MinusIcon className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={resetView}
          className="p-1 rounded bg-dark-bg/80 border border-dark-border text-gray-400 hover:text-white hover:bg-dark-hover"
          title="Reset view"
        >
          <ArrowPathIcon className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Zoom indicator */}
      {zoom !== 1 && (
        <span className="absolute bottom-1 right-2 text-[9px] text-gray-600 z-10">
          {Math.round(zoom * 100)}%
        </span>
      )}

      <svg
        width="100%"
        height={isFullscreen ? '100%' : Math.max(vbH, 180)}
        viewBox={`${vbX} ${vbY} ${scaledW} ${scaledH}`}
        className="mx-auto select-none"
        style={{
          minWidth: 260,
          maxHeight,
          cursor: dragging.current || draggingNodeId.current ? 'grabbing' : 'grab',
          touchAction: 'none',
        }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={endDrag}
        onPointerLeave={endDrag}
        onPointerCancel={endDrag}
      >
        {edges.map((e, i) => (
          <TopoEdgeLine key={`e-${i}`} edge={e} nodes={nodes} />
        ))}
        {nodes.map(n => (
          <TopoNodeBox key={n.id} node={n} onPointerDown={handleNodePointerDown} />
        ))}
      </svg>
    </div>
  )
}

// --- Main Component ---

interface TaskTopologyProps {
  task: Task
}

export default function TaskTopology({ task }: TaskTopologyProps) {
  const { agents } = useAgentStore()
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [nodeOffsets, setNodeOffsets] = useState<Record<string, NodeOffset>>({})

  // Incremental topology: accumulate node/edge snapshots once seen, never remove
  // until the task changes. This prevents timeout/failure updates from wiping
  // the earlier topology path.
  const accumulatedTopologyRef = useRef<{
    nodes: Map<string, TopoNode>
    edges: Map<string, TopoEdge>
    taskId: string
  }>({
    nodes: new Map(),
    edges: new Map(),
    taskId: '',
  })

  // Close fullscreen on Escape
  useEffect(() => {
    if (!isFullscreen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsFullscreen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isFullscreen])

  useEffect(() => {
    setNodeOffsets({})
  }, [task.id])

  // Build name lookup
  const agentNames = useMemo(() => {
    const map = new Map<string, string>()
    agents.forEach(a => map.set(a.id, a.name))
    return map
  }, [agents])

  const built = useMemo(
    () => buildTopology(task, agentNames, agents),
    [task, agentNames, agents],
  )

  // Merge: keep accumulated nodes/edges, add new ones, update status for existing.
  const { nodes: rawNodes, edges } = useMemo(() => {
    const isTerminal = ['completed', 'failed', 'cancelled'].includes(task.status)
    const terminalNodeStatus: NodeStatus =
      task.status === 'failed' ? 'failed' : task.status === 'completed' ? 'completed' : 'idle'
    const terminalEdgeStatus: TopoEdge['status'] =
      task.status === 'failed' ? 'error' : task.status === 'completed' ? 'done' : 'idle'
    const snapshot = accumulatedTopologyRef.current
    const taskChanged = snapshot.taskId !== task.id

    if (taskChanged) {
      snapshot.nodes = new Map(built.nodes.map((n) => [n.id, n]))
      snapshot.edges = new Map(built.edges.map((e) => [`${e.from}→${e.to}`, e]))
      snapshot.taskId = task.id
    } else {
      const builtNodeIds = new Set(built.nodes.map((n) => n.id))
      const builtEdgeKeys = new Set(built.edges.map((e) => `${e.from}→${e.to}`))

      built.nodes.forEach((node) => {
        snapshot.nodes.set(node.id, node)
      })
      built.edges.forEach((edge) => {
        snapshot.edges.set(`${edge.from}→${edge.to}`, edge)
      })

      if (isTerminal) {
        snapshot.nodes.forEach((node, id) => {
          if (builtNodeIds.has(id)) return
          if (node.status !== 'completed') {
            snapshot.nodes.set(id, {
              ...node,
              status: node.status === 'failed' ? 'failed' : terminalNodeStatus,
            })
          }
        })

        snapshot.edges.forEach((edge, key) => {
          if (builtEdgeKeys.has(key)) return
          if (edge.status === 'active' || edge.status === 'idle') {
            snapshot.edges.set(key, { ...edge, status: terminalEdgeStatus })
          }
        })
      } else {
        snapshot.nodes.forEach((node, id) => {
          if (builtNodeIds.has(id)) return
          if (node.status === 'executing') {
            snapshot.nodes.set(id, { ...node, status: 'completed' })
          }
        })

        snapshot.edges.forEach((edge, key) => {
          if (builtEdgeKeys.has(key)) return
          if (edge.status === 'active') {
            snapshot.edges.set(key, { ...edge, status: 'done' })
          }
        })
      }
    }

    return {
      nodes: Array.from(snapshot.nodes.values()),
      edges: Array.from(snapshot.edges.values()),
    }
  }, [built, task.id, task.status])

  const autoNodes = useMemo(() => resolveNodeOverlaps(rawNodes), [rawNodes])
  const nodes = useMemo(() => applyNodeOffsets(autoNodes, nodeOffsets), [autoNodes, nodeOffsets])

  const handleNodeOffsetChange = useCallback((nodeId: string, offset: NodeOffset) => {
    setNodeOffsets((prev) => {
      const cur = prev[nodeId]
      if (cur && cur.x === offset.x && cur.y === offset.y) return prev
      return { ...prev, [nodeId]: offset }
    })
  }, [])

  // Compute SVG dimensions
  const maxX = Math.max(...nodes.map(n => n.x)) + NODE_W / 2 + 20
  const minX = Math.min(...nodes.map(n => n.x)) - NODE_W / 2 - 20
  const maxY = Math.max(...nodes.map(n => n.y + (n.h || NODE_H) / 2)) + 16
  const svgW = Math.max(maxX - minX, 260)
  const svgH = maxY + 8

  // Phase text
  const phaseText = task.progress?.phaseLabel
    || (task.status === 'delegated' ? 'Delegated to remote AN'
    : task.status === 'running' ? 'Executing...'
    : task.status === 'completed' ? 'Completed'
    : task.status === 'failed' ? 'Failed'
    : 'Pending')

  const header = (
    <div className="flex items-center justify-between mb-2">
      <h3 className="text-sm font-medium text-gray-400">Task Flow</h3>
      <div className="flex items-center gap-2">
        <span className={clsx(
          'text-xs px-2 py-0.5 rounded-full',
          task.status === 'running' && 'bg-blue-500/20 text-blue-400',
          task.status === 'delegated' && 'bg-amber-500/20 text-amber-400',
          task.status === 'completed' && 'bg-green-500/20 text-green-400',
          task.status === 'failed' && 'bg-red-500/20 text-red-400',
          task.status === 'pending' && 'bg-gray-500/20 text-gray-400',
        )}>
          {phaseText}
        </span>
        <button
          onClick={() => setIsFullscreen(f => !f)}
          className="p-1 rounded text-gray-500 hover:text-gray-300 hover:bg-dark-hover transition-colors"
          title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
        >
          {isFullscreen
            ? <ArrowsPointingInIcon className="w-4 h-4" />
            : <ArrowsPointingOutIcon className="w-4 h-4" />
          }
        </button>
      </div>
    </div>
  )

  // Inline (normal) render
  const inlineContent = (
    <div className="w-full">
      {header}
      <div className="w-full overflow-hidden rounded-lg bg-dark-bg border border-dark-border p-2">
        <SvgViewport
          nodes={nodes} edges={edges}
          nodeOffsets={nodeOffsets}
          onNodeOffsetChange={handleNodeOffsetChange}
          vbMinX={minX} vbW={svgW} vbH={svgH}
        />
      </div>
      <Legend />
    </div>
  )

  // Fullscreen portal
  const fullscreenContent = isFullscreen ? createPortal(
    <div
      className="fixed inset-0 z-[999999] bg-dark-bg/95 backdrop-blur-sm flex flex-col"
      onClick={(e) => { if (e.target === e.currentTarget) setIsFullscreen(false) }}
    >
      <div className="px-6 py-4 border-b border-dark-border flex items-center justify-between">
        <h3 className="text-base font-medium text-gray-300">Task Flow</h3>
        <div className="flex items-center gap-3">
          <span className={clsx(
            'text-xs px-2 py-0.5 rounded-full',
            task.status === 'running' && 'bg-blue-500/20 text-blue-400',
            task.status === 'delegated' && 'bg-amber-500/20 text-amber-400',
            task.status === 'completed' && 'bg-green-500/20 text-green-400',
            task.status === 'failed' && 'bg-red-500/20 text-red-400',
            task.status === 'pending' && 'bg-gray-500/20 text-gray-400',
          )}>
            {phaseText}
          </span>
          <button
            onClick={() => setIsFullscreen(false)}
            className="p-1.5 rounded text-gray-400 hover:text-white hover:bg-dark-hover transition-colors"
            title="Exit fullscreen (Esc)"
          >
            <ArrowsPointingInIcon className="w-5 h-5" />
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-hidden p-4">
        <SvgViewport
          nodes={nodes} edges={edges}
          nodeOffsets={nodeOffsets}
          onNodeOffsetChange={handleNodeOffsetChange}
          vbMinX={minX} vbW={svgW} vbH={svgH}
          isFullscreen
        />
      </div>
      <div className="px-6 py-2 border-t border-dark-border">
        <Legend />
      </div>
    </div>,
    document.body,
  ) : null

  return (
    <>
      {inlineContent}
      {fullscreenContent}
    </>
  )
}
