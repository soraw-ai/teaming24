/**
 * Shared status display configuration for agents and tasks.
 *
 * Centralizes color, icon, and label mappings so that AgentCard,
 * TaskCard, and other components render statuses consistently.
 */

import {
  ArrowUpRightIcon,
  CheckCircleIcon,
  CheckIcon,
  ClockIcon,
  ExclamationCircleIcon,
  PlayIcon,
  SignalIcon,
  SignalSlashIcon,
  XMarkIcon,
  ArchiveBoxIcon,
} from '@heroicons/react/24/outline'
import type { AgentStatus, TaskStatus } from '../store/agentStore'

// ── Agent Status ────────────────────────────────────────────────────────

export interface StatusEntry {
  color: string
  icon: typeof CheckCircleIcon
  label: string
}

export const agentStatusConfig: Record<AgentStatus, StatusEntry> = {
  online:  { color: 'text-green-400',  icon: CheckCircleIcon,      label: 'Online'  },
  offline: { color: 'text-gray-500',   icon: SignalSlashIcon,       label: 'Offline' },
  busy:    { color: 'text-yellow-400', icon: ClockIcon,             label: 'Busy'    },
  error:   { color: 'text-red-400',    icon: ExclamationCircleIcon, label: 'Error'   },
  idle:    { color: 'text-blue-400',   icon: SignalIcon,            label: 'Idle'    },
}

// ── Task Status ─────────────────────────────────────────────────────────

export interface TaskStatusEntry {
  color: string
  bgColor: string
  icon: typeof PlayIcon
  label: string
}

export const taskStatusConfig: Record<TaskStatus, TaskStatusEntry> = {
  pending:   { color: 'text-gray-400',   bgColor: 'bg-gray-500/20',   icon: ClockIcon,        label: 'Pending'   },
  running:   { color: 'text-blue-400',   bgColor: 'bg-blue-500/20',   icon: PlayIcon,         label: 'Running'   },
  delegated: { color: 'text-purple-400', bgColor: 'bg-purple-500/20', icon: ArrowUpRightIcon, label: 'Delegated' },
  completed: { color: 'text-green-400',  bgColor: 'bg-green-500/20',  icon: CheckIcon,        label: 'Completed' },
  failed:    { color: 'text-red-400',    bgColor: 'bg-red-500/20',    icon: XMarkIcon,        label: 'Failed'    },
  cancelled: { color: 'text-yellow-400', bgColor: 'bg-yellow-500/20', icon: ArchiveBoxIcon,   label: 'Archived'  },
}
