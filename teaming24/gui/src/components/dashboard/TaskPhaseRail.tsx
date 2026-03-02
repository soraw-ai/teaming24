import clsx from 'clsx'
import {
  ArrowsPointingInIcon,
  ArrowPathIcon,
  ArrowUpRightIcon,
  CheckIcon,
  ClockIcon,
  PlayIcon,
} from '@heroicons/react/24/outline'
import type { Task, TaskPhase } from '../../store/agentStore'

interface TaskPhaseRailProps {
  task: Task
}

type RailPhaseState = 'pending' | 'active' | 'completed' | 'failed'

const PHASES: Array<{
  id: TaskPhase
  label: string
  description: string
  icon: typeof ClockIcon
}> = [
  {
    id: 'received',
    label: 'Received',
    description: 'Organizer accepted request',
    icon: ClockIcon,
  },
  {
    id: 'routing',
    label: 'Routing',
    description: 'ANRouter selecting pool members',
    icon: ArrowPathIcon,
  },
  {
    id: 'dispatching',
    label: 'Dispatch',
    description: 'Organizer dispatching subtasks',
    icon: ArrowUpRightIcon,
  },
  {
    id: 'executing',
    label: 'Execute',
    description: 'Workers processing subtasks',
    icon: PlayIcon,
  },
  {
    id: 'aggregating',
    label: 'Aggregate',
    description: 'Organizer merging all results',
    icon: ArrowsPointingInIcon,
  },
  {
    id: 'completed',
    label: 'Done',
    description: 'Task finalized',
    icon: CheckIcon,
  },
]

function inferPhase(task: Task): TaskPhase {
  if (task.progress?.phase) return task.progress.phase
  if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') return 'completed'
  if (task.status === 'delegated') return 'dispatching'
  if (task.status === 'pending') return 'received'

  const hasRoutingStep = task.steps.some((s) => {
    const action = String(s.action || '').toLowerCase()
    return action.includes('route') || action.includes('routing')
  })
  if (hasRoutingStep) return 'routing'
  if ((task.executingAgents || []).length > 0) return 'executing'
  if ((task.delegatedAgents || []).length > 0) return 'dispatching'
  return 'executing'
}

function getPhaseState(task: Task, phaseIndex: number, currentIndex: number): RailPhaseState {
  const terminalFailure = task.status === 'failed' || task.status === 'cancelled'
  const terminalSuccess = task.status === 'completed'

  if (terminalSuccess) return 'completed'

  if (terminalFailure) {
    if (phaseIndex < currentIndex) return 'completed'
    if (phaseIndex === currentIndex) return 'failed'
    return 'pending'
  }

  if (phaseIndex < currentIndex) return 'completed'
  if (phaseIndex === currentIndex) return 'active'
  return 'pending'
}

export default function TaskPhaseRail({ task }: TaskPhaseRailProps) {
  const phase = inferPhase(task)
  const currentIndex = Math.max(0, PHASES.findIndex((p) => p.id === phase))
  const progress = task.progress?.percentage ?? (
    task.status === 'completed' ? 100
      : task.status === 'failed' || task.status === 'cancelled' ? Math.max(5, Math.min(95, Math.round((currentIndex / (PHASES.length - 1)) * 100)))
      : Math.round((currentIndex / (PHASES.length - 1)) * 100)
  )

  const totalWorkers = task.progress?.totalWorkers ?? 0
  const completedWorkers = task.progress?.completedWorkers ?? 0
  const activeWorkers = task.progress?.activeWorkers ?? 0
  const skippedWorkers = task.progress?.skippedWorkers ?? 0

  return (
    <div className="rounded-xl border border-dark-border bg-dark-bg/40 p-3 sm:p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px]">
        <span className="rounded-full bg-dark-surface px-2 py-1 text-gray-300">
          {task.progress?.phaseLabel || phase}
        </span>
        <span className="rounded-full bg-primary-500/15 px-2 py-1 text-primary-300">
          {progress}%
        </span>
        {task.executionMode && (
          <span className="rounded-full bg-dark-surface px-2 py-1 text-gray-400">
            {task.executionMode}
          </span>
        )}
        {totalWorkers > 0 && (
          <span className="rounded-full bg-dark-surface px-2 py-1 text-gray-400">
            workers {completedWorkers}/{totalWorkers}
          </span>
        )}
        {activeWorkers > 0 && (
          <span className="rounded-full bg-blue-500/15 px-2 py-1 text-blue-300">
            active {activeWorkers}
          </span>
        )}
        {skippedWorkers > 0 && (
          <span className="rounded-full bg-yellow-500/15 px-2 py-1 text-yellow-300">
            skipped {skippedWorkers}
          </span>
        )}
      </div>

      <div className="overflow-x-auto pb-1">
        <div className="flex min-w-max items-center gap-2">
          {PHASES.map((item, idx) => {
            const Icon = item.icon
            const state = getPhaseState(task, idx, currentIndex)
            const isActive = state === 'active'
            return (
              <div key={item.id} className="flex items-center gap-2">
                <div
                  className={clsx(
                    'min-w-[128px] rounded-lg border px-2.5 py-2 transition-colors',
                    state === 'completed' && 'border-green-500/35 bg-green-500/10',
                    state === 'active' && 'border-primary-500/40 bg-primary-500/15',
                    state === 'failed' && 'border-red-500/35 bg-red-500/10',
                    state === 'pending' && 'border-dark-border bg-dark-surface/60',
                  )}
                  title={item.description}
                >
                  <div className="flex items-center gap-1.5">
                    <Icon
                      className={clsx(
                        'h-3.5 w-3.5',
                        state === 'completed' && 'text-green-300',
                        state === 'active' && 'text-primary-300',
                        state === 'failed' && 'text-red-300',
                        state === 'pending' && 'text-gray-500',
                        isActive && 'animate-pulse',
                      )}
                    />
                    <span
                      className={clsx(
                        'text-xs font-medium',
                        state === 'completed' && 'text-green-200',
                        state === 'active' && 'text-primary-200',
                        state === 'failed' && 'text-red-200',
                        state === 'pending' && 'text-gray-400',
                      )}
                    >
                      {item.label}
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-[10px] text-gray-500">{item.description}</p>
                </div>
                {idx < PHASES.length - 1 && (
                  <div
                    className={clsx(
                      'h-0.5 w-5 rounded-full',
                      idx < currentIndex ? 'bg-green-400/50' : 'bg-dark-border',
                    )}
                  />
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
