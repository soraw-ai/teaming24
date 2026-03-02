/**
 * Debug logging — only logs when running in development (Vite dev server).
 * Production builds do not emit these logs.
 */
const isDev = import.meta.env.DEV

export function debugLog(...args: unknown[]): void {
  if (isDev) {
    console.log(...args)
  }
}

export function debugWarn(...args: unknown[]): void {
  if (isDev) {
    console.warn(...args)
  }
}
