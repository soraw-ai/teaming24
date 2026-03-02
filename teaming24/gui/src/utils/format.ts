/**
 * Shared formatting utilities for numbers, durations, tokens, currency.
 * All numbers display without trailing zeros (0.01 not 0.0100).
 */

import { getPaymentTokenSymbol } from '../config/payment'

/**
 * Format a number with up to maxDecimals places, stripping trailing zeros.
 * e.g. formatNumberNoTrailingZeros(0.01, 4) → "0.01", (1.2, 2) → "1.2"
 */
export function formatNumberNoTrailingZeros(num: number, maxDecimals: number = 6): string {
  return num.toFixed(maxDecimals).replace(/\.?0+$/, '')
}

/**
 * Format amount as ETH token (e.g. 0.01, 1.2345). No trailing zeros.
 */
export function formatUSDC(amount: number): string {
  return formatNumberNoTrailingZeros(amount, 6)
}

/**
 * Format amount as "X.XX ETH" (for display with currency label).
 */
export function formatCurrencyUSDC(amount: number): string {
  return `${formatUSDC(amount)} ${getPaymentTokenSymbol()}`
}

/**
 * Format balance compactly: >= 1000 → "X.Xk", else formatUSDC.
 */
export function formatBalanceCompact(amount: number): string {
  if (amount >= 1000) return `${formatNumberNoTrailingZeros(amount / 1000, 1)}k`
  return formatUSDC(amount)
}

/**
 * Format token count with k/m suffixes. No trailing zeros.
 */
export function formatTokenCount(count: number): string {
  if (count === 0) return '0'
  if (count >= 1_000_000) return `${formatNumberNoTrailingZeros(count / 1_000_000, 1)}m`
  if (count >= 1_000) return `${formatNumberNoTrailingZeros(count / 1_000, 1)}k`
  return count.toLocaleString()
}

/**
 * Format uptime/duration in seconds (compact: no seconds when >= 1m).
 * e.g. 45 → "45s", 300 → "5m", 3720 → "1h 2m"
 */
export function formatUptime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${h}h ${m}m`
}

/**
 * Format duration in seconds to human-readable string.
 * Backend may send duration in seconds or milliseconds — normalizes automatically.
 */
export function formatDurationSecs(seconds: number): string {
  const secs = seconds > 1000 ? seconds / 1000 : seconds
  if (secs < 1) return `${Math.round(secs * 1000)}ms`
  if (secs < 60) return `${formatNumberNoTrailingZeros(secs, 1)}s`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`
}

/**
 * Format duration in milliseconds to human-readable string.
 */
export function formatDurationMs(ms: number): string {
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  const h = Math.floor(m / 60)
  if (h > 0) return `${h}h ${m % 60}m`
  if (m > 0) return `${m}m ${s % 60}s`
  return `${s}s`
}

/**
 * Format duration from start/end timestamps (ms).
 * Used for step durations where end may be missing (still running).
 */
export function formatDurationFromTimestamps(startMs: number, endMs?: number): string {
  if (!startMs) return '-'
  const duration = (endMs ?? Date.now()) - startMs
  if (duration < 1000) return `${duration}ms`
  if (duration < 60000) return `${formatNumberNoTrailingZeros(duration / 1000, 1)}s`
  return `${formatNumberNoTrailingZeros(duration / 60000, 1)}m`
}
