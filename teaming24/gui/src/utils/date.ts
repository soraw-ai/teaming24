/**
 * Shared date/time formatting utilities.
 *
 * Provides a consistent 24-hour date-time format across all components:
 * `YYYY-MM-DD HH:MM:SS`
 */

/**
 * Format a JS timestamp (milliseconds) to `YYYY-MM-DD HH:MM:SS`.
 *
 * @param timestamp - Epoch milliseconds (e.g. `Date.now()` or `task.createdAt`).
 * @returns Formatted date-time string, or `'-'` when the input is falsy.
 */
export function formatDateTime(timestamp?: number): string {
  if (!timestamp) return '-'
  const date = new Date(timestamp)
  const y = date.getFullYear()
  const mo = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  const h = String(date.getHours()).padStart(2, '0')
  const mi = String(date.getMinutes()).padStart(2, '0')
  const s = String(date.getSeconds()).padStart(2, '0')
  return `${y}-${mo}-${d} ${h}:${mi}:${s}`
}

/**
 * Format timestamp to compact time only (HH:MM:SS).
 * Use when date is shown elsewhere or for log/event lists.
 */
export function formatTimeCompact(timestamp?: number): string {
  if (!timestamp) return '-'
  const date = new Date(timestamp)
  const h = String(date.getHours()).padStart(2, '0')
  const mi = String(date.getMinutes()).padStart(2, '0')
  const s = String(date.getSeconds()).padStart(2, '0')
  return `${h}:${mi}:${s}`
}

/**
 * Format a **Unix epoch seconds** timestamp to `YYYY-MM-DD HH:MM:SS`.
 *
 * Some backend payloads (sandbox events) use seconds rather than milliseconds.
 */
export function formatDateTimeFromUnix(epochSeconds?: number): string {
  if (!epochSeconds) return '-'
  return formatDateTime(epochSeconds * 1000)
}

/**
 * Convert backend timestamp to milliseconds.
 * Backend may send seconds (Unix epoch) or milliseconds — detect and normalize.
 */
export function toMilliseconds(ts?: number): number | undefined {
  if (!ts) return undefined
  return ts > 1e12 ? ts : ts * 1000
}
