import type { Task, TaskStatus } from '../store/agentStore'
import { toast } from '../store/notificationStore'
import { reportUiError } from './errorReporting'

const DELETE_UNDO_MS = 5000
const ARCHIVE_UNDO_MS = 5000

const pendingDeleteTimers = new Map<string, number>()
const pendingArchiveTimers = new Map<string, number>()

function clearPendingTimer(map: Map<string, number>, taskId: string) {
  const timer = map.get(taskId)
  if (timer) {
    window.clearTimeout(timer)
    map.delete(taskId)
  }
}

export function queueTaskDeleteWithUndo(
  task: Task,
  handlers: {
    deleteTask: (id: string) => Promise<void>
  }
) {
  clearPendingTimer(pendingDeleteTimers, task.id)
  const timer = window.setTimeout(async () => {
    try {
      await handlers.deleteTask(task.id)
    } catch (error) {
      reportUiError({
        source: 'TaskUndo',
        title: 'Task Delete Failed',
        userMessage: `Failed to delete "${task.name}".`,
        error,
      })
    } finally {
      pendingDeleteTimers.delete(task.id)
    }
  }, DELETE_UNDO_MS)
  pendingDeleteTimers.set(task.id, timer)

  toast.warning(
    'Task queued for delete',
    `"${task.name}" will be deleted in ${Math.round(DELETE_UNDO_MS / 1000)}s.`,
    DELETE_UNDO_MS,
    {
      label: 'Undo',
      onClick: () => {
        clearPendingTimer(pendingDeleteTimers, task.id)
        toast.info('Delete canceled', `"${task.name}" was kept.`, 2600)
      },
    },
  )
}

export function applyArchiveWithUndo(
  task: Task,
  handlers: {
    archiveTask: (id: string) => void
    updateTask: (id: string, updates: Partial<Task>) => void
  }
) {
  const previousStatus: TaskStatus = task.status
  clearPendingTimer(pendingArchiveTimers, task.id)
  handlers.archiveTask(task.id)

  const timer = window.setTimeout(() => {
    pendingArchiveTimers.delete(task.id)
  }, ARCHIVE_UNDO_MS)
  pendingArchiveTimers.set(task.id, timer)

  toast.info(
    'Task archived',
    `"${task.name}" archived. You can undo for ${Math.round(ARCHIVE_UNDO_MS / 1000)}s.`,
    ARCHIVE_UNDO_MS,
    {
      label: 'Undo',
      onClick: () => {
        clearPendingTimer(pendingArchiveTimers, task.id)
        handlers.updateTask(task.id, { status: previousStatus })
        toast.success('Archive undone', `"${task.name}" restored.`, 2600)
      },
    },
  )
}
