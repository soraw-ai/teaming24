import { notify } from '../store/notificationStore'
import { useUiStore } from '../store/uiStore'

function stringifyError(error: unknown): string {
  if (error instanceof Error) {
    return error.stack || error.message || String(error)
  }
  if (typeof error === 'string') return error
  try {
    return JSON.stringify(error, null, 2)
  } catch {
    return String(error)
  }
}

export function reportUiError(params: {
  source: string
  title: string
  userMessage: string
  error: unknown
}) {
  const { source, title, userMessage, error } = params
  const details = stringifyError(error)
  console.error(`[${source}] ${userMessage}`, error)

  notify.error(title, userMessage, {
    label: 'View details',
    action: () => {
      useUiStore.getState().showError({
        title,
        message: userMessage,
        source,
        details,
      })
    },
  })
}
